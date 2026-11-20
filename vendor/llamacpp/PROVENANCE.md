# PROVENANCE — unified llama-server (models/llamacpp/bin)

Built by `vendor/llamacpp/build.sh` (llamacpp_unified_server_build S1/S2).
Update this file on every re-pin — the dual-load verdict is the gate:
BOTH models must load and answer, otherwise the pin does not move.

## Current pin

| field | value |
|---|---|
| llama.cpp commit | `ea84d7090302e8cb9fa1e49d34c08fde5e5c2c55` (2026-11-20, tag b12564; 560 commits after b12004, crossing v1.0.0 = b12359 2026-11-13) |
| patch | `patches/qwen2vl_head_dim.patch` (REBASED 2026-11-20: upstream replaced `Qcur = ggml_rope_multi(...)` with `build_rope(Qcur, inp_pos, il)` — src/llama-graph.h:1316, impl :1796 — breaking old hunk 2; regenerated inserts the QK-norm blocks between build_qkv and the two build_rope lines; round-trip `git apply --check` OK. Still REQUIRED upstream: b12564 `create_tensor_qkv` remains unpatched, no attn_q_norm/attn_k_norm) |
| build flags | `cmake -DGGML_NATIVE=ON -DCMAKE_BUILD_TYPE=Release` (CPU AVX2/x86, gcc 15.2.0); `-DCMAKE_BUILD_RPATH_USE_ORIGIN=ON` inherited from the 2026-11-08 fix |
| llama-server sha256 | `d6df5ab20ea599e1cece7ab44959a48a24e8d1716075d1ce19169010c36598f6` |
| libllama.so.1.0.0 sha256 | `082cb83af0596c1121574aadd5910adc26cf8240bd58195ed81cd73938b6a94f` (v1.0.0 bumped the lib sonames 0.x → 1.x: libllama/libllama-common/libggml/libmtmd .so.1.0.0; libllama-server-impl.so stays unversioned, sha `6407ab53de548f1f87b81688b13b3feba522a658b1ca97842abb5e626a9ade41`; libmtmd.so.1.0.0 `8ee9dda597386b33e89c5473f36a161ad9fcb7e3352ea377055f22c4512bda36`) |
| health verdict | PASS 2026-11-20 — `helpers/misc/llamacpp_health.py --ocr` exit 0: embgemma :8732 768-d, teleocr :8731 NaviDC chat, granite :8733 384-d (identity + live embed dims). First install attempt aborted on `cp: cannot create regular file ... llama-server: Text file busy` (old servers still running) → `llama_servers.sh stop` → rebuild exit 0 → full-replace install |
| bank gate | intent n=46 MRR=0.923 R@1=0.870 R@5=1.000 < 0.93 floor; ident n=8 MRR=1.000 PASS (`helpers/bench/script_eval_bank.py --db memory/script_search.db`). **ATTRIBUTED 2026-11-20: CORPUS DRIFT, NOT THE PIN** — A/B on the same 840-row index with the b12004 pin server (`EMBGEMMA_PORT=8742`, launch via `LD_LIBRARY_PATH=bench_data/llamacpp_pins/b12004` because the pin copy's RUNPATH still names swept scratch `/mnt/data/tmp/findata/llamacpp-build/...`) reproduces the numbers EXACTLY (0.923/1.000) → b12564 query embeddings are rank-identical to b12004; the 0.938→0.923 slide tracks index growth 705→840 rows (mtime 2026-11-15) since the 2026-10-25 re-baseline. Floor RE-BASELINED 2026-11-20 by operator order: `INTENT_FLOOR_MRR` 0.93 → 0.92 (one notch below measured; granite hybrid's 0.914 stays below it), bank green exit 0 — history recorded in the script_eval_bank docstring and the archived unified-server-build §AC. Note for pins/README: pin-server copies need LD_LIBRARY_PATH (or a RUNPATH repair) — only llama-bench was ever truly standalone |
| mmproj requant | 2026-10-09 — mmproj f16 → Q8_0 (`llama-quantize`, same pin; 32/519 tensors correctly fell back to f16). 1,266 → 800 MiB (16.01 → 10.12 BPW). A/B on corpus images: clean banner page byte-identical; dense chart page inconclusive (f16 output itself junk). Adopted as the teleocr-server default; f16 retained as fallback artifact. |

## Lineage

- b12004 → b12564 window (560 commits) — b12564-relevant legs:
  - CPU kernels: VNNI-accelerated Q2_0 quantized ops #26348, BF16 RMS_NORM #32135
  - qwen2vl: FFN scales #32241; mtmd Qwen-VL projector requirements #32130/#32129
  - server fixes #32154/#30492/#32133/#32093
