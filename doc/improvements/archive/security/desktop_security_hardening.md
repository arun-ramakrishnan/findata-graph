---
title: "Desktop security hardening — webview CSP, opener scope, claim correction"
status: executed
filed: "2026-10-01"
executed: "2026-10-01"
completed_md: "332"
area: "desktop/src-tauri/tauri.conf.json, desktop/src-tauri/capabilities/default.json, desktop/src-vue/src/components/NotePanel.vue, doc/improvements/archive/ui/findata_graph_desktop.md"
---

# Desktop security hardening — webview CSP, opener scope, claim correction

**Date:** 2026-10-01 · **Status:** EXECUTED (completed.md #332) · **Area:** the Tauri shell
(`desktop/src-tauri/`), the Vue frontend, and one arc-doc claim. Spawns
from the 2026-10-01 drift sweep (Addendum 7): zero confirmed findings,
three hardening notes on the desktop surface. The candidate gate holds
today — no affected principal, no injection path — so this is
defense-in-depth work, deliberately small.

## Motivation

The desktop viewer renders OCR-derived newsletter text in a webview that
holds IPC access to the whole vault surface. All current sinks are
verified safe (markdown-it `html:false`; `snippetHtml` filter exercised
against the partial-tag vector). The two notes below remove the
consequences of a *future* injection rather than any present one; the
third fixes a doc/code drift of the SEC-6 class.

## Slices

- **S1 — webview CSP (DESK-1).** Set a CSP in `tauri.conf.json`
  (`app.security.csp`) instead of `null`. Tauri injects nonces/hashes
  for its own assets; the app loads no remote origins, so the shape is
  `default-src 'self'; img-src 'self' data:; style-src 'self'
  'unsafe-inline'; script-src 'self'; connect-src 'self' ipc:
  http://ipc.localhost` (verify against Tauri v2's IPC guidance and the
  Vue dev server before landing; dev-mode relaxations documented inline).
  Acceptance: release build renders all tabs with zero console CSP
  violations under the smoke harness; `csp` non-null in the built
  config.
- **S2 — opener scope (DESK-2).** Replace the scope-free
  `opener:allow-open-url` in `capabilities/default.json` with a scoped
  permission limiting `openUrl` to `https?://` URLs, so the scheme gate
  becomes structural rather than frontend-regex-only
  (`NotePanel.vue:214` stays as the UX layer). Acceptance: smoke test
  asserts an injected `ftp://`/`file://` link click is rejected at the
  capability layer; the existing opener-fallback scenario stays green.
- **S3 — claim correction (DESK-2 residue).** The arc doc
  (`findata_graph_desktop.md` §S6, 2026-09-28 shakedown row) calls the
  capability a "least-privilege URL perm"; correct it to describe the
  real control chain (capability scope + frontend regex), per the SEC-6
  lesson that a comment asserting a nonexistent control is worse than
  none. Also record DESK-3 (`read_note` does not canonicalize symlinks —
  operator-local, accepted) where the vault path check is documented.
- **S4 — regression pins.** Extend the smoke harness with the two
  acceptance assertions above so S1/S2 cannot silently regress (house
  pattern: one test per change, riding the existing desktop smoke
  scenarios — no new runner).

## What this deliberately does NOT do

- No change to `read_note`'s prefix check (DESK-3 accepted;
  canonicalization would add a TOCTOU-prone resolve step for an
  operator-local-only threat).
- No auth model on the IPC surface (read-only commands, single-operator
  contract).
- No Cargo dependency changes.

## Verification

`cargo test -p findata-core` + `npm build` + smoke scenarios stay green;
release binary pixel-proven once (S1 touches rendering).
