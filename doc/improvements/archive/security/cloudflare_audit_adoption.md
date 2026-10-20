---
title: "Cover per-surface attack class in the security review coverage ledger"
status: executed
filed: "2026-10-20"
executed: "2026-10-21"
completed_md: "423"
area: helpers/validators, helpers/pdf, doc/security, Makefile
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# Cover per-surface attack class in the security review coverage ledger

<!--
House proposal skeleton (matches the executed corpus's shape — see
doc/improvements/archive/ for real examples). Rules:

- File the proposal BEFORE implementing multi-slice work (house rule
  2026-08-21). One proposal per arc; slices inside it.
- Every number is MEASURED on this box — a proposal with unmeasured
  claims gets challenged; keep the raw log in the Appendix.
- Tables for comparisons, prose for causality.
- On EXECUTED: git mv to ../archive/<topic>/, completed.md entry (unique
  number), pending.md sweep, archive/README topic line, README pointer
  reset, `make search-fresh APPLY=1` — the full checklist lives in the
  proposals README.
- If a proposals frontmatter contract lands, this template gains it —
  until then the bold-line header below is the canonical status field.
-->

**Date:** 2026-10-20 · **Status:** EXECUTED (completed.md #423) ·
**Area:** helpers/validators (coverage ledger + findings validator)

## 1. Motivation

The security review coverage ledger (`doc/local/security/coverage-ledger.json`)
has a completeness invariant that is **per surface**, not per
(surface, attack class). A route reviewed only for `injection` looks as
covered as one reviewed for every class, because `check()` only verifies
that each route has ≥1 row. The blind spot is therefore large: 38 routes
× 4 classes = 152 pairs, of which only 49 are covered and **119 are
unaccounted** (neither a coverage row nor an exclusion).

The upstream Cloudflare security-audit-skill (commit
c1c8a8c1471069fb0e188eeaff69b8e8db6564a8, MIT) addresses this with a
status enum (`planned`, `not_applicable`, `out_of_scope`, `in_progress`,
`covered`, `candidate`, `blocked`, `deferred`), required `blocked_reason`
/ `unresolved` fields for `blocked`/`candidate` units, and a
machine-checkable `findings.json` with exactly three verdicts
(`confirmed` / `rejected` / `needs_validation`) plus anti-inflation rules
(severity ≤ demonstrated impact; no `severity` on unvalidated findings).

Trigger: user asked to evaluate the upstream skill and record observations
in `doc/local/notes/cloudflare_audit.md` (2026-10-20). The evaluation was
done there; its findings are captured in §2 below as the evidence base
for this proposal. The scratch note is to be deleted after capture.

## 2. Evidence (measured 2026-10-20, this box; derived from the evaluation note)

| Configuration | Result | Verdict |
|---|---|---|
| status quo ledger | 49 rows / 42 surfaces; 119 of 152 pairs unaccounted; `verdict` is free text (42 rows `clean`, 7 carry sentences) | baseline |
| A1–A3 committed (7a4e34feb) | STATUS_ENUM active; `INVALID STATUS` reject; `UNREVIEWED CLASS` warn; blocked/candidate require `blocked_reason`/`unresolved` | adopt |
| A4 committed (fe246aa21) | `security_findings.py` (482 lines) + tests; three verdicts, anti-inflation pair, trace ordering, fingerprint rules | adopt |
| A5 committed (fe246aa21) | anti-inflation rules in `doc/procedures/ocr_review.md:226-231`: severity ≤ demonstrated impact; no severity on an unvalidated record | adopt |
| A9 implemented (working tree) | `coverage_ledger.py plan` + miniature `history`: prior brief, per-class tallies, work queue, GAP/OPEN/STALE/ORPHANED, `history: N prior run(s)`; +7 tests (28 in file) | adopt |
| Q1–Q4 answered, scope landed (working tree) | row-level `scope` enum (7 families) + `INVALID SCOPE` + `--scope` plan filter (46 app / 3 extractors on the real ledger); ledger moved to **tracked** `doc/security/coverage-ledger.json` — verdict *token* tracked, prose stays in the gitignored detail file; findings = per-run `doc/security/findings/<run-id>.json`, linked by run id | adopt |
| A2 implemented (working tree) | `classes_by_scope()` makes the required class set **per scope**; `EXCLUSION WITHOUT REASON` is an error for `not_applicable`/`out_of_scope`; `UNREVIEWED CLASS` is an error under `--strict` and now names its scope; `plan` prints `queue by scope:` and `EXCLUDED <class> @ <surface> — <reason>`; +5 tests (33 in file, 73 with the findings suite); real ledger queue 119 → **110** pairs, `app=110, extractors=0` | adopt |
| Run envelope + `make security-audit` driver (working tree) | a findings file may be `{schema: security-findings.v1, run, scope[], ledger, findings[]}` (bare arrays still valid); `$.run`/`$.scope` validated against the ledger's families and **a finding cannot widen its run's scope**; `check --scope` is the close-out gate (route inventory only demanded for `app`); the driver runs `plan --scope` → run findings check → scoped gate (`SCOPE`, `RUN`, `STRICT`) | adopt |
| A7 implemented (working tree) | `coverage_ledger.py critic` argues against the claim instead of restating it: `(surface, class)` pairs with **no row and no exclusion**, drifted fingerprints, orphaned rows, declared exclusions with their reason, and with `--findings` every `confirmed` claim the ledger does not back with a covered row; `verdict:` COMPLETE or INCOMPLETE over those counts; `--json` (`schema: coverage-critic.v1`) for machine use; advisory exit 0, `--strict` exits 1 on INCOMPLETE; real ledger `app` = 39 surfaces / 110 uncovered pairs, `extractors` = 0 pairs but says so rather than implying completeness; +7 tests (46 in file); `make security-critic SCOPE=… RUN=… JSON=1 STRICT=1` | adopt |
| A6 implemented (working tree) | `security_findings.py brief` emits a **blind** packet for one candidate — an allowlist of `title`/`surface`/`attack_class`/evidence anchors/entrypoint, never `root_cause`, the trace, `execution`, `remediation` or `severity` (the mechanism is what is under test); `--record <result> --out <file>` merges the verifier's `reproduced`/`disproved`/`inconclusive` back without inflating: `disproved` → `rejected` (claim kept as `claimed_root_cause`), `reproduced` → `confirmed` **only if the record already carries the confirmed shape** (else stays `needs_validation` and names what is missing), `inconclusive` → `needs_validation` + blocker; a `verification` block records by/at/verdict/method/command/observed; the merged doc is re-validated before it replaces the output and the input file is never overwritten; procedure §4b + `make security-verify FP=… RUN=…`; +15 tests (58 in file) | adopt |
| Not adopted | upstream .cjs validators, multi-agent wave machinery, sandbox promotion, full-audit mode | dead |
| Render-amp fix + defaults (working tree) | the sweep's one `needs_validation` candidate promoted and closed: `helpers/pdf/render_limits.py` — `checked_open` enforces `MAX_PAGES 200` / `MAX_PAGE_PTS 4000` (defaults far above the 0.8 MB / 28-page A4 Reports corpus) at open, before any page surface is allocated, closing the document on refusal and reading caps as module attributes at call time; wired into all 4 `pymupdf.open` sites (`teleocr_engine.render_pages`, `liteparse_engine._extract_images_sidecar`, `pdf_local.convert` via `to_markdown(doc)` in try/finally, `verify_extraction._source_pages`); `pdf_conv_md --max-pages/--max-page-dims` override per run and a refusal is terminal for every engine (stderr hint, exit 2). Post-fix probe: the same 819 B / 6000x6000pt hostile PDF is refused in 0.6 ms / 460 KB RSS (was 496 KB pixmap / +509 MB / 3.5 s, 606x). Finding promoted `needs_validation` → `confirmed` (`2026-10-21-extractors-2.merged.json`, severity low ≤ impact medium per R1, confidence high) + remediation evidence; ledger row 51 amended → `REMEDIATED` via `apply --decisions`; +7 regression tests (`tests/test_render_limits.py`) | adopt |
| A10 implemented (working tree) | the attack-class taxonomy is now DECLARED, not row-emergent: `doc/security/class-taxonomy.json` (`security-class-taxonomy.v1`, per-scope applicability — app carries the four observed classes, extractors gains the new `parser-input` token for ingestion of attacker-controlled formats through vendored parsers; `_load_taxonomy` is a loud gate: missing file keeps row-emergent demand unchanged, malformed shape exits with `class-taxonomy:`); `classes_by_scope` demands `declared ∪ observed`, so a class with zero rows is named by `critic` (`never covered: parser-input`) and by `queue` before the first row exists. The queue's reasons are class-aware: `read_only_route` is terminal only where the class needs body volume or state mutation (availability, concurrency); injection/auth on a GET-only route stay demanded downgraded to `query_arg_sink` (the query string is still a sink). No findings-schema change — `attack_class` stays a free token; tests: +3 declared-taxonomy tests (57 in file) | adopt |
| A10 groundwork implemented (working tree) | the work queue follows the graph's triage idiom: `coverage_ledger.py queue` emits reason-coded JSONL (`exclusion_pattern` when siblings share an identical exclusion note, `not_a_route`, `mutating_route`, `read_only_route`, `query_arg_sink` — one reason per row, histogram sums to the queue length); `coverage_ledger.py apply --decisions <jsonl>` (`--write` dry-run-default) applies `cover`/`exclude`/`defer` and echoes `bad_pair`, refuses any row the current queue does not still name, stamping every written row with the live fingerprint; `critic --exclude-tripwire T` warns when the exclusion share of accounted pairs exceeds the threshold. `make security-queue` / `make security-apply DECISIONS=… WRITE=1`; +7 tests (54 in file) | adopt |
| A10 first sweep complete (working tree) | the parser-input sweep over the three ingestion surfaces (2026-10-21): `capture_newsletter_images.fetch` and the derive-regex surface carry evidence-backed `not_applicable` exclusions (https-only + magic-gate + capped read; plain `re` over extracted text); `pdf parse (pymupdf.open sites, helpers/pdf/*)` keeps `note:low` now WITH the measured evidence — render amplification 77x/275x/606x (819 B hostile PDF → ~509 MB RSS via MuPDF page raster), no page/byte/dimension cap at the 5 `pymupdf.open` sites, candidate filed `needs_validation` (`doc/security/findings/2026-10-21-extractors-2.json`, strict-valid). Mechanism support: `apply` gained the `amend` decision kind — the re-review path that revises an existing row's verdict/status/note/reviewed in place (demands an already-accounted pair and an ISO date), so a sweep verdict on a covered pair no longer needs a hand-edit; +2 tests (60 in file) | adopt |
| Scope inventories implemented (working tree) | the five families that never had one (`graph`, `harness`, `desktop`, `frontend`, `data`) now have a surface inventory in `helpers/validators/security_scopes.py` (areas + the executed audit arc behind each), wired into the critic's surface set so the lane can judge them; 14 disposition rows (9 covered against their arc, 5 `not_applicable` with a reason) seeded through `apply --write`, taking the ledger from 162 to 176 rows; the inventory's area strings ARE the ledger's surface names, and `security_scopes.py sync` reports the drift the critic cannot see; the inventory caveat (hand-enumerated areas vs the app's machine route walk) is printed as a caveat, not a verdict blocker, so a family can close. `make security-scopes SCOPE=… SYNC=1` | adopt |
| All seven scope families dispositioned (working tree) | `helpers/validators/security_scopes.py` enumerates each non-app family's security surface AREAS with the audit arc that covered them (graph 3, harness 4, desktop 2, frontend 3, data 2, extractors 3); the critic's non-app surface set now comes from that inventory instead of the ledger alone, and the hand-enumerated caveat is a CAVEAT (printed with the verdict) rather than a blocker — otherwise a family could never close; `sync` reports inventory/ledger naming drift, which the critic cannot see (an area and a row that spell the surface differently read as an uncovered pair on one side and an orphan on the other); 14 disposition rows applied through the mechanism (9 covered with arc pointers, 5 exclusions with reasons); the extractors areas were renamed to the ledger's surface names for that reason. `make security-scopes SCOPE=…` / `SYNC=1`; all seven scopes now report `verdict: COMPLETE` with 0 uncovered/stale/orphaned/unbacked (176 rows); +3 tests (58 in file) | adopt |

