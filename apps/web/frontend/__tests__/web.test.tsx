import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Charts from "@/components/Charts";
import Leaderboard from "@/components/Leaderboard";
import { buildPlacesPath, buildWetnessPath, getWetness, postReport } from "@/lib/api";
import {
  CoverageSchema,
  PlacesSchema,
  ReportSchema,
  WetnessGeoJSONSchema,
} from "@/lib/schemas";
import {
  defaultChartPlace,
  formatReliability,
  hourlyChartData,
  monthlyChartData,
  orderLeaderboardRows,
  selectionAfterToggle,
} from "@/lib/presentation";
import {
  colorFor,
  nodeDataFromWetness,
  outlineFeaturesFromPlaces,
  radiusForWetHours,
  tooltipTextFor,
} from "@/lib/map";
import type {
  Coverage,
  HourlyRow,
  MonthlyRow,
  PlaceRecord,
  Report,
  SummaryRow,
  WetnessGeoJSON,
} from "@/lib/types";

const place = (placeKey: string, name: string): PlaceRecord => ({
  place_key: placeKey,
  name,
  kind: "golf_course",
  lat: 42.5,
  lon: -70.9,
  timezone: "America/New_York",
  geometry: { type: "Point", coordinates: [-70.9, 42.5] },
});

const summaryRow = (
  placeKey: string,
  name: string,
  wetFraction: number | null,
): SummaryRow => ({
  place_key: placeKey,
  name,
  kind: "golf_course",
  wet_fraction: wetFraction,
  wet_hours: wetFraction === null ? 0 : Math.round(wetFraction * 10),
  valid_hours: 10,
  rain_mm: 1.5,
  coverage: 1,
  tied: false,
  cell_places: 1,
  rank: 1,
});

const report: Report = {
  summary: [
    summaryRow("osm:way:1", "Wet Course", 0.4),
    summaryRow("osm:way:2", "Dry Course", 0.1),
    summaryRow("osm:way:3", "No Data Course", null),
  ],
  monthly: [
    {
      place_key: "osm:way:1",
      local_month: 7,
      name: "Wet Course",
      kind: "golf_course",
      valid_hours: 10,
      wet_hours: 4,
      rain_mm: 3,
      wet_fraction: 0.4,
    },
    {
      place_key: "osm:way:1",
      local_month: 6,
      name: "Wet Course",
      kind: "golf_course",
      valid_hours: 10,
      wet_hours: 2,
      rain_mm: 1,
      wet_fraction: 0.2,
    },
  ],
  hourly: [
    {
      place_key: "osm:way:1",
      local_hour: 17,
      name: "Wet Course",
      kind: "golf_course",
      hours_observed: 10,
      wet_hours: 4,
      rain_mm: 3,
      wet_fraction: 0.4,
    },
    {
      place_key: "osm:way:1",
      local_hour: 9,
      name: "Wet Course",
      kind: "golf_course",
      hours_observed: 10,
      wet_hours: 2,
      rain_mm: 1,
      wet_fraction: 0.2,
    },
  ],
  reliability: 0.72,
  start: "2025-06-01",
  end: "2025-08-31",
  places: [place("osm:way:1", "Wet Course"), place("osm:way:2", "Dry Course")],
};

const wetness: WetnessGeoJSON = {
  type: "FeatureCollection",
  features: [
    {
      type: "Feature",
      geometry: { type: "Point", coordinates: [-70.9, 42.5] },
      properties: { wet_hours: 3, valid_hours: 5, wet_fraction: 0.6 },
    },
  ],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("API client and contracts", () => {
  it("builds encoded place and wetness query strings", () => {
    expect(new URL(buildPlacesPath("golf & country club"), "http://local").searchParams.get("kind"))
      .toBe("golf & country club");
    expect(buildPlacesPath()).toBe("/api/places");
    expect(buildWetnessPath({ start: "2025-07-01", end: "2025-07-31", threshold: 0.1 }))
      .toBe("/api/wetness?start=2025-07-01&end=2025-07-31&threshold=0.1");
    expect(buildWetnessPath({})).toBe("/api/wetness");
  });

  it("validates JSON responses before returning their inferred types", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(wetness), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(getWetness({ start: "2025-07-01", end: "2025-07-31" })).resolves.toEqual(
      wetness,
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/wetness?start=2025-07-01&end=2025-07-31",
      expect.objectContaining({ cache: "no-store" }),
    );
    expect(WetnessGeoJSONSchema.safeParse({ type: "FeatureCollection", features: [{}] }).success)
      .toBe(false);
  });

  it("logs failed responses without changing the rejection", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response("no gridded artifact", { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getWetness({ start: "2025-07-01" })).rejects.toThrow(
      "503: no gridded artifact",
    );
    expect(consoleError).toHaveBeenCalledWith(
      "[tstorm-climo]",
      "API response error",
      "GET",
      "/api/wetness?start=2025-07-01",
      503,
      "no gridded artifact",
    );
  });

  it("logs malformed responses before rejecting", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ type: "FeatureCollection", features: [{}] }), {
        status: 200,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(getWetness({})).rejects.toThrow();
    expect(consoleError).toHaveBeenCalledWith(
      "[tstorm-climo]",
      "API response did not match the expected schema",
      "/api/wetness",
      expect.anything(),
    );
  });

  it("posts selected places as JSON and rejects malformed artifact metadata", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(report), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      postReport({
        place_keys: ["osm:way:1"],
        start: "2025-06-01",
        end: "2025-08-31",
        threshold_mm_per_hour: 0.05,
      }),
    ).resolves.toEqual(report);
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/api/report");
    expect(options?.method).toBe("POST");
    expect(options?.headers).toEqual({ "Content-Type": "application/json" });
    expect(JSON.parse(String(options?.body))).toEqual({
      place_keys: ["osm:way:1"],
      start: "2025-06-01",
      end: "2025-08-31",
      threshold_mm_per_hour: 0.05,
    });

    const coverage: Coverage = {
      observed_start: "2025-06-01",
      observed_end: "2025-08-31",
      place_count: 2,
      kinds: ["golf_course"],
      variable: "rain_rate",
      units: "mm h-1",
      gridded: true,
    };
    expect(CoverageSchema.parse(coverage)).toEqual(coverage);
    expect(CoverageSchema.safeParse({ ...coverage, place_count: -1 }).success).toBe(false);
    expect(ReportSchema.safeParse({ ...report, reliability: Number.NaN }).success).toBe(false);
    const footprint = {
      ...place("osm:way:3", "Footprint Course"),
      geometry: {
        type: "Polygon" as const,
        coordinates: [[
          [-71, 42],
          [-70.9, 42],
          [-70.9, 42.1],
          [-71, 42],
        ]],
      },
    };
    expect(PlacesSchema.parse({ places: [footprint] }).places[0].geometry.type).toBe(
      "Polygon",
    );
  });
});

