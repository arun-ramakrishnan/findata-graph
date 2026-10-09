---
title: "convo cohort: give the embed-gc Ref an active stamp so rollback insurance arms before a migration"
status: executed
filed: "2026-10-09"
executed: "2026-10-09"
completed_md: "376"
area: "helpers/maintenance/gc_embed_cache.py + tests/test_embed_gc.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). -->

# convo cohort — an active stamp for the embed-gc Ref

## 1. Motivation

`embed-gc` reasons about deadness per cohort, anchored to each reference
index's **active** `embed_model` stamp. A row whose model differs from
its cohort's stamp is rollback insurance and is never deleted; a cohort
with **no** stamp gets no such protection, because there is nothing to
anchor to.

`convo` is the only cohort with no stamp. Its `Ref` is the sole entry in
`DEFAULT_REFS` written without a `stamp_sql` — every sibling passes one.
The stamp is not missing from the database: `rebuild_convo_search.py`
writes `("embed_model", model_label)` into **`convo_meta`** on every run
(lines 494, 546, 709), and the live value is present today. The GC simply
never queries it.

So the gap is one missing line of wiring, and the consequence is that the
rollback-insurance rule is **disarmed for the largest cohort in the
cache** — 73,005 of 95,205 rows, 77% of the store by row count.

## 2. Evidence

