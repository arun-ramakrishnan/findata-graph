---
title: "TS-app.py contract hardening - type-blind assertions (A), coverage (B), runtime validation (C), stale-bundle window (D)"
status: executed
filed: "2026-10-06"
executed: "2026-10-07"
completed_md: "361"
area: "tests/test_integration_ts_contract.py, frontend/src/core/api.ts, frontend/types/guards.ts (new, S3), app.py static cache headers, tests/conftest.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# TS-app.py contract hardening — type-blind assertions (A), coverage (B), runtime validation (C), stale-bundle window (D)

**Date:** 2026-10-06 · **Status:** PROPOSED ·
**Area:** `tests/test_integration_ts_contract.py`,
`frontend/src/core/api.ts`, `frontend/types/guards.ts` (new, S3),
`app.py` static cache headers.

## 1. Motivation

The Effect evaluation (`doc/local/evaluations/effect_assessment.md` §4)
flagged one real gap: **the frontend trusts `JSON.parse`**. Follow-up
analysis (operator, 2026-10-06) split it into four concerns to fix:

- **A — type-blind contract assertions.** The P6 suite
  (`tests/test_integration_ts_contract.py`) checks key *presence* only;
  declared TS types are thrown away at parse time, so `number` vs `"5"`
  drift passes.
- **B — coverage.** The frontend consumes 25 distinct API routes; only
  12 have success-shape contract tests. 13 call sites have no coverage
  of their response shape at all.
- **C — no runtime validation.** `fetchJson` casts
  `response.json() as T` (`frontend/src/core/api.ts:46`). Types are
  erased by esbuild; a wrong payload renders silently wrong output (NaN
  graph layouts, `"10" < "9"` sorts) with no exception to catch.
- **D — deploy window / trust boundaries.** Three halves: one measured
  out as **not a defect** (timing, §2 D1), one synthetic-data half
  (fixtures cannot certify live `research.db` shapes), one real
  stale-bundle window (static assets ship with no `Cache-Control`).

This proposal fixes all four. The Effect assessment stays a research
record; this file is the fix.

## 2. Evidence (measured 2026-10-06, this box)

| # | Fact | Measurement | Consequence |
|---|---|---|---|
| A1 | parser discards types | `_parse_interfaces` (`tests/test_integration_ts_contract.py:40-77`) regex-extracts `fname` + `?` optionality; `fields[fname] = optional` — the type annotation after `:` is never captured | `_assert_keys` cannot check kinds even in principle |
| A2 | assertions are subset-presence | `_assert_keys` (`:317-324`): every interface key appears in the response; extra server keys allowed (correct); type correspondence unchecked; ~20 literal-value asserts (`:357`) spot-check values only | value-type drift passes |
| A3 | contract source | `frontend/types/api.ts` — 633 lines, **60 interfaces**, hand-written mirrors of `app.py` jsonify blocks (header `:1-21`) | S1 must serve all 60, not just the 21-name "known" list |
| B1 | route surface | **34** `/api/` routes in `app.py` (38 `@app.route` total) | — |
| B2 | frontend consumption | **25 distinct routes**, 29 real `fetchJson`/`postJson` call sites (window-scan extraction over `frontend/src/**/*.ts`) | S2's required set = these 25 |
| B3 | current success-shape coverage | **12**: docs, docs/content, docs/search, scripts/search, entities, entity, events, search, sectors, stats, graph/cloud, graph/stats; error-shape only for graph/neighbors (`:607-621`) | 13 consumed routes uncovered: graph/bridges, co-mentions, edges-by-year, edition_companies, metrics, near-duplicates, neighbors (success), positions, refresh, semantic, shortest, similar, suggestions |
| B4 | fixture tiering for the 13 | `app.py` bodies: `positions`, `metrics` read `get_db_connection` (SQLite — contract fixture works); the other 11 go through `helpers.graph.*`, whose DuckDB layer is **already hermetic** in `tests/conftest.py:338` `seeded_graph_sqlite_db` (patches `helpers.graph.query.connect`/`rebuild` at `:386-400`; its comment explicitly names `/api/graph/refresh`) | no new fixture infrastructure needed |
| C1 | the cast | `frontend/src/core/api.ts:46` `return (await response.json()) as T;`; `postJson` delegates (`:51`) | runtime = trust; only runtime checks are the non-OK dance (`:39-45`) and the error-body probe (`:28-34`) — zero `typeof`/`Array.isArray` in the client |
| C2 | erasure | esbuild build (`frontend/package.json` `build` → committed `static/findata.bundle.js`) | annotations do not exist at runtime |
| D1 | timing half — **not a defect** | qa's pytest leg is `-m "not live"` (`tests/run_gate_report.py:200-211`) and `make test` is `-m "not live"` (`Makefile:154`); `pytest --collect-only` on this file → **35 tests collected** under that filter | the contract suite already runs in the *blocking* qa leg; no gate restructuring is wanted |
| D2 | synthetic-data half | fixture seeds 5 entities / 5 edges (`:212-291`) on a tmp SQLite DB | certifies code diffs, not live-data shapes (nulls, empty corpora, real analytics rows) |
| D3 | stale-bundle half | `Flask.SEND_FILE_MAX_AGE_DEFAULT = None` (measured; no override in `app.py`); `_graph_cache_headers` (`app.py:1818-1842`) sets `Cache-Control: no-cache` for `/api/graph/*` only → `static/*.bundle.js` gets no `Cache-Control` → browser heuristic freshness may serve yesterday's bundle against today's API | real, narrow, one-header fix (S4) |

