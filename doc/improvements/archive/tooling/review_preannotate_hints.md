---
title: "Automated carrier pre-annotation with recommended hints — the review pipeline stops asking 'do I run the leg first?'"
status: executed
filed: "2026-10-21"
executed: "2026-10-21"
completed_md: "425"
area: "helpers/core + per-queue adapters"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Automated carrier pre-annotation with recommended hints — the review pipeline stops asking "do I run the leg first?"

**Date:** 2026-10-21 · **Status:** PROPOSED ·
**Area:** `helpers/core/decisions_preannotate.py`, `helpers/core/review_menu.py`, per-queue review adapters

## 1. Motivation

The mercury-decide carrier leg (`decisions-preannotate`) exists and works, but it is a **manual, separate step**: the operator must remember to run `make decisions-preannotate SURFACE=…` before a review sitting, or the sitting renders no hints. The review loops themselves never invoke the carrier — they only *read* whatever annotation file already exists (read-side `load_*_annotations` + `render_carrier_for`). So the second opinion is only as fresh as the operator's memory.

This proposal captures the whole area as one record — including the work already built **without a dedicated slice** (S0), and the two remaining slices that make the operator's stated workflow real: "it should be automatic and then we use the decisions-preannotate output as the (recommended) hint in our review menu. makes life much easier for me" (m02343).

## 2. Current state — landed work (captured here retroactively)

These landed during the 2026-10-21 review-menu batch without a dedicated proposal slice; they are recorded here so the area has one canonical home.

