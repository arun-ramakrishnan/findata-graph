---
title: "Graph rendering overhaul — measured head-to-head: cytoscape LOD vs sigma.js vs precomputed layout"
status: executed
filed: "2026-09-11"
executed: "2026-09-11"
completed_md: "225"
area: "frontend"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Graph rendering overhaul — measured head-to-head: cytoscape LOD vs sigma.js vs precomputed layout

**Date:** 2026-09-11 · **Status:** EXECUTED (completed.md #225) ·
**Area:** `frontend/src/views/graph.ts` (2,545 LOC, cytoscape + fcose),
`/api/graph/cloud|neighbors|semantic|shortest` consumers;
companion to `prefab_ui_flask_views.md` (sibling) (which excludes the
canvas by design)

## 1. Motivation

The graph canvas is the weakest surface of the UI (operator assessment
2026-09-11) and the only one that needed repeated remediation to stay
functional: #108 built the whole-graph cloud, #110 (same day) made it
usable at all (jitter, 4,110 canvas-text labels, bezier cost), the
polish pass tuned layouts, the redesign (#145+) reworked cytoscape on
fcose with five modes. Every fix stayed inside cytoscape's canvas
model, and the graph kept growing underneath it. The head-to-head this
proposal runs was explicitly deferred once before (graph_docs_ui_polish
§3 rejected sigma.js/react-force-graph/three.js to preserve the
tap/select/expand interaction model during a *styling* pass) — the TS
app is modular now, scale has 4.7x'd on edges, and the question deserves
numbers instead of a scoped rejection.

## 2. Evidence (measured 2026-09-11, this box)

| Fact | Value | Source |
|---|---|---|
| Current whole-graph scale | **1,649 entities / 19,261 edges** | sqlite read-only, `memory/research.db` |
| Scale at cloud birth (#108, 2026-08-15) | 1,209 / 4,110 | completed.md #108 |
| Delta | +36% nodes, **+369% edges** | computed |
| #110 same-day fix scope | animated `cose` → concentric-by-degree default; non-animated bounded fcose; cloud-mode stylesheet selectors | completed.md #110 |
| Prior library-swap rejection | sigma.js/react-force-graph/three.js rejected to keep cytoscape interaction model + fcose + tokens.css — during a polish arc | archive/tooling/graph_docs_ui_polish.md §3 |
| Backend lane | already consolidated: Onager round-trip + result caches (#222) | completed.md #222 |

What the numbers mean: layout and paint cost grow with edges, and edges
grew ~4.7x while the rendering strategy stayed "compute layout
client-side, draw every element". At 19,261 edges the canvas is doing
per-element work a WebGL renderer or a precomputed-position paint does
not. Status quo is re-benchmarked (not assumed) in S0 before anything
is replaced.

## 3. Design

**Mechanism:** a measurement harness first, a renderer decision by
numbers + interaction parity, then one integration slice. The four
`/api/graph/*` node-link endpoints and their payloads are untouched.

- **S0 — head-to-head harness (scratch page, no repo change):** load
  the live `/api/graph/cloud` payload into a baseline plus three
  candidate lanes and measure first-interactive-paint, sustained
  pan/zoom FPS (60 s scripted interaction), tab memory, and label
  legibility at 3 zoom levels, plus payload bytes for the same
  cloud data as JSON vs Arrow IPC (sizes the future
  Arrow-transport lane):
  baseline **status quo** — cytoscape as shipped, re-benchmarked at
  current scale (unmeasured claims are what let the library question
  stay closed since the polish arc);
  **1 — sigma.js (WebGL) + graphology** — GPU paint via node/edge
  programs; sigma's native label density grid +
  `labelRenderedSizeThreshold`; hover/click selection; the interaction
  parity list (tap-select, expand, mode switching, As-Of scrubber
  reload) is re-implemented minimally to price its true cost;
  **2 — cytoscape LOD (in-place detailing)** — degree-gated labels,
  zoom-dependent visibility, hub-only rendering below a zoom floor,
  simplified edge styles at cloud zoom — the #110 playbook extended;
  **3 — precomputed layout** — positions computed once server-side at
  snapshot time (edge-set hash gate, the embed-matrix refresh
  pattern), client paints without running fcose at all.
  The lanes compose, and the combinations are first-class candidates:
  **3+2** (cached positions + in-cytoscape detailing) is the
  minimal-diff stack — no library swap, tap/select/expand intact, and
  stable cross-visit positions neither concentric nor fcose ever gave;
  **3 also feeds 1** (sigma.js needs an initial position set anyway),
  so 1+3+2 composes into one stack if 1 wins. Each layer is measured
  separately to price it, then the winner composes.
  Decision gate: winner must beat status quo on the §4 table AND pass
  the parity checklist; ties break toward the smaller diff (2, 3+2)
  or the cheaper runtime (3).
- **S1 — integrate the winner** behind the existing graph.ts view
  interface: five modes, temporal As-Of scrubber, tokens.css color
  system mapped (or preserved), expand/select interactions kept.
- **S2 — retire the loser path** and freeze: single renderer, docs
  updated. Optional: mount the canvas behind Prefab `Embed` once
  `prefab_ui_flask_views.md` (sibling) lands — `Embed(url=...)` against a slim
  same-origin graph page (cloud payload stays out of srcdoc), controls
  as Prefab inputs driving iframe reload; the canvas becomes a pure
  viewport. Embed allows `<script>`/WebGL in its sandboxed iframe
  (doc-verified), but has no postMessage bridge — cross-frame state is
  URL params + reload, not live calls. Future investigation (recorded,
  not this arc): `Embed`'s HTML mode also supports WebGL, so a
  sigma.js canvas can live under the Prefab shell the same way — the
  renderer decision here and the Prefab hosting lane stay composable.

**Alternatives considered:**

- **graphrs (Rust/WASM igraph-for-JS, evaluated 2026-09-11 post-S0)** —
  algorithms-only (PageRank/betweenness/Louvain, FR/KK/Sugiyama layouts),
  no renderer; operator-suggested as "replace cytoscape's algo parts
  instead of the renderer." Rejected on S0 evidence: the measured FPS
  bottleneck is canvas *paint* (renderer question), the one expensive
  client algorithm (fcose 55 s/visit) is eliminated by lane 3's
  server-side precompute rather than sped up in-browser, and the metrics
  it offers are already server-side in Rust (Onager #222). Licensing
  (operator-reviewed, 2026-09-11): MIT wrapper + GPL-2.0+ WASM binary —
  no exposure for this private vault (GPL triggers on conveyance, not
  use); only a future distributed artifact would need the
  wrapper-independence position reviewed. Also pre-1.0 / no releases.
  Revisit if ego/filter views ever outgrow sub-second local layouts.

- **Keep tuning cytoscape only** — the #110 line of attack; S0 lane
  2 gives it its fair shot, but if it loses at 2x-projected scale the
  answer is structural, not stylistic.
- **Server-side image snapshots (static SVG/PNG)** — cheapest paint,
  loses all interaction; rejected as the primary lane, viable as a
  low-zoom overview texture inside lanes 2 and 3.
- **three.js / 3D force-graph** — 3D navigation for a 2D research
  corpus; rejected (also rejected in the polish arc).
- **Doing nothing** — scale grows with every parse run; the weak
  surface gets weaker.

## 4. Acceptance criteria & shakedown

1. S0 harness log (Appendix): all four lanes measured at current scale
   (1,649/19,261) and a 2x-synthetic edge set; each timing run
   repeated 3x, medians reported.
2. Winner integration: interaction parity checklist green (tap-select,
  expand, five modes, As-Of scrubber, layout dropdown, fit/zoom); no
  endpoint changes (`git diff app.py` empty for this arc).
3. `make frontend-check` (tsc) green after every TS slice; `make qa`
  with operator go at arc end.
4. Targets at 2x scale: first interactive paint < 2 s; sustained
  pan/zoom >= 50 FPS on this box; tab memory < 1.5 GB.

| Projected outcome | Measured @ S0 (status quo) | After (winner) |
|---|---|---|
| Cloud first-interactive-paint | 2.5 s @ 1x / 4.4 s @ 2x (concentric); fcose path ≈ 58 s @ 1x | target < 2 s @ 2x (1+3: precompute + WebGL) |
| Pan/zoom FPS @ 19k edges | 2.0 @ 1x / 1.1 @ 2x (software raster ceiling) | sigma 5.0 / 3.9 measured; target >= 50 on GPU |
| Client layout cost per load | concentric 0.85 s; fcose 55.4 s @ 1x (146.5 @ 2x) | zero (3) or bounded (2, 1) |
| JS heap @ 2x | 274 MB (baseline) / 457 MB (LOD) | sigma 13 MB |

## 5. Risks

- **sigma.js interaction rewrite cost** — parity list is the gate; if
  the minimal S0 re-implementation looks like a rewrite, that is data,
  and lanes 2 and 3 win by design.
- **WebGL context limits / headless CI** — benchmarks run on the real
  browser, not CI; CI keeps tsc-only checks.
- **Precomputed layout staleness** — positions regenerate on snapshot
  (edge-set hash gate, same pattern as the embed matrix refresh);
  live-filter views (ego, semantic) still compute locally — they are
  small (<= few hundred nodes).
- **Transition window with two renderers** — S2 collapses to one; no
  long-lived fork.

## 6. Non-goals

- No `/api/**` changes, no backend query work (#222 did that lane).
- No new graph analytics or metrics endpoints.
- No Prefab migration of the canvas — that split is
  `prefab_ui_flask_views.md` (sibling)'s Non-goal mirrored here.
- No Arrow IPC endpoint work in this arc — recorded as a future
  transport lane in `prefab_ui_flask_views.md` (sibling) (pyarrow is already
  a direct dep since #224); S0 measures JSON-vs-Arrow payload
  bytes only.
- No WebGL-in-Embed sigma hosting in this arc — recorded in S2;
  `Embed` HTML mode is WebGL-capable (doc-verified 2026-09-11).
- No corpus/data changes; scale numbers are inputs, not targets.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-11 | sqlite RO `select count(*) from entities` / `graph_edges` | 1,649 / 19,261 | memory/research.db |
| 2026-09-11 | doc_query "graph view rendering attempts" | #108, #110, polish §3, redesign, #222 | history table §2 |
| **S0** 2026-09-11 | `/api/graph/cloud` payload captured (live Flask) | 1,648 nodes / 19,261 edges, **1,772,172 B JSON** (gz 190,121 B) | cloud excludes 1 unconnected entity |
| **S0** 2026-09-11 | same cloud as Arrow IPC (pyarrow, nodes+edges tables) | **1,169,616 B** (gz 212,672 B) — **1.52x smaller raw**, but gz-JSON (190 KB) BEATS gz-Arrow (213 KB) | §3's Arrow-transport lane: raw-bytes win only; compressed transport wins stay with JSON for this payload shape |
| **S0** 2026-09-11 | 2x-synthetic edge set built (node+edge mirror, `~2` suffix) | 3,296 / 38,522 edges, 3,667,706 B JSON | acceptance §4.1 scale |
| **S0** 2026-09-11 | headless fcose precompute (lane 3 server-side cost; numIter 600, cloud opts from graph.ts) | **55.4 s** @ 1x, **146.5 s** @ 2x — positions written (64/133 KB) | the per-visit client layout cost lane 3 eliminates; snapshot-time cost, not request-time |
| **S0** 2026-09-11 | harness built (scratch, no repo change): 4 lanes × 2 scales × 3 runs; lanes = base (concentric as shipped) / LOD / preset-positions / sigma.js 2.4.0 WebGL + FA2(600); in-page 60 s scripted pan/zoom, rAF frame + long-frame counting, CDP heap | (results below) | sigma v2 pinned because v3 dropped UMD builds; benchmarks under headless chromium (chromium-1243, SwiftShader software raster, 1600×900) — absolute FPS conservative, relative head-to-head apples-to-apples; harness pages reusable on the desktop for GPU numbers |
| S0 2026-09-11 | **head-to-head medians (3 runs each; headless chromium-1243 1600×900, SwiftShader software raster)** — lane / scale / ready-ms / layout-ms / sustained FPS / long-frame% / JS heap: | see table below | absolute FPS is environment-conservative (software raster); ratios are the signal; harness pages (`/tmp/graph-s0/h/`) re-run on desktop GPU for absolute numbers |

**S0 medians:**

| lane | 1x ready | 1x layout | 1x FPS | 1x heap | 2x ready | 2x layout | 2x FPS | 2x heap |
|---|---|---|---|---|---|---|---|---|
| 0 baseline (concentric as shipped) | 2,501 ms | 848 ms | 2.0 | 120 MB | 4,359 ms | 1,486 ms | 1.1 | 274 MB |
| 2 cytoscape LOD | 2,641 ms | 907 ms | 1.8 | 379 MB | 5,692 ms | 1,992 ms | **0.8** | 457 MB |
| 3 precomputed positions | 2,619 ms | 1,224 ms | 2.2 | 132 MB | 4,696 ms | 2,163 ms | 1.1 | 253 MB |
| 1 sigma.js WebGL (+FA2 600) | 4,857 ms | 4,283 ms | **5.0** | **8 MB** | 10,817 ms | 10,054 ms | **3.9** | **13 MB** |

**S1 execution log — lane 3 landed (2026-09-11, operator-approved sequencing):
land 3 first (no-regret), Gate 1 checks after.**

- `helpers/graph/layout.py` (new): deterministic numpy ForceAtlas2 engine —
  chunked O(n²) repulsion collapsed to two BLAS matmuls via the separation
  linearity identity, multigraph edge weights, degree-sized nodes,
  swing/traction damping; float32 solve; 8,000-node refusal ceiling.
  Measured: **29.3 s** full compute @ 1x (1,648/19,261), **49 ms** gated
  replay, 167 s @ 2x-synthetic; quality eyeball-verified against the S0
  fcose precompute (community clusters, hub structure; harness
  `pre.html` screenshots).
- Sidecar `memory/graph_layout.json` (gitignored `memory/`), edge-set-hash
  gate (embed-matrix refresh pattern), atomic write, 0644. Python engine —
  not headless fcose — so the Node-free deploy story holds.
- `app.py`: `GET /api/graph/positions` (serves sidecar, self-heals stale
  by recomputing; inherits the /api/graph/* ETag policy) +
  post-rebuild pre-warm hook in `POST /api/graph/refresh` (non-fatal).
  First request after the arc: 200 in 17.4 s (compute), second: gated.
- Frontend: `graph.ts` fetches positions in parallel with the cloud
  (progressive enhancement — first paint never waits), "cached" preset
  layout branch (deterministic FNV scatter for post-snapshot nodes),
  cloud default becomes cached-when-available (explicit picks always
  win), "Cached layout (server)" dropdown option; bundle rebuilt;
  `tsc` green. E2E browser-verified on the live UI: cloud paints at
  server coordinates, `positions` 200/52 KB.
- Tests: `tests/test_graph_layout.py` (7: determinism, ceiling refusal,
  hash-gate reuse/recompute/corrupt-self-heal, order-insensitivity,
  endpoint shape + ETag + 304) + 98 regression green across the touched
  surfaces; ruff + ty clean.
- User-facing win already live: the cloud's default view renders at
  fcose-quality stable coordinates with **zero client layout compute**
  (was: concentric default, or 55 s fcose per explicit pick).

**Gate 1a — GPU re-run (2026-09-11, headed chromium on the desktop, ANGLE
Intel HD Graphics 530 / OpenGL ES 3.2 — physical GPU, not SwiftShader;
3 runs each, medians; sigma = v3.0.3 esbuild-IIFE bundle, the production
integration shape):**

| lane | scale | ready ms | layout ms | sustained FPS | long frames/60 s | JS heap |
|---|---|---|---|---|---|---|
| base cytoscape | 2x | 4,875 | 1,788 | 1.2 | 72 | 417 MB |
| sigma3 + FA2 | 2x | 10,494 | 10,094 | 59.4 | 36 | 17 MB |
| **sigma3 + lane-3 positions (1+3)** | 1x | 351 | 77 | 59.8 | 7 | 12 MB |
| **sigma3 + lane-3 positions (1+3)** | 2x | 579 | 147 | 59.9 | 7 | 18 MB |

§4 targets at 2x scale: first interactive paint < 2 s → **579 ms (3.5×
under)**; sustained pan/zoom ≥ 50 FPS → **59.9 (vsync-locked, 7 long
frames/min)**; tab memory < 1.5 GB → **18 MB (83× under)**. Baseline on
the same GPU stays 1.2 FPS — canvas2d per-edge redraw does not benefit
from GPU raster; the gap is structural, not environmental. **Gate 1a
PASSES.** Remaining Gate 1b: interaction-parity pricing prototype (§5
risk gate) — select/highlight/expand/mode-switch/As-Of in sigma v3.

**Gate 1b — interaction-parity pricing prototype (2026-09-11, sigma v3.0.3
vs the live Flask API, scratch harness `h/parity.html` ~140 LOC of
interaction JS):** select + neighbor-highlight via node/edgeReducer
(declarative, ~15 lines — the cytoscape `.classes()` pattern maps
directly); expand = neighbors-bundle fetch + incremental merge (graphology
addNode/addEdge re-render live, no manual refresh); cloud↔ego mode switch
(clear + rebuild); As-Of = refetch with `?as_of=` (mechanism verified;
payload stability on probed entities is a data property). Interaction FPS
during reducer-driven highlight churn: 60 Hz headless. **Verdict: NOT a
rewrite** — the §5 "if parity looks like a rewrite" escape hatch does not
trigger. Real S1 cost items surfaced by the prototype: (1) click vs
doubleClick gesture separation (two leading clicks deselect — graph.ts
already solves this for cytoscape, same discipline ports); (2) neighbors
bundle mixes scalar + list relations (subsidiary_of is a string — payload
normalization layer needed); (3) positions for dynamically added nodes
(prototype ring-scatters; S1 should reuse cached sidecar coords + small
local FA2 for unknowns); (4) async event handlers need explicit catch
plumbing (silent failures otherwise). **Gate 1b PASSES.** Both gates
green → S1 renderer swap (sigma v3 seeded by lane-3 positions, behind the
existing graph.ts lens interface) is the recommended next slice.

**S1 — renderer swap landed (2026-09-11, operator-approved: "ok use v3. Land 3
and we can do checks on Gate 1", S1 go in the same session):** sigma v3.0.3 +
graphology 0.25.4 + graphology-layout-forceatlas2 0.10.1 + @sigma/node-border
3.0.0 replace cytoscape 3.28.1 + cytoscape-fcose 2.2.0 (deps uninstalled;
`vendors.d.ts` cytoscape stubs deleted — sigma ships its own types). Bundle
519.8 KB (sigma in, cytoscape out), still committed so the deploy stays
Node-free.

- `frontend/src/views/graphRenderer.ts` (new, ~700 LOC): the renderer engine —
  graphology multigraph sync (`setElements`/`mergeElements`), node/edge
  reducers as the declarative counterpart of the old cytoscape stylesheet
  (visual state = fields — hover set, component, path, focal, label bucket,
  communities — reducers read them), all eight `#graph-layout` engines (FA2
  for fcose/cose; deterministic preset assignments for cached / components /
  concentric / circle / grid / breadthfirst; the component grid-packing
  ported verbatim), camera math in sigma's normalized space
  (`fitCapped` keeps the old zoom-multiplier units — 1.3 = 130%), spotlight
  animation, `EdgeArrowProgram` preserves arrowheads, `@sigma/node-border`
  with `drawDiscNodeLabel`/`drawDiscNodeHover` re-passed preserves label+hover
  discs on bordered (focal/set/path) nodes.
- `graph.ts` (2,632 → ~2,050 LOC): domain layer only now — payload builders,
  modes, filters, panels. The whole `_cytoscapeStyle` stylesheet (261 lines),
  `_cloudComponentPositions`, and every `cy.*` call are gone. Palette
  (`_EDGE_TOKENS` reading tokens.css) moved to the renderer — legend chips and
  canvas still share one source of truth.
- Gate 1b's four priced cost items, addressed: (1) gestures — sigma fires two
  `clickNode` before a dblclick; handlers are idempotent and the ego
  re-centre is debounced 350 ms (`lastNodeTapAt`); (2) payload
  normalization — stayed in the existing `_bundleElements` builders (they
  already normalize scalar+list), renderer consumes `GraphElement[]`
  unchanged; (3) dynamic-node positions — `mergeElements` seeds fresh nodes
  in a ring around the anchor and relaxes with a short local FA2 (80 iters),
  keeping existing positions; (4) async errors — callbacks promise-wrapped in
  the renderer, failures land in the status line via `onError`.
- Visual deltas, accepted: all nodes are discs (sector rectangles → colour +
  label), node font is global 10px (per-group sizes → sigma's density-grid
  gating), label-fade buckets are fit-relative camera ratios (the cytoscape
  model-px zooms are meaningless in normalized space; thresholds 2.5/1.4).
- E2E (headless chromium-1243 vs the live Flask, `/tmp/graph-s1/`): parity
  checklist green — cloud paints the FA2 sidecar at fit (93%), tap-select
  (detail panel + component ring-highlight), expand on an isolated jv_with
  subgraph (+10 nodes merged, 114→124), ego tap re-centre (Infosys → Coforge,
  search box + status follow), five modes, As-Of refetch, spotlight
  (74-entity set, camera animate), community shading (1,648 entities), path
  chain (CEAT→Automotive→MRF renders as the hop chain), zoom slider ↔ camera
  sync both directions; console clean (favicon 404s only). Testing gotcha
  recorded: in All mode the legend panels push `#graph-canvas` below the
  fold — screenshots/mouse coordinates must `scrollIntoView` first (the
  first E2E pass mis-read this as a blank canvas; the same settings render
  the full 19k-edge cloud in isolation).
- `window.__graphRenderer` exposed on the instance for E2E/diagnostics.
- tsc green; `tests/test_graph_layout.py` 7/7 (backend untouched by S1 — no
  `/api/**` changes: `git diff app.py` empty for this slice, §4.2 met).
- S2's retirement clause is effectively co-landed: cytoscape is fully removed
  (single renderer, no transition window). Remaining from S2: doc updates
  (this log) + the optional Prefab `Embed` hosting, which stays recorded-not-
  scheduled per §6.

**S0 readings (decision-gate inputs):**

- **Lane 1 (sigma) wins the head-to-head decisively**: 2.5× baseline FPS
  at 1x, 3.5× at 2x — under *software* raster, where WebGL's GPU
  advantage is at its smallest; and 15–30× lower heap (graph lives in
  WebGL buffers, not JS objects). Memory target (< 1.5 GB @ 2x) passes
  with 100× headroom. Its ready-time cost is the bundled FA2 layout —
  which lane 3 eliminates (below).
- **Lane 2 (cytoscape LOD) LOSES** — worse FPS than baseline at 1x and
  *below* baseline at 2x (0.8 vs 1.1), plus +250 MB heap for the
  class-bucket machinery. Per §3's own framing ("if it loses at
  2x-projected scale the answer is structural, not stylistic"): the
  per-edge canvas redraw is the bottleneck and styling around it does
  not pay. Lane 2 as a standalone lane is rejected by its own numbers.
- **Lane 3 (precomputed) is a wash on FPS** (canvas redraw still
  dominates) but removes the layout compute entirely: concentric was
  cheap (848 ms), but fcose — the quality layout — costs **55.4 s at 1x
  / 146.5 s at 2x** client-side today; precompute turns that into a
  snapshot-time job (edge-set hash gate) and gives stable cross-visit
  positions. Zero-interaction-risk, works for both renderers.
- **Composition 1+3 is the numbers-backed winner**: sigma seeded with
  precomputed fcose positions would drop lane 1's 4.3–10.1 s layout to
  ~0 and keep its FPS/memory wins. 3+2 (the minimal-diff stack) inherits
  lane 2's loss. The §4 FPS target (≥ 50 @ 2x) is untestable under
  SwiftShader — harness re-run on the desktop GPU is the remaining
  measurement before S1 commits to the rewrite; parity-cost pricing
  (tap-select/expand/five modes/As-Of in sigma) is the S1 gate per §5.
- **Caveat, sigma version**: v2.4.0 (UMD) pinned for the harness because
  v3 ships ESM-only chunks; production integration would bundle v3 via
  the repo's existing esbuild lane. v2 camera API differs (`setState`,
  no `reset()`).
