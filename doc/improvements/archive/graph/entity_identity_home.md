---
title: "Entity identity home — one name normalisation, one registry lookup, two tiers"
status: executed
filed: "2026-10-15"
executed: "2026-10-16"
completed_md: "398"
area: "helpers/core"
---

## 0. TL;RA

Five name normalisers drifted across the estate —
`extract_relations._canonical_key` (de-accent + apostrophe +
camelCase), `name_dedup.normalise` (case + footnote + punctuation),
`person_names.person_name_key` (token-sort), the vigil sync's
`entities.normalized_name` lookup, and `fuzzy_match`'s tokenizer — each
folding different things, so one real-world entity could resolve at
one path and mint a duplicate at another (the entity_name_dedup
census: 1,575 collision clusters). This arc consolidates them into ONE
home, `helpers/core/entity_identity.py`, and re-points every call site.
Filed as its own proposal because it re-points the resolver hot path
(600k+ key computations per dry-run) — a cross-cutting refactor that
neither dedup proposal declared.

## 1. The model (as implemented)

- **Tier 1 — identity**: `name_key(name)` — the one fold (NFKD
  de-accent, footnote strip, apostrophe drop, camelCase split,
  punctuation→space, collapse, casefold). Superset of all four
  predecessor keys; deterministic; deliberately NOT fuzzy (no token
  re-ordering, no initials logic).
- **Tier 2 — scoring**: `helpers.core.fuzzy_match` (word_overlap,
  spellfix, threshold-gated) SUGGESTS; only the key decides identity.
  `fuzzy_match._tokenize` is a scoring tokenizer, not an identity
  function — kept inside fuzzy_match.
- **Legal lines**: `legal_line()` delegates to
  `person_names.classify_person_name` (person/huf/trust — one
  classifier). An HUF never merges into the individual.
- **Person shape**: `is_person_name()` + `speaker_names()` (the
  Quotes.md attribution harvest) live here; the company dedup's S1
  filter and the person arc both consume them.
- **Registries**: `registry_index(conn)` (census-key → entity name)
  and `fold_registry(plan_path)` (the signed plan's fold map) are the
  only lookup structures.

## 2. Re-pointed call sites (done 2026-10-15)

| call site | before | now |
|---|---|---|
| `name_dedup.normalise` / `.token_sort_key` | local folds | delegate to `name_key` / `token_sort_key` |
| `name_dedup` person filter + speakers | local regexes | `entity_identity.is_person_name` / `.speaker_names` |
| `extract_relations._canonical_key` | local fold | `name_key` behind the resolver-local `@cache` |
| `related_party_sync._census_names` / `._fold_map` | local parses | `registry_index` / `fold_registry` |
| `related_party_sync` footnote/census-key in apply paths | name_dedup imports | `entity_identity` imports |

`person_names.py` keeps its own token-sort person key (order-
insensitive by design for holder-name grouping) and remains the
classifier home `legal_line` delegates to.

## 3. Behavioral deltas (intended, tested)

- The census got STRICTLY better: 1,547 → **1,504** clusters (apostrophe
  and compound-join variants now fold — `IndianOil` ≡ `Indian Oil`,
  `McDonald's` ≡ `McDonalds` in the dedup census, not just the
  resolver).
- `_canonical_key` gains footnote-marker folding (no prior behavior
  broken — strictly more folding on the same equivalences; the
  resolver suites pin McDonald's/Systèmes/NetwebTech).

## 4. Acceptance

- All resolver/sync/dedup suites green (392 tests across the nine
  touched files at implementation time).
- Key-superset properties unit-pinned (apostrophe, diacritic,
  camelCase, footnote, punctuation).
- No new normaliser may appear in `helpers/` without importing this
  module — enforced by review convention recorded here and in both
  dedup proposals.

## 5. Follows

- `doc/improvements/archive/graph/entity_name_dedup.md` (S3's
  resolve-before-insert consumes `registry_index`/`fold_registry`).
- `doc/improvements/archive/graph/person_name_dedup.md` (S1 registry +
  S3 ingestion hook build on the same home).
- `doc/improvements/archive/graph/person_resolver_lane.md` (#293 —
  the classifier home).
