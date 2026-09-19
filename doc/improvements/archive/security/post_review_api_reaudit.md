---
title: "Security coverage expansion — availability class + cumulative coverage machinery"
status: executed
filed: "2026-09-18"
executed: "2026-09-18"
completed_md: "247b"
area: "app.py API surface, helpers/ ingestion + graph layers, doc/local/security/"
---

# Security coverage expansion — availability class + cumulative coverage
machinery

**Date:** 2026-09-18 · **Status:** EXECUTED (completed.md #247b; record: doc/local/security/security_evaluation.md, Addenda 2–5). Supersedes the scoped
`post_review_api_reaudit` scope, whose single slice is DONE — see S1) ·
**Area:** the API surface, the ingestion/graph layers, and the security
coverage record itself

**Follows:** Addendum + Addendum 2 to
`doc/local/security/security_evaluation.md`. The 13-route re-audit
landed clean (zero findings), so this proposal no longer chases a
finding — it chases the two things the re-audit *proved* are missing:
an entire attack class with no coverage, and a coverage record that
loses routes.

## 1. Motivation

The re-audit's value was structural, not vuln-shaped:

- The **availability class has never been reviewed** — not in 2026-08-17,
  not in the re-audit. It is the one gap with no coverage at all, and it
  is the class this repo is most exposed to: a 4-core CPU-only production
  constraint, unauthenticated GETs that can force expensive compute, and
  a parser pipeline that runs on externally-sourced documents.
- The **coverage record lost a route twice**. The 2026-08-17 snapshot
  missed 13 shipped routes entirely (Addendum §B); the re-audit then
  nearly mis-recorded `/api/graph/refresh` as removed because a
  literal-match inventory drops routes with `methods=` arguments. Both
  failures were caught by re-verification, neither would exist under a
  deterministic, cumulative record. Zero findings does not mean the
  record form is fine — it means the drift is quiet.

S1 is done and recorded. S2 is the proportionate next step: genuinely new
coverage, no new machinery. S3/S4 are the machinery slices, each gated on
the one before, and each small enough to abandon after one trial.

## 2. Slices

| Slice | Content | New infra? | Gate to proceed |
|---|---|---|---|
| S1 ✅ | 13-route re-audit → Addendum 2 | none | DONE |
| S2 ✅ | Availability / resource-exhaustion review | none | DONE — Addendum 3, 1 confirmed (AVAIL-1) |
| S3 ✅ | Minimal coverage ledger as data | a JSON file + a validator | DONE — Addendum 4 |
| S4 ✅ | Adversarial-validation trial on S2 | one `rlm.spawn` child | DONE — not low-yield, Addendum 5 |

### S1 — DONE (recorded, not re-proposed)

The 13 post-close routes re-audited clean; result and evidence are
Addendum 2 to the evaluation doc. No regression tests (one per confirmed
finding, none survived the gate). This slice is closed.

### S2 — Availability / resource-exhaustion review (the never-covered class)

For each surface, the question is the same: **is there a bound, is it
enforced, and can an unauthenticated actor force the expensive path
repeatedly?** Candidates, grounded in measured behavior:

1. **Unauthenticated expensive GETs.** `/api/graph/positions` pays a
   ~30 s layout solve on the first request after a topology change
   (hash-gated and disk-cached, so *not* repeatable — verify the gate
   holds under a forced-refresh sequence). `/api/graph/near-duplicates`
   runs a pairwise self-join over `v_note_embeddings` as an
   unauthenticated GET — ~1 s at ~1k docs; the open question is its cost
   at 10k, and `limit` is already clamped to 500. `/api/graph/refresh`
   (SEC-5, deploy-gated) is the write-side twin of this class.
2. **Hostile document ingestion.** PyMuPDF on externally-sourced PDFs —
   decompression/memory bombs and page-count blowups; establish whether
   any size/page bound exists before the parse. The Paddle cloud path
   (SEC-7) and the image fetch (SEC-6, scheme-fixed, magic-byte gated —
   but is there a byte cap on the fetched body?).
