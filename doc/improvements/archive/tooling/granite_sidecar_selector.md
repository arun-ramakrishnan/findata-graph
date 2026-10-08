---
title: "Prefer the shared granite llama-server for embed workloads; in-process stays the fallback"
status: executed
filed: "2026-10-08"
executed: "2026-10-09"
completed_md: "370"
area: "helpers/core/local_embedder + Makefile + vendor/llamacpp"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# Prefer the shared granite llama-server for embed workloads; in-process stays the fallback

**Date:** 2026-10-08 · **Status:** PROPOSED (revived same day — filed and
parked in the morning, §5 trigger #4 fired by afternoon; execution in
flight in the `granite_serve` patch) ·
**Area:** helpers/core/local_embedder + Makefile + vendor/llamacpp

**Follows:** `doc/improvements/archive/tooling/llamacpp_unified_server_build.md`
(completed.md #364 — one owned llama.cpp binary, D3 assume-running),
`doc/improvements/archive/tooling/script_search_gemma_adoption.md`
(completed.md #363 — the `guard_gemma_stamp` demotion-refusal pattern),
and `doc/local/evaluations/emb_gemma_assessment.md` §2 (runtime
constraints: llama-cpp-python 0.3.36 cannot load `gemma-embedding2`).

## 1. Motivation

In-process embedding models bill per **process**. Every xdist worker,
every parallel rebuild, every CLI invocation and harness session pays
its own model load and holds its own copy of the weights plus compute
buffers — N parallel consumers means N copies. The test suite already
works around this by pinning the local embedder OFF
(`tests/conftest.py::_no_local_embedder`, deterministic 64-dim pseudo
fallback), and the live-vector classes skip when they can't run.

The llama-server sidecar pattern (proved by the gemma adoption) flips
the accounting: **one process holds the model once**, and every client
is a thin loopback HTTP call — ~1 ms against granite's ~4 ms warm
in-process embed, i.e. noise. It also dissolves the in-process
concurrency hazards we hand-managed: the `_EMBED_LOCK` (two threads
entering `Llama()` together silently killed the process) and the
stdout-dup2 trap become llama-server's problem, and it handles its own
queueing/batching.

granite is the last in-process model in the repo. Every granite
consumer — the note/doc/memory/company rebuilders, `/api/search`, the
master_query legs, the TUI notes lane — imports llama-cpp-python and
loads its own copy.

## 2. Design (slices at execution time)

- **S1 — third server leg on the owned binary**: host
  `models/granite-embedding-97M-multilingual-r2-Q8_0.gguf` with the same
  vendored llama-server (proposed port 8733), a `make granite-server`
  target (env → `models/llamacpp/bin` resolution). **Operator ruling at
  revival (2026-10-08): our unified server ONLY — no system-PATH or
  scratch-build fallbacks in the resolution chain** ("it supports all
  our models in one umbrella"); a missing binary errors with
  `make llamacpp-build` guidance. Ruling applied to ALL THREE server
  targets (embgemma + teleocr trimmed to the same two-step chain in the
  same change). Plus a granite leg in `llamacpp-health`.
- **S2 — prefer-sidecar selector in `local_embedder`**:
  `GRANITE_EMBEDDER=auto|sidecar|process`, default auto — sidecar when
  healthy, in-process otherwise. **No demotion guard is needed here**:
  the model label is unchanged by a runtime swap, so stamps,
  `check_query_vector`, and the demotion-refusal class do not apply
  (that guard is about MODEL identity, not runtime).
- **S3 — cache-key decision**: the shared cache is keyed
  `(text_hash, model label)`; sidecar and in-process embeds of the same
  text would share a key while being near-identity rather than
  bit-identical (llama-cpp-python 0.3.36 bundles an older llama.cpp
  than the vendored build; cross-runtime determinism precedent: cos
  0.99984). Either accept the drift (retrieval-irrelevant — the P0 bar
  is ≥0.999) or suffix the label per runtime; decide when executed.
- **S4 — tests stay hermetic**: the pseudo-64-dim default and the
  skip-when-down live classes are unchanged; live classes may prefer
  the sidecar when it is up.

## 3. Explicitly unchanged

- gemma stays sidecar-required with `guard_gemma_stamp` refusals (its
  runtime cannot go in-process — the PyPI wheel cannot load the arch).
- in-process granite remains the always-works fallback: **no surface
  gains a hard server dependency**; a down granite server degrades to
  today's behavior, never a refusal.
- D3 doctrine: no process spawning from library code;
  `make granite-server` is operator-run.

## 4. Acceptance criteria (when executed)

1. `make llamacpp-health` probes three legs; granite leg verifies
   identity + a live 384-d embed.
2. `GRANITE_EMBEDDER=auto` resolves sidecar-up → sidecar, sidecar-down →
   in-process, with no stamp change on any surface and zero
   `script_search`-style accidents (the demotion guard class cannot
   fire — same label both runtimes).
3. Parallel rebuild benchmark (two concurrent search-fresh APPLY runs)
   shows the expected sharing win; numbers recorded in the execution
   record.
4. Targeted suites green: local_embedder, embed_cache, rebuilders,
   vec_search; ruff/format clean.

## 5. Why deferred + revisit triggers

Filed and parked 2026-10-08 (operator: "revisit this architecture
later"). The sidecar pattern is proven and the motivation is real, but
nothing hurts today: the test suite is hermetic by design, the gemma
sidecar covers the hot surface, and the embed cache keeps warm rebuilds
~seconds. Reopen when any of:

- parallel-run model overhead is **measured** as a real cost again
  (parallel rebuild arcs, xdist-heavy runs);
- a fourth surface adopts gemma (sidecar traffic grows and the
  symmetry argument strengthens);
- the PyPI llama-cpp-python wheel ships llama.cpp #30054 — gemma could
  then go in-process too, and the whole in-process-vs-server question
  reopens in the other direction;
- a scratch trial shows the granite server leg costs trivial RSS/ops —
  cheap early de-risk that would fast-track S1.

## 6. Revival (2026-10-08, same day — trigger #4 fired)

The de-risk trial ran against the unified binary that afternoon, while
the notes full-pool gemma embed held the box:

- `models/llamacpp/bin/llama-server` + the granite Q8 GGUF on **:8733**
  (`--embeddings --pooling mean`): health ok, identity verified, live
  embeds return **384-d** (the stored dimension).
- **RSS 212 MB** (gemma leg: 1.5-1.7 GB) — the "trivial RSS" trigger,
  satisfied with an order of magnitude to spare.
- Rate 1.41/s at ~3.1k-char texts **contended** (the gemma embed owned
  all four cores); the quiet §4.1 bar is 5.24/s server-side. Quiet
  re-measure is an acceptance step, gated on the notes definitive run
  finishing.

Operator revived the proposal and opened the `granite_serve` patch.
**Failsafe-first ruling (operator, same day): the in-process path stays
the load-bearing fallback — if no server is found, every granite
consumer must behave exactly as today.** S2's selector is therefore the
first scaffold slice: `GRANITE_EMBEDDER=auto|sidecar|process`, default
`auto` = probe once per call-site batch, sidecar when healthy,
in-process on ANY failure (probe, request, shape), never a refusal;
`process` never touches the network; only `sidecar` is a hard
dependency (tests/benchmarks).

Serve test runs (acceptance #1/#3, quiet re-rate) are gated on the
notes gemma embed completing (~20:57). S3 decision defaults to
**accept drift** (same model label, no cache-key suffix — a per-runtime
key would re-embed the corpus for no retrieval-visible gain at the
0.99984-cos precedent); the ctx-window nuance (in-process `_N_CTX`
2048 vs server window) is bounded to bases over ~2048 tokens and is
counted before the verdict freezes.

## 7. Execution record (2026-10-09)

- **Acceptance #1 — three-leg health**: `make llamacpp-health` green
  (embgemma :8732, teleocr :8731, granite :8733 — identity + live
  384-d embed). The teleocr leg needed the NaviDC pair decompressed
  from `.zst` into the shared `models/` symlink first.
- **Quiet re-rate** (64 × 3.1k-char texts, single client, three
  servers resident on the 4-core box): sidecar **2.86/s** vs
  in-process **2.08/s** measured back-to-back in the same box state —
  sidecar 1.38× faster. The historical 5.24/s bar was a different box
  state (2026-10-07, pre notes-flip resident models); the absolute
  number moved, the relative verdict favors the server.
- **Acceptance #3 — parallel sharing** (4 worker processes × 16
  embeds): sidecar **32.6 s** (1.96/s aggregate) vs process
  **143.3 s** (0.45/s) — **4.4×**. RSS: in-process 420 MB **per
  process** (×N consumers) vs the one 212 MB server shared by all.
- **S3 verdict frozen: accept drift.** Window count: doc_search
  61/3,045 (2.0%) and memory_search 17/93 (18.3%) bases exceed the
  in-process 2048-token ctx — the sidecar's 16,384 window embeds those
  fuller; cached truncated vectors age out with their texts. No
  cache-key suffix.
- **S4 — suites**: local_embedder 23 green (5 new hermetic selector
  tests: auto-up prefers sidecar, auto-down fails safe, process never
  touches the network, sidecar-down refuses with `make granite-server`
  guidance, unknown env degrades to auto); embed_cache, vec_search,
  rebuilders green; ruff/format clean. Selector tests stub
  `_embed_process` — calling the real loader would load the live model
  into module state and defeat the conftest hermetic pin.
- **Doctrine holds**: failsafe-first verified live (auto + server down
  → in-process, byte-identical labels, no stamp surface). granite is
  no longer the last in-process model in the hot path — but it remains
  the always-works fallback, exactly as ruled.

## 8. Server-leg operations reference (2026-10-09)

One vendored binary serves all three legs:
`models/llamacpp/bin/llama-server` (pin `78651c41` + qwen2vl
head-dim patch; rebuild: `make llamacpp-build`). Resolution chain is
env override → `models/llamacpp/bin` ONLY — no system-PATH or
scratch-build fallbacks (operator ruling). Health: `make
llamacpp-health` probes all three (identity + live embed where
applicable). `vendor/llamacpp/PROVENANCE.md` carries the binary hash
set + the mmproj requant verdict.

| leg | port (env) | model files | idle RSS | lifecycle |
|---|---|---|---|---|
| embgemma | 8732 (`EMBGEMMA_PORT`) | `models/embeddinggemma-2-Q8_0.gguf` (~615 MB) | ~1.6 GB | **always-on for search**: notes/company/memory/script surfaces are gemma-stamped — down → queries degrade BM25-only, rebuilds REFUSE |
| granite | 8733 (`GRANITE_SIDECAR_PORT`, `GRANITE_EMBEDDER=auto\|sidecar\|process`) | `models/granite-embedding-97M-multilingual-r2-Q8_0.gguf` (~104 MB) | ~212 MB | **optional, failsafe-first**: doc_search/memory/convo consumers fall back to in-process automatically; a down server costs throughput, nothing breaks |
| teleocr | 8731 (`TELEOCR_PORT`) | `models/NaviDC-OCR-Q4_K_M.gguf` (LLM, 484 MB, original — never requantized further) + `models/NaviDC-OCR-mmproj-q8_0.gguf` (vision encoder, 800 MB; requantized f16→Q8_0 2026-10-09, byte-identical OCR on the working test page) | ~2.5 GB | **ON-DEMAND ONLY**: consumer is the advisory PDF→md OCR flow (`pdf_conv_md` → `teleocr_engine`), never gates/search; D3 — no auto-spawn; start `make teleocr-server`, run the OCR work, kill it |

Model-file state: the superseded `NaviDC-OCR-mmproj-f16.gguf` (1.27 GB)
is retained as the quality-fallback artifact (deletable once q8_0 is
trusted across real batches); the `.zst` model archives were removed
from the shared `models/` symlink target on 2026-10-09 (compression
judged not worth it — Q4_K_M weights are near-incompressible; the real
lever was the mmproj requant).
