---
title: "Adopt Prefab for Python-composed Flask views (tables, charts, docs) — cytoscape graph stays TS"
status: executed
filed: "2026-09-11"
executed: "2026-09-12"
completed_md: "226"
area: "app.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Adopt Prefab for Python-composed Flask views (tables, charts, docs) — cytoscape graph stays TS

**Date:** 2026-09-11 · **Status:** EXECUTED (completed.md #226) ·
**Area:** `app.py` (page routes), `frontend/src/**` (parity surface),
`pyproject.toml` (one new dep); `/api/**` endpoints untouched

## 1. Motivation

The web UI is a 5,291-LOC TypeScript SPA (esbuild + tsc + cytoscape)
whose non-graph half is exactly the work Prefab (PrefectHQ, MIT,
`prefab-ui` 0.20.2) automates: typed fetch clients, hand-rolled DOM
rendering, a mini router, toast plumbing, and a 314-LOC markdown
renderer — for views that are DataTables, stat cards, and prose. Trigger:
operator evaluation 2026-09-11 (docs + J. Lowin AI Engineer briefing)
asked whether the existing Flask API can back Prefab views. It can —
Prefab is web-framework-agnostic ("works with anything that can serve
HTML and JSON — Flask, Django, Starlette" — API Server guide) and its
browser-side `Fetch` action consumes our ~29 existing `/api/*`
endpoints as-is. Zero API churn.

## 2. Evidence (measured 2026-09-11, this box)

| Fact | Measured | Source |
|---|---|---|
| Flask routes | 32 total: 4 page/image (`/`, `/entity/<p>`, `/findata`, images) + 28 `/api/*` JSON | `rg '@app.route' app.py` |
| Frontend LOC | 5,291 TS — graph.ts 2,545 (48%), docs 683, companies 433, entity 412, stats 251, sectors 111, core+entry ~1,360 | `wc -l frontend/src/**` |
| Frontend deps | cytoscape + fcose, esbuild, tsc, prettier (Node build; bundles committed so deploy is Node-free) | `frontend/package.json` |
| Prefab pkg | 0.20.2, `requires_python >=3.10` (repo pins >=3.14), deps `cyclopts>=4`, `pydantic>=2.11`, `rich>=13` (rich already in venv) | PyPI JSON API |
| Renderer | `PrefabApp.html()` = self-contained page (renderer+tree+state baked in); static export ~6 MB bundled or ~1 KB + CDN | docs (export.md) — **S0 verifies on 3.14** |
| Chart coverage | Bar/Line/Area/Pie/Radar/Radial/Scatter/Histogram/Sparkline/Metric — today's TS has zero chart libs | docs llms.txt |
| Graph coverage | **No network-graph component exists in Prefab** — cytoscape view cannot be expressed | docs llms.txt (full index) |

What the numbers mean: the absorbable surface is ~2.7k LOC of TS whose
every concern has a Prefab primitive (api.ts→`Fetch`, router→`Pages`/
`Tabs`, toast.ts→`ShowToast`, markdown.ts→`Markdown`, dom.ts→
components, table HTML→`DataTable` with sort/filter/pagination). The
flagship graph view (2.5k LOC, 15 `/api/graph/*` endpoints) has no
Prefab equivalent and stays TS — this is a hybrid adoption, not a
rewrite. Pre-1.0 velocity (breaking changes allowed per minor) is the
main lifecycle cost; pin exact versions.

## 3. Design

**Mechanism:** add Prefab page routes to `app.py` that build component
trees in Python and return `PrefabApp(...).html()`; browser `Fetch`
actions call existing `/api/*` endpoints same-origin (`connect_domains`
for CSP if ever cross-origin). Existing Jinja+bundle UI keeps serving
the graph view; migrated views move one by one behind parallel URLs
(`/v2/...`) until parity, then the old route flips. No endpoint, auth,
or API-shape change.

Slices (each independently landable):

- **S0 — spike (scratch venv, no repo change):** `uv venv` + install
  `prefab-ui==0.20.2` on this box's 3.14; verify import, `prefab
  serve --reload`, `PrefabApp.html()` byte size, and a one-page Fetch
  round-trip against the running Flask `/api/stats`. Kill criteria: 3.14
  incompatibility or html() > 10 MB.
- **S1 — pilot view:** `/v2/stats` Prefab page over `/api/stats` +
  `/api/graph/stats` (Metric cards, DataTable for edge types, Sparkline/
  BarChart for edges-by-year). Compare LOC + render parity vs stats.ts.
- **S2 — table views:** companies + sectors (DataTable + Combobox
  filters + Badge status) over `/api/entities`, `/api/sectors`.
- **S3 — prose + search views:** the two existing search surfaces plug
  in here unchanged — note search (`/api/search`, FTS5 over all findata
  notes) and doc search (`/api/docs` catalog, `/api/docs/search` hybrid
  BM25 + cosine, `/api/docs/content` rendering via `Markdown`) — plus
  entity detail over `/api/entity/<p>` and the events timeline. Pattern:
  search `Input` with `on_change` → `Fetch` → ranked results list /
  `Markdown` preview pane (`Tabs` for catalog vs reader). Implementation
  detail: commit searches on Enter (Form submit) or debounce — the docs'
  per-keystroke example is chatty for hybrid cosine queries.
- **S4 — decision slice:** parity review; retire migrated TS views (or
  keep dual-URL); graph view stays on the existing bundle; optionally
  mount it via `Embed` inside a Prefab layout. Frontmatter flips per
  house rules.

**Embed↔cytoscape pattern (doc-verified 2026-09-11, components/embed):**
`Embed` runs raw HTML in an isolated iframe — `<script>` tags, Canvas
and WebGL are explicitly supported in `html=` (srcdoc) mode; the
sanitization worry applies to `Markdown`/`Svg`, not `Embed`. A
`sandbox` prop takes iframe permissions (`allow-scripts`,
`allow-same-origin`). No `postMessage` bridge is documented, so data
passing is one-shot: either `Embed(url=...)` pointing at a slim
same-origin Flask page that fetches `/api/graph/*` itself (right for
the multi-MB cloud payload), or `Embed(html=...)` with the graph JSON
interpolated server-side into the srcdoc (fine for ego/semantic
sub-graphs of a few hundred nodes, wrong at cloud scale). Note for the
security re-check: a srcdoc iframe fetching same-origin APIs needs
`allow-same-origin` alongside `allow-scripts`. S0 adds one probe:
render a small cytoscape ego-graph inside `Embed` and confirm the
round-trip. WebGL is explicitly supported in the iframe too, so a
WebGL graph renderer (sigma.js — `graph_rendering_overhaul.md`) stays
viable under the Prefab shell; recorded as future investigation there.

**Serialization layers (clarified) + PyArrow lane (future
investigation):** Prefab's Python-streaming headline (~70% smaller
than JSON) is about the *UI-definition* lane — agent-authored UI code
streamed over the wire, sandbox-executed server-side, then converted
to the JSON renderer protocol (`$prefab` / `view` / `state` / `defs`,
baked into `PrefabApp.html()`). Flask-served pages do that conversion
in-process, so there is no wire cost for us; the token win matters
only when an LLM authors the UI (future generative lane — a tailwind
for the Slot/component-route pattern). Component *state* and `Fetch`
responses are JSON regardless, so `/api/*` JSON stays the contract for
native components (DataTables are paginated/small). For heavy data
payloads the columnar lane already exists in-repo: `pyarrow` is a direct
dependency (#224), so a Flask route can serve Arrow IPC (Feather
v2 / streaming format) of e.g. the graph cloud or full metric tables
at a fraction of the JSON bytes, decoded in-browser by `apache-arrow`
JS inside an `Embed` iframe or a custom handler. Investigate when S0/S1
payload timings justify it — not a slice here.

**API query endpoint inventory — the Fetch-action consumers (prime
candidates; shapes from route docstrings, app.py 2026-09-11):**

| Endpoint | Returns (shape) | Prefab view | Slice |
|---|---|---|---|
| `/api/stats` | DB statistics cards + breakdowns | Metric cards, DataTable | S1 |
| `/api/graph/stats` | edge types, structure metrics, hygiene, staleness | DataTable + Badge | S1 |
| `/api/graph/edges-by-year` | `timeline[] {year, edge_type, count}` | LineChart / BarChart | S1 |
| `/api/graph/co-mentions` | top-N entities by co-mention | BarChart + DataTable | S1 |
| `/api/graph/metrics/<m>` | pagerank / betweenness / louvain / wcc | DataTable | S1 |
| `/api/analytics/<name>` | named analytics report rows | DataTable (+ Slot fragments) | S1 |
| `/api/entities` | all entities, filterable | DataTable + Combobox | S2 |
| `/api/sectors` | all sectors | DataTable + Badge | S2 |
| `/api/graph/peers/<name>` | competitors (symmetric) | DataTable | S2 |
| `/api/graph/sector/<name>` | sector members / company's sector | DataTable | S2 |
| `/api/graph/country/<name>` | companies listed in a country | DataTable | S2 |
| `/api/graph/exposure` | country listing totals, cap histogram, country×sector matrix | DataTable + Histogram | S2 |
| `/api/graph/bridges` | cross-sector M&A/JV sector pairs | DataTable | S2 |
| `/api/graph/near-duplicates` | QA tripwire note pairs | DataTable (QA view) | S2 |
| `/api/graph/suggestions` | link-prediction suggestions | DataTable | S2 |
| `/api/graph/edition_companies` | companies similar to an edition | DataTable | S2 |
| `/api/search` | FTS5 free-text across all notes | Input + results | S3 |
| `/api/docs` | doc catalog (path filter) | Tabs + DataTable | S3 |
| `/api/docs/content` | raw doc markdown | Markdown | S3 |
| `/api/docs/search` | hybrid BM25 + cosine over doc index | Input + ranked list | S3 |
| `/api/entity/<path>` | entity detail + markdown | Markdown + cards | S3 |
| `/api/events/<name>` | entity timeline rows (date-ordered) | DataTable | S3 |
| `/api/graph/similar/<note>` | similar notes by cosine | DataTable | S3 |
| `/api/graph/neighbors/<name>` | ego-network bundle (nodes+edges) | **cytoscape (TS), not Prefab** | — |
| `/api/graph/cloud` | whole-graph cloud, every entity+edge | **cytoscape (TS), not Prefab** | — |
| `/api/graph/semantic/<name>` | VSS semantic neighbours (node set) | **cytoscape (TS), not Prefab** | — |
| `/api/graph/shortest` | path between two entities | **cytoscape (TS), not Prefab** | — |
| `/api/graph/refresh` (POST) | rebuilds DuckDB cache | Button + Dialog + toast | S4 opt |

Reading of the table: 23 of 28 API endpoints are tabular/chart/prose
shaped and map directly onto Prefab components; the 4 node-link
endpoints (neighbors, cloud, semantic, shortest) keep feeding the TS
cytoscape view; refresh is a one-button admin action. Slices S1–S3
group the candidates by view family so each lands as one coherent
page-set.

**Alternatives considered:**

- **Status quo (grow the TS SPA)** — keeps Node toolchain and DOM
  plumbing for table/markdown work; every new view re-pays router/toast/
  fetch scaffolding. Loses the Python-only iteration loop.
- **Jinja + HTMX** — server-rendered fragments without a component
  system; interactivity still hand-written JS; no charts, no state model.
- **Streamlit / Gradio** — own the whole server and page; cannot mount
  as routes inside the existing Flask app without proxy gymnastics;
  heavier runtime model. Rejected.
- **FastUI** — Prefab's stated inspiration; protocol-only, no bundled
  renderer/DSL maturity for this use; Prefab supersedes it for MCP-era
  needs.
- **Full rewrite incl. graph** — no Prefab graph primitive exists;
  would mean embedding custom JS via Custom Handlers for the core view.
  Rejected; hybrid instead.

## 4. Acceptance criteria & shakedown

1. S0 spike log (Appendix): 3.14 import OK, `prefab version`, html()
   size, Fetch round-trip status against live Flask — repeat 3x for the
   round-trip (timing-shaped).
2. S1 pilot: `/v2/stats` renders all `/api/stats` + `/api/graph/stats`
   fields the TS view shows (parity checklist in the PR); old `/findata`
   untouched; `make frontend-check` (tsc) still green (no TS edits).
3. New tests: page route returns self-contained HTML (contains renderer
   bootstrap + no external asset fetch in bundled mode); Fetch wiring
   smoke via existing Flask test client.
4. Gates green at arc end (`make qa`, with operator go); deptry accepts
   the new direct dep (used in app.py).

| Projected outcome | Today | After (S1–S4) |
|---|---|---|
| TS LOC maintained | 5,291 | ~2,550 (graph + entity entry) |
| Views needing Node to iterate | all | graph only |
| Chart capability | none | 9 chart types |
| New Python deps | — | prefab-ui (+pydantic, cyclopts) |

## 5. Risks

- **Pre-1.0 breaking changes** — pin `prefab-ui==<exact>`; upgrades are
  deliberate slice-sized steps, never floating.
- **Renderer weight per page** — measure in S0; CDN mode (~1 KB + jsDelivr)
  exists if bundled (~6 MB) is too heavy for LAN taste; cache per version.
- **Graph/TS duality** — two UI stacks coexist; contained by keeping the
  TS surface frozen at the graph view and linking between them.
- **New deps (pydantic, cyclopts)** — first UI-layer Python deps; both
  pure-Python, actively maintained; rich already present.
- **Security surface** — Fetch actions run browser-side same-origin;
  re-run the security-eval checklist (doc/local/security/) against the
  new page routes; Prefab sanitizes Svg and sandboxes Embed by design.
- **DSL learning curve** — bounded expression/action language; Custom
  Handlers are the escape hatch if a pipe is missing.

## 6. Non-goals

- No `/api/**` endpoint changes, no auth changes, no theming overhaul.
- No graph-view migration, no cytoscape replacement, no FastMCP/MCP Apps
  in this arc (the same components make that a later, small arc). The
  canvas gets its own arc:
  `../archive/ui/graph_rendering_overhaul.md` (companion, filed same day — executed 2026-09-11, completed.md #225).
- No removal of the Node build while the graph view lives in TS.
- No agent-generated UI surfaces yet (Slot + component routes enable it
  later; out of scope here).

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-11 | `rg '@app\.(route\|get\|post)' app.py \| wc -l` | 32 | 4 page/image + 28 API |
| 2026-09-11 | `find frontend/src -name '*.ts' \| xargs wc -l` | 5,291 | graph.ts = 2,545 |
| 2026-09-11 | PyPI `GET /pypi/prefab-ui/json` | 0.20.2; >=3.10; cyclopts/pydantic/rich | rich already importable in .venv |
| 2026-09-11 | docs prefab.prefect.io (llms.txt + api/fetch/export/serve/fastmcp/app pages) | Flask explicitly supported; Fetch→JSON routes; html() self-contained | claims to re-verify in S0 |
| **S0** 2026-09-11 | `uv venv` + `uv pip install prefab-ui==0.20.2` (scratch, python 3.14.4) | import OK (module name **`prefab_ui`**, not `prefab`); pydantic 2.13.5, cyclopts 4.25.2, rich 15 resolved clean | S0 kill criterion 1 passes |
| **S0** 2026-09-11 | `PrefabApp.html()` size (stats-page tree) | **2,600 B** CDN mode / **6,606,943 B** bundled | kill ceiling 10 MB passed; docs' "~6 MB bundled / ~1 KB + CDN" confirmed |
| **S0** 2026-09-11 | one-page Fetch round-trip vs live Flask `/api/stats` + `/api/graph/edges-by-year` (browser, 3x) | metrics populate with live values; **1441 / 820 / 851 ms** nav→data-visible (cold, warm, warm; bundled 6.6 MB page) | round-trip repeat 3x per acceptance §4.1 |
| **S0** 2026-09-11 | Embed probe: cytoscape ego-graph (6 nodes) in `Embed`, both modes | `html=` srcdoc AND `url=` both paint the canvas; `sandbox="allow-scripts allow-same-origin"`; same-origin `/api/graph/neighbors` fetch inside iframe OK | `</script>` inside srcdoc correctly escaped by renderer |
| **S0** 2026-09-11 | CSP integration probe (app `_security_headers` vs Prefab pages) | app-wide `script-src 'self'` blocks BOTH the CDN renderer and the bundled inline renderer; `/v2/*` needs its own CSP (`'unsafe-inline'` minimum, jsDelivr domains if CDN mode) | Flask after_request hooks run in REVERSE registration order — a /v2 override must be inserted at the list head or `_security_headers` overwrites it |
| **S0** 2026-09-11 | DataTable dynamic rows probe | **BUG (0.20.2):** `DataTable(rows="{{ state.key }}")` crashes the renderer at mount (`t.some is not a function`), even with the state key pre-initialized — takes the whole page down | workaround verified: `ForEach` + `Text` for dynamic rows (renders fetched state fine), or baked array rows; DataTable itself works with static rows (sort/filter/paginate) |

**S0 verdict:** both kill criteria pass (3.14 import OK; html() ≤ 10 MB in
both renderer modes). The Fetch round-trip, metric cards, DataTable
(baked rows), BarChart, and Embed-in-both-modes all verified against the
live Flask app. Integration constraints discovered (CSP policy lane,
after_request ordering, DataTable rows-template bug) are S1 design
inputs, not blockers. S1 should pin `prefab-ui==0.20.2` exact and watch
upstream for the DataTable fix.

---

## S1 execution log — `/v2/stats` pilot (2026-09-11)

**Landed:** `helpers/web/prefab_views.py` (page + `/v2/*` CSP relaxation,
registered from `app.py` via `register(app)`), `helpers/web/__init__.py`,
`tests/test_prefab_views.py` (7 tests over the ts-contract fixture),
`prefab-ui==0.20.2` pinned in `pyproject.toml`.

| Probe / decision | Result |
|---|---|
| Dict→rows binding (the S1 open question, resolved by reading the 0.20.2 renderer) | `ForEach` renders `null` for non-arrays (`!Array.isArray` early-return); the `length` pipe returns 0 for objects; `SetState` accepts raw values only — **no client-side dict shaping exists**. Consequence: `Record<string, number>` API fields (entity_counts, top_sectors, market_cap_counts, edges.by_type) are shaped server-side at page render (sorted desc rows + pct, count scalars); recomputed every GET, matching the TS view's fetch-on-mount freshness |
| Reactive surface | on-mount `Fetch` → `/api/stats` + `/api/graph/stats` drives all scalars: headline + graph-block `Metric`s, `sectors.top.0.sector` (integer dot-path resolves), staleness ternary (Fresh/Stale), structure values |
| Null structure values | `.default("—")` pipe is **unusable**: pipe args serialize bare (unquoted unless they contain spaces) and a bare `—` breaks the renderer's expression tokenizer — the raw `{{ }}` template is left on screen. Workaround: null-comparison ternary (`x != null ? x : '—'`), whose string tokens ARE quoted. Same 0.20.2 family as the S0 DataTable bug |
| BarChart on fetched timeline | bars bind but render **invisible**: default series fill is `var(--color-edges)`, a custom property no host stylesheet defines in bundled mode → explicit `ChartSeries(color=...)` required. Timeline is aggregated server-side per year (the raw rows are per year×edge_type and can't be pivoted client-side) |
| Browser E2E (headless chromium vs scratch Flask) | full parity surface green: 4+6 metrics live, Top Sector "Automotive", Staleness "Fresh", 4 baked breakdown tables, 8 structure metrics with values, timeline chart paints; console clean except Prefab's optional MCP-bridge noise (Method not found) + favicon 404 |
| Tests | 7 passed: 200/html, self-contained bundled page (head-scoped external-asset probe — whole-body probes false-positive on docstrings embedded in the bundle), Fetch wiring, baked-state shapes, dict count scalars, CSP relaxed on `/v2/*` AND still strict on `/findata` |
| `make frontend-check` | prettier flagged `graph.ts`/`graphRenderer.ts` (left over from the committed sigma arc — qa's 9 steps don't include prettier); reformatted, tsc green. No TS changes this slice |

**S1 parity checklist** (vs `stats.ts`): Total Entities / Entity Types /
Sectors / Market Cap Categories cards ✓; Entity Types / Top Sectors /
Market Cap Distribution breakdowns ✓ (Name/Count/Share vs TS's
label/count/pct) ✓; Total Edges / Edge Types / Graph Entities / Company
Sectors / Top Sector / Data Staleness ✓; structure metrics 8 rows with
nullable "—" ✓; Edge Types breakdown sorted desc ✓. Not shown by
design (matches TS): hygiene block, entities.by_type duplication,
sectors.size_distribution. Extras: edges-by-year BarChart (named in the
slice table), table pagination/search for free.

**S1 verdict:** parity met; `/findata` untouched; page cost 231 LOC of
Python vs ~250 LOC of stats.ts + its api.ts contracts. The dict-shaping
split is the one structural wart — upstream dict iteration (or a
computed-state primitive) would remove the baked/ reactive duality and
is the main thing to re-check on any prefab-ui upgrade, alongside the
S0 DataTable fix.

---

## S2 execution log — `/v2/companies` + `/v2/sectors` (2026-09-12)

**Landed:** both pages appended to `helpers/web/prefab_views.py` (same
`register(app)` + CSP lane as S1); 6 new tests (13 total).

| Probe / decision | Result |
|---|---|
| Interactive filtering (the S2 open question, resolved from the renderer source) | on_change actions run SEQUENTIALLY and action props (`Fetch.url` included) are re-interpolated against current state + `$event` (the changed control value) at fire time. A named control's auto state-write is suppressed when on_change is provided (`onValueChange` is occupied by the action runner), so each filter does `SetState(own_key, "{{ $event }}")` first — the subsequent Fetch's `{{ f_* }}` interpolations then see it. Verified E2E: sector=Banking → 1165→54 rows; +search "hdfc" → 1 row, all server-side composed |
| Dynamic DataTable | NOT used: the S0 `rows="{{ state }}"` crash finding stands (0.20.2); rows come from Fetch, so the companies table is `ForEach` over `Table` primitives — which also restores Badge-in-cell parity DataTable can't express |
| Badge status | `Badge(variant="outline")` per row under `If("{{ cap }} != null")` (Condition evaluates client-side against the ForEach scope). Per-value badge COLOR is not expressible (variant is a static Literal) — neutral outline only |
| Pagination | Deferred: TS paginates server-side (20/page); the pilot fetches `limit=300` and renders all rows. Full company set is 1165 — real pagination (offset state + Fetch on Prev/Next) is an S4 decision item |
| Parity deltas vs companies.ts | grid/list layout toggle and geography-tag card line dropped (geography/* extraction isn't expressible client-side — no list-find pipe); search has no debounce (per-keystroke Fetch; SQLite LIKE is cheap locally, flagged chatty in the proposal's S3 note); content-search mode is S3 scope |
| Parity deltas vs sectors.ts | classification tag cloud → Badge row ✓; sector analysis cards with truncate(150) + Read-analysis links ✓; super_sector hierarchy (Bundle M4 payload) not shown, matching the TS view which ignores it too |
| Browser E2E (headless chromium, scratch Flask) | both pages render clean: companies 300-row table with badges + links + live Matches metric; sectors 42 classification badges + 42 analysis cards; zero console errors (beyond the known MCP-bridge noise) |

**S2 verdict:** interactive round-trip — the one thing S1 couldn't prove —
works: filter state, `$event` capture, sequential action execution, and
URL interpolation compose into server-side filtering with zero custom
JS. This is also the load-bearing pattern for S3's search box.

---

## S3 execution log — `/v2/docs`, `/v2/search`, `/v2/entity` (2026-09-12)

**Landed:** three pages appended to `helpers/web/prefab_views.py`; routes
`/v2/docs`, `/v2/search`, `/v2/entity`; 8 new tests (21 total).

| Probe / decision | Result |
|---|---|
| Enter-commit search | `Form(on_submit=...)` renders a real `<form>`; the renderer's onSubmit handler harvests named inputs into state (FormData walk) BEFORE running the actions — `Fetch(url="…?q={{ q }}")` sees the committed value. Confirmed E2E on both search pages. Hybrid search landed in `mode: scan` during the arc because a same-day doc/ addition (parallel session's `doc/design/algorithms.md`) staled the doc_search index — the API's honest-degradation path, not a Prefab issue; `make search-fresh` re-converges it |
| Click-to-reader | catalog/search rows carry a 3-action chain: `SetState("doc_path", …)` → `Fetch("/api/docs/content?path={{ doc_path }}")` (reads the just-set state) → `SetState("docs_tab", "reader")` (Tabs state-binding flips the panel). Tab switching via state write verified |
| SECOND sighting of the tokenizer trap | `{{ doc_hits.length() }}` (method-call form) is not in the expression grammar — the parser throws and the MIXED-string interpolation drops the segment SILENTLY ("mode: scan —  hits"). Rule: pipes only via the `Rx` API (`{{ x \| length }}`), never hand-written method calls |
| Snippet rendering | `/api/search` snippets carry FTS `<mark>` tags; the Markdown component escapes raw HTML → marks show as literal tags. Delta vs the TS view's highlighted snippets; harmless, logged |
| Entity + timeline | one submit fires both `/api/entity/<name>` and `/api/events/<name>` (sequential actions, both interpolating `{{ entity_q }}`); detail card shows name/type/sector/cap badges + full note markdown (Coforge: 2 event rows, ~10k chars rendered); events 404 degrades to count 0 via on_error |
| Parity deltas vs docs.ts | no vault collection (wikilinks/frontmatter chips/read-size focus mode are TS-reader polish); doc_type Combobox instead of the TS toggle; no anchor deep-links to section lines; search q is raw-interpolated (URLSearchParams-grade encoding absent — a literal `&` in a query would truncate the param) |

**S3 verdict:** both search surfaces + entity detail + events timeline
live with zero custom JS. The remaining S3-table endpoints (peers,
sector members, exposure, bridges, near-duplicates, suggestions) are
mechanical ForEach+Table repeats of the S2 pattern — deferred to the
S4 parity review rather than expanding this slice.

---

## S4 execution log — parity review + decisions (2026-09-12)

**Consolidated E2E parity pass** (one headless-chromium run, scratch
Flask, all six pages, zero console errors beyond the known MCP-bridge
noise):

| Page | Verified live |
|---|---|
| `/v2/stats` | 1649 entities / 19261 edges / Top Sector Automotive / structure metrics / timeline chart SVG |
| `/v2/companies` | sector=Banking → exactly 54 rows, Matches metric in sync |
| `/v2/sectors` | 42 classification badges + Sector Analysis cards |
| `/v2/docs` | 141 catalog rows → Reader flip with rendered markdown |
| `/v2/search` | FTS5 "shrimp feed" → 60 hits with snippets |
| `/v2/entity` | Coforge detail + Events — 2 timeline + full note markdown |

**Decisions (S4 is the decision slice):**

1. **Keep dual-URL** — `/findata` stays authoritative; `/v2/*` runs
   alongside it. Rationale: the TS reader still owns vault collection,
   section anchors, highlighted snippets, and URLSearchParams-grade query
   encoding; the graph tab stays on the sigma bundle regardless, so
   /findata lives either way. Flipping the tabs would trade real UX for
   bundle-size savings before the 0.20.2 gaps close (dict iteration,
   DataTable dynamic rows, pipe-arg quoting — the three things to
   recheck on any prefab-ui upgrade). Revisit on the next prefab-ui
   minor that closes any of them.
2. **Remaining S2/S3 table endpoints (peers, sector members, country,
   exposure, bridges, near-duplicates, suggestions): DESCOPED from the
   /v2 surface.** They are mechanical ForEach+Table repeats, but
   graph-adjacent exploration is already served better by the graph
   bundle (tooltips, ego re-centre, spotlight); a Prefab mirror adds a
   second, weaker surface to maintain. Recorded as the ratchet: if a
   future Prefab migration wants them, the S2 pattern is the template.
3. **Companies-table pagination: deferred, not descoped.** The 300-row
   fetch covers pilot use; server-side offset pagination (state +
   Prev/Next Fetch chains) is the follow-up if /v2/companies sees
   operator use.
4. **Embed-mount of the graph view: not taken** (matches the graph
   overhaul's recorded-NOT-scheduled stance — one-shot data passing with
   no postMessage bridge makes it a demo, not a feature).

**Verdict:** S0–S3 landed (6 pages, 21 tests); S4 decisions recorded
above; proposal EXECUTED with dual-URL as the standing posture.
