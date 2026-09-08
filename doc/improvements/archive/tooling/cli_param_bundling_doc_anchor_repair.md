---
title: "CLI param bundling + live doc-anchor repair + relation harvest — #216 follow-up, doc-drift sweep, expanded-corpus triage"
status: executed
filed: "2026-09-08"
executed: "2026-09-09"
completed_md: "217"
area: "helpers/maintenance, helpers/graph"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# CLI param bundling + live doc-anchor repair + relation harvest — #216 follow-up, doc-drift sweep, expanded-corpus triage

**Date:** 2026-09-08 · rev 2026-09-09 · **Status:** EXECUTED ·
**Area:** helpers/maintenance (`rebuild_common.py`, `derive_cli.py`, `local_embedder.py`, `verify_notes.py`) + live doc anchors + helpers/graph (relation triage)

## 1. Motivation

The #216 arc (`archive/tooling/code_duplication_consolidation.md`,
completed.md #216) closed its quality-delta gate (28 → 0) by acking
49 findings into
`.ripwire_quality_acks` — and deliberately left **8 new-symbol rows
visible as real debt** rather than acking them: the parameterization
surface the arc chose over flag growth (proposal §Execution Results).
Separately, `ripwire . --doc-drift` reports 68 failing anchors, but
almost all sit in archive records; the **selected repair surface** is
7 anchors across 5 docs — docs still cited as authority from the live
surface (AGENTS.md → ripwire_adoption, markdown_parse.md →
quote_capture_coverage), this arc's own records (code_duplication,
archived with #216), and the archive index itself (archive/README.md).
A third slice harvests the expanded corpus (#215: ~85 new
company notes, 751-note re-render, Quotes.md catch-all): the relation
queues are empty today, so a fresh derive → suggest → triage pass converts
the new chatter into edges. Three small slices, no behavior change to
machinery.

## 2. Evidence (measured 2026-09-08, this box, main @886b693a)

| Check | Result | Verdict |
|---|---|---|
| `ripwire . --quality-delta` | regressions 0, gating 0, new-symbol 0 vs HEAD | committed debt, not a regression — safe to schedule |
| #216 proposal §Execution Results | 8 new-symbol rows named (params/verbosity) | target list, do not re-audit |
| `ripwire . --doc-drift` (full split, re-run 2026-09-09) | 68 total: selected surface 7 (pending 1, archive/README 1, ripwire_adoption 1, quote_capture 2, code_duplication 2); rest in archive records — leaders 27/12, mid-tier 6/5/4/3, singles ×3 | repair the 7, accept the rest |
| `ripwire . --callers=run_query_cli` | 2 callers (`doc_query.py:57`, `script_query.py:65`) | bundling seam confirmed |
| `ripwire . --callers=run_rebuild_cli` | 3 callers (`rebuild_doc_search.py:917`, `rebuild_note_search.py:1001`, `rebuild_script_search.py:1625`) | bundling seam confirmed |
| quote funnel re-run 2026-09-08 | 100.0% corpus, G1–G5 0, watchlist 0 open / 92 closed | unrelated — stays shut |
| `triage_pending_relations.py` + `triage_pending_quotes.py` 2026-09-08 | 0 suggested / 0 prose / 0 open quote canonicals; worklist 796 total, 0 open | queues empty because the funnel has NOT run since the corpus changed (see sidecar row) — every fresh row is corpus-new |
| #215 completed.md entry | ~85 new company notes, 751-note re-render, Quotes.md 10th super-sector | harvest fuel; rule-based pass only |
| sidecar mtimes vs corpus (2026-09-09) | `_pending_suggestions.txt` 2026-08-27, `_pending_relations.txt` 2026-08-31; 1,033 findata .md newer | funnel pre-dates the expansion — stale gate expected to trip; queue unbounded until measured |

The 8 rows (§Execution Results): `run_query_cli` (14 params, 67 LOC over
the 60 bar), `run_rebuild_cli` (12), `add_derive_args` /
`backup_last_good_index` / `build_rebuild_parser` / `run_pinned_pool` /
`_check_title_unquoted` (6–7). Live drift anchors: pending.md:8
`pragma_hnsw_index_info` undefined; ripwire_adoption.md:120 `VssRunIndex`
undefined; quote_capture_coverage.md:36 range-straddles
(`derive_insights.py:467-477` → `_PROSE_START` @477) and :98 line-moved
(`iter_company_sections` 431 → file scope :600); code_duplication
:86 past-eof (`liteparse_post.py:85`, now 47 lines) and :115 line-moved
(`local_embedder.py:268` `embed_documents_parallel` → file scope :309).

## 3. Design

S1 first (code), S2 after (docs), S3 last (corpus) — independent, landable separately.

