---
title: "Desktop lock advisory remediation — zbus/glib/tokio warnings family"
status: executed
filed: "2026-11-01"
executed: "2026-11-01"
completed_md: "466"
area: "desktop/src-tauri (Cargo.lock / Cargo.toml)"
---

# Desktop lock advisory remediation — zbus/glib/tokio warnings family

**Date:** 2026-11-01 · **Status:** EXECUTED (filed + executed same day) · **completed.md:** #466 ·
**Area:** `desktop/src-tauri` (Cargo.lock / Cargo.toml)

## 1. Motivation

The RustSec scan of `desktop/src-tauri/Cargo.lock` (479 crates) has carried a
warnings-class family since the advisory DB fetch on 2026-10-26. The Rust/
tooling arc (#446) deliberately left the desktop lock untouched — different
tree, heavier rebuild. Findings are now inventoried; none is a
vulnerability-class advisory, all are maintenance/unsoundness warnings that
will keep the advisory report red for the desktop scan indefinitely if
unaddressed.

## 2. Evidence (measured 2026-11-01, this box)

`cargo audit -n --stale` in `desktop/src-tauri` (cached advisory DB,
1395 advisories loaded):

| Crate | Version | Kind | RUSTSEC |
|---|---|---|---|
| proc-macro-error | 1.0.4 | unmaintained | RUSTSEC-2024-0370 |
| zbus_names | 4.3.4 | unmaintained | RUSTSEC-2026-0371 |
| zvariant | 5.15.0 | unmaintained | RUSTSEC-2026-0370 |
| zvariant_derive | 5.15.0 | unmaintained | RUSTSEC-2026-0372 |
| glib | 0.18.5 | unsound (VariantStrIter) | RUSTSEC-2024-0429 |
| tokio | 1.53.1 | unsound (AsyncFd io-safety) | RUSTSEC-2026-0386 |
| yoke-derive | 0.8.3 | yanked | — |

Cross-check with `cargo deny check advisories` (default config): the 4
unmaintained + 1 yanked reproduce; glib/tokio unsound did not surface in
deny's DB snapshot — treat cargo-audit as the authoritative inventory for
this proposal.

What the numbers mean: everything here rides the tauri dependency tree, not
first-party code. The zbus family (zbus_names/zvariant/zvariant_derive) is
pinned via the Linux tauri stack; glib 0.18.5 tracks the gtk-rs generation
that tauri's version selects; tokio is a semver-compatible bump candidate
(1.53.1 → latest 1.x) via plain `cargo update -p`.

## 3. Design

Slices, each independently landable:

1. **S1 — cheap semver-compatible updates.** `cargo update -p tokio -p
   yoke-derive` (plus `cargo update` for any other in-range pins the
   advisory DB flags). Rebuild + smoke the desktop app. Clears: tokio
   unsound, yanked yoke-derive. No manifest edits expected.
2. **S2 — zbus family assessment.** Determine whether the installed tauri
   version permits zbus 5.x (successor line for zbus_names/zvariant) via a
   `cargo update -p zbus --dry-run` / dependency-tree inspection. If tauri
   blocks it: document as accepted-risk in the audit trail instead of
   forcing a tauri major bump — unmaintained warnings on a desktop-only
   transitive stack are tolerable; a tauri major upgrade is a separate arc.
3. **S3 — proc-macro-error.** Unmaintained but build-time-only
   (proc-macro dependency of transitive crates); same decision shape as S2:
   bump if a semver-compatible successor is reachable, else accept + record.
4. **S4 — optional: extend `make rust-deny` to the desktop tree** with a
   desktop-specific `deny.toml` once the findings land at their floor —
   gives the same superset gate (advisories+bans+licenses+sources) the Rust/
   tree has. Only if S1–S3 leave a stable residual.

Alternative lost: "ignore the warnings in cargo-audit config" — rejected;
ignoring unmaintained/unsound warnings globally would also blind the Rust/
tree scan, which currently runs completely clean.

## 4. Acceptance criteria & shakedown

1. After S1: `cargo audit -n --stale` in `desktop/src-tauri` shows no
   unsound/yanked rows; `cargo build` succeeds.
2. After S2/S3: remaining findings (if any) are documented accepted-risk
   rows in the proposal addendum, not silently green.
3. Repeat any timing-sensitive verification twice (desktop build is
   incremental; first build after update is the slow one).

| Projected outcome | Today | After |
|---|---|---|
| vulnerability-class advisories | 0 | 0 |
| unsound/yanked findings | 3 | 0 |
| unmaintained findings | 4 | ≤4 (documented accepted-risk floor) |

## 5. Risks

- **tauri rebuild cost** — the desktop app build is the heavy step; S1-only
  scope still requires one full build to verify. Mitigation: single build
  after all S1 updates, not per-update.
- **zbus 5 force-bump breaks tauri Linux plugins** — mitigation: S2 is
  explicitly an assessment slice; forcing is out of scope.

## 6. Non-goals

- No tauri major version upgrade.
- No changes to `Rust/` (already clean).
- No promotion of desktop scans into qa (advisory remains the tier).

## 7. Execution addendum (2026-11-01, same day — EXECUTED, S1–S3; S4 not needed)

- **S1**: `cargo update -p tokio -p yoke-derive` → tokio 1.53.1→1.54.1,
  yoke-derive 0.8.3→0.8.4 (un-yanked). `cargo build` green (2m11s,
  incremental). Re-audit: **tokio unsound GONE, yanked row GONE** — 7 → 5
  findings.
- **S2 (zbus family)**: assessment ran deeper than planned — the lock
  ALREADY carries zbus 5.19.0 (via tauri-plugin-opener 2.6.0; tauri
  2.12.0). The unmaintained advisories are NOT stale-major warnings:
  RUSTSEC-2026-0370 has `patched = []` — zvariant is unmaintained at every
  5.x version; the fix is zbus 6.0 (zvariant merged as its `wire` module).
  tauri-plugin-opener does not target zbus 6 yet. **Verdict: ACCEPT-RISK**
  (desktop-only D-Bus surface) until tauri-plugin-opener ships a zbus-6
  release.
- **S3 (proc-macro-error)**: build-time-only proc-macro via
  glib-macros 0.18.5 ← gtk 0.18 ← tao/muda ← tauri 2.12. glib unsoundness
  (VariantStrIter, RUSTSEC-2024-0429) pinned by the same gtk 0.18
  generation. **Verdict: ACCEPT-RISK** until tauri moves off gtk-rs 0.18.
- Residual floor: 4 unmaintained + 1 unsound (glib) = the 5 rows now in
  every advisory report, each with a documented reason above.
- **S4 dropped**: no stable desktop deny.toml wiring — the residual is
  accepted-risk, not gate material.
