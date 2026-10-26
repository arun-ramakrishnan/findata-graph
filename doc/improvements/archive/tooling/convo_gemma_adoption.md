---
title: "Convo gemma adoption — S0 eval gate, then migrate the convo hybrid only on a measured win"
status: deferred
filed: "2026-10-26"
executed: "2026-10-26"
completed_md: "442"
area: "helpers/maintenance/rebuild_convo_search + helpers/misc/convo_query + convo eval bank"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# Convo gemma adoption

**VERDICT 2026-10-26: DEFERRED — evaluated, embedder upgrade parked behind
fusion repair.** The S0 bank and verdict stand (below). Reading: bm25 is the
spine for precedent retrieval (22/57 alone, beating BOTH hybrids), and the
hybrid legs being bm25-bound (both hy < their own pv) means the fusion
parameters — not the embedder — are the current bottleneck. Spending the
27–51 h re-embed wall before repairing fusion would compound a mistuned
ranker. Reopen triggers: (1) fusion tuned so hy ≥ bm25-alone on this bank
(cheap: reuses stored granite vectors); (2) a semantic-paraphrase recall
complaint the FTS leg demonstrably misses; (3) faster gemma serving
(batch endpoint / quantization) bringing the wall under ~1 day.

## WHY

`convo_search` is the last granite holdout: script_search (code legs — gemma
beat granite in every like-for-like pairing, MRR 0.959 vs 0.827) and
note_search (#369, `notes_gemma_adoption`, 17,263 rows @ 512-d) are both
gemma-live. Convo transcripts are code-heavy prose (tool calls, paths, error
strings), which sits closer to the code legs than to the docs legs — that is
the gemma-edge hypothesis. The counterweight: on docs prose the hybrid arms
were near-parity (gemma hybrid 0.885 vs granite hybrid 0.874 MRR), and the
throughput wall is severe — gemma 0.62–1.44 texts/s vs granite 3.70 on this
box, ~139k rows to re-embed → **27–51 h continuous cold**, with
`embed_cache` keyed `(sha256(text), model)` so a swap cold-misses 100%.

A model swap is query-visible semantics → eval-gate fires. No convo eval
bank existed. S0 builds one and decides; S1–S4 run only on a measured win.

## TRIAL DESIGN (S0)

Cloned from the notes trial (`bench_data/embgemma2/`):

- **Bank** — `convo_build_bank.py` mines REAL user turns from the live
  corpus (role='user', 30–240 ch, query-shape gate `_is_querylike`,
  near-dup dedup, 1–2 q/session cap), 3 tiers: `self` (verbatim-recall
  sanity), `decision` (why/how/should semantic-gap probes — the
  discriminating tier), `code_ident` (identifier/path/error-anchored — the
  gemma-edge hypothesis tier). No hinglish tier: zero Hinglish user turns
  exist in the corpus at any length. Frozen:
  `banks/convo_bank_57q_2026-10-26.json` (57 q: 18/18/21; harness mix
  opencode 41 / dsh 10 / prime-rlm 6). House doctrine held: expects
  verified against the RAW parquet store (`anchor_row_no`), never the
  engine under test.
- **Pool** — `convo_embed_arm.py`: expect-session rows + live FTS top-50
  recall per q + deterministic md5-ordered background fill, 5,000 rows,
  deduped `(harness, part_id)`. Reruns anchor to the same-day granite npz
  keys so both arms score identical rows.
- **Arms** — granite doc-vectors read from the STORED production embeddings
  (byte-parity, zero embed cost); gemma doc side = `embed_document(raw
  snippet)` (production-faithful convo basis — rebuild embeds raw snippets,
  no title prefix); query side = `embed_query(task="search")` per the
  two-sided prefix contract (query-prefix omission degrades SILENTLY,
  notes hard-set 1.000 → 0.880).
- **Legs** — `convo_score.py`: `bm25_base` (live production FTS sidecar,
  full corpus), `pv_<model>` (cosine over pool), `hy_<model>` (RRF_K=60,
  HEAD=160 — production constants from `convo_query.py`). Fairness caveat
  carried from notes: vector legs see the 5k pool, bm25 sees 166k — hybrid
  ABSOLUTE numbers cautious; the like-for-like signal is
  granite-vs-gemma within each leg class.

## SLICES

- **S0 — eval gate (this filing; execution artifacts already built).**
  Bank + arms + scorer, then the verdict table. GO if `hy_gemma` beats
  `hy_granite` by ≥ +2 pass@5 overall AND ≥ +0.02 MRR on `decision`
  (with `pv_gemma` ≥ `pv_granite` as corroboration); NO-GO otherwise →
  archive as evaluated-not-warranted with the scores table.
- **S1 — selector**: `_resolve_convo_embedder` mirroring
  `rebuild_script_search.py:390–430` (`CONVO_EMBEDDER=auto|gemma|granite`;
  auto = gemma when available and store not yet stamped).
- **S2 — stamp guards on BOTH lanes**: `guard_gemma_stamp` on the swap
  rebuild AND the harvest incremental lane — a sidecar-down harvest must
  refuse, never poison a gemma-stamped store with granite vectors (the
  2026-10-08 notes incident, twice over).
- **S3 — query side**: `convo_query.py` picks its embedder from the INDEX
  stamp (dims already read at `:108`; `embed_model` read at `:118` but
  unused for choice) + prefix contract. Stamps `embed_model`/`embed_dims`
  are already written per rebuild (`:726/:779`); cast width must follow
  the stamp (512 vs 384 — FLOAT[] needs no schema change).
- **S4 — migration run** (only on GO): quiet box, sidecar up, full swap
  rebuild ~27–51 h cold, `embed-gc` after to reclaim stranded granite
  vectors, parity spot-checks + convo-fresh.
- **S5 — permanence**: `convo_prod_gate.py` floor-gate in the
  `notes_prod_gate.py` shape (bank + recorded trial numbers − margin),
  bank snapshot discipline (`banks/` immutable + living twin), docs +
  `gemma_embedder.py` docstring correction (it still claims notes keeps
  granite — stale since #369).

## RISKS

- Sidecar single-process, no batch/pool (quiet-box doctrine) — the 27–51 h
  wall; batch-endpoint re-check belongs in S0's verdict if the win is
  marginal.
- Pool-subset fairness (above) — mitigated by like-for-like reading and a
  bm25 floor leg.
- Query-prefix omission degrades silently — S3 must stamp-drive the
  embedder AND the task.
- Harvest incremental lane is the poisoning vector — S2 is not optional.

## STATUS

**DEFERRED 2026-10-26** (operator ruling: protect the bm25 spine; the
embedder bet waits behind fusion repair — see VERDICT block at top).
S0 artifacts remain durable in `bench_data/convo_gemma/` (bank 57q frozen,
both npz arms, scorer, frozen scores `convo_scores_20261026.json`) — a
reopen re-runs `convo_score.py` against a fresh pool, no bank rebuild.

## S0 VERDICT (2026-10-26, hardened bank, 57 q)

Design fix found during scoring: the query text is indexed INSIDE its own
expected session, so unhardened legs handed every question a free rank-1
(bm25 saturated 57/57 MRR 1.0 — invalid instrument). Hardening = the
question's own `(harness, part_id)` excluded from every leg (bank carries
`part_id`, additive; question set unchanged). Pre-hardening scores void.

| leg | overall | code_ident | decision | self |
|---|---|---|---|---|
| pv_gemma | **24/57** | 9/21 · .303 | 9/18 · .226 | 6/18 · .219 |
| pv_granite | 14/57 | 5/21 · .147 | 5/18 · .176 | 4/18 · .116 |
| hy_gemma | 10/57 | 4/21 | 3/18 | 3/18 |
| hy_granite | 8/57 | 2/21 | 3/18 | 3/18 |
| bm25_base | 22/57 | 7/21 · .302 | 8/18 · .417 | 7/18 · .361 |

Floor outcome: hy overall +2 ✓ (10 v 8); hy decision MRR −0.017 ✗
(floor wanted +0.02); pv corroboration ✓ decisive (+10/57 = +71%
like-for-like on identical pool rows, wins every tier, largest on
code_ident 9 v 5). Hybrid legs are bm25-bound on this bank (both hy <
their own pv) — the embedder signal lives in the pv legs; the hybrid
delta is positive but noise-adjacent.
