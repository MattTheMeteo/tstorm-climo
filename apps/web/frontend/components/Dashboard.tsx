"use client";

import { useCallback, useEffect, useState } from "react";
import { getCoverage, getPlaces, postReport } from "@/lib/api";
import type { Coverage, PlaceRecord, Report } from "@/lib/types";
import { selectionAfterToggle } from "@/lib/presentation";
import Charts from "./Charts";
import Leaderboard from "./Leaderboard";
import CourseMap from "./CourseMap";

const MAX_PLACES = 20;
type Theme = "light" | "dark";

export default function Dashboard() {
  const [theme, setTheme] = useState<Theme | null>(null);
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [places, setPlaces] = useState<PlaceRecord[]>([]);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [threshold, setThreshold] = useState(0.05);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const storageKey = "course-climatology-theme";
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const savedTheme = window.localStorage.getItem(storageKey);
    setTheme(
      savedTheme === "dark" || savedTheme === "light"
        ? savedTheme
        : mediaQuery.matches
          ? "dark"
          : "light",
    );

    const handleSystemThemeChange = (event: MediaQueryListEvent) => {
      if (window.localStorage.getItem(storageKey) === null) {
        setTheme(event.matches ? "dark" : "light");
      }
    };
    mediaQuery.addEventListener("change", handleSystemThemeChange);
    return () =>
      mediaQuery.removeEventListener("change", handleSystemThemeChange);
  }, []);

  useEffect(() => {
    if (theme === null) return;
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme((current) => {
      const next = current === "dark" ? "light" : "dark";
      window.localStorage.setItem("course-climatology-theme", next);
      return next;
    });
  }, []);

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
    getPlaces("golf_course", { signal: controller.signal })
      .then((res) => {
        setPlaces(res.places);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(String(err));
      });
    return () => controller.abort();
  }, []);

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

  const togglePlace = useCallback((key: string) => {
    setSelectedKeys((keys) => selectionAfterToggle(keys, key, MAX_PLACES));
  }, []);
  const activeTheme = theme ?? "light";

  return (
    <main>
      <header className="page-header">
        <div>
          <h1>Course Climatology</h1>
          <p className="muted">
            {places.length > 0
              ? `Explore ${places.length} North Shore golf courses. Select up to ${MAX_PLACES} to compare their rain histories.`
              : "Loading golf courses…"}
          </p>
        </div>
        <button
          type="button"
          className="theme-switch"
          role="switch"
          aria-label="Dark mode"
          aria-checked={activeTheme === "dark"}
          onClick={toggleTheme}
        >
          <span className="theme-switch-thumb" aria-hidden="true" />
        </button>
      </header>

      {error && <div className="error">{error}</div>}

      <CourseMap
        places={places}
        selectedKeys={selectedKeys}
        onTogglePlace={togglePlace}
        isDark={activeTheme === "dark"}
      />

      <details className="course-picker">
        <summary>
          Choose by name · {selectedKeys.length} of {MAX_PLACES} selected
        </summary>
        <div className="place-select">
          {places.map((place) => (
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
      </details>

      {selectedKeys.length > 0 && (
        <div className="controls" aria-label="Analysis settings">
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
      )}

      {report && <Leaderboard report={report} />}
      {report && <Charts report={report} />}
    </main>
  );
}
