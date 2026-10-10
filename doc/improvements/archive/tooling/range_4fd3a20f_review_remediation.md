---
title: "range 4fd3a20f..0805d829c review: high/medium remediation slices"
status: executed
filed: "2026-10-10"
executed: "2026-10-11"
completed_md: "382"
area: "helpers/core/corpus.py, helpers/analytics/model_analytics.py, helpers/validators/static_checks.py, helpers/graph/{csr,extract_relations,triage_pending_relations}.py, helpers/core/person_names.py, helpers/misc/schema_dump.py, tests/"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). -->

# range 4fd3a20f..0805d829c review — high/medium remediation slices

## 1. Motivation

The 2026-10-10 delegation+scan review of the landed range
`4fd3a20f5..0805d829c` (111 commits, 1293 files; roster 442/1292, scan leg
bandit/shellcheck/sqlfluff/semgrep/osv, 7 host subagent lanes; evidence:
`doc/local/engineering/code_review.md`, rosters under
`outputs/reviews/review_{patch,scan}_4fd3a20f*`) surfaced 46 distinct
findings after dedup (raw ≈129 − 3 cross-leg dups − ~80 scan known-class).
Three are blocking/high and five medium; none is visible to any current
gate leg. This proposal carries the fix slices, acceptance criteria, and
test-teeth contract. **S1 is emergency-ordered by the operator** (fix
first, this filing rides the same patch per the house proposal rule).

No slice touches rosters, crosswalks, hierarchies, or extractor rules —
no eval-gate bullet required; every slice carries a mutation-check test
instead (a fix is not closed until its test is proven red on the bug).

## 2. The finding set (high + medium only; low/docs tier parked)

| # | Sev | Where (HEAD) | Defect |
|---|---|---|---|
| B1 | BLOCKING | `helpers/core/corpus.py:236,252` (8ebd60345) | corpus cache v2 persists frontmatter+body but NOT text; cache hits carry `Note.text=""` under a "no consumer reads Note.text" audit that is wrong: `sync_tags.py:244`, `verify_notes.py:1127`, `derive_cited_in.py:120`, `derive_co_mentions.py:213` read it. `maint --full` pre-warms the cache (`maint.py:622`) then runs `sync_tags --corpus --apply` (PRE_FULL+TIER2) whose apply path is unconditional `DELETE FROM entity_tags` + `if bulk:` insert → **empty bulk wipes the table** (7,351 rows armed; cache warmed 2026-10-10 14:01). |
| B3 | BLOCKING | `helpers/analytics/model_analytics.py:649,666` (30284384b) | `# noqa: S608` sits INSIDE the f-string SQL literal → the directive is query text; `local_latency()` raises `sqlite3.OperationalError: unrecognized token: "#"` (executed proof). Invisible to the gate because the noqa-in-literal family is dirty-gated: committed defects on a clean tree produce an empty scan scope. |
| B2 | HIGH | `helpers/validators/static_checks.py:1676` (b56a5b080) | `body.count("\n")` on a LIST (always 0) → the noqa-in-literal span walk inspects only line 1 of a multi-line literal; every later-line directive is silently unadjudicated (5-shape probe). The S4 teeth never seeded a later-line shape, so the bug is green. |
| M1 | MED | `helpers/graph/csr.py:196-215,150-186` | CSR substrate build is non-atomic (bins written in place, manifest last); `load()` defaults `verify_checksum=False` and the serving path never verifies → torn content served as fresh; in-place truncate+write under a concurrent memmap reader risks SIGBUS. House swap pattern not used. |
| M2 | MED | `helpers/graph/csr.py:375-385` | `--verify` always rebuilds first, then loads the just-built artifacts: the verification is tautological, its success message is unconditional, exit stays 0 — `make csr`'s verify leg can never fail. |
| M3 | MED | `extract_relations.py:2859` vs `triage_pending_relations.py:98` vs `derive_cited_in.py:382` | symmetric-type sets disagree (8 vs 5 vs pinned-0 for cited_in) and the triage comment claims parity with the extractor. Same accepted relation promoted via different ingresses gets opposite `symmetric` flags and different canonicalization; `UNIQUE(source,target,edge_type)` does not dedupe reversed pairs → contradictory duplicate rows. |
| M4 | MED | `helpers/core/person_names.py:12,20` (c69366232) | `_TRUST_TOKENS` substring-matches 2-letter tokens: `"Foods of India Ltd"`, `"Fortis Healthcare"`, `"INFO EDGE"` all classify `trust` (verified live) → `resolve_person` matches against the wrong pool; sole caller `shareholding_sync.apply_candidates`. |
| M5 | MED | `helpers/misc/schema_dump.py:95` | `_is_shadow()` prefix-matches ANY `<virtual>_*` table, dropping 8 real companion tables (`script_search_meta/info/mojo_doc/ts_doc`, `doc_search_meta/info`, `memory_search_meta/info`) from the tracked DDL surface — live-DB-verified; `test_schema_drift` re-renders the same generator, so it cannot catch this. |
| M6 | MED | `tests/test_zz_probe_scratch.py` (66d4de699) | committed probe residue: fires 7 Flask requests, asserts nothing (~3.5 s per qa run, can never fail); was formatted instead of deleted when ruff-format flagged it. |

