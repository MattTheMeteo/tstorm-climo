import {
  CoverageSchema,
  PlacesSchema,
  ReportSchema,
  WetnessGeoJSONSchema,
} from "./schemas";
import type {
  Coverage,
  PlaceRecord,
  Report,
  ReportRequest,
  WetnessGeoJSON,
  WetnessParams,
} from "./types";

const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);

async function request<T>(
  path: string,
  schema: { parse: (data: unknown) => T },
  init?: Pick<RequestInit, "signal" | "method" | "headers" | "body">,
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
    ...init,
  });
  if (!response.ok) {
    throw new Error(`${response.status}: ${await response.text()}`);
  }
  const data: unknown = await response.json();
  return schema.parse(data);
}

export function buildPlacesPath(kind?: string): string {
  const query = new URLSearchParams();
  if (kind) query.set("kind", kind);
  const search = query.toString();
  return `/api/places${search ? `?${search}` : ""}`;
}

export function buildWetnessPath(params: WetnessParams): string {
  const search = new URLSearchParams();
  if (params.start) search.set("start", params.start);
  if (params.end) search.set("end", params.end);
  if (params.threshold !== undefined) {
    search.set("threshold", String(params.threshold));
  }
  const query = search.toString();
  return `/api/wetness${query ? `?${query}` : ""}`;
}

export function getCoverage(
  init?: Pick<RequestInit, "signal">,
): Promise<Coverage> {
  return request<Coverage>("/api/coverage", CoverageSchema, init);
}

export function getPlaces(
  kind?: string,
  init?: Pick<RequestInit, "signal">,
): Promise<{ places: PlaceRecord[] }> {
  return request(buildPlacesPath(kind), PlacesSchema, init);
}

export function getWetness(
  params: WetnessParams,
  init?: Pick<RequestInit, "signal">,
): Promise<WetnessGeoJSON> {
  return request(buildWetnessPath(params), WetnessGeoJSONSchema, init);
}

export function postReport(
  body: ReportRequest,
  init?: Pick<RequestInit, "signal">,
): Promise<Report> {
  return request("/api/report", ReportSchema, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...init,
  });
}
