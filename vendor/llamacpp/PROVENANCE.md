# PROVENANCE — unified llama-server (models/llamacpp/bin)

Built by `vendor/llamacpp/build.sh` (llamacpp_unified_server_build S1/S2).
Update this file on every re-pin — the dual-load verdict is the gate:
BOTH models must load and answer, otherwise the pin does not move.

## Current pin

| field | value |
|---|---|
| llama.cpp commit | `3c809f44383bcebc318b8cddd8c6d11aaa861e9b` (2026-10-30, tag b12004) |
| patch | `patches/qwen2vl_head_dim.patch` (35 lines, src/models/qwen2vl.cpp — vendored from bench_data/teleocr/llama_cpp_qwen2vl_teleocr.patch, applied clean) |
| build flags | `cmake -DGGML_NATIVE=ON -DCMAKE_BUILD_TYPE=Release` (CPU AVX2/x86, gcc 15.2.0) |
| llama-server sha256 | `a1221415eefd44e0774096a3cc7a26a1e5617f694ede2d2b78082026466db226` (2026-10-30 rebuild after install-set fix; rebuilds are not bit-exact — morning build hashed `9593b6b2…`) |
| libllama.so.0.8.0 sha256 | `c399d1fe74530c372f51ef237a2949f488459f21fcc3ddb8a51b35400e28ab0c` (the earlier `136556bb…` row had hashed the stale libllama.so.0.6.0 fossil — the old sha line hardcoded `0.6.0`) |
| dual-load verdict | PASS 2026-10-30 (rebuild) — embgemma :8732 serves 768-d embeddings AND teleocr :8731 answers NaviDC chat completion AND granite :8733 serves 384-d embeddings, same binary, canonical ports (`helpers/misc/llamacpp_health.py --ocr` exit 0). Earlier same-day PASS was on the morning build whose install had shipped only the libllama/libggml glob — the b12004 launcher needs `libllama-server-impl.so` + `libllama-common.so`, so production exit-127'd until `build.sh` install set was fixed and the tree rebuilt |
| mmproj requant | 2026-10-09 — mmproj f16 → Q8_0 (`llama-quantize`, same pin; 32/519 tensors correctly fell back to f16). 1,266 → 800 MiB (16.01 → 10.12 BPW). A/B on corpus images: clean banner page byte-identical; dense chart page inconclusive (f16 output itself junk). Adopted as the teleocr-server default; f16 retained as fallback artifact. |

## Lineage

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
