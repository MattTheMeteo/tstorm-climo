export interface PointGeometry {
  type: "Point";
  coordinates: [number, number];
}

export interface PlaceRecord {
  place_key: string;
  name: string;
  kind: string;
  lat: number;
  lon: number;
  timezone: string;
  geometry: PointGeometry;
}

export interface Coverage {
  observed_start: string | null;
  observed_end: string | null;
  place_count: number;
  kinds: string[];
  variable: string | null;
  units: string | null;
  gridded: boolean;
}

export interface SummaryRow {
  place_key: string;
  name: string;
  kind: string;
  wet_fraction: number | null;
  wet_hours: number;
  valid_hours: number;
  rain_mm: number | null;
  coverage: number | null;
  tied: boolean;
  cell_places: number;
  rank: number;
}

export interface MonthlyRow {
  place_key: string;
  local_month: number;
  name: string;
  kind: string;
  valid_hours: number;
  wet_hours: number;
  rain_mm: number | null;
  wet_fraction: number | null;
}

export interface HourlyRow {
  place_key: string;
  local_hour: number;
  name: string;
  kind: string;
  hours_observed: number;
  wet_hours: number;
  rain_mm: number | null;
  wet_fraction: number | null;
}

export interface Report {
  summary: SummaryRow[];
  monthly: MonthlyRow[];
  hourly: HourlyRow[];
  reliability: number | null;
  start: string | null;
  end: string | null;
  places: PlaceRecord[];
}

export interface WetnessFeature {
  type: "Feature";
  geometry: PointGeometry;
  properties: {
    wet_hours: number;
    valid_hours: number;
    wet_fraction: number | null;
  };
}

export interface WetnessGeoJSON {
  type: "FeatureCollection";
  features: WetnessFeature[];
}
