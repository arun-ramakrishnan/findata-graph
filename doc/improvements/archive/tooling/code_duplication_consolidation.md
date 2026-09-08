---
title: "Code duplication consolidation — ripwire --clones map to shared scaffolds"
status: executed
filed: "2026-09-08"
executed: "2026-09-08"
completed_md: "216"
area: "helpers/maintenance/rebuild_*_search.py, helpers/pdf/, helpers/validators/verify_notes.py, helpers/graph/derive_* CLI scaffold, helpers/misc/*_query.py, helpers/core/local_embedder.py + helpers/bench/, app.py, helpers/maintenance/db_maint.py + snapshot_db.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# Code duplication consolidation — ripwire clones map to shared scaffolds

**Date:** 2026-09-08 · **Status:** EXECUTED · **Mode:** seven slices,
ordered smallest-risk first; each slice lands with its own targeted tests.
**Area:** `helpers/maintenance/rebuild_{doc,note,script}_search.py` ·
`helpers/pdf/{pdf_local,liteparse_post}.py` ·
`helpers/validators/verify_notes.py` · `helpers/graph/derive_*.py` ·
`helpers/misc/{doc_query,script_query}.py` ·
`helpers/core/local_embedder.py` + `helpers/bench/embed_pool_probe.py` ·
`app.py` · `helpers/maintenance/{db_maint,snapshot_db}.py` · `Makefile`
(advisory target only if any).

## 0. Context and method

User directive (2026-09-08): use ripwire to find duplicated code segments
that can be consolidated. This followed the `make types` gate repair
(two `redundant-condition` warnings on the `seen.add()` dedup idiom —
pre-existing debt, both fixed), which surfaced that gate surfaces and
clone-class debt are related maintenance hazards.

Method (ripgwire-only per the discovery-tool rules; one command, no
hand-grepped census):

```bash
ripwire . --clones --limit=2000      # full row dump, then post-filter
```

Filter policy applied before ranking (the raw report is dominated by
two noise classes):

1. **Build artifacts excluded** — every `frontend/src/**` function pairs
   with its own compiled copy in `static/*.bundle.js` (and the bundles
   pair with each other: webpack runtime boilerplate). A bundle equals
   its source by design; consolidating "across" it is meaningless. This
   removes the majority of the 1,350 raw groups.
2. **Test-file near-dups deprioritized** — the bulk of the remainder is
   test harness boilerplate (per-file env fixtures, response-shape
   asserts). Ripwire's own quality-delta doctrine exempts fixture dirs:
   harness repetition is convention, and forcing shared test scaffolds
   often costs more readability than it saves LOC.

**Baseline numbers (2026-09-08, recorded for the close-out delta):**

| Metric | Value |
|---|---|
| Raw groups / Type-3 rows | 158 / 1,192 |
| Clone clusters (union-find) | 488 |
| Duplicated LOC / total LOC | 15,261 / 102,050 = **15.0%** |
| Production-only groups (bundles+tests out) | 131 |
| Production-only clusters | ~71 |
| Largest 12 clusters, duplicated tokens | ~3,099 |

Pre-execution step: pin `ripwire . --quality-baseline` on a clean tree
so every slice self-checks with `--quality-delta` (dup kind must never
regress; the point of this arc is that it only shrinks).

## 1. Findings

**F1 — `rebuild_search` trio shares an entire scaffold (largest
family).** `rebuild_doc_search.py` / `rebuild_note_search.py` /
`rebuild_script_search.py` duplicate: `main` (T2 253 tok doc↔script;
T3 0.88/292 doc↔note) — the `--db/--check/--incremental` argparse +
freshness flow; `_print_staleness` × 3 (T2 160, identical);
`resolve_embedder` × 2 (T2 86, doc↔note); `_backup_last_good_index` × 2
(T3 0.87); `_mojo_stored_doc` vs `_ts_stored_doc` in one file (T2 90 —
language is the only parameter). No shared module exists today
(`rebuild_schema.py` is a different concern). The three scripts are
load-bearing in `maint --full` and the search-fresh chain.

