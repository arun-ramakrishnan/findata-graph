---
title: "Triage noise-gate observability — classify and count silent discards"
status: executed
filed: '2026-10-18'
executed: '2026-10-18'
completed_md: '413'
area: graph
---

# Triage noise-gate observability — classify and count silent discards

## 1. TL;DR

`noise_target` classifies six distinct reasons as "not an entity", but
`_bucket` reports all six under one opaque string and the extractor-side
write-time gate reports none of them. A rising discard rate is therefore
invisible and undiagnosable from either surface. Add reason codes + a
count, without changing a single verdict.

## 2. Motivation

This is the surviving half of range-review **H4**
(`doc/improvements/archive/tooling/range_review_1fda0c158_6182cd2c7.md:66`),
and it is the same defect class the repo already has a name for — "the
tool reports success while having done less than it claims" (completed.md
entry 380). The **substance** of H4 is fixed: the bare `len < 4` rule is gone and
the discriminator is now case-based, so `ITC` / `LIC` / `NSE` survive by
construction. What remains is that a human cannot tell whether the gate is
dropping mangled regex stubs (working as designed) or short real
institutions (a silent regression) — both render as `discard`.

## 3. Current state (verified at HEAD 86c1d330b, 2026-10-18)

- `helpers/graph/triage_pending_relations.py:187 noise_target(target: str) -> bool` —
  CANONICAL classifier; its docstring states `extract_relations` imports it
  for the **write-time** gate so such rows never reach the sidecar.
- Six discard causes inside that one bool: `not t` (empty),
  `len(tl) < 4 and not _is_short_entity(...)` (:208), `tl in COUNTRIES`,
  `_GENERIC_PREFIX`, `_GENERIC_SUFFIX`, `_FRAGMENT_JUNK`.
- `_is_short_entity` (:216) already handles the H4 substance: allowlists,
  ALL-CAPS 2-3, digit-only 2-3; `_GENERIC_ACRONYMS` returns False on purpose
  (`test_countries_generic_fragments` pins `ABC` / `CDMO` as noise).
- `_bucket` (:354) flattens all of it at :367 —
  `return "discard", "country/generic/fragment", False`.
- `write_report` (:518) emits per-bucket totals at :648
  (`lines.append(f"## {b} ({len(rows)})")`) but no reason breakdown.
- The only counters in the file count OTHER things: :961 decided rows
  dropped from the suggestions file; :994 `relation_noise.json` appends.

## 4. Slices

## S1 — reason codes behind the existing verdict

Add `_noise_reason(target: str) -> str | None` returning one of
`empty` / `short_fragment` / `country` / `generic_prefix` / `generic_suffix` /
`fragment_junk`. `noise_target` becomes a one-line delegation
(`return _noise_reason(target) is not None`) so the bool contract every
caller depends on is unchanged. `_bucket` surfaces the code as its detail
string instead of the flattened literal.

## S2 — count them where they are consumed

- `write_report`: a reason histogram under the `discard` bucket, whose
  parts must sum to the bucket total already printed at :648.
- Extractor side: one aggregate line per run (not per row) reporting the
  same histogram, so a rising short-fragment share is visible in the
  extract log — today the write-time gate is entirely silent.

## S3 — tripwire (advisory, optional, operator-gated)

`extract_relations --noise-tripwire PCT` warns on stderr when the discard
share of new rows crosses PCT percent:

```text
share = 100 * noise_gate_rows / (noise_gate_rows + queued_rows)
```

Discard share uses only the noise-gate bucket — bucket totals already
sum to provenance — so a tripwire compares like with like. The counter is
the deliverable; the threshold is the operator's call and may be left
unset (silent).

New `--noise-tripwire` CLI flag on `extract_relations` (post-S5 summary
line). Example output:

```text
      sidecar_skipped=3 (noise_gate:country=2 noise_gate:short_fragment=1)
!!! NOISE-TRIPWIRE tripped: discard share 50.0% (2/4) exceeds threshold 40.0%
```

## 5. Acceptance criteria

1. `noise_target(target: str) -> bool` signature and every existing verdict
   are bit-identical. **No edits required** to
   `tests/test_triage_pending_relations.py` or
   `tests/test_relations_queue_hygiene.py` — S1/S2 are purely additive.
2. New targeted test pins one positive per reason code, plus the two
   load-bearing negatives from the `_is_short_entity` docstring: `ABC` /
   `CDMO` stay noise, `SBI` / `ITC` / `LIC` / `3M` stay entities.
3. The report's discard histogram sums exactly to the :648 bucket total.
4. One aggregate extractor-side line per run; a zero-discard run still
   prints (loud success, not silence).
5. `--noise-tripwire PCT` warns on stderr when discard share crosses PCT;
   unset stays silent. Never changes a verdict — advisory only.

## Eval-gate bullet: deliberately ABSENT, with rationale

The proposals README requires `helpers/misc/ontology_eval_gate.py` for any
change altering query-visible semantics (rosters, crosswalks, hierarchies,
extractor rules). S1-S2 do not: they add a reporting helper beside an
unchanged verdict function. The gate would measure no movement. **S3 is advisory-only — it changes no
verdict, so the gate stays absent now.** If S3 is ever taken in a form that
changes a verdict, this bullet becomes mandatory and the proposal must be
amended before the slice lands.

