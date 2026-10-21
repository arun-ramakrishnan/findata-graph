---
title: "Prompt-driven review menu — a single crisp-choice surface for every human-in-the-loop queue"
status: executed
filed: "2026-10-21"
executed: "2026-10-21"
completed_md: "424"
area: "helpers/core"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Prompt-driven review menu — a single crisp-choice surface for every human-in-the-loop queue

**Date:** 2026-10-21 · **Status:** PROPOSED ·
**Area:** `helpers/core/review_menu.py` (new) + per-queue adapters

## 1. Motivation

Trigger: operator report (this session, m01062/m01162) — "I am at the
point where manual human intervention even at this scale is quite hard",
with the ask to automate the `triage_*`/`security-*` review flows behind
a prompt-driven, crisp-choice text menu.

The cost is structural, not anecdotal — every review queue is a
different terminal keypress idiom, and none of them are reachable from
an agent conversation:

| Surface | Input medium | Human must remember | Journal / apply |
|---|---|---|---|
| `triage_pending_relations.py --review` | stdin TUI | `a[/TYPE[/TARGET]]`, `d`, `al NAME`, `st`, `p`, `?`, `q/x` | `findata/Misc/_pending_triage_decisions.jsonl` → `--apply-decisions` |
| `triage_pending_quotes.py --review` | stdin TUI | `1/2/3`, `al NAME`, `st`, `sc CIN`, `d`, `p`, `?`, `q/x` | `findata/Misc/quote_triage_decisions.jsonl` → `--apply-decisions` |
| `seed_nic2008.py review` | stdin TUI | `1/2/3`, `c CODE[:matchType]`, `s`, `?`, `q/x` | `outputs/nic_review/journal.jsonl` + `concept_mappings` |
| `coverage_ledger` | hand-edited JSONL | `DECISION_KINDS = (cover, exclude, defer, bad_pair, amend)` | `doc/local/security/coverage_decisions.jsonl` → `apply --write` |
| `security_findings` verdicts | prose procedure | contract `confirmed / needs_validation / rejected` (security-scan.md:21) | recorded per-finding JSON |
| `ocr_review` host-agent loop | manual checklist | selection + 8-step loop (ocr_review.md) | delegation/managed records |
| `markdown_parse.md` checklist | manual | destination ask + 847+ checklist | verify gates |
| `review-patch` / `review_freshness` | manual per-leg | accept/park per leg | freshness ledger |

Each queue asks the same handful of crisp questions — real? which
label? confirm? stub? park? — yet the operator must switch idioms per
surface and cannot answer any of them inside the conversation.

## 2. Evidence (measured 2026-10-21, this box)

| Configuration | Result | Verdict |
|---|---|---|
| status quo | 8 distinct review surfaces; 4+ distinct keypress sets; 2 queues hand-edited JSONL; verdict contract prose-only; OCR/security/manual checklists with no menu at all | baseline |
| candidate: prompt-driven menu backend | one choice vocabulary (the queue's own verbs), rendered as numbered menu / JSONL prompt packets; journal + apply unchanged | adopt |

One paragraph on the numbers: the counts above are from the repo's own
runbook (`doc/procedures/review-kit.md` keys cheat sheet + queue
lifecycle) and the per-surface docstrings — no new measurement needed to
prove the fragmentation. What is NOT measured (and will be, in S1) is
the candidate's wall-time and error rate; the behavioral guard is AC#1
(journal equivalence), not a speed claim. Ruled out without a build: a
Textual TUI for every queue (the repo already has one rich-UI lane,
`search_tui`; a menu is a superset of the keypress loop, not a second
TUI) and a model-in-the-loop auto-decision layer (the write gate stays
human — `system_one_typed_judgment_framework` rule).

## 3. Design

A new `helpers/core/review_menu.py` sits behind every queue as a
**drop-in input backend** for the existing `ReviewSession`. The queue
still owns `render`, `spec_of`, `apply_batch`, and the write gate; the
menu only replaces how a decision is entered.

**The contract (per item):** the host prompt shows (1) the evidence
card — the queue's existing `render` output (id, canonical, bucket,
suggestions/notes/VSS hints); (2) a choice list built from the queue's
own vocabulary; (3) a decision line the operator answers in one short
phrase — the same verbs they already know (`al X`, `d`, `st`, `p`,
`1/2/3`, `c CODE`, `cover/exclude/defer/bad_pair/amend`,
`confirmed/needs_validation/rejected`).