**F2 — PDF running-header filter is a documented manual-sync copy.**
`_filter_running_headers` was 0.99-identical in `pdf_local.py` and the
pre-consolidation `liteparse_post.py` (S2 below shrank it to a 47-line
re-export), and the three module regexes
(`PAGE_NUM_RE`, `DATE_RE`, `HEADING_LINE_RE`) were independently defined
in both. Pre-consolidation `liteparse_post.py:17` said `# Copied from pdf_local.py (keep
in sync)` — the drift contract is enforced by a comment. Confirmed
byte-equivalent logic; only docstrings differ.

**F3 — `verify_notes` YAML-check family.**
`check_sector_yaml_consistency` vs `check_super_sector_yaml_consistency`
(0.99, 221 tok): the only deltas are the permalink prefix
(`/sectors/` vs `/super_sectors/`) and the expected-field set.
`check_company_yaml_consistency` grew the same shape more complex and
now carries `# noqa: C901`. Four members total
(`check_yaml_structure` generic + 3 typed).

**F4 — `derive_*` CLI scaffold (biggest single pair).** `_cli` in
`derive_cited_in.py:290` vs `derive_themes.py:378` (476 tok @ 0.81):
same `--apply/--verbose/--corpus/--stale-only` argparse, same stale-gate
block, same dry-run report shape. ≥4 of the 5 derive scripts carry it
(co_mentions, events, cited_in, themes). Drift already exists: cited_in
adds `--vault`, stale-gate wording differs.

**F5 — query CLIs duplicate their mains.** `doc_query.py:49` vs
`script_query.py:54` (413 tok @ 0.88): `query/--limit/--db/--bm25/--json`
argparse, stale-index warning, hit-render loop. Both already import
their rebuild modules (`rds` / `rss`), so a shared core has a natural
home.

**F6 — embed pool probe mirrors production (measurement-integrity
risk).** `embed_pool_probe.py` (`pool`) vs
`local_embedder.py` (`embed_documents_parallel`) — 0.91, 236 tok.
The probe imports the module (line 170) but re-implements the pool loop
("Mirror local_embedder._pool_init", line 86). A probe measuring a
mirrored implementation silently stops measuring production when
production changes (chunking, ordering, pinning). Lesson source: the
bench-epistemics doctrine (single-variable comparisons need the measured
thing to BE the thing).

**F7 — flat_knn top_k is a deliberate A/B, NOT a copy (correction of
the initial read).** `Mojo/bench/flat_knn.py:73` vs
`helpers/core/embed_matrix.py:93` (0.80) looked like a bench
reimplementing production. In fact flat_knn's header pins the design:
the bench runs a Mojo-kernel scoring core (`self._model.execute`)
against production numpy, importing `EmbedMatrix` for the harness. The
shared ~15 lines are the argpartition/normalize/assemble scaffold around
two intentionally different cores. No consolidation — documented here so
the clone row is explained, not "fixed".

**F8 — small parameterizable pairs.**
(a) `db_maint.py` `_backup_embed_store` vs `_backup_corpus` (0.88, 238
tok) — same zstd backup flow, different path/label.
(b) `snapshot_db.py` `_verify_parquet_duckdb_side` vs
`_verify_parquet_sqlite_side` (0.87, 168) — dialect is the parameter.
(c) `app.py`: `_resolve_entity_with_type_or_404` (0.96 with
`_resolve_entity_or_404` — should delegate + assert);
`_theme_neighbors_bundle` vs `_super_sector_neighbors_bundle` (T2 85);
and the `api_graph_{semantic,similar,edition_companies,near_duplicates}`
response-envelope family (four-way T3 ~0.8x, gid 7 cluster).

**F9 — false-positive classes recorded and excluded:** `onager_pagerank`
vs `onager_clustering` (same-shaped signatures, genuinely different
algorithms); `frontmatter.stringify_dates` vs
`frontmatter_schema._normalize_nested` (similar token stream, different
contracts — would over-abstract); `database_integrity_check`
`check_quotes` vs `check_company_metrics` (117 tok, same shape —
candidate for F8-style parameterization only if trivial).

## 2. Principles

