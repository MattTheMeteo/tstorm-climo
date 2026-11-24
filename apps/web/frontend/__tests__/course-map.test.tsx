import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { PlaceRecord } from "@/lib/types";

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

import CourseMap from "@/components/CourseMap";

const course: PlaceRecord = {
  place_key: "osm:way:1",
  name: "Test Course",
  kind: "golf_course",
  lat: 42.5,
  lon: -70.9,
  timezone: "America/New_York",
  geometry: { type: "Point", coordinates: [-70.9, 42.5] },
};

describe("CourseMap component", () => {
  it("renders the golf course map panel", () => {
    const { container } = render(
      <CourseMap
        places={[course]}
        selectedKeys={[]}
        onTogglePlace={vi.fn()}
        isDark={false}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "North Shore golf courses" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Select a course on the map to investigate."),
    ).toBeInTheDocument();
    expect(container.querySelector(".panel")).not.toBeNull();
  });
});