- **S0a — Producer** `helpers/core/decisions_preannotate.py`: one carrier producer, five surfaces (relations / quotes / coverage / findings / checklist ocr+markdown-parse). `SurfaceSpec` dataclass (`iter_rows`/`key_of`/`state_of`/`default_out`/`load_existing`); two `noul` questions (Q1 factual, Q2 rubric); `annotate_surface(spec, *, model="mercury-decide", limit=0, out=None, no_escalate=False)` returns `(todo, annotated, out_path)` and is **idempotent per key** (rows already present are skipped). Advisory only — never writes domain data. `render_carrier_for(key, c_map=…)` renders the `ab mercury: fact p=… / admission p=… -> admit|keep-out [ESC]` line used by every loop.
- **S0b — Menu backend** `helpers/core/review_menu.py` (#424, archived): `Choice`/`MenuSpec`/`build_packet` (schema `review_menu/v1`)/`render_packet`/`normalize_answer`/`make_menu_input`/`make_scripted_input`/`write_packets`. Verbs are the queues' own documented keys; `| note` partition; batch-confirm passthrough.
- **S0c — Carrier client** `helpers/core/typed_judgment.py` (System One framework, #345): `CARRIERS["mercury-decide"]` → `DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"` (line 88), typed `noul`/`choice`/`score`, content-hash disk cache, `MAX_CENTS` cap, retry.
- **S0d — Annotation outputs** (defaults): relations `findata/Misc/_pending_annotations_mercury.jsonl`, quotes `_quote_annotations_mercury.jsonl`, coverage `outputs/coverage_review/mercury_annotations.jsonl`, findings `outputs/findings_review/mercury_annotations.jsonl`, checklist `outputs/{surface}_checklist_review/mercury_annotations.jsonl`.
- **S0e — Wiring** Makefile target `decisions-preannotate` (Makefile:389); read-side adoption in all five loops: `triage_pending_relations.py:1314` (`load_annotations`), `triage_pending_quotes.py:407` (`load_id_annotations`), `coverage_ledger.py` review, `security_findings.py` triage, `review_checklist.py` review. Tests: `tests/test_decisions_preannotate.py` (10), plus `test_typed_judgment.py` (37), `test_review_menu.py`, queue hygiene suites.

## 3. Slices

### S1 — Automatic leg: the review sitting runs the producer first

Each review entrypoint gains a **default-on** `--preannotate` (opt-out `--no-preannotate`) that calls `annotate_surface(spec)` for its own surface **before** assembling entries, then loads the annotation map as today. Idempotency makes this cheap: fresh API calls only for keys not yet annotated; repeated sittings skip.

- Entrypoints: `triage_pending_relations.review()` (1216), `triage_pending_quotes.review()` (282), `coverage_ledger.review()` (1232), `security_findings.triage()` (832), `review_checklist.review()` (235).
- `--limit` propagates to the producer so a smoke pass annotates only the window being reviewed (bounds API cost on large queues).
- Findings passes the resolved findings path through (`_FINDINGS_PATH`); checklist resolves the surface-specific output path (`_checklist_default_out`).
- **Failure is non-fatal**: carrier errors already print `[key] FAILED: …` and continue; a dead carrier degrades to today's "no hints" behavior, never blocks the sitting.
- **Cost guard**: default-on is bounded by (a) per-key idempotency and (b) `--limit` propagation; add `--preannotate-limit N` if a hard cap on fresh calls per sitting is wanted.

### S2 — Recommended hint in the menu/packet

The carrier verdict marks a **recommended choice** in the numbered menu and in the `review_menu/v1` packet, rendered as `★ recommended` next to that choice. Pure hint — never auto-applied; the human gate is unchanged.

- `build_packet(entry, spec, *, prompt_text=None, recommended_key=None)` → packet gains `"recommended": "<key>"`; `render_packet`/`render_choices` show `★ recommended` on that numbered line.
- **Verdict → verb mapping** per surface (queue-owned vocabulary, in the adapter, not in `review_menu`):
  - relations: admit → `a[/EDGE_TYPE[/TARGET]]`; keep-out → `d` (discard)
  - quotes: admit → `1` (top suggestion); keep-out → `d`
  - coverage: admit → `c confirmed`; keep-out → `e exclude`
  - findings: admit → `k keep`; keep-out → `r reject`
  - checklist: admit → `y`; keep-out → `n|why`
- The hint line (`ab mercury: …`) stays in the evidence card; the ★ marks the choice, so the operator sees both the reasoning and the suggested action in one screen.

### S3 — Capture/consolidation

- One proposal record for the whole area (this file); on execution, archive per the checklist below and add the `completed.md` entry.
- Update `tests/test_review_menu.py` + `tests/test_decisions_preannotate.py` for the new flags and the `recommended` field; targeted tests only (no full gate mid-arc).

## 4. Acceptance criteria

- AC#1 — `--menu` and `--prompt` sittings run `annotate_surface` for their surface automatically (idempotent second run: zero new API calls on a re-run with no new rows).
- AC#2 — A kept-out carrier verdict renders `★ recommended` next to the discard/exclude/reject verb in relations/quotes/coverage/findings menus; a carrier admit marks the accept/keep/confirm verb.
- AC#3 — `--no-preannotate` restores today's behavior byte-for-byte.
- AC#4 — Carrier failure (simulated) never blocks a sitting; hints degrade to absent.
- AC#5 — No query-visible semantics change (rosters/crosswalks/extractor rules untouched) → **no eval gate required**; static_checks + md-lint clean, targeted suite green.

## 5. Archival checklist (when executed)

1. `git mv` to `../archive/tooling/`; repoint cross-refs.
2. `../completed.md` unique number entry (next free after #424).
3. `../pending.md` grep: annotate completed items.
4. `../archive/README.md` topic line + reset live pointer.
5. `make search-fresh APPLY=1`, then plain `make search-fresh` (rc=0).
6. Flip frontmatter `status: executed` + date + `completed_md` (same change).

## 6. References

- Operator ask (m02295/m02343): automatic leg + recommended hint; "moves life much easier for me".
- Recursive decision: `review_menu_prompt_driven.md` (#424); `system_one_typed_judgment_framework.md` (#345).
- `doc/improvements/proposals/README.md` house rules (file before multi-slice work; eval-gate bullet only when query-visible semantics change).
