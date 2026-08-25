# Proposal: Pending-relations triage (`triage_pending_relations`) — enclose the recurring queue workflow

**Date**: 2026-08-25
**Status**: EXECUTED 2026-08-25 (S1–S3)
**Depends on**: `extract_relations.py` sidecar semantics (`_ALIASES`,
`_GENERIC_WORDS`, Unresolved rows), `suggest_relations.py --append`,
`helpers/core/fuzzy_match`.
**Motivation**: the third manual triage of `findata/_pending_relations.txt`
in a month (cleared to 0 in Aug 2026; refilled to 689 lines / 547 unique
after two newsletter editions + a suggestions dump). Every pass redoes the
same mechanics: dedupe, split populations, bucket, eyeball, apply.

---

## 1. Problem statement

The sidecar conflates two populations and accretes noise:

| Population | Aug-2026 fill | Nature |
|---|---|---|
| `suggested` rows | 478 lines (~461 unique) | link-prediction REVIEW candidates (C2 `suggest_relations --append`) — not extraction misses |
| prose-extracted rows (`jv_with`/`supplier_to`/`acquired`/`subsidiary_of`/`competes_with`) | 211 lines (~86 unique) | true unknown-target queue |
| append-only re-run duplicates | 128 lines | the sidecar never dedupes itself |

Of the ~86 unique prose rows, measured buckets: ~32 discard-outright
(countries, generic phrases — "EPC contractors but", "Vendor Partners" —
mangled fragments — "Brookfield effectively", "Exom earlier"), a handful of
alias-table candidates (target exists under another spelling), a few
stub-worthy companies, and out-of-corpus foreign parents whose names the
mention window truncated away ("subsidiary_of → Japan" for Escorts Kubota).

The handling workflow exists only in an agent's head + a memory note. It
must be a script.

## 2. Goals / non-goals

### Goals
- G1. One command produces the full triage state: deduped, population-split,
  bucketed rows with quotes — a report for human eyeballing plus a
  decisions file the human/agent annotates. Non-destructive by default.
- G2. One command applies annotated decisions: alias rows land in a
  runtime-loaded alias file (no code edits per cycle), discards counted,
  `suggested` rows moved to their own review file, remaining unresolved
  prose rows kept (deduped) in the sidecar.
- G3. Inflow fixes: obvious noise (countries, generic-phrase targets) never
  reaches the sidecar; `suggest_relations --append` writes its own file.
- G4. The loop ends the house way: after stub/alias fixes, re-run
  `extract_relations --apply` → roster sync (if stubs) → `make
  graph-rebuild` → snapshot; script prints the exact chain.

### Non-goals
- No auto-stubbing from decisions (stub creation stays explicit — the
  collision-check discipline; the script only *plans* them).
- No changes to edge patterns or the resolver's matching itself.
- No UI; CLI + files.

## 3. Design

### 3.1 Data files
- `findata/_pending_relations.txt` — unchanged semantics (gitignored queue),
  but now holds ONLY prose-extracted rows, deduped on rewrite.
- `findata/_pending_suggestions.txt` — new home for `suggested` rows
  (gitignored, same JSONL shape; `suggest_relations --append` default).
- `findata/relation_aliases.json` — NEW git-tracked curated alias map
  `{"<lowercased mention>": "<existing entity name>"}`, loaded at
  extract_relations import and merged over `_ALIASES` (file wins). Entries
  pointing at non-existent entities are warned + skipped (the silent-break
  trap). Triage `--apply-decisions` writes here.
- `findata/_pending_triage_report.md` + `findata/_pending_triage_decisions.jsonl`
  — emitted by `--report`; gitignored scratch (regenerable).

### 3.2 Script: `helpers/graph/triage_pending_relations.py`
- `--report` (default): parse sidecar → dedupe by
  (edge_type, source, normalized target) → split populations → bucket
  prose rows via the deterministic classifiers:
  `country` (fixed set) / `generic` (prefix regex + `<4` chars) /
  `alias_candidate` (fuzzy_match against entity names, method reported) /
  `stub_candidate` (≥2 capitalized tokens AND a company-ish suffix or
  capitalized-pair shape) / `manual` (rest). Sources are validated to exist
  (unknown sources flagged, not bucketed). Emits report + decisions file
  with `decision: null` per row; prints the summary table.
- `--apply-decisions [--decisions PATH] --write`: validate every non-null
  decision (`discard` | `alias:<ExistingName>` | `stub` | `skip`);
  alias targets must resolve to entity names. With `--write`: rewrite the
  sidecar (drop applied rows, move `suggested` rows to their file, keep
  unresolved rows deduped) + persist alias additions. Without `--write`:
  dry-run of exactly that. Prints the follow-up chain (re-run extract,
  roster sync if stubs, graph-rebuild, snapshot).
- `--clear`: truncate the sidecar to 0 (the house "queue cleared" endgame;
  explicit).

### 3.3 Extractor inflow (extract_relations.py)
- Noise gate before sidecar writes: skip Unresolved rows whose target is a
  country (fixed set) or matches the generic-phrase regex — the measured
  ~35-row class.
- Alias-file load per §3.1 (merge over `_ALIASES`; warn+skip bad targets).

### 3.4 Suggestor split (suggest_relations.py)
- `append_suggestions` default path → `findata/_pending_suggestions.txt`
  (same dedup semantics); docstring + Makefile help updated. The triage
  script's split handles the backlog in flight.

## 4. Implementation slices

| Slice | Content | Gate |
|---|---|---|
| S1 | triage script (report + apply-decisions + clear) | pytest: fixture sidecar + seeded entities (dedupe, split, buckets, alias validation, rewrite keeps unresolved) |
| S2 | extractor noise gate + alias-file load; suggestor path split | pytest: existing extract/suggest suites + new cases |
| S3 | Makefile `triage-relations`; markdown_parse.md Stage 9 loop | make -n + help-alphabetical test |

## 5. Risks
- Classifier false positives → buckets are advisory; the decisions file is
  the authority, `--write` only acts on annotated rows.
- Alias file drift vs `_ALIASES` → file wins, load-time validation;
  documented in the extractor comment.
- Rewriting the sidecar could lose rows → rewrite is keyed to the
  decisions file's row ids; unknown/unparsed lines are preserved verbatim.

## 6. Success criteria
- Current backlog: one `--report` + one annotated `--apply-decisions
  --write` reduces the sidecar to ≤ the genuinely-manual residue (target:
  <30 rows), with `suggested` rows out of the extraction queue.
- Re-running `--report` on a fresh post-extract sidecar produces zero
  country/generic rows (the inflow gate works).
- The whole loop is documented in `doc/procedures/markdown_parse.md`
  Stage 9 — no head-only knowledge.
