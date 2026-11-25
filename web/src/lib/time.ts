// Everything on screen is IST, whatever the browser's time zone.
export const IST_OFFSET_SEC = 5.5 * 3600;
const TZ = "Asia/Kolkata";

const clockFmt = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const hmFmt = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hour12: false });
const dayFmt = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, day: "2-digit", month: "short" });
const longDayFmt = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, weekday: "short", day: "2-digit", month: "short" });

export const clock = (ms: number): string => clockFmt.format(ms);
export const hhmm = (iso: string): string => hmFmt.format(Date.parse(iso));
export const dayMonth = (iso: string): string => dayFmt.format(Date.parse(iso));
export const weekdayDayMonth = (iso: string): string => longDayFmt.format(Date.parse(iso));
export const dayTime = (iso: string): string => `${dayMonth(iso)} ${hhmm(iso)}`;

/** Calendar date in IST, YYYY-MM-DD. */
export function istDate(iso: string): string {
  return new Date(Date.parse(iso) + IST_OFFSET_SEC * 1000).toISOString().slice(0, 10);
}

/** lightweight-charts draws UTC, so shift timestamps to make the axis read IST. */
export function chartTime(iso: string): number {
  return Math.floor(Date.parse(iso) / 1000) + IST_OFFSET_SEC;
}

/** "2d 3h", "3h 05m", "04:12" */
export function duration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const d = Math.floor(total / 86400);
  const h = Math.floor((total % 86400) / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export const STALE_AFTER_MS = 30_000;

/**
 * The engine is offline if its last heartbeat is more than 30 s old. `receivedAt` (local clock)
 * protects against a phone whose clock disagrees with the engine host's.
 */
export function isStale(heartbeatTs: string | undefined, receivedAt: number | null, now: number): boolean {
  if (!heartbeatTs) return true;
  const byTimestamp = now - Date.parse(heartbeatTs);
  const bySeen = receivedAt === null ? Number.POSITIVE_INFINITY : now - receivedAt;
  return Math.min(byTimestamp, bySeen) > STALE_AFTER_MS;
}

/** How recently the server must have answered before an old heartbeat means anything. */
export const ANSWER_FRESH_MS = 15_000;
export type EngineStatus = "ok" | "offline" | "checking";

/**
 * "offline" is only claimed on current information: either the server was just asked, or it cannot be reached at
 * all. A tab coming back from the background is "checking" until its first answer, not "offline".
 */
export function engineStatus(
  heartbeatTs: string | undefined,
  seenAt: number | null,
  heardAt: number | null,
  reachable: boolean,
  now: number,
): EngineStatus {
  if (!isStale(heartbeatTs, seenAt, now)) return "ok";
  const justAsked = heardAt !== null && now - heardAt < ANSWER_FRESH_MS;
  return justAsked || !reachable ? "offline" : "checking";
}
