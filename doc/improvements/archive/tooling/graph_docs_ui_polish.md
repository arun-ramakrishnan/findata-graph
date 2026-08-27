# Proposal: Lens + Reading Room UI polish — graph widget overhaul & reader comfort

**Date**: 2026-08-27
**Status**: EXECUTED 2026-08-27 (same sitting; checklist §6 steps 1–7).
Live battery on :5200 verified: ego view (human-sized focal, sector label
inside the rectangle, zoom capped), acquired-only isolate (all labels on,
direction arrows, truthful counts), double-click-to-isolate, All-mode
search spotlight (in-filter hit stays in All; miss falls back to Ego),
Docs breakout width + focus mode + Esc + S/M/L persistence. tsc green,
`test_integration_ts_contract` 33 passed. Two deviations from §2: (1) edge
labels show the relationship TYPE only in mixed views — the cloud payload
carries no per-edge properties (no year) and the API surface stays frozen;
(2) the Full-graph cloud keeps its degenerate-one-ring "components" layout —
a data-density regression from the E2–E6 relation enrichment, pre-existing,
not addressed here. Environment note: the in-app browser harness converts
the Refresh-DB POST to a GET (405); server-side POST verified 200 via curl
— not observed to affect a normal browser.
**Depends on**: frontend esbuild pipeline (`make frontend` / `frontend-check`),
cytoscape + cytoscape-fcose (already vendored). **No backend/API changes** —
the TS-contract test surface is untouched by design.
**Trigger**: user report 2026-08-27 with two screenshots — *"the design is very
clunky" (All mode, acquired-only filter), *"the docs interface is not very
convenient to read stuff and too narrow"*, *"the button has no label and tiny
and blue … also doesnt refresh when i change the Edge type"*, *"can you also
change the graph display widget. It is looks very clunky"* (ego screenshot).
Note: the archived `graph_improvs.txt` pass was algorithm-coverage work; it
never touched this UI surface — this proposal is the UI counterpart.

---

## 1. Problem statement

Two tabs of the Flask UI feel clunky in daily use. Every failure below was
reproduced from the user's screenshots and traced to exact code.

### 1.1 Graph — ego widget (screenshot 2: "Nureca" focal, 3 peers + sector)

| Symptom | Root cause | Evidence |
|---|---|---|
| Focal node renders as a huge gold blob with a thick ring | 46px focal + 3px border + bold label; zoom amplifies | `graph.ts:1827-1831`; `cy.fit()` uncapped at `graph.ts:1250` |
| A 4-node graph fills the whole canvas at 208% zoom — giant sparse shapes, vast dead space | `loadEgoNetwork` runs `cy.fit(undefined, 40)` with **no zoom ceiling**, so tiny graphs balloon to `maxZoom` (3×) | `graph.ts:1250`, `243-244` |
| Sector node = flat teal rectangle with its label **clipping into the shape** ("Healthcare" half-swallowed) | rectangles keep the global `text-valign: bottom` + 2px outline; label lands half-inside the 50×30 shape | `graph.ts:1815-1818` (global), `1862-1865` (sector rect) |
| Edges read as thick sausages with chunky boxed labels ("peer") | `width: 2` + 9px boxed edge labels at high zoom; ego labels never zoom-gated (correct) but visually heavy | `graph.ts:1885-1898`, `1251` |
| Overall widget look: flat discs, hard outlines, no depth/hover states | stylesheet palette + no interaction styling beyond `.focal`/`.highlighted` | `graph.ts:1810-1951` |

### 1.2 Graph — All mode + edge-type filter (screenshot 1: "acquired 41" selected)

| Symptom | Root cause | Evidence |
|---|---|---|
| Node sizes are meaningless after filtering: 1-edge pairs render as ~46px blobs | node `deg` + hub flag + `mapData` sizing all derive from degree computed **once over the FULL graph**, never recomputed for the visible subgraph | `graph.ts:493-497` (degree), `591` (hub flag = deg ≥ 6), `1934-1935` (`mapData(deg, 1, 40, 10, 46)`) |
| **Zero labels visible** at the default post-filter view | zoom-fade buckets hide labels < 0.8× except hubs (hub = full-graph degree ≥ 6 — absent in pairs); `min-zoomed-font-size: 7` kills the 9px label below ~0.78× | `graph.ts:175-177` (`_ZOOM_LBL_OFF/HUBS`), `1474-1498` (`_applyLabelBucket`), `1933` |
| Pairs scattered across a mostly-empty grid | component packing uses a fixed `cellPad = 90` regardless of component size → 2-node components each claim a ~150px cell | `graph.ts:1650-1654` |
| The one thing that would make an acquisitions view readable — which company acquired which, and when — is hidden | cloud edges hard-suppress labels (`label: ""`, `text-opacity: 0`) unconditionally, tuned for the 4k-edge full cloud but applied to 41-edge subgraphs too | `graph.ts:603`, `1923-1929` |
| Garbled overlapping text directly under the ENTITIES heading | entity-type **names are rendered inside the 12×12px color swatch span**, so each label overflows its swatch box and overlaps its neighbours; `edition` has no swatch color rule at all (transparent) | `graph.ts:686-689` (label inside swatch), `findata.css:2623-2629` (12px box), `2631-2635` (5 of 6 types colored) |
| Filter chrome eats ~35% of the canvas column's height | legend panel stacks ENTITIES + RELATIONSHIPS groups above the canvas in a 2fr/1fr column pair | `findata.html:244-253`, `findata.css:3032` |

