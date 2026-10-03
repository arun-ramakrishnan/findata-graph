---
title: "markdown_parse.md procedure audit — false claims, unguarded gates, too many manual steps"
status: executed
filed: '2026-10-03'
executed: '2026-10-04'
completed_md: '340'
area: pipeline
---

# Proposal — `markdown_parse.md` audit: false claims, unguarded gates, and too many manual steps

- **Status:** executed 2026-10-04 (archived; completed.md #340) — Part 1 doc corrections landed 2026-10-03; code slices
  S4–S8 DONE 2026-10-04; S1 DONE 2026-10-04; S2 DONE 2026-10-04 (verify flag); S3 approved, landed;
  the extractor write-gate eval key (58 items, all hermetic) is **DONE
  2026-10-04**: noise dropped 49/49 recall 1.0, non-noise kept 9/9
  recall 1.0, bucket agreement 8/9 within the slice universe,
  truncation 4/8. (Key:
  `doc/local/evaluations/jev_pilot/extractor_gate_key.py`.)
- **Audit date:** 2026-10-03, main tree @ `f0e57b27c`, worktree @ `c232c1468`
- **Scope:** `doc/procedures/markdown_parse.md` (713 → 834 lines) and the guards it asserts exist
- **Trigger:** an ingest arc in which every defect encountered was a guard the procedure *asserted* rather than *verified*. Two of those guards had already been documented as fixed.

## 0. Summary

The procedure is the spine of the vault: stages 0–11, and every `derive_*`
surface we have judged this month hangs off it. It is also the best-written
procedure in the repo — dense, cited, with diagrams and rationale.

The audit found a consistent failure mode rather than scattered typos:

> **The procedure documents the happy path and assumes each stage's guard
> works.** Every defect in this arc was a guard believed but never measured —
> the write-time noise gate, the queue dedupe, the decision key, the
> stub-note requirement, and the `agent_id` foreign key.

Eleven claims were false or absent (Part 1). Separately, the procedure's
central claim about manual effort is false, and its manual-step design is
the direct cause of the operator-facing confusion that prompted this audit
(Part 2).

The fix pattern adopted for all of it: **for every guard, record what it
actually catches, with a measurement date** — not what it was intended to
catch.

## 1. The audit table

`doc/procedures/markdown_parse.md` said one thing; measurement said another.
"Fixed (doc)" = the procedure text now states the measured truth. "Open" =
a code slice is required, because no amount of prose fixes behaviour.

| # | Procedure asserted | Measured reality (2026-10-03) | What it cost | Status |
|---|---|---|---|---|
| 1 | "Countries/generic-phrase/mangled-fragment targets **never enter the queue** anymore (write-time noise gate in the extractor)" | **False.** `noise_target()` returns `False` for all three live queue rows, with **no clause firing** on any (`_GENERIC_PREFIX`, `_GENERIC_SUFFIX`, `_FRAGMENT_JUNK` all `False`). A deny-list cannot see a fragment that contains a real company token (`"European giant DWS Group l divesting a minority stake"`, 53 chars); the suffix list contains neither `corporation` nor `involving` (`"Sankyu Corporation involving"`); and `"Porsche"` is not noise at all — a real name captured from the wrong sentence | We trusted the gate and never checked row quality. The queue's entire residual was junk the procedure declared impossible | Fixed (doc) + **S5**, **S6** |
| 2 | Stage 9 presents dry-run first, then `--apply` | `make derive-relations` passes `--apply` **unconditionally**; there is no dry-run flavour of the target | The operator ran it expecting a preview and got 29 queued rows plus writes | Fixed (doc + `Makefile` echo) |
| 3 | Sidecar is `findata/_pending_relations.txt`; decisions file `findata/_pending_triage_decisions.jsonl` | Both live under `findata/Misc/` (`extract_relations.py:118`; confirmed by the oct-2 report run) | Wrong path in the procedure *and* in the Makefile echo; anyone grepping or copying the path fails | Fixed (doc + Makefile) |
| 4 | The triage loop closes the queue (step 3 applies decisions) | Decisions key on `_row_id`, a hash of `(edge_type, source, target_mention)` computed **at report time** (`triage_pending_relations.py:209,313`); queue rows carry no id. The hash takes `target_mention` **verbatim** | **All 8** rows of `_pending_triage_decisions.jsonl` orphaned — including a correct `accept:acquired:MHP`. Silent loss of operator work | Fixed (doc) + **S4** |
| 5 | "`make triage-relations` — dedupes" | The **report** dedupes; the **queue file is append-only** across runs | 29 queue lines = **3** distinct rows; report header counts (`prose rows (true queue)`) disagree with the file, so a stale report misleads | Fixed (doc) + **S4** |
| 6 | *(silent)* | Rows from `extract_relations.py` carry no `origin`/`method`/`score`, unlike `coinfer` or link-prediction suggestions | In a shared sidecar you cannot tell which producer emitted a row | Fixed (doc) + **S4** |
| 7 | "Stub creation itself stays explicit" | Never says the **note file is mandatory**, nor that the trees share one DB but not one corpus: `memory/research.db` is a single inode (4359674) across worktrees while `findata/` is per-tree | `Kering Beaute` failed `database_integrity_check` in main for a file committed and clean in the worktree (`c232c1468`) — a phantom defect that consumed a session | Fixed (doc) + **S7** |
| 8 | *(silent)* | `agent_id` has an FK to `provenance_agents` (`ON DELETE SET NULL`) in five tables. Raw `sqlite3.connect` leaves `PRAGMA foreign_keys` OFF, so `''` inserts silently | 5 `graph_edges` rows aborted the `db_maint` chain; found hours later by `foreign_key_check` | Fixed (doc) + **S8** |
| 9 | Stage 10 documents only the command | **Zero derived events is a legitimate outcome** — both extractors are gated conjunctions (Part 3) | A parallel session treated a 0-event run as a suspected defect | Fixed (doc) |
| 10 | Stage 11 documents the apply | `--stale-only` gated the Key-Figures pass on **pre-write** frontmatter while rendering current text | The first apply wrote **0** Key-Figures notes, silently. Fixed in the worktree patch (pre-write FM snapshot, `TestStaleOnlyTwoPass`) | Fixed (code, in patch) |
| 11 | No stage order stated anywhere | 9 → 10 → 11 is a dependency chain: Stage 11 reads Stage 5's chatter blocks; Stage 10 promotes Stage 9's edges | Stage 11 was run before Stage 9's apply; Stage 9 was dry-run but never applied | Fixed (doc) |

Two further doc defects found while auditing, not in the table above:

- **The corpus fast path cannot read another tree.**
  `extract_from_prose` skips any note whose absolute path is outside its own
  `_REPO_ROOT` (`derive_events.py:572-576`). Combined with row 7, a
  `Corpus.load()` pointed at a sibling worktree yields **zero** notes with no
  error — the same shared-DB/divergent-corpus hazard, silently applied to
  derivation itself. Measured: 1193 notes walked, 0 windows captured.
- **Perf comments are stale by ~3×.** The extractor comments cite 89K
  windows; the current corpus yields **276,716** (138,358 per extractor).

## 2. Too many manual steps, and the asks that cause the confusion

### 2.1 The claim is false

Line 26 states the worklist exists "for **the only manual step** (Stage 5)".
Reality — six stages need a human, and three of them begin by asking the
operator a question:

| stage | what needs a human | asks the operator? |
|---|---|---|
| 0 PDF → markdown | destination directory | **yes** — "ask the user for the destination directory first" |
| 1 image capture | confirm the markdown's location | **yes** — "confirm … with the user first" |
| 4 add new entities | create the SQLite record + note | no (implicit) |
| 5 enhance entities | the concall/management judgment | no |
| 9 relations triage | annotate decisions, create stubs, write notes | no |
| 11 insights | verify key figures before apply | no |

The destination ask is not incidental — it is codified as an absolute rule
in **three** places (lines 9, 34, 35, 245): *"ask the user; never assume"*,
*"always ask the user"*, *"there is no safe way to infer it"*.

### 2.2 Why this generates confusing asks

1. **The convention already exists and the procedure forbids using it.**
   Newsletter markdown belongs in `findata/The_Chatter/` — the same three
   dirs the Makefile targets already scan
   (`derive-relations: findata/The_Chatter findata/Points_And_Figures
   findata/The_PlotLines`). Line 245 names that exact directory as an
   example and then forbids inferring it. So the operator is asked a question
   whose answer is already encoded three lines away.
2. **An ask with no expected answer defeats the purpose.** The rule gives no
   criterion for what the agent should do with the answer — no default, no
   validation, no "if the answer is ambiguous, do X". Every ingest therefore
   becomes a negotiation instead of a step.
3. **"Done" is asserted, never verified.** No stage states its expected
   output, so "stage 11 ran" is not evidence that Key-Figures notes were
   written (row 10 is exactly this). The operator discovers the gap by
   counting rows.
4. **The manual work is spread across stages.** A single ingest touches 0/1,
   5, 9 and 11, so an operator answering one question at stage 0 is making
   decisions for four stages with no consolidated view.

### 2.3 Target state

Four rules, applied to every stage:

- **Convention over ask.** Resolve the destination from the vault layout;
  ask only when the convention does not resolve, and say precisely what is
  ambiguous.
- **Decide everything decidable before asking.** A pre-flight computes what
  it can (entity matches, dedupe counts, duplicate collapses, unparseable
  rows) so the question set contains only genuine judgment.
- **One brief, not scattered questions.** The operator gets a single
  consolidated decision brief per ingest — most-contested-items only, plain
  English, with the recommendation and the alternative. This is the
  `system_one` typed-judgment surface (`system_one_typed_judgment_framework.md`).
- **Expected-output assertion per stage.** Each stage states the numbers it
  must produce, so completion is checkable rather than claimed. "0 events" is
  a valid assertion for Stage 10 (row 9); "0 Key-Figures notes" was not
  discoverable for Stage 11 (row 10).

Resulting question budget for a normal ingest: **0** for a convention-
resolving PDF (was 1), and **1 consolidated brief** instead of one question
per stage for the judgment work.

## 3. Evidence appendix — Stage 10 decision tree, measured

Wrapping the real extractors and classifying every production window with the
module's own regexes (2026-10-03, 1193 notes, main-tree corpus):

**Guidance** — a 4-leg conjunction, 138,358 windows:

| gate | in | out | rejected |
|---|---|---|---|
| literal `fy`/`cy` prefilter | 138,358 | 6,911 | 131,447 |
| + fiscal token (`_FY_TOKEN_RE` / `_CY_QUARTER_RE`) | 6,911 | 2,487 | 4,424 |
| + metric (`_PCT_RE` / `_MONEY_OR_KEYWORD_RE`) | 2,487 | 1,662 | 825 |
| + forward signal (`_FORWARD_RE`) | 1,662 | **530** | 1,132 |

**Management change** — 1 positive and 3 negative gates, 138,358 windows:

| gate | in | out | rejected |
|---|---|---|---|
| verb-token prefilter (`_MGMT_VERB_TOKENS`) | 138,358 | 8,815 | 129,543 |
| + `_CHANGE_VERB_RE` | 8,815 | 52 | 8,763 |
| − `_ACQUISITION_SENSE_RE` | 52 | 49 | 3 |
| − `_MODE_SENSE_RE` | 49 | 48 | 1 |
| − `_APPOINTED_TITLE_ATTR` | 48 | 46 | 2 |
| + `_TITLE_RE` | 46 | **6** | 40 |

Emitted: **333** events (330 guidance, 3 management_change). Near-misses —
windows passing the prefilter and failing exactly one later gate — are the
eval-candidate pool: **1,681** guidance (1,132 forward, 362 fiscal, 187
metric) and **43** management.

Three findings from the same walk:

- **`magnitude` is NULL for 192 of 330 guidance rows (58%)** — the metric
  matched by money/keyword with no percent. Any consumer reading `magnitude`
  as a quantity meets NULL two times out of three.
- **289 of 333 derived rows have no `event_date` (87%)**, and
  `date_precision` is NULL for **all** 333 — while the module docstring
  calls `events` "the canonical temporal spine". Decide whether that is
  acceptable or a defect; it is currently invisible.
- **`magnitude` carries the executive title** for `management_change`
  (`derive_events.py:466`), a text value in a numeric column.

The measurement should become a feature, not a scratch script: propose
`derive_events.py --gate-stats`, which prints the funnel and the near-miss
pool, so every future corpus change is measurable. Today's run lived in
`$TMPDIR` scratch and is not reproducible from the repo.

## 4. Slices

Each slice states its eval gate, per `doc/procedures/gates.md`: targeted
tests only, full gates once per arc on the operator's go.

| slice | work | eval gate | owner |
|---|---|---|---|
| **S0** | Doc corrections for rows 1–11 + the two extra defects | md-lint clean; procedure claims match `noise_target()` / `_row_id` / target behaviour | **done** (worktree, uncommitted) |
| **S1** | Replace the three destination asks with the vault convention + one fallback ask that names the ambiguity | a PDF landing in a conventional dir triggers **no** question; a non-conventional one asks once | agent — **DONE 2026-10-04** (below) |
| **S2** | Per-stage expected-output assertions + idempotency contract (what "done" means, what re-running does) | each stage's assertion is asserted by a test or a `--verify` flag | agent — **DONE 2026-10-04** (verify flag + 8 hermetic tests; idempotency pinned by S4/S6 tests) |
| **S3** | One consolidated decision brief per ingest (Stage 5 + 9 + 11 judgment), replacing scattered asks | brief lists only contested items; each carries recommendation + alternative + why | agent (`system_one` client) — approved 2026-10-04 |
| **S4** | Queue hygiene: dedupe on append (as `suggest_relations.append_suggestions` already does), stamp a **stable id into the row at write time**, add `origin`/`method` provenance | re-running the extractor appends 0 duplicates; a decisions file written before an extractor change still joins | agent — **DONE 2026-10-04** (below) |
| **S5** | Sentence-integrity gate: the relation verb and the counterparty must co-occur in one sentence, so a newsletter header cannot yield a row | the `"Porsche"` shape is dropped at write time with a `reason` | agent — **DONE 2026-10-04** (below), gate is structural not co-occurrence |
| **S6** | Truncate predicate-absorbing mentions (`"European giant DWS Group l divesting a minority stake"` → `"European giant DWS Group"`, `"Sankyu Corporation involving"` → `"Sankyu Corporation"`) at the first predicate boundary; do **NOT** blacklist `corporation`/`group` | the mention becomes the entity name; the row resolves or buckets correctly; the row is **never** discarded | agent — **DONE 2026-10-04** (below) |
| **S7** | Corpus/tree coherence: warn (do not fail) when an accepted entity's `file_path` does not resolve **in this tree**, and say which tree has it | main reports `Kering Beaute` as missing-with-location rather than missing | agent — **DONE 2026-10-04** (below) |
| **S8** | Provenance hygiene (the approved hardening arc): self-diagnosing `agent_id` probe in the maintenance gate + a stored guard | `db_maint` names unregistered/empty `agent_id` explicitly; the guard rejects `''` | agent — **DONE 2026-10-04** (below) |

## 4.1 S4 + S5 as landed (2026-10-04)

**S4 — identity, dedupe, provenance.** `row_id()` is now canonical in
`triage_pending_relations.py` and hashes the *normalized* triple (NFKC,
casefold, boundary punctuation, possessive, collapsed whitespace) instead
of the verbatim mention. `Unresolved.__post_init__` stamps it at write
time; `build_triage` prefers the stamped value and computes it only for
legacy rows, so both paths agree. `write_sidecar` dedupes on that id —
against the existing file *and* within the batch — so the dedupe key and
the decisions key are the same key and one row can never be ambiguous
between two decisions. Rows carry `origin` and `method`
(`extract` / `regex:<edge_type>`), matching the vocabulary the coinfer
producer already wrote.

Two defects surfaced only because S4 made provenance keys always present:
`_apply_write` read `d.get("origin", "manual_triage")`, so a legacy row's
*empty* origin overrode the default and stamped accepted edges with
`origin: ""`. Fixed with `or` rather than a `.get` default.

`write_report` no longer wipes annotations: prior decisions are carried
forward onto the regenerated rows, matched on the **normalized triple**
rather than the id, so a decision survives both an id change and a
re-capture that only shifts punctuation or case.

**S5 — sentence integrity, and a correction to the slice.** The clause as
written ("verb and counterparty co-occur in one sentence") is **vacuous
for regex rows**: the pattern matched, so the verb is inside the sentence
by construction, and the test cannot fail on any live row. The failure it
was written for is *structural* — `**MHP acquired from Porsche:**` is a
newsletter bold label, and `PATTERNS[4]` (the reverse `acquired from X`
lane) matched happily inside it. So the gate tests prose-ness (heading,
table row, bold-label span) plus mention-within-one-sentence, and keeps
the co-occurrence assertion only as a cheap guard for future non-regex
producers. Every skip is counted by reason and printed by the CLI
(`sidecar_skipped=…`), never silently filtered.

Verified against the real corpus: the Porsche row is dropped
(`label_not_prose`); the DWS and Sankyu rows are kept, because they are
prose whose *mention absorbed the predicate* — S6's problem, not this
gate's. Sentence derivation reads `body`, never the stored `quote`: that
field is a mid-word-clipped 120-char window on 31/31 live rows.

**S6 — truncate, never discard.** `truncate_mention()` cuts a mention at a
closed set of predicate boundaries and strips a trailing lone lowercase
letter (OCR garble: `"Group l"`). Deliberately **not** a blanket `-ing`
rule — real names end in `-ing` (Sterling, Huntington, Genting) and
`"Indian Bank"` must survive; there is a test pinning each. Only a suffix is
ever removed: leading tokens are name elements (`"European Airlines"`).
The original mention is kept on the row as `truncated_from`.

A doctrine interaction had to be fixed alongside it. `_bucket` sends an
*exact* existing name to `stub_candidate` on purpose ("the extractor should
already have resolved it"), so after truncation `"Sankyu Corporation"` —
already an entity — landed in the stub lane, i.e. an operator accepting that
row would create a **duplicate stub for a name already in the graph**. Exact
name hits now bucket `manual` / "already a known entity — resolver gap",
which is the honest reading: the queue row is a resolver miss, not a new
entity. Measured buckets: `Sankyu Corporation involving` →
`Sankyu Corporation` → `manual`/resolver gap (was `alias_candidate` by
fuzzy word-overlap on the whole fragment); `European giant DWS Group…` →
`European giant DWS Group` → `alias_candidate → DWS (word_overlap, 1.00)`.

**S6 eval result — the hypothesis was wrong, and that is the finding.** The
slice was justified partly on the theory that a name+predicate fragment made
the pre-annotator's Q2 question ill-posed, so truncating would stabilise the
verdict. Measured on the same three rows, three repeats each:

| queue | admits per run | escalated | needs_retry |
|---|---|---|---|
| untruncated (S4/S5 only) | 0, 1, 2 | 3, 0, 0 | 0, 0, 0 |
| truncated (S6) | 0, 2, 0 | 3, 0, 0 | **2, 0, 0** |

Truncation did **not** stabilise the verdict — it swings just as hard. So
the instability is **not** caused by the malformed mention; it is the
non-reproducible endpoint already measured on the S4/S5 lane (`call_glm`
pins `temperature: 0`, yet one row was bit-identical across runs while
another flipped boolean at *identical* reported confidence). Truncation is
still correct — it fixes what is stored, resolves the stub-candidate hazard,
and makes the Q2 question well-posed — but it is not a reproducibility fix.

**Two consequences to hand on.** (1) Reproducibility must be solved at the
transport (pin `seed`, or majority-of-N, or treat any `p_*` below ~0.7 as
"human decides" rather than a boolean); every eval number in the jev arc,
including the eval-v3 floor, is a single-run measurement on a
non-deterministic endpoint. (2) A retrieval trade-off: `_search_evidence`
builds its query from `target_mention`, so truncating makes the query
*less* specific (`"Sankyu Corporation"` loses the collaboration context that
made the query findable) — visible as `needs_retry=2` on the first
truncated run. Building the query from the row `quote` instead would recover
it. **Not done here**: that is the search-lane owner's file.

**Tests**: `tests/test_relations_queue_hygiene.py` (33, hermetic) keyed on
the verbatim live rows. Blast radius: 504 relation tests pass.
`tests/test_relation_sidecars.py` had 3 **pre-existing** failures
when this slice landed — not YAML drift: the fixture still encoded the
2026-10-03 B2 batch (count `>= 7`, all `triage:accept`, one slug→confidence
table) while the operator's `aa26455a8`/`13eb1ae48` replaced it with 5
sidecars (2 `triage:accept`, 3 `cleanup:web_verified`). Fixtures
updated to accept both provenance types, expect `>= 5`, and apply the
bucket-confidence formula only to `triage:accept` rows; 10/10 pass.
(Fixtures updated, fold into the same patch — see below.)

**S7 — corpus/tree coherence.** `DatabaseIntegrityChecker` now carries
`other_roots` (default: `git -C <base_path> worktree list`, minus this
tree). `validate_file_path` keeps its verdict (`False`) but, when the
file is *this tree's* visibility gap — the same relative
`file_path` resolves in a sibling checkout — returns
`File does not exist in this tree: <full_path> (present in <sibling>)`
instead of a bare `does not exist`. Two tests pin both branches;
78/78 integrity tests pass. The tree itself is unshared, so no mutation
is made — this only makes the maintenance portability gap say its
location. `make derive-relations` is unaffected; the advisory text is
the only output change.

**S8 — provenance hygiene.** Two halves, both landed in `db_maint.py`:

*Stored guard (DDL, FK-independent).* `install_agent_id_guard(conn)`
installs a BEFORE-INSERT and a BEFORE-UPDATE trigger per fact table
(`graph_edges`, `events`, `quotes`, `company_metrics`, `hyper_edges`)
that aborts with
`agent_id must be NULL or a registered non-empty id` when `NEW.agent_id`
is `''` or not in `provenance_agents`. Because it is a trigger (not a
CHECK), it survives a raw `sqlite3.connect()` — the exact path whose FK-off
stance let five `''` rows through silently, which were only surfaced hours
later by `foreign_key_check` (5 aborted-`db_maint` rows, field note in
row 8 above). `NULL` stays legal ("unknown" is a deliberate state). Live
DB: 10 triggers installed, and a raw client INSERT with `agent_id=''`
now fails with IntegrityError at the causative statement.

*Advisory probe.* `provenance_agent_report(conn)` counts empty /
unregistered `agent_id` rows per fact table plus the registry size, and
is printed by `db_maint` (and stored in the run dict). Current live state
from that report: `{registered: 21, empty: {}, unregistered: {}}` —
the S1 backfill held, and the working-example path (registered ids,
`NULL`-as-unknown) is exactly what triggers allow. Any future drift shows
up as named `{table: count | ids}` sets rather than being buried in a
silent column-scan failure.

Hermetic: `tests/test_agent_id_guard.py` (5 tests — NULL/registered pass,
`''`/unregistered abort, absent tables skipped, probe names drift)
+ `tests/test_db_maint.py` (24) both pass.

## 5. Ownership

- **Deterministic code owns** normalization, dedupe, skip guards, id
  stability, gate predicates. No model.
- **Models are advisory.** They rank what is contested and draft the brief;
  they never write, never approve, never decide a gate.
- **The operator decides** genuine judgment only, from one consolidated
  brief, and owns every write.
- **`maint-full` may report** a brief and must never gate or auto-apply.

## 6. Out of scope

- Gating `extract_relations --apply` itself. The write path resolves edges
  with no human in the loop, and the review-side judgment cannot protect a
  write that already happened. That needs its own proposal with its own
  eval-gate bullet.
- The OCR lane.
- Changing the stage order or removing a stage.

## 7. Verification

Doc corrections: `npx markdownlint-cli2 --no-globs doc/procedures/markdown_parse.md`
clean; every corrected claim carries a measurement date; the false
noise-gate claim and both stale paths are gone (`grep` counts 0).

Code slices are not started; per-slice targeted tests only, full
`make qa` once per arc on the operator's go.
