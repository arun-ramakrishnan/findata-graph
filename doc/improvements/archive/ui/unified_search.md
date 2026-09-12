---
title: "Unified UI search — /api/scripts/search endpoint plus a grouped Docs/Scripts/Notes search view"
status: executed
filed: "2026-09-12"
executed: "2026-09-12"
completed_md: "229"
area: "app.py, frontend/src/views, frontend/src/core/markdown.ts"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Unified UI search — /api/scripts/search endpoint plus a grouped Docs/Scripts/Notes search view

**Date:** 2026-09-12 · **Status:** EXECUTED (completed.md #229) ·
**Area:** `app.py` (one new route), `frontend/src/views/` (new SearchView or
DocsView extension), `frontend/src/core/markdown.ts` (snippet highlighting
via the sugar-high path landed in
`../archive/ui/sugar_high_highlighter.md`)

## 1. Motivation

The UI can search two of the house's three content-addressable surfaces:
`/api/search` (notes over `research.db`, app.py:759) and
`/api/docs/search` (doc/ corpus hybrid, app.py:1161). The third surface —
the script/test/make/Mojo index — is agent-only: `helpers/misc/
script_query.py` is a CLI, and its docstring already anticipates "an
eventual /api/scripts/search" wrapping
`helpers/maintenance/rebuild_script_search.search_scripts` (rebuild_script_search.py:1561).
Trigger: operator observation 2026-09-12 after the sugar-high arc landed —
"given all this power, enable script_search in the UI and unify the three".

Consequences of the status quo:

- The browser surface cannot answer "which script audits relation
  diffs" / "which test covers the yfinance driver" — the INTENT layer over
  ~200 scripts/tests/make targets is reachable only from a shell.
- Three separate entry points (two endpoints + a CLI) with three response
  shapes; no single place shows where a topic lives across the corpus.

## 2. Evidence (measured 2026-09-12, this box)

| Configuration | Result | Verdict |
|---|---|---|
| `search_scripts()` core | exists, hybrid BM25+cosine, `kind`/`area` filters on UNINDEXED columns, "never raises", no per-file diversification cap (every row is a distinct script) | endpoint is a thin wrapper — adopt |
| `/api/docs/search` contract | q/limit/hybrid params, `stale` flag + scan fallback, never 500s on stale index | shape to mirror for scripts |
| `/api/search` (notes) contract | FTS5 + optional hybrid RRF, `<mark>`-highlighted snippets, 503 when index absent | keep as-is; client adapts |
| sugar-high coverage (node_modules, 2.4.0) | 28 grammars incl. python, typescript, javascript, sql, yaml, toml, shell, markdown, diff; **no mojo** | covers all repo surfaces except Mojo — see §3 mapping |
| highlighting cost | sugar-high already in both bundles (landed 2026-09-12: +66 KB unminified per IIFE copy, net static −76 KB vs hljs+prism) | per-snippet highlight is free — no new dependency |
| `_cosine_leg` decode (found in S1) | **bug**: embeddings are vec_codec blobs but the leg decoded `json.loads(emb)` — every row failed decode, mode never "hybrid", similarity always null; fixed to `load_vec(emb)` (the doc-side decode) | hybrid leg was silently dead since the script_search arc; now live (358 embedded rows, 384-dim, LE.available() True) |
| decode-class audit (S1 follow-up, operator-requested) | swept every embedding consumer for raw `json.loads` on stored columns: `app.py _scored_rows` (notes hybrid Python fallback) **broken the same way** — sim silently 0.0 for every post-blob-migration row; `helpers/bench/note_deep_probe_candidates.py` same; doc/note dims gates + KNN map + `_refresh_embed_matrix` already at the choke point (the matrix fix is the recorded #224 strike) | 3rd strike of the class → both fixed to `vec_codec.load_vec`; regression tests now seed BLOBs, not just TEXT |

What was ruled out: a server-side merged endpoint fanning out to all
three indexes and blending results — BM25 scores across three
differently-built indexes (research.db FTS5, doc_search sidecar,
script_search sidecar) are not comparable; normalizing them is the RRF
rabbit hole again, for a UX question ("where does this live?") that
grouped presentation answers better and more cheaply.

## 3. Design

Unification lives in the CLIENT: one search view fans out to the three
endpoints in parallel and renders grouped, per-corpus-ranked sections
(Docs / Scripts / Notes). Each corpus keeps its own scorer and filters;
no cross-index score normalization. The only new backend surface is the
scripts wrapper.

- **S1 — `/api/scripts/search`.** Thin Flask route over
  `search_scripts()` (open the `memory/script_search.db` sidecar the same
  way the docs route opens `doc_search.db`). Params: `q` (required),
  `limit` (default 25, clamp 1..100), `offset`, `kind`
  (script\|test\|make\|mojo\|ts — the census found 97/164/61/19/17 rows;
  ts is the corpus_uniformity S6 footprint the proposal originally
  missed), `area`, `hybrid` (default on). Response
  mirrors the docs route: `{"query", "mode", "stale", "results":
  [{"path", "kind", "area", "purpose", "cli", "make", "snippet",
  "score"}]}`. Stale index answers with `stale: true` (CLI precedent:
  warn-and-answer); missing index → 503 like the notes route. Never 500s
  on a degraded sidecar.
- **S2 — unified search view.** *(landed 2026-09-12)* New
  `frontend/src/views/search.ts` SearchView (299 LOC) + router/nav/HTML
  wiring: one input (300 ms debounce, Enter commits), parallel fan-out to
  `/api/docs/search`, `/api/scripts/search`, `/api/search` via
  `Promise.allSettled`; three grouped sections. Per-section filter
  passthrough: kind chips for scripts (all/script/test/make/mojo/ts,
  re-run on click). Zero-result sections collapse to a one-line "no hits
  in <corpus>". Every leg renders independently — a rejected leg becomes
  an inline error line (503s append the rebuild command), never a blank
  page. A result's snippet renders through the existing markdown
  pipeline — `highlightSnippet()` for docs/notes (`<mark>`-preserving
  escape), sugar-high `highlightCode()` for scripts hits so code-adjacent
  hits (CLI flags, SQL, make wiring) get token coloring. Language
  mapping: fence lang from the path's extension; `.mojo` → **python**
  approximation (Mojo is Python-shaped so strings/comments/def bodies
  color correctly while `fn`/`struct`/`owned` fall to neutral identifier
  color) labeled "mojo≈python" so nobody trusts keyword-level accuracy;
  unknown/make-target paths → plaintext. Stale docs/scripts indexes
  surface a per-section "index stale — rebuild via …" line.
- **S3 — verification + gates.** *(landed 2026-09-12)* curl contract
  tests for the new route (fresh + stale + missing sidecar + empty q →
  400; hermetic equivalents in `tests/test_api_scripts_search.py`);
  view smoke through `bun run build && bun run typecheck`; and — folded
  in after the decode-class audit — a **blocking static check**,
  `check_embedding_decode_chokepoint` in `helpers/validators/
  static_checks.py` (AST scan of helpers/ + app.py: `json.loads(<expr>)`
  on an emb/vec-named receiver is a failure unless the expr is a
  file-read shape; vec_codec + the two blob migrations are allowlisted;
  opaque receiver names (`e`, `row[4]`) stay covered by the BLOB-seeded
  behavior tests — the gap is documented in the check docstring).
  Registered as "Embedding decode chokepoint" in the CHECKS table, so it
  runs inside `make static-checks` / `make qa` from now on. `make qa` at
  arc end with user go.

S1 is independently landable (the CLI remains the agent surface; the
endpoint adds the browser). S2 depends on S1. S3 gates the arc.

## 4. Acceptance criteria & shakedown

1. `curl -s 'http://127.0.0.1:<port>/api/scripts/search?q=integrity' |
   jq '.results | length'` ≥ 1, first hit path startswith `helpers/`.
2. `curl -s '.../api/scripts/search?q=integrity&kind=make'` filters to
   Makefile targets only; `kind=mojo` returns Mojo modules.
3. Empty `q` → 400; `stale: true` served after a tree edit without
   rebuild (temp-edit a helper docstring, query, restore); missing
   sidecar → 503 with the rebuild command in the body.
4. Unified view: a query hitting all three corpora (e.g. "embeddings")
   shows three populated groups in one render; a corpus with zero hits
   collapses without layout breakage; snippets show `sh__token--*` spans
   for python/ts/sql hits and neutral text for the mojo→python blocks'
   non-Python keywords.
5. `make qa` green once at arc end, with user go.

| Projected outcome | Today | After |
|---|---|---|
| Searchable surfaces in UI | 2 of 3 (notes, docs) | 3 of 3 |
| Entry points for script intent search | CLI only | CLI + UI (same core) |
| New backend code | — | ~60–100 LOC route + tests |
| Bundle delta | 598,778 B findata.bundle.js | 608,226 B (+9,448 B / +1.6%, view code only — highlighter adds 0, already shipped) |

## 5. Risks

- **Repo-internal surface exposed over HTTP** — script hits reveal
  helpers/ layout and CLI flags. The app is localhost/vault-local (same
  exposure class as `/api/docs/search`, which already serves doc/ incl.
  procedures); no new auth posture introduced. Mitigation: none needed
  beyond keeping the route bound to the existing app; noted for any
  future external deployment.
- **Sidecar staleness UX** — a stale script index silently answers with
  old purpose lines. Mitigation: surface `stale: true` in the UI group
  header ("index stale — rebuild via helpers/maintenance/
  rebuild_script_search.py"), mirroring the docs route's degrade
  contract.
- **Notes endpoint shape drift** — `/api/search` is polymorphic and
  FTS5-syntax-sensitive (400 on malformed q). Mitigation: the client
  wraps every fan-out leg in its own try/catch and FTS-quotes tokens the
  way the docs route does, so one leg failing never blanks the page.
- **mojo→python highlighting misleads** — mitigated by the
  "mojo≈python" label on such blocks and the neutral-color behavior of
  unmatched keywords.

## 6. Non-goals

- No server-side cross-corpus score merge or single blended ranking.
- No changes to `/api/search` or `/api/docs/search` internals (S2
  consumes them as-is).
- No ripwire surface in the UI — script_search is the INTENT layer;
  symbol/caller questions stay with ripwire (AGENTS.md posture).
- No re-indexing, schema, or embedder changes — `search_scripts()` is
  consumed exactly as the CLI consumes it.
- No web-llm/"ask the vault" natural-language lane (deferred per
  `../../local/evaluations/web_components_assessment.md`; needs its own
  proposal).

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-12 | `rg -n "def search_scripts" helpers/maintenance/rebuild_script_search.py` | :1561, `(conn, q, limit=25, offset=0, *, kind=None, area=None, hybrid=True)` | core already hybrid + filterable |
| 2026-09-12 | `rg -n "route" app.py \| grep -i search` | `/api/search` :759, `/api/docs/search` :1161 | no scripts route exists |
| 2026-09-12 | `ls frontend/node_modules/sugar-high/lib/lang/` | 28 grammars, no mojo | typescript/javascript/sql/yaml/toml/shell/markdown/diff present |
| 2026-09-12 | demo page `/tmp/sugar_high_demo.html` (served, user-verified) | python/sql/yaml/ts tokenize; unknown fence → plaintext neutral | call path identical to `highlightCode()` |
| 2026-09-12 | `script_query.py` docstring | "an eventual /api/scripts/search" wrapping `search_scripts` | pre-anticipated design |
| 2026-09-12 | live route check (Flask test_client, live sidecar) | `q=integrity` → 200, mode **hybrid**, stale true (app.py/rss edited post-rebuild — warn-and-answer), 25 hits; top: database_integrity_check.py / make integration / its test; `kind=make` → 4 hits all make; `limit=2&offset=2` → 2; empty q → 400 | S1 acceptance 1/2 met |
| 2026-09-12 | `pytest tests/test_api_scripts_search.py` (new; hermetic tmp tree + fake 8-dim embedder) | 20 passed; `tests/test_script_query.py` + `tests/test_rebuild_script_search.py` → 27 passed (cosine fix regression-clean) | S1 landed: route + tests |
| 2026-09-12 | `bun run typecheck && bun run build` (frontend) | tsc clean; bundle 598,778 → 608,226 B (+9,448 B); search.ts 299 LOC | S2 build smoke |
| 2026-09-12 | `pytest tests/test_integration_ts_contract.py` (+2 new: ScriptSearchResponse/Hit + 400 ErrorResponse) | 35 passed (33 prior + 2) | api.ts ↔ route reverse contract |
| 2026-09-12 | page smoke (Flask test_client GET /) | nav `data-view="search"` + `#search-view`/`#unified-search-input`/`#unified-results`/`#unified-kind-chips` all present; 6 view sections | served template check |
| 2026-09-12 | `pytest tests/test_api_search.py` (+2: BLOB-seeded fallback + KNN regression pins, `_seed_blob_db` fixture) | 21 passed — Chatter ([0,1,0]) hits cosine 1.0 vs the pinned [0,1,0] query on a BLOB index; `tests/test_rebuild_note_search.py` + `test_embedding_blob_migration.py` + `test_api_scripts_search.py` → 65 passed | decode-class fixes regression-clean |
| 2026-09-12 | `pytest tests/test_static_checks.py` (+7: chokepoint flag/pass/allowlist/scope) | 93 passed; live `static_checks.py` run → "✓ Embedding decode chokepoint", All static checks passed (1 pre-existing OKF advisory) | S3 chokepoint check landed, tree is clean under it |
| 2026-09-12 | stray-file discovery via the new gate run | the 0-byte `doc/improvements/proposals/graph_rendering_overhaul.md` (leftover from the b60adca7 archival) failed Frontmatter schema + Proposal lifecycle — removed | the gate caught a real defect on its first run |
