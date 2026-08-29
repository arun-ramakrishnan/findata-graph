# Proposal: Documentation drift remediation — 7-day audit (2026-08-22 → 2026-08-29)

**Date**: 2026-08-29
**Status**: EXECUTED (2026-08-29, same day)
**Depends on**: nothing code-side — every fix below is prose in README.md,
`doc/schema.md`, `doc/graph_design.txt`, `doc/procedures/doc-search.md`,
`doc/improvements/completed.md` (append-only backfills). No script, schema,
or gate behavior change.
**Trigger**: audit session 2026-08-29 comparing all 36 commits in the
2026-08-22 → 2026-08-28 window (+ the mid-session commit `cca89918`) against
the evergreen doc surfaces: README, `doc/architecture.md`, `doc/schema.md`,
`doc/graph_design.txt`, `doc/procedures/*.md`, `frontend/README.md`,
AGENTS.md, Makefile help lines, and the live API route table. Method: commit
diffstats → behavior classification (CLI/make/API/published-artifact vs
internal/data-only) → doc lookup per behavior, pinned to file:line. Ground
rules: verifiable facts only; no speculation; historical logs never edited
except append-only backfills where a commit subject references a missing
entry.
**Window extension (2026-08-29, same day)**: the audit originally closed at
the 08-28 commit boundary. The uncommitted 08-29 session (zstd backup arc
#174–#177: single-snapshot, parallel per-DB snapshot, all-zstd backups,
PRE_FULL block) postdates every commit and introduces its own drift —
§2b covers it so this proposal stays the single remediation list.

---

## 1. Context

