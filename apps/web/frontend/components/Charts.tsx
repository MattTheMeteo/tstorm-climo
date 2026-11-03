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
import {
  defaultChartPlace,
  hourlyChartData,
  monthlyChartData,
} from "@/lib/presentation";

export default function Charts({ report }: { report: Report }) {
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const focus =
    focusKey && report.places.some((p) => p.place_key === focusKey)
      ? focusKey
      : defaultChartPlace(report.summary, report.places);

  const monthlyData = useMemo(
    () => monthlyChartData(report.monthly, focus),
    [report.monthly, focus],
  );

  const hourlyData = useMemo(
    () => hourlyChartData(report.hourly, focus),
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
