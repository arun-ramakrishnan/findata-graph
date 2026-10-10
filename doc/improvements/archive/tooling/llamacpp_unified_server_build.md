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

## 7. Runtime tuning follow-up (2026-10-10)

Three days after cutover the gates slowed: qa pytest 247s → 454–704s,
integration 132s → 274s, and one perf run went 11/26 benches red. Two
mechanisms, both server-side, neither a code regression:

1. **Unpinned sidecar probes in hermetic tests.** The gemma-era
   rebuilders probe `gemma_embedder.available()` first; the test conftest
   pinned only the old bge embedder, so with the servers up, hermetic
   tests ran real llama-server inference (maint-chain test 476s in-suite
   vs 133s standalone). Fixed by pinning gemma/granite probes in
   `tests/conftest.py` (targeted 132 green, maint chain → 46s).
2. **CPU oversubscription.** Both embedding servers ran `-t 4` on a
   4-core box while perf's 4 workers ran: the 22:39 perf run was 1.4–6×
   slower than the same-day 14:37 run (doc_query 1.22→7.02s,
   route_graph_stats 4.12→10.57s), same code, same budgets.

### 7.1 Model caps (probed from the GGUFs, 2026-10-10)

| server | arch | `n_ctx_train` | dim | layers | served : client dims |
|---|---|---|---|---|---|
| gemma-2 :8732 | gemma-embedding2 | 262,144 | 512 | 24 | 768 served → 512 (client Matryoshka truncate + re-normalise, `gemma_embedder.py:67-73`) |
| granite-97m-r2 :8733 | modern-bert | 32,768 | 384 | 12 | 384 = 384 |
| NaviDC-OCR :8731 | qwen2vl | 128,000 | 1024 | 28 | generative |

Note: granite-97m-r2 is **32k ctx, not 8k** (HF model card +
`modern-bert.context_length = 32768` agree; the 8k figure belongs to
older granite generations). The R2's 32k is what makes the 8KiB
trial snippets fittable.

### 7.2 Workload (measured, same day)

| surface | text size | implication |
|---|---|---|
| notes (gemma) | avg 1,471 chars; max 2.2M chars (truncates under any flag) | typical request < 1k tokens; long notes to ~8k tokens |
| convo (granite) | avg 1,353 chars; max 225k chars (truncates under any flag) | typical < 1k; 8KiB snippets ≈ 2.7k tokens |
| teleocr pages | 150-DPI render ≈ 2–2.5k image tokens + ≤ 2048 gen tokens | one request needs ≥ ~5k slot depth |

Both embed clients send **serial single-text** POSTs
(`{"input": text}`), and teleocr OCR is serial pages — no client ever
needs more than 1–2 concurrent slots.

### 7.3 Flags (Makefile:427/435/443, all three restarted + verified)

| server | flags | per-slot depth |
|---|---|---|
| :8732 | `-c 16384 -b 16384 -ub 16384 -np 2 -t 3` | 8192 |
| :8733 | `-c 16384 -b 16384 -ub 16384 -np 2 -t 3` | 8192 |
| :8731 | `-c 16384 -np 2 -b 8192 -ub 2048 -t 3` | 8192 |

`-t 4 → -t 3` on all three removes the oversubscription (perf back to
26/26 green at baseline: doc_query 0.81s, route_graph_stats 4.30s).
`-np 2` doubles per-slot KV depth at identical RAM because the clients
are serial. Ctx values were deliberately NOT cut: 16k covers long
notes and 8KiB snippets with margin, KV fits easily (box: 14 GB RAM,
~6 GB used), and the outliers truncate identically under any flag.

**`-np 2` semantics (correction on record):** it is NOT "two procs × 3
threads". Upstream README (`tools/server/README.md`): `-t` = "number
of CPU threads to use during generation" (ONE shared compute pool per
process), `-np` = "number of server slots", multiplexed by continuous
batching (default on). So `-np 2 -t 3` = one process, one 3-thread
pool, 2 request slots of ctx/2 KV each; a 3rd concurrent request
queues. Per-server CPU footprint ≈ 3 threads, not 6.

**Correctness find during tuning:** teleocr previously ran `-c 8192`
at 4 slots = 2048/slot — smaller than one request (image + gen
budget). That overflows the slot, and llama.cpp answers by shifting
context mid-generation (silent image loss on dense pages). The
8192/slot above fixes it; `REQUEST_TIMEOUT_S = 1800` stays (dense
pages measured ~500s in the trial).

### 7.4 Build verdict: no change (Skylake prior work already embodied)

The box is an i5-6500 (AVX2 + FMA + F16C; no AVX-512/VNNI — `lscpu`
verified). The running binary's own startup line proves the native
build delivered: `CPU : SSE3 = 1 | SSSE3 = 1 | AVX = 1 | AVX2 = 1 |
F16C = 1 | FMA = 1 | BMI2 = 1 | REPACK = 1` (plus `n_threads = 3`,
`n_slots = 2, n_ctx_slot = 8192` live). The applicable Skylake
doctrine was checked item by item and is already our operating point:
`GGML_NATIVE=ON` in `vendor/llamacpp/build.sh` (compiles this host's
AVX2 in); Q8_0 quants = bandwidth- and compute-optimal here per the
`embed_model_eval.md` CPU float-format note (int8 SIMD dots, F32
accumulate — never BF16/FP8 on this CPU); CPU-only build per the
`skylake_igpu_eval.md` verdict (no workload crosses the GPU bar) and
the `gpu_vs_simd` crossover (17–67 MB ≫ our KB-sized batches).
`build.sh` intentionally untouched.

