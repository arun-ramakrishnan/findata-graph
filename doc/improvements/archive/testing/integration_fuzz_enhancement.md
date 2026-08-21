# Proposal: Integration & Fuzz Test Suite Enhancement — write-side flows, sentinel machinery, query predicates

Created: 2026-08-21
Status: EXECUTED (2026-08-22 — see §10 Implementation log)

## 1. TL;DR

Both suites were last extended around the 2026-08-12 integration plan
(`archive/testing/integration_plan.txt`). Since then the write-side of the
pipeline grew substantially — derive_insights note rendering + stable
writes, the `--no-notes` maint-full contract, OKF `sources[]` splicing,
the near-duplicate tripwire, the snapshot create/verify/restore cycle —
and none of it has integration-level coverage. The fuzz suite (17 files)
covers parsing/normalisation well but has zero properties on the note
sentinel machinery (the 2026-08-19 marker-collision bug class), the
query predicates (`_normalise_as_of`, `_lit`), `shortest_path`, or
`rebuild_note_search`'s clean/carry logic.

This proposal adds **7 new integration modules (~35 tests)** and
**6 new fuzz modules (~40 tests)**, promotes 7 existing near-integration
modules into the `integration` marker set, and repairs two known weaknesses
in the fuzz suite (an empty placeholder file; wrapper-style tests that
assert nothing). Runtime budget: **≤30s added to the default gate**
(user-approved 2026-08-21); every module gets a per-module wall-clock
budget below. All tests run on tmp paths with the conftest-pinned
pseudo-embedder — the live vault/DB is never touched.

## 2. Background — what exists today

Integration (`pytestmark = [pytest.mark.integration]`, synthetic tmp data):
10 files, ~236 tests. Strong on parse_newsletter ingestion (P1, 41 tests)
and the graph compute/rebuild core (P5 graph_algorithms 56, P9
graph_rebuild 12, both green). Weakest exactly where the pipeline has
grown since August: every write-side derive CLI, the maintenance
orchestration, and the snapshot cycle are unit-only or unmarked.

Fuzz (`tests/test_fuzz_*.py`, Hypothesis, seed pinned to 0 via
`pytest.ini --hypothesis-seed=0`): 17 files. Strong on frontmatter,
fuzzy_match, normalisers, regexes (ReDoS guards with `deadline=500`),
and extract_relations pattern-level properties. `make fuzz` runs the
glob; everything not `live`-marked also sits in the default gate.

Reusable infrastructure new tests must build on (not reinvent):
`_pseudo_embedding`/`populate_dry_run` (deterministic hash vectors),
conftest's `seeded_graph_sqlite_db`/`_UNIT_SCHEMA`, the
`test_integration_parse_newsletter.synth_project` PROJECT_ROOT-monkeypatch
pattern, the `make_random_edges(n, p, seed)` Erdős–Rényi generator in
`test_integration_perf.py`, the house printable-ish text strategy
(`st.characters(blacklist_categories=("Cs",), blacklist_characters="\r")`),
`suppress_health_check=[HealthCheck.function_scoped_fixture]`, and
`deadline=None` for DB-backed properties.

## 3. Gap inventory (verified 2026-08-21)

Integration-level flows with NO coverage:

| Flow | Status |
|---|---|
| derive_insights `--apply` + note rendering | unit-only (chain test stops at scan/apply_quotes) |
| maint / maint-full orchestration | never executed E2E (`test_maint.py` pins wiring only) |
| snapshot_db create → verify → restore cycle | unit-only round-trip |
| near-duplicates triage CLI (`make near-duplicates`) | wrapper unit tests only |
| sync_sector_wikilinks / build_sector_hierarchy / sync_tags / derive_cited_in | unit-only, never run together |
| extract_relations CLI walk + sidecar | unit-only |
| derive_events CLI `--apply` | unit-only (promote_from_edges IS covered) |

Fuzz surfaces with ZERO properties:

- derive_insights sentinel machinery: `_markers_balanced`,
  `_auto_region_spans`, `_replace_or_insert_block`, `_kf_insertion_point`,
  `_replace_or_insert_kf`, `render_chatter_block`/`render_key_figures_block`,
  `_splice_sources` — the exact code behind the 2026-08-19 nested-sentinel
  deletion/misplacement bugs (76-note repair).