- The b12004 part includes gemma-embedding2 arch support (PR #30054, first builds b11457) plus fixes that matter for your legs:
  - cb55ffe6e2 2026-10-13 "graph: skip the LM head for pooled embeddings (#30445)" — qwen2vl + gemma-embedding graphs stop computing n_vocab logits
  - 9cf967a04d 2026-10-14 "models: drop the t_embd expand of the embedding graphs (#30461)"
  - AVQ2/x86 CPU kernels #30296 tiled accumulators, #30270 F16C fix, #30157 fp32→fp16 vectorize, #30791 silu/expf
  - server 63412d8 "fix exhausting CPU and MEM caused by dry sequence breaker (#31035)"
- The patch remains REQUIRED upstream: master STILL has unpatched `create_tensor_qkv(layer, i, n_embd, n_embd, n_embd_gqa, n_embd_gqa, 0)` and no attn_q_norm/attn_k_norm (teleocr head_dim 128 != n_embd/n_head 64); git apply --check against master exits 0 — no rebase needed.
- Riskier change in window: f3269ef "refactor server entrypoint (#31113)" — dual-load gate exercised both legs and passed.
- Fossil binaries (machine-local, NOT rebuild inputs):
  bench_data/teleocr/outputs/build/llama-server-5e03bdd-teleocr,
  bench_data/embgemma2/outputs/build/llama-server-78651c4.

## Re-pin procedure

1. PINNED_COMMIT= in build.sh → new commit.
2. make llamacpp-build (patch --check fails loudly on drift — rebase patches/ first if so).
3. Dual-load proof: both make embgemma-server and make teleocr-server load their models and answer (bank gate green / teleocr control).
4. Record commit + sha256 set + verdict here. The install is a full-replace
   (old-version libs wiped from `models/llamacpp/bin/` first) and ships the
   launcher's full dependency set — server-impl + common + mtmd included;
   mirroring the production set into `bench_data/llamacpp_pins/<pin>/` keeps
   a restorable server, not just a bench binary.

## 2026-11-20 teleocr dense-page decode loop (PARKED by operator order; predates this re-pin)

- Control gate (`bench_data/teleocr/control_marico_p1.png`, official prompt,
  digit-recall-100% criterion per llamacpp_unified_server_build §AC3) FAILS:
  header decodes then the generation collapses into an endless
  `539 028 768 028 768 …` loop to the 2048-token cap (572 s).
- NOT this pin: the b12004 pin server reproduces the loop BYTE-IDENTICALLY
  (same number multiset 028×254 / 768×253, 509 s), and `--image-min-tokens
  1024` on b12564 changes nothing (572 s, identical junk). Three arms,
  one verdict: the loop lives in the MODEL FILES, not the binary lineage.
- Prime suspect: the 2026-10-09 mmproj f16→Q8_0 requant — the last clean
  control (October trial, digit recall 100%) ran on the f16 mmproj, which
  NO LONGER EXISTS on disk (only q8_0 survives; re-fetch URL in
  bench_data/teleocr/README.md). The requant A/B verified only a clean
  banner page and called the dense chart page "inconclusive" — the dense
  control page was never re-run until today. Secondary suspect (untested):
  sampler/server-default drift between the trial build (5e03bdd) and the
  unified lineage — trial flags differ (n_ctx_slot 8192, -t 4, no
  --image-min-tokens era).
- Operator ruling 2026-11-20: PARK — no further trials. Production legs
  stay on b12564 with canonical flags (embgemma/granite embedding legs
  unaffected: bank gate green under the 0.92 re-baselined floor, health
  PASS). Resume path when picked up: re-fetch f16 mmproj → arm
  b12564+f16 vs q8_0 on the control page → if f16 clean, re-requant or
  revert to f16 for the teleocr leg (TELEOCR_LLAMA_BIN split-pin if the
  embedding legs must stay put); control artifacts from today:
  /mnt/data/tmp/opencode/control_marico_{b12564,b12004_ab,b12564_mintok}.md
  (scratch — volatile).

## 2026-11-08 RUNPATH repair (no re-pin)

- Symptoms: every leg's launcher exit 127 / `ldd` → `libllama-server-impl.so => not found`.
- Root cause: the built binaries baked `$SRC_DIR/build/bin` (scratch, under
  $TMPDIR) as RUNPATH; the scratch dir was swept and the installed copies in
  `models/llamacpp/bin/` lost their only resolution path.
- Fix: `patchelf --set-rpath '$ORIGIN'` on all 17 installed files (binary +
  .so set). Tree is again relocatable; shas below are the POST-REPAIR set
  (bytes changed in the dynamic section only — code unchanged, same pin).
- Build-side fix: build.sh now passes `-DCMAKE_BUILD_RPATH_USE_ORIGIN=ON`,
  so future builds are relocatable at the source.
- Operator ruling 2026-11-08: NO TMPDIR in the build recipe — source
  checkout + build tree default moved from `$TMPDIR/llamacpp-build/llama.cpp`
  to durable machine-local `models/llamacpp/src/llama.cpp` (models/ is a
  gitignored symlink; still overridable via LLAMACPP_SRC_DIR). The 2026-10-30
  install ran fine only because the scratch build dir was still alive to
  satisfy its own baked RUNPATH — that whole class of failure is now out.
- Post-repair shas: llama-server `0498998d2b79ce6a2a745f3cd2388523b234f5fd8b7fd48a27e4d46bff3f94e6`,
  libllama.so.0.8.0 `3062eeb6ed9437ba1f503dd2c0e04dafd0faab2ed77595ed4e43302c529d5e1e`.
