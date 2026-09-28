---
title: "CSR shortest_path lane remediation — activation, caching, contract, docstring"
status: proposed
filed: "2026-09-28"
area: "helpers/graph/csr.py, helpers/graph/query.py, app.py, tests/test_csr.py"
---

# CSR shortest_path lane remediation — activation, caching, contract, docstring

**Date:** 2026-09-28 · **Status:** PROPOSED ·
**Area:** `helpers/graph/csr.py`, `helpers/graph/query.py`, `app.py`,
`tests/test_csr.py`.

## 1. Motivation

The OCR delegation review of `ef8a17d4` (CSR+Mojo T1) found the CSR
shortest_path lane — the arc's headline promotion — never fires in production,
and its per-call setup cost erases most of the measured win. Four findings,
all re-verified live in this worktree. This proposal remediates them so the
lane actually delivers its documented value.

## 2. Evidence (from /tmp/ocr_delegation.md, re-verified 2026-09-28)

| # | Sev | Finding | Location |
|---|---|---|---|
| 1 | High | Lane never fires: gate needs `edge_label is None`; API/CLI don't pass it, defaulting to `"BelongsTo"` | `query.py:2268,2303`; `app.py:2171`; `query.py:4056` |
| 2 | High | Per-call `load()` (~5.2 ms) + `pos` rebuild (~2.0 ms) → 1.27× end-to-end, not 50–300× | `csr.py:111,241,252` |
| 3 | Med | `src == dst` divergence: SQL `[(src,0)]` for known-but-edgeless; CSR `(None,True)` | `csr.py:253-256` |
| 4 | Low | near-dup docstring overclaim (GEMM materializes P×P) | `query.py:3610` |

## 3. Design

- **S1 — activation.** Pass `edge_label=None` explicitly from the API route
  (`app.py:2171`) and add `--edge-label` (default `None`) to the CLI `shortest`
  subparser (`query.py:3919`), passing it through at `query.py:4056`. This
  matches the API docstring ("across all edge types (undirected)") and lets the
  gate fire. **Behavior change:** unfiltered queries go from BelongsTo-only to
  all-edge-types. **Operator-approved 2026-09-28** — the API has always
  promised all-edge-types, so this restores the documented contract rather
  than changing it.
- **S2 — caching.** Module-side cache in `csr.py` keyed on
  `(out_dir, generation)` holding `(offsets, neighbors, names, pos)`. On hit,
  skip `load()` and the `pos` rebuild; on miss (or generation drift), reload
  and repopulate. Stale entries never serve — the existing `fresh` flag still
  forces SQL fallback. Memory ~few MB (offsets ~88 KB, neighbors ~460 KB,
  names/pos ~few MB each).
- **S3 — `src == dst` contract.** In `try_shortest_path`, short-circuit
  `src == dst` when the name resolves in `v_node` → return `[(src, 0)]`,
  matching the SQL path. Query `v_node` once for the name.
- **S4 — near-dup memory claim.** Two sub-parts: (a) reword the docstring to
  scope the O(block·paths) bound to the pair accumulator and note the GEMM
  materializes P×P (bounded by the 10,000-path ceiling); (b) guard the
  `out.sort()` before truncation — at low `min_sim` it can hold up to ~50M
  pairs (latent; the API floors `min_sim` at 0.9). Truncate before the sort,
  or bound the pre-sort list.

## 4. Slices

- S1 activation (app.py, query.py CLI) — smallest-risk first; makes the lane fire.
- S2 caching (csr.py) — restores the integrated-path win.
- S3 src==dst contract (csr.py) — correctness parity with SQL.
- S4 near-dup memory claim (query.py) — docstring reword + sort/truncation guard.
- Tests: extend `tests/test_csr.py` — activation from the default call shape,
  cache hit/miss across generation drift, src==dst known-but-edgeless parity.

## 5. Acceptance

- Live: API + CLI shortest_path route through CSR (lane fires) on unfiltered calls.
- Live: integrated-path latency ≥ the raw kernel win (cache warm), not 1.27×.
- Parity: src==dst known-but-edgeless returns `[(src,0)]` on both lanes.
- Eval gate: `helpers/misc/ontology_eval_gate.py` over the frozen question set
  between dry-run and canonical apply (ontology_governance S2) — the unfiltered
  semantics change must not regress the frozen question set.
- `make qa` green; new tests cover S1-S3.
