---
title: "Range-review follow-ups a6fccfb23..4b6f50912 (delegation + scan + goldfish)"
status: "executed"
filed: "2026-11-04"
executed: "2026-11-04"
completed_md: "479"
area: "helpers/core/duckdb_shared.py, helpers/core/quack_query.py, helpers/graph/hyper_spectral.py, helpers/maintenance/shareholding_sync.py, tests/test_integration_maint_chain.py, tests/test_enrich_relations.py, Makefile, doc/security/, Mojo/"
---

# Range-review follow-ups a6fccfb23..4b6f50912 (delegation + scan + goldfish)

**Date:** 2026-11-04 · **Status:** EXECUTED 2026-11-04 (completed.md #479) ·

Provenance: first three-leg review (OCR delegation + review scan +
goldfish) over `e3147ef3c007..4b6f50912011` (71 commits; e3147ef3c is the
last reviewed boundary). Canonical report:
`outputs/reviews/review_report_e3147ef3c..4b6f50912.md`; freshness ledger
row `e3147ef3c007..4b6f50912011 fp=d52f6763e4abdd0b
legs=['delegation','goldfish','scan'] -> FRESH`; goldfish raws are the
SUPERSEDING second passes (2026-11-04):
`outputs/reviews/review_goldfish_a6fccfb23_4b6f50912_zoomout.md` and
`..._impl_readiness.md`. The scan leg yielded zero novel ≥MINOR (five
semgrep mediums are internal-identifier SQL composition; the
`quack_query.py:165/:166` pair is a dead-code symptom of R1). Items
R1–R14 below carry every finding; recording-with-rationale is a valid
close for any of them.

## MAJOR

### R1 — quack-query bridge: wire-or-cut

helpers/core/quack_query.py (187 ln, `quack_query()` :123) has zero
importers repo-wide, zero tests, no Makefile target; wire attach actually
routes via `duckdb_shared._attach_wire` (duckdb_lock.py:194). Sibling
bin Rust/embed_cache/src/bin/quack-query.rs (171 ln) equally unwired.
Host-confirmed. Scan mediums quack_query.py:165/:166 sit on this dead
code. Fix: wire into one quack read path (embed_cache.py:165 shape),
cut both files, or record as parked trial with a named-consumer
deadline (D15). Acceptance: either a named consumer + a test, or the
files gone and the trial verdict recorded.

### R2 — ambient allow_unsigned_extensions

`unsigned_ext_kwargs()` (duckdb_shared.py:242-249) makes
`allow_unsigned_extensions=True` ambient in EVERY factory, including the
shared server, with no threat note. Riskiest irreversible item in the
range. Fix: restrict to the onager-load paths; add a threat note under
`doc/security/` (create if absent). Acceptance: shared server denies
unsigned extensions; rg shows the kwarg only at load sites; note lands.

### R3 — post-freeze Mojo kernel: ruling needed

7d8c4dc56 froze Mojo; 4ebbc41c0 then added
Mojo/src/bench/bfs_bipartite_all.mojo (+91) behind `MOJO_TRAIL_BFS=1`
(hyper_trails.py:66-68,244-256,297). Parity 5.92 vs 5.98 ms; doc says
keep scipy default (scaling_next_walls.md:152-167). Dormant by default.
Fix: explicit operator ruling — record the unfreeze (with a promotion
gate) or delete the kernel. Acceptance: ruling recorded as a dated
addendum in scaling_next_walls.md; either the env flag dies or the
promotion gate is documented.

### R4 — hyper_spectral.py dead code + completed.md narration

helpers/graph/hyper_spectral.py (249 ln) has zero importers;
completed.md:13366 claims "production wire" then admits no slot (D15
breach; graph_design.md:471/473). Fix: land a caller or trim the
module; correct the completed.md narration. Acceptance: consumer or
deletion; doc sentence matches reality. (completed.md is main-owned —
operator edits.)

### R5 — refresh-chain orchestration contract unguarded

Integration gate is pytest-only (Makefile:262-264); zero coverage of
the phase-A join and the `shp --fetch-only` → scratch JSON →
`--apply-only` roundtrip (shareholding_sync.py:65/:1288/:1254; writer
:1287-1310 vs reader :1396-1404). The cd94b76f0 lock/orchestration fix
is itself unguarded — regression surfaces only in the live chain.
Fix: dry-run refresh-chain smoke leg in the integration gate.
Acceptance: gate leg exercises the fetch/apply roundtrip on fixtures
and fails on writer/reader drift.

## MINOR

### R6 — cache-age-guard wiring has no pass-level test

S2 age-guard wiring (enrich_relations.py:736, call site :731-741) is
vacuous as tested: call-site neutered → 91/91 green
(mutation-verified). tests/test_enrich_relations.py:346-372 are
unit-only. Fix: one >45d-mtime pass-level test through
`relations_enrich --apply` that fails loudly when the guard is
disconnected. Acceptance: mutation (guard removed) turns the new test
red.

### R7 — qa_gate_heavy_tests.md frontmatter + rejected-scheme note

Frontmatter still `status: proposed` / `executed: null` while the
record claims #478; its SAVEPOINT scheme was rejected
(tests/test_rebuild_schema.py:31). Fix: flip frontmatter to
executed + #478 anchor; note the rejected SAVEPOINT scheme. Acceptance:
frontmatter matches completed.md; rejection noted. (Found by BOTH fish
— zoom-out F9 and impl F3.)

### R8 — retracted onager rationale still asserted

Makefile:141/:252 and vendor/onager/build.sh still claim the retracted
rationale ("no community 2.0.0 artifact" — retracted 2026-10-24,
PROVENANCE.md:43/:48/:71). Fix: repoint the comments at PROVENANCE.md.
Acceptance: no live text asserts the retracted claim.

### R9 — ty suppression composition blind spot

Makefile:822 `--exit-zero-on-warning` composes with ty.toml:34
`unresolved-import="warn"` on the bench path ⇒ any unresolved import
under helpers/bench ships green. Fix: drop exit-zero for the
bench-scoped ty run, or escalate unresolved-import to error for bench.
Acceptance: an unresolved import in helpers/bench fails the leg.

### R10 — idempotence oracle + expected-steps independence

Idempotence oracle compares run B vs C
(tests/test_integration_maint_chain.py:708), so settles-on-first-run
mutations pass; `_full_expected_steps` (:626) derives expected order
from the same maint constants the runner iterates (real teeth =
semantic assertions :671). Fix: restore a run1-vs-run2 comparison;
source expected steps independently. Acceptance: reorder/settle
mutations fail.

### R11 — hermetic chain tests cover no-corpus only

`_shim_derive_events` forces `de._HAS_CORPUS=False` (:452-462, :461)
while production runs `--corpus`. Fix: add one --corpus fastpath (S1b)
case. Acceptance: corpus-path regression visible in the gate.

### R12 — bench memory guards untested

hyper_scale_bench.py guards ship untested: 128MiB chunk budget (:250),
connected_rand_sparse (:269); regression mode is box-thrash parked as a
timing flake. Fix: small unit tests for both guards. Acceptance: guard
mutations fail the new tests.

### R13 — 4ebbc41c0 commit-message breach (record-only)

Empty subject AND body (commit-messages.md:35) + over-bundle (5 walls +
Mojo kernel + duckdb_lock + validators + 318-ln doc). Landed on main —
no git rewrite; recorded as a process note. Acceptance: none (this is
the record).

## NIT BUNDLE (R14)

- Lanes filter silent-empty on unknown names
  (harvest_conversations.py:2251-2253; production unfiltered
  maint.py:325) — validate lane names.
- `min(workers, len(src))` cap (hyper_trails.py:408) unfalsifiable by
  suite inputs (probe-verified real) — probe test optional.
- Wall-1 sharding dormant 11.5× below trigger (k_shards=1,
  query.py:1323,2671,2674) — waiver note only.
- bipartite_communities imported only by its own test (chain lane →
  bench → baseline json → qa watch leg; thin but real) — keep, note.
- Docstring/stub drift: harvest "five lanes" vs 6; rebuild-schema WHY
  docstrings stripped; static_checks/frontmatter chain steps are
  pass-through stubs (:530-546).
- signal_sweep.py zero callers (host-confirmed) — smoke test or Make
  target, or park with a verdict.
- Stale-discovery port-reuse false-liveness (host watch; mitigated by
  served_entry path match + wire token auth) — recorded, no action.

## RECORDED, NO ACTION

- Onager vendoring judged sound (2 files / 185 ln build recipe; census
  correction: the range's real bulk is the 4b147772b vault refresh,
  70,322 churn = 59%, plus 14 db_sync regen commits).
- completed.md:13561 #478 wall claim cherry-picks the failing baseline
  (pre-fix OK 300.02s vs post 317.08 ≈6% slower best-vs-best) —
  main-owned doc; operator call.
- Verified-good teeth (fish-verified): FK guard
  tests/test_enrich_relations.py:262/:290/:345; wire-attach fallback
  tests/test_duckdb_lock.py:251 + negative :300; endpoint liveness
  tests/test_duckdb_shared.py:142/:168; mirror-data teeth
  tests/test_graph_scale_watch.py:134/:170; pool dispatch
  tests/test_hyper_trails.py:217/:252; fixture hermeticity
  tests/_tmp_hygiene.py:133.

## Resolution

Executed 2026-11-04 (same-day arc; targeted tests only, tree left dirty
for the operator):

- **R1 done** — cut. `helpers/core/quack_query.py` and
  `Rust/embed_cache/src/bin/quack-query.rs` deleted; `[[bin]]` block
  removed from `Rust/embed_cache/Cargo.toml` (TOML re-validated; the two
  semgrep mediums died with the file). Archive mentions
  (`archive/database/db_scaling_tuning.md:323`,
  `archive/testing/test_coverage_gap_sweep.md:96`) are historical
  narrative — not retro-edited.
- **R2 done (amended acceptance)** — narrowing, not server denial:
  grounding showed the quack server child MUST carry the flag or every
  onager lane over the wire degrades, so "shared server denies unsigned"
  was wrong as written. Landed: `connect_shared` connects PLAIN
  (duckdb_shared.py:239); unsigned stays explicit at onager's own con,
  the query.py file-mode build, and the server child; threat note
  `doc/security/unsigned-extensions.md`; pin
  `tests/test_duckdb_shared.py::TestConnectSharedUnsigned`.
- **R3 done** — dated freeze-chronology addendum in
  `archive/graph/scaling_next_walls.md` (frozen at 7d8c4dc56; port
  landed post-freeze in 4ebbc41c0 as dormant env-gated alternative;
  scipy default untouched; promotion still gated on measured >2×). No
  cut — the parity doc already carried the promotion gate.
- **R4 closed, finding mis-aimed** — completed.md #472 (not :13366's
  D15 breach) explicitly documents lanes 3+5 as an operator-instructed
  standalone lane CLI ("house pattern, not the proposal's nominal
  query.py slot"); zero importers is expected for CLI lanes (hyper_trails
  has no Makefile target either — house-consistent). Landed: docstring
  clarification in hyper_spectral.py; no cut; completed.md narration
  needs no correction (and is operator-owned anyway).
