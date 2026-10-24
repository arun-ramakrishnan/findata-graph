#!/usr/bin/env bash
# Build the onager DuckDB extension against DuckDB v2.0.0 (vendor/onager —
# onager_vendored_source_build).
#
# Why vendored: `INSTALL onager` under duckdb 2.0.0 LTS 404s — the community
# bucket has no v2.0.0/linux_amd64 artifact even though upstream v0.5.0 is the
# 2.0.0 port ("Update the project to build after the DuckDB 2.0.0's API
# changes (#32)", 2026-10-23) and its dist pipeline builds duckdb_version:
# v2.0.0. We build the same shape locally; helpers/graph/onager.py prefers a
# loadable extension over the scipy/numpy fallback chain the moment LOAD
# succeeds, so this artifact restores the onager lane for all 13 dispatch
# families (and the personalised-pagerank wrap stays forbidden — see
# doc/pending_improvs.md: personalised_pagerank bug; a vendored build does
# NOT fix that, only an upstream algorithm change would).
#
# Recipe: fetch onager at the PINNED commit, init submodules (their recorded
# SHAs are the release config — external/duckdb == v2.0.0 exactly, asserted
# below), `make release GEN=ninja` (rust core -> libonager.a -> loadable
# extension), install the artifact into models/onager/bin/ (gitignored, like
# the GGUFs). Provenance duty: after a re-pin, update PROVENANCE.md (commit,
# sha256, LOAD verdict — the extension must LOAD and answer one table
# function against production duckdb, that is the gate).
#
# Env overrides: ONAGER_SRC_DIR (source checkout, default under $TMPDIR),
# ONAGER_OUT (default models/onager/bin relative to this script's repo).
set -euo pipefail

PINNED_COMMIT=c45146bc4b259cfcbd32ddace34eb8fc112e061d  # v0.5.0, 2026-10-23, the 2.0.0 port
DUCKDB_PIN=d54cbbe3df17e8e5ce68312f483b4155836112aa  # == duckdb v2.0.0 tag (asserted)
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="${ONAGER_SRC_DIR:-"${TMPDIR:-/tmp}/onager-build/onager"}"
OUT="${ONAGER_OUT:-"$HERE/../../models/onager/bin"}"

echo "== onager-build: pin $PINNED_COMMIT"
if [ ! -d "$SRC_DIR/.git" ]; then
    mkdir -p "$(dirname "$SRC_DIR")"
    git clone https://github.com/CogitatorTech/onager "$SRC_DIR"
fi
git -C "$SRC_DIR" fetch --depth 1 origin "$PINNED_COMMIT"
git -C "$SRC_DIR" checkout --force FETCH_HEAD
test "$(git -C "$SRC_DIR" rev-parse HEAD)" = "$PINNED_COMMIT" || {
    echo "FATAL: fetched HEAD is not the pinned commit" >&2; exit 1; }

echo "== submodules (recorded SHAs = the release config)"
git -C "$SRC_DIR" submodule update --init --depth 1 --force
test "$(git -C "$SRC_DIR/external/duckdb" rev-parse HEAD)" = "$DUCKDB_PIN" || {
    echo "FATAL: external/duckdb is not the v2.0.0 pin" >&2; exit 1; }

echo "== rust core (cargo release; toolchain pinned by rust-toolchain.toml)"
make -C "$SRC_DIR" rust-build

echo "== cmake + build extension (GEN=ninja)"
# DUCKDB_VERSION is load-bearing: without it a source checkout stamps the
# commit hash as its version string (external/duckdb/CMakeLists.txt:484-495
# env override) and duckdb's loadable-extension check refuses the artifact —
# "built specifically for DuckDB version 'd54cbbe3df' ... (this version of
# DuckDB is 'v2.0.0')". The production wheel reports v2.0.0, so the
# extension must carry exactly that string.
DUCKDB_VERSION=v2.0.0 make -C "$SRC_DIR" release GEN=ninja

ART="$SRC_DIR/build/release/extension/onager/onager.duckdb_extension"
test -f "$ART" || { echo "FATAL: $ART not produced" >&2; exit 1; }

echo "== install to $OUT"
mkdir -p "$OUT"
cp -f "$ART" "$OUT/onager.duckdb_extension"

echo "== sha256 (record in PROVENANCE.md on re-pin)"
sha256sum "$OUT/onager.duckdb_extension"
echo "== DONE: $OUT/onager.duckdb_extension — LOAD verdict + sha go into PROVENANCE.md"
