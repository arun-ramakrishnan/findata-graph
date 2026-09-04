---
title: Archify diagram pipeline — evidence-marked HTML diagrams for key doc subjects, IR committed beside owning docs
status: executed
filed: '2026-09-05'
executed: '2026-09-05'
completed_md: '207'
area: doc/
---

# Archify diagram pipeline — evidence-marked HTML diagrams for key doc subjects

**Status:** EXECUTED 2026-09-05 (filed + executed same day; S1-S7 landed,
S8 landed with viewport caveat — completed.md #207) · **Trigger:** user go after trial 1
(`doc/local/archify_eval.txt` — storage-topology system-overview passed the
full 9-check showcase gate, browser evidence, and perceptual review; user
verdict: "looks fantastic"). · **Skill:** `archify` (installed
`~/.agents/skills/archify`, symlinked per `doc/local/skills_symlink.md`;
eval + trial record in `doc/local/archify_eval.txt`).

## Scope / Motivation

The corpus is text-first (doc_query, rg, diffs) and has zero diagram
convention — but three subjects keep proving genuinely multi-dimensional:
the PDF→OKF ingest pipeline, the 14-step maint-full chain with its
placement carve-out, and the storage/backup topology. Trial 1
demonstrated the tool can carry the third one faithfully with
acceptable cost (~6 validate rounds, minutes). This proposal makes
that a **standing, bounded pipeline** so diagrams stay few, truthful,
and synced — never wall art:

1. Prose stays the sole source of truth. A diagram is an *accompaniment*
   to a doc section that owns the facts, never a replacement.
2. Every committed diagram carries **repository evidence** (`sources` on
   components: verified file:line anchors; `meta.repository` pins the
   commit) so "as it actually is" is checkable, matching
   `doc/design/architecture.md`'s charter.
3. The typed JSON **IR is committed** (diffable, greppable, reviewable);
   the HTML is a **regenerable artifact** also committed (self-contained,
   ~0.7 MB each; the repo already carries MB-scale parquet snapshots).

## The pipeline (input → HTML, as executed in trial 1)

1. **Subject + question** — pick the doc section that owns the facts and
   the one question the diagram answers; pick the type via the guide's
   recipe table (<https://tt-a1i.github.io/archify/guide.html> — 11
   bounded recipes with useWhen/avoidWhen + copy-ready prompts;
   ambiguous → `node bin/archify.mjs guide "<scenario>"`).
2. **Facts + evidence** — query, don't scan: fix the ≤12 primary nodes
   and the one main path. Locate facts with the house search
   interfaces — `doc_query`/`script_query` for intent, procedures and
   the script/test/make inventory, **codebase-memory-mcp** for
   symbol→file:line anchors and callers (the designated tool for
   structure; `rg` is the fallback) — then verify each `sources`
   anchor against the pin target (step 9). Record only verified
   file:line.
3. **Author the IR** — read `schemas/<type>.schema.json` +
   `schemas/common.schema.json` + ONE matching `examples/` file; write
   the candidate JSON (grid layout, automatic routes, semantic labels,
   `quality_profile: "showcase"`); facts that would need long back-edges
   go to cards instead.
4. **Validate** — `node bin/archify.mjs validate <type> <candidate>
   --quality showcase --repo-root <repo> --json`; repair loop (apply the
   receipt's suggested fixes; one geometry control per diagnosed
   subject) until all 9 artifact checks pass with 0 errors/0 warnings.
5. **Deliver** — `node bin/archify.mjs deliver <type> <candidate>
   <output.html> --quality showcase --repo-root <repo> --json`: freezes
   the exact spec bytes (SHA-256 receipt), atomically commits the HTML.
   Non-zero exit can never be reported as success.
6. **Browser evidence** — `node bin/archify.mjs visual-check
   <output.html> --json` (overflow + rendering at 1440×900 / 1600×1000
   / 1920×1080 / 2048×1320, light+dark). Fix overflow by compacting
   layout gaps, never by clipping.
7. **Perceptual review** — human or image-capable reviewer reads the
   screenshots; the machine checks explicitly do not certify polish.
8. **Land** — IR + HTML committed beside (or linked from) the owning
   doc section with a pointer line; re-render whenever an arc changes
   the depicted chain (the pointer line says so explicitly).
9. **Pin discipline (evidence links) — always pin origin/main.** SRC
   chips deep-link to `github.com/<repo>/blob/<revision>/<path>#L<n>`;
   origin/main is the public tree every reader can see, so it is the
   only pin target (decided 2026-09-05: local-HEAD pins 404 until
   push and die again on stack rewrites). Resolve every anchor
   against origin/main content (`git show origin/main:<path>`), take
   the revision from `git ls-remote origin main`, and re-validate.
   Boundary condition: an anchor that does not exist at origin/main
   (code still sitting in unpushed patches) means that source is
   dropped or the diagram waits for the push — never pin worktree
   line numbers against a tree that lacks them. Note: GitHub returns
   404 for no-access on a private repo, so an authenticated browser
   session is still required.

## Artifacts & layout

- Diagram pairs live under `doc/design/diagrams/<slug>.json` + `.html`
  (+ visual-check PNGs are transient, not committed).
- Each owning doc section gets one pointer line (diagrams are opaque to
  doc_query — the .md pointer line is the discoverability path, and
  `make search-fresh APPLY=1` covers the pointer).
- No Makefile wiring, no qa/advisory gates: validation is an
  authoring-time discipline, and a stale diagram is a doc bug, not a
  gate failure.

## Slices

- **S1 — Land trial 1.** Move `doc/local/archify_trial/system_overview.*`
  → `doc/design/diagrams/`; add the pointer line to
  `doc/design/architecture.md` §8 (doc map); eval note updated.
- **S2 — markdown_parse dataflow (the crucial workflow).** Author the
  PDF→OKF ingest as a `dataflow` (sources → transforms → consumers;
  Stage 0-5 + parse_newsletter 0-6; maint-full elided to one grouped
  consumer node). Link from `doc/procedures/markdown_parse.md`. This is
  the adoption decider — hardest real subject.
- **S3 — maint-full workflow.** The 14-step chain as a `workflow`
  (PRE_FULL / TIER1 / TIER2 lanes; TIER2 `--check` steps as blocking
  checks; recovery backup as rollback). Grouping is mandatory (>12
  steps); back-references card-ified per the trial lesson. Link from
  `doc/procedures/maintenance.md`.
- **S4 — Conventions.** Naming, the pointer-line pattern, and a short
  "when to draw" rule (only when a section gains a genuinely
  multi-dimensional subject) written into `doc/procedures/` or the
  corpus-uniformity doc; `doc/local/archify_eval.txt` finalized.

## Authoring conventions (trial-1 lessons; land in S4)

- Long back-edges are the whole geometry problem: facts that would
  need one (consumer→store write-backs, operator paths) go to cards,
  not edges (trial 1: 13 edges → 10 and the layout snapped clean).
- The ≤12-primary-node cap is real: bigger subjects (the 14-step
  chain) group mandatory — grouped nodes carry their sub-steps as
  cards.
- Labels fully implied by their endpoints ("export" into "Snapshot
  parquets") are dropped — sanctioned by the authoring contract;
  every other label is semantic and gets moved/nudged, not deleted.
- The validator's suggested fixes (labelAt/labelDy values, evidence
  coords, supportedFixes) are directly usable — repair rounds are
  copy-paste-apply-rerun; budget ~6 validate rounds for a new
  10-node diagram (trial 1 took 6 rounds / 3 delivers, minutes).
- Browser-evidence overflow is fixed by compacting layout gaps
  (gapY 70→48 cured a 31px overflow), never by clipping or scrollers.
- Fail-closed is load-bearing: a non-zero deliver can never be
  reported as success, and a failed candidate never replaces the
  previous artifact.

## Risks / Non-goals

- **Rot:** the sync rule (S4) plus few diagrams is the mitigation; a
  diagram that can't be re-rendered in one sitting gets deleted rather
  than drifted.
- **Size:** ~0.7 MB per HTML; 3 diagrams ≈ 2 MB. Accepted.
- **Upstream:** single-maintainer skill, fast-moving schemas (workflow
  v1→v2 migration exists); MIT and the committed IR outlives the tool.
- **Privacy (fine for this vault):** runs fully local; the only
  network call is a notification-only update check (1 s fail-silent,
  never auto-installs; disable with `ARCHIFY_UPDATE_CHECK_DISABLED=1`).
  Skill footprint ~1.3 MB; needs Node ≥ 22 (machine has v24).
- **Non-goals:** no diagram-for-everything (the corpus stays prose);
  no rendering of vault knowledge-graph data (the frontend already does
  that off DuckDB); no CI gates.

## Candidate census (2026-09-05, post-S3; revised same day per user pointer)

User pointer: the integration suites encode the repo's CONCRETE workflows —
they are executable specifications, and a chain with a suite has a built-in
change-detector (every arc that touches the suite is a re-render trigger).
Suite-backed subjects outrank prose-only ones. Revised verdicts:

| # | Subject | Type | Owner | Executable spec | Verdict |
|---|---------|------|-------|-----------------|---------|
| 1 | Search/embeddings stack — one bge-small embedder → four surfaces + embed-matrix store + vec0→flat→cosine fallback | architecture | `doc/procedures/embeddings.md` | test_note_embeddings, rebuild suites | **DRAW (S5)** |
| 2 | Relations pipeline — extract_relations CLI walk + alias resolution + `_pending_relations` sidecar contract → triage (4 buckets) → apply → follow-up chain | workflow | `doc/procedures/markdown_parse.md` §9 | test_integration_extract_relations_cli + triage suites | **DRAW (S6)** — widened from triage-only: the CLI sidecar contract is part of the same chain |
| 3 | derive_* chain — extract_relations prose→edges (idempotent) → derive_events edge-promotion → derive_insights scan (quotes/metrics + rendered blocks) → cited_in projection; DELETE-then-INSERT + UNIQUE dedupe semantics throughout | dataflow | `doc/design/graph_design.txt` | test_integration_derive_chain (P3) + derive_events_cli + derive_insights_apply (A1) | **DRAW (S7)** — upgraded from DON'T-DRAW: the suite treats it as its own concrete chain, and neither landed diagram shows the derive interdependencies |
| 4 | Snapshot lifecycle — create (pools + twins) → verify (tamper + generation-drift detection) → restore (reconnect query parity) | lifecycle | `doc/procedures/maintenance.md` | test_integration_snapshot_cycle (A3) | **DRAW (S8)** — upgraded from DEFER: the create/verify/restore cycle is a genuine state machine with an executable spec |
| 5 | QA/perf gate chain | workflow | — (no dedicated doc) | run_gate_report.py | **DEFER** — no owner doc; Makefile self-documents |
| 6 | PDF engine fallback ladder | workflow | `doc/procedures/markdown_parse.md` §PDF | — | **DEFER** — card + table carry it |
| 7 | Reader request path | sequence | `doc/design/architecture.md` | test_api_flask_integration | **DEFER** — conventional; draw on structural change |
| 8 | OKF frontmatter lifecycle | lifecycle | `doc/okf/README.md` | frontmatter_schema gate | **DEFER** — high rot risk while schema evolves |
| 9 | Mojo bridge | sequence | `doc/design/graph_design.txt` | bench parity gates | **DEFER** — niche audience |
| 10 | Vault-scaling tripwire / filesystem layout / ts contract / note-writer matrix / validators / perf legs | — | — | — | **DON'T DRAW** — threshold tables, invariant lists, or matrices; not flows |

S1-S3 landed; S5-S8 are the live queue in priority order.

**Execution status (2026-09-05):** S5 (embeddings_stack), S6
(relations_pipeline), S7 (derive_chain) LANED in
`doc/design/diagrams/` — full 9-check showcase passes, visual-check
green, pointers live in their owner docs. **S8 (snapshot lifecycle)
LANED with a viewport caveat** (user call: broad display): showcase
validation + delivery green; the four-band canvas saturates at
~1376px scroll height, so it overflows a 1440×900 test viewport but
fits broad displays (~2100px-wide class, e.g. 2560×1440, or zoom
out) — caveat recorded in the maintenance.md pointer. Per-viewport
legs: 2048×1320 misses by 56px; 2560×1440-class fits.

Doctrine addition: when a chain has an integration suite, the suite is the
diagram's re-render tripwire — cite the suite in the diagram's card so the
next arc touching it finds both.

## Verification

- S1: artifacts in place, pointer line live, `make search-fresh
  APPLY=1` clean, `make advisory` still 10/10.
- S2/S3: showcase validation receipt (9 checks, 0/0) + visual-check
  pass + perceptual review recorded per diagram; links resolve.