- **S1 — param bundling.** Per-index config dataclasses for the 7 symbols
  (`rebuild_common.py`: `build_rebuild_parser`, `backup_last_good_index`,
  `run_rebuild_cli`, `run_query_cli`; `derive_cli.py`: `add_derive_args`;
  `local_embedder.py`: `run_pinned_pool`; `verify_notes.py`:
  `_check_title_unquoted`; line refs as of filing — S1's own edits move
  them). Callers pass one config object; per-index
  callables stay as fields (the shape that genuinely differs). License:
  dataclasses are reserved for the two orchestrators (`run_query_cli`,
  `run_rebuild_cli`); the 6–7-param tier may instead close by plain
  param reduction where two params are one concern — e.g.
  `_check_title_unquoted`'s `severity` + `issue_type` are correlated
  variants and can collapse to a single literal — same gate outcome
  either way (a 7-field config has the same surface as 7 params, plus
  construct ceremony at 2–3 call sites). Rejected:
  acking the rows (hides real debt), splitting the functions (the params
  are one concern — CLI surface — not two), adding flags (the growth #216
  avoided).
- **S2 — anchor repair on the selected surface.** Criterion: docs still
  cited as authority from the live surface (AGENTS.md →
  ripwire_adoption.md; markdown_parse.md → quote_capture_coverage.md),
  this arc's own records (code_duplication_consolidation.md), and the
  archive index itself (archive/README.md) get repairs; all other
  archive/ drift is accepted rot — records, not live docs (leaders
  27/12, mid-tier 6/5/4/3, singles ×3). 6 re-points + 1 annotation:
  ripwire_adoption.md:120 → `_VssRunIndex` (the doc names the
  pre-rename un-underscored spelling; + `build_vss_run_index` refs);
  archive/README.md:94 → reword the #201 topic line (it backticks
  `_connect_ro`, folded in #201 — name the surviving `db.connect`
  shape); quote_capture :36/:98 and code_duplication :86/:115 line
  re-points, preferring symbol-only refs where the lane supports them
  (rot resistance); `pragma_hnsw_index_info` annotated as intentional
  (deferred N5 item naming a nonexistent macro — undefined is correct).
  Measured against the post-S1 tree — S1 moves lines, so S2 re-points
  are taken AFTER S1 lands, never before.
