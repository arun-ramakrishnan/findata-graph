---
title: "Promote findata-graph desktop demo to full app — docs, metrics, time-travel, bundling"
status: executed
filed: "2026-09-27"
executed: "2026-09-28"
completed_md: "308"
area: "desktop"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Promote findata-graph desktop demo to full app — docs, metrics, time-travel, bundling

**Date:** 2026-09-27 · **Status:** PROPOSED ·
**Area:** desktop (Tauri v2 + Vue 3 + findata-core rusqlite)

## 1. Motivation

The web interface is not intuitive for exploring the knowledge graph
(tauri_assessment Motivation). The Tauri demo accepted 2026-09-27 proves
the local-first path: full binary compiles, 8 commands wired, all 3
headless-Chrome scenarios render, `make dev` launches on DISPLAY=:1
(`doc/local/evaluations/tauri_assessment.md` Verification).

The demo is deliberately a viewer, not the app: Rank/Time analytics,
Chronoscope (`as_of`), hypergraph, metrics, docs browser, external-link
opening, and bundling/installers are all out of scope
(`desktop/README.md` §Deliberately out of scope). Until
they land, the desktop cannot replace the Flask surfaces it mirrors —
it is a second renderer over the same `research.db` source of truth,
not a second backend.

Trigger: user acceptance of the demo (2026-09-27) — the assessment's
deferred "Proposal doc" step is now due.

## 2. Evidence (measured 2026-09-27, this box)

Demo inventory (source only, post-`make clean`: 71M total incl.
70M `node_modules/`; `src-tauri/target/` + `src-vue/dist/` removed):

| Surface | Measured state | Verdict |
|---|---|---|
| `core/src/lib.rs` | 700 lines, 24 `fn`, SQLite read-only per-call; `graph.duckdb` untouched | keep as data layer |
| `src/main.rs` | 77 lines, 8 commands 1:1 over core (`stats`, `graph_cloud`, `graph_ego`, `search_notes`, `suggest`, `sectors`, `read_note`, `entity_note`) | keep thin-wrapper contract |
| Vue app | GraphView 581 + NotePanel 214 + SearchBar 203 + App 234 + store 178 + api 22 + colors 32 lines; d3 + markdown-it + Tailwind v4 | keep; GraphView is the only heavy file |
| Tests | `core/tests/live.rs` 158 lines / 11 tests vs live `research.db`; harness `test/main.js` 84 lines, `?scenario=search\|note\|cloud` | keep; extend per slice |
| Fixtures | `test/fixtures.js` generated from live DB via `test/make_fixtures.py` (`make fixtures`) | keep; refresh cadence in S1 |
| Assessment | `cargo test -p findata-core` 11/11, `cargo check`/`build`, `npm run build`, 3/3 harness scenarios, `make dev` — all PASS at filing | baseline; re-prove per slice, never one run |

What the numbers mean: the demo's value is the IPC + data-layer
contract (per-call connections, errors → String → toasts, no lock held
across calls), not the pixels. Every slice below adds a surface through
that same contract; none adds a backend (no DuckDB, no Flask sidecar —
SQLite-only per user decision). Ruled out: porting the Flask server
into the desktop (defeats local-first), and reaching for Lap-scale
scope (photos/faces/maps — assessment Out of Scope stands).

## 3. Design

Contract all slices share: new query → new `findata-core` fn + new
`#[tauri::command]` thin wrapper + store action + view piece; errors
stay `String`; `Db` stays per-call read-only. Alternatives considered
per slice inline; each slice lands independently in S1→S6 order (each
unblocks the next only in review load, not in code).

**S1 — Track the demo, pin the baseline.** `git add`
`desktop/` source (`.gitignore` already covers
`target/`, `node_modules/`, `dist/`); add `make fixtures`-freshness to
the demo README; re-prove the assessment matrix twice post-clean
(`cargo test -p findata-core`, `npm run build`, 3/3 harness
screenshots). Unblocks everything: without a tracked baseline every
later slice diffs against air.