**Backend modes:**

- `--menu` — numbered in-terminal menu, strict superset of today's
  keypress loop (same verbs, printed).
- `--prompt` — renders each item as a stable JSONL prompt packet (id,
  evidence card, choice list, prompt string); reads the answer back
  from stdin/file. The host agent pastes the card into the
  conversation, the operator answers with the menu choice, the agent
  feeds the answer back. Serialization is idempotent (same item →
  same packet).

**Why it is safe:** all decisions still flow through the existing
`write_decisions`/`apply_decisions`/`coverage_ledger.apply --write`
paths; journaling, parking, `--redecide`, `--skipped`, `--dry-run`, and
the batch-confirm `apply N <noun>? y/N` gate are inherited verbatim from
`ReviewSession.run`; `spec_of`/`apply_batch` are unchanged functions.

**Alternatives considered / lost:**

| Alternative | Why lost |
|---|---|
| Textual TUI per queue | `search_tui` is already the repo's rich-UI lane; menus are a superset of keypresses, not a second TUI (review_kit non-goal) |
| Model auto-decides + human audits | violates the write-gate-stays-human rule; `system_one_typed_judgment_framework` risks (rule erosion, framing mistaken for deciding) |
| One generic YAML/JSON decision file | duplicates the journal/apply machinery; discards the existing per-queue validators |

**Slices (each independently landable):**

- S1 — `review_menu.py` core: JSONL packet renderer + menu backend +
  CLI flags (`--menu`, `--prompt`); reuses `ReviewSession`; ~250 lines
  + `tests/test_review_menu.py`.
- S2 — relations/quotes/NIC adoption: point each `ask()` at the menu
  backend; existing verbs become the choices.
- S3 — `coverage_ledger` menu + new `security_findings_menu` (verdict
  contract enforced at entry).
- S4 — OCR + markdown_parse adapters: render the checklists as menus
  (procedure logic unchanged).
- S5 — docs: `doc/procedures/review-kit.md` menu section; security-scan/
  ocr_review/markdown_parse mention menu mode; no procedure semantics
  change.
- S6 — execution close-out: completed.md #424 + archive + README reset.

## 4. Acceptance criteria & shakedown

1. AC#1 (behavioral equivalence) — drive the relations queue twice over
   a fixed fixture, once keypress, once `--prompt`; the decisions
   journal must be line-identical:
   `diff <(triage_pending_relations.py --review --limit 5 <keys) <(triage_pending_relations.py --review --prompt --limit 5 <answers)`
2. AC#2 — prompt packets stable/idempotent: same item → identical JSON;
   re-running a sitting reproduces the same prompts (run twice, `diff`
   empty).
3. AC#3 — write gate unchanged: `apply N <noun>? y/N` is still the only
   domain write; `--dry-run` journals `preview-*` non-terminal actions
   and parks nothing.
4. AC#4 — `security_findings_menu` enforces the verdict contract:
   `needs_validation` without a missing fact and `rejected` without a
   reason are refused at entry (exit ≠ 0).
5. AC#5 — existing queue tests still pass; `test_review_menu.py` covers
   packet stability, menu rendering, verdict-contract enforcement
   (targeted suite; no full-gate rerun mid-arc).
6. AC#6 — gate close-out once on operator go: qa 12/12, integration
   PASS, advisory 13/15 (chronic live-writer races reported not chased),
   perf parked on contention.

