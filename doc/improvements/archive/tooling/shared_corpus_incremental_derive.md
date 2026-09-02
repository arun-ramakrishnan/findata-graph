---
title: "Shared corpus and incremental derive — one walk and stale skip for findata"
status: executed
filed: "2026-09-02"
executed: "2026-09-02"
completed_md: "194"
area: "helpers/core"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Shared corpus and incremental derive — one walk and stale skip for findata

<!--
House proposal skeleton (matches the executed corpus's shape — see
doc/improvements/archive/ for real examples). Rules:

- File the proposal BEFORE implementing multi-slice work (house rule
  2026-08-21). One proposal per arc; slices inside it.
- Every number is MEASURED on this box — a proposal with unmeasured
  claims gets challenged; keep the raw log in the Appendix.
- Tables for comparisons, prose for causality.
- On EXECUTED: git mv to ../archive/<topic>/, completed.md entry (unique
  number), pending.md sweep, archive/README topic line, README pointer
  reset, `make search-fresh APPLY=1` — the full checklist lives in the
  proposals README.
- If a proposals frontmatter contract lands, this template gains it —
  until then the bold-line header below is the canonical status field.
-->

**Date:** 2026-09-02 · **Status:** EXECUTED 2026-09-02 ·
**Area:** `helpers/core/corpus.py` (new) · `helpers/graph/derive_*` · `helpers/graph/_extract_worker.py` · `helpers/maintenance/maint.py` · `doc/templates/python_module.py` (advisory)

## 1. Motivation

The `findata` corpus (`1243 md` / `1244 files` `1102` with frontmatter) is walked **five times** per `maint --full` — `verify_notes` `0.43s`, `frontmatter_schema` `0.77s`, `sync_tags` `0.28s`, `derive_themes` `0.51s`, `derive_insights` `2.19s` — each `sorted(rglob("*.md"))` + `read_text` + `yaml_safe_load` (`CSafeLoader` `~10×` only in `static_checks`). `static_checks` already collapsed `3→1` walk `~5s→2.42s`; the `5×` replay remains (`~4.3s` of YAML re-parse). `S0` `perf stat -d` `paranoid=4` blocked `IPC/cache` but `cProfile` showed hot is `yaml` `1.24s` + `jsonschema` `1.02s` + `re.search` `0.29s`/`56522` + `iter_company_sections` `0.78s` — Python `GIL`-held loops, not `SIMD`/`HITM`, so `ThreadPool 4` lost (`2.54s` vs `1.99s`) and `ProcessPool` pickle `__main__` crashed to serial `1.69s`.

Trigger: `S0` wall `verify 0.43s / frontmatter 0.77s / static_checks 2.37s / sync 0.28s / themes 0.51s / insights 2.19s` and the `5×` `rglob` inventory `doc/local/perf_skills.md:12-13` (user request to examine all corpus scanners before Mojo).

## 2. Evidence (measured 2026-09-02, this box `i5-6500 4c no-HT`)

