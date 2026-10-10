---
title: "Lockfile coverage — scan both lock files and admit them to review selection"
status: executed
filed: "2026-10-10"
executed: "2026-10-10"
completed_md: "379"
area: "helpers/misc/review_scan.py, .opencodereview/rule.json, desktop/src-vue/package-lock.json, tests/test_review_scan.py"
---

# Lockfile coverage — scan both lock files and admit them to review selection

**Date:** 2026-10-10 · **Status:** PROPOSED · **Area:**
`helpers/misc/review_scan.py` (new parser branch), `.opencodereview/rule.json`
(admit lockfiles), `desktop/src-vue/package-lock.json` (overrides pin),
`tests/test_review_scan.py` (teeth), `doc/procedures/ocr_review.md` (§1
selection rule).

## 1. Motivation

The dependency-vuln class `source-map-js@1.2.1` (CVE-2026-93749, CVSS 8.7,
fixed 1.2.2) was surfaced by `review-scan` on this range and sat for weeks
before anyone saw it — not because the scanner was broken, but because **no
reviewer was ever handed that file**: `.opencodereview/rule.json:27-28`
excludes `**/package-lock.json` and `**/*.lock` from the review roster, so
the file that flagged it is also the file the delegation review is forbidden
to touch. That is the selection gap.

The second gap is the reverse one. The repo ships two frontends that
contribute different lock files:

- `frontend/` — **bun** (`Makefile:553` `bun install --frozen-lockfile &&
  bun run build`), lock file `frontend/bun.lock`
- `desktop/src-vue/` — **npm** (`desktop/Makefile:10-11` `npm --prefix
  src-vue install`), lock file `desktop/src-vue/package-lock.json`

`helpers/misc/review_scan.py:_LOCK_PARSERS` parses only `uv.lock` and
`package-lock.json`. **`bun.lock` is not parsed at all**, so the bun-managed
app — the one with the repo-committed bundles under `static/` — has zero
dependency-vulnerability coverage. A range touching `frontend/bun.lock`
produces no dependency findings at all; the range touching the npm lock file
produces one, with no reviewing owner.

## 2. Root causes

1. `helpers/misc/review_scan.py:627-630` — `_LOCK_PARSERS` has no `bun.lock`
   entry (`("bun.lock", ("bun", _bun_lock_packages))`).
2. `.opencodereview/rule.json:27-28` — excludes `**/package-lock.json` and
   `**/*.lock`, which prunes every lock file from the selection roster.
3. `desktop/src-vue/package.json` has no `overrides` field, so the
   transitive pin `source-map-js@1.2.1` (pulled through
   `vue > @vue/server-renderer > @vue/compiler-ssr > @vue/compiler-dom >
   @vue/compiler-core > source-map-js`, i.e. production deps) has no local
   remedy and sits at the vulnerable version in `node_modules`.

## 3. Fix — lock files into the scanner

Add `bun.lock` to `_LOCK_PARSERS` and a corresponding `_bun_lock_packages`
parser mirroring the existing `package-lock.json` path. The `lockfiles`
group returned by `_group_files` and the lock-file iteration in the main
dispatch loop are already generic over that group, so no orchestration
change is needed: `osv` will start reading `frontend/bun.lock` too, and the
new parser must return `(name, version)` tuples in the same shape.

## 4. Fix — lock files into the review roster

Remove `**/package-lock.json` and `**/*.lock` from rule.json's `exclude`
**and add them to `include`**. Both halves are required: dropping the
exclude is not enough on its own. Under 1.12.11's additive semantics,
`include` is the ONLY way a path is admitted past the extension allow-list
gate — `.lock` is not an admitted extension, so `bun.lock` stayed dropped
with the exclude removed and the selection count did not move (measured:
185 → 185, lockfiles still `(excluded)`). Adding
`**/*.lock` + `**/package-lock.json` to `include` moved the roster to
**187/480** with both lockfiles listed reviewable. This mirrors how
`doc/**/*.md` is admitted: the same explicit-include mechanism, for the
same reason.

Lock files are generated, but they are where dependency vulnerabilities
reside, and this proposal's own motivation is that exclusion is what hid
the last real finding. `node_modules/**` stays excluded — the generated
tree remains out of review scope; only the lock file (the source of truth
for resolved versions) is admitted.

## 5. Fix — pin the vulnerable transitive dep

Add `overrides` to `desktop/src-vue/package.json`:

    "overrides": { "source-map-js": "^1.2.2" }

npm honours this on install; bun honours npm `overrides` too, so the
directive is manager-agnostic across the two frontends. Then regenerate
`desktop/src-vue/package-lock.json` and confirm the resolved tree pins
`source-map-js@1.2.2`. This is the dependency-side resolution of the same
CVE; the two coverage fixes in sections 3-4 make sure it is actually seen
next run.

## 6. Risks

- `bun.lock` parsing must not crash on `bun.lock`'s JSONC shape; parse the
  `packages` map the same way as the npm lock file (entry key
  `node_modules/<name>`, `version` field).
- Adding lock files to the roster increases roster churn on every lock
  file change, but only when a lock file itself is the thing that changed —
  otherwise they are not in `changed` and drop out.
- The `node_modules/**` exclude remains in place: the generated tree is
  still out of review scope, only the lock file (the source of truth) is
  admitted.

## 7. Verification

Run the scan before and after on the same range and assert:

- before: `osv` reports 1 finding (`source-map-js` in the npm lock file
  only); `shellcheck/sqlfluff/semgrep/bandit` as before;
- after: `osv` reports 0 findings, with a roster note that both lock files
  were parsed (`frontend/bun.lock`, `desktop/src-vue/package-lock.json`);
  the same 3/15/9/3 non-osv legs unchanged.

## 8. Execution log

Filed 2026-10-10. Operator accepted — executed the same day as a single
patch arc: overrides pin + lock file regeneration (slice 1), `_LOCK_PARSERS`
bun branch + teeth test (slice 2), rule.json admission (slice 3), then a
re-run of the scan leg to confirm both lock files parse and the finding
cleared.
