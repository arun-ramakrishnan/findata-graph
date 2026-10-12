---
title: "doc_index_stale read gate flags mtime-skewed-but-content-identical indexes stale — align the reader with the writer's 2026-08-30 content-hash doctrine"
status: executed
filed: "2026-10-13"
executed: "2026-10-13"
completed_md: "#387"
area: "helpers/maintenance/rebuild_doc_search.py (doc_index_stale), rebuild_common carry path, app.py _docs_index_search"
---

# doc_index_stale read gate: mtime verdict contradicts the writer's content-hash verdict

**Date:** 2026-10-13 · **Status:** PROPOSED ·
**Area:** `helpers/maintenance/rebuild_doc_search.py` (`doc_index_stale`),
the incremental carry path, `app.py::_docs_index_search`

**Follows:** `worktree_fresh_trample_guard.md` (executed — `--allow-stale`
made cross-tree applies a sanctioned workflow, which is what fires this
bug on every such apply), and the 2026-08-30 lesson recorded in the
writer itself (`rebuild_doc_search.py:571` comment).

## 1. Problem

The doc index has two staleness verdicts with opposite policies:

- **Writer** (rebuild freshness, `rebuild_doc_search.py:571`): verdict is
  deliberately **content-hash**-based — "mtime is deliberately NOT part
  of the comparison …: shared index DBs across git worktrees/checkouts
  see mtime skew on identical content, and mtime-first verdicts flagged
  whole corpora STALE (2026-08-30)". The incremental apply accordingly
  *carries* hash-equal files without rewriting their
  `doc_search_meta.mtime` rows — only hash-changed/new files upsert.
- **Reader gate** (`doc_index_stale`, `rebuild_doc_search.py:755`,
  runs per `/api/docs/search` request via `app.py:1361`): compares raw
  `st_mtime` with `!=` — the exact policy the writer's doctrine
  rejected.

After any cross-tree apply, the writer (correctly) refuses to churn
unchanged files, the reader then flags their inherited mtimes as stale —
**permanently, on every future apply**, because carries never heal the
meta rows and the reader never looks at content. Only a `--full` rebuild
(rewrites all meta mtimes wholesale) clears it.

`--allow-stale` made this a *supported* workflow, not a rare accident:
every sanctioned cross-tree apply now leaves the shared index
mtime-stamped with the other tree until someone runs a full rebuild.

## 2. Evidence (measured 2026-10-13, live, this box)

1. A `--allow-stale` apply from the wt (HEAD `9ece7c499`, stamped
   2026-10-12 19:09:55) left `memory/doc_search.db` meta carrying wt
   mtimes: 60/60 sampled meta rows equalled the **wt's** disk mtimes,
   0/60 main's.
2. Main re-applied (`make search-fresh APPLY=1` first refused — diverged
   stamps — then `ARGS="--allow-stale"`): writer said **"✓ all search
   indexes fresh"** while the reader said **`stale: True`**. Full walk:
   **281 of 353** meta rows mismatched main's disk mtimes — all
   content-identical (the writer had just carried them).
3. Every docs consumer degraded to scan three times today
   (`embed_eval.py docs` hybrid leg served `['scan']` each time) until
   `rebuild_doc_search.py --full --allow-stale` ran: 3,201 rows,
   **3,201 embed-cache hits / 0 misses** (proving content was unchanged —
   the fix was pure meta-mtime healing), after which `stale: False` and
   the hybrid leg served.
4. The blast radius is the whole doc read path: `doc_query`, the app
   `/api/docs/search`, the TUI docs lane, and the eval harness all gate
   on `doc_index_stale`.

## 3. Design

### S1 — reader hash-verifies on mtime mismatch

`doc_index_stale` keeps the fast path (mtime equality ⇒ unchanged; the
same-tree common case costs exactly what it costs today) but demotes
mtime mismatch to *suspect*, not stale: hash the mismatching file and
compare to `doc_search_meta.content_hash`; stale only when the hash
differs, the file is missing, or the file set differs. The reader stays
write-free (house rule). Cost: zero extra hashing same-tree; in the
cross-tree case, one sha256 per drifted file (normally a handful; a full
doc/ hash walk is the bound, tens of ms).

### S2 — writer heals carried mtimes (optional, belt-and-braces)

The incremental carry path already has the disk stat in hand for every
hash-equal file; refreshing `doc_search_meta.mtime` for carried files
converges the state so even dumb readers stay green. Meta-only writes:
no `doc_search` rewrite, no generation bump (zero-churn discipline
preserved — the content multiset is untouched). If S1 lands alone this
is a pure hygiene pass; both together mean the raw-mtime check also
converges after any sanctioned apply.

