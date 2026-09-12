/**
 * Ambient declarations for the third-party libraries the frontend uses.
 *
 * 1. Browser-global `<script>` tags in `templates/findata.html` (marked,
 *    DOMPurify) — declared with `declare const` below. Syntax highlighting
 *    is bundled (sugar-high, S1 of doc/improvements/archive/ui/sugar_high_highlighter.md)
 *    and needs no
 *    global declaration.
 * 2. npm packages bundled by esbuild — sigma/graphology/FA2 (the graph
 *    renderer, S1 of graph_rendering_overhaul.md) and sugar-high ship their
 *    own TypeScript declarations, so they need nothing here. The old
 *    cytoscape minimal stubs were removed with the renderer swap.
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
// DOMPurify (vendored global: dompurify@3)                                    //
// --------------------------------------------------------------------------- //
declare const DOMPurify: {
    sanitize(dirty: string, config?: Record<string, unknown>): string;
};
