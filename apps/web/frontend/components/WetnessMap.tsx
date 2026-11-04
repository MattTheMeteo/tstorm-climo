"use client";

import { useMemo } from "react";
import DeckGL from "@deck.gl/react";
import { GeoJsonLayer, ScatterplotLayer } from "@deck.gl/layers";
import { setWorkerUrl } from "maplibre-gl";
import Map from "react-map-gl/maplibre";
import type { PlaceRecord, WetnessGeoJSON } from "@/lib/types";
import {
  colorFor,
  nodeDataFromWetness,
  outlineFeaturesFromPlaces,
  placeCoordinates,
  radiusForWetHours,
  tooltipTextFor,
  type NodeDatum,
  type PlaceOutlineFeature,
  type Rgba,
} from "@/lib/map";

// A free, key-less basemap (Carto Positron).
const MAP_STYLE = "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json";

setWorkerUrl(
  new URL("maplibre-gl/dist/maplibre-gl-worker.mjs", import.meta.url).toString(),
);

const PLACE_COLOR: Rgba = [17, 24, 39, 255];

export default function WetnessMap({
  wetness,
  places,
}: {
  wetness: WetnessGeoJSON | null;
  places: PlaceRecord[];
}) {
  const nodeData: NodeDatum[] = useMemo(
    () => nodeDataFromWetness(wetness),
    [wetness],
  );
  const placeOutlines = useMemo(
    () => outlineFeaturesFromPlaces(places),
    [places],
  );

  const initialViewState = useMemo(() => {
    const features = wetness?.features ?? [];
    if (features.length === 0) {
      return { longitude: -70.9, latitude: 42.5, zoom: 9 };
    }
    const longitudes = features.map((f) => f.geometry.coordinates[0]);
    const latitudes = features.map((f) => f.geometry.coordinates[1]);
    return {
      longitude: (Math.min(...longitudes) + Math.max(...longitudes)) / 2,
      latitude: (Math.min(...latitudes) + Math.max(...latitudes)) / 2,
      zoom: 9,
    };
  }, [wetness]);

  const layers = [
    new ScatterplotLayer<NodeDatum>({
      id: "wetness-nodes",
      data: nodeData,
      getPosition: (d) => d.position,
      getFillColor: (d) => colorFor(d.wetFraction),
      getRadius: (d) => radiusForWetHours(d.wetHours),
      radiusUnits: "pixels",
      pickable: true,
    }),
    new GeoJsonLayer<PlaceOutlineFeature["properties"]>({
      id: "place-footprints",
      data: placeOutlines,
      filled: false,
      stroked: true,
      getLineColor: PLACE_COLOR,
      lineWidthUnits: "pixels",
      lineWidthMinPixels: 2,
      pickable: true,
    }),
    new ScatterplotLayer<PlaceRecord>({
      id: "places",
      data: places,
      getPosition: placeCoordinates,
      getFillColor: PLACE_COLOR,
      getRadius: 5,
      radiusUnits: "pixels",
      pickable: true,
    }),
  ];

  return (
    <section className="panel">
      <h2>Wet hours by node</h2>
      <div style={{ width: "100%", height: 420, position: "relative" }}>
        <DeckGL
          initialViewState={initialViewState}
          controller
          layers={layers}
          getTooltip={({ object }) => {
            const text = tooltipTextFor(object);
            return text === null ? null : { text };
          }}
        >
          <Map mapStyle={MAP_STYLE} />
        </DeckGL>
      </div>
    </section>
  );
}
