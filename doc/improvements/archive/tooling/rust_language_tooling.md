# Rust/ language tooling — fmt/clippy/test/audit gates, build hook, toolchain pin

title: Rust/ language tooling — fmt/clippy/test/audit gates, build hook, toolchain pin
area: tooling
status: executed
filed: "2026-10-26"
completed_md: "446"
executed: "2026-10-26"
related: doc/improvements/archive/database/rust_embed_cache_port.md (#418 era), doc/improvements/archive/graph/rust_cosine_knn_port.md (#440)

## Why (survey, measured 2026-10-26)

`Rust/` (filed 2026-10-25) is now production-carrying — the `cosine_knn`
abi3 extension module backs the live auto→rust cosine selector in
`rebuild_script_search.py` (892 q/s measured at the port arc, #440) —
but it is the only language in the repo with **no toolchain surface**.
The checked-in set is 14 files: 2 workspace manifests + 2 crate
manifests/READMEs + 7 `.rs` sources (cosine_knn: `lib.rs` + `bin/trial.rs`;
embed_cache: `lib.rs` + 3 bins) + 2 Python bench scripts. Gaps, each
verified by running the thing today:

| concern | python | mojo | ts | Rust/ today |
|---|---|---|---|---|
| lint | ruff (qa) | — (parity leg instead) | — | **none** |
| format | ruff format + `test_lint_gates` (qa) | `mojo format` + same gate (qa) | prettier (frontend-check) | **rustfmt not installed** (`cargo fmt`: "'cargo-fmt' is not installed for the toolchain 'stable-x86_64-unknown-linux-gnu'") |
| types | ty (qa) | — | tsc (frontend-check) | **none** (no cargo check leg) |
| tests | pytest (qa/integration) | mojo-test (advisory) | guard tests | `cargo test --workspace` runs **green with 0 tests** — leg exists nowhere |
| security | ruff S (lint-audit, advisory) | — | cargo-audit | **cargo-audit scans only `desktop/src-tauri/Cargo.lock`** — the live `Rust/Cargo.lock` is unaudited |
| deps | deptry (qa) | — | — | nothing (cargo-machete-class, future) |
| perf | perf gate legs | mojo-bench | — | nothing (`cargo bench` surfaces exist in neither crate) |
| build | — | mojo-build (machinery in Makefile.mojo) | frontend build | **cargo's job, and cargo does it** (incremental per-crate, no Makefile machinery needed) — but the INSTALL step is uncovered: `libcosine_knn.so` → `cosine_knn.abi3.so` into site-packages is ad hoc, and the selector degrades silently to numpy when it is absent |
| toolchain pin | — | pinned 1.2/max 26.7 | — | **no `rust-toolchain.toml`** (cargo 1.99.1 / rustc 1.99.1 floating) |

Additional facts that shape the design:

- `clippy-driver`/`cargo-clippy` ARE on PATH but **not installed as
  components** for the stable toolchain — clippy runs refuse.
- `Rust/target/` is gitignored; `Cargo.lock` is tracked (auditable) —
  both correct, keep.
- The pyo3 crate is `crate-type = ["cdylib", "rlib"]`,
  `abi3-py39`, `extension-module` — the venv module
  `cosine_knn.abi3.so` sits in `.venv/lib/python3.14/site-packages/`,
  so the build hook must copy/rename, not just `cargo build`.
- embed_cache stays BACKUP-ONLY (operator ruling #418-era): its bins
  build, but nothing installs it into the venv.

## Decision

- **Advisory-first placement** (mojo-test precedent), promotion to qa
  per-leg on operator go once green for an arc. No new blocking qa legs
  in the first slice — the quartet just closed green; this arc must not
  destabilize it.
- **Format rides the existing qa gate** (`tests/test_lint_gates.py`),
  mirroring the mojo precedent — rustfmt natively has `--check`, so no
  copy-format dance is needed.
- **`rust-toolchain.toml` pin** in `Rust/` (channel + components), the
  Mojo-pinned-toolchain analogue: reproducible builds, and the
  components (`rustfmt`, `clippy`) are declared, not remembered.
- **cargo-audit gains the live lockfile**: `Rust/Cargo.lock` is scanned
  alongside `desktop/src-tauri/Cargo.lock` — the production-carrying
  lock is arguably the higher-value target.
- **No rust-build mirroring mojo-build**: cargo IS the incremental
  build system (per-crate caching; Makefile machinery would be
  re-implementing it). The uncovered piece is the INSTALL step — the
  pyo3 cdylib must reach the venv site-packages as
  `cosine_knn.abi3.so` for the selector to import it (Mojo has no
  analogue: its binaries are consumed from Mojo/bin/ by make targets).
  `make rust-install` does only that copy/rename with a dynamic
  site-packages path; a skipped install is exactly today's behavior
  (numpy fallback, silent by design).

## Design contract

- `Rust/rust-toolchain.toml` — `[toolchain] channel = "1.99.1"`
  (or stable-pin at arc time) + `components = ["rustfmt", "clippy"]`.
- Makefile targets (alphabetical, `##`-annotated, `.PHONY` + help both):
  - `rust-fmt` — fix: `cargo fmt --all` (mirror `mojo-format`).
  - `rust-clippy` — `cargo clippy --workspace --all-targets` (advisory
    leg; `-D warnings` decision deferred to first green run).
  - qa hardening (rides the existing pytest leg, no new gate):
    `test_rust_module_importable_and_parity_with_numpy` is
    importorskip-shaped — a missing/broken abi3 module reads as SKIP
    (green) and the live selector silently degrades to numpy, the
    gemma-sidecar-fallback shape `guard_gemma_stamp` exists for. Add
    an env-gated hard pin (e.g. `SCRIPT_KNN_STRICT=1` set by the qa
    harness -> the test asserts the import instead of skipping),
    keeping the skip for non-qa boxes.
  - `rust-test` — `cargo test --workspace` (advisory leg).
  - `rust-install` — `cargo build --release -p cosine_knn` + copy
    `target/release/libcosine_knn.so` →
    `<purelib>/cosine_knn.abi3.so` (purelib via
    `sysconfig.get_paths()`); SKIPs cleanly without cargo (mirror the
    cargo-audit skip pattern). embed_cache never installs (backup-only).
- `tests/test_lint_gates.py` — new `test_rust_format_footprint_clean`:
  `cargo fmt --all --check` over the checked-in `.rs` footprint; SKIP
  without the rustfmt component (same SKIP-without-toolchain pattern as
  md-lint/mojo legs).
- `Makefile advisory` — add `rust-clippy rust-test` to the leg roster
  (and the help line); `cargo-audit` extended to a second scan of
  `Rust/Cargo.lock`.
- `THIRD_PARTY_LICENSES.md` — pyo3 (Apache-2.0/MIT) entry; the
  license-check reads pyproject only today, so the Rust crate inventory
  is a documented-manual row for now (cargo-deny gate = future slice).
- **Out of scope (future, not filed dead)**: Rust-side unit tests
  (vec_codec round-trip parity is already pinned from the Python side by
  `tests/test_cosine_knn_backends.py`), `cargo bench` wiring into the
  perf gate, cargo-deny (licenses + advisories + bans in one gate),
  unused-dep scan (cargo-machete).

## Risks / open items

1. Clippy's first run on a 7-file workspace may still surface lints —
   the advisory placement absorbs that; the `-D warnings` hardening is
   a follow-up decision on first green.
2. `abi3-py39` vs the venv's py3.14: the abi3 forward-compat wheel is
   the port arc's settled choice — the install hook must not silently
   rebuild for a different interpreter; pin the copy semantics.
3. Toolchain pin freezes at 1.99.1 — bumps become a deliberate diff
   (that is the point), but `Cargo.lock` updates may trail rustup
   updates; noted for the first bump.
4. The `.so` copy is venv-layout-sensitive (`python3.14` path segment);
   the target derives the site-packages dir dynamically
   (`sysconfig.get_paths()["purelib"]`), never hard-coded.

## Action items

- [x] S0: `rustup component add rustfmt clippy` + `Rust/rust-toolchain.toml` pin
- [x] S1: `make rust-fmt` fix target + `test_rust_format_footprint_clean` in `tests/test_lint_gates.py` (qa format gate)
- [x] S2: `make rust-clippy` + `make rust-test`, wired into `make advisory` roster + help line
- [x] S3: `cargo-audit` second scan of `Rust/Cargo.lock` (+ help line)
- [x] S4: `make rust-install` — release-build + dynamic site-packages install of `cosine_knn.abi3.so`, SKIP-without-cargo (no rust-build: cargo owns incremental builds)
- [x] S5: `THIRD_PARTY_LICENSES.md` pyo3 row + pyproject-style inventory note
- [x] S6: review-scan + teeth visibility — `.opencodereview/rule.json` gains
  `Rust/**/*.rs`, `Rust/bench/**/*.py`, `Rust/**/Cargo.toml`,
  `Rust/**/README.md` (READMEs today read `unsupported_ext` — same
  treatment `Makefile.mojo` got); `PRODUCT_FAMILIES` gains a Rust family
  so the teeth assert it (live probe 2026-10-26: OCR delegation already
  selects 25/27 on embed_cache-arc traffic incl. all 7 `.rs` — the gap is
  rule.json/teeth, not the binary)
- [x] S7: `make md-lint`, targeted pytest (`test_lint_gates`), `make search-fresh APPLY=1` after doc edits; gates run same day (trailer in the patch message)

## Execution status (2026-10-26)

- S0: components installed (rustfmt 1.10.0, clippy 0.1.99); `Rust/rust-toolchain.toml` pins channel 1.99.1 + both components (rustup auto-installed the pinned toolchain).
- S1: one-time `cargo fmt --all` normalization (trial.rs line-wrapping); `make rust-fmt` fix target; `test_rust_format_footprint_clean` rides the qa pytest leg (native `--check`, SKIPs without the component).
- S2: `make rust-clippy` + `make rust-test` wired into the advisory roster; clippy first run = 23 warnings (needless casts ×4, doc-list indent ×3, chunks_exact ×2, useless conversions ×2, complex type ×1 — all style-grade; `-D warnings` deferred to first green arc per proposal). qa hard pin: `SCRIPT_KNN_STRICT=1` set by the qa runner turns the importorskip into a hard assert (gemma-fallback shape).
- S3: cargo-audit now scans both lockfiles (desktop 479 crates + `Rust/Cargo.lock`).
- S4: `make rust-install` — release build 12 s + copy to `<purelib>/cosine_knn.abi3.so` verified live.
- S5: pyo3 row in THIRD_PARTY_LICENSES.md.
- S6: rule.json gains 5 Rust includes; PRODUCT_FAMILIES gains cosine_knn/embed_cache/bench teeth families; 81 review-scan tests green.
- **S3 finding of record**: the first `Rust/Cargo.lock` scan found **2 RUSTSEC advisories in pyo3 0.22.6** — RUSTSEC-2026-0177 (`PyCFunction::new_closure` missing `Sync` bound, fix ≥0.29.0) and RUSTSEC-2025-0020 (`PyString::from_object` buffer overflow, fix ≥0.24.1). Our surface uses NEITHER API (grep clean) — not exploitable through `knn(matrix, query, k)`. Remediation = pyo3 0.22 → ≥0.29 upgrade (7 minors, API surface) — its own follow-up slice, not a targeted gate fix. The desktop lockfile also picked up 5 fresh advisories published today (zbus/zvariant/glib family + proc-macro-error unmaintained) — desktop-arc surface.