### S3 — survey the sibling legs

Check whether `script_search` / `note_search` / `memory_search` read
gates carry the same mtime-equality pattern (`rebuild_common` shared
helpers included) and apply S1/S2 where found. The doc leg is the
confirmed instance; the others are unverified as of filing.

### Eval-gate bullet

This alters read-path semantics (what serves hybrid vs scan), so: the
D1 docs harness (`embed_eval.py docs`) must serve `{'hybrid'}` on a
freshly-applied cross-tree index *without* a full rebuild — the exact
scenario that failed three times today. Plus contract tests: hash-equal
+ mtime-skew ⇒ not stale; content change ⇒ stale; new/deleted file ⇒
stale.

## 4. Risks

- **Per-request hash cost** — bounded by the fast path: hashing only
  fires on mtime mismatch. A pathological tree-wide touch (e.g. `git
  checkout .`) hashes all of doc/ once per request until an apply heals
  meta — acceptable for a local tool, and S2 caps the window.
- **Weakening the gate** — the gate still fails closed: unreadable file
  ⇒ OSError ⇒ stale, missing meta row ⇒ stale, hash mismatch ⇒ stale.
  Only the false-positive quadrant (mtime skew, content equal) changes.
- **Interaction with the trample guard** — none: the guard governs
  *applies*; this governs the *read* verdict on whatever the shared
  index carries.

## 5. Summary

| behavior | today | after S1(+S2) |
|---|---|---|
| cross-tree apply → docs read path | scan until a full rebuild | hybrid serves (content decides) |
| same-tree staleness detection | mtime `!=` | unchanged (fast path) |
| content-change detection | mtime proxy | content hash on mismatch |
| writer carry path | skips meta rows | (S2) heals meta mtimes |
| full rebuilds needed to clear phantom staleness | one per cross-tree apply | zero |

**Revisit trigger:** if a fourth staleness surface appears (e.g. a
consumer that caches the verdict), fold it into S1's contract tests
rather than adding another verdict policy.

## 6. Execution record (2026-10-13, the `mtime_gate` patch)

Shipped same-day, filed → executed → archived in one session:

- **S1 — reader hash-verifies** (`rebuild_doc_search.py::doc_index_stale`,
  :772): mtime match stays the zero-cost fast path; a mismatch demotes to
  *suspect* and the file is `_text_hash`-verified against
  `doc_search_meta.content_hash` before judging stale. Fails closed
  (unreadable file or missing meta hash ⇒ stale). The doc consumers
  (`app.py:1364/:1427`, `doc_query.py:71`) keep their contract — only the
  false-positive quadrant changes.
- **S2 — writer heals** (incremental apply tail): every hash-equal file
  whose meta mtime lags the disk converges in the same transaction
  (`stats["mtime_healed"]`; covers carried files AND the zero-row
  hash-equal skips — `empty.md`-shaped files never enter `carried`, the
  first heal loop missed them and the test caught it). Meta-only writes:
  no `doc_search` rewrite, no stamp/generation touch.
- **S3 — sibling survey**: `memory_index_stale` already hash-based (the
  worktree lesson cited in-source — clean); the note leg has **no read
  gate** (CLI stats only — nothing to break); the script leg had the
  **identical bug** (`rebuild_script_search.py::_units_current`, mtime
  `!=`) → same S1 fix with the writer's blake2b-8 identity of record.
- **Tests** (targeted): `tests/test_rebuild_doc_search.py` 42 ✓ — the old
  `test_touched_file_stale` encoded the bug and was replaced by
  `test_touched_file_hash_equal_not_stale` (both the bare-touch and the
  meta-carries-other-tree-mtimes shape) + `test_content_change_stale` +
  `test_writer_heals_skewed_mtimes` (asserts `upserts == 0`, zero row
  churn, meta == disk post-apply). `tests/test_rebuild_script_search.py`
  +`test_staleness_touch_hash_equal_not_stale`, 60 ✓ across both suites.
- **Live proofs on the shared index** (2026-10-13): S1 — every meta mtime
  skewed −12 345.5 s with hash-equal content ⇒ `doc_index_stale` False
  (old code: True — the exact three-times-yesterday scan degradation);
  S2 — one incremental apply converged 353/353 meta rows to disk with
  zero row churn, after which even a raw-mtime check passes. Phantom
  staleness now self-heals on the next sanctioned apply; full rebuilds
  needed to clear it: **zero**. Eval-gate bullet satisfied: consumers
  serve hybrid on a cross-tree-applied index with no full rebuild.
