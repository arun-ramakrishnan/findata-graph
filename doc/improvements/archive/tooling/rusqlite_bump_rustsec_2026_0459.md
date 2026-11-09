---
title: "rusqlite/libsqlite3-sys bump — clear RUSTSEC-2026-0459 (CVE-2026-119375) from both lockfiles"
status: executed
executed: "2026-11-09"
completed_md: "498"
filed: "2026-11-09"
area: tooling
---

# rusqlite/libsqlite3-sys bump — RUSTSEC-2026-0459

**Date:** 2026-11-09 · **Status:** EXECUTED same day · completed.md: #498

## Summary

The advisory gate's `cargo-audit` leg scans two lockfiles and BOTH now fail
with a real vulnerability (not an unmaintained warning):

| Lockfile | audited deps | libsqlite3-sys | via | patched |
|---|---|---|---|---|
| `desktop/src-tauri/Cargo.lock` | 481 | **0.30.1** | `rusqlite` 0.32.1 ← `findata-core` (`desktop/src-tauri/core/Cargo.toml:8`, `bundled`) | ≥ **0.39.0** |
| `Rust/Cargo.lock` | 222 | **0.31.0** | `rusqlite` 0.33.0 ← `embed_cache` (`Rust/embed_cache/Cargo.toml`, `bundled`) | ≥ **0.39.0** |

`cargo-audit` reports **1 vulnerability found** per scan:
**RUSTSEC-2026-0459** — "`libsqlite3-sys` via C SQLite **CVE-2026-119375**",
severity **6.8 (medium)**, dated 2026-10-30, fix **upgrade to >=0.39.0**.
This is the single red keeping the advisory gate at 19/24 that is actually
ours to fix (the other reds: convo-fresh parked chronic, lint-audit fixed
upstream, script-search fixed, rust-deny license exception landed in
feature_evals).

Both consumers use `features = ["bundled"]` — the CVE'd C SQLite source is
**vendored and compiled into the binary**, so the fix is a crate bump, not a
2026-11-09 via `cargo search`). Measured pairing (crates.io deps API,
2026-11-09): rusqlite 0.40.2 still requires libsqlite3-sys `^0.38.2` —
**0.41.1 is the FIRST and only stable minor satisfying the >=0.39 fix bar**
(`^0.39.0`), so "latest" and "minimal sufficient" are the same version:

| rusqlite | libsqlite3-sys req |
|---|---|
| 0.37.0 | ^0.35.0 |
| 0.38.0 | ^0.36.0 |
| 0.39.0 | ^0.37.0 |
| 0.40.2 | ^0.38.2 |
| **0.41.1** | **^0.39.0** |

## Evidence

- qa/perf/advisory run of 2026-11-09 21:03–21:06 (`outputs/advisory_report.md`
  tail, run at 21:03:17): cargo-audit FAILED on both scans with the table
  above; the same run's `rust-deny` failure was the (separately fixed)
  license allowlist, NOT this advisory — deny's advisory DB will flag the
  same RUSTSEC id until the bump lands.
- `embed_cache` is **bench-only**: its `.abi3.so` is not installed into the
  venv; production consumes `helpers/core/embed_cache` (a different, Python
  module). Blast radius of the Rust/ bump = benches + 1 cargo test.
  `findata-core` IS the desktop app's storage layer — the desktop bump is
  the one with real surface, and it ships through the desktop's own build,
  not this repo's Python.
- Recorded baseline to re-verify after the bump
  (`Rust/embed_cache/README.md`): full-pipeline Rust ~2.5–3.4x faster than
  Python; rusqlite insert/lookup 3–4x; raw SHA-256 still OpenSSL-led
  (226–265 vs 138–180 MB/s). A newer bundled SQLite can move these numbers.
- API surface in consumer code is the stable core only:
  `Connection`, `params!`, `OptionalExtension`, `ValueRef`,
  `Connection::open_in_memory`, `rusqlite::Result`
  (`Rust/embed_cache/src/lib.rs:5-49`) — no exotic APIs, so the
  0.32/0.33 → 0.41 port should be small.

## Doctrine hooks

- `Makefile` cargo-audit target comment: "RustSec scan of the desktop +
  Rust/ lockfiles (advisory; ... security_evaluation Addendum 7 §E)" — the
  desktop lock is explicitly in this repo's audit scope.
- Advisory-gate doctrine: informational legs never block an arc, but a
  medium-severity RUSTSEC in vendored C is a real finding, not gate noise.
- The pending.md kernel-selector ladder is untouched: this is a dependency
  security bump, not a kernel adoption.
- Landing mechanics follow the standard arc rules: work stays in the tree
  for operator staging; full gates once on operator go.

## Plan

- **S1 — Rust workspace bump** (`Rust/embed_cache/Cargo.toml`):
  `rusqlite` 0.33 → **0.41.1** (measured minimal sufficient, see Evidence). `cargo update -p rusqlite`, fix API fallout, read the
  resolved `libsqlite3-sys` version back from `Rust/Cargo.lock` and assert
  ≥0.39.0. `cargo test --workspace`, clippy, fmt green.