### 7.5 Server throughput bench (2026-10-10, same window, 256 real note texts)

New `--sidecar` mode in `helpers/bench/embed_runtime_bench.py` POSTs
the production shape (serial single-text) at a live leg; pool leg is
`local_embedder.embed_documents_parallel` on the identical texts:

| path | rate | notes |
|---|---|---|
| granite sidecar serial :8733 | **5.42/s** | tuned `-t 3 -np 2` |
| granite sidecar array (1 POST, 256 inputs) | 5.39/s | batching buys nothing — forwards are sync-bound |
| granite in-process pool | 5.63/s | statistical tie with the sidecar |
| gemma sidecar serial :8732 | **1.05/s** | 5× the granite cost (309M/24L vs 97M/12L) — the per-miss price of the gemma stamp |

Read: server flags do not move embedding throughput on this workload —
the bert forward (~185 ms granite, ~950 ms gemma) is the floor in every
shape. The `-t 3`/`-np 2` tuning stands, but for coexistence
(perf 26/26) and slot depth (teleocr overflow), not speed. The
sidecar's value is isolation (no 431 MB model loads inside gate/test
processes, no `_MODEL` singleton leaks), not velocity — and the pool
fallback costs nothing when the sidecar is down. The only lever on
embed wall-time is avoiding the work (the cache), never speeding it:
a full 17k-note gemma re-embed ≈ 4.5 h, 85k convo granite ≈ 4.4 h.

### 7.6 Maintenance timing corpus (maint_query, same day)

Slowness kept arriving without numbers, so the six maint commands got
the gates' evidence treatment: `helpers/maintenance/maint_timing.py`
(RunTimer — run + phase rows with wall start/end/elapsed, writes that
never break the CLI, records only under `MAINT_TARGET` so unit tests
stay out of the corpus) persists into `maint_runs`/`maint_phases` in
`outputs/gate_runs.duckdb`, and `helpers/misc/maint_query.py` mirrors
the gate_query grammar (`recent`, `timing --cmd X [--phase P]`,
`failures`, `phases`). Instrumented: all five `run_rebuild_cli`
surfaces (doc/script/memory/note/convo — walk/collect/compose/embed/
write phases), harvest lanes, gc survey/evict, snapshot
create/check/quick/restore, hif export/validate, analytics loaders
per-source, analytics_fresh. Makefile passes `MAINT_TARGET=<target>`
(`snapshot-full` does not exist — covered `snapshot`,
`snapshot-check`, `snapshot-fresh`, `hif-export` instead). First
finding from the corpus itself: note_search APPLY is
write-dominated (walk 0.9s / embed 0.9s / write 10.2s) — the FTS
rewrite, not embedding, is the cost center.

### 7.7 maint leg in master_query + llama_servers.sh (same day)

`maint` is now a first-class `master_query` leg (`--legs maint`,
included in `--legs all`, N excluded from the default six): it matches
the query against `maint_runs` (cmd/mode/target/summary) and
`maint_phases` (phase/extra), scored-OR newest-first, rendered as
`maint:<id>` hits with phase breakdowns — e.g. `master_query "convo
embed" --legs maint` surfaces the convo APPLY candidate rows with
their embed-phase seconds. Self-writing leg, age-guard exempt like
gates. `helpers/misc/llama_servers.sh {start [--teleocr]|stop|status|
restart}` owns server lifecycle: canonical flags stay in the Makefile
targets (the script carries none), teleocr opt-in only (never started
by default), exact-PID TERM via kernel socket holders (the make/sh
wrapper cmdline mentions the binary, so cmdline match alone
false-positives — status anchors on `^models/llamacpp/bin/…`).
`make llamacpp-health` is KEPT (different mechanism: functional
identity+dims assertion with exit codes, vs the script's process
view) but aligned to the teleocr-down default — pair only, teleocr
via `health.py --ocr`.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-07 | `llama-server 78651c4 -m NaviDC-OCR-Q4_K_M.gguf --mmproj …` :8748 | load FAIL: `blk.0.attn_q.weight expected 1024,1024 got 1024,2048` | stock qwen2vl loader; patch required — `bench_data/teleocr/outputs/build/navidc_load_fail_78651c4.log` |
| 2026-10-07 | archived binary standalone runs | `5e03bdd` and `78651c4` both `--version` OK from bench_data | $ORIGIN rpath, sha256 beside each |
| 2026-10-07 | TMPDIR harvest | build logs + binaries → `bench_data/{teleocr,embgemma2}/outputs/build/` | embgemma2-precedent harvest |
