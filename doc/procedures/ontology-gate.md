# Ontology change gate — operating procedure

The deterministic gate that answers "did this ontology-affecting change
move anything it shouldn't?" — ontology_governance S2. Runner:
`helpers/misc/ontology_eval_gate.py`; frozen question set:
`helpers/misc/ontology_questions.json` (co-located — the
`embed_eval_questions.json` precedent: a frozen eval set lives beside
the tool that consumes it, reviewed in patches like code). Normative
context: `doc/design/ontology.md` §4/§6.

## When to run (mandatory)

Any change that alters query-visible semantics — edge/event/hyperedge
rosters, crosswalks, hierarchies, extractor rules, identifier
resolution. The rule lives in `doc/templates/proposal.md` §4 and
`doc/improvements/proposals/README.md`: the proposal's acceptance
criteria MUST carry an eval-gate bullet. Run it on the disposable copy
between dry-run and canonical apply:

```bash
# what changed and shouldn't (undeclared) — run first:
.venv/bin/python3 helpers/misc/ontology_eval_gate.py \
    --parent /tmp/pre.db --candidate /tmp/post.db

# what you INTENDED to move — declare it, then the gate demands it move:
.venv/bin/python3 helpers/misc/ontology_eval_gate.py \
    --parent /tmp/pre.db --candidate /tmp/post.db --expect-improve qid1,qid2
```

Accept rule (EvoOntology-derived, translated for row-sets): ACCEPT iff
zero regressions AND no undeclared changes AND every declared
improvement materialized. A declared change that only LOSES rows is a
regression, not an improvement. Exit codes: 0 ACCEPT, 1 REJECT, 2
usage/set error.

## The question file

JSON: `version` (int, bumped only by rebaseline), optional `draft`
(true = the gate refuses — the operator authors and verifies before
flipping), `_note` (provenance), `questions[]` of
`{id, shape, params, expected}` — `expected` is a sorted row-set,
computed by the gate's own `answer_question` (grading and derivation
share one code path — no gap between how an answer was frozen and how
it is re-computed).

Eight shapes, each pinning a documented surface:

| Shape | Params | Pins |
|---|---|---|
| `subtree` | `root` | concept hierarchy closure, ACTIVE-only (ontology.md §2.7) |
| `event_counterparties` | `entity`, `event_type`? | events spine + counterparty resolution (§2.3) |
| `resolve_identifier` | `value` | entities.cin then the registry, NOCASE (db_schema §identifiers) |
| `entities_of_type` | `entity_type` | entity-type vocabulary rosters (§2.6) |
| `edge_neighbors` | `source`, `edge_type` | out-edge targets per edge type (§2.1 roster) |
| `hyperedge_members` | `edge_type`, `label` | incidence-store membership (§2.5 roster) |
| `concept_mappings_for` | `source_scheme`, `source_concept`, `match_type`? | ACTIVE crosswalk targets — the one crosswalk home, D-O1 |
| `provenance_agents_for` | `table` | distinct agent_ids per fact table (db_schema §provenance) |

## Coverage map (v2 roster, 79 questions)

| Doc surface | Questions |
|---|---|
| ontology.md §2.1 — 20 edge types | `edge-*` × 19 (one per POPULATED type, top out-degree source; `penalized_by` registered but zero rows) |
| ontology.md §2.3 — 4 event types | `cp-*` × 13 (acquisition/jv coverage picks + a guidance negative pinning "guidance carries no counterparties") |
| ontology.md §2.5 — 9 hyperedge types | `hyper-*` × 9 (largest membership per type) |
| ontology.md §2.6 — 8 entity types | `type-*` × 7 (vocabulary types only; `company` excluded — thousands of churny rows, not vocabulary) |
| ontology.md §2.7 — 11 schemes | `subtree-*` × 14 (all super roots + top-3 sectors + a leaf) + `xwalk-*` × 4 crosswalk picks |
| db_schema — identifiers | `resolve-*` × 8 (6 CIN positives across vintages + 2 negatives) |
| db_schema — provenance | `agents-*` × 5 (one per agent-carrying fact table) |

**Scope boundary (measured)**: the gate pins ontology semantics —
vocabularies, crosswalks, closures, memberships, resolution — not bulk
data. Deleting a handful of edges in an unpinned neighborhood is data
drift (the integrity check + snapshot parity own that); deleting a
pinned neighborhood (e.g. a source's co-mentions) rejects immediately
with the exact lost rows.

## Re-deriving the roster (extension)

Add questions by shape+params, compute `expected` via
`answer_question` against the live store, verify a sample by raw SQL,
then run the live-vs-live self-check (must ACCEPT). New SHAPES land in
the gate runner + tests first. Regenerating wholesale re-derives from
the coverage map above — keep the map and the file in the same change.

## Rebaseline

The frozen answers are the gate's memory; after a change is ACCEPTED,
the answers it legitimately moved must be re-frozen — else every later
run reports undeclared changes against stale expectations. The
mechanics make goalpost-moving loud and auditable:

```bash
.venv/bin/python3 helpers/misc/ontology_eval_gate.py \
    --parent /tmp/pre.db --candidate /tmp/post.db \
    --expect-improve qid1 --rebaseline "completed.md #NNN (the change)"
```

`--rebaseline` runs the FULL gate first. Only on ACCEPT does it rewrite
every question's `expected` from the candidate DB, bump `version`, and
stamp `rebaselined: {at, ref}` into the file. On REJECT it refuses and
the file is untouched — you cannot move the goalposts on a failing run.
Every rebaseline is therefore: gated by an accept, stamped with the
accepting change's reference, version-bumped, and visible in git
history. Never hand-edit `expected` lists.