### 2b. Low tier (added 2026-10-10 on operator ask; same dedup provenance)

| # | Where (HEAD) | Defect |
|---|---|---|
| L1 | `helpers/maintenance/rebuild_convo_search.py:583` | row-count verify opens `duckdb.connect(read_only=True)` bare, outside any io_lock and without the ladder — concurrent write window makes the swap-rebuild crash after the swap succeeded; fix = `open_read_only` (host-confirmed). | **PARKED (operator decision)** — next duckdb-touching arc: `open_read_only` + the io_lock ladder.
| L2 | `helpers/misc/trace_quote.py:605,697` | SELECT-only tool opens the trace store READ-WRITE; `--sweep` with no store silently creates an empty `memory/data/agent_traces.duckdb`; holds the RW lock against real writers. Fix: existence check + `open_read_only`. | **PARKED (operator decision)** — same arc as L1: existence check + `open_read_only` (kills the phantom-store creation and the RW lock hold).
| L3 | `helpers/misc/ocr_key_map.py:143-144` | error path collapses `edenai`/`litellm`/`ollama-cloud` (no env fallback, api_key_cmd-only) with bedrock's AWS-credential-chain message — wrong guidance on the no-key failure path. | **PARKED (operator decision)** — provider-specific no-key messages, small behavioral patch.
| L4 | `helpers/misc/trace_quote.py:34` | literal `/tmp/store.duckdb` in the usage docstring (house rule: `${TMPDIR:-/tmp}`). | **FIXED 2026-10-11 (Group A)**: usage docstring now `--db ${TMPDIR:-/tmp}/store.duckdb`.
| L5 | `helpers/graph/query.py:1520-1535` | duplicated `_announce_destructive_op` call back-to-back in `stamp_centrality_cache` (stderr noise; landed in-range). | **PARKED (operator decision)** — dedupe the double `_announce_destructive_op`, with a targeted output test.
| L6 | `helpers/graph/layout.py:100-102` | comment claims "Anchor set is fixed for the whole solve" but `compute_positions_fa2` resamples per iteration (per `layout_anchored_lift.md:80` — behavior is per design; the comment is stale). | **FIXED 2026-10-11 (Group A)**: both comments corrected — anchors are RESAMPLED per iteration (layout.py:167 is ground truth); determinism survives via the seeded rng, which is what the cross-visit stability contract actually needs.
| L7 | `helpers/graph/stats.py:~87-120` | `_chain_dist_stats` sampled mode drops sampled-root diagonal zeros the old materialized path included; docstring claims "byte-identical"; no test pins it (exact/triu unaffected). | **FIXED 2026-10-11 (operator ruling: zeros stay dropped)** — docstring corrected (the byte-identical claim held only for exact mode; sampled totals deliberately differ from the legacy render by exactly n_roots self-distance non-pairs), and both behaviors are now pinned in `tests/test_graph_stats.py::test_chain_dist_stats_*` (exact halving; sampled zero-drop). Zeros-included mutation proven red. No rendered-number change at live scale (`_total` is discarded at both call sites).
| L8 | `helpers/graph/igraph_bridge.py:106-113` | `_require_igraph` error message directs users to the retired `/tmp/venv_igraph` and says NOT to add igraph to .venv — igraph is declared (pyproject.toml:88) and importable; also cites a dead `proposals/` path (see L14). | **FIXED 2026-10-11**: message rewritten (igraph is a declared dependency — `make install-dev`; dead `/tmp/venv_igraph` + not-in-.venv claims gone), guidance pinned by `tests/test_igraph_bridge_guidance.py`, and the test module's own stale docstring/importorskip reason corrected — the bridge suite now runs in .venv (10 green).
| L9 | `tests/test_review_scan.py:815` | `assert "NO durable artifact" in capsys.readouterr().err or True` — doubly dead (`or True` tautology; buffer already consumed on :814). Intended target exists (`review_scan.py:1096`). | **FIXED 2026-10-11 (Group A)**: err captured once; both `ROSTER NOT WRITTEN` and `NO durable artifact` (review_scan.py:1138-1139, same print) asserted for real.
| L10 | `tests/test_trace_contracts_s2_s4.py:124-171` | `test_loader_wires_validation_end_to_end` claims to exercise production load_log wiring but the registered parser is the test's own fake and the load_log row is INSERTed by the test — production writer never run. | **PARKED (operator decision)** — needs a real production-writer test design (the current test's fake parser + test-side INSERT can't be fixed by a one-liner); own small slice.
| L11 | `tests/test_schema_drift.py:63-74` | `test_missing_file_reported` hard-asserts `sd.source_dbs()` non-empty OUTSIDE the module's `_live_dbs_present` skip fixture — fails (not skips) on fresh clones, contradicting the module's own SKIP doctrine. | **FIXED 2026-10-11 (Group A)**: `test_missing_file_reported` now carries the module's `_live_dbs_present` fixture — skips on fresh clones per the module doctrine.
| L12 | `tests/test_review_tooling.py:24` | dead constant `FP = "5ab2f84a6c4d3e91"` commented "asserted via recomputation below" — never referenced. | **FIXED 2026-10-11 (Group A)**: dead `FP` constant deleted (grep-verified unreferenced).
| L13 | `helpers/pdf/pdf_conv_md.py:322-332` | ungated `requests.get(item["url"])` remote-image branch left by the Paddle cut — dormant today (all engines emit local paths) but a live content-derived fetch if a future engine emits URLs; delete with the Paddle remnants. | **PARKED (operator decision)** — delete with the Paddle remnants, next pdf-touching arc.
| L14 | stale doc paths (10 sites, 2 legs concur) | `pdf_conv_md.py:5,511` + `teleocr_engine.py:9` → `proposals/teleocr_pdf_fallback.md` (lives in `archive/pipeline/`); `igraph_bridge.py:110-113` ImportError → `proposals/hybrid_graph_onager_igraph.md`; `agent_traces.py:8,2089` → two `archive/tooling/` proposals via dead `proposals/` form; `test_rollout_report_legs.py:10`, `test_agent_traces_rollout.py:9`, `test_harvest_zcode_rollout.py:10`, `test_model_analytics_self_contained.py:12` → same class. Plus `extract_relations.py:274` + `tests/test_query_plans.py:143` cite `doc/improvements/pending_improvs.md` (wrong directory). And `doc/procedures/ocr_review.md:105,142` cite `../archive/tooling/ocr_rule_census.md`, which resolves to nonexistent `doc/archive/` from `doc/procedures/` (promotion leftover; backtick refs are invisible to the link gates). | **DONE 2026-10-11** — the S12 sweep repointed all 16 sites (this row's 10 + igraph_bridge + ocr_review census self-ref + the two `pending_improvs` directory citations).
| L15 | `helpers/misc/gate_query.py:493,763` (scan adjudication) | junitxml/coverage ingestion via `xml.etree` (bandit B314 / semgrep XXE class). Input is machine-local gate-run files, not attacker-controlled — **FIXED 2026-10-11**: the ruff `# noqa: S314` alone never reached the scan legs (bandit reads `# nosec`, semgrep `# nosemgrep:`); both directives + threat-model comment added (defusedxml deliberately not a dep). |
| L16 | desktop `src-tauri/src/main.rs:17-102` | all 14 Tauri commands are sync (main-thread); `similar_notes`/`search_hybrid` load ~17k vectors + O(n·k) cosine per call, `read_note` ≤4 MB blocking read → UI jank under load. Fix: `async fn` (or `#[tauri::command(async)]`) on the three heavy ones. | **PARKED (operator decision 2026-10-11)** — own slice when the desktop surface next gets work.
| L17 | `helpers/misc/parity_harness.py:341,366` (scan adjudication) | `exec` of repo-internal module source at a pinned ref + non-literal `import_module` from a fixture relpath — parity-harness by design, operator-owned input. **FIXED 2026-10-11**: three-tool suppression (`noqa: S102`/`nosec B102`/`nosemgrep exec-detected`; `nosemgrep non-literal-import`) + threat-model comments. |
| L18 | `helpers/misc/llama_servers.sh:61,102` (shellcheck SC2086) | `set -- $spec` word-splits the `"target port"` spec DELIBERATELY. **FIXED 2026-10-11**: `# shellcheck disable=SC2086` with rationale at both sites. |
| L19 | `tests/test_rollout_report_legs.py:112` (bandit B105) | "hardcoded password" on a SQL column-list constant — false positive; the ruff `# noqa: S105` never reached bandit. **FIXED 2026-10-11**: `# nosec B105` added beside it. |

## 3. Slices

### S1 — corpus cache v3: persist `text` (B1) — EXECUTING NOW, operator-ordered

`corpus_cache` gains a `text` column (ALTER-guarded migration for existing
DBs; CREATE for fresh ones). Cache-hit paths return the stored text instead
of `""`; rows with `text IS NULL` (pre-v3) self-heal by reading the file
once and riding the existing upsert backfill. The mtime-hit fast path is
preserved for migrated rows; cache grows by raw vault text (~MBs, sidecar
DB, gitignored — same trade body already made).

- **Correctness contract:** for every note, `Corpus.load(use_cache=True)`
  warm-cache `note.text == Path(note.path).read_text()` (utf-8/replace),
  identical to the cold-load value; `split_front_matter(note.text)`
  reproduces the cached frontmatter.
- **Test teeth:** regression test builds a tmp vault + cache, loads cold
  then warm, asserts text parity on BOTH hits (mtime-hit and hash-hit
  paths) and that `sync_tags.split_front_matter(warm.text)` extracts the
  tags. Mutation: revert the two `text=""` return sites → test RED.
- **Fix comment:** the wrong "no consumer reads Note.text" audit comment
  is corrected in place, naming the four consumers.

### S2 — move two in-literal noqa directives (B3) — LANDED 2026-10-11

`model_analytics.py:649,666`: relocate both directives after the closing
quotes (house-correct placement pinned by
`test_noqa_after_closing_quotes_passes`); drop the inline comment or move
it above the statement. Accept: `local_latency()` executes clean against a
synthetic db; `check_noqa_in_sql_literal` full-scan reports 0.
Executed: both directives now trail the closing `"""` + comma (the
pinned shape), rationale kept as the second comment; teeth =
`test_local_latency_executes_synthetic_db` (red on the bug — directive
text reaches sqlite). Post-fix full scan 0 fatal / 0 advisory.
Follow-up (structural, separate slice if the operator wants it): scan the
union of applied stgit patches in the dirty-gate so committed defects are
in scope (H1 of the review; `_stack_files()` already supports it).

### S3 — fix the span walk (B2) — LANDED 2026-10-11

`static_checks.py:1676`: `range(1 + body.count("\n"))` →
`range(len(body))` (or `1 + tok.string.count("\n")`). Accept: seeded
directive on a LATER line of a multi-line f-string is reported; the five
existing shapes stay green; mutation `tok.string.count` → `1` leaves the
new test red. Also delete the now-dead `body[off] if off < len(body) else ""`
guard's dead arm.
Executed: `range(len(body))` + dead-arm deletion; new
`test_noqa_on_later_line_of_multiline_literal_rejected` seeds the
directive on line 4 of a 5-line literal; mutation
`range(len(body))` → `range(1)` proven RED, restored green; the five
existing shapes stayed green throughout.

### S4 — CSR substrate: swap-pattern writes + honest verify (M1+M2) — LANDED 2026-10-11

Build to tmp bins + `os.replace` (house swap pattern, as `query.py`
rebuilds and convo_search already do); `load()` gains a
`verify_checksum` default that at minimum verifies on the serving path's
first open; `--verify` verifies EXISTING state without rebuilding and
reports `fresh=` truthfully with exit 1 on mismatch. Accept: kill between
bin writes leaves the previous manifest serving coherent content;
corrupted `neighbors.bin` + `--verify` exits non-zero.
Executed: build() stages all four artifacts to dot-tmp files and
`os.replace`s them with the manifest LAST (commit point); `load()`
defaults `verify_checksum=True` (the serving lane passes no flag, so a
swap interrupted between replaces is caught at first open); `--verify`
no longer builds — it verifies existing state and exits 1 naming the
failure mode (missing/corrupt, checksum mismatch, stale), so
`make csr`/`csr-rebuild` became build-then-verify as two steps.
Contract note: a mid-swap kill leaves either the fully-old state or a
checksum-detected mismatch — torn content is never SERVED as fresh (the
test pins this with the generation deliberately held constant so the
checksum alone must catch it). 4 tests in `tests/test_csr.py`; both
mutations (default-verify off; tautological verify) proven red.

### S5 — reconcile the symmetric convention (M3) — LANDED 2026-10-11

One set, one home: the extractor's 8-type set vs the triage lane's 5-type
set vs `derive_cited_in`'s pinned-0. Decision needed from the operator on
`invested_in`/`exposed_to`/`cited_in` semantics (data question, not just
code): reconcile to one frozenset imported by all three writers, align
canonicalization, and add a one-time dedup/repair pass for already-landed
contradictory rows. Accept: all three writers import the same constant;
queue-hygiene test pins the set.
Executed per the operator ruling of 2026-10-11: the three contested
types are DIRECTIONAL (all 3,136 live rows carried symmetric=0), so
`helpers/graph/edge_conventions.py` now owns the shared five-type
`SYMMETRIC_EDGE_TYPES` + `canonical_pair`. The extractor's promote path
and the triage lane alias the constant (the triage writer also gained
the canonicalization its "downstream (no-op)" comment only claimed);
`derive_cited_in` derives its flag instead of pinning. Repair applied
directly on the live DB through `helpers.core.db.connect()`, one
transaction, safety export first (`/mnt/data/tmp/s5_repair_backup.json`):
115 flag flips (competes_with migration 105 + jv_with manual 10) and 6
non-canonical reversed-pair twins deleted (the 4 Tata/Indian-Hotels
same_group pairs, Globus/ANSA McAL, AbbVie/Glenmark Nordic). Verified
post-repair: 0 reversed dupes, 0 flag-inconsistent rows.
Queue-hygiene suite: `tests/test_edge_conventions.py` (8 tests — set
contents, writer aliases, pattern-table sync, derivation, end-to-end
canonical write, no-redeclaration).
**Parked observation (out of approved scope):** 34 unique symmetric-type
rows still sit in non-canonical orientation (source > target, no twin) —
harmless while UNIQUE sees them, but a one-line canonicalization sweep
could align them if the operator wants.

### S6 — person_names token matching (M4) — LANDED 2026-10-11

`any(token in normalized)` → word-boundary token match
(`re.search(rf"\b{re.escape(token)}\b", normalized)`), with the existing
4 tests plus `"Foods of India"`, `"Fortis Healthcare"`, `"INFO EDGE"`
pinned as NOT trust. Mutation: revert to substring → new tests red.
Callout: `shareholding_sync.apply_candidates` behavior may shift for
names that were silently misclassified — worth a one-off diff of affected
candidates after landing.
Executed: `_matches_any_token` in person_names.py applies the boundary
discipline to BOTH token families (`\bFO\b` still matches a real
family-office abbreviation; `\bHUF\b` likewise). 5/5 tests green;
mutation (substring revert) proven red.
**Callout executed on live data:** 1,150 of 26,162 entity names (4.4%)
shift trust → person — 3i Infotech, Adani Green Energy Twenty Four,
Abram Food, foundations, CFO-named rows. Fallout analysis: zero of them
are stored as `entity_type='trust'` and none remain trust under the
fixed classifier, so the stored types were always right — no data repair
needed; only the match-time pool selection was broken and is fixed.
candidates after landing.

### S7 — schema_dump shadow allowlist (M5) — LANDED 2026-10-11

`_is_shadow()` → suffix allowlist (`_data|_idx|_content|_docsize|_config`
+ vec0 shadows) instead of prefix match. Accept: `make schema-dump`
renders the 8 companion tables; `schema-check` green; drift test extended
to assert the companion tables appear.
Executed: the matcher is ENGINE-KEYED (`_virtual_modules` parses the
USING clause) — fts5 shadows are exactly data/idx/content/docsize/config,
vec0 shadows are chunks/rowids/info + vector_chunksNN. The engine key is
load-bearing: `_info` is a vec0 SHADOW but a REAL house table for the
FTS5 search DBs, so a blanket suffix list would have re-swallowed
doc_search_info/memory_search_info/script_search_info. Regenerated goldens
carry 9 companions (the finding's 8 + `note_search_meta` in
research.sql, which the original count missed); `make schema-check`
green (13 DBs match); drift test gained
`test_fts5_companion_tables_are_tracked` (asserts the companions in the
RENDERED DDL — the only assertion that can catch a matcher regression,
since the drift gate re-renders the same generator); prefix-match
mutation proven red.

### S8 — delete the probe-residue test (M6) — LANDED 2026-10-11

`git rm tests/test_zz_probe_scratch.py` (routes already covered by
`test_api_graph_*`). One-line; no teeth needed (its absence is the fix).
Executed: file deleted; no code references remained (only historical
docs/outputs mention it).

### S9 — review-selection boundary: cover what the roster silently drops (mechanism — operator-priority) — LANDED 2026-10-11

The range review proved the roster is not the diff: OCR 1.12.11's
BUILT-IN exclusion classes sit outside `rule.json` and outside the
teeth-family list — `deleted` (13 files, incl. the whole pix2text
removal), `provider_directory` (first-party `vendor/llamacpp/health.py`
+ `build.sh`), `default_path` (`frontend/tests/guards.test.ts`, a live
security test neither leg selected). Four changes, in order:

(a) **`.opencodereview/rule.json`**: `include` gains
`frontend/tests/**` and `vendor/llamacpp/health.py`; `build.sh` stays
excluded but DECLARED (comment in rule.json + a line in ocr_review.md —
its out-of-scope status already produced one vacuous scanner green per
`review_scan_leg.md:308`).
(b) **teeth**: `make review-patch`'s hardcoded family list gains
`frontend/tests/` so a rule regression fires instead of self-masking.
(c) **deletion census** (operator-flagged as the crucial half) —
**LANDED 2026-10-11**: `review_scan.py` owns it (both `make review-scan`
and `make review-patch` inherit it via the shared leg) — `_deleted_files`
(git name-status, D rows) feeds a `deletions: N — review by hand` block
in the report and a roster section (20-path cap with `… +K more`), and a
deletion-only diff still produces a roster. Verified end-to-end on the
pix2text-removal commit (2 deletions listed, all-scanners-skipped run);
4 census tests in `tests/test_review_scan.py` (71 green).
(d) **`ocr_review.md` §1**: document the three built-in exclusion
classes and the re-derive duty (preview JSON `exclude_reason`, not
rule.json, is where they live). **LANDED 2026-10-11.**

**All four landed 2026-10-11.** (a) rule.json include gains
`frontend/tests/**` + `vendor/llamacpp/health.py`; build.sh stays out of
scope but DECLARED as an explicit rule.json exclude (JSON has no
comments — the declaration is the entry plus the ocr_review.md §1 note).
(b) `PRODUCT_FAMILIES` gains `frontend/tests/`. (d) the §1 block
documents the three built-in classes + the post-upgrade re-derive duty.
Acceptance run live: `make review-patch COMMIT=66d4de699 OFFLINE=1` (the
exact commit whose frontend test traffic default_path dropped) —
`frontend/tests/guards.test.ts` now SELECTED, `selection-teeth OK`. The
scan leg's materializer hit this sandbox's read-only $TMPDIR
(environment, not code); the census half was already verified
end-to-end on the pix2text-removal commit in (c).

Accept: preview on a diff carrying deletions + frontend test traffic
selects the test file AND prints the deletion census; teeth assertion
names the new family. Boundary note: S9 is about WHAT THE ROSTER
SELECTS; it does not change any gate. S10 below is a different surface
(what the dirty-gated GATES scan), not a S9 alternative.

### S10 — dirty-gate scope: committed defects must be in view (mechanism) — LANDED 2026-10-11

`check_noqa_in_sql_literal` (and every dirty-gated static_checks family)
scans only the uncommitted tree — B3 proved a committed defect is
permanently invisible on a clean tree. Fix: the dirty-gate scope becomes
`dirty tree ∪ union of applied stgit patches` (the patch union via
`_stack_files()`, already available), env-escapable for one-off narrow
runs. Accept: a defect committed inside an applied patch (but not in the
worktree) is reported; clean tree + clean stack = empty scope;
`get_dirty_py_scope()` gains the union with its own test.
Executed: `get_dirty_py_scope()` unions the applied-patch `.py` surface
(non-stgit checkouts unchanged — `_stack_files()` None short-circuits);
`STATIC_CHECKS_DIRTY_SCOPE=worktree` escapes the union. 4 tests in
`tests/test_dirty_scope.py` (union, clean∧clean=empty, env escape,
no-stack unchanged); suite 35 green. Live-verified on this stack: the
scope is 37 files including patch-committed, worktree-clean ones
(csr.py, review_freshness.py).

### S11 — freshness ledger: record landed ranges (mechanism) — LANDED 2026-10-11

`review_freshness.py` gained `--from/--to` (pair-required, outranking
`--stack` per REVIEW_RANGE_ARGS convention): `fingerprint_range`/
`status_range` key the LANDED pair's diff text (same sha256[:16] as stack
mode; row `stack: null`). `review-patch` (review_selection) and
`review-scan` both print the freshness line in range mode instead of the
old `n/a`. Live-verified on this range: UNREVIEWED → `--record --leg
delegation --leg managed` → FRESH with note — **the reviewed range is now
remembered; the next run can start from the last scanned head**
(`--from <last> --to <new-head>`). 5 range-mode tests in
`tests/test_review_tooling.py` (record→FRESH, content-change→STALE,
null-stack row, half-range refusal, print wiring); 100→107 green.