- **No behavior change without a test pinning the old behavior first.**
  Every consolidation slice starts by writing/pinning the test that
  fails if the merge drifts (the rebuild trio's `--check` exit-code
  semantics differ — doc exits 1 on stale, note does not; those
  differences are load-bearing for the search-fresh gate).
- **Parameterize by data, not by flags** — permalink prefixes, DB
  paths, labels, dialects become arguments; behavior switches do not
  grow new flag surface.
- **One owner per body.** Where a "keep in sync" comment exists (F2),
  the comment is deleted in the same slice that replaces it with an
  import — a sync comment plus a shared module is worse than either.
- **The probe must call what it measures** (F6): extend the production
  function with an optional instrumentation hook rather than forking
  the loop again.
- **Explain-don't-fix for deliberate divergence** (F7): the clone row
  gets a code comment naming the A/B intent so the next audit skips it.

## 3. Slices

**S1 — PDF header-filter single-owner** (F2; ~30 min, zero risk).
Move `_filter_running_headers` + the three regexes to
`helpers/pdf/pdf_local.py` as the canonical owner (it is the primary
engine); `liteparse_post.py` imports them and drops its copies +
the keep-in-sync comment. Test: existing PDF post-processing tests
plus one new unit asserting both call sites behave identically on a
fixture with running headers/dates/dup URLs.

**S2 — verify_notes typed-YAML parameterization** (F3). Extract
`_check_yaml_fields(file_path, data, yaml_content, *, permalink_prefix,
expected_fields)`; sector/super_sector calls shrink to data. Company
variant adopts the helper for its shared parts (its C901 noqa should
shrink or vanish; do not force it — complexity that remains is real).
Tests: existing verify_notes suite green unchanged is the pin.

**S3 — rebuild_search shared scaffold** (F1; the big one). New
`helpers/maintenance/rebuild_common.py`:
`build_parser(default_db, check_help, incremental_help)`,
`print_staleness(...)`, `backup_last_good_index(...)`,
`resolve_embedder(...)`, `stored_doc_for(lang)` (the mojo/ts pair), and
a `run_rebuild_cli(rebuild_fn, *, check_stale_exit: bool)` shell. The
three scripts keep only their walker/extractor/rebuild core. Per script:
pin its `--check`/`--incremental` CLI contract with a test BEFORE the
swap (exit codes, output strings the gates grep). After all three
migrate: full `maint-full` pass + `make search-fresh` (both directions)
+ `make types` in-session.

**S4 — bench measures production** (F6; plus F7's explanatory
comment). Add an optional `on_batch: Callable[[int, int], None] | None`
progress hook to `embed_documents_parallel` (default None = current
behavior, byte-identical); `embed_pool_probe.pool` becomes a thin
driver that passes its meter instead of re-implementing the loop.
Re-run one probe leg and confirm numbers within noise of the recorded
pool results (they should move only by the hook overhead, ~0). F7:
comment on `flat_knn.top_k` naming the deliberate Mojo-vs-numpy A/B.

**S5 — query-CLI shared core** (F5). `helpers/misc/query_cli.py` (or a
module in `helpers/core/`): argparse builder + stale-warning +
render loop, parameterized by (index module, default filters). doc_query
and script_query keep their public `main()` signatures (Makefile/gates
call them). Tests: existing CLI tests + one golden-output test per CLI.

**S6 — derive_* CLI scaffold** (F4). After S3's pattern is proven:
shared `derive_cli.py` in `helpers/graph/` — parser scaffold
(`--apply/--verbose/--corpus/--stale-only[/--vault]`) + the stale-gate
runner + dry-run report shape. Migrate one script first (themes — the
0.81 twin of cited_in), then the rest. Each migration is
`--dry-run output byte-identical before/after` on a fixed fixture set.
Watch: maint-full PRE_FULL invokes cited_in/insights — run maint-full
after the cited_in migration.

**S7 — small parameterizations** (F8). (a) db_maint backup pair →
`_backup_store(path, label)`; (b) snapshot verify pair → dialect
parameter; (c) app.py: `_resolve_entity_with_type_or_404` delegates;
neighbors-bundle pair → one `_entity_neighbors_bundle(entity, kind)`
(or a thin wrapper where the SQL genuinely differs); envelope family →
`_graph_response(rows, ...)` helper if the four endpoints' envelopes
converge on inspection — stop where the response contracts genuinely
differ (API shape changes are user-visible).

