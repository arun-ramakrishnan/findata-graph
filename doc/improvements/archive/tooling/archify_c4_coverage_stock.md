---
title: "Archify C4 coverage on stock schema — external actors + first sequence diagram"
status: executed
filed: "2026-09-13"
executed: "2026-09-14"
completed_md: "232"
area: doc/design/diagrams
---

# Archify C4 coverage on stock schema — external actors + first sequence diagram

**Date:** 2026-09-13 · **Status:** EXECUTED 2026-09-14 ·
completed.md entry 232 · **Area:** `doc/design/diagrams/`

## 1. Motivation

The terrain C4 evaluation (`doc/local/evaluations/terrain_c4_evaluation.md`,
2026-09-13) lands two findings against our diagram surface: the archify IR
schema carries no C4 actor semantics, and the `sequence` diagram type has
zero instances — no behavioral/dynamic coverage anywhere in the set.
Verdict there: no replacement, shape-only supplement.

The follow-up question was whether to enhance the schema (fork) to close
these. Answer investigated and rejected: **both gaps close with stock
primitives**, and the proof is already committed:

- `type: "external"` exists in the stock enum (`frontend, backend,
  database, cloud, security, messagebus, external` —
  `common.schema.json`) **and already renders in two shipped diagrams**:
  `markdown_parse.json` (3 nodes: PDFs in, newsletter .md in, Stage 5
  curation) and `relations_pipeline.json` (1 node: the human per-row
  triage decision, `lane: human`). No renderer wall, no fork needed.
- The `sequence` schema (`participants` + `messages`, fixed/spread
  layout, `return` legend entries) is complete and unused. Zero
  instances is an authoring backlog, not a capability gap.

So this proposal is authoring-only: no validator change, no schema
change, no new tooling. Explicit non-goal: forking `tt-a1i/archify`
(any renderer wall found becomes a `diagrams.md` constraint note plus
an upstream issue, never a fork).

## 2. Evidence (measured 2026-09-13, this box)

### Census: external-type usage and sequence coverage

| Diagram | Type | `type: "external"` nodes | C4 reading |
|---|---|---|---|
| system_overview | architecture | 0 | L1/L2 without actors — the gap |
| embeddings_stack | architecture | 0 | L2, self-contained (model files? operator runs embed — candidate) |
| markdown_parse | dataflow | 3 (pdf, md_in, agent) | external inputs proven |
| maint_full | workflow | 0 | chain internal to repo |
| derive_chain | dataflow | 0 | chain internal to repo |
| relations_pipeline | workflow | 1 (annotate, human lane) | human actor proven |
| snapshot_lifecycle | lifecycle | 0 | states, no handshake |
| quote_capture | workflow | 0 | chain internal to repo |
| sequence instances | — | 0 of 8 | behavioral gap |

### What system_overview is missing (L1 framing)

`system_overview.json` shows Ingest helpers, Maintenance chains, the
vault, SQLite, DuckDB — the system and its stores, but nobody outside
them. The load-bearing external relationships exist in prose and
practice: the writer owns `findata/` (vault frontmatter carries
`writer-owned` tags already), the operator runs `maint --full`, and
every evidence anchor pins `origin/main` on GitHub
(`meta.repository.revision: b057071c...`). A C4-L1 reader gets the
boxes but not the actors. Two `external` nodes plus edges fix that
without restructuring the grid.

## 3. Plan

### Slice A — External actors on system_overview (S1)

Lightest touch, mirrors the refresh arc's Slice A. Add two nodes to
the existing 4-col grid (or an actor band if the grid crowds — validator
receipt decides, ~150px explicit widths per `diagrams.md:66-67`):

- `writer` — `type: external`, tag `person · writer-owner`, edge to the
  `vault` node. No `sources` (persons carry no file anchor; provenance
  goes in a card citing the vault's writer-owned convention).
- `github` — `type: external`, tag `external system`, edge to the
  stores/chain it anchors (`meta.repository` URL already names it).
  Card cites the origin/main pinning contract (`diagrams.md:57-62`).

Re-validate 9/9 showcase, `deliver`, `visual-check` (4 viewports × 2
themes), human perceptual pass. Success criterion: the renderer
visually distinguishes `external` (already true in markdown_parse /
relations_pipeline — this slice re-confirms on architecture type).

Optional S1b if S1 is clean: one `external` node on embeddings_stack
(the operator/model-source boundary) — only if a genuine question
demands it, not for symmetry.

### Slice B — First sequence diagram (S2)

Author one `sequence` instance for the most ordered multi-participant
protocol in the repo: the **snapshot apply handshake**
(participants: operator → snapshot CLI → verify → SQLite/DuckDB restores
→ vault notes; the state machine already lives in snapshot_lifecycle,
this covers the handshake it doesn't show). Complement, not duplicate:
states stay in the lifecycle diagram, messages go here.

Bounds per pipeline contract: one owning section + one question ("what
talks to what, in what order, when a snapshot is applied"), ≤8
participants, cards for anything needing back-edges. Owner pointer line
in the owning doc section (`diagrams.md:30-37` pattern). Full showcase
validation + visual-check + perceptual review.

If authoring surfaces a genuine renderer wall (e.g., no alt/opt
fragments for the verify-fail branch — which instead goes in a card per
the back-edge rule), record it as a named constraint in `diagrams.md`
and file it upstream. Not a fork trigger.

### Slice C — Procedure notes (S3)

- `diagrams.md`: one line under Hard-won constraints — `external` is
  the actor primitive (cite markdown_parse/relations_pipeline +
  system_overview as prior art); sequence is the behavioral type with
  its one-question bound. No new gates (pipeline stays authoring-time
  discipline per `diagrams.md:75-77`).
- Pointer line for the S2 diagram in its owning section.

## 4. Execution order

A → B → C. S1 first because it is minutes-bounded and proves the
`external`-on-architecture rendering the rest assumes; S2 after the set
is stable; doc notes last.

Prerequisite (not a slice): restore the archify skill from
`~/.agents/skills.backup/skills/archify/` — the live
`~/.agents/skills/` is empty (terrain eval §3 ⚠️, re-verified
2026-09-13). No render runs without it.

## 5. Verification

- S1/S2 IRs: `node bin/archify.mjs validate <type> <ir> --quality
  showcase --repo-root . --json` → 9 checks, 0 errors, 0 warnings.
- S1/S2 HTML: `deliver` (SHA-pinned, atomic) → `visual-check` →
  overflow + rendering green at 1440×900 / 1600×1000 / 1920×1080 /
  2048×1320, light+dark → human perceptual pass → commit IR + HTML.
- S2 anchors: ripwire symbol→file:line for every participant's
  implementation before pinning; verify against origin/main.
- Final: `make search-fresh APPLY=1`, then plain `make search-fresh`
  (rc=0). No `make qa` gate by pipeline design (stale diagram = doc
  bug, not build failure).

## 6. Risks

- **Grid crowding (S1):** two actor nodes on a 4-col grid may push the
  viewBox past the ~1050px sublabel floor — mitigated by the standing
  rule (tags/cards, never sublabels at width) and by validator
  diagnostics; worst case the actors become a dedicated row.
- **Scope creep (S2):** the snapshot handshake touches the whole
  storage topology — bound it to apply-order messages only; internals
  of each store stay in their own diagrams.
- **Fork temptation:** if the renderer disappoints anywhere, the
  standing decision is constraint-note + upstream issue. Revisit only
  with a named wall and a priced merge-burden estimate.
- **Rot:** same mitigation as the pipeline — re-render in one sitting
  after an arc changes the subject, else delete (`diagrams.md:19-20`).