- **S3 — relation harvest on the expanded corpus.** Fresh operational
  pass, no machinery change. Step 0 captures the denominator: run the
  derive-relations stale gate and record its output in the run log
  (sidecars pre-date the #215 expansion, so the gate is expected to
  trip and the fresh queue is unbounded until measured). Then:
  `make derive-relations` (wraps `extract_relations.py --apply`) →
  `python3 helpers/graph/suggest_relations.py --append` (the make
  target forwards no args) → `make triage-relations` → annotate
  decisions (`accept:<edge_type>[:<target>]` / `alias:<target>` /
  `stub` / `discard` per the #169 schema) → `python3
  helpers/graph/triage_pending_relations.py --apply-decisions`
  (batch the annotations if the queue is large) → roster sync +
  `make graph-rebuild`. New aliases persist to
  `findata/Misc/relation_aliases.json` so recurring mentions resolve
  instead of refilling the queue (the #169 lesson: sidecar rows are
  append-only between triages — unresolved mentions re-enter every
  full-corpus extract unless aliased, stubbed, or noise-gated). Triage
  artifacts (`_pending_triage_report.md`, decisions JSONL, suggestions
  store) are gitignored operator-private review surface — decisions are
  recorded by re-running, not committed. Rejected: B2 sidecars in this arc
  (revisit only if accepted edges become annoying to audit in
  `graph_edges` alone); any change to extractor patterns or predictor
  thresholds (rule-candidates wait for recurrence).

## 4. Acceptance criteria & shakedown

1. `ripwire . --quality-delta` — gating 0 AND new-symbol params/verbosity rows for the 7 symbols gone (exit 0, no new acks for these rows).
2. `ripwire . --doc-drift` — the 6 re-pointed anchors clean (incl. archive/README) and the pre-rev self-referential row gone; `pending.md:8` remains undefined-by-intent with its annotation.
3. S3: stale-gate output captured at step 0 (the denominator); triage queues back to zero (`triage-relations` report: 0 suggested / 0 prose after `--apply-decisions`); accepted-edge count + new-alias count reported from the run (measured, not projected); `make graph-rebuild` clean.
4. `make qa` 9/9 + `make advisory` 10/10 on the final tree (full gates once, at the end, with user go); touched-suite tests green (rebuild ×3, doc/script query, derive CLI, local_embedder, verify_notes).

| Projected outcome | Today | After |
|---|---|---|
| #216 new-symbol param/verbosity rows | 8 visible | 0 |
| selected drift surface (5 docs) | 7 (6 rot + 1 intentional) | 0 rot + 1 annotated |
| relation queues | 0 / 0 (empty baseline) | 0 / 0 after harvest; accepted edges + aliases = measured delta |

## 5. Risks

- **Dataclass over-abstraction** — a wrong shared config beats a low score; mitigation: dataclasses only for the two orchestrators, param-reduction license for the 6–7 tier, callables stay fields.
- **Caller churn in tests** — `default_db` zero-arg-callable contract and module-global passthrough (`BACKUP_DIR`, `_pseudo_warned`) must hold; mitigation: keep constructor signatures backward-compatible where tests monkeypatch, run the 22 touched suites from #216.
- **Drift re-rot** — line anchors rot again on next edit; mitigation: prefer symbol-only refs where the lane supports them, no archive edits.
- **Harvest re-entry** — accepted/discarded rows refill the queue on the next full-corpus extract unless aliased, stubbed, or noise-gated; mitigation: persist aliases in the same pass, stub genuinely-missing entities, triage promptly after each derive run (future_items §D/H1).

## 6. Non-goals

- Archive doc-drift outside the selected surface (the 27 + 12 + mid-tier 6/5/4/3 + singles ×3) — dated records, not live rot.
- Other derive passes (themes / events / co_mentions / cited_in) — deterministic direct-apply riding maint-full/graph-rebuild; the suggest → triage queue machinery is relations (+ quotes) only.
- `_backup` (research.db) vs `_sqlite_zstd_backup` merge — accepted residual in #216 S7 (open-handle vs open-by-path safety contract).
- Any behavior change to CLI flags, defaults, or output wording.
- H1/H3 coverage (item 2 in the planning discussion): typed-edge LLM extraction (budget decision) and the sector-wikilink pass — separate arcs.

## Execution Results (2026-09-09)

All three slices executed in one session, S1 → S2 → S3, gates deferred to
the end per house rule. Every acceptance criterion below is measured on
this tree.

**S1 — param bundling (criterion 1: MET).** Five spec dataclasses
(`RebuildCliSpec`, `QueryCliSpec`, `IndexBackupSpec`, `DeriveArgsSpec`,
`PinnedPoolSpec`) carry the per-index config; `run_query_cli` 14→2
params, `run_rebuild_cli` 12→2, `backup_last_good_index` 5→2,
`add_derive_args` 5→2, `run_pinned_pool` 5→3. One symbol closed by
param reduction past the proposal's "single literal" suggestion:
`_check_title_unquoted` dropped BOTH variant params by deriving the
severity from the note's own `type` field (the dispatcher's own
discriminator — 4 params, no flag at call sites). Global-derived spec
fields (`backup_dir`, copier) are constructed inside the wrappers at
call time, preserving the monkeypatch contract (documented in
rebuild_common's module docstring). Derive specs hoisted to module-level
constants (static strings) so `_cli` LOC did not grow. Measured:
`--quality-delta` regressions 0 / gating 0 / new-symbol 0 / exit 0;
lenient `--quality-panel` full sweep (2,955 rows) — all 7 symbols AND
all 5 new specs under every absolute bar. 15 `short-horizon-churn` rows
fired (S1 deliberately re-touches #216's symbols one day later — the
kind needs committed thrash + this diff) and were acked with that
reason: the ONLY new acks, none for params/verbosity rows (fixed, not
acked). Fixture note: `test_quoted_title_warning` now passes
`type: company` (the variant key it was implicitly relying on).
Tooling discovery recorded: ripwire DELETES a `.ripwire_quality_baseline`
pinned at any sha ≠ current HEAD before falling back to git-HEAD — the
pre-scaffold floor of #216 is unrecoverable by design, so this arc's
floor was pinned fresh at HEAD on a clean tree and closure is proven by
the absolute-bar panel sweep, not by delta-vs-#216-floor.

**S2 — anchor repair (criterion 2: MET, scope +2).** The 6 planned
re-points + 1 annotation landed; symbol-only refs preferred where the
lane supports them (quote_capture ×2, code_duplication ×2, archive/README
topic line, ripwire_adoption rename note; pending.md carries the
undefined-by-intent annotation and stays as the one remaining row). Two
rows discovered mid-arc and fixed in the same pass: this proposal's own
§3 S1 line cites (moved by S1 itself — re-worded symbol-only) and
`doc/design/db_schema.md` `deal_value` (a `metric_label` enum value no
extractor emits — removed from the live schema doc). Post-repair
doc-drift on the selected surface: 0 rot rows; 1 annotated
(pending.md); the proposal's own dated evidence rows remain by design.

**S3 — relation harvest (criterion 3: MET).** Denominator (dry-run,
step 0): 114 files, 104 extracted edges (24 acquired / 2 competes_with /
1 customer_of / 47 jv_with / 24 subsidiary_of / 6 supplier_to), 12
unresolved, 7 suppressed, 2 ambiguous. Apply pass: 2 new edges
idempotent-applied. Suggestion pass: 4 appended (of 25, rest deduped).
Triage: 12 prose rows → 2 `stub` (Circle, American Express — real
companies with explicit partnership quotes, absent from DB; both
alias_candidate hints were word-overlap false positives and were
rejected, 0 aliases persisted) + 10 `discard` (noise: prose fragments,
places, projects, anonymous references). Stubs created via
`parse_newsletter.create_entity` (entity row + part_of/has_company
membership edges + note; hand-written richer notes never clobbered) —
re-extract then landed both `jv_with` edges (47→49), companies
1163→1165. The #169 re-entry lesson observed live: the 10 discarded
noise rows re-entered on the very next extract (12 prose − 10 dupes =
2 stale + 10 re-entered); re-discarded with notes; queue now
0 suggested / 0 prose, `make graph-rebuild` clean. One stub-teaching
detail: `verify_notes` name_sync caught a normalized_name/filename
mismatch in the hand-written American_Express stub (fixed; the DB row
was already correct via `normalize_name`).

**Criterion 4** — MET, both gates green on the FIRST run, nothing to
fix: `make qa` 9/9 (lint, md-lint, types, deptry, static_checks,
pytest 2,646 passed + 3 skipped in 111.9s, verify_notes, integrity,
snapshot_check) and `make advisory` 10/10 (ty-tests, live-invariants
218 passed, frontend-check, graph-algos, analytics, suggest-relations,
doc/script/note-search-check, lint-audit). Touched-suite pre-verification
during the slices (282 + 93 + 82 + 66) predicted it — zero gate-driven
fixups this arc, versus four at #216.

| Outcome | Projected | Actual |
|---|---|---|
| #216 params/verbosity rows | 0 | 0 (7 symbols + 5 new specs under every panel bar) |
| drift on selected surface | 0 rot + 1 annotated | 0 rot + 1 annotated (scope +2 rows found and fixed) |
| relation queues after harvest | 0 / 0 | 0 / 0; edges +4 (2 jv_with + 2 via stubs) + 4 membership; aliases +0 |
| quality-delta | exit 0 | regressions 0 / gating 0 / new-symbol 0, exit 0 |

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-08 | `ripwire . --quality-delta` | regressions 0, gating 0, new-symbol 0, at 886b693ab | HEAD-clean; debt is committed, not a delta |
| 2026-09-08 | `ripwire . --callers=run_query_cli` / `=run_rebuild_cli` | 2 / 3 callers | seams above |
| 2026-09-08 | `ripwire . --doc-drift` + filter live docs | 6 anchors in 4 live docs; archive leaders 27/12 untouched | scope above |
| 2026-09-08 | `helpers/graph/triage_pending_relations.py` + `triage_pending_quotes.py` | 0 suggested / 0 prose / 0 open canonicals | empty baseline for S3 |
| 2026-09-08 | `helpers/validators/quote_coverage_audit.py` | 100.0%, G1–G5 0, watchlist 0/92 | confirms no quote work leaks into this arc |
| 2026-09-09 | sidecar mtimes vs `find findata -newer` | suggestions 2026-08-27 / relations 2026-08-31; 1,033 newer | S3 has real work; queue measured at run time |
| 2026-09-09 | `ripwire . --doc-drift` re-run | 68 total; full split in §2 | selected surface 7 across 5 docs (incl. archive/README `_connect_ro`) |
| 2026-09-09 | rg re-verification of symbol/line refs | all 7 S1 symbols at cited lines; all S2 anchor claims reproduce | evidence current at rev time |
| 2026-09-09 | `--quality-baseline` re-pin attempts | sidecar pinned at parent 902b2f48 DELETED by delta run (sha≠HEAD self-heal) | floor must be pinned at HEAD, clean tree |
| 2026-09-09 | post-S1 `--quality-delta` | first pass 15 churn + 4 verbosity minors → module-constant derive specs + walrus/`type`-derivation fixes → 0/0/0 exit 0 | churn 15 acked (only new acks) |
| 2026-09-09 | lenient `--quality-panel` sweep | 2,955 rows, all 2955 scanned: 7 symbols + 5 specs absent except pre-fix `_check_title_unquoted` params=6 | absolute-bar proof |
| 2026-09-09 | S3 harvest | dry-run 114 files/104 edges/12 unresolved; apply +2 edges; triage 12 rows (2 stub/10 discard); stubs Circle+American_Express; re-extract jv_with 47→49; queue 0/0; graph-rebuild ✓ | measured deltas above |
| 2026-09-09 | post-S3 verify_notes + snapshot + search-fresh | 1,217 files: 1 name_sync error (Amex stub normalized_name) → fixed → 0/0; snapshot refreshed; indexes converged | final tree state |