Order: S1 → S2 → S3 → S4 → S7 → S5 → S6 (smallest risk first; the two
gate-adjacent slices S3/S6 land after the pattern is warm). Full gates
(`make qa` + `make advisory`) ONCE at the end, with the user's go.

## 4. Decisions taken (D1–D4)

- **D1:** Bundles (`static/*.bundle.js`) and `frontend/src`↔bundle pairs
  are excluded from scope — build outputs, not source duplication.
- **D2:** Test-file near-dups stay as-is this arc (convention; forcing
  shared test scaffolds is a readability loss at this corpus size).
- **D3:** `flat_knn.top_k` is documented as deliberate divergence (F7),
  not consolidated.
- **D4:** `onager_*` and the `stringify_dates`/`_normalize_nested` pairs
  are false positives — no action (F9).

## 5. Risks

- **R1 — gate-contract drift (highest):** the rebuild trio's CLI
  outputs/exit codes are consumed by `maint.py`, `Makefile`
  search-fresh checks, and static checks. Mitigation: per-script CLI
  contract tests pinned BEFORE migration; byte-identical `--check`
  output asserted.
- **R2 — API shape drift in app.py (S7c):** response envelopes are
  user-visible; consolidation stops where contracts differ.
- **R3 — import cycles:** pdf_local ↔ liteparse_post and
  rebuild_common ↔ rebuild_* must stay one-directional (owner →
  consumer); verified per slice with `make types` + import smoke.
- **R4 — probe-hook overhead contaminating the pool numbers (S4):** the
  hook is a no-op default; the re-run comparison proves ~0 delta before
  the probe switches over.
- **R5 — consolidation churn masking real diffs in review:** slices are
  separate commits' worth of changes; the user stages/commits per slice
  (house pattern), never one mega-diff.

## 6. Non-goals

- Frontend TS/JS source dedup beyond the bundle exclusion (no live
  duplication found in `frontend/src` itself beyond the pairs above).
