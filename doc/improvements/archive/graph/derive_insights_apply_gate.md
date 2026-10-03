---
title: derive_insights apply-gate — duplicate collapse, skip guard, decision briefs, edition-entity ordering
status: executed
executed: '2026-10-04'
completed_md: '339'
filed: '2026-10-03'
area: graph
---

# Proposal: derive_insights apply-gate — duplicate collapse, skip guard, decision briefs, edition-entity ordering

**Date**: 2026-10-03
**Status**: S0 documented (`markdown_parse.md` Stage 11 + runbook
checklist). S1 DONE 2026-10-04 (deterministic duplicate-fact collapse:
`_canonical_metric_value` + `_collapse_metrics`; 8 tests). S2 DONE 2026-10-04
(_skip_guard; full-sentence gate, 3 tests). S3 DONE 2026-10-04
(decision_brief_for_rows; brief-before-write, 8 tests). S4 DONE 2026-10-04
(advisory enrichment on `TYPED_JUDGMENT=1`). S5 DONE (snapshot gate already
implemented in the tree). S6 Pinned 2026-10-04 (rubric key: label set /
question wording / carrier id + 15-row labeled seed key at
`doc/local/evaluations/jev_pilot/derive_metrics_gate_key.py`;
`dataset/derive_metrics_key/score.json` shows PASS). S2 gate-call defect
found and fixed 2026-10-04: `_cli` passed `PROJECT_ROOT / "findata"` as
`_skip_guard`'s `vault`, but `entities.file_path` is repo-root-relative, so
every lookup resolved `findata/findata/...` and the guard skipped 100% of
rows — the live gate reported `0 rows for apply`. `_skip_guard`'s own unit
tests had encoded the correct contract (vault = repo root), so the tests
passed while the call site was wrong. Live dry-run after the fix:
`4302 metrics -> 1557 rows for apply (757 collapsed, 1988 skipped)`. The
remaining skips are genuine `no full quote` cases. Related: the S3/S4
fixtures were not hermetic — `_expand_paths("findata")` probes
`Path("findata").is_dir()` against **CWD**, so patching `PROJECT_ROOT`
alone left `_cli` scanning the live vault and asserting against real data;
`monkeypatch.chdir(repo)` is load-bearing for any `_cli` test using the
default target, and the fixture note must live under a `_NEWSLETTER_TREES`
path since `scan` filters `Companies/` out. Consequence worth knowing: the
gate is **not idempotent on its first pass**. S2 judges a metric against the
note as it stands, and each render is what puts the next sentence into the
note — so run 1 retains fewer rows than run 2 (integration fixture: 2 of 3,
then 3 of 3). Idempotency is a property of the fixed point, not of run 1;
`test_full_apply_idempotent_bytes_and_rows` and
`test_stale_only_skips_fresh_note` now warm up twice before measuring. On the
live vault this reads as a one-time warm-up, not an oscillation: after the
fix, 1988 of 4302 rows skip as `no full quote` and stay skipped, because a
rendered note carries a paraphrase rather than the verbatim quote. Only probe
numbers
gathered: 2026-10-03 Adani edition (77 rows, 13 distinct keys, 28 collapsed).
**Depends on**: `doc/procedures/markdown_parse.md` Stage 11 (the runbook
this fixes); `helpers/graph/derive_insights.py`; the typed-judgment client
proposal (`../tooling/system_one_typed_judgment_framework.md`, S1 shipped in the
`system_one` patch).
**Trigger**: operator 2026-10-03, working a live newsletter end-to-end in
the main tree — *"btw we did duplicate-fact collapsing, the skip rule, and
the top-3 shortlist printed before the write … who does it in the future?"*,
then *"so in the procedure it should have come before derive insights"*
and *"lets also fix the key figures stale bug"*.

## Problem

`derive_insights.py --apply` is the busiest writer in the pipeline and the
one whose output nobody reads before it lands. A live run on 2026-10-03
(edition `Adani_Power_Motilal_Oswal`) wrote **59 quotes + 77 metrics** and
rewrote **18 notes**, announced by two summary lines. What those lines do
not say, measured on the same run:

- **26 of the 77 metric rows are repeat surface forms of 51 facts** —
  `1–2%` and `1-2%`, `20-30%` twice, `25%` three times, `40%` three times,
  `54%`/`55%` out of one sentence. The summary said "77 new".
- **6 of 77 rows carry a `metric_label`**; the rest cannot be rendered
  meaningfully in a Key-Figures note.
- **A judgment asked per row rejects 33/77 as non-metrics** — but that
  count is only trustworthy on the FULL sentence. On the `--verbose` line
  (`source_quote` truncated to 70 chars) the same carrier rejects real,
  labelled metrics too; re-asked with the full sentence, Ather's
  `ebitda_margin` 0.8% went P 0.12 → **0.97**. **Context completeness
  dominates the verdict.**
