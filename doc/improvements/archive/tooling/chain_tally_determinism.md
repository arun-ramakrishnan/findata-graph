---
title: "Chain-tally determinism — PYTHONHASHSEED-flipping renders and the bare -kv[1] shape"
status: executed
filed: "2026-09-26"
executed: "2026-09-27"
completed_md: "305"
area: "helpers/graph/stats.py + 13 files; guard in helpers/validators/static_checks.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Make count-ranked tallies byte-reproducible — total-order tiebreaks

**Date:** 2026-09-26 · **Status:** EXECUTED (2026-09-27; S1+S2+S3 landed — 18 tiebreak edits across 14 files, S3 guard in `static_checks.py` + 5 tests; the guard's first full-repo run caught a 19th site in `check_dead_c901_noqa`'s own advisory render, fixed with it) ·
**Area:** `helpers/graph/stats.py` (proven) + 14 `-kv[1]`-shaped sites in
12 files (audit)

## 1. Motivation

Found while establishing the parity baseline for the C901 debt arc
(`../proposals/c901_complexity_debt.md` §3.1). The first
`longest_chains` before/after diff reported a **divergence** on two
lines. It was not a regression: the refactor was byte-identical, and the
"divergence" was the same code rendering differently in two processes
with different `PYTHONHASHSEED` values.

That is the real defect. A rendered report whose line order depends on
per-process string-hash randomization is **not byte-reproducible run to
run**, which means:

1. Any consumer that diffs rendered output — snapshot checks, report
   gates, a human bisecting a bad render — sees phantom changes.
2. **Refactor verification becomes unreliable.** It cost this arc a
   false alarm on an otherwise clean 228-line parity run, and the
   recovery (re-run pinned) is not something a future session will
   necessarily know to do.

The proximate cause is a sort key that is not a total order.

## 2. Evidence (measured 2026-09-26, this box)

### The mechanism

`stats.longest_chains` renders its tally as:

```python
", ".join(f"{k} x{v}" for k, v in sorted(family_tally.items(), key=lambda kv: -kv[1]))
```

`-kv[1]` orders by count only, so equal-count entries keep
`family_tally` **insertion** order. Insertion order is fed by
`for fam in pair_types.get(key, {"?"})` — iteration over a `set` of
strings. CPython randomizes string hashing per process, so the
insertion order of equal-count families varies per run.

### Table A — the flip is reproducible on unmodified `HEAD`

| `PYTHONHASHSEED` | Rendered `chain composition across top-1:` |
|---|---|
| 0 | `subsidiary_of x1, supplier_of x1` |
| 1 | `supplier_of x1, subsidiary_of x1` |
| 2 | `supplier_of x1, subsidiary_of x1` |

Confirmed against `git show HEAD:helpers/graph/stats.py` in a fresh
process, so it is **pre-existing** and not an artifact of the refactor
under test.

### Table B — the fix is a name tiebreak, and it is order-only

Adding `kv[0]` as a secondary key makes the order total:

```python
key = lambda kv: (-kv[1], kv[0])
```

| Property | Result |
|---|---|
| Byte-identical across seeds 0/1/2/3/7 | **yes** (was: no) |
| Multiset of `(family, count)` per tally line unchanged vs `HEAD` | **yes**, all 5 seeds |

The second row is the load-bearing one: the fix reorders equal-count
entries and changes **no count**. This was verified by normalizing the
token order within each tally line before comparing — an earlier
whole-line `sort` check was itself wrong (it normalized across lines,
not within them) and briefly showed a false content difference.

### Table C — the pattern is repo-wide (14 sites, 12 files)

| Site | Feeds rendered output? | Insertion order source |
|---|---|---|
| `graph/stats.py:347` (chains tally) | yes — the proven case | `set` of edge families |
| `graph/stats.py:413` (capture-quality tally) | yes — **same report** | SQL row order (no `ORDER BY` at `stats.py:405`) |
| `core/sync_tags.py:500,503` | yes | tag/sector scan |
| `web/prefab_views.py:109` | yes (web) | counts dict |
| `graph/algorithms.py:1134,1144` | yes | bucket scan |
| `graph/hyper_centralities.py:286` | yes | `values` dict |
| `graph/derive_hyperedges.py:966,983` | yes | tuple list |
| `misc/seed_nic2008.py:1122` | report | `cin` dict |
| `maintenance/enrich_relations.py:501` | report | industries scan |
| `maintenance/geo_converge.py:244` | report | kinds scan |

Severity is **not** uniform. The key is only a total order by accident
when no two entries tie; and even with ties the output is stable if the
source dict was built by iterating a sorted/ordered structure. So a
site is observably nondeterministic only when **both** hold: ties exist,
**and** the dict is filled from a hash-ordered or DB-unordered source.
`stats.py:347` satisfies both. The others need a per-site verdict rather
than a blanket edit.

## 3. Design

Make the sort key total by tiebreaking on the name:

```python
key = lambda kv: (-kv[1], kv[0])
```

Rationale: minimal, local, order-only, and it makes the rendered output
a pure function of the data rather than of the interpreter's hash seed.
The alternative of sorting the *dict population* (iterate a sorted list
instead of a set) fixes the symptom at the source but requires the
insertion path to be identified per site — which is exactly the audit
S2 has to do anyway, and it leaves the fragile sort key in place for the
next caller. Third alternative, emitting tally lines through a
`Counter.most_common()`, was rejected: it hides the ordering rule
rather than stating it.