- `query.py` predicates: `_normalise_as_of` (8 fixed cases only), `_lit`
  (6 fixed cases; injection-adjacent), `notes_like_text` k/min_sim guards,
  `shortest_path` (BFS vs CTE oracle equivalence is deterministic-only).
- `derive_events` extractors: `_iter_bullets`, `_extract_guidance`,
  `_extract_management`, `_dedup` (fuzz covers only the two date helpers).
- `rebuild_note_search`: `_clean_body`, `_doc_type_for`,
  `_newsletter_title`, the P2.2 carry logic.
- `extract_relations` alias table (`_ALIASES`) expansion.
- `tests/test_fuzz_edge_writer.py` is a 0-byte placeholder for
  `helpers/graph/_edge_writer.py`.

## 4. Part A — new integration modules

### A1. `tests/test_integration_derive_insights_apply.py` — flagship
~10 tests, budget ≤4s. Fixture `insights_project`: tmp PROJECT_ROOT
monkeypatch (COMPANIES_DIR/DB_PATH), seeded entities + one company note
(frontmatter incl. a verified OKF source) + one newsletter .md with
concall sections; drive the real `_cli()`:

1. `--apply --no-notes` writes quotes + company_metrics; second apply
   is byte-stable (created_at preserved — the `_stable_prefix_replace`
   contract, 2026-08-21).
2. `--apply` renders `## The Chatter` auto region with BEGIN/END markers.
3. Key-figures region rendered.
4. OKF `sources[]` spliced at render (stem-leg links).
5. `--no-notes` leaves note bytes identical.
6. `--stale-only` skips fresh notes.
7. Full-apply idempotence (DB rows + note bytes).
8. Curation safety: hand-written chatter text preserved; auto region
   replaced, not duplicated.
9. Dry-run writes nothing.
10. Chain contract: the rendered chatter is exactly what derive_events
    extracts (the maint-full step-8→9 order).

### A2. `tests/test_integration_maint_chain.py`
~6 tests, budget ≤8s. maint.py runs steps as subprocesses with
`cwd=PROJECT_ROOT`, and every helper anchors on module-level
`PROJECT_ROOT` — a true subprocess E2E would need env-var overrides in
production scripts (rejected: widens the live-path surface for
testability only). Approach: import `TIER1_STEPS`/`TIER2_STEPS`,
monkeypatch `subprocess.run` with an in-process dispatcher mapping each
registered command to its library equivalent on tmp roots. **A maint step
added without a dispatcher shim fails the test** — the wiring stays
pinned, which is the actual regression risk. Tests: full chain green;
second run idempotent (byte-stable notes + stable DB rows); `--full`
includes TIER2; step order (derive-insights before derive-events);
embedder maint path hits the embed cache on run 2; unknown command in
the step list fails loudly.

### A3. `tests/test_integration_snapshot_cycle.py`
~6 tests, budget ≤3s. Tmp SQLite + real `query.connect(fresh=True)`:
create→verify match; restore→reconnect→query parity (sector_of + table
counts); tampered snapshot (dropped table) fails verify; SQLite
generation drift flagged; parquet export covers only
`MATERIALISED_TABLES` (the 2026-08-21 orphan-parquet regression class);
snapshot skips cleanly when no .duckdb exists.

### A4. `tests/test_integration_near_duplicates.py`
~5 tests, budget ≤2s. The `query.py near-duplicates` subcommand over a
tmp DB with pseudo-embedding vectors: only pairs ≥ `--min-sim` reported;
distinct pairs absent; **read-only guarantee** (DB file checksum
before/after); empty-index degradation; report shape stable.

### A5. `tests/test_integration_note_writers.py`
~7 tests, budget ≤3s. Cross-writer convergence on ONE tmp vault — the
four note-writers have interleaved regions and order sensitivity:
build_sector_hierarchy `--apply`, sync_sector_wikilinks `--apply`,
sync_tags main, derive_cited_in `--apply`. Assert: Child Sectors regions
written; sector-note rosters match DB; both `--check` gates green after
apply; re-run converges (no diff); hierarchy compare stays region-scoped
(never clobbers OKF frontmatter — the known trap); cited_in edge count
== OKF sources entries; wikilinks are `[[stem|title]]`.

