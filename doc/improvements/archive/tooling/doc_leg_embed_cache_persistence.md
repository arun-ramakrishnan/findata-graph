---
title: "doc leg: persist + surface embed-cache writes; per-section carry for ledger files"
status: executed
filed: "2026-10-17"
executed: "2026-10-18"
completed_md: "410"
area: "helpers/core + helpers/maintenance"
---

# doc leg: persist + surface embed-cache writes; per-section carry for ledger files

**Date:** 2026-10-17 · **Status:** EXECUTED (2026-10-18)

## 1. TL;DR

The 2026-10-17 23:21 `search-fresh` doc apply re-indexed six dirty doc
files and embedded ~3.4k sections over ~15 min of solid CPU on the
llama :8732 embedder — while `embed_cache` gained **zero** doc rows
(`embeddinggemma-2-q8_512`/`doc` = 0 rows before and after; only the
3,322 granite-era rows exist). `CachedEmbed` is best-effort-silent:
attach failure (`helpers/core/embed_cache.py:152-160`) and per-row
store failure (`:194-209`, `except Exception: pass`) both degrade to
uncached with no surfaced signal. Live probe (2026-10-17, store idle):
attach ok, `dirty=1` on first miss — the write path is healthy
uncontended, so the loss was lock contention with the concurrent
session's `embed_store` writer (convo leg, +201 rows 23:39).

Independently, the doc carry check is file-grained
(`helpers/maintenance/rebuild_doc_search.py:656`, mtime equality): a
30-line insert into `completed.md` (~400 of the corpus's 3,405
sections, a 166 KB monotone ledger) re-chunks and re-embeds the whole
file. With the cache gap, even unchanged sections miss (empty
gemma/doc cohort) → ~15 min CPU per small edit, and nothing warms for
next time.

**Measured (2026-10-17, live):**

| observation | value |
|---|---|
| gemma/doc cache rows before / after the 23:21 run | 0 / 0 |
| granite/doc rows (rollback insurance, intact) | 3,322 |
| run wall time on doc leg | ~15 min (23:21:02→23:36) |
| llama-server :8732 during run | 260–281% CPU (3 threads) |
| probe: CachedEmbed store uncontended | attach ok, dirty=1 |
| completed.md share of doc corpus | ~400 / 3,405 sections |

## 2. Design

### S1 — surface + heal cache degradation

- `CachedEmbed._try_init` (`helpers/core/embed_cache.py`): on failure,
  don't stay dead for the whole pass — lazily retry init on miss,
  bounded (e.g. once per 128 misses), reusing `_attach_vec_db`'s
  busy_timeout.
- Stats MUST surface state: the summary line prints
  `embed-cache: OFF (attach failed: <err>)` when `_ok` is False, and
  hits/misses/dirty when on (the per-miss progress meter at `:183`
  already exists — extend it, don't duplicate).
- `rebuild_*_search` check mode: when misses > N and dirty == 0, print
  a loud WARNING (cache consumed compute and stored nothing) — visible
  in `make search-fresh` output without digging into sidecars.
- Keep per-row commits (`:201-206`, the 2026-10-07 lesson — do not
  batch them back).

### S2 — per-section carry for append-mostly ledgers

- Extend the `doc_search_meta` fingerprint (today: per-file
  mtime+content_hash) with a per-section content-hash multiset. On an
  mtime mismatch, carry sections whose hash is unchanged (verbatim
  stored rows: title/anchor/content/embedding/meta/links/trail);
  re-chunk + embed only new/changed sections.
- No chunker change; hashes are computable at walk time from the
  section text the chunker already produces.
- Explicitly in scope: `completed.md` (monotone append ledger, ~400
  sections per touch) and the big archive indexes.

## 3. Acceptance

1. Forced-failure test: hold a writer lock on `embed_store.db` during
   init → run completes, summary prints the OFF marker; after release,
   lazy re-init stores subsequent misses (both states asserted).
2. Uncontended doc edit → gemma/doc cache rows > 0 afterwards; an
   immediately repeated identical run reports hits == sections,
   misses == 0.
3. `completed.md` 30-line insert → misses ≈ new/changed sections, not
   ~400 (S2 carry), with unchanged sections carried verbatim.
4. Index parity: carried rows byte-identical to a full rebuild for the
   same tree (row count + `doc_query` spot checks).
5. `guard_gemma_stamp` behavior unchanged; no purge of granite/doc
   rollback rows (the "No automatic model-difference purge" doctrine
   stays).
6. `make static-checks` clean.

Query-visible semantics do not change (same texts → same embeddings →
same index content; S2 is carry-verbatim, acceptance 4 proves parity),
so no ontology eval-gate bullet is required — but acceptance 4 is the
parity gate that must hold.

## 4. Non-goals / risks

- **No embedder/model/recipe change.** The gemma stamp, dims, and the
  pure-prose @4000 recipe are untouched.
- **Not an embedder-speed fix.** llamacpp CPU throughput (-t 3) and
  GPU/server tuning stay out of scope; S1+S2 reduce *work*, not
  per-vector cost.
- **Contention is mitigated, not eliminated.** Per-row commits keep
  writer windows tiny; the lazy re-init covers a busy moment at init.
  No schema change to `embed_cache`.
- **S2 must not weaken freshness.** mtime+hash still gates; hash carry
  only reuses sections whose text is byte-identical.

## 5. Follows

- `doc/improvements/archive/tooling/doc_search_gemma_adoption.md`
  (S1 recipe, stamp-keyed read path, trial-seeded cache — which is why
  doc slices were never warmed under gemma).
- `doc/improvements/archive/tooling/default_incremental_rebuilds.md`
  (completed.md #384 — incremental defaults).
- The 2026-10-07 lesson comment, `helpers/core/embed_cache.py:201-205`
  (per-row commits; 550 embeds lost to a SIGKILL).
- `doc/procedures/search.md` §Corpus lifecycle.

## 6. Outcome (2026-10-18)

- S1 + S2 implemented as designed; acceptance 2/3/4/5/6 proven at test
  level (acceptance 1's forced-failure variant is monkeypatch-level, not a
  live lock hold). See completed.md #410.
- The lazy re-init's first cut called `_try_init()` without assigning
  `self._ok` — recovery announced itself while the cache stayed off;
  `TestCachedEmbedDegradation` caught it before landing.
- Lived once more the same night: a post-archival doc edit re-chunked
  `completed.md` (~400 sections) and the S2 path carried them against the
  stored rows — the basis-keyed carry map and the `_doc_title` stem
  fallback (frontmatter'd files title from the filename stem) are both
  pinned by test now.