### 1.3 Graph — buttons

- **`#graph-search-btn`** (`findata.html:220-222`): `btn-primary` (blue) with an
  icon-only arrow — no label, tiny hit area. Behaviour compounds it: empty
  input silently does nothing; a name **yanks the view to Ego mode**, so after
  changing edge-type chips in All mode the button appears to "not refresh"
  anything (`graph.ts:280-283`).
- **Refresh DB** (`graph.ts:302-327`): re-opens the connection, wipes every
  cache — then tells the user to *"re-run the view to see new data"*
  (`graph.ts:308`) instead of just re-running it.

### 1.4 Docs — Reading Room

Measured on the user's ~1350px window: `.container` caps at 1400px
(`findata.css:21-23`) → 330px sidebar (`1970-1976`) + 235px TOC rail
(`2212-2217`) + 2×2.5rem pane padding (`2203-2204`) + `reader-main` 72ch cap
(`2219-2221`) ⇒ a **~600px text column** — under half the window — inside an
internal `max-height: 78vh` scroll box (`2205`). No way to widen, no focus
mode, no text-size control, TOC doesn't stick while scrolling.

---

## 2. Design

Still cytoscape + the existing token system (`tokens.css`); **no new
frontend dependencies**. All numbers below are design targets, tuned in the
live-verification pass (§5).

### 2.1 Graph widget visual overhaul (`_cytoscapeStyle()` + canvas chrome)

**Ego mode**
- Node sizing down and layered: focal ~32px with a **thin ring + soft glow**
  (replacing the 46px/thick-border halo); neighbours ~22-24px.
- Rectangular shapes (sector/sub_sector/super_sector) render their label
  **centred inside the shape** (per-group `text-valign: center`); round nodes
  keep labels below with a clean halo plate — no more clipping.
- Edges: 1.5-2px, per-type colour at ~75% alpha; smaller arrows; edge labels
  as compact plates (smaller font/padding than today).
- **Zoom cap after ego fit: ~1.3×** — a 4-node graph stays human-sized
  instead of stretching to 208%+. (`postfit` clamp in `loadEgoNetwork` +
  `_renderShortestPath` path mode + `_expandNode`.)
- fcose `idealEdgeLength` tightened for tiny graphs so relatives don't fly
  to the corners.

**Cloud/filtered mode**
- Degree→size mapping moves to a **sqrt curve with a hard cap** (≈ 8-30px),
  eliminating the blob extremes; hub threshold recomputed per visible state.
- Node fill: type-tinted with 1px ring borders (depth without slabs); label
  halo plates instead of hard 2px outlines.
- Hover = **neighbourhood highlight** (hovered node + its edges full, rest
  dimmed, animated); selected node gets a clear ring. Today hover only shows
  a tooltip.
- Layout transitions **animated in cloud mode too** (`animate: !cloud` →
  always animate; the hard cut on every chip toggle is a big part of the
  "clunky" feel).
- Canvas chrome: calmer flat background + faint dot texture; zoom cluster
  with larger hit targets; tooltip + empty-state polish.

### 2.2 Filtered-subgraph rendering policy (`_applyCloudFilter`)

1. Recompute **visible-degree** per filter state; size + hub flag + sqrt
   sizing read from it (full-graph degree no longer leaks into filtered
   views).
2. **Adaptive label policy**: visible nodes ≤ 400 → labels always on
   (zoom-gating suspended — it exists for the 700-node full cloud only);
   above that, current zoom-fade buckets.
3. **Edge labels on** when visible edges ≤ 80 (acquired year, JV venture,
   "part of"); suppressed above.
4. **Compact component packing**: cell pad scales with component size
   (pairs pack tight; large components keep air).
5. Status line keeps reporting counts (unchanged contract).

### 2.3 Legend / toolbar compaction + swatch fix

- Entity-type chips become `swatch square + label text beside it` (label out
  of the 12px box); add the missing `edition` color.
- Legend compacts to a slim strip above the canvas (ENTITIES inline with
  RELATIONSHIPS chip row); the Relationship Cloud card stays.
