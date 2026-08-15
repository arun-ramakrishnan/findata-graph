/**
 * Ambient declarations for the third-party libraries loaded via CDN
 * `<script>` tags in `templates/findata.html` (marked, Prism, highlight.js,
 * cytoscape). These run as browser globals; this file teaches `tsc` about the
 * shapes the frontend actually calls, so call sites are navigable and
 * typo-checked without pulling the full (and heavy) type packages into the
 * build.
 *
 * This is an AMBIENT file (no top-level import/export) so every declaration
 * here is global and merges with the `declare global` block in findata.ts.
 *
 * These are intentionally MINIMAL: only the methods the frontend invokes are
 * declared. If the frontend starts using more of a library, expand the
 * relevant declaration here rather than `any`-casting at the call site.
 */

// --------------------------------------------------------------------------- //
// marked — markdown → HTML (CDN: marked@12)                                   //
// --------------------------------------------------------------------------- //
declare const marked: {
    parse(markdown: string): string;
};

// --------------------------------------------------------------------------- //
// highlight.js (CDN: highlight.js@11)                                         //
// --------------------------------------------------------------------------- //
interface HljsResult {
    value: string;
}

// --------------------------------------------------------------------------- //
// Prism (CDN: prism@1.29) — declared on Window in findata.ts's `declare global`
// --------------------------------------------------------------------------- //

// --------------------------------------------------------------------------- //
// cytoscape (CDN: cytoscape@3.28)                                             //
// --------------------------------------------------------------------------- //
// The frontend uses cytoscape fairly heavily (instance + stylesheet builder +
// layout/run/collection APIs). Declaring the full surface is out of scope for
// the type-check goal here, so we model the constructor + the methods called
// and lean on structural typing. `CyCore` is the static side (the function
// you call + `.stylesheet()`); `CyInstance` is the returned graph instance.

/** Minimal description of an element (node or edge) added to the graph. */
interface CyElementInput {
    data: {
        id: string;
        label?: string;
        group?: string;
        centrality?: number;
        source?: string;
        target?: string;
        type?: string;
        props?: Record<string, unknown>;
        [k: string]: unknown;
    };
}

/** Data payload cytoscape hands back to a `.data()` / event callback. */
interface CyNodeData {
    id: string;
    label?: string;
    group?: string;
    centrality?: number;
    [k: string]: unknown;
}

/** A cytoscape collection (subset of elements) returned by `.collection()`. */
interface CyCollection {
    merge(other: CyCollection | CyElementInput | unknown): CyCollection;
    addClass(cls: string): void;
    removeClass(cls: string): void;
}

/** A single node/edge the event handlers receive. */
interface CySingular {
    data(): CyNodeData;
    edgesWith(other: CySingular): CyCollection;
    addClass(cls: string): this;
    removeClass(cls: string): this;
}

interface CyElements {
    remove(): void;
    add(els: CyElementInput[] | CyElementInput): CyElements;
    length: number;
    not(other: CyCollection): CyElements;
    removeClass(cls: string): void;
    addClass(cls: string): void;
    /** Iterate the collection (cloud set-highlight needs per-element comp). */
    forEach(cb: (el: CySingular) => void): void;
}

/** Builder returned by `cytoscape.stylesheet()`; chained `.selector().style()`. */
interface CyStylesheet {
    selector(sel: string): CyStylesheet;
    style(s: Record<string, unknown>): CyStylesheet;
}

/** A live cytoscape graph instance. */
interface CyInstance {
    on(evt: string, sel: string, cb: (e: { target: CySingular }) => void): void;
    on(evt: string, cb: () => void): void;
    on(evt: string, cb: (e: { target: CySingular }) => void): void;
    elements(): CyElements;
    add(els: CyElementInput[] | CyElementInput): CyElements;
    layout(opts: Record<string, unknown>): { run(): void };
    resize(): void;
    // Zoom / panning (used by the graph zoom slider + fit button).
    zoom(level?: number): number;
    minZoom(): number;
    maxZoom(): number;
    fit(elements?: CyElements, padding?: number): CyInstance;
    // getElementById returns a chainable accessor whose own methods return
    // `this` so `.addClass(...).select()` type-checks; `length` lets callers
    // test existence (0 when absent).
    getElementById(id: string): CySingular & {
        length: number;
        select(): this;
        addClass(cls: string): this;
    };
    collection(): CyCollection;
}

/** The global constructor + its `stylesheet()` static helper. */
interface CyCore {
    (opts: Record<string, unknown>): CyInstance;
    stylesheet(): CyStylesheet;
}

declare const cytoscape: CyCore;
