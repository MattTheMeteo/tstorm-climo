"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getCoverage,
  getPlaces,
  getWetness,
  postReport,
} from "@/lib/api";
import type {
  Coverage,
  PlaceRecord,
  Report,
  WetnessGeoJSON,
} from "@/lib/types";
import Charts from "./Charts";
import Leaderboard from "./Leaderboard";
import WetnessMap from "./WetnessMap";

const MAX_PLACES = 20;

export default function Dashboard() {
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [places, setPlaces] = useState<PlaceRecord[]>([]);
  const [kind, setKind] = useState("all");
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [threshold, setThreshold] = useState(0.05);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [wetness, setWetness] = useState<WetnessGeoJSON | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCoverage()
      .then((cov) => {
        setCoverage(cov);
        setStart(cov.observed_start?.slice(0, 10) ?? "");
        setEnd(cov.observed_end?.slice(0, 10) ?? "");
      })
      .catch((err) => setError(String(err)));
    getPlaces()
      .then((res) => {
        setPlaces(res.places);
        setSelectedKeys(res.places.slice(0, MAX_PLACES).map((p) => p.place_key));
      })
      .catch((err) => setError(String(err)));
  }, []);

  useEffect(() => {
    if (!coverage?.gridded) return;
    getWetness({ threshold })
      .then(setWetness)
      .catch((err) => setError(String(err)));
  }, [coverage?.gridded, threshold]);

  useEffect(() => {
    if (selectedKeys.length === 0) {
      setReport(null);
      return;
    }
    postReport({
      place_keys: selectedKeys,
      start: start || undefined,
      end: end || undefined,
      threshold_mm_per_hour: threshold,
    })
      .then(setReport)
      .catch((err) => setError(String(err)));
  }, [selectedKeys, start, end, threshold]);

  const kinds = useMemo(
    () => ["all", ...(coverage?.kinds ?? [])],
    [coverage?.kinds],
  );

  const visiblePlaces = useMemo(
    () => (kind === "all" ? places : places.filter((p) => p.kind === kind)),
    [places, kind],
  );

  const selectedPlaces = useMemo(
    () => places.filter((p) => selectedKeys.includes(p.place_key)),
    [places, selectedKeys],
  );

  const onKindChange = useCallback(
    (value: string) => {
      setKind(value);
      const subset =
        value === "all" ? places : places.filter((p) => p.kind === value);
      setSelectedKeys(subset.slice(0, MAX_PLACES).map((p) => p.place_key));
    },
    [places],
  );

  const togglePlace = useCallback((key: string) => {
    setSelectedKeys((keys) =>
      keys.includes(key) ? keys.filter((k) => k !== key) : [...keys, key],
    );
  }, []);

  return (
    <main>
      <h1>North Shore rain climatology</h1>
      <p className="muted">
        {coverage
          ? `${coverage.place_count} places · ${coverage.variable ?? ""} (${coverage.units ?? ""}) · ${coverage.observed_start?.slice(0, 10) ?? ""} → ${coverage.observed_end?.slice(0, 10) ?? ""}`
          : "Loading…"}
      </p>

      {error && <div className="error">{error}</div>}

      <div className="controls">
        <div className="control">
          <label htmlFor="kind">Attraction kind</label>
          <select
            id="kind"
            value={kind}
            onChange={(e) => onKindChange(e.target.value)}
          >
            {kinds.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </div>

        <div className="control">
          <label htmlFor="threshold">Wet threshold (mm/h)</label>
          <input
            id="threshold"
            type="number"
            min={0}
            max={2}
            step={0.01}
            value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))}
          />
        </div>

        <div className="control">
          <label htmlFor="start">Start</label>
          <input
            id="start"
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
        </div>

        <div className="control">
          <label htmlFor="end">End</label>
          <input
            id="end"
            type="date"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
          />
        </div>
      </div>

      <div className="control" style={{ marginBottom: "1rem" }}>
        <label>Places ({selectedKeys.length} selected)</label>
        <div className="place-select">
          {visiblePlaces.map((place) => (
            <label key={place.place_key}>
              <input
                type="checkbox"
                checked={selectedKeys.includes(place.place_key)}
                onChange={() => togglePlace(place.place_key)}
              />
              {place.name}
            </label>
          ))}
        </div>
      </div>

      {report && <Leaderboard report={report} />}
      {report && <Charts report={report} />}
      {coverage?.gridded && (
        <WetnessMap wetness={wetness} places={selectedPlaces} />
      )}
    </main>
  );
}
