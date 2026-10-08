---
title: "Prefer the shared granite llama-server for embed workloads; in-process stays the fallback"
status: deferred
filed: "2026-10-08"
executed: "2026-10-08"
completed_md: "366"
area: "helpers/core/local_embedder + Makefile + vendor/llamacpp"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# Prefer the shared granite llama-server for embed workloads; in-process stays the fallback

**Date:** 2026-10-08 · **Status:** ARCHIVED DEFERRED (filed and parked
the same day, never executed — revisit triggers in §5) ·
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
  target mirroring `embgemma-server` (env → `models/llamacpp/bin` →
  PATH resolution), and a granite leg in `llamacpp-health`.
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
