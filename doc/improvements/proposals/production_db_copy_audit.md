---
title: "Audit and de-amplify production-DB copies (keep_all + backup lanes)"
status: proposed
filed: "2026-09-29"
executed: null
completed_md: null
area: "tests/, helpers/maintenance/"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Audit and de-amplify production-DB copies

**Date:** 2026-09-29 · **Status:** EXECUTED — S1–S4 DELIVERED SAME DAY ·
AWAITING `make qa` (parked with the session's other gates) ·
**Area:** `tests/`, `helpers/maintenance/`

> **Disposition: EXECUTED 2026-09-29.** All four slices delivered in one
> session; per-slice records inline below. Headline: the two keep_all
> amplifiers went from 30 backups ≈ 9.0 GiB + ~14.3 GiB total writes per
> run to 1 shared template + reflink clones ≈ 5.6 GiB writes (**−61%
> volume, −23% wall**: 38.08 → 29.31 s for the same 30 tests under
> `-n auto --dist=loadgroup`); note_writers' fixture fell 232.0 →
> 23.6 MiB (content-prune proven semantics-safe by writer read-surface
> analysis); the chokepoint now denies by default
> (`SANCTIONED_REQUESTORS`, operator-owned registry + 1:1 static check);
> the production lane is measured (1,506 MiB → 749 MiB zst, 16.56 s per
> run) with options recorded for the operator, no action taken. The
> census revisit trigger is armed in `doc/improvements/pending.md`.
> Frontmatter stays `status: proposed` until archival per
> `check_proposal_lifecycle` (archive = post-`make qa`).

## Motivation

The production stores are massive now (measured 2026-09-29):

| store | size |
|---|---|
| `convo_search.duckdb` | 820.5 MiB |
| `research.db` | 307.2 MiB |
| `embed_store.db` | 184.6 MiB |
| `convo_search_fts.db` | 102.0 MiB |
| `corpus.db` | 52.6 MiB |
| `graph.duckdb` (+ xdist twin) | 39.3 + 39.0 MiB |
| `doc_search.db` | 24.7 MiB |

Every site that copies or backs one of these up pays the full cost, and
the test side still has two function-scoped full-corpus amplifiers the
tmpfs era never inventoried (both `copy_production_db(..., keep_all=True)`):

| module (function-scoped) | tests | copies/run | writes | transient |
|---|---|---|---|---|
| `test_query_plans.py` | 10 | 10 × 307 MiB | **0** | ~3.0 GiB |
| `test_rebuild_schema.py` | 20 | 20 × 307 MiB | 12 | ~6.1 GiB |

~9 GiB of identical copies per full run — an order of magnitude past
everything §D2 fixed, previously invisible because each individual copy
is "sanctioned". That is the general lesson this proposal exists to
carry: **a sanction is sized against the data of its day, and nothing
re-audits it as the data grows** (operator framing, 2026-09-29: the
keep_all copies were trivial when sanctioned mid-bigger-work; the
footprint since grew ~100x). Copy/backup sites are the shape of
decision that silently compounds — hence a census with verdicts and a
growth trigger below, not a one-off cleanup. And the misattribution
is part of the pattern too: gate-timing growth was historically blamed
on graph growth and algorithm inefficiency (operator observation,
2026-09-29) — yet with graph and algorithms untouched, the live lane
went 187–308 s → 99 s warm / 144 s cold once the copy/tmp layer was
fixed (shared graph cache + disk tempdir, same day), and db_maint's
trio fell 105 s → 20.6 s at #307. The copy layer was a co-factor in
every one of those "algo" regressions; S1 therefore measures copy-phase
vs compute-phase time per leg so the next attribution is by
measurement. The read-only row is pure
waste (ten byte-identical files); the writer row needs isolation but
not necessarily provenance from a fresh backup each time.

Complete copy-site census (2026-09-29), so this never has to be
re-derived:

- **Test side, research.db**: `helpers.copy_production_db` (the
  chokepoint; `vacuum=True` by default for pruned copies since
  2026-09-29) ← `test_query_plans` + `test_rebuild_schema`
  (`keep_all=True`, the two amplifiers above), `test_graph`
  `_minimal_db` dead no-factory fallback (factory branch uses the
  shared `schema_template`). Inline: `test_integration_maint_chain`
  (VACUUMed, 10 MiB, allowlisted), `test_integration_note_writers`
  (VACUUM fix 2026-09-29; 232 MiB is retained data — its prune clears
  only entities/graph_edges), `tests/_tmp_hygiene.py` template builders
  (once per run, shared — the sanctioned pattern).
- **Test side, DuckDB stores**: none copy convo/agent/sources/graph
  directly; `test_db_maint_duckdb` copies the shared trimmed template.
- **Production lanes (by design, operator-owned)**: `db_maint.py`
  primary sqlite+duckdb backups plus five sidecar backups (convo pair,
  convo corpus tar, embed store, memory catch-all — tests opt out via
  `backup_sidecars=False`); `snapshot_db.py` snapshot exports;
  `rebuild_doc_search.py` pre-rebuild backup. These pay full-store cost
  per run against the 820 MiB convo pair et al.

## Slices

### S1 — verdict table, per site

Formalise the census into per-site verdicts (sanctioned / candidate /
flagged) with per-run transient measured under the real gate invocation
(`-n auto`, `--dist=loadgroup`), not the per-copy arithmetic above.
Split each affected leg's wall time into copy-phase vs test-phase
(gate_query `timing` views) — the standing corrective for the
blame-it-on-the-graph reflex above.
Includes the note_writers 232 MiB question: whether pruning content
tables for dropped companies is semantics-safe (needs the bsh/ssw/dci
writer read-surface analysis) or accepted as the fixture's real size.

**DELIVERED (verdict table, measured 2026-09-29 under
`-n auto --dist=loadgroup`, TMPDIR=/mnt/data/tmp):**

| site | scope | verdict | before → after |
|---|---|---|---|
| `test_query_plans` (10 tests) | was function | **FLAGGED → fixed (S2)** | 10 × 307 MiB backups ≈ 3.0 GiB, copy-phase ~1.07 s/test → 0 per-test copies, `mode=ro` open of the shared template |
| `test_rebuild_schema` (20 tests) | function | **FLAGGED → fixed (S3)** | 20 × 1.07 s backups → 20 × 0.017 s reflink clones (+WAL flip); copy volume 6.1 GiB → ~0 |
| `test_integration_note_writers` | module | **content-prune SAFE (below)** | 232.0 → **23.6 MiB** |
| `test_integration_maint_chain` | module | **SANCTIONED** (downsampler, operator 2026-09-29) | ~10 MiB VACUUMed, unchanged |
| `test_graph _minimal_db` fallback | call site | **SANCTIONED via registry** (dead in-suite: every caller passes the factory branch = schema-template copyfile) | now `requestor=`-gated |
| `_tmp_hygiene` template builders | once/run | **SANCTIONED** (the mechanism itself) | + `full_template` 1 × 307 MiB shared by S2+S3 |
| `copy_production_db` chokepoint | — | **GATED** | deny-by-default; `SANCTIONED_REQUESTORS` (operator-owned) + 1:1 static check |
| production backup lanes | per prod run | **OPERATOR POLICY (S4, measured)** | 1,506 MiB → 749 MiB zst, 16.56 s/run, no change made |

Copy-phase vs compute-phase split (the attribution corrective): in
`test_rebuild_schema` the copy-phase is now 0.017 s clone + WAL flip
(~0.05 s) against a compute-phase of 3.42–4.25 s per rebuild — the
compute side is ~95% of the leg and irreducible at full corpus; any
future timing regression in this leg is the rebuild machinery, not the
data layer. `test_query_plans` has no copy-phase left to regress.

**note_writers read-surface analysis (the 232 MiB question):**
content-table pruning IS semantics-safe here. The three writers' DB
read surfaces are: bsh → `entities` + `graph_edges`; ssw → `entities`;
dci → `entities`, `graph_edges`, `quotes` (and vault files —
`quote_counts` scans the whole quotes table but only
(kept-entity, edition) pairs are ever looked up, so dropped-company
rows are observationally dead). No test in the module queries any
content table (verified by search). The retained 232 MiB decomposed
as graph_analytics ≈ 100 MiB (never pruned at all), note_search FTS
≈ 78 MiB, company_metrics ≈ 26 MiB — all unread. Extended `_build_db`
prune (analytics/metrics/embeddings/identifiers/events FK-pruned,
quotes pruned to kept entities, hyper + note_search cleared) →
**23.6 MiB, 7/7 module tests green** (byte-identical convergence +
cited_in counts act as discriminators). Accepted as the fixture's real
size: 23.6 MiB, no further downsizing warranted.

### S2 — query_plans: share the corpus, drop the copies

Ten read-only tests over byte-identical full copies is pure waste. One
shared per-run full-corpus template (the §D2 mechanism — flock +
atomic `os.replace` publish, `keep_all` flavour) with each test
copyfile-ing from it, or a module-scoped read-only fixture. Expected:
10 × 307 MiB → 1 × 307 MiB (+ cheap copyfiles if per-test isolation is
still wanted), and the ~3 s backup cost paid once.

**DELIVERED — one step past the expected floor:** module-scoped
fixture hands every test the template path and tests open it
`mode=ro` — zero per-test copies at all (the acceptance's "+ copyfiles
if isolation is still wanted" turned out unnecessary: EXPLAIN-only
tests never isolate by writing). A write attempt now RAISES instead of
silently touching the shared file. Load-bearing detail found on the
way: a sqlite backup inherits the source's WAL header, and every
`mode=ro` open of a WAL file creates `-shm`/`-wal` sidecars the reader
can neither checkpoint nor remove — N workers would race sidecars onto
the shared template. `build_full_template` therefore normalises to
`journal_mode=DELETE` (verified: read-only opens then create no
sidecars; row counts identical to production). query_plans transient:
3.0 GiB → 0 (the template is shared with S3).

### S3 — rebuild_schema: isolation without provenance

The 20 mutating tests need per-test files, but a copyfile from a shared
per-run template gives the same isolation at ~10× the speed of a
backup+re-open — IF the rebuild machinery's assertions don't depend on
fresh-backup provenance. Verify; if a subset genuinely needs the full
corpus and others don't, split the fixture.

**DELIVERED — provenance answer, in writing:** NO assertion in the
module depends on fresh-backup provenance. The rebuild machinery reads
the file as a sqlite database (schema + rows + mtime-free stats); the
template is built from the same live DB each run by the same backup
API the fixture used — same schema, same rows, per-run freshness.
Every test either asserts pre==post counts (holds on any corpus),
DDL/index/trigger presence (schema), or post-rebuild write behavior;
the one mtime-adjacent CLI test documents itself as mtime-independent.
The fixture went one step further than planned: `reflink_or_copy`
(btrfs `/mnt/data` is reflink-capable) clones the template
metadata-only — measured per test: backup 1.07 s → copyfile 0.221 s →
**reflink clone 0.017 s, ~0.1 MiB written** (vs 307.2 MiB). The
clone flips to WAL post-clone (rebuild write-phase 3.50 s on WAL vs
4.15 s on DELETE; the TEMPLATE stays DELETE for sidecar-free sharing).
Under 4-worker contention the reflink path is fastest end-to-end:
same 30 tests (with S2) 38.08 s → 29.31 s; write volume ~14.3 GiB →
~5.6 GiB per run (the ~270 MiB/test the rebuild itself rewrites is
irreducible compute, not copy). Fallback to plain copyfile keeps
/tmp-tmpfs and foreign-CI behaviour identical. No fixture split
needed: every test genuinely uses the full corpus.

### S4 — production backup lanes: record, then operator decides

`db_maint`'s sidecar backups alone copy well over 1 GiB per production
run (convo pair 922 MiB, embed store 185 MiB, corpus tar). This slice
measures the lane's per-run cost and records options (zstd, incremental,
cadence, per-store opt-outs) — a policy decision, explicitly not an
implementation slice without the operator.

