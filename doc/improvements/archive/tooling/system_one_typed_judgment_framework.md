---
title: System One typed-judgment framework — a reusable second source for triage queues
status: executed
filed: '2026-10-03'
area: tooling
executed: '2026-10-04'
completed_md: '345'
---

# Proposal: System One typed-judgment framework — a reusable second source for triage queues

**Date**: 2026-10-03
**Status**: EXECUTED 2026-10-04 (completed.md #345) — see Execution results.
**Depends on**: the Jev-pattern pilot record
(`../../local/evaluations/jev_assessment.md`, `../../local/evaluations/jev_pilot/`);
the relations pre-annotator
(`triage_preannotation_escalation.md`, implemented); `helpers/core/review_kit.py`
(the journaled sitting every triage queue already uses).
**Trigger**: operator 2026-10-03 — *"open a detailed proposal for
integrating a system one model framework which we can reuse in so many
areas … let it serve as second source for reviews to do A/B triage and
help human operator"*, after the `inception/mercury-decide:free`
comparison.

## Problem

Every triage queue in this repo asks a human the same three questions:
is this claim real, is this specific enough to admit, and which of these
labels does it belong to. The operator answers them one row at a time,
so review cost scales linearly with queue fill, and the review journal
(`doc/procedures/review-kit.md`) records only human actions — there is no
second, machine-checkable opinion on the row to compare against.

**The failure mode this proposal exists to prevent.** The 2026-10-03
cleanup found 16 bad edges admitted through ad-hoc harness-assisted
accepts (the collapsed LVB deal, a mangled Kering Beauté alias, wrong-type
JVs). The operator's words for the cause: the decisions were put in front
of them as *"some very complex JSONL doc"* where **it was not clear what
was demanded** — so approvals produced spurious edges, spurious rows, and
poisoned the graph. A judgment layer helps here in a way no amount of
prompting does: it converts a row into **one plain-English question with
the context needed to answer it**, and it ranks the queue so the operator
sees the most contentious item first instead of the whole lane. JSON is
for machines and journals; the human-facing surface is a sentence.

The chat-completion carriers we benchmarked force the opinion into prose
or a JSON array we have to parse and defend: a 2026-10-03 measurement
(`triage_preannotate` regression on the eval-v3 key) showed glm-5.3's
`p_*` fields are **confidence in its own boolean**, not P(truth) — a
derivation that treated them as probabilities scored **35/56 against
55/56** on the same key. System One carriers return the typed answer
directly (`noul` = P(yes), `choice` = argmax + full distribution,
`score` = ordinal), which removes both the parsing and the
inversion class of error.

## What the pilot measured (2026-10-03, eval-v3 key, 56 items)

| carrier | lane | Q1 (fact) | Q2 (rubric) | ctx-Q2 | artifacts rejected | wrongly admitted |
|---|---|---|---|---|---|---|
| `inception/mercury-decide:free` | OpenRouter decisions API | 46/56 | 54/56 | 36/36 | 37/37 | 0 |
| `glm-5.3` (pre-annotator + probe) | zai coding plan | 52/56 | 55/56 | 35/36 | 37/37 | 0 |
| `respan/span-01-lite:free` | OpenRouter decisions API | 45/56 | 32/56 | 16/36 | 17/37 | 20 |

Read: mercury-decide ties the rubric question and holds artifact
precision at 37/37 where span-01-lite collapses (it admits the false
LVB–Clix edge at q2=0.82 — the exact failure that disqualified span from
admission). It is 4 behind glm on Q1, the knowledge-bound question. The
two carriers disagree on exactly the two key-dispute items (47
Nippon–DWS, 54 Globus–ANSA McAL), which is the signature of
adjudication-needed rows, not of a capability gap. `mercury-decide` is a
**decisions-lane-only** id — it 404s on `/api/v1/models`, so the
transport is not swappable with a chat model.

**Re-measure 2026-10-04 (wt, after the retrieval + temperature fixes).**
Both carriers were re-run against this key from the same tree
(`helpers/graph/triage_preannotate` without `temperature: 0`, with
`evidence_query` preferring `truncated_from`; mercury via its decisions
transport):

| carrier | admit (rubric_admit) | escalated | verdict flips vs 2026-10-03 |
|---|---|---|---|
| `glm-5.3` pre-annotator | 1 → **3** | **31 → 19** | 7 factual-guidance flips; 2 admit flips, both marginal (id=20 → true @ p=0.6, id=47 → true @ p=0.55) |
| `inception/mercury-decide:free` | 0 → 0 | n/a (no retrieval leg) | **0** — byte-identical verdicts |

Read: the no-temperature re-run shifted GLM only at marginal
confidences — noise, not a doctrine break — while the retrieval fix
roughly halved its escalation load. The mercury carrier reproduced
bit-identically, which is the strongest evidence this proposal's S2
(`ab()`) and AC-2 (same key, same verdicts) hold across weeks of
provider churn. Still: `p_*` values are not probabilities, and admit
marginals are endpoint-noise territory; a written system-this-firm
claim should re-run this key rather than quote single runs.

## Per-use-case improvement over the state of affairs today

This is the case for the framework. Only the first two rows are
measured; the rest are projected and each names the eval key it would
need before it could be trusted.

| triage surface | current rule (evidence) | what breaks today | what a typed judgment adds | status |
|---|---|---|---|---|
| **`triage_pending_relations`** (queue fed by `make derive-relations`) | full manual pass per fill; pre-annotator ships (`triage_preannotation_escalation.md`) | the operator reads every row | a second carrier as A/B + agreement flag; **the decision vocabulary is already a closed enum** (`discard` / `alias:<Entity>` / `stub` / `skip`), so it maps 1:1 onto a `choice` question with a full distribution instead of a boolean to interpret; deterministic pre-filters first (dedupe, fragment-suffix) | **MEASURED + LIVE QUEUE 2026-10-03**: `make derive-relations` produced 29 lines → **3 distinct rows, all malformed** (10× a sentence fragment as counterparty, 9× `TCS acquired Porsche` where the announcement was TCS→MHP, 10× `Sankyu Corporation involving`). Q2 55/56 (glm) / 54/56 (mercury), artifacts 37/37, disagreement confined to the 2 key-dispute items |
| **`derive_co_mentions`** → `co_mentioned_in` | no admission filter; dyads become edges | the 37 co-mention artifacts — the class behind the 16 bad edges cleaned up 2026-10-03 | an admission judgment *before* the write: mercury rejects 37/37, span-01-lite only 17/37 | **MEASURED** |
| **`derive_events`** (D7 spine) | regex + `_iter_bullets`; schema has 4 `event_type` values but only `acquired` carries `valid_from` (16/25 rows); guidance and management_change unrepresented | events missing from the timeline, not wrong-but-visible | `choice` over exactly the 4 schema values + date + P — surfaces the unrepresented classes and catches "wrong type AS STATED", the Q1 failure mode | **PROJECTED, highest value** — the enum maps 1:1 onto the column |
| **`derive_insights`** (quotes + metrics) | concall-body capture; catch-all `Quotes.md` is 4.87 MB, ~23% of the vault in one note | a quote/metric attributed to the wrong section company; duplicate surface forms of one fact written as separate rows | `choice` over the section's companies ("which company does this excerpt belong to?") as a mis-attribution veto; noul "is this a standalone recordable metric, or a passing mention?" | **PROTOTYPE-EXERCISED 2026-10-03** on the live gate (edition *Adani Power Motilal Oswal*): 52 metric rows, **37 distinct values — 28 rows are repeat surface forms of 13 facts**; mis-attribution is *triage/quality*, not the blocker in this batch |
| **`derive_themes`** (`exposed_to`) | narrow alias regex, precision-biased by design | recall misses when an alias never fires; ambiguous aliases (`PLI`, `China+1`) mis-hit | noul "genuinely exposed, given the note text" — adds recall and vetoes false hits; `score` for intensity | **PROJECTED** |
| **`derive_hyperedges`** | dyads regrouped into clusters (96 hyperedges / 2,687 incidences; weights = `quote_count`) | a co-mention-driven label sweeps unrelated members into a cluster, invisible without inspection | per-incidence noul "does this member belong to this cluster?" so only low-P members reach the operator | **PROJECTED** |
| **`derive_countries`** (`listed_in`) | closed 16-suffix map, never guessed; chatter country mentions are noise-classified **by design** | not the mapping — only 9 tickers / 4 suffixes unmapped today. The blind spot is **20,198 of 26,160 entities (77%) with no dotted ticker**, which get no edge at all | `choice` over the country vocabulary as a **worklist triage** for untickered entities; routes to the existing worklist, never auto-applies | **RULING 2026-10-04: chatter may seed the country worklist — candidates promoted only through the worklist, `listed_in` auto-apply stays OFF** |
| **`triage_pending_quotes`** | manual accept of quote canonicals | garbled/fragment rejection is a per-quote judgment call | noul P(clean quote) so the operator works only the low-P tail | **PROJECTED** — needs a quote eval key (eval-v3 is relations-only) |
| `derive_cited_in`, `derive_indices` | deterministic (note frontmatter `sources[]`; constituent sidecar) | the derivation is not the weak point — failures are edition-identity conflicts (OKF F0: one edition keyed four ways) | **no decision gain, real triage gain**: a conflict flag that says *"Note A cites edition E, but E's identity resolves to F"* as a one-line brief, so the operator sees the 3 conflicting notes instead of diffing `sources[]` by hand | **TRIAGE ONLY** — never auto-resolves; the identity fix stays a curation decision |
| subsector / worklist authoring (`seed_nic2008`, parked labels) | operator strategy decision over parked buckets | the operator must hold the whole taxonomy in their head to answer | **no decision gain, real framing gain**: the lane is a rubric question, so it earns a `choice` over the candidate labels + a `score` per bucket ("how strong is this membership argument?"), rendered as a brief that names the ONE most contested bucket and the thesis for each side | **FRAMING ONLY** — the strategy call is the operator's; the brief just makes it answerable without the surrounding 15 buckets |

Cross-cutting, for every queue that adopts it: the sitting renders
agreement vs disagreement, so the operator's attention goes only to
contested rows; each decision gains a journaled machine second opinion
(the review journal today records human actions only); and every run
carries a cost / latency / token / context ledger with a content-hash
cache, so a re-ask is free.

## Approach

- **S1 — the reusable client.** `helpers/core/typed_judgment.py`:
  `noul` / `choice` / `score` question builders, a `Carrier` registry
  (decisions lane + chat lane), injectable transport (tests never touch
  the network), per-call `CallResult` telemetry (wall time, attempts,
  prompt/completion tokens where reported, context-used %, cached flag,
  error), a `RunStats` ledger with an optional hard spend cap, a
  content-hash disk cache, and a CLI (`python -m
  helpers.core.typed_judgment --state-file … --questions-file …`) that
  can A/B carriers and append a JSONL record.
- **S2 — A/B as a first-class call.** `ab()` asks the same state across
  N carriers and returns per-carrier verdicts plus an agreement map
  (`agree` + each carrier's value). Agreement is the operator's
  attention filter; disagreement is the adjudication queue. Both lanes
  normalize through one adapter, so a chat carrier cannot silently drop
  out of a mixed A/B (a defect the first test pass caught).
- **S3a — the state must be complete or the row is skipped (measured).**
  On the live `derive_insights --apply` gate the carrier was asked
  per row using the `--verbose` line, which truncates `source_quote` to 70
  chars, and it rejected 33/52 rows — *including rows the extractor had
  labelled* (`ebitda_margin` 0.8%, `capex` 20–30%). Re-asking the same rows
  with the **full sentence** flipped both: Ather's 0.8% EBITDA margin went
  P 0.12 → **0.97** on *"EBITDA at approximately 0.8% positive for the first
  time"*. Two rows were inconclusive because the sentence could not be
  located in the edition text. Rule: a judgment is built from the full
  sentence, and a row whose full quote cannot be located is **skipped, not
  judged** — a fragment produces a confident wrong answer, which is the
  failure this proposal exists to prevent.
- **S3 — the plain-English decision brief (the human surface).**
  `brief()` renders one card — the disagreement verdict in the first
  line, the question in a sentence, the context needed to answer it,
  each carrier's lean in words ("92% no"), and an explicit note that the
  models do not get a vote on the write. `rank_contentious()` reduces a
  lane to its most contested items, worst gap first, on one shared
  tolerance (`AGREE_TOL`) so the card and the ranker can never disagree.
  No JSON reaches the operator; JSON stays in the journal.
- **S3b — the decision-vocabulary brief (the enum fit).** The triage
  contract already fixes the operator's answer set —
  `discard` / `alias:<Existing Entity>` / `stub` / `skip`
  (`doc/procedures/markdown_parse.md` §9). That is a `choice` question with
  four options, so the brief reads *"which of these four, and how
  confident?"* and the distribution is directly actionable
  (`discard 0.72 / alias:DWS Group 0.20 / stub 0.05`). Two typed questions
  per row: the choice above, plus a noul *"is `target_mention` a named
  company, or sentence text?"* — the fragment class (2 of 3 live rows).
  Fresh events (the `TCS acquired Porsche` row is really TCS→MHP) route to
  the retrieval escalation that already shipped.
  **Deterministic pre-filters run first and need no model:** dedupe on
  `(edge_type, source, target)` (29 → 3 on the live queue) and a
  fragment-suffix detector. The carrier only ever sees the residual.
- **S4 — one adopter end-to-end (relations).** Extend the existing
  pre-annotation sidecar with a `carrier` field so a second carrier's
  verdicts land beside the first, and render both plus the agreement
  flag in `_pending_triage_report.md` (the render hook already exists —
  `load_annotations` + `TestPreannotationRendering`). The human
  `--apply-decisions` gate is untouched.
- **S5 — the review-sitting second opinion.** `ReviewSession.render` is
  a domain callback, so each queue can print its A/B block with no kit
  change; the verdict pair is journaled as a non-terminal line beside
  the human decision, giving the audit trail "the model said X, the
  human said Y" that the journal cannot express today.
- **S6 — eval keys per surface.** No projected row above is trusted
  without one. Each adopting surface gets a small labeled key (the
  relations key exists; quotes, hyperedge incidence, theme membership and
  event typing do not) scored with the same two-question shape, so a
  carrier swap is a measured decision.
- **S7 — prompt/question pinning.** The typed question text is the
  regression surface, exactly as the batch prompt is for glm-5.3: any
  change re-runs that surface's key.

## What does NOT change (and why no ontology eval-gate bullet — yet)

Advisory annotations, report rendering and journal lines are read-side;
no roster, crosswalk, hierarchy or extractor rule moves, so the S2 gate
rule in `doc/improvements/proposals/README.md` does not trigger. The
moment a judgment becomes a **write gate** (S3 for co-mentions, or
country worklist auto-apply), this proposal gains a mandatory
`helpers/misc/ontology_eval_gate.py` bullet between dry-run and canonical
apply, and the human gate stops being the only writer.

## Acceptance criteria

1. `helpers/core/typed_judgment.py` has hermetic tests (fake transport):
   typed-question shapes, answer normalization for all three types,
   retry/backoff, cache hit + `--no-cache`, spend cap, telemetry fields,
   agreement map for agree and disagree cases. No network in tests.
2. The relations A/B (S3) reproduces the measured comparison through the
   shipped module — same key, same verdicts, agreement flag naming the
   2 key-dispute items.
3. The `derive_insights --apply` gate can print the shaped decision:
   duplicate-fact count, the mechanical remainder, and the ranked
   shortlist with a brief each — with the skip rule from S3a holding
   (no row is judged on a fragment).
4. Report render shows both carriers and the agreement flag; the
   decisions file and `--apply-decisions` path are byte-unchanged. The
   rendered card is plain English: a regression asserts the brief shows
   no `{`/`noul`/raw-JSON token, and that `rank_contentious` on the
   eval-v3 legs surfaces the measured dispute set.
5. Every adopting surface has a labeled eval key before its judgment is
   shown to the operator (S6); the `derive_countries` case now has its
   ruling (2026-10-04): chatter may seed the worklist but `listed_in`
   auto-apply stays OFF — candidates promote only through the worklist.
6. Cost ledger: a full 56-item A/B run reports its call count, wall
   time, cache hits and spend; the free lane stays at $0.

## Execution results (2026-10-04)

**S1–S4** landed in the `system_one` patch: the typed client
(`helpers/core/typed_judgment.py`: noul/choice/score, `ab()`, briefs,
rank_contentious, cache, telemetry), the carrier registry + consolidated
brief wired into the queue report, the dual-carrier annotations files,
and `TestPreannotationRendering`/`TestS3DecisionBrief` coverage.

**S5 — the sitting second opinion**: `review()` now renders the A/B
block beside the evidence (per-carrier fact/admission leans, the
`AGREE_TOL` 2-carrier AGREE/DISAGREE flag, the no-vote reminder on
disagreement) and journals the verdict pair as a NON-TERMINAL
`second-opinion` line beside the human decision (`latest_action_by`
parks only on approve/skip, so the line never changes walk inclusion).
Read-side: zero model calls. Tests:
`TestS5SecondOpinionSitting` (block + journal + no-park).

**S6 — no key, no show**: `SURFACE_EVAL_KEYS` + `require_surface_key()`
make the per-surface key policy executable — the relations queue
registers eval-v3; a new adopting surface fails loudly until it files
its key. Both carrier-verdict surfaces (report brief, sitting block)
call the gate. Tests: `TestS6SurfaceKeyRegistry`.

**S7 — question pinning**: the surface's card texts are module
constants (`Q1_FACTUAL_QUESTION`/`Q2_ADMIT_QUESTION`), pinned by
`TestS7QuestionPinning` alongside the shared `AGREE_TOL`; the JUDGING
questions are pinned by the key itself (`mercury_callout.py` Q1/Q2,
which the module-run imports — single source). Any change re-runs the
key.

**AC#2 — same key, same verdicts, through the shipped module**: the
frozen eval-v3 56-item set was replayed with `tj.ab()` (mercury-decide,
decisions lane, the key's own `state_of`/Q1/Q2 objects — replacing the
ad-hoc `requests.post`) → **Q1 46/56, Q2 54/56, artifacts 37/37, and
per-item verdict parity 0/56 diffs vs the record** on both questions.
Between-carrier agreement vs today's glm pre-annotate leg flags **both
key-dispute items (47 Nippon–DWS, 54 Globus–ANSA McAL)** — the
additional factual-axis disagreements sit on rows where only the glm
leg carried retrieval, the expected scout asymmetry. Run record:
`doc/local/evaluations/jev_pilot/legs3/module-ab-mercury/`.

**AC#6 — cost ledger** (from that run): 56/56 calls OK, wall 27.7 s,
spend **$0.0000** (free lane held), 1 cache hit. The run also surfaced
one real gap, fixed: `ab()` gained per-carrier `keys=` (mixed-registry
A/Bs whose carriers live on different endpoints), hermetic-tested.

**AC#4 — byte-stability**: the decisions file and `--apply-decisions`
path are untouched by S5–S7 (render/journal only); the plain-English
card contract is pinned by the existing brief tests (no `{`/`noul`/raw
JSON tokens).

## Risks

- **Conservative misses.** mercury-decide's Q1 misses are all low-P
  (it scores 0.05–0.32 on 10 rows) — safe under a human gate, but it
  will not raise recall on its own; retrieval escalation remains the
  recall lever.
- **Distribution drift.** A free provider's model can change under the
  id. The cache keys on the id, not a fingerprint, so a silent model
  swap would only surface through a key re-run (S6 discipline).
- **Per-call cost shape.** The decisions lane is one state per call —
  56 calls versus glm's 6 batches. Free today; the ledger is what makes
  a future paid lane safe.
- **The write path is the real risk on this lane, not the review.**
  `make derive-relations` (`extract_relations.py --apply`) writes resolved
  edges to `graph_edges` with **no human in the loop**; only unresolved
  names reach the queue. The 2026-10-03 queue shows what the extractor
  considers resolved: a sentence fragment used as a counterparty, and a
  `TCS acquired Porsche` row whose quote is a garbled newsletter header.
  A judgment layer on the *review* side cannot protect a write that already
  happened — so any proposal to gate the extractor's writes needs its own
  eval-gate bullet and is explicitly out of scope here.
- **Rule erosion.** Advisory text next to a row invites an operator to
  rubber-stamp — the brief must lead with the disagreement, not with a
  recommendation, and the write gate stays human.
- **Framing mistaken for deciding.** On the two framing-only surfaces
  (subsector authoring, edition identity) a confident brief can feel
  like a ruling. Those briefs carry no verdict word at all: they state
  the conflict and the options, and stop.

## Alternatives considered

- **`respan/span-01-lite:free`** — rejected on measurement: 32/56 on the
  rubric question, 17/37 artifacts rejected, and it admits the false
  LVB–Clix edge. Its typed API works; the model does not hold the line.
- **glm-5.3 as the only carrier** — kept as the primary (Q1 lead,
  batched, retrieval escalation). This proposal adds a second opinion,
  it does not replace the primary.
- **The Jev DuckDB extension** — already ruled out in
  `../../local/evaluations/jev_assessment.md`: our judgment surface is
  Python text-file queues, not SQL.
- **Prompt-only second opinions on a chat carrier** — keeps the parsing
  and confidence-vs-probability ambiguity that cost 20 points on
  2026-10-03.
