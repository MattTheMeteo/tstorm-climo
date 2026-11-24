"use client";

import { useMemo } from "react";
import DeckGL from "@deck.gl/react";
import { GeoJsonLayer, ScatterplotLayer } from "@deck.gl/layers";
import { setWorkerUrl } from "maplibre-gl";
import Map from "react-map-gl/maplibre";
import type { PlaceRecord } from "@/lib/types";
import {
  outlineFeaturesFromPlaces,
  placeCoordinates,
  tooltipTextFor,
  type PlaceOutlineFeature,
  type Rgba,
} from "@/lib/map";

const MAP_STYLES = {
  light: "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
  dark: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
};

setWorkerUrl(
  new URL("maplibre-gl/dist/maplibre-gl-worker.mjs", import.meta.url).toString(),
);

export default function CourseMap({
  places,
  selectedKeys,
  onTogglePlace,
  isDark,
}: {
  places: PlaceRecord[];
  selectedKeys: string[];
  onTogglePlace: (placeKey: string) => void;
  isDark: boolean;
}) {
  const placeOutlines = useMemo(
    () => outlineFeaturesFromPlaces(places),
    [places],
  );
  const selected = useMemo(() => new Set(selectedKeys), [selectedKeys]);
  const courseColor: Rgba = isDark ? [226, 232, 240, 245] : [51, 65, 85, 220];
  const selectedColor: Rgba = isDark ? [45, 212, 191, 255] : [13, 148, 136, 255];

  const initialViewState = useMemo(() => {
    if (places.length === 0) {
      return { longitude: -70.9, latitude: 42.5, zoom: 9 };
    }
    const longitudes = places.map((place) => place.lon);
    const latitudes = places.map((place) => place.lat);
    return {
      longitude: (Math.min(...longitudes) + Math.max(...longitudes)) / 2,
      latitude: (Math.min(...latitudes) + Math.max(...latitudes)) / 2,
      zoom: 9,
    };
  }, [places]);

  const layers = [
    new GeoJsonLayer<PlaceOutlineFeature["properties"]>({
      id: "place-footprints",
      data: placeOutlines,
      filled: false,
      stroked: true,
      getLineColor: (feature) =>
        selected.has(feature.properties?.place_key ?? "")
          ? selectedColor
          : courseColor,
      lineWidthUnits: "pixels",
      lineWidthMinPixels: 1,
      pickable: true,
      onClick: (info) => {
        const object = info.object;
        if (
          typeof object === "object" &&
          object !== null &&
          "properties" in object &&
          typeof object.properties === "object" &&
          object.properties !== null &&
          "place_key" in object.properties &&
          typeof object.properties.place_key === "string"
        ) {
          onTogglePlace(object.properties.place_key);
        }
      },
    }),
    new ScatterplotLayer<PlaceRecord>({
      id: "places",
      data: places,
      getPosition: placeCoordinates,
      getFillColor: (place) =>
        selected.has(place.place_key) ? selectedColor : courseColor,
      getRadius: (place) => (selected.has(place.place_key) ? 8 : 5),
      radiusUnits: "pixels",
      pickable: true,
      autoHighlight: true,
      onClick: ({ object }) => {
        if (object) onTogglePlace(object.place_key);
      },
    }),
  ];

  return (
    <section className="panel course-map-panel">
      <div className="course-map-heading">
        <h2>North Shore golf courses</h2>
        <p className="muted">
          {selectedKeys.length === 0
            ? "Select a course on the map to investigate."
            : `${selectedKeys.length} selected · click another course to compare`}
        </p>
      </div>
      <div style={{ width: "100%", height: 420, position: "relative" }}>
        <DeckGL
          initialViewState={initialViewState}
          controller
          layers={layers}
          getTooltip={({ object }) => {
            const text = tooltipTextFor(object);
            return text === null ? null : { text };
          }}
          getCursor={({ isHovering }) => (isHovering ? "pointer" : "grab")}
        >
          <Map mapStyle={isDark ? MAP_STYLES.dark : MAP_STYLES.light} />
        </DeckGL>
      </div>
    </section>
  );
}