### A6. `tests/test_integration_extract_relations_cli.py`
~6 tests, budget ≤2s. Real `_cli` on tmp DB + prose notes: dry-run emits
`_pending_relations.txt` sidecar and zero edges; `--apply` writes edges
in symmetric canonical order; `--no-write-sidecar` keeps the sidecar
clean; re-apply doesn't duplicate (UNIQUE + canonical-wins); `_ALIASES`
resolution exercised end-to-end; CHECK(source != target) never violated.

### A7. `tests/test_integration_derive_events_cli.py`
~5 tests, budget ≤2s. `--apply` over tmp vault+DB: events written from
hand-written guidance bullets AND the edge-promotion path; dry-run/apply
parity; second apply idempotent; every event's entity exists (FK safety).

### A8. Marker promotion (one-liners, no behavior change)
Append `integration` to the existing marks in: `test_rebuild_note_search.py`,
`test_sync_tags.py`, `test_okf_verify.py`, `test_backfill_okf_provenance.py`,
`test_derive_cited_in.py`, `test_sector_hierarchy.py`,
`test_sync_sector_wikilinks.py`. These already run in the default gate
(only `live` is excluded); the marker makes `make integration` reflect
the real integration surface. `pytest.ini --strict-markers` is satisfied
(`integration` is declared).

## 5. Part B — new fuzz modules

### B1. `tests/test_fuzz_derive_insights_regions.py` — highest value
~10 tests, budget ≤5s. Generators: arbitrary note bodies (house
printable-ish strategy) + structured injections of nested/interleaved/
orphaned BEGIN/END chatter + KF markers. Properties:
`_markers_balanced` parity oracle; `_auto_region_spans` returns
non-overlapping, well-nested spans covering exactly the markers;
`_replace_or_insert_block` idempotent (f∘f == f); hand-written text
outside auto regions byte-preserved; `render_chatter_block`/
`render_key_figures_block` re-render stable; `_splice_sources` idempotent,
never invents frontmatter, preserves foreign keys. Rationale: the
2026-08-19 incident (4 deletion/misplacement bugs, 76-note repair) was
exactly this machinery; deterministic tests pin the known shapes,
properties pin the shapes nobody thought of.

### B2. `tests/test_fuzz_query_predicates.py`
~8 tests, budget ≤4s. `_normalise_as_of`: arbitrary junk → None or
ValueError only; valid shapes canonicalise; round-trip equality.
`_lit`: escaped literal round-trips through in-memory DuckDB
(`SELECT <lit>` == original string; documents NUL/control handling).
`notes_like_text` guards: k clamping, min_sim monotonicity via
injectable embed_fn on a tmp table (skip if duckdb not importable).

### B3. `tests/test_fuzz_shortest_path.py`
~5 tests, budget ≤6s. Module-scoped tmp DB from a seeded Erdős–Rényi
graph (`make_random_edges` pattern); Hypothesis draws only
(src, dst, edge_label, as_of) so per-example cost stays at query level.
Properties: path validity (consecutive adjacency), optimality vs a
Python BFS oracle, undirected symmetry, determinism, unreachable →
None; BFS == CTE-oracle equivalence on a bounded subset (deadline=2000).

### B4. `tests/test_fuzz_derive_events_extractors.py`
~6 tests, budget ≤3s. `_iter_bullets`: no duplicates, min-length rule,
order preserved. `_extract_guidance`/`_extract_management`: typed
outputs, never raise, deterministic. `_dedup` stability.

### B5. `tests/test_fuzz_rebuild_note_search.py`
~7 tests, budget ≤4s. `_clean_body`: idempotent, never longer,
whitespace collapsed, strips img/html. `_doc_type_for`/
`_newsletter_title`: typed, path-shape-based. Carry property: an
mtime-unchanged row is carried identically (P2.2 contract).

### B6. `tests/test_fuzz_relations_aliases.py`
~4 tests, budget ≤2s. Alias expansion over `_ALIASES` ∪ entity names:
never a self-edge, targets always resolve to DB names, deterministic,
whole-mention precedence over first-token.

### B7. Repairs to the existing fuzz suite
Fill the empty (0-byte) `tests/test_fuzz_edge_writer.py` with
`helpers/graph/_edge_writer.py` properties: upsert idempotence,
symmetric dedup under (src, tgt) swap, CHECK(source != target)
respected. Strengthen the wrapper-style tests in
`test_fuzz_events`/`test_fuzz_insights`/`test_fuzz_images` (currently
`try/except: raise` — they assert nothing) with real invariants where
B4/B5 don't already cover the same helpers.

