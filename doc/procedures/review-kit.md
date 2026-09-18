# Procedure: The Review Kit (journaled sitting workflow)

**Date:** 2026-09-19
**Proposal:** `doc/improvements/archive/tooling/review_kit.md` (S1)
**Scope:** `helpers/core/review_kit.py` — the shared sitting spine every
triage queue rehomes onto. First client: `seed_nic2008.review`. Follows
`archive/database/nic2008_seed_table.md` (completed.md #246).

## What the kit owns

| Piece | API | Notes |
|---|---|---|
| Journal | `Journal(path, session=...)` | session-bounded JSONL, buffered, flushed as one sitting block on `end()` |
| Parking read-back | `latest_action_by(path, key_field=...)` | latest terminal action per item id wins |
| Lane assembly | `assemble_entries(wl, ...)` | open walk by default; `--redecide`/`--skipped`/`--labels` inclusion rules |
| Sitting spine | `ReviewSession(...).run()` | render -> ask -> dispatch; batch confirm is the only write gate |

The kit NEVER writes domain data — `apply_batch` delegates to the
queue's existing validated machinery.

## Journal convention (stable across queues)

One consolidated JSONL per queue, **append-only, never rewritten**.
Default location: `outputs/<queue>/journal.jsonl`.

- Every line carries `ts` and `session` (the sitting id,
  `YYYYMMDD_HHMMSS`).
- A sitting writes exactly one block:
  `sitting-start` line, then decision lines in order, then a
  `sitting-end` line with the sitting's approved count.
- Decision lines carry the item key (domain-named field, e.g. `label`)
  and `action`:
  - terminal: `approve`, `skip` — these count for parking;
  - control: `quit`, `abort` (not terminal — a quit sitting leaves the
    item open for the next sitting);
  - domain attempt lines, conventionally `bad-*` (e.g. `bad-override`),
    journaled via the `note()` callback as they happen;
  - derived lines (e.g. `supersede-previous`) emitted by `post_approve`.
- Read-back keys on the item id with LATEST terminal action wins: an
  item whose latest terminal action is `approve`/`skip` is parked
  (`skipped` lane) or done (`promoted` lane) when the producer refills
  the queue — decided once, never re-asked.
- **Item ids must be stable across producer cycles** — parking is only
  as good as the id. A producer that changes id shape re-opens its
  queue.
- The journal is evidence, never an undo log: replays are new sittings.

## Clients

| Queue | Verbs | Apply lane |
|---|---|---|
| NIC promotion (`seed_nic2008.review`) | `1/2/3` ranked pick, `c CODE:mt` override, `s`, `?`, `q`, `x` | `seed_concepts.promote` + operator-override upsert |
| relations triage (`triage_pending_relations --review`, S2) | `a[/TYPE[/TARGET]]` accept, `d` reject->noise, `al NAME` alias, `st` stub, `p` park; any verb may carry `\| free note` | sitting only APPENDS decision rows to the decisions file; `--apply-decisions` (unchanged) stays the apply step |
| quotes triage (`triage_pending_quotes --review`, S3) | `1/2/3` pick suggestion, `al NAME` alias, `st` stub, `sc CIN` stub+cin (parse-validated at ask), `d`, `p`; VSS hints render from a prior report, never recomputed | same append-only contract; `--apply-decisions` (unchanged) merges aliases, marks worklist entries decided |

The relations sitting keys parking on the stable `_row_id`; noise-merged
discards additionally stay suppressed across producer cycles via the
existing noise gate (id-drift guard). Journal:
`outputs/relations_review/journal.jsonl`, item field `id`.

## Export-side parking (S4 — queues with no in-tool apply lane)

`country_worklist`, `subsector_worklist`, `counterparty_worklist` are
consumed by hand elsewhere (ticker fills, taxonomy decisions, entity
creation), so they get no sitting — they get the PARKING lane only:

```bash
python3 helpers/misc/worklist_park.py --queue countries --id "AWL Agri Business"
python3 helpers/misc/worklist_park.py --list
```

- Journals: `outputs/worklist_parking/<queue>/journal.jsonl` (kit
  format; `park_items` writes one sitting of `skip` lines).
- Producers suppress parked items on export and report
  `"parked": N` in the payload — decided once, never re-asked.
- Reopen = remove the sitting block (append-only evidence, no undo).
- `retro_resolution_worklist` (D17) is deliberately NOT wired: it is a
  one-shot curation artifact (its own decisions are embedded as
  `curation_2026_09_15`), not a refill-cycle queue.

## Driving the queues (operator runbook)

### Universal contract

- One keypress per item. `?` re-renders evidence, `q` quits to the batch
  review, `x` stops the walk. The **batch confirm is the only write gate**.
- `--dry-run` journals the sitting (approve/skip lines land as
  non-terminal `preview-*` actions) and prints the batch but writes no
  queue files — explore freely, previews never park an item.
- A REAL sitting whose confirm was declined leaves terminal `approve`
  lines that park the items — reopen them with `--skipped` (parked and
  declined rows both live there).
- Journals are append-only evidence; to un-park, delete the item's
  sitting block from `outputs/<queue>/journal.jsonl` (no undo command).

### Queue lifecycle — who refills, who closes

| Queue | Refilled by | Drive | Apply | Closes when |
|---|---|---|---|---|
| NIC promotion | new industry labels (derive) | `seed_nic2008.py review [--skipped\|--redecide]` | in-sitting confirm (promote lane / operator override) | decisions journal; worklist lanes re-partition |
| relations | `make derive-relations` + `suggest_relations.py` | `triage_pending_relations.py --review` | confirm appends rows; `--apply-decisions` (separate, unchanged) | apply drops decided rows from the sidecar; discards persist as noise gates |
| quotes | `quote_coverage_audit.py` (re-opens still-unresolved canonicals, auto-resolves the rest) | `triage_pending_quotes.py --review` | confirm appends rows; `--apply-decisions` (separate, unchanged) | apply marks entries decided; the audit flips them `resolved` once the alias resolves |
| countries / subsector / counterparty | `derive_countries` / `derive_hyperedges` (maint chain) | `worklist_park.py --queue <q> --id <name>` | none (hand-consumed elsewhere) | producer suppresses parked items, stamps `"parked": N` |

### Keys cheat sheet

| Surface | Keys |
|---|---|
| NIC | `1/2/3` ranked pick · `c CODE[:matchType]` override · `s` park · `?` · `q` · `x` |
| relations | `a[/TYPE[/TARGET]]` (override target DB-resolves: `a//kec`) · `d` discard→noise · `al NAME` · `st` stub · `p` park; any verb + `\| free note` |
| quotes | `1/2/3` suggestion pick · `al NAME` · `st` · `sc CIN` (parsed at keypress) · `d` · `p` |
| parking | `--id NAME...` (repeatable), `--list` to inspect |

### Evidence guardrails (why the screens show what they show)

- **Suggestions are semantic neighbors, not identities** (D4, both
  triage tools): the VSS/jaccard hint matches the DOMAIN, not the
  company — e.g. `Oil and Natural Gas Corporation` once suggested
  `Vedanta Oil and Gas` (same sector, different company). Hints are
  never pre-filled as decisions; verify before `1/2/3`.
- **NIC CIN vintage split** (`post-2008: n, pre-2008: n`): same nic5 can
  mean different things per CIN series (the 65110 trap) — read the
  split before approving a code.
- **relations word-overlap marker** (`_confirm? word-overlap alias_`):
  the mention overlaps an existing entity name — confirm it is that
  entity before accepting.
- **reverse-capture marker** (relations): the mention is the edge
  SOURCE; accept handles the swap — sanity-check the printed spec.

### Familiarization drive (all dry-run, zero queue writes)

```bash
.venv/bin/python3 helpers/misc/worklist_park.py --list
.venv/bin/python3 helpers/graph/triage_pending_relations.py --review --redecide --dry-run --limit 3
.venv/bin/python3 helpers/misc/seed_nic2008.py review --skipped --dry-run
.venv/bin/python3 helpers/graph/triage_pending_quotes.py --review --dry-run
```

(The quotes queue is empty unless the audit just re-opened canonicals;
re-run `quote_coverage_audit.py` to refill it.)

### Where things land

| Artifact | Path |
|---|---|
| NIC journal / decisions | `outputs/nic_review/journal.jsonl` / `concept_mappings` (DB) |
| relations journal / decisions | `outputs/relations_review/journal.jsonl` / `findata/Misc/_pending_triage_decisions.jsonl` |
| quotes journal / decisions | `outputs/quotes_review/journal.jsonl` / `findata/Misc/quote_triage_decisions.jsonl` |
| parking journals | `outputs/worklist_parking/<queue>/journal.jsonl` |
| worklists (writer-owned, committed by operator) | `findata/Misc/{quote_entity_worklist,country_worklist,subsector_worklist,counterparty_worklist}.json` |

## Adding a queue (S2-S4 pattern)

1. Assemble the worklist with `assemble_entries` (lanes:
   `suggested`/`promoted`/`skipped`/`no_signal`, item key `label`).
2. Provide callbacks: `render` (header + evidence), `ask` (domain
   keypresses, re-ask on invalid input, `note()` for `bad-*` lines),
   `spec_of`, and `apply_batch` delegating to the queue's existing
   apply machinery.
3. Wire the CLI flags through: `--labels`/`--redecide`/`--skipped`/
   `--limit`/`--dry-run` (`apply=False`).
4. Tests: domain suite stays the regression harness; add kit-semantics
   coverage in `tests/test_review_kit.py` only when a NEW spine
   behavior is introduced.
