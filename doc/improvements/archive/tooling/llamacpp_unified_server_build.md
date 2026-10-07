---
title: "Unified llama.cpp server build — one owned binary for teleocr + embgemma"
status: executed
filed: "2026-10-07"
executed: "2026-10-07"
completed_md: "364"
area: "Makefile + vendor/llamacpp"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Unified llama.cpp server build — one owned binary for teleocr + embgemma

**Date:** 2026-10-07 · **Status:** EXECUTED (completed.md #364) ·
**Area:** Makefile + vendor/llamacpp (new, committed)

Follows: `doc/local/evaluations/emb_gemma_assessment.md` §2 (runtime
constraints) and `bench_data/teleocr/README.md` (TeleOCR D3 ruling:
llama-server assume-running, `make teleocr-server`). Absorbs the parked
"sidecar production hardening" item: build durability, model/binary
provenance, and maint-time health visibility for the gemma stamps.

**Execution record (2026-10-07, same day):** S1–S3 landed and cut over —
`vendor/llamacpp/{build.sh, patches/qwen2vl_head_dim.patch,
PROVENANCE.md, health.py}` committed; the recipe built the pinned
commit `78651c4` in ~3 min (patch applied clean); BOTH live servers
restarted from `models/llamacpp/bin/llama-server` (gemma :8732,
teleocr :8731); dual-load proof green (NaviDC chat + gemma 768-d embed
from one binary); bank gate green post-cutover (intent 0.964, ident
8/8); `make llamacpp-health` ok/ok. The S3 maint guard (gemma-stamped
index + sidecar down → loud stderr warning) is pinned by
`test_gemma_stamp_with_sidecar_down_warns_loud`. A later same-day
incident validated the design end to end: both servers were manually
killed mid-rebuild, the owned binary brought both legs back, and the
S3 warning path was exercised for real.

## 1. Motivation

Two production legs assume a running llama-server (teleocr on :8731,
embgemma on :8732), and both resolve it through Makefile fallback chains
whose last resort is a scratch build under `/mnt/data/tmp/`
(`Makefile:410-425`) — volatile by policy. The recipe that could rebuild
each binary is *also* not durable: `bench_data/` is entirely gitignored
(`.gitignore:134`), so the teleocr qwen2vl patch, its build logs, and
both working binaries survive only machine-locally. A box change, a tmp
cleanup, or an over-eager `rm -rf` silently reverts both legs to
"not found", and the gemma scripts leg then degrades to granite
**invisibly** (the fallback is correct at query time; nothing surfaces
it at maint time). Two parallel builds also fail a simpler test: they
are mutually exclusive, so "keep both" is not even a working strategy.

## 2. Evidence (measured 2026-10-07, this box)

| Fact | Result | Source |
|---|---|---|
| teleocr scratch build | llama.cpp **5e03bdd** (2026-10-05): loads NaviDC (patched), predates gemma arch (#30054, first builds b11457 2026-10-06) — cannot serve embeddings | `bench_data/teleocr/outputs/build/build.log`, binary archived beside it |
| embgemma scratch build | llama.cpp **78651c4** (2026-10-07): loads gemma Q8_0 (production today), **fails NaviDC at load**: `check_tensor_dims: 'blk.0.attn_q.weight' has wrong shape; expected 1024,1024 got 1024,2048` | load test 2026-10-07, archived `bench_data/teleocr/outputs/build/navidc_load_fail_78651c4.log` |
| qwen2vl patch | 35 lines, `src/models/qwen2vl.cpp` only (Q/wo widths from `n_head * head_dim` + optional per-head QK-norm); orthogonal to the gemma-embedding arch registry | `bench_data/teleocr/llama_cpp_qwen2vl_teleocr.patch` |
| patch still required | YES as of master 2026-10-07 — the 78651c4 load failure above IS the stock-loader path | same log |
| in-venv alternative | llama-cpp-python 0.3.36 is the PyPI latest and cannot load `gemma-embedding2`; in-process would need an undeclarable from-source wheel | assessment §2 |
| binaries archived | both scratch binaries + .so sets run standalone from `bench_data/*/outputs/build/` (sha256 recorded, $ORIGIN rpath verified) | harvested 2026-10-07 |

One paragraph: the two legs need ONE llama.cpp — any commit ≥ b11457 —
with the 35-line qwen2vl patch applied on top. The patch touches only
the qwen2vl model file and is backward-compatible with stock Qwen2.5-VL
GGUFs, so a single binary serves NaviDC-OCR (:8731) and
EmbeddingGemma-2 (:8732) as two processes (llama-server is
single-model; we share the BUILD, not the process). What's missing is
ownership: a committed recipe, a pinned commit, recorded provenance,
and a maint-time health signal.

## 3. Design

Slices, each independently landable:

- **S1 — owned, committed build recipe.** New tree dir `vendor/llamacpp/`:
  - `build.sh` — clones `ggml-org/llama.cpp` at a PINNED commit (≥ b11457;
    first candidate: the commit that reproduces both legs, verified in S2),
    applies `patches/qwen2vl_head_dim.patch` (vendored verbatim from
    `bench_data/teleocr/llama_cpp_qwen2vl_teleocr.patch`), configures CPU
    AVX2 `GGML_NATIVE=ON` (the measured-good scratch shape, assessment
    §2), builds `llama-server` + libs into `models/llamacpp/bin/`.
  - `patches/qwen2vl_head_dim.patch` — the vendored patch (in git; the
    bench_data copy stays as provenance).
  - `PROVENANCE.md` — llama.cpp commit + date, patch identity, build
    flags, sha256 of the produced binary set, and the dual-load verdict
    (both models load + answer). Update on every re-pin.
  - `models/llamacpp/` is under the existing gitignored `models/` — the
    BINARY is a build artifact like the GGUFs; the RECIPE is the durable
    asset. Fallback if models/ is lost: re-run the recipe (~10 min).
- **S2 — one-time production build + Makefile rewire.** Run the recipe;
  add `make llamacpp-build` (delegates to the recipe) and rewire BOTH
  server targets to resolve: env override (`EMBGEMMA_LLAMA_BIN` /
  `TELEOCR_LLAMA_BIN` keep working) → `models/llamacpp/bin/llama-server`
  → PATH → scratch paths (kept one arc, then removed). Cutover proof:
  restart both servers from the owned binary; embgemma bank gate green on
  live (`helpers/bench/script_eval_bank.py`); teleocr control
  (`scripts/run_official_prompt.py`, digit recall 100%) — targeted only.
- **S3 — maint-time health visibility.** When an index stamp says gemma
  (`script_search_info`/`doc_search_info` = `embeddinggemma-2-q8_512`)
  and `gemma_embedder.available()` is False, the rebuild CLIs
  (`rebuild_script_search.py`, `rebuild_doc_search.py`) and
  `make search-fresh` print a loud `WARNING: gemma-stamped index but
  sidecar down — this rebuild degrades to granite` (and exit non-zero
  under `--check`-style modes). Plus `make llamacpp-health`: one-shot
  probe of :8731 and :8732 (model identity + dims), usable from maint.
  Query-time behavior is UNCHANGED — the silent granite fallback stays
  correct for serving; this only makes it visible to the operator.
- **S4 — upstream watch (no work until it fires).** When upstream lands
  the qwen2vl head_dim fix (or the GGUF is re-conformed), drop the
  patch, advance the pin, re-run the dual-load proof. The recipe's
  both-models-load check is the gate that keeps a re-pin honest.

Alternatives: (A) keep two scratch builds — rejected: volatile,
mutually exclusive, already proven non-durable (this proposal exists
because of it). (B) vendored llama-cpp-python wheel with #30054 —
rejected for now: undeclarable dependency (PyPI latest 0.3.36), from-source
build re-vanishes on venv rebuild, and the sidecar shape is the measured
verdict (one 4T server, assessment §4.6); loopback HTTP is ~ms against a
~740 ms/text forward. Revisit only if the HTTP hop ever shows in a
profile. (C) single multi-model server (llama-swap et al.) — rejected:
new supervision infra for two models that never co-schedule.

## 4. Acceptance criteria & shakedown

1. `rm -rf /mnt/data/tmp/embgemma2/llama.cpp /mnt/data/tmp/teleocr_trial/llama.cpp`
   (the scratch builds) leaves both `make embgemma-server` and
   `make teleocr-server` functional from `models/llamacpp/bin/`.
2. Recipe rebuild from a fresh clone dir reproduces a working binary;
   `PROVENANCE.md` carries the new sha256 set; the archived bench_data
   binaries remain as historical provenance.
3. Dual-load proof with ONE binary: embgemma bank gate green on live
   (intent ≥ 0.94, ident 1.000) AND teleocr control digit recall 100%.
4. Health: stop the gemma server, run a stamped-index rebuild
   `--check` — loud warning, non-zero exit; restart, warning gone.
   Targeted suites green (`script_search`, `local_embedder`, shared-vector)
   + ruff + `ty` — never `make pytest`.

| Projected outcome | Today | After |
|---|---|---|
| binaries durable across box change | no (TMPDIR scratch, gitignored recipes) | yes (committed recipe + pinned commit) |
| builds to maintain | 2, mutually exclusive | 1, dual-load gated |
| silent gemma→granite degradation at maint | invisible | loud warning + non-zero check |
| rebuild time after loss | unknown (recipes scattered) | ~10 min, one command |

## 5. Risks

- **Patch rebase drift on future pins** — the patch is small and
  file-local; the dual-load proof (AC3) gates every re-pin, so drift
  fails loudly at build time, not at serving time.
- **Upstream moves #30054 / model archs** — pin + sha256 makes drift a
  diff, not a mystery; S4 is the standing exit.
- **Binary rot in models/ (gitignored)** — same doctrine as the GGUFs:
  provenance recorded, recipe reproduces; bench_data binaries are the
  last-resort fossil.
- **Port collision on shared boxes** — unchanged from today (8731/8732
  loopback, assume-running D3); `make llamacpp-health` reports identity
  so a wrong-server-on-port is caught too.

## 6. Non-goals

In-process llama-cpp-python vendoring (alternative B, on record);
single-process multi-model serving; Vulkan iGPU (CLOSED negative,
assessment §2); Q6 re-quant swap (on-record fallback); torch/multimodal
(page-image retrieval — separate infra decision); any change to
query-time fallback semantics.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-07 | `llama-server 78651c4 -m NaviDC-OCR-Q4_K_M.gguf --mmproj …` :8748 | load FAIL: `blk.0.attn_q.weight expected 1024,1024 got 1024,2048` | stock qwen2vl loader; patch required — `bench_data/teleocr/outputs/build/navidc_load_fail_78651c4.log` |
| 2026-10-07 | archived binary standalone runs | `5e03bdd` and `78651c4` both `--version` OK from bench_data | $ORIGIN rpath, sha256 beside each |
| 2026-10-07 | TMPDIR harvest | build logs + binaries → `bench_data/{teleocr,embgemma2}/outputs/build/` | embgemma2-precedent harvest |
