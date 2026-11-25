/** Time wording for people (feedback: "make the minutes more explanatory"). Pure functions: unit-tested. */

export const MIN = 60_000;
export const HOUR = 60 * MIN;
export const DAY = 24 * HOUR;

/** "less than a minute", "12 min", "1 h 5 min", "3 days". */
export function duration(ms: number): string {
  const mins = Math.floor(Math.max(0, ms) / MIN);
  if (mins < 1) return "less than a minute";
  if (mins < 60) return `${mins} min`;
  const h = Math.floor(mins / 60);
  if (h < 24) return `${h} h ${mins % 60} min`;
  const d = Math.floor(h / 24);
  return d === 1 ? "1 day" : `${d} days`;
}

/** "1 chat", "3 chats": the word only, so callers can style the number separately. */
export const plural = (n: number, one: string, many = `${one}s`): string => (n === 1 ? one : many);

export const since = (iso: string, now = Date.now()): number => now - new Date(iso).getTime();
export const ago = (iso: string, now = Date.now()): string => `${duration(since(iso, now))} ago`;

/** "today 10:42", "yesterday 23:12", "3 Nov 09:15", in the viewer's time zone. */
export function when(iso: string, now = new Date()): string {
  const d = new Date(iso);
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return `today ${time}`;
  const y = new Date(now);
  y.setDate(y.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return `yesterday ${time}`;
  return `${d.toLocaleDateString([], { day: "numeric", month: "short" })} ${time}`;
}

/** Waiting "heat": green < 5 min, amber 5–15 min, red > 15 min (15 min = the "missed" limit in PLAN.md). */
export type Heat = "g" | "a" | "r";
export const heat = (ms: number): Heat => (ms < 5 * MIN ? "g" : ms < 15 * MIN ? "a" : "r");

export type Preset = "all" | "today" | "yesterday" | "7d" | "30d" | "custom";

/** A preset as the API's `from` / `to` (RFC 3339, `to` exclusive). Custom dates are local days. */
export function presetRange(
  preset: Preset,
  customFrom = "",
  customTo = "",
  now = new Date(),
): { from?: string; to?: string } {
  const startOfToday = new Date(now);
  startOfToday.setHours(0, 0, 0, 0);
  const iso = (d: Date) => d.toISOString();
  switch (preset) {
    case "today":
      return { from: iso(startOfToday) };
    case "yesterday":
      return { from: iso(new Date(startOfToday.getTime() - DAY)), to: iso(startOfToday) };
    case "7d":
      return { from: iso(new Date(now.getTime() - 7 * DAY)) };
    case "30d":
      return { from: iso(new Date(now.getTime() - 30 * DAY)) };
    case "custom": {
      const range: { from?: string; to?: string } = {};
      if (customFrom) range.from = iso(new Date(`${customFrom}T00:00:00`));
      if (customTo) {
        const end = new Date(`${customTo}T00:00:00`);
        end.setDate(end.getDate() + 1); // include the whole "to" day
        range.to = iso(end);
      }
      return range;
    }
    default:
      return {};
  }
}

export const percent = (rate: number | null | undefined): string =>
  rate === null || rate === undefined ? "–" : `${Math.round(rate * 100)}%`;
