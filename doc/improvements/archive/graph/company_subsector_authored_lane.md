---
title: "Company sub_sector authored lane — optional `subsector:` YAML field, canonical precedence + entity_tags mirror"
status: executed
filed: "2026-09-15"
executed: "2026-09-16"
completed_md: "238"
area: "helpers/graph/derive_hyperedges.py (authored-field read + canonical precedence), helpers/core/sync_tags.py (entity_tags mirror), tests, doc/design/graph_design.md — D7 capture discipline, no schema change"
---

# Company sub_sector authored lane (D7)

## 1. Problem — measured state

- Company→sub_sector membership today is **derived only**: the Yahoo
  `industry:` frontmatter field (816 values / 117 labels, written by
  `enrich_from_yfinance`) → `SUB_SECTOR_ALIASES` (68 curated mappings) →
  `derive_sub_sectors` union → 55 sub_sector hyperedges / **600
  memberships** (2026-09-15).
- **51 industry labels are unmapped** (top: Banks - Regional ×38, Credit
  Services ×23, Capital Markets ×10 — `findata/Misc/subsector_worklist.json`),
  so ~100+ companies have no sub_sector membership at all, and every
  mapped company inherits the alias-table's one-label-to-one-sector
  union even when the Yahoo label is a poor fit.
- The authored escape hatch **does not exist**: 0 of 1,165 company notes
  carry a `subsector:` field. `sync_tags` mirrors `subsector/…` tag
  values but only sector notes carry those (57 rows — child listings,
  not company classification). Nothing consumes a company-level authored
  classification anywhere.
- The `industry:` writer (`_update_frontmatter`) is surgical — it only
  touches the `industry:` line — so an operator-authored `subsector:`
  field already survives `make metrics-rebuild`. The preservation
  property is real but unpinned by any test.

## 2. Design

**Authored field**: optional `subsector: <Sub_Sector_Entity_Name>` in a
company note's frontmatter (the `industry:` precedent — a named YAML
field, not a tag). Authored is **canonical for that company**: in the
derive regroup the company is moved out of every alias-derived sub_sector
group and placed only in the authored one. Alias-derived membership fills
gaps; authored overrides everything.

**Value discipline**: the value must name an existing `sub_sector`
entity (case/spacing-insensitive: `apparel retail` → `Apparel_Retail`).
Unlike the S11 alias staleness check (map rides in version control →
raise), an unknown hand-authored value does NOT raise: it is warned and
appended to `subsector_worklist.json` under a new `unmapped_authored`
section, and the company keeps its alias-derived membership. Hand
typos must be visible, not pipeline-fatal.

**Lanes**:

1. **derive lane** — `extract_subsector_membership` in
   `derive_hyperedges.py` (mirrors `extract_industry_membership`: front
   read, path→name join, null/absent skip), consumed in
   `derive_sub_sectors` with canonical precedence.
2. **sync_tags lane** — `sync_tags.py` mirrors the named field for
   company notes into `entity_tags` as `subsector/<slug>` (slug =
   canonical entity name lowercased, underscores kept — consistent with
   the sector-note tag style). SQL consumers see the authored
   classification without a filesystem scan.
3. **discipline pin** — a regression test that `_update_frontmatter`
   preserves an unrelated `subsector:` field (the metrics-rebuild
   round-trip).

## 3. Slices

- **S1** `extract_subsector_membership` + canonical precedence in
  `derive_sub_sectors` + `unmapped_authored` worklist section.
- **S2** `sync_tags.py` named-field mirror (company notes).
- **S3** Tests: field read + normalization; precedence (authored moves
  the company, alias groups lose it); unknown → worklist + membership
  untouched; sync_tags mirror row; `_update_frontmatter` preservation.
- **S4** Docs: graph_design.md sub_sector line (authored-canonical
  note), this file §6, wiring-proposal D7 pointer.

## 4. Non-goals

- **No authoring of live notes.** Company notes are the operator's
  authored surface; this arc wires the lane, the operator writes the
  first values (the worklist's unmapped companies are the natural
  candidates).
- No new sub_sector entities, no alias-table additions (taxonomy
  decisions, #233 worklist process unchanged).
- No change to `DEFAULT_SOURCES` / source scope — that is D3, an
  explicit operator checkpoint.
- No static-checks vocabulary gate on the field (the derive worklist +
  warning is the discipline; a static gate needs a DB-free vocabulary
  source first).

## 5. Verification

- Unit tests per S3 (tmp vaults, no live DB writes).
- **Live regression proof**: with 0 authored values live, a
  `make derive-hyperedges` run must produce byte-identical regroup
  output (0 new edges / 0 new incidences; same 55/600 sub_sector
  counts) — the lane is a no-op until the operator authors.
- `make sync-tags` live: no row changes (0 authored values) — same
  no-op proof for the mirror lane.

## 6. Execution Results (2026-09-15)

- **S1 EXECUTED** — `_company_path_map(conn)` shared helper (single source
  for the S5 frontmatter-read join), `extract_subsector_membership`
  (industry-extractor mirror: null/absent skip, path→name join),
  `derive_sub_sectors(authored=, sub_sector_entities=)` with canonical
  precedence (authored company removed from every alias-derived group,
  placed only in the authored one; case/spacing-insensitive resolution),
  3-tuple return `(groups, unmapped, unmapped_authored)`;
  `_write_subsector_worklist(unmapped, unmapped_authored)` extended with
  the `unmapped_authored` JSON section.
- **S2 EXECUTED** — `sync_tags.py` `_authored_subsector()` named-field
  reader (split_frontmatter + yaml_safe_load, slug normalization mirrors
  `_norm_subsector`); mirror wired in BOTH paths (corpus fast path +
  per-file fallback), company notes only.
- **S3 EXECUTED** — 10 new tests: 5 derive-side (canonical-exclusive,
  unknown-worklisted, solo-membership-from-unmapped-industry, field read,
  worklist section), 4 sync_tags mirror tests (slug, spacing, null/absent,
  non-company ignored), 1 `_update_frontmatter` preservation pin.
  `test_hyper_incidence.py` 57/57, `test_sync_tags.py` 65/65.
- **S4 EXECUTED** — live no-op proof with 0 authored values:
  `make derive-hyperedges` 0 new edges / 0 new incidences (55 sub_sector
  hyperedges / 600 members unchanged); `make sync-tags` byte-stable
  (7,211 entity_tags rows before and after). db_schema.md hyperedge notes
  + wiring-proposal D7 pointer updated. Ruff clean.
- **Not done (by design)**: no live notes authored (operator surface —
  the unmapped worklist `findata/Misc/subsector_worklist.json` names the
  candidates: Banks - Regional ×38, Credit Services ×23, Capital Markets
  ×10, …); frontmatter stays `proposed` until the archival flip.