- **S2 — desktop bump** (`desktop/src-tauri/core/Cargo.toml`):
  `rusqlite` 0.32 → same target; `cargo test -p findata-core` + desktop
  test suite as available. Desktop builds ride the desktop toolchain — do
  not wire desktop builds into this repo's Make targets.
- **S3 — re-baseline embed_cache bench claims**: rerun the README's
  insert/lookup + SHA comparisons against the new bundled SQLite; update
  `Rust/embed_cache/README.md` numbers if drifted. The 3–4x / 2.5–3.4x
  claims must reflect the shipped versions, not the retired ones.
- **S4 — advisory legs re-run (targeted)**: `cargo audit` on BOTH
  lockfiles (expect: RUSTSEC-2026-0459 gone, allowed warnings unchanged);
  `cargo deny check licenses` unchanged-green; record results in the arc
  trailer. Full advisory gate once on operator go.

## Acceptance

1. `cargo audit` (desktop + Rust/) reports **0 vulnerabilities** — the
   six allowed warnings (proc-macro-error, zbus_names, zvariant,
   zvariant_derive, glib, async-executor yanked) are explicitly allowed and
   out of scope here.
2. `Rust/Cargo.lock` + `desktop/src-tauri/Cargo.lock` both carry
   `libsqlite3-sys >= 0.39.0`.
3. `cargo test --workspace` green (Rust/); findata-core tests green
   (desktop); clippy + fmt clean.
4. `Rust/embed_cache/README.md` bench numbers re-measured post-bump (or
   unchanged and stated as re-verified).
5. Advisory gate's cargo-audit leg green on the next full run.

## Decision points

1. **Bump target** — RESOLVED by measurement (Evidence table, 2026-11-09):
   rusqlite 0.40.2 still requires libsqlite3-sys ^0.38.2, so 0.41.1 is the
   FIRST and only stable minor resolving >=0.39 — "latest" and "minimal
   sufficient" are the same version. Target: **0.41.1**, no choice remains.
2. **One arc or two**: both locks in one arc (recommended — one RUSTSEC id,
   one close-out) vs Rust/ first (bench-only risk) with desktop following.
3. **bundled stays**: keep `features = ["bundled"]` (vendored, patched
   source, reproducible builds) — recommend yes; switching to system
   libsqlite3 moves the CVE decision to the distro.

## Alternatives rejected

- **Stay on current versions**: gate stays red and the vendored C SQLite
  keeps the CVE — not a parkable class (vulnerability, not unmaintained).
- **Link system libsqlite3** (drop `bundled`): CVE posture becomes
  distro-dependent; the audit finding would return as an uncontrolled
  native-dep question. Rejected.
- **Delete the `embed_cache` crate** (it is bench-only and would clear one
  scan): loses the recorded rusqlite/SHA comparison baseline and the bench
  harness for future cache work; the bump is cheaper than the deletion.

## Out of scope

- The **allowed warnings** in both scans: proc-macro-error RUSTSEC-2024-0370
  (unmaintained; waits on divan/proptest upstream), zbus_names /
  zvariant / zvariant_derive RUSTSEC-2026-037{0,1,2} + glib
  RUSTSEC-2024-0429 + async-executor yanked (desktop/tauri stack,
  upstream-bound), arrow-data RUSTSEC-2026-0442 (unsound; rides the duckdb
  crate, not rusqlite).
- `helpers/core/embed_cache` (Python) — unaffected; its tests never touch
  the Rust crate.
- Any desktop feature work; this arc is dependency-security only.

## Execution record (2026-11-09)

All three decision points resolved by the same-day arc:

1. **Bump target** — 0.41.1 exactly (measured minimal sufficient, Evidence table).
2. **One arc or two** — one arc: Rust/ and desktop bumped the same day.
3. **bundled stays** — kept on both sides.

Outcome: locks resolve `libsqlite3-sys` 0.39.1 (Rust/ 0.31.0→0.39.1, desktop
0.30.1→0.39.1); `cargo audit` rc=0 on both locks (allowed warnings unchanged);
`cargo deny check licenses` ok; workspace tests green, clippy -D warnings + fmt
clean, zero API fallout. `findata-core` 16/20 — the 4 failures
(ego_company_bundle, ego_sector_bundle, entity_hyperedges_bundle,
similar_notes_ranking) are PRE-EXISTING live-data drift, control-proven by
re-running on the old lock (identical 16/4). `embed_cache/README.md`
re-baselined: full-pipeline 3.1–4.7×, insert 3.1–3.4×, lookup 1.4–1.8× per row.
New transitives: hashlink 0.12.2, thiserror 2, rsqlite-vfs, sqlite-wasm-rs.
Acceptance 5 (advisory full-run cargo-audit green) fires on the operator's
gate go.