**DELIVERED (measurement only, no lane change):** replicating each
store's exact backup operation (same APIs, same zstd path) into
scratch: research 307.2 MiB → 105.6 zst (3.18 s); embed_store 184.6 →
136.1 (2.03 s); corpus 52.6 → 9.5 (0.61 s); convo_fts 102.0 → 45.6
(1.66 s); graph.duckdb 39.3 → 25.5 (0.41 s); **convo_search.duckdb
820.5 → 426.4 (8.68 s — 52% of lane time)**. Lane total: **1,506 MiB
read → 749 MiB zst written, 16.56 s per production run**; the convo
corpus tar lane is minor (144 parquet files, 38 MiB). Options recorded
for the operator, none implemented: (a) **skip-if-unchanged** — the
convo pair rewrites only on convo rebuilds but the lane re-pays
~10.3 s + 472 MiB zst per run unconditionally (size+mtime manifest
beside each `.zst`; biggest lever, zero semantics change); (b) cadence
split (sidecars daily, primaries per-run); (c) zstd level: leave
(the #174 no-level-switch policy; the whole lane is 16.6 s);
(d) per-store opt-out already exists (`backup_sidecars=False`); (e)
incremental/differential: overkill at 16.6 s full. Decision deferred to
the operator per the non-goals.

## Non-goals

- **No production backup semantics change without the operator** — S4
  records and proposes; it does not act.
- **The downsampler class stays** (`maint_chain`, `note_writers`) —
  operator-adjudicated VALID 2026-09-29; only the note_writers
  content-table question (S1) may revisit, and only on evidence.
- **No new shared template without a measurement** proving the win
  (S2/S3 carry their own numbers first).
- No `eval-gate` bullet: no query-visible semantics anywhere.

## Risks

- Shared full-corpus templates pin run-start production state: tests
  reading "current" production data see a per-run snapshot. Acceptable
  (same as every existing shared template) but must be stated where a
  test asserts liveness.
- A mutating test accidentally sharing the template FILE (not a copy)
  would corrupt every consumer — the flock+publish mechanism plus a
  copyfile-per-scope keeps writers off the template; the §D2 guards
  pattern extends.

## Acceptance

- [x] S1 verdict table recorded (per-site, measured under the gate)
- [x] S2: query_plans transient ≤ 1 full copy + copyfiles per run
      (delivered below the bound: 0 per-test copies; 1 shared template)
- [x] S3: rebuild_schema per-test cost drops from backup to copyfile,
      with the provenance question answered in writing (answered: no
      provenance dependency; mechanism landed as reflink clone with
      copyfile fallback)
- [x] S4: production lane per-run cost measured; options recorded for
      the operator; no unauthorised change
- [x] Revisit trigger recorded in pending.md: re-run the census when
      any production store roughly doubles (sanctions decay with data
      growth — the keep_all class proves it)
- [x] Operator addition (2026-09-29, mid-arc): the chokepoint denies by
      default — `copy_production_db(requestor=...)` raises
      `PermissionError` unless the requestor is a key of the
      operator-owned `SANCTIONED_REQUESTORS` registry (entry = reason
      + date); `test_static_checks` pins call sites and registry in
      1:1 lockstep so an unsanctioned site can't land silently. The
      sanctions-decay lesson, enforced: new copy sites get re-audited
      at the moment they appear.
- [ ] `make qa` clean (parked with the session's gates; run once on the
      operator's go, then archive)

## Follows

`archive/testing/pytest_tmpfs_amplification.md` (§D2 + the 2026-09-29
bug-class note — this proposal is its estate-wide continuation),
`helpers/maintenance/db_maint.py`, `helpers/maintenance/snapshot_db.py`.