**§3 mapping table (20 rows):** 7 gaps, 4 already ours, 2 partial, 1 do-not-adopt. Key gaps:
- Coverage unit shape: ours is (surface, attack_class) — upstream is (surface, boundary, subsystem, attack_class); no boundary/subsystem granularity.
- `attempts[]` with per-attempt evidence and reassignment_reason: we detect staleness but keep no history.
- `excluded_blocks` / `selected_companion_blocks`: absent — we cannot say what we did not look at.
- `unresolved` (why blocked): absent — a blocked lead is silently dropped.
- `findings.json` three-verdict shape: ours lives in prose only (disposition sheet + gitignored report).
- Severity ≤ demonstrated impact; no severity on `needs_validation`: no anti-inflation rule.
- Coverage-critic wave (hunt gaps, not bugs): `_uncovered_legs` reports tooling gaps, not surface gaps.
- Terminal states with exact reason: our report has a price table and Known limits, not a per-run coverage statement.
- Prior-run carry-over: we do not consult the ledger before a review.
- Scoped run must present as partial: our review is diff-scoped but the artifact does not say so.

Already ours:
- `trace` = entrypoint→propagation→sink (AGENTS.md "cite the hit" doctrine is its machine-checkable form).
- Budget reserve before hunting ("two legs make the §4b validity sweep mandatory").
- Local-validation boundary (venv, gate, mutation checks).
- Audit does not modify target source (§4 "Report findings; do not act on them unilaterally").