| Configuration | Result | Verdict |
|---|---|---|
| status quo `5× rglob+ yaml` `maint --full` replay | `verify 0.43s + frontmatter 0.77s + sync 0.28s + themes 0.51s + insights 2.19s ≈4.3s` `cProfile` `yaml 1.24s` `validate 1.02s` | baseline |
| `Corpus.load findata workers=1` serial | `0.37s` `1243` `0.003s fs_walk` `DB memory/corpus.db 0.16s` cached `0.15s` `per-file mtime` `1/1243` `0.23s` vs `0.70s` `29MB` sidecar | adopt — `1` walk replaces `5` (consolidated `build/load/store` in `corpus.py`, `DB` not `/tmp` pickle) |
| `Corpus workers=4` `ThreadPool` | `0.44-0.50s` `0.48s` `1243` `vs 0.37s serial` pool overhead > win for `1.2k` small files | serial default, `4` only for larger |
| `sync_tags --corpus` via `Corpus` `1764?` `by_path` fallback | `0.32s` `cached` `6764 tags 1129 ent` `vs 0.29s` serial `parity` `second 0.32s` `cold 0.44s` | keep `serial` default, `Corpus` amortized `5×` `1.45s→0.44+5*0.16≈1.24s` |
| `derive_themes --corpus` | `0.44-0.56s` vs `0.47-0.69s` serial `359 edges` parity | adopt `0.13s` win |
| `derive_cited_in --corpus` | `0.23-0.29s` vs `0.35-0.50s` serial `1106 edges` `103 cited_in` | adopt `0.12-0.21s` win |
| `derive_insights --corpus` `ThreadPool` | `2.81s` vs `2.27s` serial `2742 q 1496 m` `iter_company_sections 0.78s` `GIL` | keep serial `2.36s` `+1 DB` `resolver_map` not `ThreadPool` |
| `extract_relations ProcessPool 4` via `helpers/graph/_extract_worker.py` | `1.66-2.20s` `112 files 88 edges` `no WARNING` `pickle b'helpers.graph._extract_worker'` vs `S0` `1.69s` `WARNING` `__main__` `BrokenProcessPool→serial` `ThreadPool 5.3-7.9s` | adopt worker fix `correct`, wall ≈ serial for `112` `Fork` overhead, scales with `1243` |
| `themes --stale-only` `MAX(created_at)` vs `max(mtime)` | `full 0.59s → SKIP 0.12s` `80%` `themes stale-only: no Company note newer than 2026-09-02 — skipping` | adopt |
| `cited_in --stale-only` | `0.43s → 0.11s` `73%` | adopt |
| `insights --stale-only` existing `308/343` `194 past stale_after` | already `2.19s` `stale` vs full | keep |

Ruled out, measured, do not re-audit: `ThreadPool 4` on `derive_insights` `2.54s` `2.07s scan +2.52s wait` `GIL` `iter_company_sections` Python loops — kept serial `2.36s` `+1 DB`; `Corpus workers=4` not faster for `1.2k` `1-2KB` files; `ProcessPool` `112` `Fork` overhead ≈ win until `1243` scale; `Mojo corpus_sweep 15s` parity `bench` stays `mojo-bench` not `perf` until `S1` budget still insufficient.

## 3. Design

**Chosen:** `S1` before Mojo — `parallel + dedup` then `Mojo` only if still over budget. `S0` `linux-perf` `paranoid=4` `cProfile` `§13` filed, `performance-patterns` triaged `no SIMD/HITM` `GIL` `yaml/regex` not `accumulator`.

Slices (each independently landable, order matters):