## 6. Non-goals (do NOT do these)

- Do NOT widen `_INSTITUTION_SHORT_NAMES` (:253) or
  `_CORPORATE_SHORT_NAMES`. They stay exact-name by decision — the :250
  comment is explicit that the two allowlists are extended *together* with
  the resolver's `extract_relations.INSTITUTION_MENTIONS`. H4's data loss
  is already fixed; touching the sets re-opens a closed decision.
- Do NOT change *which* rows are discarded. This slice changes only what
  the system says about what it discarded.
- Do NOT retire the `discard` bucket itself.

## 7. Side finding (cheap, do with S2)

`_merge_noise_file` (:966) labels its docstring **"G3:"** — a collision with
`parse_extraction_gaps` G3 (acquisition / guidance magnitudes, `[APPLIED]`
at `doc/improvements/completed.md:251`). Two unrelated "G3"s now read as
one. Relabel the local slice to its own name while the file is open.

## 8. Follows

- Range review H4: `doc/improvements/archive/tooling/range_review_1fda0c158_6182cd2c7.md:66`
- Falsified in the same survey, for the record (do not re-propose): G3
  acquisition magnitudes (APPLIED, completed.md:251); the `merge_tracker`
  ingest close (no such module — `merge_tracker` / `merge_groups` absent
  repo-wide); the scraper publisher release-note leg (no such artifact).

## 9. Implementation findings (2026-10-18)

Slices S1–S3 landed as patch `triage_noise`. Evaluation of the landed code
turned up four problems; three are fixed, one is a documented limitation.

### 9.1 S3 tripwire was blind in parallel mode — FIXED

`_SKIP_STATS` (`extract_relations.py:2115`) is a module-global written only
inside `_queue_unresolved` (:2343/2346/2350), which runs inside
`_extract_batch` (:3103). `ProcessPoolExecutor` (used when
`len(nl_paths) >= _PARALLEL_THRESHOLD = 20`, :3366) runs `_extract_batch` in
child processes; each worker mutates its own copy of `_SKIP_STATS` and
discards it on exit. The reduce loop unpacked a strict 7-tuple per batch with
no skip-stats field, so `skip_stats()` at :3575 read the parent's counter —
which only the serial path ever populated.

Measured on the real corpus before the fix:

```text
$ extract_relations.py findata/Companies --noise-tripwire 40
TOTAL files=1207 extracted=195 applied=0 unresolved=13
      sidecar_skipped=0 ()
```

1207 files → parallel path → `sidecar_skipped=0 ()`, no tripwire. The switch
was implemented but inactive on anything larger than 20 files.

**Fix:** `_extract_batch` now returns `(results, skip_stats)`; the reduce
loop merges each worker's counters back into the parent's `_SKIP_STATS`.
Same result on the real corpus after the fix:

```text
      sidecar_skipped=79 (mention_spans_sentences=1 noise_gate=45
      noise_gate:country=8 noise_gate:fragment_junk=13
      noise_gate:generic_prefix=7 noise_gate:generic_suffix=5)
!!! NOISE-TRIPWIRE tripped: discard share 77.6% (45/58) exceeds threshold 40.0%
```

The serial path writes straight into the module-global and is not re-merged
(merging would double-count). All 61 tests still pass.

### 9.2 Extractor-side line is uninformative on this repo — KNOWN LIMITATION

Acceptance criterion 4 ("one aggregate extractor-side line per run; a zero-discard
run still prints") is satisfied formally — the line always prints — but on this
repo it prints `sidecar_skipped=0 ()` on essentially every live run. The reason
is structural, not a bug: the noise gate is populated by the triage tool's
`_merge_noise_file` ("G5" path), which writes rejected triples into a sidecar
that `extract_relations` consults at write time. Live extraction rarely hits
the gate directly, so the per-run line is loud but empty. The histogram in
`write_report` (S2) is where the signal actually lives; that half is the more
valuable deliverable. Noted here so the criterion is not misread as fully met.

### 9.3 Side-finding (S7) was applied — G3 → G5

Section 7 asked to relabel `_merge_noise_file`'s docstring from `"G3:"` to its
own name, because it collided with `parse_extraction_gaps`'s G3 (acquisition /
guidance magnitudes). The patch skipped this. Now applied: the local slice is
renamed **G5** at `triage_pending_relations.py:64` (module-level constant
comment), `:958` (step 5 comment), and `:1015` (docstring). No `G3` remains in
the file. Chose G5 rather than another arbitrary name because the repo's
`completed.md` already carries G3 (acquisition magnitudes, APPLIED) and G4 —
G5 is the next free slot in the same numbering convention.

### 9.4 Dead `order` duplicate — REMOVED

The patch added a second `order = ["discard", "alias_candidate",
"stub_candidate", "manual", "bad_source"]` at `:682`, duplicating the one at
`:616`. Both were already dead in the parent (the `:616`/`:578` assignment was
never read before the loop reassigns it). The `:616` duplicate was removed; the
`:682` one (which the loop actually consumes) is kept.
