// Typed fetch client for the /api/* surface.
//
// Every call site in the original single file did `fetch(url)` +
// `response.json()` with per-endpoint error dances. This wrapper centralizes
// the strict pattern the graph endpoints already used: on a non-OK status,
// throw an ApiError carrying the server's `error` message (or the HTTP
// statusText). Callers that need raw Response access (e.g. the FTS content
// search special-cases 503) use `fetchResponse` directly.
//
// Runtime shape validation (ts_contract_hardening S3, valibot arm): every
// call site passes the generated guard for its declared response type —
// `fetchJson<SectorsResponse>(url, isSectorsResponse)` — so each bundle
// tree-shakes to exactly the guards its views consume and a drifted payload
// throws a ShapeError naming the endpoint and first offending path instead
// of rendering silently wrong output.

import type { Guard } from "../../types/guards";

/** Error thrown by fetchJson/postJson on non-2xx responses. */
export class ApiError extends Error {
    readonly status: number;

    constructor(status: number, message: string) {
        super(message);
        this.status = status;
    }
}

/** Error thrown when a success payload violates its declared api.ts shape. */
export class ShapeError extends Error {
    readonly endpoint: string;
    readonly detail: string;

    constructor(endpoint: string, detail: string) {
        super(`${endpoint}: ${detail}`);
        this.endpoint = endpoint;
        this.detail = detail;
    }
}

/**
 * Raw Response access for endpoints with bespoke status handling.
 * (Exported so callers don't touch global fetch directly.)
 */
export function fetchResponse(url: string, init?: RequestInit): Promise<Response> {
    return fetch(url, init);
}

function extractErrorMessage(body: unknown, fallback: string): string {
    if (body && typeof body === "object" && "error" in body) {
        const err = (body as { error?: unknown }).error;
        if (typeof err === "string" && err) return err;
    }
    return fallback;
}

/** Fetch + parse JSON; throws ApiError on non-OK, ShapeError on shape drift. */
export async function fetchJson<T>(url: string, guard?: Guard, init?: RequestInit): Promise<T> {
    const response = await fetch(url);
    if (!response.ok) {
        const body = await response.json().catch((): unknown => ({}));
        throw new ApiError(
            response.status,
            extractErrorMessage(body, response.statusText || `HTTP ${response.status}`),
        );
    }
    const data: unknown = await response.json();
    if (guard) {
        const violation = guard(data);
        if (violation) throw new ShapeError(url, violation);
    }
    return data as T;
}

/** POST (no body — the API surface has no JSON-body writes) + parse JSON. */
export async function postJson<T>(url: string, guard?: Guard): Promise<T> {
    return await fetchJson<T>(url, guard, { method: "POST" });
}

// Re-export the generated guards so views can pass their route guard at the
// call site; bundles tree-shake to exactly the guards their views consume.
export * from "../../types/guards";
