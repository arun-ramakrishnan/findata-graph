---
title: "cargo-deny superset scan for Rust/ — advisories + bans + licenses + sources"
status: executed
filed: "2026-11-01"
executed: "2026-11-01"
completed_md: "464"
area: "Rust/ (tooling) + Makefile advisory"
---

# cargo-deny superset scan for Rust/ — advisories + bans + licenses + sources

**Date:** 2026-11-01 · **Status:** EXECUTED (filed + executed same day) ·
**Area:** `Rust/` (tooling) + Makefile advisory

## 1. Motivation

cargo-audit covers only RustSec advisories. The tooling-mapping verdict
(2026-10-26, `rust_language_tooling` #446) recorded cargo-deny as the
"future one-gate superset (advisories+licenses+bans) replacing per-crate
THIRD_PARTY rows". The 2026-11-01 enhancement sweep ranked it the only
unblocked, untriggered Rust tooling slice; the operator ruled "lets do
cargo-deny first".

## 2. Evidence (measured 2026-11-01, this box)

cargo-deny 0.20.2 (`cargo install cargo-deny --locked`, ~5 min compile),
`cargo deny check` over `Rust/` (cosine_knn + embed_cache, 68-crate lock):

| Check | First result | After fixes |
|---|---|---|
| advisories | ok (0 findings incl. warnings — pyo3 0.30.1 holds) | ok |
| bans | ok | ok |
| sources | ok | ok |
| licenses | FAILED (3 classes) | ok |

License findings and fixes (the real work of the slice):

1. **First-party crates unlicensed** — cosine_knn/embed_cache had no
   `license` manifest field. Fixed: `license = "AGPL-3.0-or-later"` on both
   + per-crate `[[licenses.exceptions]]`. **AGPL stays OFF the global allow
   list** — a copyleft-viral third-party dep must still fail the gate.
2. **tiny-keccak CC0-1.0** (via ahash → arrow → duckdb → embed_cache) —
   FSF-free but not OSI-listed; allowed deliberately with a comment.
3. **Unmatched allowances dropped** — MPL-2.0 and Unicode-DFS-2016 were
   never encountered in this lock (the pyo3-ffi MPL note in
   THIRD_PARTY_LICENSES.md says "where applicable"; it isn't here).

Schema gotcha for the record: cargo-deny 0.20 `[advisories]` severity keys
(`vulnerability`/`unmaintained`/`unsound`/`yanked`) take SCOPE values
(`all`/`workspace`/`transitive`/`none`), not `warn`/`deny` — severity
routing is 0.20-defaults-only (vulnerabilities=error, warnings-class
report-only), which is already the house shape. `[graph] no-index` no
longer exists.

## 3. Design

Landed as a single-sitting slice (house corollary: single-sitting
follow-ups validated by targeted tests need no pre-filed proposal — this
document retro-files the record at operator request, "since it is a major
feature"):

- `Rust/deny.toml` — advisories v2 + bans + licenses + sources; comments
  pin the intent (extend allow list only on first-run failure, same
  doctrine as clippy `-D` arming).
- `make rust-deny` advisory leg — mirrors the cargo-audit leg's shape
  (SKIPs without the binary, `PATH="$HOME/.cargo/bin:$PATH"`).
- `tests/run_gate_report.py` — Step after `rust-test` in the advisory
  plan; Makefile `.PHONY` + help echo + advisory docstring updated.

Scope decision: **Rust/ only.** The desktop tree (479 crates) stays on
cargo-audit — its warnings family is the separate
`desktop_lock_advisory_remediation` proposal, and cargo-deny's DEFAULT
config marks unmaintained as ERROR (cargo-audit marks warn), so desktop
wiring needs a tuned deny.toml first (S4 there). Note: cargo-deny's
advisory-DB snapshot did not surface the glib/tokio unsound rows that
cargo-audit shows — cargo-audit remains the authoritative desktop
inventory.

## 4. Acceptance criteria & shakedown

1. `make rust-deny` → `advisories ok, bans ok, licenses ok, sources ok`
   (rc=0), verified 2026-11-01.
2. `make rust-clippy` (-D) / `make rust-test` (8) / `make rust-format` /
   `SCRIPT_KNN_STRICT=1 pytest tests/test_cosine_knn_backends.py` (9/9) —
   all green after the manifest edits.
3. `tests/test_lint_gates.py` 5 passed; proposal-lifecycle static check 21
   passed; `make md-lint` clean; `make search-fresh` converged rc=0 (one
   `--allow-stale` APPLY after the operator's mid-session commit ff853d34f
   tripped `worktree_fresh_trample_guard` — stamps lagged the ff).
4. Negative check: reverting a crate manifest license field must turn
   `cargo deny check licenses` red (the gate has teeth by construction —
   unlicensed = error).

## 5. Risks

- **Binary-only install** — cargo-deny lives in `~/.cargo/bin` per box;
  other machines SKIP the leg until installed. Accepted (cargo-audit
  precedent).
- **Advisory-DB drift vs cargo-audit** — the two tools fetch the same DB
  at different times; findings may momentarily disagree. cargo-audit stays
  authoritative for desktop; both run in advisory so disagreement is
  visible, never silent.

## 6. Non-goals

- No desktop/src-tauri wiring (separate proposal, S4 there).
- No qa promotion — advisory tier, like clippy/rust-test.
- No cargo-deny `bans` hardening (multiple-versions stays warn; first
  green decides, none fired).
- THIRD_PARTY_LICENSES.md rows stay (cargo-deny licenses gate covers the
  Rust lock; the human-readable inventory remains the doc of record).