**Perf correction (same day, operator-reported: the range lookup took
~1 min).** Measured on this 1293-file range: myers / histogram / patience
diff algorithms all 62–66s (algorithm choice is irrelevant — the cost is
~12.6 MB of patch-text generation over the file pairs; the name-only tree
walk is 0.06s), so neither `--histogram` nor `git range-diff` (a
two-range comparison tool, wrong question AND more expensive) helps.
Two-part fix: (a) `--no-renames` on both diff surfaces — the fingerprint
becomes a function of CONTENT, not of the local `diff.renames` config;
(b) the fp is **memoized by the resolved SHA pair**
(`review-freshness.diffcache.json`, flock-serialized, last-64 kept) —
identical SHAs provably mean identical diff text, so a repeat lookup
never recomputes: measured **61.7s miss → 0.076s hit**. The `--no-renames`
re-key staled the first recorded row by design; one re-record (done).
Rows now carry `legs: delegation|managed|scan` — `scan` is the native
scanner leg; record ONLY the legs that actually ran (the upsert unions,
so a wrong leg is corrected in the row).

**S11b containment (same day, operator ask):** ranged rows now carry
`base_sha`/`head_sha` (backfilled on re-record), and `status_range`
reports **SUBSUMED** — same base + asked head an ancestor of a recorded
head — instead of a lying UNREVIEWED for a subrange of an
already-reviewed superset. Live-verified on the exact case that prompted
it (`4fd3a20f5..30284384b` inside the recorded `4fd3a20f5..0805d829c`):
SUBSUMED with the containing review's legs/note, 0.097s. Different-base
overlap deliberately stays UNREVIEWED (partial overlap is not
containment). 4 new tests (SHAs recorded, SUBSUMED + print wording, not-
ancestor, different-base); suite 105 green.

