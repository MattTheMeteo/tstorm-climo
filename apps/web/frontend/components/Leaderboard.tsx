"use client";

import type { Report } from "@/lib/types";
import { formatReliability, orderLeaderboardRows } from "@/lib/presentation";

const PERCENT = new Intl.NumberFormat("en-US", {
  style: "percent",
  maximumFractionDigits: 1,
});

export default function Leaderboard({ report }: { report: Report }) {
  const rows = orderLeaderboardRows(report.summary);

  return (
    <section className="panel">
      <h2>Leaderboard</h2>
      <p className="muted">Sorted by wet-hour share (wettest first).</p>
      <p className="reliability" role="note" aria-label="Ranking reliability">
        Ranking reliability (split-half r):{" "}
        <strong>{formatReliability(report.reliability)}</strong>
        {report.reliability === null
          ? ". It could not be estimated for this selection and period."
          : ". Below ~0.5, the order of neighbouring places is mostly sampling noise."}
      </p>
      <div className="table-scroll">
        <table className="leaderboard">
          <caption className="sr-only">
            Places ordered by wet-hour share, wettest first.
          </caption>
          <thead>
            <tr>
              <th scope="col">Place</th>
              <th scope="col">Kind</th>
              <th scope="col">Wet share</th>
              <th scope="col">Wet hours</th>
              <th scope="col">Rain (mm)</th>
              <th scope="col">Valid hours</th>
              <th scope="col">Tied</th>
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
      </div>
    </section>
  );
}