1. **S1a — single DB query** `derive_insights._build_resolver_map` `1 SELECT` vs `N` per-file `SELECT ... WHERE IN (...)` `N=351` `2.19s→2.36s` `+_scan_one_file` `2742 quotes` parity `144 passed` — `ThreadPool` reverted `GIL` (DONE).
2. **S1b — shared walk** `helpers/core/corpus.py` `Corpus.load(workers=1,use_cache=True)` `one fs_walk 0.003s + read_text + CSafeLoader yaml C` `0.37s` `DB memory/corpus.db` `per-file mtime` incremental `0.16s` cached `vs 0.44s` cold `29MB` sidecar `not research.db` `29MB` `29MB` + legacy `/tmp/findata_corpus.pkl` fallback `by_path()` `Note(path,text,frontmatter,body)` `clear_cache()` `_init_db_cache` consolidated `build/load/store` in `corpus.py` one place not duped per-module (DONE module `+DB` `~6500B`).
3. **S1b wire-up** `sync_tags --corpus` via `_sync_from_corpus` `Path(file_path)` relative → absolute + suffix fallback `6764 tags` `0.32s` `PRE_FULL` `sync-tags --corpus` + `maint --full` pre-warm `Corpus.load 0.15s` once so `5×` `subprocess` hit `DB` `0.16s` not `0.37s` walk (DONE). `derive_themes` `extract_theme_membership(root,path_to_name,corpus)` + `derive_cited_in` `extract_citations(vault,path_to_name,stems,corpus)` + `derive_insights` `scan(target,conn,corpus)` each `try Corpus load` `corpus frontmatter/body` not `read_text` `re` `0.12-0.21s` win `359/1106` parity (DONE `§16`, `insights --corpus` kept `serial` `2.81s` slower so `maint` stays `serial` for `insights`). `DB` `per-file mtime` `hash` incremental: `1/1243` changed → `0.23s` `vs 0.70s` full `save 0.47s` not `max_mtime` full rebuild.
4. **Worker fix** `helpers/graph/_extract_worker.py` `1182B` `helpers.graph._extract_worker._extract_batch_arg` stable `pickle` vs `__main__` `AttributeError` `BrokenProcessPool→serial` `1.69s` `WARNING` (DONE `S0` `cProfile` 4 crashes → `0`, wall `1.66s` `no WARNING` `pickle b'helpers...'`).
5. **S1c — incremental** `derive_themes --stale-only` + `derive_cited_in --stale-only` `MAX(created_at) FROM graph_edges WHERE edge_type=...` vs `max(mtime)` `Companies 1078` / `Companies+Sectors+Super_Sectors 1243` `0.59→0.12s` `0.43→0.11s` `insights` already `308/343` `§16` (DONE).
6. **S1d — advisory (this proposal)** `doc/templates/python_module.py` `contract: helpers.core.corpus` `Corpus` + `--stale-only` `MAX(created_at)` `§15` + `tests/test_corpus_advisory.py` `rglob("*.md")` without `Corpus` `8` `WARNING` + `derive_*` without `--stale-only` `2` `WARNING` `advisory` not `FAIL` (like `ty-tests` `nonblocking`) + consolidated `helpers/core/corpus.py` `build/load/store` one place `DB memory/corpus.db` `per-file` not duped per-module `S1b` consolidation — see `§4` below.

Alternatives considered: `ProcessPool 4` `derive_insights` `GIL` `ThreadPool` lost; `Corpus workers=4` overhead for `1.2k` small files; `Mojo corpus_sweep 15s` parity `mojo-yaml` only after `S1` still over `perf 8/4s`; `shared mmap` `DB` `corpus` table `hash` `stale` overkill vs `max(mtime)` `0.12s` `try/except` fallback to full scan.

## 4. Acceptance criteria & shakedown

1. `python3 -c 'from helpers.core.corpus import Corpus; Corpus.clear_cache(); c=Corpus.load("findata",workers=1); print(len(c.notes))'` `1243` `0.37s` `second 0.15s` `pickle` `by_path` absolute/relative both resolve `6764 tags`.
2. `python3 helpers/core/sync_tags.py --corpus` `6764 tags 1129 ent` `0.32s` `parity` `python3 helpers/graph/derive_themes.py --corpus` `359` `0.44s` `python3 helpers/graph/derive_cited_in.py --corpus` `1106` `0.23s` `x3` stable `tests/test_derive_insights.py 144 passed` `tests/test_extract_relations* 43 passed`.
3. `--stale-only` `SKIP` `0.12s` `themes` `0.11s` `cited_in` when `MAX(created_at)=now >= max_mtime`, full `0.59s/0.43s` when `2026-08-19` `DB` vs `2026-09-01` files — `insights` `244?` `308/343` already.
4. `helpers/graph/extract_relations.py` `112` `no WARNING` `pickle b'helpers.graph._extract_worker'` `1.66s` `cProfile` no `AttributeError` `ThreadPool` `5.3s` not used.
5. `doc/templates/python_module.py` carries `contract: helpers.core.corpus` `Corpus` + `--stale-only` advisory comment and `tests/test_templates.py` advisory `rglob` without `Corpus` `WARNING` not `FAIL` (see `§5`).

| Projected outcome | Today `S0` | After `S1` |
|---|---|---|
| `maint --full` `5×` replay `~4.3s` `yaml` | `5× rglob+ yaml` `4.3s` | `0.37s` `Corpus` `+0.32*2+0.12*2≈1.1s` `~0.9s` saved per no-op `§15-16` |
| `themes/cited_in` full | `0.59s/0.43s` | `0.12s/0.11s` `SKIP` `73-80%` |
| `insights` | `2.19s` `2.07s scan` | `2.36s` serial `+1 DB` `correct` `ThreadPool` not used |

