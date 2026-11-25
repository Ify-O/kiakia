import { describe, expect, it } from "vitest";
import { ApiProblem, ensureOk, isOutage, unwrap } from "./client";

const res = (status: number) => new Response(null, { status });

describe("unwrap / ensureOk: API results become data or a Problem", () => {
  it("returns the data", () => {
    expect(unwrap({ data: { ok: 1 }, response: res(200) })).toEqual({ ok: 1 });
  });

  it("turns an error body into an ApiProblem with its detail and field errors", () => {
    const problem = { title: "Bad", detail: "phone is wrong", errors: [{ field: "phone", message: "nope" }] };
    try {
      unwrap({ error: problem, response: res(422) });
      expect.unreachable();
    } catch (err) {
      expect(err).toBeInstanceOf(ApiProblem);
      const p = err as ApiProblem;
      expect([p.status, p.message, p.fieldError("phone")]).toEqual([422, "phone is wrong", "nope"]);
    }
  });

  it("accepts 204 No Content without treating it as a failure (forget customer)", () => {
    expect(() => ensureOk({ response: res(204) })).not.toThrow();
    expect(() => ensureOk({ error: { title: "Not found" }, response: res(404) })).toThrow(ApiProblem);
  });

  it("knows an outage from a normal error", () => {
    expect(isOutage(new TypeError("fetch failed"))).toBe(true);
    expect(isOutage(new ApiProblem(503, {}))).toBe(true);
    expect(isOutage(new ApiProblem(404, {}))).toBe(false);
  });
});
