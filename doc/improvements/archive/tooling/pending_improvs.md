# Pending Improvements — perpetual improvements tracker

STATUS: this file is the **perpetual** improvements tracker (operator
ruling 2026-10-05). It holds the standing pending-improvements backlog —
every category, including DB, Onager and the other gated items — and
stays open for new work. The original improvement bundles A–F (all DONE
or SKIPPED) are kept below as a dated historical section because ~45
note stubs and several doc/test files cite specific items by number
(e.g. "item #11", "Bundle C3, DONE"); those citations resolve here.

**Where tracking lives now:** the committed live-trigger list is
`doc/improvements/pending.md` (survey-by-survey findings + open
follow-ups). Unique slices of a backlog survey become separate
proposals under `doc/improvements/proposals/` (for example
`markdown_fold.md`, archived 2026-10-05 as completed.md entry 346, and
`graph_rebuild_fast_path.md` — rows 4/5/6/12 of the 2026-10-05 backlog
below).

## Historical — bundles A–F (closed; kept for item-number citations)

The closed bundles covered correctness, schema, and indexing fixes that
landed in the live DB and validators. Survey files scoped to a single
surface, each built on A–F:

- `../database/duckdb_improvs.md` — DuckDB engine surface (built on
  A–F + G–J)
- `../graph/graph_improvs.md` — graph algorithm coverage (G–J;
  candidates, not committed work)
- `../pipeline/findata_corpus_audit.md` — markdown ↔ SQLite ↔ DuckDB ↔
  graph coverage
- `../graph/hierarchy_design_roadmap.md` — forward-looking
  sector-hierarchy design

(`sqlite_improvs.txt` was a zero-byte stub removed long ago — see
completed.md; its subject lives in the DuckDB survey.)

If you arrived here from a "Bundle Xn" or "item #n" citation, that item
is closed; see the relevant sibling file above for the current state of
that area, or git log for the change.

## The 2026-10-05 backlog (recorded by improvs_backlog_record S1)

Status legend: **UNLOCK** = actionable now, **NO-ACTION** = trigger
checked and not fired, **CLOSED** = stop tracking, **DECIDE** = needs the
operator, **BLOCKED** = gated on an external/upstream condition,
**FILED** = slice of a filed proposal awaiting its execution go.

