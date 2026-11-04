import { z } from "zod";

const finiteNumber = z.number().finite();
const nonnegativeInteger = z.number().int().nonnegative();

const positionSchema = z.tuple([
  finiteNumber.min(-180).max(180),
  finiteNumber.min(-90).max(90),
]);

export const PointGeometrySchema = z.object({
  type: z.literal("Point"),
  coordinates: positionSchema,
});

export const PolygonGeometrySchema = z.object({
  type: z.literal("Polygon"),
  coordinates: z.array(z.array(positionSchema)),
});

export const MultiPolygonGeometrySchema = z.object({
  type: z.literal("MultiPolygon"),
  coordinates: z.array(z.array(z.array(positionSchema))),
});

export const GeoJSONGeometrySchema = z.discriminatedUnion("type", [
  PointGeometrySchema,
  PolygonGeometrySchema,
  MultiPolygonGeometrySchema,
]);

export const PlaceRecordSchema = z.object({
  place_key: z.string(),
  name: z.string(),
  kind: z.string(),
  lat: finiteNumber.min(-90).max(90),
  lon: finiteNumber.min(-180).max(180),
  timezone: z.string(),
  geometry: GeoJSONGeometrySchema,
});

export const PlacesSchema = z.object({ places: z.array(PlaceRecordSchema) });

export const CoverageSchema = z.object({
  observed_start: z.string().nullable(),
  observed_end: z.string().nullable(),
  place_count: nonnegativeInteger,
  kinds: z.array(z.string()),
  variable: z.string().nullable(),
  units: z.string().nullable(),
  gridded: z.boolean(),
});

export const SummaryRowSchema = z.object({
  place_key: z.string(),
  name: z.string(),
  kind: z.string(),
  wet_fraction: finiteNumber.min(0).max(1).nullable(),
  wet_hours: nonnegativeInteger,
  valid_hours: nonnegativeInteger,
  rain_mm: finiteNumber.nonnegative().nullable(),
  coverage: finiteNumber.min(0).max(1).nullable(),
  tied: z.boolean(),
  cell_places: nonnegativeInteger,
  rank: z.number().int().positive(),
});

export const MonthlyRowSchema = z.object({
  place_key: z.string(),
  local_month: z.number().int().min(1).max(12),
  name: z.string(),
  kind: z.string(),
  valid_hours: nonnegativeInteger,
  wet_hours: nonnegativeInteger,
  rain_mm: finiteNumber.nonnegative().nullable(),
  wet_fraction: finiteNumber.min(0).max(1).nullable(),
});

export const HourlyRowSchema = z.object({
  place_key: z.string(),
  local_hour: z.number().int().min(0).max(23),
  name: z.string(),
  kind: z.string(),
  hours_observed: nonnegativeInteger,
  wet_hours: nonnegativeInteger,
  rain_mm: finiteNumber.nonnegative().nullable(),
  wet_fraction: finiteNumber.min(0).max(1).nullable(),
});

export const ReportSchema = z.object({
  summary: z.array(SummaryRowSchema),
  monthly: z.array(MonthlyRowSchema),
  hourly: z.array(HourlyRowSchema),
  reliability: finiteNumber.min(-1).max(1).nullable(),
  start: z.string().nullable(),
  end: z.string().nullable(),
  places: z.array(PlaceRecordSchema),
});

const WetnessFeatureSchema = z.object({
  type: z.literal("Feature"),
  geometry: PointGeometrySchema,
  properties: z.object({
    wet_hours: nonnegativeInteger,
    valid_hours: nonnegativeInteger,
    wet_fraction: finiteNumber.min(0).max(1).nullable(),
  }),
});

export const WetnessGeoJSONSchema = z.object({
  type: z.literal("FeatureCollection"),
  features: z.array(WetnessFeatureSchema),
});