The window covered ~20 arcs; nearly all shipped their own proposal/archived
doc in the same commit, and the 2026-08-27 consistency pass (`a6d77ca9`,
#167) had already reconciled README/procedures/schema against ground truth.
The gaps that remain are concentrated where that pass's scope or the window
boundary left seams:

1. the Mojo pilot (#171), the only arc in the window with **zero**
   `doc/improvements` footprint;
2. two `_build_meta` key-list sentences whose missing keys were introduced
   one day **before** the window (`1fda0c15`, 2026-08-21) and survived the
   #167 pass because it verified the schema_version *value*, not the key
   enumeration;
3. run-log numbering: three commit subjects reference completed.md entries
   that do not exist as of HEAD;
4. (added with the window extension) the 08-29 session shipped a codec
   rename (.gz → .zst) across every backup artifact and a maint-full
   recomposition, both of which predate their own commit — evergreen docs
   still describe the gzip world (§2b).

---

## 2. Findings

### F1 — Mojo pilot undocumented in the tracked corpus (HIGH)

Commit `caad4bcd` (#171, 2026-08-27) added a new top-level surface:
`Mojo/` tree (bench_cosine.mojo, analyzer.mojo, test_cosine.mojo, bench
harness), `Makefile.mojo`, make targets `mojo-build`/`mojo-bench`/
`mojo-test`, and a pyproject `mojo` optional extra (project bumped 0.2.0).

- No `doc/improvements` file in the commit diffstat; no completed.md entry
  (headings run `## 169` → `## 173`; `rg -n "mojo" doc/improvements/completed.md`
  = 0 hits) despite the `(#171)` subject ref, which by repo convention is a
  run-log ref.
- Findings log exists only in gitignored `doc/local/mojo_pilot.md`. The
  archived `parallel_cold_embed.md` carries the *deferred-scale* Mojo
  record (revisit triggers), which mitigates but does not document the
  shipped pilot.
- README's top-level layout table (`README.md:73-85`) documents every other
  top-level dir — `helpers/pdf/`, `frontend/`, `snapshots/` — but has no
  `Mojo/` row.
- Partial mitigation already in place: Makefile help lines 59–61 / 137–144
  document the three targets.

**Update targets**: README layout table (one `Mojo/` row + pyproject-extra
note); completed.md backfill `## 171`; optionally one toolchain sentence in
`doc/architecture.md`.

### F2 — `_build_meta` key list stale in two evergreen docs (HIGH)

`doc/schema.md:190` and `doc/graph_design.txt:266-267` both state the table
holds `schema_version, built_at, source_db, generation, duckdb_version`.

The actual writer (`helpers/graph/query.py:710-718`) also stamps
`note_embed_dims` and `note_embed_model`, and `_is_warm()` reads both
(`query.py:590-603`) to catch same-dims model swaps — so the omission is
load-bearing, not cosmetic. Introduced in `1fda0c15` (2026-08-21, one day
before the audit window); the in-window #167 pass edited this exact
schema.md line (S1: schema_version "9"→"13") without fixing the key list.

**Update targets**: the two sentences above — add
`note_embed_dims, note_embed_model` to the key enumeration.

### F3 — Dangling run-log refs `#170` and `#172` (HIGH)

By repo convention `(#NNN)` in commit subjects is a completed.md run-log
ref. As of HEAD:

- `6e058011` "(#170)" (Chatter #83 ingest) — no `## 170` entry; topic search
  ("Borosil", "Chatter #83") = 0 hits. Ingest arcs have gone unlogged
  before (e.g. `a5d213a5` carries no ref), so a backfill here is optional.
- `33141b79` "(#172)" — no `## 172` entry ("typographic"/"italicized" = 0
  hits). This one is a real behavior change, not just data:
  `helpers/graph/derive_insights.py` +22 extends quote extraction to
  typographic/italicized local-engine sections, with a ~190-note backfill
  in the same commit. No tracked doc describes it anywhere.
- Related mismatch (no fix needed beyond awareness): subject refs and log
  numbers are swapped for the Aug-24/25 pair — `24543050` (market-data)
  says `#153` while completed.md `## 152` is the market-data entry;
  `91e34162` (relation enrichment E3–E6) says `#152` while `## 153` is
  Relations 2.0. Entries exist and cover the right topics; do not renumber
  (house rule: never renumber).

**Update targets**: append-only backfills `## 172` (required) and `## 170`
(optional), each marked as backfills.

### F4 — README `snapshot_db` codec branding (LOW, superseded by F7)

`cca89918` (#174) switched `snapshot_db.export_parquet_sqlite` from gzip to
zstd and added the unchanged-embed-store gz reuse. Its own archive doc +
completed.md entry cover it and no evergreen doc contradicts it; only nit:
`README.md:79` still brands snapshot_db "(gzip + Parquet)" — the gzip half
is now true only of the `db-backup/` twins.

**Update target**: `README.md:79` — but NOT the wording originally proposed
here ("+ gzip backups"): the 08-29 session renamed those backups to `.zst`
too (#175/#176), so F7's row for line 79 supersedes this finding. Kept for
audit trail.

### F5 — `note_search --check` undocumented (LOW-MEDIUM)

`40b0e43a` (#164) gave `rebuild_note_search.py --check` FRESH/STALE drift
reporting + exit-1-on-drift, symmetric with the doc/script rebuilders whose
`--check` flags each have a procedure doc (`doc-search.md`, `script-search.md`,
`embeddings.md` §E1). note_search has no procedure doc and the flag appears
in no evergreen surface; only the aggregate `make search-fresh` gate is
documented (AGENTS.md).

**Update target**: a short note-search block in `doc/procedures/doc-search.md`
(§Drift reporting) covering all three rebuilders' `--check` contract, or a
standalone `doc/procedures/note-search.md` if the user prefers symmetry.

### F6 — User actions, not doc edits (flag only)

- ~~`cca89918` (maint-full single snapshot + zstd codec, logged as `## 174`)
  has an **empty commit subject**~~ — superseded by F10: the work now lives
  in two still-empty-subject stgit patches (`maint_optimizations`,
  `backup_enhancements`); see §2b.
- Snapshot provenance (`118efc89` + `0fbb8a43`): verified the git-tracked
  `snapshots/parquet/duckdb/_build_meta.parquet` now carries
  `source_db = memory/research.db` (repo-relative). Historical blobs still
  carry the absolute path inside old `_build_meta.parquet` history (per the
  commit message) — relevant to the identity-scrub playbook
  (`doc/local/git_identity_scrub.md`), which can be updated to record that
  the forward leak is closed and only history remains.

### Checked clean (no action)

- README API table (`README.md:148-161`) matches all 30 live `app.py`
  routes.
- `doc/procedures/embeddings.md` fully documents #173 (parallel cold-embed
  pool, measured numbers, constants).
- doc-search/script-search procedures + AGENTS.md updated in-commit for
  `search-fresh` / advisory rows (`f7ebe790`).
- Embed-store path references current everywhere checked post-#166.
- `frontend/README.md` is a build/layout doc — the #168 UI polish
  (reader modes, focus, chips) does not stale it.
- `doc/graph_design.txt:243` UI line still substantively true (cytoscape
  ego network, typeahead, shortest-path tool all exist post-#145-146/#168).
- Google Finance fallback: reachable via documented
  `make relations-enrich ARGS="--source googlefinance …"`; no evergreen doc
  states a market-data doctrine it would contradict.
- Temporal analytics (#150), doc-search (#148/#149), script-search (#154),
  GF fallback (#153→log #152), enrichment (#152→log #153): all documented
  in their own archived proposals + completed.md entries.

---

## 2b. Post-window findings (2026-08-29 session, uncommitted at audit time)

The 08-29 session shipped #174–#177 (single TIER1-snapshot elision,
parallel per-DB snapshot, all-zstd backups incl. the snapshot binary
branch `.gz`→`.zst`, tiny-backup fixture-leak fix, garbled maint report
fix, PRE_FULL block + `doc/procedures/maintenance.md`). None of it is in
a commit yet, so the commit-window method above cannot see it; this
section is the same class of finding, gathered by reading the session's
surfaces directly. completed.md now carries `## 174`–`## 177` (unique
numbers preserved — the F3 backfills `## 170`/`## 171`/`## 172` are
unaffected and still required).

### F7 — gzip/`.gz` branding + one broken restore instruction (HIGH)

#175/#176 renamed every `db-backup/` artifact to `.zst` (stdlib
`compression.zstd`, no explicit level — the level policy is §2 of the
archived `zstd_binary_backups.md`). Evergreen docs still describe the
gzip world; one reference is a **broken restore instruction**, not just
branding:

| File:line | Stale text | Why wrong |
|---|---|---|
| `README.md:79` | `snapshot_db` "(gzip + Parquet)" | parquet = zstd (sqlite side) since #174; F4 superseded |
| `README.md:84` | "runtime DB + local gzip scratch" | all db-backup artifacts are `.zst` |
| `README.md:98-99` | `gzip -dc db-backup/*.gz > memory/…` | **no `.gz` files exist** — restore is `zstd -dc db-backup/research_backup.db.zst` |
| `README.md:175` | "versioned gzip + Parquet snapshots" | same as :79 |
| `doc/architecture.md:35` | "gzip copies under `db-backup/`" | `.zst` copies |
| `doc/architecture.md:44` | "gzip snapshots + raw `*_backup.*` copies" | no raw copies remain either (all compressed) |
| `doc/schema.md:232` | "local gzip copies" | `.zst` copies |
| `doc/graph_design.txt:278` | "gzip copies into git-ignored…" | `.zst` |
| `doc/graph_design.txt:345` | `db-backup/ *.snapshot.*.gz` | artifact glob is `*.snapshot.*.zst` |
| `doc/procedures/doc-search.md:92-93` | `embed_store_backup.db`, `embed_store.snapshot.db.gz` | actual: `embed_store_backup.db.zst`, `embed_store.snapshot.db.zst` |
| `doc/procedures/doc-search.md:96` | "copy `doc_search_backup.db` back into `memory/`" | artifact is `doc_search_backup.db.zst` — needs `zstd -dc`, not a plain copy |
| `doc/procedures/embeddings.md:130` | "gzip/raw backup twins" | zstd twins, no raw |

**Update targets**: mechanical s/gzip/zstd/ + `.gz`→`.zst` per row; the
README quickstart line is the one that must be functionally correct
(`zstd -dc`), not just descriptive.

### F8 — maint-full recomposition + new procedure doc unindexed (MEDIUM)

#177 moved `sync-tags` + `rebuild-note-search` into a new PRE_FULL block
(they now run BEFORE the recovery backup) and added
`doc/procedures/maintenance.md` (composition doctrine, backup-vs-snapshot
semantics, placement invariant). `doc/architecture.md` was updated
in-session (row 99 + procedures table row), but README still indexes only
three procedures:

- `README.md:142` — `doc/procedures/{doc-search,script-search,embeddings}.md`
  → add `maintenance`.
- `README.md:196-197` — procedures table → add a `maintenance.md` row.
- `README.md:175` maint-full cell — while editing for F7, the wording
  "post-ingest cleanup" stays true; optionally append "(PRE_FULL + TIER1 +
  TIER2, see procedures/maintenance.md)".

**Update targets**: the two README index points (+ optional :175 note).

### F9 — this audit's own location (LOW)

RESOLVED during execution: the user moved this file from the repo root
to `doc/improvements/proposals/` on 2026-08-29; it now lives (post-
execution) in `../archive/tooling/`.

### F10 — empty patch subjects before push (user action)

The 08-22→08-29 work is parked in stgit patches, two of which have empty
subjects: `maint_optimizations` (59959452) and `backup_enhancements`
(9a119d0b — renamed from zstd_fixes during the stack reorg). A third
patch `mojo_regex_interop` was parked with its proposal doc untracked;
RESOLVED 2026-08-29 — committed as `7d7ae154` (proposal + repro scripts;
proposal status VERIFIED/RESOLVED).
Name the subjects before push (`stg edit <patch> -f <msgfile>`); the F6
snapshot-provenance item above is unaffected.

### Post-window checked clean

- `doc/procedures/maintenance.md` content matches the implemented
  composition (written with #177, test-pinned in `test_maint.py`).
- Makefile help lines updated in-session for both the maint-full
  description and the `.zst` snapshot echo — no Makefile drift.
- AGENTS.md query-first guidance unaffected by the session (no new
  index consumer).
- `doc/improvements/completed.md` #174–#177 entries + addenda are
  self-consistent and reference only existing files.

| # | Fix | File:line | Effort |
|---|-----|-----------|--------|
| 1 | Add `Mojo/` row to layout table + pyproject `mojo` extra note | `README.md:73-85` (after the `frontend/` row) | 5 min |
| 2 | `_build_meta` key list + note_embed_* keys | `doc/schema.md:190` | 2 min |
| 3 | Same fix in §8 `_build_meta` bullet | `doc/graph_design.txt:266-267` | 2 min |
| 4 | Backfill `## 172` (derive_insights typographic quotes; #172 ref) | `doc/improvements/completed.md` (numbering per README checklist rule 2 — unique, never renumber) | 15 min |
| 5 | Backfill `## 171` (Mojo pilot; #171 ref) | same | 10 min |
| 6 | Backfill `## 170` (Chatter #83 ingest) — optional | same | 10 min |
| 7 | `snapshot_db` codec wording | `README.md:79` | 2 min |
| 8 | note-search `--check` doc block | `doc/procedures/doc-search.md` | 15 min |
| 9 | Update `doc/local/git_identity_scrub.md`: forward source_db leak closed (118efc89), history-only residue remains | `doc/local/` (gitignored) | 5 min |
| 10 | Name the empty stgit patch subjects `maint_optimizations` + `backup_enhancements` before push (F10, supersedes the cca89918 item) | user action (`stg edit <patch> -f <msgfile>`) | user |
| 11 | zstd/`.zst` rename sweep — all 12 F7 rows; README:98-99 and doc-search.md:96 must become working `zstd -dc` restore lines, not just rebranding | F7 table | 20 min |
| 12 | README procedures index: add `maintenance.md` (`:142` list + `:196-197` table; optional `:175` note) | F8 | 5 min |
| 13 | Move this proposal to `doc/improvements/proposals/` (F9) | repo root → proposals/ | 1 min |

Archival checklist (proposals/README.md rules) applies when this proposal is
executed: move to `../archive/tooling/`, unique completed.md numbers,
`make search-fresh APPLY=1` then plain `make search-fresh` rc=0.

## 4. Verification

- Doc-only change: `make search-fresh APPLY=1` + plain `make search-fresh`
  (rc=0) to converge the doc index (doc/ content changed — including the
  new `procedures/maintenance.md` and this proposal's move).
- `make lint` untouched surfaces; no pytest surface affected.
- After the F7 sweep, grep the tracked corpus for residual `gzip|\bgz\b`
  outside `doc/improvements/` history: expected zero hits in evergreen
  surfaces.
- No full gates without explicit user go; tree left dirty for the user's
  manual commit (standing convention).
