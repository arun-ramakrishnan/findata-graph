---
title: "Desktop IPC shape guards - valibot schemas over the Tauri invoke surface (A), validated api.js wrappers (B), fixture-driven contract gate (C), build-hygiene record (D)"
status: executed
filed: "2026-10-06"
executed: "2026-10-07"
completed_md: "362"
area: "desktop/src-vue/src/lib/api.js, desktop/src-vue/src/lib/schemas.js (new), desktop/src-vue/test/, desktop/Makefile; desktop/src-tauri/core/src/lib.rs read-only as the contract reference"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Desktop IPC shape guards — valibot schemas over the Tauri invoke surface

**Date:** 2026-10-06 · **Status:** PROPOSED ·
**Area:** `desktop/src-vue/src/lib/api.js`, `schemas.js` (new),
`desktop/src-vue/test/`, `desktop/Makefile`; `core/src/lib.rs`
read-only as the contract reference.

## 1. Motivation

`ts_contract_hardening` (§3c, executed 2026-10-06) hardened the HTTP
SPA's fetch boundary and recorded `desktop/src-vue` as a non-goal
("checkJs, `strict: false` — its own decision"). The desktop's data
path is different — no `fetch()` at all — but it has the same class of
unvalidated-trust gap one layer over: **the Vue side assumes the shapes
of 14 Tauri `invoke()` results and nothing checks that assumption.**

