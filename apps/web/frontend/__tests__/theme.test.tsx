import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getCoverage: vi.fn(),
  getPlaces: vi.fn(),
  postReport: vi.fn(),
}));

vi.mock("@/lib/api", () => api);
vi.mock("@/components/CourseMap", () => ({ default: () => null }));

import Dashboard from "@/components/Dashboard";

afterEach(() => {
  cleanup();
  localStorage.removeItem("course-climatology-theme");
  delete document.documentElement.dataset.theme;
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("theme switch", () => {
  it("follows the system preference until the user chooses a theme", async () => {
    api.getCoverage.mockResolvedValue({
      observed_start: null,
      observed_end: null,
      place_count: 0,
      kinds: [],
      variable: null,
      units: null,
      gridded: false,
    });
    api.getPlaces.mockResolvedValue({ places: [] });

    let prefersDark = true;
    let onSystemChange: ((event: MediaQueryListEvent) => void) | undefined;
    const addEventListener = vi.fn(
      (_type: string, listener: (event: MediaQueryListEvent) => void) => {
        onSystemChange = listener;
      },
    );
    vi.stubGlobal(
      "matchMedia",
      vi.fn((media: string) => ({
        media,
        get matches() {
          return prefersDark;
        },
        addEventListener,
        removeEventListener: vi.fn(),
      })),
    );

    render(<Dashboard />);

    const toggle = screen.getByRole("switch", { name: "Dark mode" });
    await waitFor(() => {
      expect(document.documentElement.dataset.theme).toBe("dark");
      expect(toggle).toHaveAttribute("aria-checked", "true");
    });

    prefersDark = false;
    act(() => onSystemChange?.({ matches: false } as MediaQueryListEvent));
    await waitFor(() => {
      expect(document.documentElement.dataset.theme).toBe("light");
      expect(toggle).toHaveAttribute("aria-checked", "false");
    });

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(localStorage.getItem("course-climatology-theme")).toBe("dark");

    prefersDark = false;
    act(() => onSystemChange?.({ matches: false } as MediaQueryListEvent));
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});
