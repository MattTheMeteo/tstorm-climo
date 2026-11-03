import type { HourlyRow, MonthlyRow, PlaceRecord, SummaryRow } from "./types";

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

export function formatReliability(value: number | null): string {
  return value === null ? "Unavailable" : value.toFixed(2);
}

export function orderLeaderboardRows(rows: SummaryRow[]): SummaryRow[] {
  return [...rows].sort((left, right) => {
    if (left.wet_fraction === null && right.wet_fraction === null) {
      return left.place_key.localeCompare(right.place_key);
    }
    if (left.wet_fraction === null) return 1;
    if (right.wet_fraction === null) return -1;
    return (
      right.wet_fraction - left.wet_fraction ||
      left.place_key.localeCompare(right.place_key)
    );
  });
}

export function monthlyChartData(
  rows: MonthlyRow[],
  placeKey: string,
): { label: string; wet_fraction: number | null }[] {
  return rows
    .filter((row) => row.place_key === placeKey)
    .toSorted((left, right) => left.local_month - right.local_month)
    .map((row) => ({
      label: MONTHS[row.local_month - 1],
      wet_fraction: row.wet_fraction,
    }));
}

export function hourlyChartData(
  rows: HourlyRow[],
  placeKey: string,
): { label: number; wet_fraction: number | null }[] {
  return rows
    .filter((row) => row.place_key === placeKey)
    .toSorted((left, right) => left.local_hour - right.local_hour)
    .map((row) => ({
      label: row.local_hour,
      wet_fraction: row.wet_fraction,
    }));
}

export function selectionAfterToggle(
  selectedKeys: string[],
  placeKey: string,
  maximum: number,
): string[] {
  if (selectedKeys.includes(placeKey)) {
    return selectedKeys.filter((key) => key !== placeKey);
  }
  return selectedKeys.length < maximum ? [...selectedKeys, placeKey] : selectedKeys;
}

export function defaultChartPlace(
  summary: SummaryRow[],
  places: PlaceRecord[],
): string {
  return (
    orderLeaderboardRows(summary)[0]?.place_key ?? places[0]?.place_key ?? ""
  );
}
