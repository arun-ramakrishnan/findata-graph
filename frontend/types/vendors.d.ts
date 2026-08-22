/**
 * Ambient declarations for the third-party libraries the frontend uses.
 *
 * Two loading strategies coexist:
 *
 * 1. Browser-global `<script>` tags in `templates/findata.html` (marked,
 *    Prism, highlight.js, DOMPurify) — declared with `declare const` /
 *    `interface Window` merges below.
 * 2. npm packages bundled by esbuild since S2 (cytoscape, cytoscape-fcose)
 *    — declared with `declare module` so the imports type-check against our
 *    intentionally MINIMAL shapes instead of pulling heavy type packages
 *    into the build (neither package ships its own .d.ts).
 *
 * Only the methods the frontend invokes are declared. If the frontend starts
 * using more of a library, expand the relevant declaration here rather than
 * `any`-casting at the call site.
 */

// --------------------------------------------------------------------------- //
// marked — markdown → HTML (vendored global: marked@12)                       //
// --------------------------------------------------------------------------- //
declare const marked: {
    parse(markdown: string): string;
};

// --------------------------------------------------------------------------- //
// highlight.js (vendored global: highlight.js@11)                             //
// --------------------------------------------------------------------------- //
interface HljsResult {
    value: string;
}

// --------------------------------------------------------------------------- //
// Prism (vendored global: prism@1.29) — declared on Window in findata.ts      //
// --------------------------------------------------------------------------- //

// --------------------------------------------------------------------------- //
// DOMPurify (vendored global: dompurify@3)                                    //
// --------------------------------------------------------------------------- //
declare const DOMPurify: {
    sanitize(dirty: string, config?: Record<string, unknown>): string;
};

// --------------------------------------------------------------------------- //
// cytoscape (npm: cytoscape@3.28.1, bundled by esbuild)                       //
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

/** Event object cytoscape passes to handlers (S3: tooltips need the pointer). */
interface CyEvent {
    target: CySingular;
    /** Viewport-space pointer position (node/edge mouse events). */
    renderedPosition?: { x: number; y: number };
    originalEvent?: { clientX: number; clientY: number };
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
    data(key: string, value: unknown): this;
    removeData(key: string): this;
    edgesWith(other: CySingular): CyCollection;
    addClass(cls: string): this;
    removeClass(cls: string): this;
    toggleClass(cls: string, add?: boolean): this;
    hide(): this;
    show(): this;
}

interface CyElements {
    remove(): void;
    add(els: CyElementInput[] | CyElementInput): CyElements;
    length: number;
    not(other: CyCollection): CyElements;
    removeClass(cls: string): void;
    addClass(cls: string): void;
    hide(): CyElements;
    show(): CyElements;
    /** Sub-collections by kind (cloud legend filters, zoom-fade labels). */
    nodes(selector?: string): CyElements;
    edges(selector?: string): CyElements;
    /** Iterate the collection (cloud set-highlight needs per-element comp). */
    forEach(cb: (el: CySingular) => void): void;
}

/** Builder returned by `cytoscape.stylesheet()`; chained `.selector().style()`. */
interface CyStylesheet {
    selector(sel: string): CyStylesheet;
    style(s: Record<string, unknown>): CyStylesheet;
}

/** Live style object returned by `cy.style()` — patch then `.update()`. */
interface CyStyleUpdater extends CyStylesheet {
    update(): void;
}

/** A live cytoscape graph instance. */
interface CyInstance {
    on(evt: string, sel: string, cb: (e: CyEvent) => void): void;
    on(evt: string, cb: (e: CyEvent) => void): void;
    elements(): CyElements;
    nodes(selector?: string): CyElements;
    edges(selector?: string): CyElements;
    add(els: CyElementInput[] | CyElementInput): CyElements;
    layout(opts: Record<string, unknown>): { run(): void };
    resize(): void;
    batch(cb: () => void): void;
    /** Live stylesheet accessor (zoom-fade patches: selector().style().update()). */
    style(): CyStyleUpdater;
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

/** The constructor function + its statics (`stylesheet`, extension `use`). */
interface CyCore {
    (opts: Record<string, unknown>): CyInstance;
    stylesheet(): CyStylesheet;
    /** Register a layout/extension plugin (fcose). */
    use(ext: unknown): void;
}

declare module "cytoscape" {
    const cytoscape: CyCore;
    export default cytoscape;
}

// --------------------------------------------------------------------------- //
// cytoscape-fcose (npm: cytoscape-fcose@2.2.0, bundled by esbuild)            //
// --------------------------------------------------------------------------- //
// Registered via cytoscape.use() at graph-module load; the "fcose" layout
// name becomes available to cy.layout(). Consumed by S3+ slices.

declare module "cytoscape-fcose" {
    const fcose: unknown;
    export default fcose;
}
