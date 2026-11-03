import type { Coverage, PlaceRecord, Report, WetnessGeoJSON } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    throw new Error(`${response.status}: ${await response.text()}`);
  }
  return (await response.json()) as T;
}

export function getCoverage(): Promise<Coverage> {
  return request<Coverage>("/api/coverage");
}

export function getPlaces(kind?: string): Promise<{ places: PlaceRecord[] }> {
  const query = kind ? `?kind=${encodeURIComponent(kind)}` : "";
  return request<{ places: PlaceRecord[] }>(`/api/places${query}`);
}

export function getWetness(params: {
  start?: string;
  end?: string;
  threshold?: number;
}): Promise<WetnessGeoJSON> {
  const search = new URLSearchParams();
  if (params.start) search.set("start", params.start);
  if (params.end) search.set("end", params.end);
  if (params.threshold !== undefined) search.set("threshold", String(params.threshold));
  const query = search.toString();
  return request<WetnessGeoJSON>(`/api/wetness${query ? `?${query}` : ""}`);
}

export function postReport(body: {
  place_keys: string[];
  start?: string;
  end?: string;
  threshold_mm_per_hour?: number;
}): Promise<Report> {
  return request<Report>("/api/report", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