**S2 — Docs browser.** `browse_docs` over the `doc_search` FTS5 sidecar
(`memory/doc_search.db`: new `findata-core` fn + command + store action
+ sidebar section: filter input + flat list + existing NotePanel) on top
of the already-present path-guarded `read_note`. Two falsified premises
on the way here: (1) the original plan (reuse `search_notes` with the
`doc_type` param) — `note_search` holds 0 `doc/%` rows; (2) the interim
disk-walk — docs search is already sqlite-embedded in the sidecar
(244 files / 2244 chunks, porter-stemmed, BM25 + per-file dedupe in the
`search_page` shape). Unfiltered listing sorts by path; MATCH filter
caps at 200 (AND-first, OR-fill). Residence rationale: the sidecar
deliberately lives outside `research.db` so `doc/local/` plaintext
never enters the snapshotted DB — the desktop opens it as a second
read-only connection. Missing sidecar is a hard error (`make
search-fresh` rebuilds it). Alternative (index `doc/` into `note_search`
via the Python pipeline) rejected: breaks the residence boundary.

**S3 — Metrics + Rank/Time panels.** Core fns reading the existing
SQLite metric/rank tables (no new derivation in Rust — Python
`derive_insights`/analytics stay the writers); ego view gains a metrics
tab, cloud legend gains rank tint. Alternative (recompute metrics in
Rust) rejected: duplicates the Python pipeline and risks divergence.

**S4 — Chronoscope (`as_of`).** Core fns thread an optional `as_of`
param into ego/cloud queries against the existing temporal columns;
UI gains a date scrubber that re-invokes with the same args + `as_of`.
Alternative (snapshot swapping) rejected: per-call connections make
param threading cheap, file swapping racy.

**S5 — Hypergraph lane.** Read-only hyperedge fetch in core + overlay
in GraphView (toggle, distinct colour per hyperedge). Deferred until
S3/S4 prove the tab/param patterns, because it composes both.

**S6 — Opener + bundling.** `tauri-plugin-opener` for external links,
`tauri.conf.json` bundling targets, icons audit, `make build`
smoke-run of the release binary. Ships the app only after the S2–S5
surfaces exist (S7 can still feed back a model artifact into the
bundle — see below). Alternative (bundle now) rejected: installers
for a viewer would freeze the IPC surface prematurely.

**S7 — Hybrid semantic search.** `search_hybrid(q)`: BM25 leg = the
existing `search()` ranks; cosine leg = pure-Rust cosine over stored
note vectors (FTS JSON embeddings / `company_embeddings` BLOBs); RRF
fuse K=60 with BM25-only degradation on dims mismatch — the
`rebuild_doc_search.py:857` rule mirrored, not reinvented.
Query-embedding decision (open, feeds back into S6 bundling): (i)
bundle the granite GGUF + `llama-cpp-2` — full parity, tens of MB of
model weight plus a heavy native dep; (ii) `similar_notes(path)` first
— stored vectors only, no model, ships the fusion machinery cheap;
(iii) query-vec IPC from a local helper — rejected (breaks the
no-Python-backend rule). Lands (ii) → (i) only if accepted, after S6
because (i) changes what the installer ships. Alternative (DuckDB
`vec0` KNN in the desktop) rejected: a second engine for what
pure-Rust cosine already does over SQLite-stored vectors.

**S8 — Frontend type-checks (deferred hygiene).** `vue-tsc` integration
for `desktop/src-vue`: `jsconfig.json` with `checkJs`, `npm run
typecheck` script, zero-baseline on the S1–S7 surface. Filed per
operator request 2026-09-28; explicitly NOT in the S5/S6/S7 path —
runs after the arc lands, when the component surface is stable.
Alternative (TypeScript rewrite of the frontend) rejected: checkJs over
the existing JS buys the safety without a language migration; the
Svelte question is separately closed (Vue stays — the docs list it
first-class, and rewrite cost buys no functional gain).