| # | Broad category | Item | State / trigger | Verdict | Evidence |
|---|---|---|---|---|---|
| 1 | Gate & stack | qa run 1225 (2026-10-05 08:16) | 5 legs red: md-lint, types, static_checks, pytest, integrity_check | **CLOSED** (re-verdicted 2026-10-05) — all 5 causes fixed and each red leg re-validated green **targeted** (md-lint / types / static-checks / the two pytest node-ids / integrity); per the gates protocol there is no confirming full re-run — the next full `make qa` is the next arc's baseline | md-lint 0 violations · `make types` "All checks passed" · `static_checks` ✓ · both pytest nodes pass · `database_integrity_check.py` rc=0, 0 cache errors · `outputs/qa_report.md` run 1225 |
| 2 | Gate & stack | 5 stack patches carry empty commit messages (`db_sync`, `chatter_process`, `jev_eval`, `system_one`, `bug_fixes`) | commit subjects/bodies blank | **CLOSED** (2026-10-05) — all four surviving patches messaged via `stg edit -f` (one per patch, stack order); `bug_fixes` was the gate-fix collector — emptied, deleted, name freed at close-out | `stg series -d` shows subjects on 4/4 |
| 3 | Gate & stack | advisory gate run in flight (started 2026-10-05 08:37) | ran 08:37:58–08:41:52 | **CLOSED** (2026-10-05) — collected: 7/12 baseline; live-invariants ×2 fixed (stale closeness/betweenness incumbents after the arc's edge writes → `make recompute-graph` + `make snapshot`, both nodes green) and lint-audit 21 → 0 (reviewed noqas + UP031/UP037 fixes); doc/script-search-check + convo-fresh-check always-red by design mid-arc | `outputs/advisory_report.md` |
| 4 | Graph/DuckDB rebuild (P2.2) | (iii) table-level dirty tracking via per-input hashes in `_build_meta` | HOLD replaced by measured alternatives; probe #325 landed in-repo | **EXECUTED 2026-10-05** — S1 of `archive/graph/graph_rebuild_fast_path.md` (completed.md entry 347) | `pending.md` P2.2 · `tests/bench_rebuild_scale.py` · T(R) ≈ 2.28 s + 3.0 µs·R (R=115k → 2.62 s) |
| 5 | Graph/DuckDB rebuild (P2.2) | (ii) fold the 12 `EDGE_REGISTRY` CTAS + `e_all_und`/`e_dir` into one `edge_type`-discriminated scan; drop duplicated `v_centrality_*` DROPs | attacks the 1.70 s fixed band (56 DROPs ≈ 0.21 s, INSTALL/LOAD ≈ 0.29 s) | **EXECUTED 2026-10-05** — S3 of `archive/graph/graph_rebuild_fast_path.md` (completed.md entry 347) | `helpers/graph/query.py:245` (12 entries) · `pending.md` P2.2 |
| 6 | Graph/DuckDB rebuild (P2.2) | (C) rebuild-on-apply: `derive-relations --apply` / `parse_newsletter --apply` call `rebuild()` + stale fail-fast | no ingest lane calls rebuild today, so the first graph query after a generation bump pays it inline; RO callers serialise on `<cache>.build.lock` | **EXECUTED 2026-10-05** — S2 of `archive/graph/graph_rebuild_fast_path.md` (completed.md entry 347) | no `rebuild=True` outside `query.py`/tests; `make graph-rebuild` is manual |
| 7 | Graph/DuckDB rebuild (P2.2) | original P2.2 row-level diff | superseded by rows 4–6; partial-application risk to the single generation stamp (23 `connect()` callers, `csr.py:298`) | CLOSED — retired | `pending.md` P2.2 "HOLD in favour of" |
| 8 | DB estate hygiene | `memory/research.db.bak-20261003-precleanup` (308 MB) | referenced nowhere (doc/tests/helpers) | **CLOSED** (2026-10-05) — file already gone: `memory/` carries no `*bak*`/`*precleanup*` entry (1.5 GB total); nothing to drop | `ls memory/ \| grep -iE "bak\|precleanup"` → empty · `rg "bak-20261003\|precleanup"` → 0 hits |
| 9 | DB estate hygiene | production-store copy census (armed 2026-09-29) | revisit "when any production store roughly doubles": convo_search 820→428 MB, research 307→314, embed 185→211, convo_fts 102→152, corpus 52.6→53, graph 39→40, doc_search 24.7→30 | NO-ACTION — trigger not fired (convo_search halved) | `du -h memory/*.db*` vs census filing in `pending.md` |
| 10 | DB estate hygiene | vault_scaling T1 (1M doubled rows) | `e_all_und` = 115,148 (8.7× headroom); rebuild = 87% fixed statement overhead at live scale | NO-ACTION — not fired | read-only count on `memory/graph.duckdb` · `pending.md` P2.2 measurement |
| 11 | Tracking/doc hygiene | `pending.md` trace-analyzer "open follow-up: add `error_message`" | already executed by `trace_error_forensics` #337 | **CLOSED** (2026-10-05) — done in place: the pending.md follow-up is now the #337 closure line | `doc/improvements/pending.md` (archived 2026-10-02, #337) |
| 12 | Tracking/doc hygiene | C901 residue: two unruled masks — `_materialise_centrality_cache` and `_cli` | booked to "the next c901 pass" (all four ruling masks executed) | **EXECUTED 2026-10-05** — S4 of `archive/graph/graph_rebuild_fast_path.md` (completed.md entry 347) | `helpers/graph/query.py:1594` (`_materialise_centrality_cache`), `:4055` (`_cli`) |
| 13 | Tracking/doc hygiene | OCR proposal `ocr_review_0b63_remediation.md` is gone (never committed) | High items F1/S1 (read-only downgrade) and F2/S2 (list-mention semantics) landed with mutation-hardened tests; the review's remaining lower-priority findings are untracked | **CLOSED** (2026-10-05) — not untracked: the never-committed file was the working draft of #312 (`archive/tooling/ocr_remediation.md`, executed 2026-09-29, S1–S6 = F1–F17, same eval-gate bullet, same mutation-hardened tests); only the `code_review.md:445` pointer was stale (annotated) | `doc/local/engineering/code_review.md:445` + the #312 slice list |
| 14 | Tracking/doc hygiene | thread B: `recompute-graph` apply vs 15 s target | resolved 2026-09-25 — ceiling raised to 20 s, live apply 16.71 s, "no longer blocking" | CLOSED — stop tracking | `archive/graph/recompute_graph_parallel_fanout.md` §7 |
| 15 | Trigger-gated (leave) | HNSW ANN index macros | declined on quality+perf: recall ≈57% @10 on the real corpus, +75% db size, no latency win; actionable swap (distance ranking) already executed as #266 | BLOCKED — revisit on a vss build fixing the COSINE opclass/scan recall, or ≈10⁶ vectors | `pending.md` "Re-evaluate HNSW index macros" |
| 16 | Trigger-gated (leave) | Onager personalised-PageRank wrap | upstream ignores the personalisation vector (restart hardcoded to node 1) **and** the graph is structurally degenerate for PPR (leaf-heavy → ≈1-hop ego rank) | BLOCKED — do not re-litigate on solver grounds | `pending.md` "Wrap `onager_ctr_personalized_pagerank`", `archive/graph/scipy_personalized_pagerank.md` §7 |
| 17 | Trigger-gated (leave) | Security Phase 4 (deploy-time) | activates only if the Flask app is deployed publicly (confirmed not deployed) | BLOCKED | `pending.md` "Security Phase 4" |
| 18 | Trigger-gated (leave) | HGX D10 prediction/motifs lanes | gated on incidence density (measured: group 8/k=3.8, jv 6/k=2.0, 533 live hyper_edges); hyper-native link prediction unstarted | BLOCKED | `pending.md` "HGX D10" |
| 19 | Trigger-gated (leave) | Ontology governance (a) mapping-record glossary, (b) LLM-judge protocol 2 | triggers: a metrics Q&A lane proposal; an LLM-API posture (terrain C4 revival) | BLOCKED | `pending.md` "Ontology governance #244 deferred" |
| 20 | Trigger-gated (leave) | OpenViking context-server pilot | revive only for its differentiators (L0/L1 hierarchy, automatic memory extraction, retrieval traces); labeled eval set transfers verbatim | BLOCKED | `pending.md` "OpenViking … DEFERRED" |
| 21 | Trigger-gated (leave) | scipy minimum-spanning-tree lane | deferred need (Kruskal toy oracle recorded) | BLOCKED | `pending.md` "scipy_algos umbrella batch" |

**Census (updated 2026-10-05, close-out):** rows 4, 5, 6 and 12
EXECUTED via `graph_rebuild_fast_path.md` (S1–S4; parity 31/31 tables,
no-op rebuild 1.11 s vs 3.51 s fresh, C901 masks zero — completed.md
entry 347). CLOSED (8): 1, 2, 3, 7, 8, 11, 13, 14 — rows 1–3/8 by the
2026-10-05 gate close-out (they were written while the gates were still
in flight; row 8's file turned out to be already removed), 11 done in
place, and 13 resolved: the "untracked residue" was the never-committed
working draft of archived #312 (all findings executed). NO-ACTION (2):
9, 10. BLOCKED (7): 15–21.