### S12 — sweep the stale doc-path cluster (L14, mechanical) — LANDED 2026-10-11

Repoint the 10 dead `proposals/`/directory forms to their `archive/`
locations (incl. the user-facing `igraph_bridge` ImportError and the two
`ocr_review.md` self-references), and fix the two
`pending_improvs` directory citations. The archival checklist gains one
line: grep by proposal NAME, not by tracked path (proposals are filed
untracked, so path-based sweeps never see code references — H2 of the
review).
Executed: 16 sites repointed (agent_traces ×2, teleocr_engine,
pdf_conv_md ×2, igraph_bridge ImportError, extract_relations +
test_query_plans `pending_improvs` → archived ledger, the three rollout
test docstrings, model_analytics_self_contained, Mojo/bench ×3,
ocr_review census self-ref), archival checklist item 7 added.
**Boundary note (EXECUTED 2026-10-11, operator-directed):** the 38
`findata/**` company stubs carrying the `.txt`-form ref
(`doc/improvements/pending_improvs.txt`, template residue from
2026-07-20) were bulk-repointed to
`doc/improvements/archive/tooling/pending_improvs.md`; 0 `.txt` refs
remain.

### S13 — scan-leg adjudication: complete the three-tool suppressions (LANDED 2026-10-11 for the non-noise set)