## 4. Acceptance criteria & shakedown

1. S1: `cd desktop && make test` 11/11 twice + `make smoke`
   3/3 screenshots render + `git status` shows source tracked, artifacts ignored.
2. S2–S5 + S7: each slice adds ≥1 live-DB core test + 1 harness scenario or
   fixture regeneration (`make fixtures` + `cargo test -p findata-core`),
   run twice; GraphView stays ≤2 s ego cool-down on the CEAT-class ego.
3. S6: `make build` release binary launches via `make dev`-equivalent
   path and renders the cloud scenario once; installer artifact exists
   for the host platform.
4. Gates stay green throughout: `make qa` blocking legs, `ruff` +
   `make md-lint` clean on touched files; no Flask/Python backend added.
5. Eval-gate: N/A — this arc adds no query-visible semantics to rosters,
   crosswalks, hierarchies, or extractor rules (read-only views over
   existing tables); `ontology_eval_gate.py` not triggered. If S3/S4
   scope creeps into derivation, file an amendment before implementing.
6. S7: RRF order differs from BM25-only on ≥1 pinned probe query
   (live-DB test); `similar_notes` harness scenario renders; the
   query-embedding option (bundle GGUF vs stored-vectors-only) is
   decided before S6 bundling closes.

| Projected outcome | Today (demo) | After (full app) |
|---|---|---|
| Surfaces in desktop | ego / cloud / search / note | + docs, metrics/rank/time, as_of, hypergraph overlay, hybrid search, opener, installer |
| Tracked source | untracked (`??`) | tracked, artifacts ignored |
| IPC commands | 8 | ~13–15 (docs + metrics + as_of + hypergraph + hybrid) |
| Test floor | 11 core + 3 harness | 11 + ≥1/slice core, harness per surface |

## 5. Risks

- **Scope creep into derivation (Rust recomputes metrics)** — mitigation:
  S3 contract forbids it; Python stays the writer, Rust the reader.
- **GraphView becomes the god file (581 lines already)** — mitigation:
  new tabs/overlays go in new components, GraphView keeps canvas+sim only.
- **Fixture rot (`fixtures.js` drifts from live DB)** — mitigation: S1
  documents the `make fixtures` cadence; any slice touching queries
  regenerates fixtures in the same change.
- **Query-embedding weight (S7 option i)** — mitigation: default to
  `similar_notes` on stored vectors (no model); bundling the GGUF is an
  explicit decision with a measured installer-size delta, not drift.
- **Build weight (`target/` was 3.1G)** — mitigation: never commit
  artifacts; `make clean` is the documented reclaim; CI (if added)
  caches `target/`, it does not track it.

## 6. Non-goals

Photo/video management, face recognition / AI tagging, map views /
smart albums, full Lap backend features, DuckDB integration (SQLite
only per user decision), pipeline embedding-model or ranking-semantics
changes (S7 mirrors the RRF rule viewer-side over stored vectors —
reads only, changes nothing upstream), Flask parity beyond read
surfaces. From the README list, Rank/Time *display* is S3 but Rank/Time
*derivation* stays out — as does any write path into the vault.

## 7. References

