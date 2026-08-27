# Proposal: Accept path for pending relations — apply suggested + known-target rows to `graph_edges` (S4)

**Date**: 2026-08-27
**Status**: EXECUTED 2026-08-27. Live apply: **24 accepted edges** written to
`graph_edges` (of 26 planned — SAIL jv and Groww↔Fisdom were pre-existing
rows, correctly no-op'd by INSERT OR IGNORE); **3 stubs** created via
`parse_newsletter.create_entity` (Sumitomo Mitsui Banking Corporation/Banking,
Piramal Enterprises/Diversified, McDonald's/FMCG); `extract_relations --apply`
then resolved Yes Bank↔SMBC, Piramal Finance→PEL and Dixon→Q Tech India with
proper derive-provenance. Three durable aliases added (`sail`, `micron`,
`mcdonald`) so recurring mentions resolve instead of refilling the queue —
sidecar rows are append-only between triages, which is why discards appeared
to "never resolve": unresolved mentions re-enter on every full-corpus extract
unless aliased, stubbed, or noise-gated. TACO stub HELD (corpus only ever
says "TACO"; self-heals back into the queue once named). Tests 18 passed;
ruff clean. Roster sync + `make graph-rebuild` done; snapshot deferred to
session end per user.
**Depends on**: `pending_relations_triage` (S1–S3, archived
`../archive/graph/pending_relations_triage.md`) — `triage_pending_relations.py`
decision machinery; `extract_relations.py` INSERT discipline.
**Trigger**: user 2026-08-27 — the archived task *"has not resolved things at
all"*; `suggest_relations.py` "shows tons of entries"; `_pending_suggestions.txt`
cleared by mistake (recovered same day via idempotent `--append`, 100 rows);
*"lets look at this whole task again since it could enrich our graph"*.

---

## 1. Problem statement

Two enrichment paths dead-end in sidecar files:

1. **`suggested` rows (link-prediction candidates) have no exit.**
   `suggest_relations.py` ranks missing pairs (onager link prediction over the
   DuckDB cache) and appends JSONL to `findata/_pending_suggestions.txt`. The
   design said "the human assigns a typed edge during triage" — but the triage
   decision schema only knows `discard | skip | alias:<target> | stub`. Nothing
   consumes the file; it can only regrow (100 rows at score ≥ 0.3 today, 461
   unique at the Aug-2026 dump). The high band looks genuinely enriching
   (Future Lifestyle↔Page Industries 1.0, ITC↔Philip Morris 1.0, Delhivery↔
   Snowman 0.78 — mostly missing `competes_with`-class edges between
   same-sector companies).
2. **Prose rows with a KNOWN true target cannot be accepted directly.**
   The queue holds mangled-mention rows whose target we can name (e.g.
   `jv_with John Cockerill India -> "SAIL"` where the entity is **Steel
   Authority of India**; `supplier_to Inox India -> "Micron"` → **Micron
   Technology**). The only mechanised route is `alias:<target>` + a full
   `extract_relations --apply` re-run — which works for word-mentions but is
   the wrong shape for (a) phrase-length mangled mentions
   (`alias:"groww to broaden its wealth management vertical" → Groww`
   pollutes the alias namespace) and (b) one-off rows from dumps that no
   extraction pass will ever reproduce.

## 2. Design

One new decision action in `triage_pending_relations.py`, applied through the
existing decisions-JSONL workflow:

- **`accept:<edge_type>`** — target = the row's `target_mention`, which must
  resolve to an existing entity (link-prediction rows always name real nodes).
- **`accept:<edge_type>:<Target Entity>`** — explicit target override for
  mangled prose mentions (e.g. `accept:jv_with:Groww`).

Validation: `edge_type` ∈ the graph's relation vocabulary
(`competes_with, jv_with, supplier_to, customer_of, acquired, subsidiary_of,
same_group, co_mentioned_in, semantic_peer, invested_in, exposed_to, cited_in`);
target ∈ `load_entity_names()` (FK-safe); `source != target` is enforced by the
table's CHECK anyway.

Write path (mirrors `extract_relations` exactly):

- `INSERT OR IGNORE INTO graph_edges (source, target, edge_type, properties,
  source_ref, symmetric, valid_from) VALUES (…)` — idempotent by the table's
  UNIQUE constraint; per-row try/except counting integrity skips like the
  extractor.
- `symmetric = 1` for `jv_with, same_group, competes_with, co_mentioned_in`.
- `properties = {"edition": <row edition>, "origin": "link_prediction",
  "score": X, "method": Y}` for suggestion rows; `{"edition": …,
  "origin": "manual_triage"}` for prose rows. `source_ref = "triage:accept"`.
- Connection via `helpers.core.db.connect()` (FK pragmas ON — raw
  `sqlite3.connect` is the house trap), behind a monkeypatchable
  module-level `EDGE_DB_PATH` (the VAULT_ROOT lesson) so tests stay hermetic
  against the conftest `_UNIT_SCHEMA`.

Plumbing:

- `build_triage` also reads `_pending_suggestions.txt`, so decisions may
  reference rows from EITHER file (keys are unique across both). The files
  stay separate — the population-split lesson (§1 of the S1–S3 doc) stands;
  the report gains a suggestions summary (count + top-10 preview) instead of
  merging them into the prose buckets.
- `_apply_write` drops decided rows (accept **and** discard) from the
  suggestions file; the sidecar rewrite treats accept keys as applied.
- The follow-up chain print already mandates `make graph-rebuild` (the DuckDB
  drift gate fires on out-of-band `graph_edges` writes) + `make snapshot` —
  accepted edges ride the same cascade as extracted ones.

## 3. Alternatives considered

- **Route everything through `alias:` + re-extract** — wrong shape for phrase
  mentions (namespace pollution) and impossible for suggestions (no extraction
  pass produces them; they come from graph topology, not prose).
- **Standalone accept script** — duplicates the decisions/validate/plan
  machinery and splits the workflow in two; the decision schema is the right
  home.
- **Auto-accept above a score threshold** — rejected: the 0.5–0.7 band mixes
  real peers with coincidental neighbourhood overlaps (GitLab↔PayTM 0.6);
  human review stays in the loop, the tooling just removes the dead-end.

## 4. Tests (`tests/test_triage_pending_relations.py`)

- accept writes the expected row into a tmp `graph_edges` (conftest
  `_UNIT_SCHEMA` + monkeypatched `EDGE_DB_PATH`), with symmetric flag,
  properties JSON, source_ref.
- re-apply is a no-op (UNIQUE/INSERT OR IGNORE) and drops the row from the
  suggestions file.
- `accept:<type>:<Target>` override writes to the named entity.
- invalid edge_type / unknown target → validation failure, nothing written.
- discard removes a suggestions row without writing an edge.

## 5. Risks / rollback

- Pure additive tooling; no pipeline behaviour changes. Rollback = revert the
  module + tests.
- The drift gate makes `graph-rebuild` mandatory after any apply — already
  printed as the follow-up chain.
- Wrong accepts are ordinary `graph_edges` rows: deletable, and the next
  snapshot publishes the fix.

## 6. Execution checklist

1. [ ] `accept` action: validation + plan + writer + suggestions-file drops.
2. [ ] Tests green (targeted file run).
3. [ ] Draft decisions for the 27-row prose queue + the 100-row suggestions
       dump (score-bucketed) for user review — apply only on user go.
4. [ ] Follow-up chain after apply: stubs → `sync_sector_wikilinks` →
       `extract --apply` → `make graph-rebuild` → `make snapshot`.
5. [ ] Lifecycle: Status EXECUTED → `../archive/graph/` + completed.md entry
       + search trio rebuild.
