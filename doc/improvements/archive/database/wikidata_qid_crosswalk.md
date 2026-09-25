---
title: "Wikidata QID crosswalk — sidecar + SKOS mappings constraining semantic_peer"
status: executed
filed: "2026-09-25"
executed: "2026-09-25"
completed_md: "296"
area: "helpers/maintenance"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json -->

# Wikidata QID crosswalk — sidecar + SKOS mappings constraining semantic_peer

**Date:** 2026-09-25 · **Status:** EXECUTED ·
**Area:** helpers/maintenance (new `wikidata_sync.py` fetch lane),
`enrich_relations.py` E3 side-input; concept_mappings store

Un-defers the Wikidata row of the ontology convention stack's
future-facing list (§7: "QID crosswalks as an embedding side-input
constraining `semantic_peer` — after S2 lands"). S2's SKOS machinery is
proven — the NIC builder that was itself gated on S2 has shipped and
runs in maint. Tier-1 item 4 of the 2026-09-25 pending-items survey.
The consumer is live: `build_semantic_peer_edges`
(enrich_relations.py:683) reads `company_embeddings`, now stamped
**granite-embedding-97m-r2** end-to-end; 30 edges live via
`triage:accept`.

## 1. TL;RA

A one-time, politeness-cached Wikidata fetch resolves company entities
to QIDs; the crosswalk lands in `concept_mappings` (the S2 SKOS store —
**no schema change**, avoiding the `entity_identifiers` CHECK edit that
a 'qid' type would force) with a sidecar parquet cache in the mca_cin
pattern; and the E3 semantic-peer builder consumes it as a constraint —
same-QID pairs are suppressed before triage (they are the same company,
not peers). Along the way the arc retires a piece of stale debt found
in the survey: E3's docstring and `source_ref` still say
`embeddings:bge-small:v1` while reading granite vectors.

## 2. Evidence (measured/verified 2026-09-25, this box)

| Fact | Value | Notes |
|---|---|---|
| candidate companies | 727 quoted ∪ 1,185 embedded | dedup at S1; overlap small |
| semantic_peer edges live | 30 | all `triage:accept` |
| company_embeddings model | granite-embedding-97m-r2 | swap propagated |
| E3 label | `embeddings:bge-small:v1` | STALE — relabel here |
| `identifier_type` CHECK | cin/lei/cik/isin/llpin/alias | no 'qid' → concept_mappings route |
| `concept_mappings` cols | scheme/concept pairs + match_type + status + version | S2 machinery, converger pattern exists |
| sidecar precedent | `memory/data/mca_cin.parquet` + manual-CSV override | build/apply/resolve |

## 3. Design

1. **S1 — fetch lane** (`helpers/maintenance/wikidata_sync.py`):
   `wbsearchentities` by name with India filter, corroborated by
   ticker (Wikidata exchange-ticker statements) and/or ISIN where
   present; writes sidecar `memory/data/wikidata_qids.parquet` with a
   manual-curation CSV override that wins on merge (the mca_cin
   pattern). Dry-run default; `--apply` writes the sidecar only (no DB
   write). Etiquette: User-Agent **`Findata-Agent`** (operator-set
   2026-09-25), serialized requests, full-result caching — one-time
   backfill, quarterly refresh cadence (operator-confirmed 2026-09-25),
   never wired into refresh-chain automation.
2. **S2 — SKOS converge**: exactMatch rows into `concept_mappings`
   (`findata:entity` → `wikidata:qid`) ONLY for unique + corroborated
   hits; ambiguous results land as closeMatch suggestions in the
   triage queue, never auto-accepted. Idempotent converger; manual CSV
   overrides authoritative.
3. **S3 — E3 side-input**: same-QID (post-canonicalisation) pair
   suppression in `build_semantic_peer_edges` before triage, rejected
   count logged; relabel the stale bge docstring/source_ref to the
   granite stamp read from `company_embeddings.model` (never a second
   hard-coded model string).
4. **S4 — shakedown + archival** per the proposals checklist.

## 4. Acceptance criteria & shakedown

1. Sidecar: resolved-QID count measured (target band ~60–80% of the
   deduped candidate set — large caps resolve, long-tail stubs won't);
   operator precision audit of a 25-row random sample ≥ 95% before any
   exactMatch converges.
2. `concept_mappings` converge idempotent (re-run = 0 changes);
   ambiguous names surface as suggestions, count reported.
3. E3 re-run: same-QID suppressions measured; remaining candidate set
   unchanged otherwise; `source_ref` reflects the live model stamp.
4. Eval gate (`ontology_eval_gate.py`, frozen set) between dry-run and
   apply: zero regressions — `semantic_peer` is a query-visible edge
   surface.
5. `make qa` 11/11; snapshot round-trip covers `concept_mappings`
   (existing tracked table).

| Projected outcome | Today | After |
|---|---|---|
| company→QID crosswalk | absent | sidecar + SKOS rows (measured yield) |
| semantic_peer precision | uncorroborated KNN | same-QID suppression before triage |
| E3 labeling | stale bge string | model-stamped |

## 5. Risks

- **Name ambiguity** (common Indian corporate names, group entities) —
  corroboration + unique-hit rule + manual CSV override; precision
  audit gates S2.
- **API etiquette / availability** — full caching, serialized calls,
  quarterly cadence; the sidecar is the interface, Wikidata is
  re-fetchable.
- **Over-suppression** — same QID means same company by definition;
  suppression only removes self-pairs, recorded and reversible.
- **QID churn upstream** (merges/redirects) — sidecar keeps fetched-at
  + version; refresh re-resolves and reports deltas.

## 6. Non-goals

- No `entity_identifiers` CHECK change ('qid' stays out of the
  registry; concept_mappings is the store).
- No Wikipedia content ingestion, no QID-driven UI, no person QIDs
  (the person-resolver lane owns persons), no auto-refresh wiring into
  refresh-chain, no new edge types.

## Appendix — raw measurement log (2026-09-25, this box)

| Probe | Result | Notes |
|---|---|---|
| quoted entities | 727 | quotes table |
| company_embeddings | 1,185 rows | granite-embedding-97m-r2 |
| semantic_peer | 30 edges | source_ref `triage:accept` |
| E3 header comment | "bge-small-en-v1.5, 384d" | stale vs live stamp |
| registry CHECK | 5 types, no qid | sqlite_master DDL |
| concept_mappings | 8 cols, status/version | S2 store |

## 7. Implementation log

- S1 helper `helpers/maintenance/wikidata_sync.py` now collects deduplicated
  quoted/embedded company candidates, performs serialized Wikidata searches
  with `Findata-Agent`, caches complete JSON responses, and writes a dry-run-
  default parquet sidecar. Manual CSV rows take precedence.
- S2 convergence is dry-run-default and idempotent: `exactMatch` rows become
  active mappings; `closeMatch` rows become candidates. No database mappings
  were applied.
- S3 semantic-peer code now reads active `findata:entity → wikidata:qid`
  mappings, suppresses same-QID pairs before triage, and stamps E3 source refs
  from the live `company_embeddings.model`; the legacy bge delete prefix now
  covers all `embeddings:%` batches.
- Five resolver/convergence tests and one semantic-peer input test pass. A
  25-company live dry-run yielded
  `2` close matches and `0` exact matches; remaining requests were throttled
  by Wikidata with HTTP 429. The sidecar was written with those two close
  suggestions only, and the S2 dry-run planned `2` candidate mappings.
- The resolver was switched to direct Wikidata SPARQL at
  `query.wikidata.org/sparql`: identifier-first ISIN (`P946`), ticker
  (`P249`), and LEI (`P1278`) lookup, exact English-label/alias fallback,
  one-second serialized pacing, progress reporting, and bounded timeout
  handling. A 25-row identified-company run was manually interrupted after
  reaching `3/25`; the first two produced no QID and the third was incomplete.
  The endpoint remains too slow/unreliable for unattended execution.
- Decision: park the live fetch before any DB mapping apply. Keep the helper,
  tests, sidecar, and dry-run convergence available for a future controlled
  retry; do not run a background fetcher.