- **R5 done** — `tests/test_refresh_chain_smoke.py` (4 tests,
  `@pytest.mark.integration`, auto-picked by the integration gate):
  dry-run prints/writes nothing, `--apply` consumes scratch into tmp
  sources.duckdb, empty-scratch quiet no-op, writer-schema drift fails
  loud (KeyError). Sentinel seam at `_resolve_companies` keeps it
  hermetic; payloads use the writer's exact JSON shape.
- **R6 done** — three tests through the real wiring point
  (`_load_or_fetch_infos`, the call site the mutation neutered): stale
  cache on disk → SystemExit on apply, dry-run OK, refresh_cache bypasses
  guard without network. Guard-call removal at the wiring point turns
  them red (the unit-only gap is closed).
- **R7 done** — frontmatter → executed/executed 2026-11-04/completed_md
  478; header Status EXECUTED; SAVEPOINT-rejection noted in the S2
  design + risk sections (tests/test_rebuild_schema.py:31).
- **R8 done** — Makefile:141/:252 + vendor/onager/build.sh header
  reworded to the corrected truth (community repo DOES serve the 2.0.0
  artifact; vendoring = speed/reproducibility; see PROVENANCE.md).
- **R9 done (scoped)** — bench `unresolved-import` → ERROR in ty.toml;
  the sklearn residual is this ty version's SAME rule aliased as
  import-not-found, so it is carved back to WARN by a file-level
  override on hyper_community_probe.py (later overrides win). Verified:
  `ty check --config-file ty.toml --exit-zero-on-warning helpers/bench`
  rc=0 with the probe warn; a first-party unresolved import in bench
  would now be an error and fail the leg.
