import type { z } from "zod";
import type {
  CoverageSchema,
  HourlyRowSchema,
  MonthlyRowSchema,
  PlaceRecordSchema,
  ReportSchema,
  SummaryRowSchema,
  WetnessGeoJSONSchema,
} from "./schemas";

export type PlaceRecord = z.infer<typeof PlaceRecordSchema>;
export type Coverage = z.infer<typeof CoverageSchema>;
export type SummaryRow = z.infer<typeof SummaryRowSchema>;
export type MonthlyRow = z.infer<typeof MonthlyRowSchema>;
export type HourlyRow = z.infer<typeof HourlyRowSchema>;
export type Report = z.infer<typeof ReportSchema>;
export type WetnessGeoJSON = z.infer<typeof WetnessGeoJSONSchema>;

export interface ReportRequest {
  place_keys: string[];
  start?: string;
  end?: string;
  threshold_mm_per_hour?: number;
}

export interface WetnessParams {
  start?: string;
  end?: string;
  threshold?: number;
}