## 5. Risks

- **`Corpus` `pickle` staleness** — `max_mtime >= cache_mtime` `try/except` fallback to rebuild on `OSError` `pickle` error; `/tmp` ephemeral `gitignored`, `clear_cache()` in tests; `S1c` `stale-only` orthogonal `MAX(created_at)` `try` fallback to full scan.
- **Absolute vs relative path drift** `findata/Companies/...` repo-relative `DB` vs `Corpus` `findata/...` `resolve()` `relative_to(_REPO_ROOT)` vs `is_relative_to` `Python 3.9` `try` `suffix` fallback already `suffix` `as_posix` `endswith` for cache `relative/absolute` both forms (fixed `sync_tags` `0 tags` `→6764`).
- **Advisory vs gating** — `Corpus` `+ stale` as `advisory` not `qa` `static_checks` `FAIL` (not every helper needs `Corpus`; `verify_notes` `0.43s` `ThreadPool` already, `note_search` `bge` `1.56s` hot is `bge` not `yaml`). Advisory is `tests/test_templates.py` `rglob` without `Corpus` `WARNING` `tail` `60` not `rc 1` (like `types-tests` `nonblocking` `advisory` `ty-tests`), so future `findata` walkers see the nudge from start without blocking `qa` `lint+types+pytest`.
- **Mojo premature** — `S1` before `Mojo` per `performance-patterns` triage `no SIMD` `GIL` `yaml`; `Mojo` `corpus_sweep` stays `mojo-bench` not `perf` until `S1` `8/4s` budgets still over.

## 6. Non-goals

- `Mojo` `corpus_sweep` `db_access` `graph_algos` promotion — `bench` only until `S1` `perf` budgets still over.
- `note_search` `bge-small` `4× spawn` `1.56s` `doc_search` `1.16s` `script_search` `1.18s` `embed` `1.2s` not `yaml` `0.37s` — `Corpus` `body` reuse saves `0.2s` but `bge` dominates, `S2` separate.

## 7. Scale — what to do now vs defer (`10k` `breadth`, timing `×10` not `wall`)

**Collated from `S0` `§13` `cProfile` `yaml 1.24s` `re 0.29s` + `S1b` `Corpus 0.37s` `DB 0.16s` `S1c` `0.12s` `+DB per-file mtime` `S1d` `advisory` `8+2` `WARNING` — `forget wall` `S0` `2.19s` `→` `10k` `21s` `×10` not `0.47s` `×10` `4.7s`.**

**Address now (low effort, unblocks `10k` `list` `OOM` `250MB` `5× Fork` `1.2GB` and `mtime` churn):**

| Now | Effort | Why `10k` needs it now |
|---|---|---|
| **S1b.2 — `Corpus` per-file `hash` `blake3` not `mtime`** `corpus_cache.hash TEXT` `mtime` already but `hash` is `content` stable `git rebase` `mtime now` `false full` `0.70s` `→` `hash == DB hash` reuse `frontmatter/body` without `read` `0.02s` `vs mtime` `false` | `1 line` `hashlib.blake2b` `read_text 8KB` `hash` `DB` `mmtime` already `S1b` `DB` `234` | `10k` `rebase` `mtime` churn `10k` `re-parse` `7s` `→` `hash` `0.02s` `git` |
| **S1b.3 — `Corpus` `shard` + `maint` `shard`** `Corpus.load(root, shard="Companies")` `iter_findata_files` `1243` `→` `Companies 1078` `Sectors 42` `The_Chatter 112` `shard` `not findata 1243` `memory 29MB →8MB` `derive_themes` `Companies` only not `findata` `1243` `derive_themes` `extract_theme_membership` already `Companies` `1080` | `1 param` `filter` `str(root)` `+maint` `PRE_FULL` `Companies` `shard` | `10k` `list[Note]` `250MB` `5× Fork` `1.2GB` `OOM` `shard` `8MB` |
| **S1b wire remaining `2` high-value walkers** `verify_notes 0.43s` `frontmatter_schema 0.77s` `8` `→` `Corpus frontmatter` direct `1.24s` saved `S1b` `S1d` `advisory` `8→6` | `2` `from helpers.core.corpus import` `±5 lines` each `path_to_name` already | `10k` `verify+frontmatter 1.20s →10× 12s` `qa 8s` `fail` |
| **S1c wire remaining `2` `derive_events/co_mentions` `--stale-only`** `0.77s/0.28s` `→ SKiP 0.12s` `S1c` `2→0` | `2` `MAX(created_at)` `max(mtime)` `try` `5 lines` each | `10k` `derive_events 0.77s →7.7s` `maint` no-op `0.12s` keeps `12 steps` fast |