3. **Regex backtracking, the derive layer.** `tests/test_fuzz_regex.py`
   already fuzzes three parse-layer patterns (`SECTION_RE`, `IMG_BLOCK_RE`,
   `_parse_edition_number`) with the Hypothesis contract "terminates
   quickly, does not crash" — but the ~30+ compiled patterns in the derive
   layer (`derive_insights.py`, `derive_events.py`) have *no* fuzz
   coverage, and at least two carry the nested-quantifier shape that
   contract exists to catch: `_ATTR_RE`
   (`derive_insights.py:849`, `(.+?)` inside an alternation against
   `\s*$`) and `_SPEAKER_NCT_RE` (`:461`, `(?: [A-Z][\w.'\-]+)+` over a
   `+` inner class). Adversarial newsletter/OCR text is the expected input
   to this layer, not an edge case — extend the existing module to them
   rather than writing a new one.
4. **Unbounded query legs.** The DuckDB full-corpus cosine and the
   near-duplicate self-join are O(n) and O(n²) in embedding count; the
   100M-row KNN is bench-only (prod does not use it). Confirm the
   serving paths each terminate at a documented scale.

Verdict contract as S1: a candidate is `confirmed` only with a bounded
local observed result (a sanitizer, a measured runaway time, or an
enforced absence of a cap); a "probably fine at current scale" reading is
a note, not a finding. Findings land as Addendum 3, with one regression
test per confirmed finding (house Hypothesis pattern).

### S3 — Minimal coverage ledger as data (gated on S2)

Not the skill's full surface × boundary × subsystem × attack-class
matrix. One file, `doc/local/security/coverage-ledger.json`, seeded with
exactly the dimensions the two drift failures exposed: **route (or
ingestion surface) × attack class × review date × method × verdict**,
plus a `changed_since` fingerprint derived from the source. Then:

- A tiny validator (house Python, or the skill's zero-dep
  `validate-coverage-ledger.cjs` behind the Node gate like `md-lint`)
  that refuses a ledger where a route in `app.py` has no row — the exact
  failure that missed the 13 routes and mis-counted `refresh`.
- A `doc_query`/`script_query` index entry so "has this surface been
  reviewed" is a query, not a re-read of a 40 KB markdown file.

Success criterion: running the validator against the current tree fails
(it should — the ledger starts empty), and after seeding it passes, and
adding a 39th route to `app.py` makes it fail again. If that loop does
not hold, the ledger is not earning its keep and S4 is abandoned.

### S4 — Adversarial-validation trial (gated on S3)

Run the S2 review a second time with one `rlm.spawn` child as verifier
whose only job is to refute the first pass's clean verdicts. This is the
cheapest possible test of §C.3: one trial, no permanent harness. **Result (Addendum 5): not low-yield — standing doctrine.** The
verifier corrected the reason behind one clean verdict (`_ATTR_RE` IS
quadratic; the length guard is the load-bearing control), refined
AVAIL-1's severity (the default request is already the worst case), and
found two new confirmed findings: **AVAIL-2** (cubic ReDoS in the metric
extractors) and **CONC-1** (shared graph connection returning wrong rows
under concurrency). The next audit runs with a refuting verifier by
default.

## 3. Gates

- Each slice's doc output is md-lint clean (`doc/` is the remediable
  surface) and lands as a numbered Addendum to the evaluation doc — no
  parallel artifact tree.
- `make search-fresh` advisory after each doc change (operator applies).
- Regression tests for any confirmed finding ride `make qa` in existing
  per-surface test files; no new marker, no new runner.
- No target-source change inside this proposal — a confirmed finding
  spawns its own smaller fix proposal (as S1 did none).

## 4. Non-goals

- The skill's full six-phase orchestration, hunter waves, and critic
  loop. S4 is one verifier, one time.
- The 11-step artifact-promotion procedure — this repo does not execute
  attacker code (see Addendum §E for the one future condition that would
  change this).
- Full cumulative carry-forward (fingerprint-keyed prior records,
  `prior_confirmed_changed_source` revalidation units). It is gated on S3
  actually being used across a second run; building it before then is
  speculative machinery.
- Deploy-gated items, unchanged from 2026-08-17: SEC-5 Phase 4 auth,
  `uv lock` / extension pinning (D8). Decision-level, not work.
- Reopening the 25 pre-close routes' recorded coverage; only changed
  source would do that, and that is the ledger's job (S3), not a manual
  re-read.
