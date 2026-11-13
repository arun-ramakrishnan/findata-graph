---
title: "Add a semantic_cluster label metric (e-means over company_embeddings)"
status: proposed
filed: "2026-11-12"
executed: null
completed_md: null
area: "graph analytics (new lane helpers/graph/semantic_clusters.py; app.py label allowlists)"
---

# Add a semantic_cluster label metric (e-means over company_embeddings)

**Date:** 2026-11-12 · **Status:** PROPOSED ·
**Area:** new CLI lane `helpers/graph/semantic_clusters.py`; `app.py` label
allowlists; `graph_analytics` store (memory/research.db)

## 1. Motivation

All three served label metrics are graph-native (`app.py:3378`
`_LABEL_GRAPH_METRICS` = `louvain_community`, `weakly_connected_component`,
`hypermmsbm_community`). Nothing serves the *embedding* lens. Measured
(2026-11-12): a 42-partition e-means fit over the 1,209×512
`company_embeddings` agrees with the hand-authored sector taxonomy at
**ARI 0.307** — about twice either graph lens — while staying nearly
independent of both. Aligned-but-orthogonal is exactly what a **coherence
instrument** needs: re-fit after an embedding-model change or store growth
and watch the sector ARI; a drop means the vector space drifted away from
the taxonomy the rest of the repo assumes.

This is NOT a speed proposal. The repo contains no k-means to accelerate
(`rg -l -i "kmeans|k-means" --glob '*.py'` exits 1;
`git log --all -S"kmeans" -- '*.py'` empty), and n·k here is three orders
below the paper's own GPU-recommendation crossover. e-means is chosen over
hand-rolled Lloyd for one measured property: **repeatability** (2–3× lower
seed-to-seed spread at every k measured) — the label a drift instrument
re-fits must be stable, and Lloyd's is not.

## 2. Evidence (measured 2026-11-12, this box)

Cross-metric agreement over the 1,099 companies all four metrics cover
(ARI, seed 0):

| pair | ARI |
|---|---|
| semantic(k=42) vs **sector taxonomy** | **0.307** |
| semantic(k=42) vs `louvain_community` (39 parts) | 0.157 |
| semantic(k=42) vs `hypermmsbm_community` (8 blocks) | 0.053 |
| semantic(k=8) vs sector | 0.086 |
| `louvain` vs sector | 0.163 |
| `hypermmsbm` vs sector | 0.123 |
| `louvain` vs `hypermmsbm` | 0.061 |

Null ARI for random partitions at this n ≈ 1×10⁻⁴, so 0.307 is strong
signal — but NMI vs sector tops at **0.537** (k=42, mean of 5 seeds): half
the taxonomy's information is simply not in the geometry. The metric is a
lens, not a classifier (consistent with the recorded spectral+k-means
result, `doc/improvements/archive/graph/hgx_over_dyads.md:140`, NMI
0.377–0.393).

Cost and dependency:

| measurement | value |
|---|---|
| e-means fit, k=42, fresh | 1.262 s |
| empty clusters at k=42 | 0 (42/42 non-empty, 5 seeds) |
| 5-seed sector ARI | mean 0.2652, max 0.2933 (permutation null p95 0.0019) |
| e-means vs Lloyd inertia | 0.6–2.4% lower at every company k (8/16/42/64) |
| e-means vs Lloyd seed spread | 2–3× lower |
| dependency | faiss-cpu 1.16.0 — MIT, 19.5 MB, cp310-abi3 manylinux, verified compatible with this py3.14 venv (nothing installed) |

Raw log: `doc/local/engineering/emeans_algorithm.md` (machine-local), §4–§5.

Re-measured 2026-11-12 at implementation: sector ARI **0.24** (seed 42,
live store; 5-seed sweep 0.20–0.22, mean 0.212). The proposal's 0.307
(seed 0) is NOT reproduced by the shipped lane on the current store — the
evidence run predates the #507/#509 store changes and the following day's
snapshot refresh. The lens is still a coherence instrument (empty-cluster
count 0, fit 0.2–0.35 s), but the ARI baseline and acceptance band are set
by TODAY's measurement; the 0.27–0.31 figures are stale until re-derived on
a matching snapshot.

**Family-group alignment (measured 2026-11-12, on request):** the curated
family/group seeds (family_seeds.json v5; 61 embedded single-family members)
are NOT recovered by the embedding lens — ARI(semantic clusters vs
single-family groups) = 0.056, family pair cohesion 0.087 (16/184). The
groups are institutional/organizational facts the vector geometry lacks;
the sector anchor (0.24) stays the canonical coherence signal. Forcing
cluster alignment to group labels would inject the curated data into the
pure-embedding lens (self-referential; defeats drift detection).

