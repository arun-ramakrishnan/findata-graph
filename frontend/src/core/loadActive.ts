// Shared fetch → isActive? → display → catch scaffolding (consolidation).
//
// Canonical home for the load pattern previously re-rolled in the
// companies/sectors/stats views: fetch, run unguarded side work, display
// only when the view is still visible, log view-labelled errors.

export interface LoadActiveOptions<T> {
    /** The fetch (may build guarded request params inside). */
    fetch: () => Promise<T>;
    /** Guarded render — runs only when the view is still visible. */
    display: (data: T) => void;
    /** Whether this view is the visible one (deferred-render check). */
    isActive: () => boolean;
    /** View-labelled error handling (log, fallback render, toast). */
    onError: (error: unknown) => void;
    /** Unguarded post-fetch side work (e.g. cross-view filter population). */
    onFetched?: (data: T) => void;
}

export async function loadActive<T>(opts: LoadActiveOptions<T>): Promise<void> {
    try {
        const data = await opts.fetch();
        opts.onFetched?.(data);
        if (opts.isActive()) opts.display(data);
    } catch (error) {
        opts.onError(error);
    }
}
