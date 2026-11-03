"use client";

import { useMemo } from "react";
import DeckGL from "@deck.gl/react";
import { ScatterplotLayer } from "@deck.gl/layers";
import Map from "react-map-gl/maplibre";
import type { PlaceRecord, WetnessGeoJSON } from "@/lib/types";

// A free, key-less basemap (Carto Positron).
const MAP_STYLE = "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json";

type Rgba = [number, number, number, number];

const PLACE_COLOR: Rgba = [17, 24, 39, 255];

interface NodeDatum {
  position: [number, number];
  wetHours: number;
  wetFraction: number | null;
}

function colorFor(wetFraction: number | null): Rgba {
  if (wetFraction == null) return [200, 200, 200, 140];
  // Approximate viridis: dark blue -> yellow as the wet share grows.
  const t = Math.min(wetFraction / 0.2, 1);
  return [
    Math.round(68 + t * (253 - 68)),
    Math.round(1 + t * (231 - 1)),
    Math.round(84 + t * (37 - 84)),
    230,
  ];
}

export default function WetnessMap({
  wetness,
  places,
}: {
  wetness: WetnessGeoJSON | null;
  places: PlaceRecord[];
}) {
  const nodeData: NodeDatum[] = useMemo(
    () =>
      (wetness?.features ?? []).map((feature) => ({
        position: feature.geometry.coordinates,
        wetHours: feature.properties.wet_hours,
        wetFraction: feature.properties.wet_fraction,
      })),
    [wetness],
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
      getRadius: (d) => 4 + Math.min(d.wetHours, 24),
      radiusUnits: "pixels",
      pickable: true,
    }),
    new ScatterplotLayer<PlaceRecord>({
      id: "places",
      data: places,
      getPosition: (d) => [d.lon, d.lat],
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
            if (!object) return null;
            if ("wetHours" in object) {
              const node = object as NodeDatum;
              const share =
                node.wetFraction != null
                  ? ` (${(node.wetFraction * 100).toFixed(1)}%)`
                  : "";
              return { text: `${node.wetHours} wet hours${share}` };
            }
            const place = object as PlaceRecord;
            return { text: `${place.name} · ${place.kind}` };
          }}
        >
          <Map mapStyle={MAP_STYLE} />
        </DeckGL>
      </div>
    </section>
  );
}