Measured on the live store (`memory/embed_store.db`, 2026-10-09, after
the operator's rollback-insurance cleanup):

```text
rows 95,205 | live 94,181 | dead 1,024 | retained 0
  company -> embeddinggemma-2-q8_512     note   -> embeddinggemma-2-q8_512
  convo   -> None                        script -> embeddinggemma-2-q8_512
  doc     -> granite-embedding-97m-r2    memory -> embeddinggemma-2-q8_512
dead by cohort: {'convo': 1024}
```

Two facts make the change safe today and necessary eventually:

1. **The convo population is single-model.** `SELECT model, COUNT(*)
   … WHERE source='convo'` returns exactly one row: `granite-embedding-97m-r2`,
   73,005. That equals the stamp `convo_meta` declares, so `model ==
   stamp` for every row — **none become retained**, and the 1,024
   genuinely dead rows stay dead and stay collectable.
2. **The table name is not the sibling's.** convo writes `convo_meta`;
   the sqlite cohorts use `*_search_info`. Copying a sibling's
   `stamp_sql` verbatim would query a table that does not exist — and
   `_read_ref_rows` executes the stamp inside a `try` whose failure
   `_read_refs` re-raises as
   `RuntimeError("reference index unreadable: convo … — refusing to GC")`.
   A wrong name turns a working tool into a hard abort for **all** cohorts.
   That is the loud, safe direction, but it must be pinned by a test.

## 3. Design

One line in `DEFAULT_REFS` (`gc_embed_cache.py:150`):

```python
(
    Ref(
        "convo",
        REPO / "memory/convo_search.duckdb",
        "SELECT snippet FROM convo_search",
        stamp_sql="SELECT value FROM convo_meta WHERE key = 'embed_model'",
    ),
)
```

No change to `_read_refs`, `_classify`, the retention rule, or
`purge_foreign_models`. The default `text` (identity on `snippet`) is
correct for the granite stamp, and convo sets neither `text_by_model` nor
`text_db_by_model`, so no basis-recipe dispatch changes.

## 4. What this changes, and what it does not

- **Today:** nothing observable. Retained stays 0, dead stays 1,024.
- **On a future convo → gemma migration:** the granite rows become
  rollback insurance automatically — retained by default, reclaimable only
  via `gc_embed_cache.py --retire-model granite-embedding-97m-r2 --source convo`.
  That is the rule working as designed for every other cohort, armed
  *before* the migration rather than after.
- **Not in scope:** retiring any model, deleting rows, or changing the
  1,024 dead convo rows. The operator owns that decision and the existing
  `--retire-model` / `APPLY=1` paths.

## 5. Acceptance

1. `_read_refs` resolves `convo` to `granite-embedding-97m-r2` (was `None`).
2. `survey()` on the live store: `retained` stays 0 and `dead` stays
   1,024 — the change is inert on today's single-model population.
3. A seeded convo-shaped index with a foreign-model row has that row
   **retained** once the stamp is present (the insurance arms), and
   dropped when it is not — pinning both directions.
4. A Ref whose `stamp_sql` names a missing table raises the
   `refusing to GC` RuntimeError rather than silently yielding `None`.
5. The existing `test_stampless_cohort_keeps_legacy_behavior` still
   passes: a stamp-less Ref keeps legacy behaviour, so the mechanism
   remains available for genuinely unstamped cohorts.
6. `ruff check` + `--select S,UP,C901` clean; `static_checks` rc=0;
   `md-lint` clean; `search-fresh` fresh.

## 6. Slices

- **S1 — Measure the live population and the failure mode.** DONE (§2):
  single-model convo, 73,005 rows; missing table name aborts the whole
  tool.
- **S2 — Add `stamp_sql` to the convo Ref.** DONE.
- **S3 — Teeth.** DONE: four tests — stamp resolves and arms the
  insurance, the same population unstamped keeps legacy behaviour, a
  wrong table name aborts, and the production `DEFAULT_REFS` convo entry
  is stamped (asserting no cohort is left unstamped).
- **S4 — Acceptance sweep.** DONE: `convo` resolves to
  `granite-embedding-97m-r2`; `retained` stays 0 and the pre-existing
  `test_stampless_cohort_keeps_legacy_behavior` still passes; three
  mutants RED (remove the stamp / narrow the except / rename the table);
  `ruff` + `--select S,UP,C901` clean; `static_checks` rc=0;
  `md-lint` clean; `search-fresh` fresh.

## 8. Execution finding — the abort was real but unwrapped

§2.2 predicted a wrong table name surfaces as
`RuntimeError("reference index unreadable: convo … — refusing to GC")`.
The test says otherwise: for a **DuckDB** ref the stamp read raises
`duckdb.CatalogException`, which the narrow
`except (sqlite3.Error, OSError, RuntimeError)` did not catch, so the raw
driver message escaped instead of the house one.

The safety property survived — it still aborted, and never degraded to a
silent `None` — but the operator-facing message was a driver internal, and
the proposal's own §2 claim was wrong. `_read_refs` now catches broadly
(`except Exception` with a `# noqa: BLE001`), mirroring the text-basis
guard a few lines below it in `_read_ref_rows`, which already catches
broad for the same "an unreadable reference must never read as no stamp"
reason. Pinned by
`test_convo_stamp_on_a_missing_table_aborts_the_gc`, which asserts the
`refusing to GC` message; mutant #2 narrows the except back and goes red.

## 9. Operator note — do not GC during an in-flight `convo-fresh` (RESOLVED 2026-10-09)

Measured while the operator ran `make convo-fresh`, the convo dead count
climbed `1,024 → 1,536 → 2,048` across successive surveys, against a
cohort that had no genuinely dead rows. Re-measured once the rebuild
committed:

```text
rows 96,696 | live 96,694 | dead 2 | retained 0
convo segments 84,545 (was 73,005 at survey time)
dead by cohort: {'script': 1, 'doc': 1}   convo: 0
```

The climb was a benign write-ordering artifact — the rebuild writes cache
rows before the segment is visible in `convo_search`, so a concurrent
survey sees a hash with no index row and calls it dead. It resolved
completely: **convo's dead count went to zero** and the cohort grew by
~11.5k properly-embedded segments. `retained` stayed 0 and the convo
stamp kept resolving throughout, which is the §4 "inert today" claim
holding under a real workload rather than a quiet tree.

The doctrine stands and is now evidenced: **a dead count observed
mid-rebuild is not a statement about which rows are garbage.** Do not run
`make embed-gc APPLY=1` against an index being rebuilt — `convo_search`
is swapped atomically, so the reference is never half-written, but the
*derived* dead count is meaningless until the writer finishes.

Residual: 2 genuinely dead rows (1 `script`, 1 `doc`). Not worth a
`VACUUM` of a ~285 MB store to reclaim two rows; report mode exits 1 on
any dead row, so that exit code is the signal, not an alarm.

## 7. Risks

- **Hard-abort regression if the table is renamed.** `convo_meta` is
  written by `rebuild_convo_search.py` in three places; a future rename
  that does not update this Ref turns `embed-gc` into an all-cohort
  abort. Accepted: the tool refusing to guess is the documented
  doctrine ("aborting costs a re-embed, deleting live rows costs
  correctness"), and §5.4 pins the abort so it is a tested behaviour
  rather than a surprise.
- **Retention grows after a migration.** By design, and the same growth
  every other cohort already accepted.