Eval-gate bullet: this change does **not** alter query-visible
semantics (rosters, crosswalks, hierarchies, extractor rules) — it
changes only the input surface of review flows. The house-mandated
`helpers/misc/ontology_eval_gate.py` bullet is therefore **not
triggered**; AC#1 journal equivalence stands in as the behavioral guard.

| Projected outcome | Today | After |
|---|---|---|
| review surfaces | 8 distinct, 4+ keypress sets, 2 hand-edited JSONL | 1 menu backend; per-queue vocabulary; same journals |
| operator decision entry | sub-terminal keypresses (per-surface idiom) | one prompt menu in-conversation |
| verdict-contract enforcement | prose-only (security-scan.md:21) | enforced at entry (AC#4) |

## 5. Risks

- **Mal-packet drift** — items may re-enter with new evidence; mitigated
  by stable item ids (same rule as `review_kit` parking) and
  idempotent serialization (AC#2).
- **Verbatim-key confusion** — menu must show the same verbs the queue
  documents; the menu is generated from the queue's own `legend`, so the
  two cannot diverge.
- **OCR/security judgment is a review, not a button** — the menu never
  auto-approves; a human answers every card (write gate stays human).
- **Scope creep** — each queue is adopted behind its existing tests;
  slices land one at a time; S4/O4 keep procedure logic untouched.

## 6. Non-goals

- No model auto-decision, no auto-approve, no change to any queue's
  semantics or apply path.
- No new human-review pipeline (unification only; inventory in §3).
- No changes to `search_tui` or any rich-UI lane.
- No schema change; no new DB tables; no new dependencies.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-21 | `.venv/bin/python3 helpers/misc/convo_query.py "automate triage menu prompt interactive human review ocr security" --limit 10` | hits: user 2026-10-15 "there is a triage script for this"; 2026-10-20 "we have a triage_* mechanism already... you need to adopt that"; 2026-10-19 "lets create a manual script which can do the T1, T2 and T3 nudge" | intent: route through existing triage_* mechanism |
| 2026-10-21 | `.venv/bin/python3 helpers/misc/master_query.py "review_kit ReviewSession menu prompt agent decision" --limit 5` | docs: `review_kit.md:82` (Design); `review-kit.md:9/81` (kit owns, universal contract); completed.md #252; scripts: `review_kit.py`, `worklist_park.py`, `typed_judgment.py` | spine + producer map confirmed |
| 2026-10-21 | `ripwire . --for="review menu interactive session decisions"` | ranked: `triage_pending_quotes.py:main/review/apply_batch`; `triage_pending_relations.py:main/review/apply_decisions`; `ReviewSession.run` (in=8); `coverage_ledger.apply`; `review_kit.Journal/park_items` | reuse targets identified |
| 2026-10-21 | `sed -n '80,175p' doc/procedures/review-kit.md` | universal contract: one keypress per item, batch confirm = only write gate, `--dry-run` preview-* | runbook contract quoted into AC#3 |
| 2026-10-21 | `sed -n '205,266p' helpers/core/review_kit.py` | `ReviewSession.run()` spine: render→ask→dispatch; `preview-*` journaling; `apply N y/N` gate | backend reuses this verbatim |
| 2026-10-21 | `sed -n '120,220p' helpers/validators/coverage_ledger.py` + grep `def apply` | `DECISION_KINDS`, `GAP_REASONS`, `STATUS_ENUM`, `SCOPE_ENUM`, `apply --write` dry-run default | S3 vocabulary source |
| 2026-10-21 | `sed -n '280,380p' helpers/graph/triage_pending_quotes.py` | `review()` verbs + `_bucket_rank` + stable id sha256(canonical) | S2 vocabulary source |
| 2026-10-21 | `grep -nE "^#{2,3} " doc/procedures/markdown_parse.md` | 879 lines v8.9; manual checklist §847+; output-destination ask | S4 inventory |