The Rust side is strongly typed (`findata-core`'s 21 `pub struct`s),
but that types the *producer*. On the consumer side, `api.js` calls
`invoke()` and returns the raw promise; `store.js` (333 lines) consumes
every result as `any` under `checkJs` with `strict: false`. A Rust
struct rename or a serde field reshuffle surfaces in the UI as a quiet
`undefined` — the same silent-garbage failure mode concern (C) fixed
for the SPA, one bridge over.

## 2. Evidence (measured 2026-10-06, this tree)

| # | Fact | Measurement | Consequence |
|---|---|---|---|
| A1 | IPC surface | **14** `#[tauri::command]` fns in `src-tauri/src/main.rs`; `lib/api.js` exposes exactly those 14 wrappers; **zero** `fetch()` in `src-vue/src` (grep) | the HTTP contract suite does not cover this surface; it has no contract at all |
| A2 | the contract lives Rust-side | 21 `pub struct`s in `src-tauri/core/src/lib.rs` (1,197 lines: `Stats`, `Cloud`, `Ego`, `SearchResults`, `NoteContent`, `EntityMetrics`, `RankEntry`, …) | producer-typed only; serde serialises whatever the structs say |
| A3 | consumer is untyped | `api.js` header admits "typed-ish"; no JSDoc returns; `store.js` consumes all 14 results as `any` (`checkJs: true, strict: false`, jsconfig S8) | a field rename in `lib.rs` reaches the DOM as `undefined` — no build or runtime error |
| A4 | one shape-guessing parse | `MetricsPanel.vue:99` `JSON.parse(raw)` in try/catch, guessing scalar-vs-label-vs-raw by probing keys | defensive, but silently degrades to `raw` display on drift |
| C1 | a fixture pipeline already exists | `desktop/src-vue/test/`: `make_fixtures.py` (real `research.db` payloads) → `fixtures.js`; harness stubs the Tauri IPC with them | the contract gate's anchor: real shapes, regenerable, already wired into the smoke harness |
| D1 | no runtime cache window | `desktop/.gitignore:3` `src-vue/dist/`; `tauri.conf.json` `frontendDist: "../src-vue/dist"` — the UI is compiled into the binary at desktop build time | the stale-bundle window (ts_contract_hardening D) cannot occur here; the residual risk is building the desktop app from a stale `dist` — build hygiene, not runtime |
| D2 | no validation lib | `src-vue/package.json` has no zod/valibot/ajv | the gap is real, and cheap to close with the library the frontend just adopted |

## 3. Design

- **S1 — schemas for the IPC contract (fix A).**
  `desktop/src-vue/src/lib/schemas.js`: hand-written valibot schemas
  mirroring the 21 `findata-core` structs (one source of truth per
  side; the Rust struct is normative, the schema is its JS mirror —
  same doctrine as `api.ts` ↔ Flask jsonify). JS, not TS: src-vue is a
  JS codebase under checkJs; valibot's runtime checks carry the value,
  and JSDoc `@type {import('valibot').GenericSchema}` keeps checkJs
  useful. Depends on `valibot` (already accepted repo-wide in
  ts_contract_hardening §3c; Standard Schema keeps one validation
  idiom across both frontends).
- **S2 — validated wrappers (fix B).** `api.js` routes every `invoke`
  through one helper: `ipc(schema, cmd, args)` — parse result, return
  it, or throw `IpcShapeError(cmd, path, message)`; the 14 wrappers
  become one-liners over it. `store.js`'s existing try/catch surfaces
  the failure loudly instead of rendering `undefined`-soup.
  `MetricsPanel.vue`'s prober stays (it degrades display gracefully)
  but gains a schema-validated input.
- **S3 — fixture-driven contract gate (fix C).** A test
  (`desktop/src-vue/test/`) asserts every payload in
  `test/fixtures.js` against its schema — so `make fixtures`
  regeneration becomes the drift alarm: change a Rust struct without
  updating the schema and the next fixture regen fails the test. Wired
  into the desktop Makefile's check lane alongside the existing
  harness. Rust-side `core/tests/live.rs` stays the producer's own
  net; this is the consumer-side twin.
- **S4 — record D1, hygiene.** The stale-bundle concern is recorded as
  structurally N/A (compiled-in dist); the build-hygiene note (desktop
  release builds should rebuild `src-vue/dist` first) goes in
  `desktop/README.md`. Hygiene: `make fixtures` + the new test green,
  checkJs clean, desktop Makefile check target green.

Alternatives rejected: **do nothing** (A3's silent-undefined is the
exact failure class this arc series exists to kill); **TypeScript
migration of src-vue** (reverses S8's own recorded decision; the
runtime guards deliver the value without it); **ts-rs/specta
Rust→TS codegen** (bigger machinery than 21 structs need; revisit if
the struct count grows or drift bites twice); **zod** (dominated in
the ts_contract_hardening trial; one validation idiom across
frontends is worth more than zod's adapter mass here).

## 4. Acceptance criteria & shakedown

1. Schemas exist for all **21** `findata-core` structs and every one of
   the **14** `api.js` wrappers validates its result before returning.
2. A Rust-side field rename (mutation test: rename in `lib.rs`,
   regenerate fixtures) fails the S3 test with a message naming the
   command and field.
3. `MetricsPanel.vue` parses only schema-valid input.
4. Desktop check lane green: fixtures regenerate, S3 test passes,
   checkJs clean. Frontend (`make frontend-check`) untouched and green.
5. `make md-lint` green on this file; `make search-fresh` rc=0;
   ruff clean on touched Python (`make_fixtures.py` if touched).
6. **Eval gate: N/A** — no query-visible semantics change: desktop UI
   validation only, no rosters/crosswalks/hierarchies/extractor rules,
   no API payload changes. Recorded, not skipped.

## 5. Non-goals & rollback

Non-goals: TypeScript migration or `strict: true` for src-vue (S8's
recorded decision, revisited only on its own merits); any change to
`findata-core` structs or the Tauri commands; the HTTP SPA
(covered by ts_contract_hardening); a Rust→TS codegen pipeline;
desktop release/CI plumbing beyond the README build-hygiene note.

Rollback: S1/S2 are one new file plus wrapper bodies (delete to
revert); S3 is one test file; nothing touches data, payloads, or the
Rust crate — no snapshot.

## 6. Execution record (2026-10-07)

**S1** — `desktop/src-vue/src/lib/schemas.js`: hand-written valibot
schemas for the wire contract. Correction to §2/A2 in-record: `grep
'pub struct'` finds **21**, but the 21st is `Db` — the connection
handle, never serialized — so the wire contract is **20 structs**, all
mirrored here, plus one inner union `AnalyticsValueSchema` (the
{value: f64} scalar vs {community|componentId|block: int} label shapes
inside an analytics `value` string). `Option<T>` → `v.nullable`,
`Vec<T>` → `v.array`, `(String, i64)` → `v.tuple`,
`serde_json::Value` → `v.unknown`; integral Rust fields pin
`v.integer()`; objects are non-strict (extra keys tolerated).
**S2** — `api.js`: one `ipc(schema, cmd, args)` helper (safeParse +
`v.getDotPath`), `IpcShapeError(cmd, path, message)`, all 14 wrappers
one-liners over it (names/args unchanged — `store.js` untouched); its
try/catch now surfaces drift as a loud status-line error.
`MetricsPanel.vue`'s prober stays but only interprets
`AnalyticsValueSchema`-valid input (§4.3). **S3** —
`test/contract.test.js` (node:test, 14 tests): every `fixtures.js`
payload asserted against its command's schema + a completeness check
that all 14 commands have a fixture contract; wired as
`make contract` / `make check` (typecheck + contract);
`contract.test.js` excluded from checkJs (node-only surface).
`valibot ^1.5.0` added to `src-vue` dependencies.

**Live drift found during survey (the arc's own target class):**
`make_fixtures.py::_stats` emitted `entity_types`/`edge_types` as
`{type, count}` objects while the Rust `Vec<(String, i64)>` serializes
as `[type, count]` pairs — and `App.vue:254` destructures them as
tuples, so the smoke fixtures were the stale side. Fixed the mirror to
pairs and regenerated `fixtures.js`; the S3 gate now pins it. (The
proposal's fixture-freshness README section was right that regen is
manual — this is what happens when it lapses.)

**AC2 shakedown (executed):** renamed `SearchHit.snippet` → `excerpt`
in `lib.rs` + the `make_fixtures.py` mirror, regenerated — contract
test failed exactly as specified: `search_notes fixture drifted:
results.0.snippet: Invalid key: Expected "snippet" but received
undefined` (both SearchResults consumers flagged). Reverted, regen,
14/14 green, zero residue.

**S4 / validation:** build-hygiene note recorded in
`desktop/README.md` (Ship section; D1 confirmed N/A at runtime — the
dist is compiled in; the residual risk is a bare `cargo build
--release` skipping the frontend build, documented). `make check`
green (vue-tsc checkJs clean, 14/14 contract), `make fixtures` green,
ruff clean on `make_fixtures.py`, `make frontend-check` untouched and
green (§4.4), md-lint + search-fresh green (§4.5). §4.6 eval gate: N/A
recorded — no query-visible semantics change.