## 3. Design

- **S1 — type-aware assertions (fix A).** Extend `_parse_interfaces`
  to retain the declared type text per field; add `_assert_types(data,
  iface)` walking a response value against the interface: primitives,
  `T | null`/unions, `T[]`/`Array<T>`, nested inline objects (the
  depth-tracking brace matcher exists), interface references resolved
  through the existing `extends` inliner with a cycle guard,
  `unknown`/`any` skipped. Refactor `_assert_keys` call sites into one
  `_assert_contract` = keys + types. Parser-fidelity test extended: all
  **60** interfaces keep type info. Python-only — the suite's own
  doctrine (`:16-19`: "deliberately Python-only … to keep the QA gate
  Python-only").
- **S2 — coverage to 25/25 (fix B).** Add contract classes for the 13
  uncovered consumed routes, tiered per B4: SQLite fixture for
  `positions`/`metrics` (seed `graph_analytics` rows), hermetic
  `seeded_graph_sqlite_db` for the 11 `helpers.graph.*` routes
  (bridges, co-mentions, edges-by-year, edition_companies,
  near-duplicates, refresh, semantic, shortest, similar, suggestions,
  neighbors-success). Every one gets success-shape + type assertions;
  4xx/5xx shapes kept where the endpoint defines them.
- **S3 — runtime validation at the boundary (fix C).** One spike,
  measured in this repo's real build (`cd frontend && bun run build`;
  bytes + gzip of `static/findata.bundle.js` before/after):
  1. **(c) generated guards — recommended.** Emit
     `frontend/types/guards.ts` from the `api.ts` interfaces using the
     parser S1 just built — one source of truth, **zero new
     dependencies**, with a regeneration check in qa (test regenerates
     in-memory and diffs the committed file, so guards cannot drift).
  2. (a) valibot, (b) zod — the measured alternatives; each pays a
     runtime dependency into a *committed* bundle.

  Then wire the chosen validator into `fetchJson` (`api.ts:46`): on
  mismatch throw a typed `ShapeError(endpoint, path, expected, got)`
  before returning — views' existing catch blocks surface it, turning
  silent-garbage rendering into a loud, localised failure. Tests via
  `bun test` (bun at `~/.bun/bin/bun`; runner built in, no new deps).