- `doc/local/evaluations/tauri_assessment.md` — accepted demo record
- `desktop/README.md` — run/verify/architecture, out-of-scope list
- `desktop/src-tauri/src/main.rs` — 14-command contract (S2: +`browse_docs`, S3: +`entity_metrics`/`metric_values`, S5: +`entity_hyperedges`, S7: +`similar_notes`/`search_hybrid`)
- `desktop/src-tauri/core/src/lib.rs` — SQLite data layer
- `desktop/src-vue/src/components/GraphView.vue` — canvas + d3-force
- `desktop/Makefile` — install/dev/test/build/fixtures/smoke/clean
- `helpers/maintenance/rebuild_doc_search.py:857` — RRF K=60 fusion rule S7 mirrors
- `helpers/core/vec_search.py`, `helpers/core/vss_index.py` — cosine legs (sqlite-vec / company BLOBs)
- `helpers/core/local_embedder.py` — query-side embedder (granite GGUF) behind S7 option (i)

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-27 | `du -sh desktop/` (pre-clean) | 3.2G | 3.1G target/ + 70M node_modules/ |
| 2026-09-27 | `make clean && du -sh .` | 71M | target/ + dist/ removed |
| 2026-09-27 | `wc -l` over vue/tauri sources | 1706 lines (see §2 table) | post-clean tree |
| 2026-09-27 | `git status --porcelain -- desktop/` | `?? desktop/` | fully untracked |
| 2026-09-27 | assessment Verification (filing) | 11/11 cargo, check/build, npm build, 3/3 harness, make dev | baseline to re-prove in S1 |
| 2026-09-28 | S1 re-prove post-rename | cargo 11/11 twice, npm build, smoke 3/3 (verified renders) | stale Sep27 vite killed (squatted :5173, 404s) |
| 2026-09-28 | `note_search` doc_types + `doc/` census | 0 `doc/%` rows; doc/ = 231 md / 3.8 MB | S2 FTS-scope premise falsified → disk-walk |
| 2026-09-28 | S2 shakedown (disk-walk, superseded) | cargo 12/12 twice, npm build, smoke `docs` 231/231 render | premise falsified mid-slice — see next row |
| 2026-09-28 | S2 revision: doc_search sidecar | 244 files / 2244 chunks (39 local), cargo 13/13 twice, fixtures mirror sidecar | search is sqlite-embedded — disk-walk replaced, same IPC contract |
| 2026-09-28 | S7 grounding: hybrid leg | RRF K=60 both lanes; granite GGUF query-side; 1186 company BLOBs + FTS JSON vecs in SQLite | S7 filed: RRF mirror, (ii)→(i) staging, vec0 rejected |
| 2026-09-28 | S3 shakedown | cargo 15/15 twice; `entity_metrics` CEAT 17+100; pagerank top Reliance; smoke `metrics` badge ok | NULL labels real (12 guidance rows → Option); cloud shot flaky under virtual time, state proven |
| 2026-09-28 | S3 data findings disposition | fixture tint unioned w/ ego nodes (228 entries, 42/57 overlap); NULL-label census 3,119 rows / 476 entities | finding #2 fixed in-arc; #1 deferred to `company_metrics_null_labels.md` (desktop robust) |
| 2026-09-28 | S4 shakedown | cargo 17/17 twice; CEAT 2022 drops Camso + 2026 listings; smoke `asof` badge + scrubber render | single static SQL w/ NULL-bound params (no 4-way match); cloud summary stays unfiltered |
| 2026-09-28 | S5 shakedown | cargo 18/18 twice; CEAT 12 hedges, @2022 drops acquisition:1; smoke `hyper` chips + halo render | incidences undated (edge validity governs); halo is paint-time via hyperRev |
| 2026-09-28 | S6 shakedown | opener wired (least-privilege URL perm); deb 5.1M + AppImage 86M built; release binary pixel-proven under xvfb; smoke `opener` fallback green | conf split-base quirk (commands↔desktop, dist↔src-tauri); no new core fn — deviation from per-slice core-test rule recorded |
| 2026-09-28 | S7 shakedown (option ii) | cargo 20/20 twice; similar Avanti top + determinism pinned; hybrid head-stability pinned; smoke `hybrid` badge + toggle render | embeddings are f32-LE BLOBs (not JSON); sidecar-pin lesson: S2 test now pins stable architecture.md, never the churning proposal |
| 2026-09-28 | S8 shakedown | `vue-tsc` checkJs zero-baseline; `make typecheck`; note smoke re-shot | caught a REAL bug: `openEntityNote` used-but-never-imported (“Open note →” dead since S3); harness window props via `harness.d.ts` |

---
**Follows:** doc/local/evaluations/tauri_assessment.md