- Test-harness refactor (D2).
- Any performance work — this arc moves code, changes no hot paths
  (S4's hook is measured to be free, not assumed).
- Mojo source dedup beyond the F7 comment (bench internals are
  legibility-first).

## 7. Definition of Done

- Every slice: targeted tests green, `ruff check` + `ruff format --check`
  clean, `make types` clean (the gate that started this arc), tree left
  dirty for the user.
- S3/S6 additionally: `maint-full` 14/14 + `make search-fresh` both
  directions in-session.
- End of arc: `ripwire . --clones` re-run — production-only clusters
  measurably down from the 131/71 baseline (delta table appended below),
  `--quality-delta` dup kind shows no regressions vs the pinned
  baseline; full gates with the user's go; archival ceremony
  (completed.md number, archive move, README pointer reset).

## 8. Rollback

Each slice is an independent import/extract refactor over a module
boundary — revert the slice's files and the previous shape returns; no
data, schema, or note-format changes anywhere in this arc. The rebuild
trio keeps its CLI contracts pinned by tests, so a revert cannot strand
a gate.

## Execution Results

*(2026-09-08 — all seven slices landed same-day; per-slice targeted checks; full gates pending the user's go)*

**Clone delta (ripwire --clones, corpus-wide):** dup_pct **15.0% → 14.5%**
(dup_loc 15,261 → 14,753 of ~102k; clone clusters 488 → 477);
production-only groups **131 → 119**. The remaining production clusters
are the documented exclusions (D1–D4) and the ≤26-token noise pairs acked
below.

| Slice | Result |
|---|---|
| S1 | `liteparse_post.py` 175 → 45 lines — the whole normalization block (7 regexes + 50-entry `SECTOR_PREFIXES` + 6 functions, AST-identical mod docstrings) is single-owned by `pdf_local.py`; `CAP_TAIL_RE`/`SECTOR_PREFIXES` re-exported for `liteparse_markdown`. New `tests/test_liteparse_post.py` pins single-ownership by IDENTITY (`lp.X is pl.X`) + behavior fixture. PDF suites 73 green. |
| S2 | Five `_check_*` primitives on `NotesVerifier`; sector/super_sector shrink to data; company adopts permalink+title (warning variant) and **loses its `# noqa: C901`**. Log messages byte-identical (66 verify_notes + 8 integration tests + live corpus run green). |
| S3 | New `helpers/maintenance/rebuild_common.py`: `build_rebuild_parser`, `print_rebuild_report`, `print_staleness`, `resolve_embedder` (warn-flag passed through so per-module `_pseudo_warned` monkeypatching keeps working), `backup_last_good_index` (copier param; BACKUP_DIR read at call time), `run_rebuild_cli`. Three `main()`s + `_print_staleness`×3 + `_backup_last_good_index`×2 + `resolve_embedder`×2 consolidated. 92 rebuild tests + live `--check` runs on all three; maint-full 14/14; ty clean. |
| S4 | `run_pinned_pool` in `local_embedder.py` (spawn ctx + core-pin queue + gather + fail-loud guard); probe's GGUF leg delegates (its initializer — the actual variable under test — stays). **Correction to F6:** the probe's bge leg already called production (`le.embed_documents_parallel`) — the clone was the GGUF plumbing only, no production hook needed. `flat_knn.top_k` carries the deliberate-A/B comment (F7/D3). 59 tests green. |
| S5 | `run_query_cli` in rebuild_common (read-side counterpart): argparse, ready-gate, stale-WARNING, JSON emission, no-hits/header shared; per-index callables + `render_hits` (doc `path:anchor` vs script `kind/area` rows). Both CLIs byte-compatible (16 tests). `Callable[..., X]` variance + `sqlite3.Connection` return typing per ty. |
| S6 | New `helpers/graph/derive_cli.py`: `add_derive_args`, `stale_gate` (returns `(skip, db_max)`; each script keeps its own skip-line wording), `dump_edges_verbose`. Migrated: cited_in, themes, events, co_mentions (args only — it has no gate body, pre-existing). Live `--stale-only` smokes correct; 76 derive tests; maint-full 14/14. |
| S7 | `db_maint._sqlite_zstd_backup` tail extracted (embed store + corpus callers). `app.py`: `_resolve_entity_or_404` delegates to the typed variant. **Inspected-and-rejected (R2):** snapshot verify pair (duckdb-vs-pyarrow counting differs per engine), api_graph envelope family + neighbors bundle trio (response contracts genuinely differ — Flask idiom, not logic). |

**Residual accepted, not fixed:** `_backup` (research.db) does NOT use
`_sqlite_zstd_backup` — it backs up from an already-open connection
handle (the safe mid-session form) vs the helper's open-by-path; forcing
it would change the safety contract (S7, weighed per ripwire's own "a
wrong abstraction beats a low score").

**Quality-delta ratchet:** gating findings 28 → **0** (exit 0); 49
findings acked into `.ripwire_quality_acks` with the arc rationale
(dead-code rows = first-class callable refs invisible to the static call
graph + pytest-collected tests; the `_backup` residual; ≤26-token noise
pairs; churn inherent to the edits). **8 new-symbol rows left visibly
un-acked as real debt:** `run_query_cli` (14 params, 67 LOC over the 60
bar), `run_rebuild_cli` (12), `add_derive_args`/`backup_last_good_index`/
`build_rebuild_parser`/`run_pinned_pool`/`_check_title_unquoted` (6-7) —
the parameterization surface this arc chose over flag growth or config
objects; a follow-up could bundle them into per-index config dataclasses
if the params bar starts gating.

**Combined regression:** 472 passed across all 22 touched suites.
**Full gates (2026-09-08, post-fix):** `make qa` 9/9 PASS, `make advisory`
10/10 PASS. First qa pass caught two pieces of arc debt (missing shebangs
on the two new library modules; snapshot one generation stale after a
post-maint search-fresh) and first advisory pass caught two more
(script-search stale fingerprints after the shebang edit; S608 on the
schema-constant `SELECT COUNT(*) FROM {table}` — noqa'd per the
snapshot_db convention). All four fixed, both gates re-run to green on
the final tree.
