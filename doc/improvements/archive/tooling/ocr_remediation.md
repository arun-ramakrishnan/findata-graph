---
title: "Remediate the two-leg OCR review of 0b63ce4507 — 15 live findings (2 High) plus one scope adjudication"
status: executed
filed: "2026-09-28"
executed: "2026-09-29"
completed_md: "8185"
area: "helpers/graph/query.py, helpers/graph/extract_relations.py, helpers/misc/parity_harness.py, helpers/validators/static_checks.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Remediate the two-leg OCR review of 0b63ce4507 — 15 live findings (2 High) plus one scope adjudication

**Date:** 2026-09-28 · **Status:** EXECUTED (completed.md #312; arc grew to
S12 — the three-leg review experiment — see the execution log) ·
**Area:** graph query/extraction seams, parity harness, static-check validators

## 1. Motivation

Trigger: a two-leg OCR review of commit `0b63ce4507` (graph-derive
refactor) on 2026-09-28 — a host-driven **delegation leg** (5 findings,
mutation-verified) plus an external **managed leg** (`glm-5.3`, 31 files,
4.02M tokens, 32m53s, 0×429). Accounting: 23 raw reports − 3
reported-by-both − 3 false positives (one PEP 758 blind spot) = **17
distinct real findings**; re-verified against HEAD `530c4e36`, **15 still
live**, 1 already fixed, 1 sitting on a documented scope boundary.

Two of the live items are behavioural regressions, not style:

