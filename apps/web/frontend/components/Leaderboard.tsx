"use client";

import type { Report } from "@/lib/types";

const PERCENT = new Intl.NumberFormat("en-US", {
  style: "percent",
  maximumFractionDigits: 1,
});

export default function Leaderboard({ report }: { report: Report }) {
  const rows = [...report.summary].sort(
    (a, b) => (b.wet_fraction ?? 0) - (a.wet_fraction ?? 0),
  );

  return (
    <section className="panel">
      <h2>Leaderboard</h2>
      {report.reliability != null && (
        <p className="reliability">
          Ranking reliability (split-half r) ={" "}
          <strong>{report.reliability.toFixed(2)}</strong>. Below ~0.5 the
          order of neighbouring places is mostly sampling noise.
        </p>
      )}
      <table className="leaderboard">
        <thead>
          <tr>
            <th>Place</th>
            <th>Kind</th>
            <th>Wet share</th>
            <th>Wet hours</th>
            <th>Rain (mm)</th>
            <th>Valid hours</th>
            <th>Tied</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.place_key}>
              <td>{row.name}</td>
              <td>{row.kind}</td>
              <td>
                {row.wet_fraction != null ? PERCENT.format(row.wet_fraction) : "—"}
              </td>
              <td>{row.wet_hours}</td>
              <td>{row.rain_mm != null ? row.rain_mm.toFixed(1) : "—"}</td>
              <td>{row.valid_hours}</td>
              <td>{row.tied ? "yes" : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
