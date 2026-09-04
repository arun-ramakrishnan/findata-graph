# Procedure: Repo diagrams (archify pipeline)

**Date:** 2026-09-05 · **Proposal:** `../improvements/archive/tooling/archify_diagram_pipeline.md`

Diagrams are bounded, evidence-carrying companions to doc sections —
never replacements for prose. The corpus stays text-first; a diagram
earns its place only when a section gains a genuinely multi-dimensional
subject (a multi-stage chain, a store topology, an ordered protocol).

## When to draw (and when not to)

- Draw: a subject whose structure is genuinely 2-D (the maint-full
  chain, the ingest pipeline, the storage topology). One diagram per
  owning doc section, answering that section's core question.
- Don't draw: anything renderable as a table or a sentence; the vault's
  knowledge graph (the frontend already renders it off DuckDB); anything
  aspirational — diagrams, like `architecture.md`, are
  "as it actually is".
- A diagram that cannot be re-rendered in one sitting after an arc
  changes its subject gets deleted, not drifted.

## Location & naming

- Pair lives at `doc/design/diagrams/<slug>.json` + `<slug>.html`
  (current: `system_overview`, `markdown_parse`, `maint_full`).
- The JSON IR is the committed source of truth (diffable, greppable);
  the HTML is a regenerable build artifact, also committed
  (self-contained, ~0.7 MB). visual-check PNGs are transient — never
  committed.
- The owning doc section carries a pointer line (diagrams are opaque to
  doc_query; the pointer is the discoverability path):

  ```markdown
  > **Diagram:** `../design/diagrams/<slug>.{json,html}` — <what it
  > shows> (archify; JSON IR is the committed source, HTML
  > regenerable). Re-render when <the depicted chain> changes.
  ```

## The pipeline (input → HTML)

1. Pick the owning section + the one question; type via the guide's
   recipes (<https://tt-a1i.github.io/archify/guide.html>).
2. Facts: `doc_query`/`script_query` for intent, codebase-memory-mcp
   for symbol→file:line, `rg` to verify; ≤12 primary nodes, one main
   path.
3. Author the IR (schema + ONE example; grid/stage/col auto-layout;
   `quality_profile: "showcase"`). Facts that would need long
   back-edges go to cards.
4. `node bin/archify.mjs validate <type> <ir> --quality showcase
   [--repo-root .] --json` — repair with the receipt's suggested fixes
   until 9/9 checks, 0 errors/0 warnings.
5. `deliver` (SHA-pinned, atomic) → `visual-check` (4 viewports × 2
   themes) → perceptual review by a human → commit IR + HTML.

## Hard-won constraints (trial + S2/S3 lessons)

- **Evidence marks (`sources`) are architecture-only** — and they
  require `meta.repository`. Always pin **origin/main** (`git
  ls-remote origin main`), verifying every anchor against
  `git show origin/main:<path>`; worktree line numbers against an
  unpushed tree produce 404s or silent lies. Dataflow/workflow diagrams
  carry provenance in cards instead.
- **No sublabels at wide viewBoxes** — sublabel text renders at a fixed
  7px and fails the 6px desktop-readability floor beyond ~1050px viewBox
  width. Use `tag` chips and cards instead.
- Node width: set explicitly (~150px); same-column nodes sharing auto
  verticals must share widths (center alignment).
- Reciprocal/skipping flows need side loops with via points whose
  coordinates align with the edge-attachment anchors (read them off the
  validator's diagnostics); drop edges whose facts fit cards.
- Overflow: fix by compacting gaps / cropping empty viewBox bands /
  shortening card copy — never by clipping.
- Budget: ~6 validate rounds for a new 10-node diagram, minutes total.

## Gates

None added to `make qa` / `make advisory` — validation is an
authoring-time discipline; a stale diagram is a doc bug fixed by
re-rendering, not a gate failure.