- **S4 — close the deploy window, record the non-defect (fix D).**
  (i) `after_request` adding `Cache-Control: no-cache` for static
  bundles, mirroring the `_graph_cache_headers` policy ("the browser
  MUST revalidate"), plus a test asserting the header on
  `/static/findata.bundle.js`; (ii) live-data half: S3's validator is
  the runtime answer, residual recorded (fixtures stay synthetic by
  design — `live`-marker doctrine); (iii) timing half: D1 recorded as a
  measured non-defect, no gate change.
- **S5 — hygiene.** `make frontend-check` (tsc + prettier) after S3,
  targeted `pytest -m "not live" tests/test_integration_ts_contract.py`,
  ruff on touched Python, `make md-lint` on this file,
  `make search-fresh`, `completed.md` entry on execution.

Alternatives rejected: **status quo** (fails A/B/C by construction);
**OpenAPI codegen** — `frontend/types/api.ts:6` records "there is no
OpenAPI/swagger contract to generate from", and introducing one for 34
hand-written Flask routes is a bigger project than the fix;
**zod/valibot without the spike** (unmeasured cost in a committed
artifact); **`effect/Schema`** — rejected in `effect_assessment.md` §4
(drags the Effect runtime in for a validator); **hiding the contract
suite behind a stricter marker** — D1 shows it already runs in qa;
excluding it would be the wrong direction.

## 3a. S3 spike results (measured 2026-10-06, this box)

All three arms wired identically in a scratch copy of `frontend/`
(same `ROUTE_SHAPES` prefix map over all 25 consumed routes, full
59-interface coverage — 261 fields — in every arm; tsc `--noEmit` rc=0
per arm; esbuild ~60–170 ms). valibot/zod schemas were machine-emitted
from the same S1 parser (`tests/api_contract.py`) so all arms validate
identical shapes. Baseline bundle reproduces the committed artifact
byte-for-byte (608,676 / 119,861 gz).

| arm (findata.bundle.js) | raw | Δ raw | gzip −9 | Δ gz |
|---|---|---|---|---|
| baseline (no validation) | 608,676 | — | 119,861 | — |
| **(c) generated guards** | 692,793 | +84,117 (+13.8%) | 126,858 | +6,997 (+5.8%) |
| **(a) valibot 1.5.0** | 638,741 | +30,065 (+4.9%) | 125,106 | +5,245 (+4.4%) |
| **(b) zod 4.6.5** | 833,075 | +224,399 (+36.9%) | 161,714 | +41,853 (+34.9%) |

entity.bundle.js (same stack rides along in every arm via `core/api.ts`):
baseline 91,056 / 21,583 gz → guards 175,086 / 28,400 (+92% raw);
valibot 121,057 / 26,683 (+33%); zod 315,259 / 63,346 (+246%).

**Read:** zod is dominated on both axes — out unless its ecosystem is
wanted for other reasons. valibot is the smallest artifact; generated
guards are close on the wire (gzip) and carry the no-drift regeneration
property, but see findings 1–3 below — the raw-bytes gap is mostly
fixable generator work, not an inherent cost.

### Spike findings (all three pre-adoption work items for arm (c))

1. **Generator union bug (real defect, found by the spike; radius
   widened on root-cause read).** For multi-member unions `_first`
   (`gen_api_guards.py:191-196`) joins per-member *reject-predicates*
   with `||` — accept only if **all** members accept — where the union
   needs reject-only-if-**all**-reject. Measured: the committed
   `guards.ts` rejects every valid value of EVERY multi-member union in
   the corpus — `acquired[].year` (`string | number`),
   `DocSearchResponse.mode: "hybrid"` (valid → rejected),
   `GraphRefreshResponse.status: "ok"` (valid → rejected),
   `ScriptSearchHit.kind: "script"` (valid → rejected). Root cause: the
   generator's docstring claims to mirror `assert_type` branch for
   branch (`:89-90`), and the Python twin's union branch is control
   flow — try members, `return` on first success, raise after all fail
   (`tests/api_contract.py:310-320`); the TS translation flattened that
   into a logical join and flipped the short-circuit. The Python-side
   suites are green because `assert_type` is correct — this is
   browser-side only, but wiring arm (c) as-is would break docs search,
   graph refresh, script search and the acquired timeline on first
   load. Fix = sequential accept-any emission (bind each member's
   error, return the label error only after the last member also
   fails) + a behavior battery over the emitted TS as a regression
   test — before S3 lands. (Single literals and `T | null` paths are
   emitted correctly; the `expected one of` branch at `:167-171` is
   nearly dead — spaced literal unions split at `:96` before reaching
   it.)
2. **Guards do not enforce required-key presence.** Every field check
   is wrapped `if (o.x !== undefined)` (presence is the Python suite's
   job at test time). valibot/zod reject missing required keys at
   runtime (parity battery: `CN_MISSING_REQUIRED` guards=A, valibot=zod
   =R). Either accepted as designed or add a required-keys pass to the
   generator.
3. **The registry retains all 59 guards in BOTH bundles**
   (side-effectful `registry.set` calls defeat tree-shaking), which is
   why entity.bundle.js nearly doubles under arm (c). A
   reachability-pruned emission (only the ~25 route-consumed roots plus
   nested refs) would shrink the guard cost substantially.

Parity battery (8 payloads × 3 arms, `CompanyNeighbors`): valibot ≡
zod on every case, including the literal-union, nullable, nested-inline
and extra-key cases; guards differ only on findings 1–2. Type aliases
(`NoteFrontmatter` etc.) are skipped by the generated guards but
resolved by the valibot/zod emitters (near-vacuous
`Record<string, unknown>` check) — negligible either way.

Effect comparison, for the record: `effect_assessment.md` §3–§6
rejected `effect/Schema` because it imports the Effect runtime and its
`Effect<A, E, R>` paradigm into a committed IIFE bundle to gain a
validator (§3.3–§3.4), and pre-committed this exact spike as the
gate-(b) follow-up ("run a separate zod/valibot-class eval first"). The
What the spike adds: the validator-only bundle cost is now *measured*
(+4.4% wire for valibot vs a native zero-dep emitter at +5.8%), so the
Effect rejection stands on numbers, not just aesthetics.

Decision (per AC3): **open — awaiting operator review of this table.**
valibot requires accepting one npm runtime dependency; arm (c) requires
findings 1–3 fixed first and is the only zero-dep option.

## 3b. Spike v2 — findings fixed, trial re-run (2026-10-06, this box)

The v1 table above measured the committed guards as-is: buggy (finding
1), presence-blind (finding 2), and unshaken (finding 3) — a
measurement of debt, not of the design. Per operator direction the
three findings were fixed first and the trial re-run:

- **F1 (union bug) — fixed.** `_first` now emits a sequential
  accept-any chain (bind each member's reject-predicate, accept on the
  first null, label-error only after the last member also fails),
  mirroring `assert_type`'s try-members loop. Verified against every
  multi-member union in the corpus: `acquired[].year` string AND number
  accepted, `mode`/`status`/`kind` literal members all accepted.
- **F2 (required-key presence) — fixed.** The emitter now emits a
  presence check per required field (`"k" in o`, before the type
  checks, nested inline objects included); optional fields pass when
  absent — mirroring `assert_keys(required_only=True)`.
- **F3 (tree-shaking) — fixed, with a design change.** Shakeable
  exports alone were NOT enough (measured: a shared prefix→guard map in
  `core/api.ts` still pulls the 25-route closure into every bundle —
  entity 177,627, worse than v1). The wiring that delivers
  route-reachable guards is **call-site guards**: each view passes its
  guard explicitly (`fetchJson<SectorsResponse>(url, isSectorsResponse)`
  — it already names the type), and `fetchJson` validates or passes
  through. Each bundle then shakes to exactly its own guards.
- **Regression class added.** `frontend/tests/guards.test.ts` (14
  cases) executes the emitted TS itself — union acceptance per member,
  presence, nested objects, extra-key tolerance — the missing test
  class that let F1 ship. `make frontend-check` now runs tsc +
  prettier + `bun test` + `gen_api_guards.py --check`, so the guards
  can neither drift nor behave-wrong without a loud failure.
  guards.ts regenerated (59 exported guards; the side-effectful
  registry and `assertShape` are gone — guards are plain named
  functions). Contract suite 71/71; tsc clean per arm.

| arm (call-site wiring, post-fix) | findata raw | Δ raw | gz | Δ gz | entity raw | Δ raw |
|---|---|---|---|---|---|---|
| baseline | 608,676 | — | 119,861 | — | 91,056 | — |
| **(c) generated guards** | 690,242 | +13.4% | 126,845 | +5.8% | 107,158 | +17.7% |
| **(a) valibot 1.5.0** | 644,933 | +6.0% | 125,440 | +4.7% | 120,717 | +32.6% |
| **(b) zod 4.6.5** | 839,809 | +38.0% | 162,035 | +35.2% | 315,001 | +246% |

Parity v2: guards ≡ valibot ≡ zod on all 8 battery cases — including
missing-required, which guards now reject like the libraries. The
guards arm's entity cost collapsed from +92% (v1) to +17.7%: its floor
is zero runtime, just the few hand-rolled guard functions a bundle
uses, where valibot's floor is its runtime (~24KB) before the first
schema. valibot keeps the smallest findata bundle (+6.0% raw / +4.7%
wire vs +13.4% / +5.8%) because hand-rolled per-guard code is more
verbose than v.safeParse calls once the runtime is amortized across 22
route-reachable interfaces. zod remains dominated.

**Read:** the guards arm is now semantically equal to the libraries
(parity 8/8, presence included), regression-gated against generator
drift, and the cheapest zero-dependency option per bundle on the
entity side; valibot stays ~1.1% cheaper on findata's wire bytes at
the cost of one accepted npm dependency. The choice between them is
now purely dependency-policy, not correctness or measurement.

## 3c. Decision + execution (2026-10-06 evening)

**Decision: valibot arm — the dependency is explicitly accepted**
(operator, 2026-10-06, after the §3a/§3b tables and the
capability comparison). Rationale on record: equal semantics (proven
8/8), upstream-owned checking semantics instead of a hand-rolled
validator compiler (the layer the v1 union bug lived in), refinement
headroom, compile-time `GenericSchema<Iface>` correspondence, Standard
Schema ecosystem optionality — at +4.7% wire on findata (cheaper than
guards' +5.8%) and +32.6% raw on entity (its ~24KB runtime floor
looms, but the views consume few schemas). zod: not adopted —
dominated on every measured axis; its popularity premium (tRPC/forms/
AI-tool adapters) targets surfaces this repo's thin read-only frontend
doesn't have, and the Standard Schema spec (zod+valibot+ArkType)
keeps that door open. Effect: stays rejected per §3/§6 of
`effect_assessment.md`.

Landed (S3 execution):

- `frontend/package.json`: `valibot ^1.5.0` (the one runtime dep);
  `bun.lock` updated.
- `helpers/misc/gen_api_guards.py` rewritten as the valibot emitter:
  same parser (`tests/api_contract.py`), emits
  `frontend/types/schemas_valibot.ts` (one schema per interface,
  dependency order, unknown type constructs RAISE — never a loose
  schema; `export type` aliases resolved) and `frontend/types/guards.ts`
  (the `is<Interface>` safeParse wrappers, 2,758 lines of hand-rolled
  checks → 489 lines). `--check` verifies both; wired into
  `make frontend-check` beside `bun test`.
- `frontend/src/core/api.ts`: `fetchJson<T>(url, guard?, init?)` /
  `postJson<T>(url, guard?)` — call-site guards, `ShapeError(endpoint,
  detail)` on mismatch, guards re-exported for views.
- All view call sites pass their guard (`fetchJson<SectorsResponse>(
  url, isSectorsResponse)`); `neighbors/` uses a two-member union
  helper (`isNeighborsUnion`) because the alias `NeighborsBundle` maps
  to CompanyNeighbors-or-SectorNeighbors by entity kind.
- `frontend/tests/guards.test.ts`: 14-case behaviour battery, now
  asserting the valibot-backed guards (path-based assertions).
- Bundles rebuilt and committed: findata 647,257 / gz 125,584
  (+6.3% / +4.8% vs baseline), entity 122,126 / gz 26,565 (+34.1% /
  +23.1%) — matches the §3b valibot row within header-comment noise.
- Validation: tsc clean, prettier clean, battery 14/14, contract
  suite 73/73 (71 + the two S4 tests below), ruff clean on touched
  Python.

S4 also landed: `app.py` `_static_bundle_cache_headers` (after_request,
`Cache-Control: no-cache` for GET `/static/*.bundle.js` 200s) + two
contract tests. **D3 correction found while testing:** Flask already
sends `Cache-Control: no-cache` on ALL static responses because
`SEND_FILE_MAX_AGE_DEFAULT = None` — the "static ships with no
Cache-Control" premise of D3 does not match live Flask behaviour; where
that reading originated (desktop/Tauri asset loading vs an older probe)
is not pinned. The handler keeps the policy explicit and test-gated
rather
than incidental to a framework default, and the scope test pins that
it does not leak onto API responses.

S5 residue: full gates (qa + integration + perf + advisory) on the
operator's go; then `completed.md` entry + archival per the checklist
in `proposals/README.md`.

## 4. Acceptance criteria & shakedown

1. `_parse_interfaces` retains type info for **all 60** interfaces;
   every existing contract assertion runs keys **and** types; the 35
   current tests stay green.
2. Contract coverage of frontend-consumed routes: **12 → 25** — each of
   B3's 13 gets success-shape + type assertions.
3. Runtime validator live in `fetchJson` with `bun test` coverage
   (mismatch → `ShapeError` naming endpoint/field/expected/got); the
   S3 spike's bundle-delta numbers recorded in this file; chosen option
   has zero new npm dependencies or an explicitly accepted one.
4. `/static/findata.bundle.js` responses carry
   `Cache-Control: no-cache` (test asserts the header).
5. `make frontend-check`, `make lint`, targeted contract pytest, and
   `make md-lint` green; `check_proposal_lifecycle()` fatal `[]`;
   `make search-fresh` rc=0.
6. **Eval gate: N/A** — no query-visible semantics change: payload
   *contents* are untouched; only client-side failure detection, test
   coverage, and a cache header policy change. Recorded, not skipped.

## 5. Non-goals & rollback

Non-goals: the 9 backend `/api/*` routes no TS client calls (resolve,
peers, country, exposure, sector, hyper/*, analytics — B1 vs B2), any
`api.ts` interface redesign, `desktop/src-vue` (checkJs,
`strict: false` — its own decision), introducing vitest, changing the
committed-bundle/Node-free deploy policy, backend route changes.

Rollback: S1/S2 are test files (delete to revert); S3 = `api.ts` +
guards file revert; S4 = one `after_request` revert. Nothing here
touches data or API payloads, so no snapshot.