The range's scan leg kept re-reporting four findings that already carried
ruff noqas: **bandit reads `# nosec` and semgrep reads `# nosemgrep:` —
neither reads ruff's `# noqa`** (the house checklist's "'I annotated it'
is not 'the finding is adjudicated'" trap, live). Fixed by adding the
tool-native directives at the four non-noise sites: gate_query xml.etree
×2 (L15), parity_harness exec + importlib (L17), llama_servers SC2086 ×2
(L18, `# shellcheck disable=SC2086` with rationale), rollout-test B105
(L19). Each carries a one-line threat-model note; defusedxml deliberately
NOT added as a dependency for machine-local inputs. Verified by direct
shellcheck (0 SC2086 file-wide) and the post-fix bandit-only scan leg.
**Swept 2026-10-11 (S2/S3 now landed)**: the formatted-SQL cluster on
`helpers/graph/query.py` — 40 B608 findings over 39 lines, every site
the adjudicated f-string-with-`?-placeholders`-and-bound-params pattern —
now carries closing-line `# noqa: S608` annotations (the later-line
placement S2/S3 made gate-adjudicable; the scan leg's NOQA_MAP
adjudicates them, bandit's raw count is unchanged by design). Sweep
mechanics worth keeping: the directive MUST go on the literal's closing
line — bandit cites the OPENING `f"""` line, and annotating that line
puts the directive into query text; the first sweep attempt did exactly
that on 22 sites and the span-walk gate caught every one. Final state:
gate 0/0, ruff clean, bandit at its 40-finding baseline.

## 4. Sequencing and gates

S1 lands immediately (this patch, with its regression test). S2/S3/S8/S12
are mechanical and can ride the same or next patch on the operator's go.
S4/S5/S6/S7 are behavioral and land one per patch with their own targeted
tests. S9–S11 are review-mechanism changes (rule.json + Makefile +
review_freshness) — advisory-facing only, never gate legs. Full gates
quartet ONCE over the batch on the operator's explicit go (per
`doc/procedures/gates.md`); targeted tests only per-fix.