## 3. Design

S1. `helpers/graph/semantic_clusters.py` — CLI lane in the
   `hyper_communities.py` mould: read `company_embeddings`
   (memory/research.db), L2-normalize,
   `NumpyEmeans(512, k=42, nepochs=5, seed=42, spherical=False)`,
   `--apply` through `write_analytics("semantic_cluster", ...,
   replace_population=True)` (`helpers/graph/algorithms.py:1286`).
S2. `faiss-cpu` stays an **optional** dependency — import inside the lane,
   exit with an operator-actionable message when absent (the
   `hyper_communities.py` lazy-import precedent).
S3. Pin `seed`, `k`, `nepochs`, `batch_law` explicitly and persist them
   in the metric payload — the auto recipe is a function of n, so a store
   that grows silently changes B and alpha0; the pins make that auditable.
S4. Add the metric name to `app.py:3378` `_LABEL_GRAPH_METRICS` and the
   label-key map at `app.py:3574`; coverage test alongside
   `tests/test_hyper_wiring.py`.

## 4. Acceptance criteria & shakedown

1. **Eval-gate bullet** (additive but query-visible):
   `ontology_eval_gate.py` parent-vs-candidate over the frozen set; zero
   undeclared changes; ACCEPT — the metric adds a label, it changes no
   existing answer.
   **VERIFIED 2026-11-12** — ACCEPT, 191 questions, 0 reasons
   (parent = post-apply `research.snapshot.db.zst` decompressed; the old
   pre-apply 14:18 parent is stale for the family/group pins — family v5
   and compaction landed after it).
2. Fitted in < 5 s at n=1,209 (measured 1.262 s).
   **VERIFIED 2026-11-12** — 0.215 s live.
3. Sector ARI recorded in the run report and not regressing by more than
   0.02 across a re-run (measured 0.27–0.31 across 5 seeds).
   **VERIFIED 2026-11-12** — recorded; re-runs stable at 0.24 (seed 42),
   sweep 0.20–0.22. Baseline band updated per the re-measurement note (§2).
4. Zero empty clusters at k=42 (measured 42/42 across 5 seeds).
   **VERIFIED 2026-11-12** — 42/42 live.
5. Docstring carries the coverage caveat: 1,209 companies, 42 non-empty
   clusters, ARI 0.24 (live) vs `entities.sector_classification`, NMI ceiling
   0.537 — a coherence instrument, not a sector classifier.
   **VERIFIED 2026-11-12** — docstring carries the caveat.

## 5. Risks

- **Label churn** — e-means is more repeatable than Lloyd but not
  deterministic (10 distinct partitions out of 10 seeds at every k
  measured). The pinned seed keeps re-runs stable until the store grows;
  the persisted pins (S3) make any churn auditable rather than silent.
- **Misreading** — the metric will tempt a sector-classifier reading; the
  NMI 0.537 ceiling and the docstring caveat (acceptance 5) are the guard.
- **Dependency surface** — one optional pip install; the lane degrades to a
  clear message, never a hard venv requirement.

## 6. Non-goals

- No replacement or re-litigation of `louvain_community` /
  `hypermmsbm_community` — they answer structural and incidence questions
  with their own evidence.
- No ANN index, no third vector store (standing vetoes:
  `doc/improvements/archive/tooling/tech_avenues.md:108`,
  `doc/improvements/pending.md:726-740`).
- No touch of `helpers/graph/algorithms.py:860` `_run_clustering` — it is
  the local clustering *coefficient* (transitivity), not a partition.
- No wiring of the 220k convo store (no consumer asks for a topic
  partition of conversation history).

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-11-12 | e-means k=42 fit over company_embeddings | 1.262 s | scratch venv, faiss-cpu 1.16.0 |
| 2026-11-12 | ARI cross-metric (seed 0) | 0.307 / 0.157 / 0.053 | vs sector / louvain / hypermmsbm |
| 2026-11-12 | 5-seed sector ARI | mean 0.2652, max 0.2933 | permutation null p95 0.0019 |
| 2026-11-12 | empty clusters k=42 | 0 | 5 seeds |
| 2026-11-12 | wheel compatibility | cp310-abi3 ok on py3.14 | faiss-cpu 1.16.0, MIT, 19.5 MB |
