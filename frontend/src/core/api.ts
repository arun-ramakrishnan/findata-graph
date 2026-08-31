// Typed fetch client for the /api/* surface.
//
// Every call site in the original single file did `fetch(url)` +
// `response.json()` with per-endpoint error dances. This wrapper centralizes
// the strict pattern the graph endpoints already used: on a non-OK status,
// throw an ApiError carrying the server's `error` message (or the HTTP
// statusText). Callers that need raw Response access (e.g. the FTS content
// search special-cases 503) use `fetchResponse` directly.

/** Error thrown by fetchJson/postJson on non-2xx responses. */
export class ApiError extends Error {
    readonly status: number;

    constructor(status: number, message: string) {
        super(message);
        this.status = status;
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

/** Fetch + parse JSON; throws ApiError on non-OK. */
export async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
    const response = await fetch(url);
    if (!response.ok) {
        const body = await response.json().catch((): unknown => ({}));
        throw new ApiError(
            response.status,
            extractErrorMessage(body, response.statusText || `HTTP ${response.status}`),
        );
    }
    return (await response.json()) as T;
}

/** POST (no body — the API surface has no JSON-body writes) + parse JSON. */
export async function postJson<T>(url: string): Promise<T> {
    return await fetchJson<T>(url, { method: "POST" });
}
