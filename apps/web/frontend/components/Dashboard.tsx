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
import { selectionAfterToggle } from "@/lib/presentation";
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
    const controller = new AbortController();
    getCoverage({ signal: controller.signal })
      .then((cov) => {
        setCoverage(cov);
        setStart(cov.observed_start?.slice(0, 10) ?? "");
        setEnd(cov.observed_end?.slice(0, 10) ?? "");
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(String(err));
      });
    getPlaces(undefined, { signal: controller.signal })
      .then((res) => {
        setPlaces(res.places);
        setSelectedKeys(res.places.slice(0, MAX_PLACES).map((p) => p.place_key));
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(String(err));
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!coverage?.gridded) return;
    const controller = new AbortController();
    getWetness(
      { start: start || undefined, end: end || undefined, threshold },
      { signal: controller.signal },
    )
      .then(setWetness)
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(String(err));
      });
    return () => controller.abort();
  }, [coverage?.gridded, start, end, threshold]);

  useEffect(() => {
    if (selectedKeys.length === 0) {
      setReport(null);
      return;
    }
    setReport(null);
    const controller = new AbortController();
    postReport(
      {
        place_keys: selectedKeys,
        start: start || undefined,
        end: end || undefined,
        threshold_mm_per_hour: threshold,
      },
      { signal: controller.signal },
    )
      .then(setReport)
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(String(err));
      });
    return () => controller.abort();
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
    setSelectedKeys((keys) => selectionAfterToggle(keys, key, MAX_PLACES));
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
            min={coverage?.observed_start?.slice(0, 10) ?? undefined}
            max={end || coverage?.observed_end?.slice(0, 10) || undefined}
            onChange={(e) => setStart(e.target.value)}
          />
        </div>

        <div className="control">
          <label htmlFor="end">End</label>
          <input
            id="end"
            type="date"
            value={end}
            min={start || coverage?.observed_start?.slice(0, 10) || undefined}
            max={coverage?.observed_end?.slice(0, 10) ?? undefined}
            onChange={(e) => setEnd(e.target.value)}
          />
        </div>
      </div>

      <fieldset className="control place-control">
        <legend>Places ({selectedKeys.length} selected, up to {MAX_PLACES})</legend>
        <div className="place-select">
          {visiblePlaces.map((place) => (
            <label key={place.place_key}>
              <input
                type="checkbox"
                checked={selectedKeys.includes(place.place_key)}
                disabled={
                  !selectedKeys.includes(place.place_key) &&
                  selectedKeys.length >= MAX_PLACES
                }
                onChange={() => togglePlace(place.place_key)}
              />
              {place.name}
            </label>
          ))}
        </div>
      </fieldset>

      {report && <Leaderboard report={report} />}
      {report && <Charts report={report} />}
      {coverage?.gridded && (
        <WetnessMap wetness={wetness} places={selectedPlaces} />
      )}
    </main>
  );
}