- Reproduce-and-fix any residual overlap at 1350px during verification.

### 2.4 Buttons

- `#graph-search-btn`: visible text label ("Centre"), standard `btn-primary`
  size. **All-mode behaviour**: if the typed entity is in the (filtered)
  cloud → spotlight its component + centre the camera there (no mode yank);
  if absent → say so in the status line and fall back to the Ego jump.
  Ego mode keeps today's behaviour.
- Refresh DB: after a successful refresh, **auto re-run the active view**
  (cloud reload / ego re-run / rank-time reload) — no "go re-run it
  yourself" message. Spinner + disabled state stay.

### 2.5 Docs — Reading Room comfort

- **Width liberation**: `#docs-view` exempted from the 1400px container cap
  (fluid to ~1800px); sidebar 330→300px; TOC rail 235→215px; `reader-main`
  72→80ch; pane padding up on wide screens; body text ~1.05rem / 1.7.
- **Focus mode**: reader-header toggle — hides sidebar + TOC rail, centres a
  ~76ch column, `Esc` exits, preference persisted (`localStorage`).
- **Text size control** (S/M/L) in the reader header, persisted.
- TOC rail becomes sticky while the article scrolls.

### 2.6 Out of scope (explicitly)

Backend/API changes; Ego/Path/Rank/Time data logic beyond styling + the two
button behaviours; a replacement graph library; the entity pages bundle
(`entity.ts`); pagination/search behaviour in either tab.

---

## 3. Alternatives considered

- **Switch graph library (sigma.js / react-force-graph / three.js)** —
  rejected: cytoscape's interaction model (tap/select/expand), fcose and the
  existing styling architecture are fine; the clunk is our stylesheet +
  sizing policy, not the engine. A swap would risk every mode for cosmetics.
- **Edge-list side panel for filtered views** (table of "acquirer → target
  (year)" rows, click to spotlight) — genuinely useful for one-type views;
  proposed as a *follow-up option* if the graph rendering alone still isn't
  readable enough after §2.2. Not in the first slice to keep the diff
  reviewable.
- **Full container/grid rework of both tabs** — rejected: the width problem
  is docs-specific; the graph tab's grid is sound once the legend strip
  compacts.
- **Server-side subgraph endpoint (filter in SQL)** — rejected: the full
  cloud payload is already client-cached and filtering is instant
  client-side; no API change wanted (TS-contract surface stays frozen).

---

## 4. Tests & verification

1. `make frontend-check` (tsc) after each TS slice; `cd frontend &&
   npm run build` for the bundle (the running Flask serves the bundle from
   disk — no restart needed).
2. Targeted: `tests/test_integration_ts_contract.py` (expect unchanged-green
   — proof the API surface stayed frozen).
3. **Live browser battery** on :5200 (app already running; fresh browser
   profile sidesteps bundle caching):
   - Ego: small graph (e.g. Nureca) — node sizes, label-inside-rectangle,
     zoom cap ≤ ~1.3×, edge weight/labels.
   - All → acquired-only: visible-degree sizing, labels on, edge labels
     with years, compact packing, legend strip clean (no overlap).
   - Search "Centre" in All mode spotlights within filter; Ego fallback
     message; Refresh DB re-renders the active view.
   - Docs: width at 1350px, focus mode toggle + Esc, S/M/L persistence,
     sticky TOC.
   - DOM assertions where possible; screenshots judged with **neutral**
     vision prompts (established recipe — leading checklists hallucinate).
4. Full gates (`make qa` etc.): **only on explicit permission at the end**,
   per standing etiquette.

## 5. Risks / rollback

- Pure client-surface change: rollback = revert the working-tree files +
  rebuild bundle; no data, no schema, no API drift.
- Browser bundle caching for the user's existing tabs: hard-reload needed
  once; noted in the wrap-up message.
- Risk of over-tuning visual constants: mitigated by the live battery
  before declaring done; constants grouped at the top of the style block
  for cheap iteration.
- The proposal doc itself is the only doc/ corpus change → wrap-up includes
  the search-trio incremental rebuild + `--check` (standing rule).

## 6. Execution checklist

1. [ ] Graph stylesheet + sizing rework (§2.1) — `frontend-check` green.
2. [ ] `_applyCloudFilter` visible-degree + adaptive labels + packing (§2.2).
3. [ ] Legend strip + swatch fix (§2.3).
4. [ ] Search "Centre"/spotlight + Refresh auto re-run (§2.4).
5. [ ] Docs width + focus mode + text size + sticky TOC (§2.5) — TS + CSS.
6. [ ] Bundle build; live browser battery (§4.3); tune constants.
7. [ ] TS-contract targeted test; search-trio rebuild; wrap-up summary
       (full gates on explicit permission).
