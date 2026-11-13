import { describe, expect, it, vi } from "vitest";
import { createLogger, resolveLogLevel } from "@/lib/logger";

function fakeSink() {
  return {
    debug: vi.fn<Console["debug"]>(),
    info: vi.fn<Console["info"]>(),
    warn: vi.fn<Console["warn"]>(),
    error: vi.fn<Console["error"]>(),
  };
}

describe("logger", () => {
  it("suppresses messages below the configured level", () => {
    const sink = fakeSink();
    const logger = createLogger("warn", sink);

    logger.debug("hidden debug");
    logger.info("hidden info");
    logger.warn("shown warning", 1);
    logger.error("shown error");

    expect(sink.debug).not.toHaveBeenCalled();
    expect(sink.info).not.toHaveBeenCalled();
    expect(sink.warn).toHaveBeenCalledWith("[tstorm-climo]", "shown warning", 1);
    expect(sink.error).toHaveBeenCalledWith("[tstorm-climo]", "shown error");
  });

  it("silences every level when configured as silent", () => {
    const sink = fakeSink();
    const logger = createLogger("silent", sink);

    logger.debug("no");
    logger.info("no");
    logger.warn("no");
    logger.error("no");

    expect(sink.debug).not.toHaveBeenCalled();
    expect(sink.info).not.toHaveBeenCalled();
    expect(sink.warn).not.toHaveBeenCalled();
    expect(sink.error).not.toHaveBeenCalled();
  });

  it("reads the configured level and falls back by environment", () => {
    expect(
      resolveLogLevel({ NEXT_PUBLIC_LOG_LEVEL: "debug", NODE_ENV: "production" }),
    ).toBe("debug");
    expect(
      resolveLogLevel({ NEXT_PUBLIC_LOG_LEVEL: " SILENT ", NODE_ENV: "development" }),
    ).toBe("silent");
    expect(
      resolveLogLevel({ NEXT_PUBLIC_LOG_LEVEL: "verbose", NODE_ENV: "production" }),
    ).toBe("warn");
    expect(resolveLogLevel({ NODE_ENV: "development" })).toBe("debug");
    expect(resolveLogLevel({ NODE_ENV: "production" })).toBe("warn");
  });
});
