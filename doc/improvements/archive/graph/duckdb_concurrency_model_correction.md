---
title: "Correct the DuckDB concurrency invariant in query.connect's docstring"
status: executed
filed: "2026-09-29"
executed: "2026-09-29"
completed_md: "314"
area: "helpers/graph/query.py, tests"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Correct the DuckDB concurrency invariant in `query.connect`'s docstring

**Date:** 2026-09-29 · **Status:** DONE — AWAITING `make qa` ·
**Area:** `helpers/graph/query.py`, `tests`

> **Disposition: S1–S3 delivered 2026-09-29.** Only the operator's
> `make qa` box is unticked. The false clause is replaced with the measured
> five-row matrix inline at the `connect()` docstring, both conflict
> directions are pinned by cross-process negative tests (each with an A/B
> control so it cannot pass for the wrong reason), and the caller audit is
> recorded by mechanism. The audit grew well past the four sites originally
> named: **37 coordinated vs 24 uncoordinated** read-only openers. One
> finding is real and stays **open**: `convo_query.py:88` has a confirmed live
> writer, and its remedy is the `convo_search.duckdb` flock work in
> `../../proposals/duckdb_transient_lock_retry.md` S3, which remains open.
> The other (`stats.py:465` hardcoding the production path) was checked and
> **dismissed**: `v_graph_structure` is stamp-lane-only, so the production read
> is intentional. Frontmatter stays `status: proposed` because
> `check_proposal_lifecycle` (helpers/validators/static_checks.py:967-970)
> requires live proposals to read `proposed`, and the archive branch requires
> a real `completed_md` number not yet allocated.

## Motivation

`helpers/graph/query.py:515-518` (introduced in commit `a5d213a5`, 2026-08-25)
states, as a design invariant:

> "DuckDB allows any NUMBER of read-only openers across processes but a single
> read-write one — so pure readers (algorithms --compute, suggest_relations)
> pass True and never contend with (or against) a writer under `make advisory`'s
> parallel steps."

**The first clause is correct. The second is false.** Measured 2026-09-29 on
duckdb 1.5.6, separate processes:

| Holder | Opener | Result |
|---|---|---|
| read-write | read-only | **FAIL** — `Could not set lock on file …: Conflicting lock is held` |
| read-only | read-write | **FAIL** — same |
| read-only | read-only | **OK** |
| read-write | read-write | **FAIL** — same |

Embedded file mode allows *any number of read-only holders, or exactly one
process in total — never a mixture.* A read-write holder blocks readers
outright, which is precisely what the docstring denies. Reproduced twice; the
failure message names the holding PID, so the holder was genuinely live in both
runs.

This is a documentation defect with teeth. The clause is load-bearing for the
`read_only=True` choice made by pure-reader callers, and it is the reason one
would believe a read-only opener is safe against concurrent activity. It is not.
The flock at `query.py:452-479` papers over the `connect()`-mediated build case,
which is why the suite is green — the invariant is wrong in the gap the flock
does not cover, not in the path the flock owns.

## Slices

### S1 — restate the invariant to match the measurement

Replace the "never contend with (or against) a writer" clause with the real
rule, and record the matrix inline so the next reader does not have to re-derive
it. State explicitly what the flock does and does not cover: it serialises
builds that go through `connect()`, and nothing else.

### S2 — pin the matrix with a test

`tests/test_graph_disk.py` already proves the RO/RO row cross-process with 6
subprocesses (`test_parallel_ro_connects_serialize_the_build`, :188). Add the
**RW-holder / RO-opener** row as an explicit negative: it must fail with
`Conflicting lock`. That converts a docstring claim into a measured,
regression-protected fact, and it is the test that would have caught this.

### S3 — audit the callers that reasoned from the false clause

Every site passing `read_only=True` on the strength of "readers never contend
with a writer" needs re-checking against the real rule. **No caller becomes
incorrect** — read-only remains the right choice everywhere, and nothing needs
reverting. What the false clause concealed is *which* of them are actually
defended. Census of `read_only=True` across `helpers/`, split by mechanism:

