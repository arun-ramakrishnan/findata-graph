---
title: "Capture reasoning and tool blocks in the convo corpus (prime lane, opencode model facet, encrypted-reasoning marker)"
status: executed
executed: "2026-10-18"
completed_md: "412"
filed: "2026-10-18"
area: "convo_search harvest"
---

# Capture reasoning and tool blocks in the convo corpus (prime lane, opencode model facet, encrypted-reasoning marker)

**Date:** 2026-10-18 · **Status:** EXECUTED (S1–S4 by the filing session;
S7 + the two force-lane/backfill defects below by the close-out session the
same day; S5(a) done, S6(a) proven permanent, S6(b) open) · **Area:**
`helpers/maintenance/harvest_conversations.py`,
`memory/data/harness/prime-rlm/`, `tests/test_harvest_reasoning.py`

## 0. Why this was invisible

Three defects, none of which raised an error, none of which a query could
have surfaced. Each produced a *plausible* number: a lane that archived
"20,313 rows" (all of them text), a model facet that reads `''`, and 9,657
reasoning rows that read as "the model thought nothing". The corpus was
internally consistent throughout — the search index never disagreed with the
corpus, because both were built from the same truncated rows.

| # | trap | what it silently produced |
|---|---|---|
| 1 | `_prime_text` reads only a block's `text` | prime's `thinking` blocks (reasoning lives under `thinking`) and every `toolCall` block were dropped: **0 reasoning, 0 tool rows** for the lane while the corpus showed 20,313 healthy rows |
| 2 | `part_type` hardcoded `"text"` in the prime lane | even a recovered `thinking` block would have been indexed as text |
| 3 | opencode assistant rows carry `modelID`, not `model` | 129,430 of 167,274 opencode corpus rows (77.4 %) have `model = ''`; the facet splits per row instead of per model |
| 4 | harvest reads `d.get("model")` for that message row | model is a *dict* in the legacy shape (`{"id": …, "providerID": …}`), so the value must go through `_oc_model_value` |
| 5 | opencode reasoning arrives **encrypted** (`metadata.openai.reasoningEncryptedContent`) with `text == ""` | 9,657 corpus rows that read as a model with no thoughts; the harvest discarded `metadata`, leaving `meta='{}'` |
| 6 | watermark = (mtime, size) per source file | a *parser* fix recovers nothing from an already-harvested source: every source is "quiet" and the lane re-emits its old rows |
| 7 | "prime's live dir has files" ⇒ content is there | the live dir holds 19 files that yield ~0 blocks; the content is in the timeshift snapshots (88 files) |

## 1. Question that opened this (m01884)

`master_query` / `convo_query` were asked to find thinking-token content.
Answer after audit: opencode, zcode and dsh **do** capture it; `prime-rlm`
captured none of it, and the opencode empty-reasoning rows had no explanation
in the corpus.

## 2. Measurements (2026-10-18)

Reasoning rows in the corpus before the fix, by harness:

| harness | reasoning rows | chars |
|---|---|---|
| opencode | 23,710 | 37,749,000 |
| zcode | 3,985 | 9,650,000 |
| dsh | 810 | 2,100,000 |
| **prime-rlm** | **0** | **0** |

Prime sources (`_prime_files()` = 107 files: 88 snapshot + 19 live), blocks
by kind: `text` 13,757 · `toolCall` 9,297 · `thinking` 4,491. Blocks that
yield a row: text 13,615 · reasoning 4,128 · tool 9,297. Skipped as
empty-text: 142 text, 363 reasoning (an empty `thinking` block is not a
thought).

Prime corpus before → after the forced re-harvest:

| part_type | rows | chars |
|---|---|---|
| text | 20,313 (unchanged) | 19,711,814 (unchanged) |
| reasoning | 0 → **3,541** | 0 → **9,421,800** |
| tool | 0 → **7,919** | 0 → **7,242,683** |

The lane's searchable content grew by 16,664,483 chars (+60 %) and 11,460
rows. `text` does not move because the forced re-read emits the same
`part_id`s and merges (see §3.4).