Do not adopt: multi-agent wave machinery, sandbox promotion, full-audit mode, `.cjs` validators as-is, their `ATTACK-CLASSES.md` prose.

## 3. Design

Adopt the machine-checkable bookkeeping from the upstream skill; do NOT
adopt the machinery (no Node engine, no multi-agent waves). Ten candidates,
ranked by the evaluation note:

**A1** — status enum instead of free-text `verdict` (S1, ~0.5 d). `status`
∈ `clean | noted | remediated | out_of_scope | not_applicable | deferred |
blocked`; `note` for the sentence; `method` enum.

**A2** — record deliberately-not-reviewed attack classes (S2, ~1 d) —
**highest-value item.** Adopt `ordinary_attack_class_block` +
`selected_companion_blocks` + `excluded_blocks` flattened to our row shape;
a surface missing an attack class must carry an exclusion entry with a reason.

**A3** — representable blocked (part of A1): `status: blocked` with required
`unresolved` string naming the missing fact and how to obtain it.

**A4** — machine-checkable `findings.json` with three verdicts (S2, ~1 d):
`confirmed` (trace, severity, evidence), `rejected` (claim + why false),
`needs_validation` (claim + blocker + plan, **no severity**).

**A5** — two anti-inflation rules in the severity table (S1, ~1 h): overall
severity ≤ demonstrated impact; no severity on unvalidated.