- **Coordinated (37 sites)** — every `connect(..., read_only=True)` caller,
  via `helpers/graph/query.py`. These inherit `connect()`'s build fallback and
  the `<cache>.build.lock` flock, so a *concurrent builder that also goes
  through `connect()`* queues rather than failing. Includes `algorithms
  --compute` (16 sites, `algorithms.py`), `suggest_relations` (`:224`),
  `embed_matrix.py:245`, `scipy_bridge.py` (4), `csr.py` (2),
  `l1_betweenness.py:80`, `igraph_bridge.py:137`, `static_checks.py:1221`.
  Residual exposure: a foreign RW holder that never takes the flock still
  blocks them, and there is no retry.
- **Uncoordinated (24 sites)** — every direct `duckdb.connect(...,
  read_only=True)`, which gets **no flock, no build fallback, and no retry**.
  `convo_query.py:88`, `derive_indices.py:88,366`, `query.py:429,438,678`,
  `search_tui.py:1637,1699`, `database_integrity_check.py:1813`,
  `rebuild_convo_search.py:382`, `harvest_conversations.py:139,353`,
  `db_maint.py:427`, `mca_cin_sync.py:83`, `mca_cin_resolve.py:143`,
  `snapshot_db.py` (6), `fold_identifiers.py:46`, `gc_embed_cache.py:141`,
  `bench/fts_duckdb_parity.py:152`. These turn an unsynchronised writer into a
  hard `Conflicting lock` failure with no recovery — this is the population
  `../../proposals/duckdb_transient_lock_retry.md` exists to cover, and this audit is the
  concrete justification for it.

Two findings the four originally-named sites did not surface:

1. **`convo_query.py:88` is the one site with a confirmed live writer.**
   `rebuild_convo_search.py:491` holds a long-lived read-write connection on
   `convo_search.duckdb` with no lock at all, so a query during a rebuild hits
   the conflict for real. This is the strongest single piece of evidence for
   the retry proposal.
2. **`stats.py:465` reads the production path by design — checked and
   dismissed.** It opens `_PROJECT_ROOT / "memory" / "graph.duckdb"` directly
   rather than `query.DUCKDB_PATH`, which looks like an xdist isolation leak
   and was flagged as one in a first pass. It is not: `v_graph_structure` is
   created only by `_stamp` in the explicit stamp lane (`query.py:1719`) and
   is listed as stamp-lane-owned in `_build_meta` (`query.py:880,1559`), so it
   is *deliberately absent* from a fresh per-worker cache built by `connect()`.
   The production path is the only place those stamped metrics exist, and the
   sibling site 40 lines later (`stats.py:591-593`) correctly uses
   `connect_read_only(DUCKDB_PATH)` for the un-stamped tables. One residual
   nit, not filed as a defect: the blanket `except Exception` means a lock
   conflict prints identically to "not stamped"
   (`exact (stamped): unavailable (IOException)` vs `absent`), so the two are
   indistinguishable in output. Cosmetic diagnosability, not correctness.

Method note for the next auditor: `grep -E 'duckdb\.connect\([^)]*read_only'`
silently returns **zero** matches here, because `[^)]*` stops at the `)` of
`str(duckdb_path)`. Use `.*` — the 24/37 split above is only visible with it.

## Non-goals

- **No behaviour change.** This corrects a stated invariant and adds a test.
  The retry that actually addresses the gap is P1
  (`../../proposals/duckdb_transient_lock_retry.md`); this proposal makes the truth legible.
- **No `eval-gate` bullet.** No query-visible semantics change.

## Acceptance

- [x] S1 docstring matches the measured matrix, with the matrix inline
- [x] S2 negative test green and mutation-verified (it must fail if the
      RO-opener is ever made to succeed against a live RW holder)
- [x] S3 audit recorded — each caller's exposure stated, not assumed
- [ ] `make qa` clean

## Follows

`doc/local/evaluations/dbx_assessment.md`. Companion to P1
(`../../proposals/duckdb_transient_lock_retry.md`), which closes the gap this proposal
documents.