Opencode model facet: 129,430 / 167,274 rows (77.4 %) carry `model = ''`.
The v2 assistant message row carries `modelID`; the legacy row carries
`model` as a dict.

Opencode empty reasoning: 33,392 reasoning rows, 9,657 empty. A live-db probe
of 2,647 reasoning parts found 76 empty — **71 of them carrying
`metadata.openai.reasoningEncryptedContent` plus an `openai.itemId`**, 4 with
no metadata at all, 1 with `metadata` present but no encryption key. The
class is model-correlated: by session, `muse-spark-1.3-contributor-free` 72 /
`longcat-2.5-preview-free` 2 / `fledge-alpha-free` 2. Corpus-wide, the model
with the highest empty rate is `muse-spark-1.3-contributor-free` at 65.8 %
(1,839 / 2,793); rows with no model recorded are 35.6 % empty (7,818 /
21,973).

## 3. Slices

### 3.1 S1 — prime block kinds (done)

`_PRIME_THINKING = ("thinking", "redacted_thinking")`; `_prime_block(blk) ->
(text, part_type, meta)` is the single mapping for the lane: `text → text`,
`thinking → reasoning` (meta keeps `signature`, and `redacted` when set),
`toolCall → tool` (meta keeps `tool` name and `call_id`, text is the
serialised `arguments`). Empty text still skips the row, so an empty
reasoning block never becomes a phantom.

### 3.2 S2 — opencode model facet (done)

`_oc_msg_model(d)` resolves `modelID`, then `model` as a dict (`_oc_model_value`
handles `modelId`/`modelID`/`id`), then a JSON-string model, then `""`. Used
by `_harvest_opencode_db` (the message-id → (role, agent, model) map) and by
the `_v2_msg_envelope` message-table fallback. The column is always a string:
a dict in the parquet would split the facet per row.

### 3.3 S3 — encrypted-reasoning marker (done)

`_oc_reasoning_meta(d)` returns
`{"encrypted": true, "enc_bytes": N, "item_id": …}` when
`metadata.openai.reasoningEncryptedContent` is present, else `{}`. The
ciphertext is **never** copied into the corpus — only the fact that the row
is empty *because it is encrypted*, plus its size and item id. This turns
"why did the model say nothing?" into a queryable answer.

### 3.4 S4 — `--force-lane` (done, required for S1–S3 to matter)

The per-source watermark is (mtime, size): an already-harvested source is
`quiet` and a parser fix recovers nothing. New flag
`--force-lane {opencode,prime-rlm,zcode,dsh,bili}` bypasses **one** lane's
watermark and runs the normal harvest otherwise. Safe because prime
`part_id`s are `f"{message_id}:{block_index}"` — stable across the parser
change — so a forced re-read merges instead of duplicating (pinned by
`TestForceLane::test_forced_re_read_merges_instead_of_duplicating`).

Applied: backup `memory/data/harness/prime-rlm/` + `memory/convo_search.duckdb`
→ `harvest_conversations.py --force-lane prime-rlm` (exit 0, 181.7 s, lane
itself 2.05 s) → `rebuild_convo_search.py --incremental`.

### 3.5 S5 — the irrecoverable class (open)

The 9,657 pre-fix empty opencode reasoning rows cannot be back-filled with
text: the plaintext never left the provider (encrypted reasoning is a design
of the OpenAI-Responses shape, and this harness is `protocol: openai`).
S5 should (a) mark the rows whose source db still exists by running the
marker path over them, and (b) report the rest as an expected, permanent
class so the next audit does not re-file it as a defect.

### 3.6 S6 — model facet for pre-fix rows (measured 2026-10-18, close-out)

129,430 rows keep `model = ''`. A re-harvest of the live/snapshot dbs would
fix the sessions whose source still exists; the rest are permanent. Decide
whether the facet (and any query that groups by model) should say
`unrecorded` rather than blank, so an absent model is never mistaken for a
missing model column.

**Measured after the corrected full force (see §3.7):** blank-model
assistant rows stand at 55,211 (tool 30,051 / reasoning 14,136 / text
10,860) and are PERMANENT — their parquets were re-emitted by the full
re-read (mtime witnesses the rewrite) and the model still resolved blank,
so the source `message` rows genuinely carry no model field for those
vintages. S6(a) is thereby complete: every row whose source could answer
now does. S6(b) — the `unrecorded` relabel — stays open as a query-side
convention choice.