**Defer (`scale` `10k` `breadth` not `timing` `×10`):**

| Defer | Why defer |
|---|---|
| **S1b.4 — `Corpus` `frontmatter-only` vs `full text` `lazy` `Iterator[Note]` `yield` not `list[Note]` `2.5MB` `10k 250MB`** `Corpus.load(fields="frontmatter")` `verify` `frontmatter only 0.77s` `not body 2.5MB` `S1b` `body` always `2.5MB` `waste` + `Iterator` `yield` `not list` `OOM` | `1243` `2.5MB` `list` fine `10k` `250MB` only `defer` until `5k` `breadth` `body` `10×` `frontmatter` `1×` `S1b.2` `hash` `shard` already `8MB` |
| **S1b.5 — `Corpus` `generation` `DB user_version 7→8` `one MAX(generation)` vs `per edge_type 2 queries` `0.12s` `S1c`** `corpus_cache.generation INT` `all derive_*` share `generation` `not edge_type` `10k` `themes 359→2000` | `1243` `2 queries 0.12s` fine `10k` `N edge_type 10k` `S1c` `2` fine `generation` `1` `defer` until `10k` `themes` `2000` `Sectors 200` |
| **S1b.6 — `advisory` `→` `gating` `P0` `static_checks` `allowlist`** `tests/test_corpus_advisory.py` `8 WARNING` `advisory` `→ FAIL` when `>2000` notes or `>5` walkers `rglob` without `Corpus` `doc/templates/python_module.py` `contract` `→ qa` `P0` `helpers/validators/static_checks.py` `allowlist` | `1243` `advisory` fine `8` `WARNING` `10k` `5× rglob+ yaml 1.24s →12s` `qa 8s` `fail` `then` `gating` |
| **S2 `Mojo` `corpus_sweep` `15s` `db_access` `graph_algos`** `mojo-bench` `15s` `parity` `C` `bge` `4× spawn` | `S0` `performance-patterns` `no SIMD/HITM` `GIL yaml` `S1` `scale` `10k` `list` `shard` first `S2` only if `S1` `8/4s` still over `10k` |

