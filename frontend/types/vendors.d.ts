/**
 * Ambient declarations for the third-party libraries the frontend uses.
 *
 * 1. Browser-global `<script>` tags in `templates/findata.html` (marked,
 *    Prism, highlight.js, DOMPurify) — declared with `declare const` /
 *    `interface Window` merges below.
 * 2. npm packages bundled by esbuild — sigma/graphology/FA2 (the graph
 *    renderer, S1 of graph_rendering_overhaul.md) ship their own TypeScript
 *    declarations, so they need nothing here. The old cytoscape minimal
 *    stubs were removed with the renderer swap.
 *
 * Only the methods the frontend invokes are declared for the globals. If the
 * frontend starts using more of a library, expand the relevant declaration
 * here rather than `any`-casting at the call site.
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
