/**
 * A tiny leveled logger for the browser app.
 *
 * `NEXT_PUBLIC_LOG_LEVEL` sets the threshold (`debug`, `info`, `warn`,
 * `error`, or `silent`). Development defaults to `debug`; production
 * defaults to `warn`, so request tracing stays out of users' consoles while
 * failures still surface. The factory is exported so tests can supply both a
 * level and a sink without touching globals.
 */

export type LogLevel = "debug" | "info" | "warn" | "error" | "silent";

type LogMethod = Exclude<LogLevel, "silent">;
type LogSink = Pick<Console, LogMethod>;

export interface Logger {
  debug: (...args: unknown[]) => void;
  info: (...args: unknown[]) => void;
  warn: (...args: unknown[]) => void;
  error: (...args: unknown[]) => void;
}

const LEVEL_PRIORITY: Record<LogLevel, number> = {
  debug: 10,
  info: 20,
  warn: 30,
  error: 40,
  silent: 100,
};

function isLogLevel(value: string): value is LogLevel {
  return (
    value === "debug" ||
    value === "info" ||
    value === "warn" ||
    value === "error" ||
    value === "silent"
  );
}

export function resolveLogLevel(
  env: { NEXT_PUBLIC_LOG_LEVEL?: string; NODE_ENV?: string } = process.env,
): LogLevel {
  const configured = env.NEXT_PUBLIC_LOG_LEVEL?.trim().toLowerCase();
  if (configured && isLogLevel(configured)) return configured;
  return env.NODE_ENV === "development" ? "debug" : "warn";
}

export function createLogger(
  level: LogLevel = resolveLogLevel(),
  sink: LogSink = console,
): Logger {
  const threshold = LEVEL_PRIORITY[level];
  const emit = (method: LogMethod, args: unknown[]): void => {
    if (LEVEL_PRIORITY[method] < threshold) return;
    sink[method]("[tstorm-climo]", ...args);
  };
  return {
    debug: (...args) => emit("debug", args),
    info: (...args) => emit("info", args),
    warn: (...args) => emit("warn", args),
    error: (...args) => emit("error", args),
  };
}

export const logger = createLogger();
