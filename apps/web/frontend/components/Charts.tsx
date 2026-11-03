"use client";

import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Report } from "@/lib/types";

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
];

export default function Charts({ report }: { report: Report }) {
  const wettest = useMemo(
    () =>
      [...report.summary].sort(
        (a, b) => (b.wet_fraction ?? 0) - (a.wet_fraction ?? 0),
      )[0],
    [report.summary],
  );

  const [focusKey, setFocusKey] = useState<string | null>(null);
  const focus =
    focusKey && report.places.some((p) => p.place_key === focusKey)
      ? focusKey
      : (wettest?.place_key ?? report.places[0]?.place_key ?? "");

  const monthlyData = useMemo(
    () =>
      report.monthly
        .filter((row) => row.place_key === focus)
        .map((row) => ({
          label: MONTHS[row.local_month - 1],
          wet_fraction: row.wet_fraction ?? 0,
        })),
    [report.monthly, focus],
  );

  const hourlyData = useMemo(
    () =>
      report.hourly
        .filter((row) => row.place_key === focus)
        .map((row) => ({
          label: row.local_hour,
          wet_fraction: row.wet_fraction ?? 0,
        })),
    [report.hourly, focus],
  );

  return (
    <section className="panel">
      <div className="control" style={{ marginBottom: "0.5rem" }}>
        <label htmlFor="focus">Charts show</label>
        <select
          id="focus"
          value={focus}
          onChange={(event) => setFocusKey(event.target.value)}
        >
          {report.places.map((place) => (
            <option key={place.place_key} value={place.place_key}>
              {place.name}
            </option>
          ))}
        </select>
      </div>

      <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
        <div style={{ width: 360, height: 220 }}>
          <h3 style={{ fontSize: "0.9rem" }}>Wet fraction by local month</h3>
          <ResponsiveContainer>
            <BarChart data={monthlyData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="label" />
              <YAxis tickFormatter={(value) => `${(value * 100).toFixed(0)}%`} />
              <Tooltip />
              <Bar dataKey="wet_fraction" fill="#3b82f6" />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div style={{ width: 360, height: 220 }}>
          <h3 style={{ fontSize: "0.9rem" }}>Wet fraction by local hour</h3>
          <ResponsiveContainer>
            <BarChart data={hourlyData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="label" />
              <YAxis tickFormatter={(value) => `${(value * 100).toFixed(0)}%`} />
              <Tooltip />
              <Bar dataKey="wet_fraction" fill="#3b82f6" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </section>
  );
}