### 3.7 S7 — zcode tool slice + two force/backfill defects (done, close-out)

The census that opened S5/S6 found zcode's raw rollouts carry
`response.toolCalls` in 87 of 102 sampled live records while the corpus
held 0 zcode tool rows — the same defect shape as trap #1, one lane over.

- **S7 parser**: `_zcode_best_and_resps` collects `toolCalls`; each call
  becomes a searchable `tool` row `zc:<sid>:tool:<rid>:<i>` — text is the
  serialised `input` (the command is the searchable content), meta keeps
  `{tool, call_id}` (prime's `_prime_block` convention). Result: **0 →
  2,664 tool rows / 2,434,858 chars**.
- **Defect (force-lane opencode was a no-op)**: the flag bypassed the
  quiet-skip but NOT the per-source `hwm` filter
  (`harvest_conversations.py` `_harvest_opencode` passed the watermark
  into `_harvest_opencode_db`, whose `WHERE time_updated > ?` re-emitted
  nothing already harvested — a forced pass rewrote exactly 1 file, live
  traffic only) and the v2/evt sub-lanes had no force bypass at all.
  Fixed: force now means full re-read (`hwm=None`; part_ids stable, so
  it merges). Corrected run: 8 snapshots + live, **184,919 parts
  re-emitted, 135 session files rewritten**, encrypted-reasoning markers
  0 → **1,801** (S5(a)).
- **Defect (in-place migrations never reached the index)**: the model
  migrations rewrite corpus parquets with text/part_ids untouched, so no
  incremental rebuild notices a changed facet — corpus 0 dict-string
  model rows, index 5,327. Fix: `_backfill_models` in
  `rebuild_convo_search.py` (sibling of `_backfill_provenance`, same
  positional `(file_path, row_no)` join, no re-embed), exposed as
  `--refresh-index-models` and auto-run by both migrate flags. Result:
  JSON-string model rows 8,208 → **0**.

## 4. Verification

`tests/test_harvest_reasoning.py` — 17 tests: prime block mapping (thinking,
redacted, toolCall, dict arguments, text), the model facet across both
vintages, the encrypted marker (asserts the ciphertext is absent), the
end-to-end part-row wiring, and `--force-lane` (quiet skip, forced re-read,
no duplication, other lanes unaffected). Plus the neighbouring suites:
`tests/test_harvest_bili.py`, `tests/test_harvest_retention.py`,
`tests/test_harvest_zcode_rollout.py`, `tests/test_convo_provenance.py`,
`tests/test_static_checks.py` — 184 passed, 1 skipped. `ruff check`,
`ruff format --check` and `ty check` clean.

**Close-out (S7 + defect fixes)**: +4 zcode tool tests in
`tests/test_harvest_zcode_rollout.py` (mapping, part-id stability across
re-reads, empty-input call kept as `{}`, no-toolCalls yields none) — 27/27
across both suites, 191 passed / 1 skipped over the six-suite sweep, ruff +
static-checks clean. Live: index 136,076 → **139,039 rows**; lane census
reasoning opencode 23,942 / zcode 3,985 / dsh 810 / prime-rlm 3,541;
zcode tool 2,664; encrypted markers 1,801; dict-string model rows 0.

## 5. Follow-ups

- `next_steps`: ~~re-run `rebuild_convo_search.py --check` after the
  incremental rebuild and record the index-side reasoning count for
  prime-rlm (the corpus number is 3,541)~~ — done: index carries
  prime-rlm reasoning 3,541 / 9,421,800 chars exactly.
- The stats lines in `_harvest_prime` label every file with its snapshot
  tag only (`prime-snap:<ts>: N parts`), so a per-file tally is unreadable
  when 20 files share a tag — cosmetic, but it hid the shape of §2.
- S6(b): the `unrecorded` relabel for the 55,211 proven-permanent
  blank-model rows — query-side convention, operator's call.
