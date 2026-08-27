# findata frontend (TypeScript + esbuild)

TypeScript source + build config for the FinData viewer frontend. The built
bundle is served by the Flask app at `/findata`.

## Layout

```
frontend/
├── src/findata.ts      # viewer entry — bundles to static/findata.bundle.js
├── src/entity.ts       # entity-page entry — bundles to static/entity.bundle.js (ui_redesign S6)
├── src/core/           # dom.ts / router.ts / markdown.ts / api.ts / toast.ts
├── src/views/          # graph.ts / stats.ts / companies.ts / sectors.ts / docs.ts
├── types/api.ts        # response shapes for the /api/* endpoints (the type contract)
├── types/vendors.d.ts  # ambient declarations for CDN libs (cytoscape, marked, Prism, hljs)
├── package.json        # devDeps + build/typecheck scripts
├── tsconfig.json       # strict type-check config (noEmit — esbuild emits)
└── package-lock.json   # committed for reproducible `npm ci`
```

## Build

```bash
make frontend         # cd frontend && npm ci && npm run build
                      # → emits ../static/{findata,entity}.bundle.js (+ .map)
make frontend-check   # cd frontend && npx tsc --noEmit   (strict, fast)
```

Both targets need Node installed. **`make qa` does NOT need Node** — the QA
gate stays Python-only. Both built bundles are committed to git, so the deploy (`nixpacks.toml` —
no Dockerfile) stays 100% Python and a contributor without Node can still
run the app and pass `make qa`.

## Workflow when changing the frontend

1. Edit `src/findata.ts` (and `types/api.ts` if an endpoint shape changed).
2. `make frontend-check` — fix any `tsc` errors (this is what catches API
   shape-drift; a wrong field access against an `api.ts` interface fails here).
3. `make frontend` — rebuild the bundle.
4. Commit `src/findata.ts`, `types/`, **and** `../static/findata.bundle.js`
   (plus `.map`).

## Runtime contract (do not break)

The bundle is an **IIFE** (not an ES module) and runs immediately at the end
of `<body>` (the script tag has no `defer`/`type=module`). The instance is
re-attached to `window.viewer` at the bottom of `findata.ts`:

```ts
const viewer = new FinDataViewer();
window.viewer = viewer;
```

This is **load-bearing**: inline `onclick="viewer.goToPage(2)"` /
`viewer.openLightbox(...)` / `viewer.copyCode(...)` handlers in the
dynamically-built HTML resolve `viewer` as a bare global at runtime. If the
window re-attach is removed or the output format changes to a module, those
handlers silently break. `tsc` does NOT catch this — it's a runtime/DOM
contract, so keep the IIFE format + the `window.viewer` line.

## What this does NOT cover

Both UI surfaces ship as committed esbuild IIFE bundles built from the TS
sources above — the viewer since the TS port, the entity page since the
ui_redesign arc. There is no hand-written static JS under `static/`; this
README's earlier "localized pilot" framing predates that consolidation.