- **The edition entity for a fresh newsletter does not exist yet** when
  Stage 11 runs (the newsletter's own edition row). It does NOT abort:
  `--apply` self-heals (S4/G5 ingest-gap repair at
  `derive_insights.py:3262`, printing `edition entity created:`). It only
  mints the entity, never the `cited_in` edges — so `derive_cited_in`
  still belongs earlier in the chain when you want citations projected
  (S0). *Correction: an earlier reading of this run claimed the apply
  would die on the `quotes.entity` FK; that was inferred, never observed,
  and the code disproves it.*
- **`--stale-only` starves its own second pass**: the dry-run promised
  `9 key-figures notes would write`, the apply wrote **0** (`490 gated`),
  because the chatter pass bumped `generated.at` on the very notes the
  Key-Figures pass then gated.

Root cause of the operator-facing risk is not the numbers — it is that
nothing between the decision and the write says *"here is what you are
about to do, in words"*. The 2026-10-03 graph poisoning had the same
shape: a complex artifact, an unclear demand, spurious writes.

## Approach

- **S0 — edition-entity ordering (DONE, doc; premise corrected).**
  `doc/procedures/markdown_parse.md` Stage 11 now records the accurate
  contract: `--apply` self-heals a missing edition entity (so the run
  completes), and `derive_cited_in --apply` is what projects the
  edition's `cited_in` edges — it belongs earlier in the chain
  (`maint` already orders it 0d-before-8; the manual runbook did not
  mention it). Plus a checklist line: Key-Figures render confirmed,
  not assumed.

- **S1 — deterministic duplicate-fact collapse (no model).** Before the
  metric write, canonicalize the value (NFKC, dash folding, whitespace and
  comma stripping, `a-b` range canonical form) and key on
  `(entity, edition, canonical_value)`; keep the first occurrence, count
  the rest, and **report** the collapse instead of writing 26 redundant
  rows. Exact, free, and the only piece that protects the unattended
  `maint-full --no-notes` path. Owner: the derive script.

- **S2 — the skip guard (no model).** A judgment is built from the full
  `source_quote`; a row whose full sentence cannot be located in the
  edition is **skipped, not judged**, and the skip is counted in the
  summary (`skipped: N (no full quote)`). Never silently dropped: a skip
  is a report line, not a filter. Owner: the screening layer, enforced
  before any carrier call so it costs nothing.

- **S3 — the decision brief before the write.** Render the gate in plain
  English via `typed_judgment.brief()`: how many rows, how many distinct
  facts, how many collapsed, how many skipped, the mechanical remainder,
  and the ranked shortlist (worst disagreement first, `AGREE_TOL`-gated)
  with one brief each. No JSON reaches the operator. On the unattended
  `maint-full` path there is no human, so the same block is written to
  `outputs/` and the run proceeds — it never blocks and never
  auto-approves.

- **S4 — advisory briefs enrichment (optional).** When
  `TYPED_JUDGMENT=1`, screen the new rows with the free decisions-lane
  carrier (`inception/mercury-decide:free`): noul *"is this a standalone
  recordable business metric, or a passing mention?"* + choice over
  `{capacity, margin, growth, share, cost, guidance, other}`. Output is a
  brief plus a machine-readable sidecar. Advisory only — no row is dropped
  on a model verdict; the operator decides.

- **S5 — the `--stale-only` two-pass fix (the real bug on this run).** Evaluate the staleness gate
  ONCE before any pass writes, and pass the pre-computed decision set to
  both the chatter and Key-Figures passes, so a note written by the first
  pass is still eligible for the second. Today the second pass re-reads
  `generated.at` and gates what the first pass just made fresh.

- **S6 — key pin.** The per-row question text is a regression surface
  exactly as the batch prompt is for glm-5.3: pin the label set, the
  question wording and the carrier id, and re-run a small labeled key on
  any change. The eval-v3 relations key does not cover metric screening;
  this surface needs its own (the 77 rows of this edition are the
  candidate seed set). As of 2026-10-04 the candidate seed is pinned in
  this session: 77 rows, 13 distinct canonical keys, 28 duplicates
  collapsed on a 2026-10-03 edition. The relations-side carrier-drift
  measurement for the same discipline now has its own proposal
  (`provider_drift_glm_mercury.md`); the metric surface has no
  equivalent cross-carrier baseline yet.

**Probe counts (the only ones gathered so far).** S3 shipped only as a
visual probe regime; there is no labeled S3 key. The 2026-10-03
`Adani_Power_Motilal_Oswal` edition gives: **77 rows, 13 distinct
canonical metric keys → 28 collapsed** by the deterministic S1 logic, of
which the skeletal run reported 9 key-figures candidates and 0 written
(S5 two-pass bug). S2's full-sentence gate is the one whose inversion
was shown to work: one truncated-sentence `ebitda_margin` row flipped
from 0.12 → 0.97 when fed truncated context (caught on live run;
Ather was the rare row where p moved but not the picked label). The
operator's question — "who does this in the future?" — is the S7 key pin
measurement note's subject, not a quantity we can stat before the S1/S2
rerun lands.

## Sibling lane: the same three shapes on `make derive-relations`

The relations queue (fed by `extract_relations.py --apply`) shows the same
three shapes as this proposal, with one extra fact worth recording:
`make derive-relations` wrote **29 lines that collapse to 3 distinct rows**,
and all three are malformed — 10× a sentence fragment as the counterparty
(`"European giant DWS Group l divesting a minority stake"`), 9× a
wrong-counterparty row (`Tata Consultancy Services acquired "Porsche"`,
where the announcement was TCS→MHP, with a garbled newsletter header as its
quote), 10× a glued fragment word (`"Sankyu Corporation involving"`).

- **Deterministic first:** dedupe `(edge_type, source, target)` 29 → 3, plus
  a fragment-suffix detector — no model needed, and it is the same class of
  work as S1.
- **Skip guard (S2) applies verbatim:** judge the full sentence or skip.
- **The brief is an enum fit:** the triage contract already fixes the answer
  set (`discard` / `alias:<Entity>` / `stub` / `skip`), so a `choice`
  question returns the distribution over exactly those four — the clearest
  brief in the whole framework.
- **The risk is upstream:** `extract_relations --apply` writes resolved
  edges with no human in the loop. Judging the review queue cannot protect a
  write that already happened; gating the extractor is a separate proposal
  with its own eval-gate bullet. Recorded in
  `../tooling/system_one_typed_judgment_framework.md` §Risks.

## Ownership — who does what, in the future

| job | owner | model involved? |
|---|---|---|
| duplicate-fact collapse | deterministic code in the derive script | **no** |
| skip guard | screening layer, before any call | **no** |
| summary counts (distinct facts, collapsed, skipped) | deterministic code | **no** |
| per-row judgment | the carrier, via `typed_judgment` | yes, advisory |
| shortlist ranking + brief text | `rank_contentious` / `brief` | yes, presentation |
| the write | the human on the standalone path; never the screen | **no** |

## What does NOT change

No change to extractor patterns, the quote/metric schemas, the sentinel
curation-safety rule, or `--apply` semantics. S1 changes *how many* rows
land (fewer, deduplicated) and S3/S4 change *what the operator is shown* —
neither alters a query-visible semantic (rosters, crosswalks,
hierarchies). **If S1 is ever allowed to DROP a row rather than collapse
it, this proposal gains a mandatory `helpers/misc/ontology_eval_gate.py`
bullet** between dry-run and canonical apply.

## Acceptance criteria

1. A live re-run reports `N rows -> M distinct facts (C collapsed)` and
   writes M, not N; the DB count of rows for the edition equals the
   distinct-fact count.
2. A row whose full sentence cannot be located appears in
   `skipped: N (no full quote)` and is never sent to a carrier (assert via
   the fake transport call count in tests).
3. The gate prints the brief block; on a corpus where the judgment is
   skipped entirely the block still prints its deterministic parts.
4. `--stale-only` on a fresh edition writes both the chatter and the
   Key-Figures blocks for the same notes (the S5 regression: a dry-run
   promising `N key-figures notes` may not apply as 0).
5. Idempotent: a second `--apply --stale-only` writes 0 rows and 0 notes.
6. `maint-full --no-notes` still completes with the screen enabled.
7. The metric-screening key exists and pins: label set, question wording,
   carrier id.

## Risks

- **Collapse destroying signal.** Collapsing `1–2%` with `1-2%` is safe;
  collapsing two *distinct* facts that normalize alike is not. Mitigation:
  the key is `(entity, edition, canonical_value)` — same company, same
  edition, same number — never value alone, and the collapse is reported
  per cluster so the operator can audit it.
- **A free carrier changes under its id.** Mitigated by S6 pinning.
- **Brief fatigue.** A brief on every row recreates the noise the gate
  exists to remove. Only the ranked shortlist is briefed, `top=3` by
  default.
- **Judgment as gate.** The moment S4's output drops a row, this becomes a
  write gate and needs the ontology eval-gate bullet.

## Alternatives

- **Fix only the docs (S0) and leave the rest.** Rejected: the duplicate
  write is deterministic and cheap to fix, and it is the piece that
  protects the unattended path.
- **Drop rows by model verdict in the same run.** Rejected — that is a
  write gate built on an unpinned free model; it needs its own proposal
  and the eval-gate bullet.
- **Rely on `maint-full` ordering only.** Already true and insufficient:
  the manual Stage 11 path is the one operators actually use.
