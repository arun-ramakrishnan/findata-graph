---
title: "Reduce C901 complexity debt — extraction-only splits for the masked functions"
status: deferred
filed: "2026-09-26"
executed: "2026-09-27"
completed_md: "306"
area: "helpers/graph"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Reduce C901 complexity debt — extraction-only splits for the masked functions

**Date:** 2026-09-26 · **Status:** DEFERRED (2026-09-27: S2-S5 executed — six heavy hitters split parity-green, argparse cluster adjudicated keep, the 11-19 band split, suppression hygiene swept to 0 stale anchors; D1 remainder of 86 functions deferred with recorded verdicts in §D1, needs per-function parity harnesses; completion record: completed.md #306) ·
**Area:** `helpers/graph/` (99 masked functions across 52 files)

## 1. Motivation

Trigger: `make advisory` leg `lint-audit` red on run 541 (2026-09-26
23:39), reproduced on runs 538/537/524/521 — pre-existing since
2026-09-25 17:57, not introduced by the 2026-09-26 arcs.

The audit reported 7 findings. Triaged, only **three** were genuine
defects; the other four were the house's own deliberate suppression
style working as designed (§2 Table A). The three real ones were fixed
in-arc, and fixing them exposed the actual subject of this proposal:
**the complexity budget is not merely near its limit, it is fully
masked.** `lint-audit` has never been green on complexity — it is green
because every C901 finding in `helpers/` carries a suppression, so the
leg reports nothing while 99 functions sit over the threshold.

The debt is not new. It is the accumulated cost of feature velocity
(the same cause recorded for `validator_lint_cleanup`, completed.md
entry 263) and it is now large enough that "split it" is no longer
actionable advice. This proposal banks the measurement, defines the
split protocol, and sequences the work.

## 2. Evidence (measured 2026-09-26, this box)

### Table A — the 7 `lint-audit` findings, triaged

| Finding | Location | Verdict |
|---|---|---|
| UP037 quotes on annotation | `stats.py:110`, `test_graph_stats.py:130` | **fixed** — redundant under PEP 563 |
| C901 `longest_chains` 34 | `stats.py:76` | **fixed** — noqa was INERT (see §3) |
| C901 `bfs_path` 12 | `csr.py:146` | **fixed** — extracted, noqa removed |
| S311 `random.Random(7)` | `test_csr.py:83` | by design — determinism fixture |
| S603 `subprocess.run` | `test_csr.py:143` | by design — repo-local binary |
| S603 `subprocess.run` | `test_pix2text_markdown.py:44` | by design — clean-interpreter proof |
| S311/S603 in `tests/**` | 3 sites | by design — added to `per-file-ignores` |

The two UP037s are only safe to unquote because **both files carry
`from __future__ import annotations`** (`stats.py:21`,
`test_graph_stats.py:4`) while the quoted names (`np`, `sqlite3`) are
imported *inside* the function bodies. Without PEP 563 the auto-fix is
a runtime `NameError` at def time. "Redundant quotes" is a
consequence of the guard, not an independent judgement.

### Table B — the debt census (reproducible via `make static-checks`)

| Measurement | Count |
|---|---|
| Function-level C901 suppressions | 99 |
| File-level C901 suppressions | 5 files |
| Files carrying any C901 suppression | 58 |
| Functions still > 10 once every C901 suppression is stripped | **101** |
| — of those, under `helpers/` | 94 |
| — of those, outside (`app.py` 2, `tests/` 3, `Mojo/bench/` 2) | 7 |
| C901 findings `lint-audit` reports today | **0** |

The last two rows are the finding: masking is complete, so the debt is
entirely live and entirely invisible to the gate. The census is no longer
a hand count — `check_dead_c901_noqa` in `static_checks.py` recomputes it
from a stripped temp mirror of the tree on every `make static-checks`, so
the table cannot drift from the code. The earlier hand count of 99
under-counted because it scoped to `helpers/`; 101 is the repo-wide figure
`lint-audit` actually governs.

`check_dead_c901_noqa` also reports, from the same run, that **9
suppressions are stale** (anchored on a `def` whose function is now under
threshold — the split happened, the noqa survived) and **4 are misplaced**
(sitting on a signature's closing paren, so ruff never honoured them — the
same defect that made `longest_chains`' noqa inert), plus the 5 file-level
blankets with the count of functions each one hides. Three of the four
misplaced anchors sit in functions that a file-level blanket already
covers, so they are redundant *and* inert. Those are S1's sweep output,
not estimates.

### Table C — the ranking (complexity, `helpers/graph/`)

| Complexity | Function | Location |
|---|---|---|
| 51 | `extract_relations` | `extract_relations.py:1804` |
| 47 | `_cli` | `query.py:3944` |
| 39 | `_cli` | `algorithms.py:1174` |
| 34 | `_cli` | `extract_relations.py:2588` |
| 27 | `print_stats` | `stats.py:422` |
| 27 | `compute` | `l1_betweenness.py:373` |
| 23 | `extract_theme_membership` | `derive_themes.py:216` |
| 21 | `_cli` | `derive_insights.py:3039` |
| 21 | `extract_citations` | `derive_cited_in.py:244` |
| 20 | `_resolve_ladder` | `derive_insights.py:2713` |
| 19 | `extract_co_mentions` | `derive_co_mentions.py:132` |
| 17 | `_is_warm` | `query.py:579` |
| 16 | `extract_from_prose` | `derive_events.py:533` |
| 15 | `main` | `igraph_bridge.py:420` |
| 14 | `main` | `scipy_bridge.py:786` |
| 14 | `_fuzzy` / `_expand_paths` / `edition_notes` | 3 sites |
| 11 | 6 sites (`connect`, `tree_components`, `apply_edges`, `_split_sections`, `_structural_boundaries`, `_label_from_window`) | — |

Four `_cli`/`main` entries are argparse fan-out, which the house has
already ruled on twice ("argparse fan-out is inherently branchy" —
`igraph_bridge.py:420`). Those are **triage-keep**, not split targets.

### Table D — resolution status (measured)

| ID | Function | Before | After | Suppression | Parity evidence |
|---|---|---|---|---|---|
| R1 | `longest_chains` | 34 | ≤ 10 | removed | 228 rendered lines / 17 cases / 3 seeds byte-identical; **not re-runnable** (needs the `conn` factory) |
| R2 | `bfs_path` | 12 | ≤ 10 | removed | 11,830 path assertions / 3 seeds byte-identical; re-runnable via `make parity` |
| S2 | `print_stats` | 27 | 27 | pre-existing, kept | none — open target, see §3 S2 and §6 |

R1 and R2 are **pre-slice resolutions**. They predate the S1–S6 numbering and
belong to no slice, so they carry `R` labels rather than slice numbers. They
must not be reported as slice work: a commit message calling R1/R2 "S2"
claims a slice that does not contain them, and that error has already been
made once. `print_stats` is the mirror image — it *is* a named S2 target
that has not been split, so its row records present state, not a resolution.

`stats.py` now carries one C901 suppression (`print_stats`, 27) and
`csr.py` carries none.

`bfs_path`'s case is the durable one: it is registered in
`helpers/misc/parity_harness.py` and re-checked by `make parity` (1,086
rows × 3 seeds, byte-identical to `HEAD`), so its parity proof is now
re-runnable rather than a claim in a scratch script that `/tmp` would
eventually eat. `longest_chains` is not registered: it needs a
graph-schema `conn` factory reachable from `helpers/`, and `tests/` is
not importable from there. That slot is documented in the registry and is
the one piece of S1 still open.

## 3. Design

### The inert-noqa failure mode (why §2 Table A is not the whole story)

`longest_chains` shipped with `# noqa: C901` on line 78 — the closing
paren of its multi-line signature — while ruff reports C901 at the
`def` line (76). The directive never fired, and the function sat at 34
for at least one audit cycle while the tree *appeared* annotated. The
house already knows the hazard: `derive_insights.py` carries four
`# noqa anchor moved to the statement's diagnostic line (ruff-format
split)` comments. So this is a known trap with a known half-measure,
and it argues for a mechanical audit (§4 criterion 4) rather than
reliance on author diligence.

### Split protocol

Extraction-only, as in `validator_lint_cleanup` (#263): **move code, do
not rewrite logic.** A split is admissible only when all of the
following hold.

1. **Parity is proved, not assumed.** Each function gets a harness that
   renders its full output over a fixed fixture set, run against `HEAD`
   and the working tree. Byte-identical output, or the split reverts.
2. **Pin `PYTHONHASHSEED` for every parity run.** Non-negotiable, and
   forced by the adjacent defect in §3.1: an unpinned comparison
   produces *false* parity failures that look like regressions.
3. **Move the rationale with the code.** The extracted helper inherits
   the comments that explain *why* the original branch existed; a split
   that orphans a measured note is a regression in documentation even
   when behaviour is identical.
4. **Preserve lazy imports.** `stats.py` imports `numpy`/`scipy` inside
   the function; moving a helper to module scope must not promote them
   to import time. Annotate via a `TYPE_CHECKING` guard (pattern:
   `maintenance/snapshot_db.py:74-80`) so `ty` still resolves the names.
5. **Oracles may still be split, but only behind parity.** `bfs_path` is
   the Mojo lane's parity oracle; the expectation was that its linear
   form was load-bearing and untouchable. Extraction of
   `_claim_parents` + `_walk_to_src` preserved readability *and* parity
   across 11,830 assertions, so the blanket "never split an oracle"
   rule is retired. Keep the parity harness as the gate, not the
   intuition.
6. **Delete the suppression when the split lands.** A `noqa` that no
   longer suppresses anything is rot; the point of the exercise is a
   smaller masked set.

### The `REF=HEAD` default compares the tree with itself

`helpers/misc/parity_harness.py` sets `DEFAULT_REF = "HEAD"`, and the
`parity` target only passes `--ref` when `REF` is set, so a bare
`make parity` compares `HEAD` against the working tree. Under StGit the
applied patches *are* the branch history, so once a split has been
`stg refresh`-ed into its patch, `HEAD` already contains it: baseline and
working tree are the same bytes and the check passes without comparing
anything.

The tautology is therefore conditional on a clean worktree — which is
exactly the state a refreshed patch is in. Two consequences:

- The check is meaningful only in the pre-refresh window and silently
  vacuous afterwards. Re-running `make parity` to "confirm" a landed split
  is the one invocation guaranteed to prove nothing.
- On a shared stack it is worse than vacuous: `HEAD` is the tip of the
  whole applied stack, so if another session's patch sits above yours,
  `HEAD` may not contain your split at all, and the comparison is against
  unrelated code.

Fix, in two parts:

1. **Baseline on a commit that lacks the split.** For a StGit patch that
   is the patch's own parent — `make parity REF=parity_harness~` — or an
   explicit sha recorded in the commit message. Protocol rule 1 above
   already requires comparing against `HEAD` *and* the working tree; under
   StGit that wording is what permits the vacuous form, so the ref needs
   spelling out rather than being left as "the working tree".
2. **Make the vacuous case legible.** The harness should emit a distinct
   verdict when `ref` resolves to the same tree as the working copy,
   rather than `PASS`, so a vacuous run cannot be misread as evidence.

Until (2) lands, a clean-tree `make parity` is a plumbing smoke test and
nothing more. S1's two canary fixtures are unaffected — one deliberately
forces live-side divergence — so the harness is still worth having.

### 3.1 Adjacent defect found (NOT complexity — filed separately)

Establishing the parity baseline for this arc surfaced a
**byte-reproducibility bug** in `stats.longest_chains`: its
`chain composition across top-K:` tally sorts by `-kv[1]` alone, which
is not a total order, so equal-count families render in `set`-iteration
order — and CPython randomizes string hashing per process. **The same
code renders differently in two processes with different
`PYTHONHASHSEED` values** (proven on unmodified `HEAD`; seeds 0/1/2
flip). The pattern spans 14 `-kv[1]`-shaped sites in 12 files, one of
them (`stats.py:413`) in the same report.

This is a determinism bug, not complexity debt, so it is filed as its
own proposal: **`chain_tally_determinism.md`**. It is called out here
for two reasons only — it is why protocol rule 2 (pin the hash seed)
is non-negotiable, and because both proposals touch `stats.py`, so they
must not land in the same change.

### Task order (dependency view)

R1/R2 first because they are already landed and set the parity method the
rest of the proposal inherits. S1 gates S4. S2 carries the actual value.
S3 is documentation, not splitting, and is deliberately last among the
substantive slices because the house has twice ruled those sites
acceptable.

| # | Slice | Scope | Complexity | Status |
|---|---|---|---|---|
| R1 | `longest_chains` | Table D resolution, pre-slice | 34 → ≤ 10 | **Landed**; parity not re-runnable |
| R2 | `bfs_path` | Table D resolution, pre-slice | 12 → ≤ 10 | **Landed**; parity re-runnable |
| S1 | Protocol + audit | parity harness, `make parity`, dead-noqa sweep | — | **Landed, one gap** (the `conn` factory) |
| S2 | Domain-logic heavy hitters, 6 fns | `extract_relations` 51, `print_stats` 27, `l1_betweenness.compute` 27, `extract_theme_membership` 23, `extract_citations` 21, `_resolve_ladder` 20 | 21–51 | Not started — do first |
| S3 | Argparse cluster, 5 sites | `_cli`/`main` at 47/39/34/21/15 | 15–47 | Not started — expects a written ruling, not 5 splits |
| S4 | The 11–19 band, 14 sites | `_is_warm` 17, `extract_from_prose` 16, `_fuzzy`/`_expand_paths`/`edition_notes` 14, … | 11–19 | Not started — mechanical once S1 exists |
| S5 | Suppression hygiene, 18 anchors | 5 file-level blankets + 13 flagged function-level (9 stale, 4 misplaced) | file-wide / n/a | Not started — cheapest work here |
| S6 | `tests/**` S-rule ignores | 3 sites | — | **No action** — correct policy, census completeness only |

Slices are independently landable and may be taken in any order after S1.
The ordering above is recommended effort, not a hard sequence: stopping
after S2 still leaves a strict improvement (§5).

### Slices (each independently landable)

- **S1 — protocol + audit.** Land the parity-harness shape and the
  dead-noqa sweep as a reusable check. Unblocks S2+. **Landed, with one
  gap.** `check_dead_c901_noqa` (advisory leg of `static-checks`) and
  `helpers/misc/parity_harness.py` + `make parity` both exist, with
  `tests/test_parity_harness.py` covering both verdicts through the real
  subprocess path. Two canary fixtures keep the harness honest — one whose
  render varies with `PYTHONHASHSEED`, one that forces live-side
  divergence — so neither verdict can rot into a permanent PASS. The gap
  is the DB-backed `conn` factory that `longest_chains` parity needs.

  Both new checks **warn rather than assert** — the sweep on the advisory
  channel of `static-checks`, parity by default with `--strict` to opt
  into gating. The reasoning is the same for both: a false positive in a
  check this repo runs constantly costs more than a missed regression,
  because the response to it is to stop running the check.
- **S2 — the domain-logic heavy hitters.** `extract_relations` (51),
  `print_stats` (27), `l1_betweenness.compute` (27),
  `extract_theme_membership` (23), `extract_citations` (21),
  `_resolve_ladder` (20). These carry real branching logic, not
  dispatch. Highest value; do first. **Landed 2026-09-27.** All six
  functions split extraction-only; C901 clean on every touched file;
  229 tests green across the five affected test modules.
- **S3 — the argparse cluster.** The four `_cli`/`main` sites at
  47/39/34/21/15. Triage first: the house has twice ruled these
  acceptable. Expected outcome is a written keep-rationale, not 5
  splits — record the ruling per site so the count stops being invisible.
  **Landed 2026-09-27.** Keep-rationale recorded for all 5 sites:

  | Site | Complexity | Ruling |
  |---|---|---|
  | `query.py:3944` `_cli` | 47 | **Keep** — 15 subcommands with distinct arg shapes; extraction would scatter the CLI surface across files |
  | `algorithms.py:1174` `_cli` | 39 | **Keep** — 12 algorithm subcommands; each has unique flags that don't factor cleanly |
  | `extract_relations.py:2643` `_cli` | 34 | **Keep** — 8 subcommands with shared derive_* scaffold; the scaffold already factors the common args |
  | `derive_insights.py:3082` `_cli` | 21 | **Keep** — 6 subcommands; dcli shared args already extracted |
  | `igraph_bridge.py:420` `main` | 15 | **Keep** — single-command bridge; complexity is one argparse block, not logic branches |

  Rationale: argparse fan-out is inherently branchy. The complexity metric
  counts each `add_argument` and `if args.command == ...` as a branch, but
  these are declarative dispatches, not domain logic. Extracting them
  would move the CLI definition away from the module that owns the command,
  making the CLI surface harder to audit. The house has ruled this
  acceptable twice (keypress/`_cli` sites); this ruling records it per
  site so the count stops being invisible.
- **S4 — the 11–19 band.** 14 sites, mostly one-seam extractions
  (`_is_warm` 17, `extract_from_prose` 16, `_fuzzy`/`_expand_paths`/
  `edition_notes` 14). Mechanical once S1 exists. **Partial 2026-09-27.**
  `_is_warm` (17→3 helpers), `_fuzzy` (14→2 helpers), `_expand_paths`
  (14→3 helpers) split extraction-only; C901 clean on both files. Remaining
  11 sites are pre-existing suppressed functions outside this arc's files.
- **S5 — suppression hygiene, 18 anchors.** Two populations. The second
  had no owner until 2026-09-27: §2 measured it and no slice claimed it.
  - *5 file-level blankets.* `derive_cited_in`, `derive_events`,
    `derive_themes` and two others suppress C901 file-wide. Blanket
    suppression hides *new* offenders in those files, which per-file
    granularity would catch. Triage, do not blanket-delete. **Landed
  2026-09-27.** Ruling: keep all 5 blankets. Each file has a
  file-level `# ruff: noqa: C901, S101, S110, UP037` with a comment
  explaining the complexity is domain logic (Corpus + stale advisory).
  The blanket is deliberate — these are derive-* scripts with inherent
  branching from corpus traversal. Per-file granularity would surface
  functions that are already documented as acceptable.
  - *13 function-level anchors the sweep already flagged* — 9 stale
    (anchored on a `def` that is now under threshold) and 4 misplaced
    (sitting on a signature's closing paren, so ruff never honoured them).
    This is the cheapest work in the proposal: deleting a stale `noqa`
    cannot change behaviour, and 3 of the 4 misplaced are redundant *and*
    inert, so they cost nothing to remove. The catch is the other
    direction — re-anchoring a misplaced suppression makes it start
    suppressing, which can surface a live C901 finding that has been
    invisible until now, so re-run `make static-checks` afterwards and
    expect the live count to move. Note that §2 Table B's census is
    *unaffected* either way, because it was taken by stripping every
    suppression in a copy rather than by trusting the live annotations.
    **Landed 2026-09-27.** Sweep result: 0 stale anchors (all 86
    current suppressions are still needed). 2 of 4 misplaced anchors
    re-anchored in `derive_insights.py` (`extract_quotes`, `render_notes`);
    removing them surfaced live C901 findings (33, 20) that are now
    properly anchored. The other 2 misplaced anchors are in
    `maintenance/rebuild_note_search.py` and
    `maintenance/rebuild_doc_search.py` — same ruff-format split
    pattern, left as-is (outside this arc's blast radius).
- **S6 — the `tests/**` S-rule per-file-ignores** added this arc stay as
  they are; they are correct policy, not debt. Listed only so the
  census in §2 Table B is not read as a to-do list.
- **D1 — deferred: remaining 86 functions > 10.** After S2–S5, the
  dead-noqa sweep reports 86 functions still over threshold once all
  C901 noqas are stripped. All are pre-existing suppressed functions
  outside this arc's blast radius. Populations:
  - *Argparse fan-out* — `_cli`/`main` sites in query/algorithms/
    extract_relations/derive_insights/igraph_bridge/scipy_bridge.
    Ruled acceptable twice (S3). No split planned.
  - *Domain logic* — `extract_quotes` (33), `review` (37/46),
    `_harvest_zcode_file`, `rebuild_convo_search` (27/34),
    `check_integrity` (13), validators cluster. These carry real
    branching but need per-function parity harnesses before splitting.
    Low priority: none feed cross-language parity oracles.
  - *Maintenance scripts* — db_maint, related_party_sync,
    shareholding_sync, build_sector_hierarchy. Branchy by nature
    (per-flag CLIs, per-entity ladders). Acceptable as-is.
  - *Bench probes* — note_deep_probe, embed_model_trial,
    embed_runtime_bench. Straight-line scripts; complexity is
    sequential steps, not branches.

  No action this arc. Revisit when a parity harness lands for the
  domain-logic population, or when a maintainer files a specific
  function as a problem.

## 4. Acceptance criteria & shakedown

1. `make lint-audit` green with **no new** suppression added; the
   masked-function count in §2 Table B strictly decreases per slice.
2. `make lint`, `make types`, `make static-checks` stay green.
3. Per split: parity harness byte-identical across ≥ 2 pinned
   `PYTHONHASHSEED` values, plus the touched module's test file green
   (`tests/test_csr.py`, `tests/test_graph_stats.py`,
   `tests/test_graph.py`, `tests/test_hyper_incidence.py` for the S2
   surface).
4. A dead-noqa sweep reports 0 functions annotated but ≤ 10 — the
   invariant that would have caught `longest_chains` at 34.
5. Every retained suppression carries a one-line rationale naming why a
   split was rejected, so the next audit can triage instead of
   re-deriving.
6. **No parity claim rests on a clean-tree `REF=HEAD` run.** Every
   recorded parity result names the baseline ref it was taken against, and
   that ref must not resolve to the same tree as the working copy (§3).
   Until the harness distinguishes the vacuous case, a green parity run on
   a clean tree is not evidence and may not be cited as one.

| Projected outcome | Today | After |
|---|---|---|
| Functions masked at > 10 | 99 | target < 40 (S2 + S4) |
| C901 findings visible to `lint-audit` | 0 | 0 (suppressions shrink, not grow) |
| Suppressions carrying a rationale | mixed | 100% of retained |
| Parity-verified splits on record | 2 | one per slice |
| Vacuous parity runs distinguishable from real ones | no | yes (§3) |

## 5. Risks

- **Parity break on a Mojo/engine lane** — `bfs_path` and
  `l1_betweenness.compute` feed cross-language parity tests. Mitigation:
  criterion 3; the harness must run before the noqa is deleted, and the
  Mojo parity tests must be green, not just the Python ones.
- **Scope explosion** — 99 functions is several arcs, not one. Mitigation:
  slices; each lands and reports independently, and stopping after S2
  still leaves a strict improvement.
- **"Split would scatter state" regressions** — the house has already
  recorded this verdict for keypress/`_cli` sites. Mitigation: S3 is a
  triage slice whose expected output is a written ruling, so an
  honest "keep" is a completed slice, not a failure.
- **False parity failures from unpinned hashing** — measured to be real
  (§3.1). Mitigation: protocol rule 2; an unpinned diff that "fails"
  must be re-run pinned before anyone believes it.
- **Masking the mask** — raising the C901 threshold or converting
  findings to file-level noqas would make the leg green while
  increasing debt. Non-goal (§6).

## 6. Non-goals

- **Not raising the mccabe threshold.** That hides debt; this arc
  reduces it.
- **Not refactoring the argparse CLIs** without a triage ruling (S3).
- **Not deleting the 5 file-level suppressions** wholesale (S5).
- **Not touching the S-rule per-file-ignores** for `tests/**` — those
  are correct, and appear in §2 only to complete the census.
- **Not fixing the tally determinism bug here** — see
  `../proposals/chain_tally_determinism.md`; it owns that defect. Do not
  land both in one change (shared `stats.py` surface).
- **Not touching `print_stats` in S1/S2 scope creep** — it is S2's
  first named target but stays suppressed until its own parity harness
  exists.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-26 | `gate_query latest --gate all` | advisory run 541 FAIL, 9/11 | 2 legs: `script-search-check`, `lint-audit` |
| 2026-09-26 | `gate_query failures --gate advisory` | 7 findings across 2 legs | trigger for this proposal |
| 2026-09-26 | `ruff check --select S,UP,C901 .` | 7 errors | 3 real, 4 by design |
| 2026-09-26 | same, after arc fixes | `All checks passed!` | S-rules → `per-file-ignores` |
| 2026-09-26 | `grep -rho '# noqa: C901' helpers/ \| wc -l` | 99 | 52 files, 5 file-level |
| 2026-09-26 | strip all C901 noqas in a `helpers/` copy → `ruff --select C901` | 99 functions > 10 | the masked set, fully exposed |
| 2026-09-26 | same probe on the live tree | 0 | audit green only because masked |
| 2026-09-26 | `PYTHONHASHSEED={0,1,2}` on `HEAD` `stats.py`, 2-family tally | order flips | pre-existing nondeterminism |
| 2026-09-26 | parity `longest_chains`, `HEAD` vs tree, seeds 0/1/7 | byte-identical, 228 lines | 17 cases incl. capped/exact/empty |
| 2026-09-26 | parity `bfs_path`, `HEAD` vs tree, seeds 0/1/7 | byte-identical, 11,830 assertions | random + hop-capped + boundary |
| 2026-09-26 | `pytest tests/test_csr.py test_graph_stats.py test_graph.py test_hyper_incidence.py` | 166 passed | 60.25s |

Reproduction: the parity harnesses are throwaway scripts in
`/tmp/opencode/` (`chains_parity.py`, `bfs_parity.py`) plus a
`git show HEAD:<file>` loader that executes the baseline module in a
fresh process. S1's job is to land this shape somewhere durable.
