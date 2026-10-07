#!/usr/bin/env bash
# Build the UNIFIED llama-server (vendor/llamacpp — llamacpp_unified_server_build).
#
# One binary serves both server legs:
#   - embgemma  (:8732, models/embeddinggemma-2-Q8_0.gguf)  — needs llama.cpp
#     arch support for gemma-embedding2 (PR #30054, first builds b11457)
#   - teleocr   (:8731, models/NaviDC-OCR-Q4_K_M.gguf + mmproj) — needs the
#     vendored qwen2vl head_dim patch (upstream unfixed as of the pin date)
#
# Recipe: fetch llama.cpp at the PINNED commit, apply the patch, CPU AVX2
# build (GGML_NATIVE=ON — the measured-good scratch shape), install the
# binary + its .so set into models/llamacpp/bin/ (gitignored, like the
# GGUFs). Provenance duty: after a re-pin, update PROVENANCE.md (commit,
# sha256 set, dual-load verdict — BOTH models must load, that is the gate).
#
# Env overrides: LLAMACPP_SRC_DIR (source checkout, default under $TMPDIR),
# LLAMACPP_OUT (default models/llamacpp/bin relative to this script's repo).
set -euo pipefail

PINNED_COMMIT=78651c410dd8d97e3e22e533e0e7117889e3863a  # 2026-10-07, has gemma-embedding2
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH="$HERE/patches/qwen2vl_head_dim.patch"
SRC_DIR="${LLAMACPP_SRC_DIR:-"${TMPDIR:-/tmp}/llamacpp-build/llama.cpp"}"
OUT="${LLAMACPP_OUT:-"$HERE/../../models/llamacpp/bin"}"

echo "== llamacpp-build: pin $PINNED_COMMIT"
if [ ! -d "$SRC_DIR/.git" ]; then
    mkdir -p "$(dirname "$SRC_DIR")"
    git clone https://github.com/ggml-org/llama.cpp "$SRC_DIR"
fi
git -C "$SRC_DIR" fetch --depth 1 origin "$PINNED_COMMIT"
git -C "$SRC_DIR" checkout --force FETCH_HEAD
test "$(git -C "$SRC_DIR" rev-parse HEAD)" = "$PINNED_COMMIT" || {
    echo "FATAL: fetched HEAD is not the pinned commit" >&2; exit 1; }

echo "== applying $(basename "$PATCH")"
git -C "$SRC_DIR" apply --check "$PATCH"   # fails loudly if upstream drifted
git -C "$SRC_DIR" apply "$PATCH"

echo "== cmake + build (CPU AVX2, GGML_NATIVE=ON)"
cmake -S "$SRC_DIR" -B "$SRC_DIR/build" -DGGML_NATIVE=ON -DCMAKE_BUILD_TYPE=Release
cmake --build "$SRC_DIR/build" --config Release -j"$(nproc)"

echo "== install to $OUT"
mkdir -p "$OUT"
# llama-server is a thin launcher against libllama/libggml — ship the .so set
# (copied through, symlinks resolved) so the install runs standalone via $ORIGIN.
cp -L "$SRC_DIR/build/bin/llama-server" "$OUT/llama-server"
cp -L "$SRC_DIR"/build/bin/libllama.so* "$SRC_DIR"/build/bin/libggml*.so* "$OUT/"

echo "== sha256 (record in PROVENANCE.md on re-pin)"
( cd "$OUT" && sha256sum llama-server libllama.so.0.6.0 2>/dev/null || sha256sum llama-server )
echo "== DONE: $OUT/llama-server — dual-load verdict + sha go into PROVENANCE.md"
