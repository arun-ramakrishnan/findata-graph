# PROVENANCE — unified llama-server (models/llamacpp/bin)

Built by `vendor/llamacpp/build.sh` (llamacpp_unified_server_build S1/S2).
Update this file on every re-pin — the dual-load verdict is the gate:
BOTH models must load and answer, otherwise the pin does not move.

## Current pin

| field | value |
|---|---|
| llama.cpp commit | `78651c410dd8d97e3e22e533e0e7117889e3863a` (2026-10-07) |
| patch | `patches/qwen2vl_head_dim.patch` (35 lines, `src/models/qwen2vl.cpp` — vendored from `bench_data/teleocr/llama_cpp_qwen2vl_teleocr.patch`, applied clean) |
| build flags | `cmake -DGGML_NATIVE=ON -DCMAKE_BUILD_TYPE=Release` (CPU AVX2/x86, gcc 15.2.0) |
| llama-server sha256 | `a1beb2b35b8dff3b017eeeb2a769deaf042e6bd68e6e64ddc7017c9dbad7a088` |
| libllama.so.0.6.0 sha256 | `136556bb136f24ba83c0ee1f16e6575f09cd713810ac31992c393c73f26b1ce6` |
| dual-load verdict | PASS 2026-10-07 — NaviDC-OCR-Q4_K_M + mmproj answers a chat completion AND EmbeddingGemma-2-Q8_0 serves 768-d embeddings, same binary, scratch ports 8748/8749 (`$TMPDIR/docs_leg/dual_{navidc,gemma}.log`) |
| mmproj requant | 2026-10-09 — mmproj f16 → Q8_0 (`llama-quantize`, same pin; 32/519 tensors correctly fell back to f16). 1,266 → 800 MiB (16.01 → 10.12 BPW). A/B on corpus images: clean banner page **byte-identical**; dense chart page inconclusive (f16 output itself junk). Adopted as the `teleocr-server` default; f16 retained as fallback artifact. |

## Lineage

- The 78651c4 part reproduces the production embgemma scratch build
  (serving since the script_search_gemma_adoption cutover, completed.md
  #363); the patch part reproduces the teleocr 5e03bdd build
  (`bench_data/teleocr/outputs/build/` — build logs, sha, commit msg).
- Why this pin: it is the newest measured-good commit for the gemma
  arch; upstream master 2026-10-07 still fails NaviDC without the patch
  (`bench_data/teleocr/outputs/build/navidc_load_fail_78651c4.log`).
- Fossil binaries (machine-local, NOT rebuild inputs):
  `bench_data/teleocr/outputs/build/llama-server-5e03bdd-teleocr`,
  `bench_data/embgemma2/outputs/build/llama-server-78651c4`.

## Re-pin procedure

1. `PINNED_COMMIT=` in `build.sh` → new commit.
2. `make llamacpp-build` (patch `--check` fails loudly on drift — rebase
   `patches/` first if so).
3. Dual-load proof: both `make embgemma-server` and `make teleocr-server`
   load their models and answer (bank gate green / teleocr control).
4. Record commit + sha256 set + verdict here.