## 5. Non-goals

Docstring/comment-only nits documented but not slice-worthy (L4 /tmp
docstring, L6 layout comment) ride whichever patch next touches their
files; informational observations (maint F4 gate_query INSERT pattern,
embed_store TEXT/BLOB fresh-create churn, trace_contracts warn+count
deferral, desktop provenance/CSP posture, pre-existing `graph.ts` option
interpolation) stay recorded in
`doc/local/engineering/code_review.md` and are not carried as slices.

## 6. Deferred (2026-10-11 gate close-out)

- **extract_relations C901 (lint-audit)** — `extract_relations` sits over
  the complexity cap (11 > 10, `ruff --select C901` at :2458).
  PRE-EXISTING: present at HEAD before any slice edit (verified by a HEAD
  probe), and lint-audit has been red on it since run 1340 (2026-10-09).
  DEFERRED to its own split slice per the c901_d1 house pattern — the
  function is the extraction core, and a mechanical split needs its own
  parity shakedown (the test_extract_relations_* suites are the oracle;
  the toy baseline JSON pins the bridge side). Not a rush job: an
  11-point function is one bad extraction away from a behavior change in
  the repo's highest-signal lane.
- **dispatch-parity staleness (live-invariants)** — the two
  exact-vs-incumbent tests (closeness 0.0055, betweenness 0.0091)
  pre-date this arc (failing since run 1375, 2026-10-10 13:52) and
  survive the store repairs: `stamp-centrality` deliberately SKIPS
  lane-served metrics (csr_lane_remediation S4 — exact compute was too
  slow in the critical qa path), so `graph_analytics` rows go stale
  while the cache/lane serves fresh values, and the old parity tests
  compared live dispatch output against those deliberately-stale rows.
  **RESOLVED 2026-10-11 (operator ruling: serve-from-cache stays —
  option (b) re-stamp rejected for the qa-path cost):** the two tests
  are reshaped to serving-stack INVARIANTS (full 1,734-population,
  values in range, deterministic within a generation —
  `test_dispatch_live_{closeness,betweenness}_serving_stack_invariants`);
  fresh-exact parity belongs to the off-gate slow class (the stamped
  graph_analytics rows are the durable exact record). Both green; the
  classification meta-test updated to the new names.
