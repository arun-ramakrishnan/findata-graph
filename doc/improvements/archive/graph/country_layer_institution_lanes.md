---
title: "Country layer + institution counterparties — listed_in edges and regulator/rating lanes"
status: executed
filed: "2026-09-09"
executed: "2026-09-10"
completed_md: "219"
area: "helpers/graph + findata geography surfaces"
---

# Country layer + institution counterparties — listed_in edges and regulator/rating lanes

**Date:** 2026-09-09 · **Status:** EXECUTED 2026-09-10 ·
completed.md entry 219 ·
**Area:** helpers/graph (`extract_relations.py`, `triage_pending_relations.py`,
one new producer), helpers/core (`sync_tags.py`, `parse_newsletter.py` tag
seeding), findata geography surfaces

## 1. Motivation

Two graph-representation gaps surfaced by the 2026-09-09 representation
survey (this session), both sharpened by the #215 commentary refresh
(13x corpus growth, ~85 global companies):

**Geography.** The corpus now has a real geography axis, carried by three
surfaces that disagree: entity_tags geography/* (1,229 rows, 11 values,
241 of them non-country slop), the note-frontmatter geography key
(198/1,165 notes — never converged), and exchange tickers (the only
authoritative signal). The graph has zero country representation — no
country entities, no listed_in edges. Trigger: user direction 2026-09-09
(country layer + non-country slop cleanup), which also activates the
long-deferred A3 sync_tags whitelist.

**Institutional counterparties.** The highest-signal institutional facts
in the commentary are structurally invisible: RBI (139 chatter mentions)
and SEBI (72) do not exist as entities, the relation extractor resolves
targets against companies only, and RBI (3 chars) is discarded by the
fragment-length noise gate before triage ever sees it. Rating actions
(~129 upgrade/downgrade mentions) fare no better: CRISIL and ICRA exist
as companies but no rated_by pattern exists to link them.

## 2. Evidence (measured 2026-09-09, this box)

| Check | Result | Verdict |
|---|---|---|
| entity_tags geography/* census | 1,229 rows / 11 values: india 971, global 221, usa 14, domestic_focused 13, international 2, north_india 2, south_korea 2, taiwan/pan_india/west_india/south_asia 1 each | 241 slop rows; countries are the minority of the vocabulary |
| geography frontmatter key | 198/1,165 Companies notes (india 95, global 93, usa 9, south_korea 1); 967 without the key | #215-era convention, never converged |
| Ticker suffix buckets (entities.ticker) | india 850 (.NS 731 + .BO 119), no-ticker 235, plain-symbol 64, .KS 3 / .SG 2 / .PA 2 / .TW 1, other-suffix 8 (KQ, MI, HK, SS, SR, T, HE, DE) | authoritative home-market signal; matches #218 §7's 850 |
| entities DDL (sqlite_master) | no entity_type domain CHECK — only the company-suffix guard, scoped to company | entity_type='country' rows drop in without migration |
| OKF schema (`doc/okf/frontmatter.company.v1.json`) | geography key is pattern-only (`^[a-z_]+$`), open vocabulary; global appears only in the description | vocabulary change = description tweak, no enum bump |
| `noise_target` fragment rule | `len(t) < 4` discards "RBI" at write time (`triage_pending_relations.py:190`) | institution short-names need an exact-name exemption |
| extract_relations resolver | `SELECT name FROM entities WHERE entity_type='company'` | companies only; RBI/SEBI mentions resolve to nothing |
| Existing institutions | 205 rows, 0/205 with file_path (bare rows), all invested_in-sourced via enrich_relations; roster is entirely asset managers | the type means "fund holder" today; RBI/SEBI extend it to regulator |
| Triage fuzzy surface | `load_entity_names` reads ALL entity types; #218's guard downranks non-company alias targets | country rows must be excluded from alias candidates or "india" contained in "Bank of India" fires |
| Mention volumes (The_Chatter + Points_And_Figures) | RBI 139, SEBI 72, BSE 60, CRISIL 16, NSE 10, upgrade 123 / downgrade 6 | ~340 institution-lane mention sites |
| #218 (landed 2026-09-09) | word-overlap confirm tier + relation_noise.json discard gate + non-company downrank | the FP rail for institution lanes already exists |

## 3. Design

One arc, four slices in dependency order. C1 → C2 share one ticker→country
map (single source of truth); I1 → I2 are the institution lane, sequenced
after so the country diff stays clean.

**C1 — country entities + listed_in edges.** New bare entity rows,
entity_type='country', named exactly as the tag vocabulary value (india,
usa, south_korea, taiwan, singapore, france + hand-mapped exotic homes:
italy, hong_kong, china, saudi_arabia, japan, finland, germany) so the
tag↔entity mapping stays mechanical. One deterministic producer
(helpers/graph/derive_countries.py, dry-run default, make target
derive-countries) reads entities.ticker through the suffix map and writes
country rows + listed_in edges (company→country, matching the part_of
company→sector direction convention) with source_ref provenance. The 235
no-ticker companies are never guessed — they land in
findata/Misc/country_worklist.json (same pattern as the quote worklists)
so future ticker fills converge them. Guard rider:
`load_entity_names` in triage excludes country rows (one WHERE clause) —
otherwise a country entity literally named "india" becomes an
alias-candidate target for every "Bank of India"-shaped mention.
Alternative rejected: deriving country from chatter extraction — country
mentions are already noise-classified by `noise_target`, and tickers are
exact. graph-rebuild runs in-slice (entity inserts trip the DuckDB drift
gate).

**C2 — slop cleanup + geography convergence.** Policy: regional variants
(domestic_focused, pan_india, north_india, west_india) → india;
international and south_asia → dropped (too vague to be a country);
global → dropped as a value everywhere (the geography key is
single-valued home market; "multi-market" is a question for listed_in
edges, not a tag). The geography: frontmatter key converges to the C1
ticker-derived country on every ticker-backed note. Mechanics: a
mechanical bulk frontmatter editor with a --dry-run diff (existing
mass-edit discipline), operator eyeballs the diff, apply happens only at
an explicit user checkpoint (house rule for mass rewrites of live
markdown notes). Source prevention — the A3 activation: `sync_tags` gains
a geography/* vocabulary whitelist (country values only, slop cannot
re-enter), and `parse_newsletter` tag seeding derives geography from the
ticker suffix instead of the hardcoded geography/india at
`parse_newsletter.py:552`. OKF schema description updated in the same
change (global leaves the examples; frontmatter_keys.md regen if it
documents the vocabulary). After apply: make sync-tags rebuild,
verify_notes, static_checks OKF census — all green. Touched notes'
last_modified bumps are data churn: bulk-patch commit rider collapses
them per the commit contract.

**I1 — institution entities + resolver lane + noise carve-out.** RBI and
SEBI as institution entities (bare rows via the enrich_relations
insertion path, same shape as the existing 205). The resolver widening is
PATTERN-SCOPED, not global: the allowlist {RBI, SEBI} is consulted only
by the new institution-lane patterns of I2, so a stray "SEBI" capture in
some existing generic pattern still resolves to nothing.
`noise_target` gains an exact-name exemption set (normalized {"RBI",
"SEBI"}) consulted before the length rule — everything else in the
fragment class still dies. #218 guard carve-out: the non-company
downrank in the triage bucket spares institution targets when the row's
edge type is an institution lane. Alternative rejected: widening the
resolver to all 205 institutions — a Vanguard-class FP surface with zero
current demand; the allowlist grows only via triage evidence.

**I2 — extraction patterns + one harvest.** New patterns in
extract_relations, named to the existing verb-convention (invested_in,
acquired, supplier_to): rated_by (company→company — CRISIL and ICRA
already exist as companies, zero new entities; CARE/India Ratings get a
probe for an exchange listing during implementation and a triage stub
plan if warranted, never auto-create) and regulated_by / approved_by /
penalized_by (company→institution, {RBI, SEBI}). Then ONE harvest run:
make derive-relations → triage report → operator decisions →
--apply-decisions, with FPs persisting through relation_noise.json (the
G3 machinery from #218). Direction is re-checked on every accepted row
(house doctrine from the acquired rows). Expected yield: tens to low hundreds
of edges from the ~340 mention sites.

## 4. Acceptance criteria & shakedown

1. C1: dry-run prints the bucket table; --apply adds exactly the country
   entity set; listed_in census matches the buckets (850 / 64 / 3 / 2 /
   2 / 2 + 8 hand-mapped, 0 for no-ticker); graph-rebuild clean; drift
   gate green.
2. C1 guard: a seeded "Bank of India"-shaped triage queue produces zero
   alias candidates pointing at country rows.
3. C2: dry-run diff eyeballed → apply at the user checkpoint →
   geography/* census is country-vocabulary only (zero
   global/domestic_focused/regional values); the geography: key is
   present on every ticker-backed note and equals its listed_in target;
   sync-tags + verify_notes + static_checks green.
4. I1: dry-run resolves "SEBI" mentions to the SEBI institution entity
   for institution-lane patterns only; `noise_target("RBI")` is False
   while `noise_target("and")` stays True.
5. I2: harvest lands rated_by/regulated_by edges with source_ref; triage
   renders institution-lane candidates unpenalized; a second extract
   shows discarded rows suppressed via relation_noise.json.
6. Gates: targeted suites per slice; full make qa + perf + advisory ONCE
   at arc end with the user's go.

| Projected outcome | Today | After |
|---|---|---|
| Country entities / listed_in edges | 0 / 0 | ~14 / ~932 |
| geography/* distinct values | 11 (7 non-country) | ~14 country-only, 0 slop |
| Notes with geography key matching listed_in | 198 (95 india vs 850 ticker-true) | ~930 |
| No-ticker companies (invisible gap) | 235 | 235 worklisted for convergence |
| Institution counterparties | 0 entities, 0 lanes, RBI noise-gated | RBI + SEBI entities, 4 lanes over ~340 mention sites |

## 5. Risks

- **C2 mass note rewrite** — dry-run diff + explicit permission
  checkpoint; bulk-patch commit rider collapses the data churn.
- **Plain-symbol ⇒ usa assumption** — the 64 plain tickers are
  hand-audited before C1 apply (appendix list); any non-US symbol is
  re-bucketed rather than guessed.
- **"global" leaves the observable vocabulary** — OKF description +
  frontmatter_keys regen ride in the same change; #215-era notes
  converge to ticker-true values.
- **Institution-lane false positives** — resolver widening is
  pattern-scoped to the allowlist; the #218 confirm tier and non-company
  downrank still govern everything else.
- **Noise exemption over-reach** — exact normalized-name match only,
  before the length rule; the fragment class is test-pinned to still
  die.
- **Edge-type proliferation** — listed_in is deterministic
  (ticker-derived), never chatter-extracted; `noise_target`'s COUNTRIES
  class already blocks country mentions as relation targets, so the two
  directions cannot cross-contaminate.
- **Drift / snapshot** — entity inserts trip the DuckDB drift gate:
  graph-rebuild in-slice; the snapshot step stays user-held (doctrine).

## 6. Non-goals

- B2 relation sidecars (rejected in #218 §3) and listed_on_index edges
  (deferred in #218 §7 — 850 India-exchange tickers prove exchange
  listing, not index membership).
- Person/speaker nodes (D6): re-assessed 2026-09-09 and rejected by the
  user — speakers are non-famous and name-colliding (the 78 cross-entity
  speakers decompose into common-name collisions, attribution leaks, and
  a handful of globally-known figures); the quotes table stays the
  person layer. Guidance-event reification was re-assessed the same day
  and also rejected (unary facts; magnitude/period already live in the
  events table).
- Other tag namespaces (business_model 24 values, risk_investment 45) —
  slop audit out of scope; geography only.
- Alphabet/Google duplicate entities (found during the survey) — a
  separate data fix, not this arc.
- Widening the institution resolver beyond {RBI, SEBI}; other regulators
  (TRAI, CCI, NCLT) wait for mention-volume evidence.
- Trigger-gated backlog untouched: HNSW macros, onager, OpenViking,
  Security Phase 4.

## Execution Results (2026-09-09, in progress)

**C1 — LANDED.** `helpers/core/countries.py` (shared vocabulary module) +
`helpers/graph/derive_countries.py` (producer, dry-run default) + the
`derive-countries` make target. Applied: **21 country entities, 930
listed_in edges** (850 india; 41 usa after the 64 plain symbols were
hand-audited — 23 ADR/OTC overrides re-bucketed to home markets, ticker
trail in edge properties), **235 no-ticker companies worklisted** to
`findata/Misc/country_worklist.json` with the geography-tag hint column.
Triage guard: `load_entity_names` excludes country rows (test-pinned).
DuckDB: `v_country` + `e_listed_in` materialized out-of-registry (the
exposed_to precedent), `_SCHEMA_VERSION` 13 → 14, verified live (21/930
rows). Zero unmapped dotted suffixes.

**C2 — LANDED (applied 2026-09-10 at the operator checkpoint).**
`helpers/maintenance/geo_converge.py` (line-surgery editor, dry-run
default — no YAML round-trip). Dry-run over the live vault: **947/1,165
notes would change** (815 key_add, 150 tag_converge, 94 slop_drop, 55
key_drop, 38 key_fix). Source prevention landed: sync_tags gains the
company geography whitelist (the A3 activation — violations dropped +
warned, sectors exempt), `render_stub` seeds geography from the ticker
(unlisted companies now carry NO geography carrier), verify_notes
vocabulary = country set ∪ {global} with the regional/scope values
removed (drift tripwire), OKF description updated + frontmatter_keys.md
regenerated. **Scope decision (measured):** sector-note coverage tags
(31 india / 15 global, zero slop) are deliberately NOT converged. The
mass note rewrite is blocked on the house permission checkpoint; re-run
`geo_converge.py` bare to regenerate the diff.

**I1 — LANDED.** RBI + SEBI institution entities (bare rows, idempotent
ensure on --apply; 2 inserted). Pattern-scoped resolution:
institution-lane captures resolve via the exact allowlist
(`resolve_institution`), never the company resolver — test-pinned
("tie-up with RBI" goes to the sidecar, not a jv_with edge).
`noise_target` gains the exact-name exemption {rbi, sebi} ahead of the
fragment-length rule ("RBI" was silently discarded at write time). The guard
carve-out from #218 was assessed and is UNNECESSARY: the alias path
cannot reach institution targets (exact-match allowlist, no fuzzy) —
recorded so the question does not reopen.

**I2 — LANDED (9 patterns: 7 institution-lane + 2 rated_by).** Rated-by
captures restricted to CRISIL and ICRA (both exist as companies; CARE /
India Ratings / Moody's / Fitch probed and absent — excluded until
recurrence evidence, the #217 rule-candidate doctrine). Harvest
2026-09-09: 115 extracted, **9 new-lane edges landed** — regulated_by
Canara Bank / ICICI Bank / Muthoot Finance / SBI / TeamLease → RBI;
approved_by BSE → SEBI and ICICI Bank → RBI; rated_by Muthoot Capital
Services and Muthoot Finance → CRISIL; zero penalized_by matches.
Triage: 10 prose rows, all adjudicated discard (generic classes,
locations, programs, one word-overlap alias FP) — persisted via
`relation_noise.json` (38 entries); re-extract verified unresolved=0
(no re-entry). Institutions 205 → 207; DuckDB rebuilt.

**C2 apply (2026-09-10).** 947 notes rewritten; idempotency re-run = 0.
A real bug surfaced on the first apply and was fixed before anything
shipped: the editor's key pass used a STALE line index when the tag pass
had deleted lines above the geography key (live on Hisense: the key-drop
ate `listed: false` and kept the global key). Fix: the key pass re-locates
the key line after the tag pass; regression-pinned (TestStaleIndexGuard);
the Companies tree was `git restore`d and re-applied deterministically,
then every one of the 947 changed notes was byte-verified against the
fixed plan over its HEAD version — 0 mismatches. Post-apply: sync-tags
rebuilt (company geography tags = 21 country values only, zero slop;
sector coverage rows untouched), verify_notes 1,217/1,217 clean.

**Arc close (2026-09-10).** Gates: `make qa` 9/9, `make perf` 22/22,
`make advisory` 10/10 (after index convergence). Gate debt fixed en
route: make-help registration for derive-countries (alphabetical —
after derive-co-mentions), 'country' added to both fileless-entity
exemption sets and the integrity checker's v_node expected-count list,
the 4 new edge types registered in `_KNOWN_EDGE_TYPES`, a shebang for
helpers/core/countries.py, ty narrowing + a C901 split in geo_converge
(`_plan_tag_pass` / `_plan_tag_worklist` / `_plan_key_pass`), and a
snapshot refresh. One environment incident: transient gate failures were
traced to /tmp quota exhaustion (2.0G pytest dirs + 4×270M stray tmp
dirs from interrupted sweeps — cleaned; not gate debt).

## Appendix — raw measurement log

| Date | Command / probe | Result | Notes |
|---|---|---|---|
| 2026-09-09 | sqlite ro-URI: tag prefix census on entity_tags | geography 1,229 rows / 11 values (see §2) | read-only peek |
| 2026-09-09 | rg `^geography:` over findata/Companies | 198 notes: india 95, global 93, usa 9, south_korea 1 | 967 without the key |
| 2026-09-09 | sqlite ro-URI: ticker suffix buckets (companies) | india 850, no-ticker 235, plain 64, .KS 3 / .SG 2 / .PA 2 / .TW 1, other 8: 060230.KQ, 1MAT.MI, 1810.HK, 601138.SS, 2222.SR, 4188.T, HUH1V.HE, HEI.DE | matches #218 §7 |
| 2026-09-09 | sqlite_master DDL for entities | entity_type CHECK absent (company-suffix guard only) | no migration for new types |
| 2026-09-09 | rg geography in doc/okf/frontmatter.company.v1.json | pattern `^[a-z_]+$`, open vocabulary, global only in description | no enum bump needed |
| 2026-09-09 | Read triage_pending_relations.py:181-195 | `noise_target`: len < 4 gates "RBI" | write-time, before the sidecar |
| 2026-09-09 | sqlite ro-URI: institutions census | 205 rows, 0 with file_path; top degrees Vanguard 44, Blackrock 42 | enrich_relations producer |
| 2026-09-09 | rg counts over The_Chatter + Points_And_Figures | RBI 139, SEBI 72, BSE 60, CRISIL 16, NSE 10, upgrade 123, downgrade 6 | mention sites |
| 2026-09-09 | sqlite ro-URI: quotes speaker analysis | 1,384 distinct speakers; cross-entity set = name collisions + attribution leaks + famous few | D6 rejection evidence |
