import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { PlaceRecord, WetnessGeoJSON } from "@/lib/types";

vi.mock("@deck.gl/react", () => ({ default: () => null }));
vi.mock("@deck.gl/layers", () => ({
  GeoJsonLayer: class TestGeoJsonLayer {
    constructor(_props: unknown) {}
  },
  ScatterplotLayer: class TestScatterplotLayer {
    constructor(_props: unknown) {}
  },
}));
vi.mock("react-map-gl/maplibre", () => ({ default: () => null }));
vi.mock("maplibre-gl", () => ({ setWorkerUrl: vi.fn() }));

import WetnessMap from "@/components/WetnessMap";

const mapData: WetnessGeoJSON = {
  type: "FeatureCollection",
  features: [
    {
      type: "Feature",
      geometry: { type: "Point", coordinates: [-70.9, 42.5] },
      properties: { wet_hours: 2, valid_hours: 4, wet_fraction: 0.5 },
    },
  ],
};

const course: PlaceRecord = {
  place_key: "osm:way:1",
  name: "Test Course",
  kind: "golf_course",
  lat: 42.5,
  lon: -70.9,
  timezone: "America/New_York",
  geometry: { type: "Point", coordinates: [-70.9, 42.5] },
};

describe("WetnessMap component", () => {
  it("renders the map panel without creating a network or WebGL renderer", () => {
    const { container } = render(<WetnessMap wetness={mapData} places={[course]} />);

    expect(screen.getByRole("heading", { name: "Wet hours by node" })).toBeInTheDocument();
    expect(container.querySelector(".panel")).not.toBeNull();
  });
});
