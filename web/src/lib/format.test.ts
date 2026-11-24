import { describe, expect, it } from "vitest";
import { DAY, HOUR, MIN, duration, heat, percent, presetRange } from "./format";

describe("duration: clear wording", () => {
  it.each([
    [20_000, "less than a minute"],
    [12 * MIN, "12 min"],
    [HOUR + 5 * MIN, "1 h 5 min"],
    [DAY + HOUR, "1 day"],
    [3 * DAY, "3 days"],
  ])("%i ms -> %s", (ms, text) => expect(duration(ms)).toBe(text));
});

describe("heat: the waiting colours", () => {
  it("is green under 5 min, amber up to 15 min, red after", () => {
    expect(heat(4 * MIN)).toBe("g");
    expect(heat(10 * MIN)).toBe("a");
    expect(heat(16 * MIN)).toBe("r");
  });
});

describe("presetRange: filters become the API's from/to", () => {
  const now = new Date("2026-11-25T15:30:00");
  it("all time sends nothing", () => expect(presetRange("all", "", "", now)).toEqual({}));
  it("today starts at local midnight", () =>
    expect(presetRange("today", "", "", now).from).toBe(new Date("2026-11-25T00:00:00").toISOString()));
  it("yesterday is a closed day", () => {
    const r = presetRange("yesterday", "", "", now);
    expect(r.from).toBe(new Date("2026-11-24T00:00:00").toISOString());
    expect(r.to).toBe(new Date("2026-11-25T00:00:00").toISOString());
  });
  it("custom 'to' includes the whole day", () =>
    expect(presetRange("custom", "2026-11-01", "2026-11-03", now).to).toBe(new Date("2026-11-04T00:00:00").toISOString()));
});

it("percent shows a dash when there is no data", () => {
  expect(percent(null)).toBe("–");
  expect(percent(0.667)).toBe("67%");
});