- **F1 (High)** — `query.connect()` lost the under-`flock` read-only
  downgrade that completed #165 *landed as its core mechanic*
  ("waiters re-check `needs_build` UNDER the lock — a warmed cache
  downgrades read_only callers straight back to the read-only open").
  A `connect(read_only=True)` that queues behind a builder now gets a
  **read-write** connection.
- **F2 (High)** — `extract_relations` changed control flow: a
  list-shaped mention where **zero chunks resolve** used to leave
  `target_entity = None` (noise gate / triage sidecar); it now falls
  through to the whole-mention `resolver.resolve()`, i.e. exactly the
  fuzzy whole-list collapse the chunking exists to prevent.

Vault sweep (doc_query + convo_query + `pending.md` + ripwire greps):
neither was deferred or rejected. F1 is a regression of recorded design;
F2 has no record at all.

## 2. Evidence (measured 2026-09-28, this box)

| # | Sev | Finding | Location | HEAD `530c4e36` | Vault record |
|---|---|---|---|---|---|
| F1 | High | `read_only` never forwarded to `_build_graph_connection`; helper opens `duckdb.connect(str(...))` unconditionally | `helpers/graph/query.py:444,557` | PRESENT | regression of completed #165; known-issue §4 of `gate_xdist_phase2` (#189) |
| F2 | High | zero-resolved chunk list now falls to whole-mention resolve | `helpers/graph/extract_relations.py:2061-2079` | PRESENT | none |
| F3 | Med | guard misses `key=lambda kv: kv[1], reverse=True` / ascending forms | `helpers/validators/static_checks.py:1518` | PRESENT | scope documented `chain_tally_determinism` #305 §3/§5; reverse/ascending never adjudicated |
| F4 | Med | ruff C901 sweep runs `check=False`, returncode/stderr never read → ruff failure ≡ "no findings" | `helpers/validators/static_checks.py:1367` | PRESENT | none |
| F5 | Med | corpus lane lost the `note.as_posix()` relative-path arm; CWD ≠ repo root → `ValueError` → `note.stem` fallback | `helpers/graph/derive_themes.py:224` | PRESENT | none |
| F6 | Med | canary-exclusion test asserts only `main([]) == 0`, which holds even when canaries fail | `tests/test_parity_harness.py:139` | PRESENT | none |
| F7 | Med | `REGISTRY` = `bfs_path` + 2 canaries; none of the six S2 split functions registered | `helpers/misc/parity_harness.py:113` | PRESENT | per-split parity proved at landing (#306); registration never done; c901 D1 deferral adjacent |
| F8 | Low | `renders[0]` indexes by literal seed → KeyError when `--seeds` omits 0 | `helpers/misc/parity_harness.py:253` | PRESENT | none |
| F9 | Low | empty/blank `--seeds` → `seeds=()` → prints `N/N byte-identical`, exit 0 | `helpers/misc/parity_harness.py:295` | PRESENT | vacuity class noted in #306; instance novel |
| F10 | Low | `_first_diff` trailing-newline case reports "length differs (baseline N, working N)" with N == N | `helpers/misc/parity_harness.py:226` | PRESENT | none |
| F11 | Low | every (side, seed) render runs twice (parity loop + determinism loop) → 2× subprocesses | `helpers/misc/parity_harness.py:239` | PRESENT | none |
| F12 | Low | `parity` target missing from `.PHONY` (line 27) | `Makefile:120` | PRESENT | none |
| F13 | Low | inert allowlist entry `findata-graph-desktop/...` | `static_checks.py` sqlite allowlist | **FIXED** (0 hits repo-wide, `ff59665c`) | n/a — dropped |
| F14 | Low | determinism filter's condition never references the outer seed `s` → all-or-nothing seed report | `helpers/misc/parity_harness.py:251` | PRESENT | none |
| F15 | Low | `timer` declared in `_rebuild_full_mode` / `_rebuild_incremental_mode`, never used (0 attribute reads) | `helpers/maintenance/rebuild_convo_search.py:403,416` | PRESENT | none |
| F16 | Low | T8 delta rationale comments (append-only invariant, 11 s re-tokenize, wholesale delete) dropped in the extraction | `helpers/maintenance/rebuild_convo_search.py` | PRESENT | none |
| F17 | Low | `cached_embed_batch` docstring still returns `{"hits","misses","dirty"}` while the dict carries `unique_misses` | `helpers/core/embed_cache.py:247` | PRESENT | none |

False positives recorded for the corpus, so nobody re-litigates them:
the managed leg's three CRITICAL "Python 2 `except A, B:`" reports are
**PEP 758**, valid on this box's Python 3.14.0; `import
helpers.validators.static_checks` registers 20 checks. The delegation
leg had 0 false positives; the managed leg 3/18.

Sweep commands used: `doc_query.py`, `convo_query.py`, `rg` over
`doc/improvements/pending.md` + `status: deferred|rejected` markers,
`ripwire . --grep=<symbol> --grep-in=any`.

## 3. Design

Slices, each independently landable in this order — S1/S2 first (both
High), S3-S6 after:

- **S1 — restore the under-lock read-only downgrade (F1).** Forward
  `read_only` into `_build_graph_connection()`; after `needs_build` is
  computed under the flock, re-check `read_only and not needs_build` and
  route to `_open_read_only_connection()` — exactly completed #165's
  landed text. Keep the caller at `query.py:557` behaviour-neutral.
  Pin with a regression test: cold cache + `connect(read_only=True)`
  while another holder releases the build lock must yield a connection
  that refuses writes (extend `tests/test_graph_disk.py`, whose
  `test_parallel_ro_connects_serialize_the_build` already owns this
  seam).
- **S2 — restore list-mention semantics (F2).** When the mention was
  list-shaped (`len(chunks) > 1`) and *no* chunk resolved, do **not**
  fall through to whole-mention resolve; keep `target_entity = None`
  so the noise gate / triage sidecar runs as before. Extract the
  condition into a named predicate so the intent survives the next
  refactor. **Eval-gate bullet mandatory** (extractor rules — see §4).
- **S3 — validator truthfulness (F4) + guard adjudication (F3).**
  Inspect `proc.returncode`/`stderr` after the ruff sweep and report a
  distinct "ruff failed to run" verdict instead of an empty finding set.
  For F3, adjudicate with the measured probe: extend the AST rule to
  `.items()` sorts carrying `reverse=True` / ascending single-field
  keys, **or** record them in the guard docstring as accepted scope —
  decision recorded either way (chain_tally #305 §5 explicitly allows
  tightening the scope).
- **S4 — parity coverage (F7) + test honesty (F5, F6).** Register the
  six S2 split functions as parity fixtures (they carry byte-evidence in
  #306's table; registration makes it re-runnable); restore the
  `note.as_posix()` relative-path arm in `_resolve_theme_company_name`;
  make the canary-exclusion test assert on the printed fixture roster
  (or `--strict`) rather than `main([]) == 0`.
- **S5 — harness robustness (F8, F9, F10, F11, F12, F14).** Key the
  determinism renders by seed and compare against a fixed baseline
  (kills the KeyError *and* the seed-independent filter together);
  validate `--seeds` after parsing (blank → argparse error, non-numeric
  → `parser.error`, never a traceback or a vacuous `N/N`); report a
  trailing-newline-only diff as exactly that; render once per (side,
  seed) and reuse for both loops; add `parity` to `.PHONY`.
- **S6 — dead weight and doc truth (F15, F16, F17).** Drop the two dead
  `timer` parameters, restore the T8 rationale comment above the
  drop/keep logic it justifies, align `cached_embed_batch`'s docstring
  key list with the returned dict.

Alternative considered and rejected: **one mega-patch** — the items span
five subsystems and two severity classes; landing them separately keeps
each reviewable and keeps S2's eval-gate diff attributable.

## 4. Acceptance criteria & shakedown

1. **S1:** `pytest tests/test_graph_disk.py tests/test_query*.py` green
   including a NEW test that fails when the under-lock downgrade is
   removed (mutation check: strip the re-check → test red). Cross-process
   test `test_parallel_ro_connects_serialize_the_build` stays 6/6.
2. **S2:** `pytest -k "extract_relations or relations"` green; a new test
   pins the zero-resolved-chunk path to `target_entity is None` (mutation:
   restore the fall-through → test red). Edge-count delta measured on a
   dry-run over the current corpus, reported in this proposal before
   `--apply`.
3. **Eval-gate bullet (mandatory — S2 alters extractor rules):** run
   `helpers/misc/ontology_eval_gate.py` over the frozen question set
   (`helpers/misc/ontology_questions.json`) between S2's dry-run and its
   canonical apply — zero regressions, no undeclared changes, declared
   improvements materialize (ontology_governance S2).
4. **S3-S6:** `pytest tests/test_static_checks.py tests/test_parity_harness.py`
   green; `python3 helpers/misc/parity_harness.py --seeds ""` exits 2 with
   an argparse message; `python3 helpers/misc/parity_harness.py --seeds 42`
   runs without KeyError; `make parity` green; `make static-checks` green.
5. **Gates:** after each slice, re-run only that slice's failing legs;
   one full `make qa` at arc end, on the operator's go. `make md-lint`
   stays 0 violations (this file included).

| Projected outcome | Today | After |
|---|---|---|
| `connect(read_only=True)` on a cold/queued cache | read-write conn (silent) | read-only conn (pinned by test) |
| zero-resolved list mention | may emit a fuzzy-collapsed edge | noise gate / triage sidecar, as pre-refactor |
| ruff sweep failure surface | indistinguishable from "0 findings" | distinct reported failure |
| parity fixtures registered | 1 + 2 canaries | 1 + 6 split functions |
| `--seeds ""` / `--seeds 42` | exit 0 vacuous / KeyError | argparse error / runs |

## 5. Risks

- **S2 changes extracted edge counts** — that is the point, but it must
  be measured: dry-run diff + eval gate before apply; never apply on the
  strength of "the old code looked right".
- **S1 could stall builders** — the downgrade happens *inside* the lock;
  the existing 6-process regression test is the guard against adding a
  second open under the same flock.
- **S3's guard extension may misfire** — chain_tally #305 §5 already
  names this risk ("too broad → legitimate single-field sorts of unique
  keys"); land the extension advisory-first if the probe misfires.
- **S4 registration re-opens c901 scope** — registration is cheap and
  test-only; the D1 86-function remainder stays c901's (deferred) work.

## 6. Non-goals

- c901 D1's per-function parity harnesses (owned by the deferred
  `c901_complexity_debt` proposal).
- Snapshot/skip-worktree conventions (`doc/local/notes/snapshot.md`).
- OCR tooling changes — the `--concurrency 2` throttle rule is already
  persisted in `doc/procedures/ocr_review.md` §5b.
- Any PEP 758 "fix" — the `except A, B:` reports are false positives.
- `findata/**` (writer-owned vault) and any re-litigation of F13
  (already fixed at HEAD).

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-28 | `ocr review` delegation leg (host) | 5 findings, 0 FP | mutation-verified (2 mutations, restored) |
| 2026-09-28 | `ocr review --resume … --concurrency 2 --model glm-5.3` | 18 findings, 3 FP, 0×429, 4.02M tok, 32m53s | results: `TMPDIR/ocr_review_0b63_resume.json`; throttle evidence: `doc/procedures/ocr_review.md` §5b |
| 2026-09-28 | HEAD re-verification script (17 presence checks) | 15 PRESENT, 1 ABSENT (F13), 1 scope-documented (F3) | HEAD `530c4e36` |
| 2026-09-28 | vault sweep (`doc_query`/`convo_query`/`pending.md`/ripwire) | 1 regression (#165), 1 partial (c901 D1), 0 rejected | see §2 |

## Execution log — 2026-09-29 (S1–S6)

S1/S2 were already in the `ocr_reviews` patch (with their regression tests);
this pass executed S3–S6 and the S2 acceptance bullets. All findings in §2
now resolved or adjudicated.

- **S1/S2 verification:** `test_graph_disk` + `test_extract_relations_extraction`
  + `test_query_*` = 122 passed, including the new under-lock-downgrade and
  zero-resolved-sidecar regression tests.
- **S2 delta (measured, dry-run, `--counts-json` over the three newsletter
  dirs, 120 files, sidecar writes off):** current (fixed) vs regressed
  `0b63ce4507` — **0 delta on every edge type** (acquired 25, competes_with 2,
  customer_of 1, jv_with 50, subsidiary_of 24, supplier_to 7, and the newer
  lanes identical). The fall-through path never fires on today's corpus, so
  the fix restores the guard with **no output change**. Cross-check vs the
  pre-refactor semantics (`aed9d84e`): affected types identical too; the
  only differences are lanes other commits added since (approved_by, rated_by,
  regulated_by, same_group) — not F2's doing.
- **S2 eval gate (mandatory bullet):** extracted + applied the full corpus
  through the FIXED extractor into a **candidate copy** of `memory/research.db`
  (CLI `connect` patched to the copy — the live DB was never touched), then
  `ontology_eval_gate.py --parent … --candidate …` over the frozen set:
  **ACCEPT — 164 questions, 0 reasons.** Zero regressions, zero undeclared
  changes.
- **S3/F3 adjudication (probe-extended, per #305 §5):** the repo probe found
  **9 genuine `key=kv[1], reverse=True` value sorts** (algorithms ×4,
  igraph_bridge, l1_betweenness, scipy_bridge ×3) and **0 ascending/key-name
  single-field sorts** — no false-positive surface exists, so the guard was
  tightened: any single-field value sort over `<dict>.items()` now flags
  (either direction); tuple keys and `kv[0]` (unique-key) sorts stay accepted.
  All 9 sites converted to `key=lambda kv: (-kv[1], kv[0])`, plus 3 identical
  `list.sort` rankings in `query.py` (outside the guard's `.items()` scope,
  same tie class). `make static-checks` green.
- **S3/F4:** the ruff C901 sweep now raises on rc ≥ 2 (sweep failure ≠ "no
  findings"); the caller already surfaces the RuntimeError as an advisory.
- **S4/F5:** `note.as_posix()` relative-path arm restored in
  `_resolve_theme_company_name` (absolute-path and stem fallbacks unchanged).
- **S4/F7 adjudication:** **5 of the 6 S2 splits registered** as parity
  fixtures (`extract_relations`, `_resolve_ladder`, `extract_theme_membership`,
  `extract_citations`, `l1_betweenness_compute`) with fixed inputs in the new
  `helpers/graph/fixture_graph_db.py`; **`print_stats` is NOT registrable** —
  it opens the live production DB via `helpers.core.db.connect()`
  (stats.py) and the census/hygiene helpers, and fixture-rendering it needs a
  db path threaded through that plumbing (deferred c901 D1 owner). Recorded
  in the registry comment. Also fixed en route: the baseline loader now
  registers the exec'd module in `sys.modules` (py3.14 dataclass crash).
  `make parity`: **6/6 fixtures byte-identical to HEAD**, 3 seeds each.
- **S4/F6:** the canary-exclusion test now asserts the printed roster (every
  non-canary fixture ran, no canary did, count matches) instead of the
  vacuous `main([]) == 0`.
- **S5:** determinism keyed by seed against a fixed anchor (F14 + F8's
  KeyError); `--seeds ""`/non-numeric → `parser.error` exit 2 (F9); trailing-
  newline-only diffs reported as exactly that (F10); one render per
  (side, seed) reused across both loops (F11); `parity` in `.PHONY` (F12).
  Acceptance probes: `--seeds ""` rc=2, `--seeds 42 bfs_path` runs clean.
- **S6:** dead `timer` params dropped from both `_rebuild_*_mode` functions
  (F15); T8 delta rationale comments restored above the wholesale delete and
  the delta-only fingerprint (F16); `cached_embed_batch` docstring now lists
  the real keys incl. `unique_misses` (F17).
- **Legs run:** `test_static_checks` + `test_parity_harness` 126 passed;
  `test_convo_search` 13 passed; `make static-checks` green; `make parity`
  green. Full `make qa` run 557: 7/11, all four failed legs re-run green
  individually (types, pytest 3618 passed, snapshot-fresh, snapshot_check).
- **S7 — provider key-name mapping + no-plaintext key bridge (2026-09-29,
  operator-directed after the managed-launch key incident).** OCR expects a
  PROVIDER-SPECIFIC env-var fallback name (exporting the operator's
  `ZAI_API_KEY` failed: *"provider z-ai-coding has no api_key or api_key_cmd
  configured and no environment variable fallback found"*), and OCR's static
  `api_key` stores plaintext. Landed:
  - `helpers/misc/ocr_key_map.py` — 28-provider → env-fallback-name table
    (24 from the published docs 2026-09-29; `z-ai-coding` →
    `Z_AI_CODING_API_KEY` **verified by the live failed/succeeded launch
    pair**; 3 docs-absent locals marked UNVERIFIED with a probe recipe) +
    a `run` bridge that resolves the operator key (`OCR_KEY_NAME` /
    `OCR_KEY_FILE`), execs the command with the provider env var set — key
    crosses only via the child's environment, never argv/config/output;
    house-named var popped from the child env. Verified end-to-end
    (`ocr llm test` through the bridge).
  - `ocr_review.md` §5b rewritten: ask → map → launch; no-plaintext routes
    ranked (per-invocation bridge; `api_key_cmd` persistent-command; static
    `api_key` = never).
  - **Residual (tracked):** 3 UNVERIFIED provider env names (edenai,
    litellm, ollama-cloud) — probe before first use; `api_key_cmd` route
    only if managed passes become routine.
- **S8 — managed-launch prompt-ceiling discipline (2026-09-29).** OCR
  already chunks reviews into per-file/bundle subtasks with a `max_tokens`
  prompt ceiling (default 200,000 for `ocr review`); a small-context model
  needs the ceiling lowered (`--max-tokens` ≈ 60–70% of the window), not
  diff-chunking built. Operator note 2026-09-29: the glm runs were LUCKY,
  not safe — glm-5.3's 1M context forgave the 200k default; the same diff
  on a 32k-window provider would have crashed a subtask. Recorded in
  `ocr_review.md` §5b; boundary: a single file over the ceiling is a
  delegation-mode job. Nothing to build — knowledge slice; revisit only if
  a small-window model is ever wired.
- **S9 — the managed second-opinion round, end-to-end (2026-09-29).** The
  clean-refreshed-stack managed pass this proposal's disposition deferred to
  the operator — executed on `c83a11d9` (this arc, folded, 24 files), model
  glm-5.3, `--concurrency 2`, per §5b (operator-named `ZAI_API_KEY` mapped
  via `ocr_key_map.py run`; the first launch used a guessed key name and was
  killed — that miss is why S7 exists). Same-model caveat: glm reviewing
  GLM-authored code; independence is the harness, not the weights. Two runs:
  run 1 = 13 findings / 17 of 21 items / 1.98M tok / 27m20s (4 timeouts on
  the extractor+fixture files); resume session `c4b22eff` = complete, 20
  findings / 21 items, +0.44M tok / 20m01s. **Accounting: 19 confirmed,
  1 partially wrong, 0 false positives.** The partially wrong: the claim
  that onager's `(-len(kv[1]), min(kv[1]))` tuple "ties exactly like the
  bare form" — component min-ids are unique across disjoint groups, so the
  code IS deterministic; the meta-point (the checker cannot prove tuple-tail
  uniqueness) accepted and documented instead.
  - **Headline catch — the two HIGH-severity findings: both HIGH-fix regression tests
    were mutation-vacuous.** The S1 test warmed the cache first, so
    `connect()` took the outer fast path and never reached the under-lock
    route; the F2 test resolved nothing anywhere, so pre-fix fall-through
    produced the identical sidecar row. Both claims proven by stripping the
    fix (tests still passed), both tests rewritten with real discriminators
    (S1: cold-then-warm `_is_warm` interleave; F2: alias on the WHOLE
    mention to a non-chunk entity) and mutation-proven red-on-mutated /
    green-restored. §4.1/§4.2's original mutation criteria are NOW actually
    satisfied. Doctrine added to `ocr_review.md` §5: mutation-check the
    TEST, not just the fix.
  - The other 17 findings, all confirmed and fixed: `review_selection.py`
    (SystemExit escape + spurious non-stgit fire; the `[ocr]`-line strip
    skipped against the procedure's own rule; `Mojo/` family collapse
    swallowing the vendor exclusion), `review_freshness.py` (`--stack 0`
    scope aliasing; unchecked stg returncode), `static_checks.py`
    (direction-blind remediation hint; docstring scope overstated),
    `parity_harness.py` (`isdigit`→`isdecimal`; `splitlines()` Unicode
    misdiagnosis), `harvest_conversations.py` (second corruption mode:
    valid sqlite / corrupt JSON row), `fixture_graph_db.py` ×4 (invented
    trigger phrasings replaced with verified-firing ones; real chatter
    sentinel; dead INDEX_NOISE), `extract_relations.py` (triple
    `_LIST_CHUNK_SPLIT_RE.split` → single `_list_chunks()` split-point),
    `test_convo_search.py` (silent no-op lane extraction), `Makefile`
    (`STACK=N` passthrough). Verification: 261 targeted tests, parity 6/6
    byte-identical, ruff/format + md-lint clean.
  - **Mode-decision datum (closes the S4-era open item):** on this arc the
    managed leg earned its slot — 19 real findings incl. two vacuous
    regression tests that the author, the 3618-test gate, and the mutation
    claims all missed. Cost 2.42M tok / ~48 min wall across two runs.
    Verdict: per-arc second opinion on high-stakes patches, never a gate
    leg. Freshness ledger records the round (fp 81e0363b, legs
    delegation+managed). Full triage: `code_review.md` §Managed leg.
- **S11 — delegation A/B round on the same commit (2026-09-29, top-priority
  tooling-robustness execution).** Same-commit comparison run per the
  operator (delegation+host vs the managed round): 2.5 min / ~12.6k fresh
  tokens vs 47.4 min / ~726k fresh — ~16 of the managed 20 re-derived from
  the checklist; the two mutation-vacuous-test catches NOT reached (they
  need the §5 mutation-the-test discipline, which the checklist doesn't
  carry); and **2 genuine LOWs the managed leg missed**, both fixed:
  1. `review_freshness._load()` crashed with a raw JSONDecodeError on a
     corrupt ledger — now a clean named error (`fix or delete <path>`,
     verified rc=1 against a corrupt file).
  2. The `--record` path was lock-free — two concurrent recorders could
     last-writer-clobber rows — now flock-guarded (house pattern),
     round-trip verified.
  Executed in the same pass, S10.1 (the top-priority engineering residual):
  `_load_baseline(..., restore_after=True)` for in-process callers with
  half-initialized-module rollback on exec failure; the test asserts the
  no-poisoning contract directly. Cache-fairness caveat recorded in
  `code_review.md`: the delegation leg's 99.7% cache hit flatters the token
  ratio (an un-primed host pays uncached input for diff + checklist; both
  legs enjoy caching — managed at 65%) — cache does no reasoning, so the
  QUALITY ordering is untouched; the structural driver is 78 LLM subtask
  requests vs zero.
- **S12 — leg-3 cross-model delegation round (2026-09-29, operator-run).**
  First weight-independent pass: opencode host + **Muse Spark 1.3** reviewed
  the stack top `8906dbeb` (= c83a11d9 + the S9 fix layer; ref relationship
  verified by the reviewer). Report `/tmp/muse_review.md` (ephemeral;
  substance in `code_review.md` §Leg 3). **2 unique LOW defects, both
  confirmed and fixed:**
  1. `ocr_key_map.py` popped the env entry by the secret VALUE, not the
     house NAME — a no-op that leaked the house-named variable (secret
     included) into the child env whenever the key was environment-resolved.
     Fixed: `_resolve_operator_key` returns the name; pop by name. Verified
     end-to-end through the exact leaking path; the assertion fails on the
     old code.
  2. The harvest skip net (`ValueError`) relabeled unrelated bugs as
     "unreadable source" — silent data loss wearing a corruption label.
     Fixed: narrowed to `(sqlite3.DatabaseError, OSError,
     json.JSONDecodeError, AttributeError)` — documented corruption modes
     stay caught, unexpected ValueErrors propagate loudly. 13 convo tests
     green.
  Round verification: 21/21 handoff items (legs 1–2) re-verified
  fixed-or-correctly-scoped, 0 re-surfaced; the reviewer independently
  re-executed the F2 mutation probe (RED as designed — cross-model
  confirmation the rewrite is non-vacuous) and cross-checked all 4 fixture
  triggers against PATTERNS. Operator verdict on the three-leg experiment:
  full verification from all angles; delegation loop transfers across
  harnesses and model families with the procedure as the only carrier.
- **S10 — residuals (remaining):**
  1. ~~S7's 3 UNVERIFIED provider env names~~ **EXECUTED 2026-09-29 (A1):**
     probed with dummy-value env at 7 candidate names (docs-style guesses +
     alternates) against the discriminating control (z-ai-coding + wrong
     name reaches the fallback-stage error; the three never do). Verdict:
     **edenai / litellm / ollama-cloud have NO env-var fallback** — keys go
     via `api_key_cmd` (never static `api_key`). `ocr_key_map.py` table
     updated (rendered as "no env fallback; api_key_cmd only").
  2. `api_key_cmd` persistent route: with the managed round proven out,
     the "only if routine" precondition is arguably met — operator call.
  3. The operator may still reject any S9 disposition; each fix is an
     independent, revertible edit.
