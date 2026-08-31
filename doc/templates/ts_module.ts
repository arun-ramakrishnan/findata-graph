/**
 * <One-line purpose — the script_search TS footprint indexes THIS block.>
 *
 * <2-6 lines: what this module owns in the viewer SPA / entity pages,
 * who routes to it (core/router.ts, an entity page), and what it must
 * never do (no fetch outside core/api.ts, no DOM writes outside the
 * mounted view).>
 */

// contract: frontend/tsconfig.json — strict `tsc --noEmit` gates this file
//           (make frontend-check, advisory)
// contract: frontend/package.json — esbuild bundles src/*.ts into
//           ../static/*.bundle.js, and that output is COMMITTED so the
//           Python deploy stays Node-free. Never hand-edit a bundle;
//           rebuild via `make frontend`.
//
// House rules (frontend/):
//  - types/api.ts is the response-shape contract: the Python-side
//    integration test requires every declared field — including `?:`
//    optional ones — to be PRESENT in responses. Emit null, never omit.
//  - New runtime dependencies need a real reason (bundle size is committed
//    debt); vendored libs under static/vendor/ serve the Python pages and
//    are typed via types/vendors.d.ts.

/** <One-line doc for the export — extraction pairs it with the symbol.> */
export function toPermalink(stem: string): string {
  return stem.toLowerCase().replace(/[^a-z0-9]+/g, "_");
}
