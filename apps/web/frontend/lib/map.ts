import type { PlaceRecord, WetnessGeoJSON } from "./types";

export type Rgba = [number, number, number, number];

export interface NodeDatum {
  position: [number, number];
  wetHours: number;
  wetFraction: number | null;
}

export interface PlaceOutlineFeature {
  type: "Feature";
  geometry: Exclude<PlaceRecord["geometry"], { type: "Point" }>;
  properties: Pick<PlaceRecord, "place_key" | "name" | "kind">;
}

export interface PlaceOutlineCollection {
  type: "FeatureCollection";
  features: PlaceOutlineFeature[];
}

export function colorFor(wetFraction: number | null): Rgba {
  if (wetFraction === null || !Number.isFinite(wetFraction)) {
    return [200, 200, 200, 140];
  }
  const t = Math.max(0, Math.min(wetFraction / 0.2, 1));
  return [
    Math.round(68 + t * (253 - 68)),
    Math.round(1 + t * (231 - 1)),
    Math.round(84 + t * (37 - 84)),
    230,
  ];
}

export function radiusForWetHours(wetHours: number): number {
  return 4 + Math.max(0, Math.min(wetHours, 24));
}

export function nodeDataFromWetness(
  wetness: WetnessGeoJSON | null,
): NodeDatum[] {
  return (wetness?.features ?? []).map((feature) => ({
    position: feature.geometry.coordinates,
    wetHours: feature.properties.wet_hours,
    wetFraction: feature.properties.wet_fraction,
  }));
}

export function tooltipTextFor(object: unknown): string | null {
  if (typeof object !== "object" || object === null) return null;

  if (
    "properties" in object &&
    typeof object.properties === "object" &&
    object.properties !== null
  ) {
    return tooltipTextFor(object.properties);
  }

  if (
    "wetHours" in object &&
    typeof object.wetHours === "number" &&
    "wetFraction" in object &&
    (typeof object.wetFraction === "number" || object.wetFraction === null)
  ) {
    const share =
      object.wetFraction === null
        ? ""
        : ` (${(object.wetFraction * 100).toFixed(1)}%)`;
    return `${object.wetHours} wet hours${share}`;
  }

  if (
    "name" in object &&
    typeof object.name === "string" &&
    "kind" in object &&
    typeof object.kind === "string"
  ) {
    return `${object.name} · ${object.kind}`;
  }
  return null;
}

export function placeCoordinates(place: PlaceRecord): [number, number] {
  return [place.lon, place.lat];
}

export function outlineFeaturesFromPlaces(
  places: PlaceRecord[],
): PlaceOutlineCollection {
  return {
    type: "FeatureCollection",
    features: places.flatMap((place) => {
      if (place.geometry.type === "Point") return [];
      return [
        {
          type: "Feature",
          geometry: place.geometry,
          properties: {
            place_key: place.place_key,
            name: place.name,
            kind: place.kind,
          },
        },
      ];
    }),
  };
}