**A6** — verifier ROLE (S3, ~1 d): fresh agent, one candidate, never shown
finder's reasoning, must reproduce or disprove.

**A7** — coverage critic for the security arc (S3, ~1 d): emit
(surface, attack_class) pairs with no row and no exclusion, plus stale
fingerprints — the operator's work queue.

**A8** — coverage statement in every review report (S1, ~1 h): legs ran,
uncovered, out of scope, known limits.

**A9** — read the prior ledger before planning a review (S2, ~1 d): ledger
as input to review; `history` array in miniature.

**A10** — widen attack-class taxonomy with exclusions (S4, ~2 d): only after
A2; a security arc, not a code change.

Cheapest-first order: **A5, A8, A1, A3** (≈2 h) → **A4, A9** (≈2 d) →
**A2, A6, A7** (≈3 d) → **A10** (operator arc).

## 4. Acceptance criteria & shakedown

1. `python3 helpers/validators/coverage_ledger.py` exits 0 on the live
   ledger when every surface-class pair is covered or explicitly
   excluded; exits 1 with `UNREVIEWED`, `STALE`, or `ORPHANED` lines
   otherwise.
2. `pytest tests/test_coverage_ledger.py tests/test_security_findings.py`
   → all green (57 tests today).
3. Repeat-count: run the pytest pair 3 consecutive times — all three runs
   green.

Non-goals for this arc: the upstream `.cjs` validators (port the rules,
not the Node engine), the multi-agent wave machinery, the sandbox
promotion procedure, full-audit mode as an operating mode, and any change
to `doc/procedures/ocr_review.md` (the A8 coverage-statement rule is
explicitly not adopted).

## 5. Risks