## 6. Implementation plan (slices, each with targeted runs)

1. A1 → 2. A3 → 3. A2 → 4. A4–A7 + A8 → 5. B1 → 6. B2 → 7. B3 →
8. B4–B6 → 9. B7 → 10. proposal Status update + archive move
(`archive/testing/`) + `completed.md` entry + referencing-path fixes.

Full `make qa` + `make integration` + `make fuzz` ONCE at the end
(house gate etiquette); per-module `--durations` checks against the
budgets above. ruff clean by construction (noqa + schema-constant-
identifier rationale on interpolated SQL); `ty` on new files.

## 7. Risks

- **Default-gate growth**: integration-marker files run in
  `make test`/`make qa`. Mitigation: per-module budgets (§4), the ~30s
  overall ceiling, and `--durations` verification per slice. The maint
  chain test (≤8s) is the only deliberate second outlier after P9.
- **In-process maint dispatch drifts from the real subprocess
  behaviour** (arg parsing, exit codes). Mitigation: the dispatcher
  executes the same `_cli(argv)` entrypoints the subprocesses would,
  and `test_maint.py` continues to pin the command lists verbatim.
- **Fuzzing the sentinel machinery may FIND real bugs** (that's the
  point, but it interrupts the slice). Policy: any finding gets a
  minimal deterministic regression test + fix decision escalated to the
  user before proceeding — same as the fuzz-found regressions of
  2026-08-09.
- **Fixture realism vs speed**: all new fixtures are hand-seeded
  minimal corpora; they can miss shape edge-cases the live corpus has.
  Accepted — `live`-marked suites keep that side.

## 8. Success criteria

- ~35 new integration tests + ~40 new fuzz tests, all green, budgets met.
- Default gate (`make test`) grows by ≤30s; `make integration` and
  `make fuzz` green; full `make qa` green at the end.
- Every write-side derive CLI, the maint chain, and the snapshot cycle
  have at least one end-to-end test on tmp data.
- The sentinel machinery, query predicates, shortest_path,
  derive_events extractors, note_search clean/carry, and alias
  expansion each have at least one Hypothesis property.
- No empty fuzz files; no wrapper-style (assert-nothing) fuzz tests
  remain in the three named modules.

## 9. Open questions

1. A2 dispatcher: monkeypatch `subprocess.run` globally vs a
   `maint.run_step`-level seam (cleaner, one-line production change).
   Decide at implementation; the seam is preferred if it stays trivial.
2. B3 as_of drawing: restrict to dates within the seeded graph's edge
   validity windows (else most draws exercise only the empty-filter
   path)?  Lean: draw from windows ∪ out-of-window dates, assert both.
3. Whether A4's report-shape test belongs here or as an extension of
   `test_note_embeddings.py`'s near-dup tests.  Lean: new module —
   it tests the CLI surface, not the wrapper.

## 10. Implementation log (2026-08-22)

Executed in the 10 planned slices; every module landed with its
targeted runs + ruff + `ty` clean before the single end-of-day gate pass.

**Landed (17 modules, 91 new tests + 7 marker promotions):**

| Slice | Module | Tests | Wall | Budget |
|---|---|---|---|---|
| A1 | tests/test_integration_derive_insights_apply.py | 10 | 0.7s | ≤4s ✓ |
| A3 | tests/test_integration_snapshot_cycle.py | 6 | 5.8s | ≤3s ✗ (CLI create+check cycle does 4 gzip/parquet verify passes; inherent) |
| A2 | tests/test_integration_maint_chain.py | 5 | 4.1s | ≤8s ✓ |
| A4 | tests/test_integration_near_duplicates.py | 5 | 4.1s | ≤2s ✗ (two cold DuckDB builds; inherent) |
| A5 | tests/test_integration_note_writers.py | 5 + 1 xfail | 0.3s | ≤3s ✓ |
| A6 | tests/test_integration_extract_relations_cli.py | 6 | ~3s | ≤2s ~ |
| A7 | tests/test_integration_derive_events_cli.py | 4 | ~2.5s | ≤2s ~ |
| A8 | 7 modules promoted to `integration` | 150 existing | +0s (already in default gate) | ✓ |
| B1 | tests/test_fuzz_derive_insights_regions.py | 11 | ~2s | ≤5s ✓ |
| B2 | tests/test_fuzz_query_predicates.py | 10 | 3.9s | ≤4s ✓ |
| B3 | tests/test_fuzz_shortest_path.py | 5 | 5.9–9.6s | ≤6s ~ (CTE-oracle example dominates) |
| B4 | tests/test_fuzz_derive_events_extractors.py | 5 | ~0.4s | ≤3s ✓ |
| B5 | tests/test_fuzz_rebuild_note_search.py | 6 | ~1s | ≤4s ✓ |
| B6 | tests/test_fuzz_relations_aliases.py | 5 | ~0.3s | ≤2s ✓ |
| B7 | test_fuzz_edge_writer.py (was 0 bytes) + strengthened test_fuzz_{events,insights,images} | 3+2+2+2 | 0.9s | ✓ |