### The durable fix is prevention, not 14 edits

Fourteen one-line edits will rot the same way the complexity budget
did. The recurrence needs a mechanical guard, which is the highest-value
slice here:

- **S1 — the proven pair.** `stats.py:347` + `:413`, both in the one
  rendered report. Add the tiebreak; add a test that renders under ≥ 2
  `PYTHONHASHSEED` values and asserts byte-equality. Smallest slice that
  closes the *proven* defect.
- **S2 — audit the remaining 12 sites.** Per site record: do ties occur,
  and is the source dict filled from a hash-ordered/DB-unordered
  structure? Verdict is one of `add tiebreak` / `already deterministic
  (why)` / `needs ORDER BY`. Sites already deterministic are a completed
  outcome, not skipped work.
- **S3 — prevent recurrence.** A `static_checks.py` assertion (or a
  narrow ruff rule) rejecting `sorted(<dict>.items(), key=…)` whose key
  expression is a bare negated single field, i.e. the `-kv[1]` shape,
  without a second element. The house already encodes checks of exactly
  this style (Proposal lifecycle, corpus_uniformity), so it has a home.

Ordering: S1 first (it is the proven bug), S2 and S3 independent after.

## 4. Acceptance criteria & shakedown

1. The `stats` report renders byte-identically under ≥ 3
   `PYTHONHASHSEED` values, verified by rendering the full transcript in
   a subprocess per seed and diffing.
2. Per tally line, the multiset of `(family, count)` pairs is unchanged
   versus `HEAD` — order-only, no count drifts.
3. `make lint`, `make types`, `make static-checks` green;
   `make lint-audit` stays green.
4. `tests/test_graph_stats.py` and `tests/test_hyper_incidence.py` green
   (both assert on `longest_chains` output).
5. S2 table complete: all 14 sites carry a verdict.
6. S3 rule landed and demonstrated to reject a synthetic bad key.

Shakedown (2026-09-27): criterion 6 exercised by five tests in
`tests/test_static_checks.py` (`test_bare_negated_sort_*`), including the
synthetic bad-key rejection and a live-repo-green pin. The S2 audit
concluded `add tiebreak` for every adjudicated site — no site met the
`already deterministic` bar cleanly enough to leave unguarded, so the
tiebreak was applied mechanically at all 18 call shapes listed in Table C
plus the four report-only sites (`misc/note_query.py:175`,
`misc/convo_query.py:201,227`, `maintenance/gc_embed_cache.py:280`).
Criterion 3's `make types` ran as `ty` (mypy not installed in this
checkout).

| Projected outcome | Today | After |
|---|---|---|
| Rendered lines that vary run to run | ≥ 1 proven, 13 unverified | 0 proven, all 14 adjudicated |
| Sites protected against recurrence | 0 | 14 (mechanical rule) |
| Refactor parity runs needing pinned seeds | ad-hoc discovery | documented + enforced by S1's test |

## 5. Risks

- **A golden/snapshot test has captured one tie order and will flip.**
  Expected for S1: the fix intentionally changes output. Mitigation: find
  those assertions deliberately and update them to the new order with the
  reason recorded — never blanket-rebaseline a snapshot to make a gate
  green.
- **The tiebreak could paper over deeper nondeterminism.** If some
  *content* also varied run to run, reordering would hide it. Mitigation:
  acceptance criterion 2 — a multiset comparison per line across seeds.
  A tiebreak that changed any count fails the slice.
- **S2 becomes an open-ended audit.** Mitigation: the per-site verdict
  explicitly allows "already deterministic", and the count of such
  verdicts is itself the deliverable.
- **S3's rule could be too broad** and reject legitimate
  single-field sorts of genuinely unique keys. Mitigation: scope the
  pattern to sorts over `.items()` of a mapping, and land it as advisory
  first if it misfires.

## 6. Non-goals

- **Not changing pair *selection*.** `_chain_pair_tiers` is already
  deterministic: `np.nonzero`/`np.argwhere` are row-major and the
  selection rule is documented as relying on that ordering
  (`stats.py`, "both orientations of a pair always get the same
  verdict"). Only the *tally rendering* is at issue.
- **Not the C901 work** — see `../proposals/c901_complexity_debt.md`.
  The only overlap is that both touch `stats.py`; order the two arcs so
  they do not collide in the same change.
- **Not a repo-wide `ORDER BY` policy** for SQL-fed tallies. If S2 finds
  a site where DB row order is the real source, fix that site; the
  blanket policy question is separate.
- **Not changing the report's wording, thresholds, or tie semantics** —
  this is an ordering fix only.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-26 | `chains_parity.py`, unpinned, two runs | 2 lines differed | false alarm during the C901 arc |
| 2026-09-26 | same, `PYTHONHASHSEED=0/1/2` on `HEAD` | order flips | proven pre-existing |
| 2026-09-26 | isolated 2-family tally, seeds 0–3 | flips at seed 1 | minimal repro, no DB needed |
| 2026-09-26 | tiebreak applied to a scratch copy, seeds 0/1/2/5/99 | stable | fix verified |
| 2026-09-26 | within-line multiset compare, seeds 0/1/2/3/7 | identical | order-only confirmed |
| 2026-09-26 | `grep -rn "key=lambda kv: -" helpers/` | 10 direct + 4 `-x[1]` variants | Table C site list |
| 2026-09-26 | read `stats.py:404-414` | `tally` filled from unordered SQL | second site in the same report |