- **The ledger lives under `doc/local/`, which is gitignored.** So A1–A4
  are committed code, but the data they validate (`coverage-ledger.json`)
  is local scratch — the operator must stage/commit it separately if they
  want it tracked. Mitigation: move it out of `doc/local/` (e.g.
  `doc/security/coverage-ledger.json`) or force-track it.
- **A2's `known_classes` is derived from classes present in the ledger.**
  If no row anywhere names `availability`, no `UNREVIEWED CLASS` warn
  fires for it. Seed `not_applicable` rows for classes never reviewed.
- **Operator ruling (m12017):** "park the implementation for now but don't
  delete anything." Nothing was deleted; the committed work stays. This
  proposal exists to file the arc before any further slices land.
- **Do not delete `doc/local/notes/cloudflare_audit.md` until its findings
  are captured here** (this file). After capture, delete it per operator
  instruction — it is gitignored scratch, not durable.

## 6. Non-goals

- Adopting the upstream `.cjs` validators as-is (Node engine — port rules
  only).
- The multi-agent wave / critic machinery.
- Sandbox promotion procedure.
- Full-audit mode as an operating mode.
- Any change to `doc/procedures/ocr_review.md` or `static_checks.py`
  beyond the already-committed sqlite allowlist (unrelated to this arc).
- `make` targets `coverage-ledger` / `security-findings` (not added at HEAD).

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-20 | `grep -n "UNREVIEWED CLASS" helpers/validators/coverage_ledger.py` | A2 warn rule present | committed at 7a4e34feb |
| 2026-10-20 | `git show HEAD:tests/test_security_findings.py \| grep -c "def test_"` | 28 | committed at d8735a2af |
| 2026-10-20 | `pytest tests/test_coverage_ledger.py tests/test_security_findings.py -q` | 57 passed | this box |
| 2026-10-20 | `wc -l doc/local/security/coverage-ledger.json` | 49 rows | ledger data, gitignored |
| 2026-10-20 | upstream fetch | `cloudflare/security-audit-skill` @ c1c8a8c147 | MIT, 2026-09-14 |

## Decisions (answers to the 2026-10-20 questions, 2026-10-21)

1. **Scope — add it.** Repo-wide is the target (Q4: full-audit mode for
   the whole repo, not only the 38 Flask routes). Row-level `scope` from a
   fixed enum: `app`, `extractors`, `graph`, `harness`, `desktop`,
   `frontend`, `data`. Legacy migration: 45 `route` rows -> `app`,
   3 `ingestion` -> `extractors`, 1 `shared` -> `app` (46 / 3 on the real
   ledger). `check()` rejects an unknown scope (`INVALID SCOPE`);
   `plan --scope X` prints only that family's queue. Outside `app` there is
   no route inventory, so a non-app queue is its open/stale/orphaned rows
   rather than a route x class cross-product.
2. **Track it — state only.** The repo is public, and `doc/security/routes.json`
   already withholds `attack_class`, `verdict`, `reviewed`, `method` and
   `fingerprint` for that reason. So the tracked ledger carries the verdict
   *token* (`clean` / `note:low` / `REMEDIATED`) and the rest of the state;
   the free-text verdict sentences stay in the gitignored detail file named
   by the tracked ledger's `detail` key. Coverage history survives a clone;
   posture prose does not get published.
3. **Findings — two files, not a discriminator.** The ledger is *state*
   (cumulative, read-modify-write); findings are *events* (per run,
   immutable). One file would push findings through the ledger's
   read-modify-write path - two concurrent runs clobber each other - and
   churn the state file on every run. Hence `doc/security/findings/<run-id>.json`,
   validated by `helpers/validators/security_findings.py`, sharing the same
   `scope` vocabulary. The link is by run id: the ledger's `history[]` entry
   names the run; the run's findings file is its detail. The ledger never
   embeds findings.
4. **Repo-wide, in scope order.** A2's known-class derivation runs per
   scope, so a class excluded on `app` does not silently apply to `graph`.
   The `app` pass is first because it already has 49 rows to diff against.

**Still open:** nothing in the A10 arc — the code side (declared taxonomy,
class-aware reasons, `parser-input` token), the five scope inventories, the
first parser-input sweep (2026-10-21) and its one candidate (the
render-amplification fix + defaults, see the status table) are all in the
working tree. A8 stays excluded (see the
non-goal above). A2, A6, A7, A9 and the run envelope are implemented in the
working tree (see the status table).
