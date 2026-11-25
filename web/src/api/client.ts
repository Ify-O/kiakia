/**
 * The ONLY way the UI talks to Kiakia: a client typed from openapi.yaml.
 *
 * `schema.d.ts` is generated from the contract (`npm run gen:api`). openapi-fetch checks every
 * path, parameter and body against it at compile time, so the UI cannot use anything the
 * contract doesn't define (docs/adr/0001, 0005).
 */
import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

export const api = createClient<paths>({ baseUrl: "" });

type S = components["schemas"];
export type Channel = S["Channel"];
export type Category = S["Category"];
export type ConversationStatus = S["ConversationStatus"];
export type Conversation = S["ConversationSummary"];
export type ConversationDetail = S["ConversationDetail"];
export type Message = S["Message"];
export type FeedItem = S["FeedItem"];
export type InboxStats = S["InboxStats"];
export type Problem = S["Problem"];
export type ConsentStatus = S["ConsentStatus"];
export type Customer = S["Customer"];
export type CustomerDetail = S["CustomerDetail"];
export type ConsentRecord = S["ConsentRecord"];

/** An error response from the API, in RFC 9457 Problem Details form (docs/adr/0002). */
export class ApiProblem extends Error {
  constructor(
    public readonly status: number,
    public readonly problem: Partial<Problem>,
  ) {
    super(problem.detail ?? problem.title ?? `HTTP ${status}`);
  }

  /** The last part of `type`, e.g. "delivery-unknown". */
  get kind(): string {
    return (this.problem.type ?? "").split("/").pop() ?? "";
  }

  fieldError(field: string): string | undefined {
    return this.problem.errors?.find((e) => e.field === field)?.message;
  }
}

/** Turns an openapi-fetch result into data, or throws ApiProblem (network errors throw TypeError). */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.data !== undefined) return result.data;
  throw new ApiProblem(result.response.status, (result.error ?? {}) as Partial<Problem>);
}

/** For responses with no body (e.g. 204 No Content): succeeds on any 2xx, else throws ApiProblem. */
export function ensureOk(result: { error?: unknown; response: Response }): void {
  if (result.response.ok) return;
  throw new ApiProblem(result.response.status, (result.error ?? {}) as Partial<Problem>);
}

/** True when the API itself can't be reached or is down (show the "can't reach Kiakia" banner). */
export function isOutage(err: unknown): boolean {
  return err instanceof TypeError || (err instanceof ApiProblem && (err.status === 503 || err.status === 500));
}