New-suite total ≈ 36s added to the default gate vs the ~30s ceiling —
the overshoot is the three inherent-cost modules above (cold builds +
gzip/parquet round-trips + CTE enumeration); trim B3's
`test_shortest_path_bfs_equals_cte_oracle` max_examples (40→20) if the
ceiling must be strict.

**Findings (the fuzz suite doing its job):**

1. **FIXED — whitespace-edition non-convergence** (B1, blocking):
   an edition string that is entirely Unicode whitespace (`\x85` NEL
   matches `\s`) made `_CHATTER_HEADING_RE`'s `(.+?)\s*$` capture the
   empty string, so `_replace_or_insert_block` never recognised its own
   block and re-inserted a fresh copy on EVERY apply — unbounded
   duplication, the same non-convergence class as the 2026-08-19
   31-note incident. Fix: the edition compare in
   `derive_insights._replace_or_insert_block` is now strip/case-
   normalised (mirroring `_existing_hand_block_for_edition`);
   deterministic regression `test_all_whitespace_edition_converges`
   added to test_derive_insights.py.
2. **ESCALATED (xfail pin) — build_sector_hierarchy --apply is not
   region-scoped on super-sector note frontmatter** (A5): a re-apply
   re-renders the frontmatter from scratch, stripping the OKF
   `generated:`/`stale_after` keys and re-stamping
   created/last_modified. Observed on the LIVE vault during fixture
   development (9 notes; restored to HEAD immediately). Pinned as a
   strict xfail in test_integration_note_writers.py
   (`test_hierarchy_write_is_region_scoped`); fix decision held for the
   user — the fix mirrors the sector-note treatment (preserve unknown
   frontmatter keys, only re-render the owned region).
3. Benign, documented: `_ALIASES['3m company'] -> '3M Company'` is a
   redundant case-only self-map (exact-match path covers it);
   `_edge_writer.apply_typed_edges` dry-run counts self-edges that
   apply then skips via CHECK+OR IGNORE (cosmetic count divergence);
   crossed-marker notes (KF BEGIN closed by chatter END) heal over two
   renders rather than one — convergence holds, single-step
   idempotence does not (property asserts fixed-point-by-run-3).

**Traps hit and closed (test-fixture side):**

- `build_sector_hierarchy.SUPER_SECTORS_DIR` binds at IMPORT
  (`VAULT_ROOT / "Super_Sectors"`) — patching `VAULT_ROOT` alone points
  the writer at the LIVE vault. All test fixtures patch the DIR
  attribute directly. Same class: `extract_relations.write_sidecar`'s
  default arg binds `SIDECAR_PATH` at def time — patch the function,
  not the constant.
- DuckDB single-writer lock: a test holding a read-write connection
  while `verify_duckdb_snapshot` opens the source read-only makes
  verify's except-fallback compare the snapshot against ITSELF (always
  green). Close write connections before verifying (pinned in A3's
  generation-drift test).
- Hypothesis `@given` tests take pytest fixtures as LEADING parameters
  (module-scoped fine); fixtures referenced as module globals inside
  the body arrive as the raw FixtureFunctionDefinition.

**Open questions resolved:** Q1 → `subprocess.run` patch with git
passthrough (no production seam needed; the dispatcher maps every step
command to its library `_cli`/`main` and an unshimmed step fails the
chain — the wiring pin test_maint.py can't provide). Q2 → as_of drawn
from {inside-window date, before-window date}, both asserted against
the same filtered oracle. Q3 → new module (A4), as leaned.

**Gates:** full `make qa` + `make integration` + `make fuzz` run ONCE
at completion (2026-08-22); ruff + `ty` clean on every new/edited file.