describe("ranking, chart, selection, and map presentation logic", () => {
  it("formats reliability and orders unavailable values last without mutation", () => {
    expect(formatReliability(0.726)).toBe("0.73");
    expect(formatReliability(null)).toBe("Unavailable");
    expect(orderLeaderboardRows(report.summary).map((row) => row.name)).toEqual([
      "Wet Course",
      "Dry Course",
      "No Data Course",
    ]);
    expect(report.summary[0].name).toBe("Wet Course");
  });

  it("shapes monthly and local-hour chart series in chronological order", () => {
    const monthly: MonthlyRow[] = report.monthly;
    const hourly: HourlyRow[] = report.hourly;

    expect(monthlyChartData(monthly, "osm:way:1")).toEqual([
      { label: "Jun", wet_fraction: 0.2 },
      { label: "Jul", wet_fraction: 0.4 },
    ]);
    expect(hourlyChartData(hourly, "osm:way:1")).toEqual([
      { label: 9, wet_fraction: 0.2 },
      { label: 17, wet_fraction: 0.4 },
    ]);
    expect(monthlyChartData(monthly, "osm:way:2")).toEqual([]);
    expect(defaultChartPlace(report.summary, report.places)).toBe("osm:way:1");
  });

  it("enforces the selection cap while allowing deselection", () => {
    const selected = ["one", "two"];
    expect(selectionAfterToggle(selected, "three", 2)).toBe(selected);
    expect(selectionAfterToggle(selected, "two", 2)).toEqual(["one"]);
    expect(selectionAfterToggle(["one"], "two", 2)).toEqual(["one", "two"]);
  });

  it("maps wet fractions and counts without treating missing data as dry", () => {
    expect(colorFor(null)).toEqual([200, 200, 200, 140]);
    expect(colorFor(0)).toEqual([68, 1, 84, 230]);
    expect(colorFor(0.2)).toEqual([253, 231, 37, 230]);
    expect(radiusForWetHours(0)).toBe(4);
    expect(radiusForWetHours(50)).toBe(28);
    expect(nodeDataFromWetness(wetness)).toEqual([
      { position: [-70.9, 42.5], wetHours: 3, wetFraction: 0.6 },
    ]);
    expect(nodeDataFromWetness(null)).toEqual([]);
    expect(tooltipTextFor(nodeDataFromWetness(wetness)[0])).toBe("3 wet hours (60.0%)");
    expect(tooltipTextFor(place("osm:way:1", "Wet Course"))).toBe(
      "Wet Course · golf_course",
    );
    const footprint: PlaceRecord = {
      ...place("osm:way:4", "Footprint Course"),
      geometry: {
        type: "Polygon",
        coordinates: [[
          [-71, 42],
          [-70.9, 42],
          [-70.9, 42.1],
          [-71, 42],
        ]],
      },
    };
    const outlines = outlineFeaturesFromPlaces([
      place("osm:way:1", "Point Course"),
      footprint,
    ]);
    expect(outlines.features).toHaveLength(1);
    expect(outlines.features[0].geometry).toEqual(footprint.geometry);
    expect(tooltipTextFor(outlines.features[0])).toBe(
      "Footprint Course · golf_course",
    );
    expect(tooltipTextFor(null)).toBeNull();
  });
});

describe("dashboard components", () => {
  it("renders a leaderboard with ranking reliability next to every ranking", () => {
    render(<Leaderboard report={report} />);

    const rows = screen.getAllByRole("row").slice(1);
    expect(rows.map((row) => within(row).getAllByRole("cell")[0].textContent)).toEqual([
      "Wet Course",
      "Dry Course",
      "No Data Course",
    ]);
    expect(screen.getByRole("note", { name: "Ranking reliability" })).toHaveTextContent(
      "0.72",
    );
  });

  it("keeps the reliability notice visible when the statistic is unavailable", () => {
    render(<Leaderboard report={{ ...report, reliability: null }} />);

    expect(screen.getByRole("note", { name: "Ranking reliability" })).toHaveTextContent(
      "Unavailable",
    );
    expect(screen.getByRole("note")).toHaveTextContent("could not be estimated");
  });

  it("renders both chart panels and the place selector", () => {
    render(<Charts report={report} />);

    expect(screen.getByText("Wet fraction by local month")).toBeInTheDocument();
    expect(screen.getByText("Wet fraction by local hour")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Charts show" })).toHaveValue("osm:way:1");
  });
});