**Record:** this `§7` is the `now vs defer` for `scale` `10k` `breadth` — `S1a` `single DB` `S1b` `Corpus DB per-file mtime→hash` `shard` `S1c` `stale` `2/2` `S1d` `advisory` `python_module` stay `§3` `S1b.2-3` + `2 walkers` + `2 stale` now, rest `defer` until `5k` `breadth`.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-02 | `perf stat -d wall` `verify 0.43s frontmatter 0.77s static_checks 2.37s sync 0.28s themes 0.51s insights 2.19s` `paranoid=4` `No supported events` `cProfile` `iter_company_sections 0.78s` `extract_metrics 0.74s` `yaml 0.42s` `re.search 0.29s/56522` `static_checks yaml 1.24s validate 1.02s` | `S0` `doc/local/perf_skills.md:13` `fs_walk 1244 0.003s read_text 200 0.023s` `CSafeLoader 10×` `GIL` `yaml` | `linux-perf` `Part 1` `paranoid=4` `Flow A/B` blocked `fallback cProfile` `performance-patterns` `no SIMD/HITM` |
| 2026-09-02 | `python3 helpers/graph/derive_insights.py findata --stale-only` `ThreadPool 4` | `2.54-2.74s` `2.07s scan +2.52s wait` vs `1.99s` serial `2.36s` `+1 DB` kept | `GIL` `iter_company_sections` Python loops `re C` not enough `S1a` `§14` `144 passed` |
| 2026-09-02 | `python3 helpers/graph/extract_relations.py` `112` `ThreadPool 4` | `5.3-7.9s` vs `1.69s` `serial fallback` `GIL` `re` | reverted to `ProcessPool` `§14` |
| 2026-09-02 | `Corpus.load findata workers=1` `1243` `0.37s` `cold 0.44s` `cached 0.15s` `pickle 0.16s` `workers=4` `0.48s` `max_mtime 0.003s` | `§15` `by_path` `absolute/relative` fallback `0 tags→6764` fixed | `/tmp/findata_corpus.pkl` `use_cache=True` `clear_cache()` |
| 2026-09-02 | `sync_tags --corpus` `0.32s` `cached` `6764` vs `0.29s` serial `parity` `PRE_FULL --corpus` `maint --full` pre-warm `0.15s` | `S1b` `§15` `by_path_alt` | `amortized 5× 1.45s→1.24s` |
| 2026-09-02 | `derive_themes --corpus` `0.44-0.56s` vs `0.47-0.69s` `359` `derive_cited_in --corpus` `0.23-0.29s` vs `0.35-0.50s` `1106` `insights --corpus` `2.81s` vs `2.27s` `slower` kept `serial` | `S1b` wired `§16` `maint` stays `serial` for `insights` | `Corpus frontmatter/body` not `read_text` |
| 2026-09-02 | `helpers/graph/_extract_worker.py` `1182B` `pickle b'helpers.graph._extract_worker'` `1.66-2.20s` `112` `no WARNING` vs `S0` `1.69s` `WARNING` `__main__` `BrokenProcessPool→serial` `cProfile` 4 crashes→0 | `S1b` worker fix `§15` `43 passed` | `ThreadPool` `5.3s` not used `Fork` overhead ≈ serial for `112` |
| 2026-09-02 | `derive_themes --stale-only` `full 0.59s → SKIP 0.12s` `80%` `cited_in 0.43s→0.11s` `73%` `bumped now` `SKIP` `no Company note newer` | `S1c` `§16` `MAX(created_at)` `try` fallback | `S1c` |
| 2026-09-02 | `Corpus` `sqlite` `memory/corpus.db` `28.14 MB` `1243` `730B fm` `10KB body` `cold 0.70s` `SELECT 0.079s` vs `duckdb` `memory/corpus.duckdb` `47.01 MB` `1.7×` `CREATE 8.62s` `12×` `SELECT 0.135s` `1.7×` `graph.duckdb 10.5 MB` `duckdb 1.5.5` `VARCHAR` `PK` `text 11KB` `columnar` not `FSS` | `keep sqlite` `helpers.core.db.connect(_CACHE_DB)` `WAL` `sidecar` not `research.db` `2 connections` `sqlite corpus+duckdb graph` not `1 duckdb` `S1b` `DB` not `duckdb` `§7` | `S1b` |
| 2026-09-02 | `Corpus` `blake2b 8` `content_hash` `share note_search 8` `mmtime` `carry` `vs hash verdict` `cold 0.82s` `cached 0.17s` `touch same content 0.19s` `vs 0.70s` `full` `content change 0.20s` `DB per-file hash` `hash ==` `reuse` `0.02s` `git worktree` `skew` | `S1b.2` `blake2b 8` `hashlib.blake2b(text,8)` `like` `note_search` `_file_fingerprint` `title,sector,content 8` `mtime` `carry` `hash` `verdict` `S1b` `DB` `incremental` `scale` `10k` | `S1b.2` |
| 2026-09-02 | `Corpus` `DB` `per-file mtime` `0.17s` `cached` `vs` `pickle /tmp 0.16s` `1/1243` `0.23s` `vs 0.70s` `blake2b 8` share `note_search` `8` `vs mtime` pending `S1b.2` | `S1b` `DB` `incremental` `scale` `10k` `hash` `S1b.2` | `insights` `308/343` already |