- **R10 done** — `assert record2 == record` (full script+argv+rc
  equality) in test_chain_idempotence; settle-on-first-run mutations now
  fail.
- **R11 done (both halves)** — pinned `EXPECTED_FULL_SCRIPTS` literal
  (32 steps) + `TestChainContract::test_full_chain_scripts_pinned`
  (constants drift fails); AND the S1b corpus-fastpath contract
  (`TestCorpusFastpath`): fastpath must actually be taken
  (Corpus.load invoked — the silent walk fallback would leave the
  spy empty) and derive the SAME events as the walk arm.
- **R12 done** — `_SAMPLED_MEM_BUDGET_BYTES`/`_DENSE_GEN_MAX_N`/
  `connected_rand`/`connected_rand_sparse`/`roots_chunk` hoisted to
  module level (generators lazy-import scipy per the module's pattern);
  9 tests in `tests/test_hyper_scale_bench.py` pin the budget bound,
  the 5000 ceiling, chunk≥1, the wall-posture block (~128 MB at 60k),
  and generator shape/parity.
- **R13 done (record-only)** — the record above IS the close.
- **R14 done (lanes guard; rest recorded)** — `_check_lanes` guard in
  harvest_conversations.py (ValueError on unknown names; wired into both
  `harvest()` and `_drift()`), 3 tests in test_dsh_lane.py. Remaining
  NIT bullets closed as recorded: workers-cap probe optional (skip —
  probe-verified real), sharding waiver (11.5× below trigger is fine),
  bipartite_communities keep (thin but real chain lane), docstring/stub
  drift + signal_sweep parked for a future nit sweep, stale-discovery
  false-liveness recorded (mitigated: path match + wire token auth).

Validation: targeted batteries only (never make pytest) —
test_duckdb_shared 31, test_hyper_scale_bench 9, test_dsh_lane 38,
test_refresh_chain_smoke 4, test_enrich_relations CacheAgeGuard 9,
test_integration_maint_chain contract+corpus+idempotence+tier1 4;
ruff check + format clean on every touched file; md-lint clean on every
touched doc. On archival, move to `../archive/<topic>/` with the
completed.md entry per the `proposals/README.md` checklist.
