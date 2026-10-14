---
title: "Range review 1fda0c158..6182cd2c7 — 43 findings (9 H / 19 M / 15 L) + review-apparatus fixes"
status: executed
filed: "2026-10-14"
executed: "2026-10-15"
completed_md: "394"
area: "helpers/maintenance/enrich_relations.py, helpers/graph/, helpers/pdf/, frontend/src/, Mojo/, app.py, .opencodereview/rule.json, helpers/misc/review_selection.py, helpers/misc/review_freshness.py, doc/procedures/ocr_review.md"
---

# Range review `1fda0c158..6182cd2c7` — findings and remediation

**Date:** 2026-10-14 · **Status:** PROPOSED · **Range:** 48 commits, 534 files, +84 818 / −6 914
**Legs:** `delegation` (host, free) + `scan` (`review_scan.py`). No `managed` leg — no key supplied.
**Evidence:** `outputs/reviews/review_report_1fda0c158..6182cd2c7.md` (gitignored, machine-local)
plus the two rosters beside it. Roster line numbers are **range-end relative** (`6182cd2c7`) —
`app.py` grew 2 662 → 3 595 lines in the single commit past the range end, so adjudicating
against the working tree reads the wrong code.

## 1. Context

The range is the bulk of the findata-graph September arc: embed-store consolidation,
zero-churn `maint-full`, the Mojo pilot and its integrity-check port, the in-repo PDF engine,
market-data resolution with a Google-Finance fallback, doc/script search, the "Research Desk"
frontend redesign, and the triage/pre-annotation tooling. Most commits are database snapshot
refreshes; the reviewable surface is the ~40 files that carry real logic.

Both legs ran and were compared. **Selection precondition passed both halves** (77 `tests/` +
130 non-test; `make review-patch` teeth `OK`). **Freshness before: `UNREVIEWED`.**

**Two results shaped everything else:**

1. **The scan leg produced zero distinct real findings.** 21 raw → 21 false positives / already
   adjudicated. Its `nan-injection`, `formatted-sql` and `B110` clusters were all defeated by
   guards, constants or `# noqa: S110` annotations that a line-level matcher cannot see.
2. **`make qa` was green across the range, and both HIGH security findings (H2/H3) and the HIGH
   correctness finding (H1) passed every gate.** That is the defect class this loop exists for.

## 2. Arithmetic

| Leg | Raw | Distinct real |
|---|---|---|
| Delegation — maintenance+core | 3 | 2 |
| Delegation — graph/pdf/app | 13 | 12 |
| Delegation — frontend/UI | 9 | 8 |
| Delegation — Mojo/bench | 15 | 15 |
| Host-derived (selection + scan semantics) | 5 | 5 |
| Scan | 21 | **0** |

**66 raw − 3 merges/superseded − 22 false positives = 43 distinct real (9 H / 19 M / 15 L).**
Zero overlap between the two legs. **L16 was added after the range review closed**, from a
rule.json audit probe — see §6 E.

**One lane claim was disproved** and is recorded so the next session does not re-litigate it: a
lane reported that the range-end `triage_pending_relations.py` imports `triage_preannotate` /
`review_kit` / `typed_judgment`, which do not exist at `6182cd2c7` (would be an `ImportError`).
The range-end file imports none of the three — the lane had read HEAD's copy. **Cost of not
pinning the read ref.**

## 3. HIGH findings

| # | Site | Defect |
|---|---|---|
| **H1** | `helpers/maintenance/enrich_relations.py:2129` (+ read twin `:281`) | DB-sourced `entities.file_path` → `PROJECT_ROOT / fp` → `note_path.write_text(...)`, **no containment check**. A `file_path` containing `../` rewrites the `ticker:` frontmatter of a file outside the vault |
| **H2** | `frontend/src/views/graph.ts:504,508` → sink `:520` | Entity names spliced into `<option>` unescaped → `dl.innerHTML`. 64 `escapeHtml` sites in the same file |
| **H3** | `frontend/src/views/graph.ts:1882,1897` | `file_path` into an unencoded, unquoted `href`; the same file does it right at `:1322`/`:1324` |
| **H4** | `helpers/graph/triage_pending_relations.py:117-130` | `noise_target`'s `len(tl) < 4` rule drops real institutions. Executed: `noise_target('RBI')=True`, `('SBI')=True`, `('ITC')=True` |
| **H5** | `Mojo/src/common/integrity_check.mojo:285-297` | Fileless-entity exemption omits `country`/`index`/company-with-ticker/relation-homed → ~24k legitimate rows scored invalid |
| **H6** | `Mojo/src/common/integrity_check.mojo:398-403` | Hardcoded `KNOWN` IN-list has 15 edge types; `_KNOWN_EDGE_TYPES` has 21 |
| **H7** | `Mojo/bench/run_bench.py:225-238` | The db-integrity leg is documented *"GOLDEN PARITY required and GATED: any mismatch exits 1"* — and parity is **red on the live tree** |
| **H8** | `Mojo/src/common/integrity_check.mojo:469-475` | Circular-relation query omits the `_TWO_WAY_TYPES` `NOT IN` guard |
| **H9** | `Mojo/src/common/integrity_check.mojo:627-634` | `orphan_companies` omits **both** `AND NOT (...)` exemptions |
| **H10** | **the drift mechanism itself** — `Makefile`, `Makefile.mojo`, `tests/run_gate_report.py` | **NEW, found during convergence. Closed in this proposal, §6-F.** The golden-parity differential is the *only* mechanism that can detect drift between `integrity_check.mojo` and `database_integrity_check.py`, and it ran **only** inside `run_bench.py`'s `db-integrity` leg — a **performance** harness behind `make mojo-bench`. `make mojo-test` never touched it, `make qa` contained neither, the repo has no `.github/`, and `gate_query timing --leg db-integrity` returns *"no history for leg 'db-integrity'"*: **it has never appeared in a recorded gate run.** So H7's "GATED" docstring is true only when an operator manually runs a benchmark. This is why the other eight Mojo findings accumulated undetected |

### Classification notes that change the priority

- **H1 is a regression-of-record.** `doc/improvements/completed.md:8823-8826` (entry #333,
  filed 2026-10-01) gated this exact pattern in `derive_insights._paths_by_entity` and called it
  *"the one place a DB string became a read+write filesystem join."* `enrich_relations.py` was
  added 2026-08-25 (`91e341621`), five weeks **earlier** — so that audit missed it, the
  completeness claim is falsified, and the gap is **still open at HEAD**.
- **H2/H3 are regression-of-record.** `completed.md:1970` — entry **#117 "Security hardening
  Phases 1/1b/2/3/5 — XSS routes, escapes, CSP, vendored assets, regression suite"** — landed the
  escape discipline. `graph_docs_ui_redesign.md:88,101` cites `entity_detail.js:310-315` as the
  sanitising reference the port was to preserve. The redesign re-introduced unescaped sinks in the
  same origin. The inherited CSP (`script-src 'self'`) blocks inline handlers, not tag or
  attribute injection.
- **H5–H9 are one root cause with five faces.** The Mojo port was written as a re-implementation
  rather than a port, and each query dropped the original's **deliberate curated exemptions**
  (`_TWO_WAY_TYPES`, `_TICKER_DUP_SUPPRESSED`, `SUPER_SECTORS[...]['childless_ok']`, the
  fileless-entity list). The port therefore reports curation decisions as data corruption. **H7 is
  the delivery defect**: a gate armed against a permanently failing baseline, living in
  `make mojo-bench` — which is not a `make qa` leg, so nobody runs it.
- **H4 is a known class, partially fixed.** HEAD carries
  `_INSTITUTION_SHORT_NAMES = frozenset({"rbi","sebi"})` (`triage_pending_relations.py:212`),
  but `ITC` and any future short name still drop, the `len<4` rule stands, and the gate has no
  drop counter (M10) so the loss is invisible.

## 4. MEDIUM findings

| # | Site | Defect |
|---|---|---|
| **M1** | `helpers/graph/query.py:778 _rebuild_via_swap` | flock derived from the **temp** path (`lock_path` at `:455`), so the serializer the module advertises never covers the rebuild path. HEAD later added `io.lock EX` |
| **M2** | `helpers/pdf/verify_extraction.py:142` | Frontmatter regex has no `re.S` → never strips multi-line YAML; frontmatter tokens counted as content → `doc_coverage` optimistic vs `WARN_DOC_BELOW=0.95` |
| **M3** | `helpers/pdf/verify_extraction.py:68,174` | `WIKILINK_RE` captures `../../../etc/passwd` into the manifest with no containment check (read-only `.is_file()` oracle) |
| **M4** | `app.py:768-789` + `:815-830` | Symlink-following catalog lists `doc/local/**` that `_resolve_doc_path` then rejects → every entry is a 404 |
| **M5** | `frontend/src/views/docs.ts:712-713`; `frontend/src/entity.ts:281,282,301` | `escapeHtml` (`textContent → innerHTML`) escapes `& < >` but not quotes, used inside double-quoted attributes. `entity.ts:254` compensates; these do not. A correct `escapeAttr` exists at `core/markdown.ts:220`, unexported |
| **M6** | `frontend/src/core/markdown.ts:84` → `:172-186` | `processExternalContent` runs **after** `DOMPurify.sanitize` and re-injects `<iframe>` markup; also breaks attribute context when the URL is matched inside an `href` |
| **M7** | `helpers/graph/triage_pending_relations.py:80-82` + `:507` | Comment claims *"Stored canonicalised as ONE row with symmetric=1"*; no `canonical_pair` is written, so a reversed pair lands twice. `semantic_peer` is missing from the symmetric set, contradicting `helpers/graph/edge_conventions.py:22` and `app.py _EDGE_SEMANTICS` |
| **M8** | `helpers/graph/derive_insights.py:1273-1280` | Curly→ASCII quote normalisation applied to **every** line, not just quote lines; `paraphrase` is a persisted column, so the mutation is stored |
| **M9** | `frontend/tests/**` | **No frontend test file exists at `6182cd2c7`** — five views, two render pipelines, zero guard coverage over H2/H3/M5/M6 |
| **M10** | `helpers/graph/extract_relations.py:1557` | Noise gate `continue`s with no counter, so neither the stderr summary nor `--counts-json` reports what was dropped |
| **M11** | `app.py:1361` | `except Exception: # noqa: S110  # no sidecar -> scan fallback` claims a bound the code does not make — a genuine bug in `search_docs` downgrades silently with no log line |
| **M12** | `integrity_check.mojo:767-770` | Duplicate-normalization query lost `WHERE file_path IS NOT NULL` |
| **M13** | `integrity_check.mojo:829` | `nm_warn` omits `fileless_duplicate_groups` (423 live rows) |
| **M14** | `integrity_check.mojo:862-884` | Duplicate-ticker grouping has no `_TICKER_DUP_SUPPRESSED` filter |
| **M15** | `integrity_check.mojo:1288-1294` | `v_node` SQLite-side check omits `'country','index','person'` → false cache-drift error |
| **M16** | `integrity_check.mojo:667-673` | `super_sector_orphans` omits the curated `childless_ok` exclusion |
| **M17** | `Mojo/bench/mojo_graph_algos.py:196` | `_JACCARD_SQL` commented *"onager_link_prediction's SQL verbatim"*, but that function's DB path moved to a 2-hop CTE (`onager.py:876-951`); the table function is now synthetic-branch only |
| **M18** | `Mojo/bench/run_bench.py:71`; `integrity_check.mojo:1400`; `corpus_sweep.mojo:39`; `mojo_regex_corpus.py:48,76` | Hardcoded literal `/tmp` paths with `# noqa: S108` suppressing the signal instead of fixing it. Fixed shared path, no lock → concurrent corpus legs collide |
| **M19** | `corpus_sweep.mojo:49-54` + `mojo_regex_corpus.py:41-45` | Both sides take `parts[2]`, discarding everything after the first `---`. **Parity stays green precisely because both sides truncate identically** |
| **M20** | `integrity_check.mojo` — `duplicate_tickers` grouping (pre-fix `:928-953`) | **NEW, found during convergence.** A ticker's names were packed into one `";"`-delimited string and split back with `split(";")`, so `;` did double duty as separator *and* list delimiter with no escaping. 17 live company names carry the HTML entity `&amp;`, each fabricating a phantom duplicate-ticker group: `errors` read 18 against the Python original's 0, and the single genuine group (GOOGL) was never suppressed. Replaced with an ordered-run walk over the existing `ORDER BY ticker, name` — no delimiter to collide |
| **M21** | `integrity_check.mojo` — `meaningful_tokens` (pre-fix `:216-234`) | **NEW, found during convergence.** The Python `_meaningful_tokens` returns a **set**; the Mojo port returned a **list**. A repeated word therefore inflated `len(ci)`, changing which branch `fuzzy_match` takes (its single-token fast path requires `len == 1`) and inflating the `len(ci) >= 2` subset test. 658 live company names carry a repeated token (`Mahindra & Mahindra`, `Johnson & Johnson`, `SBI Mutual Fund - SBI … ETF`), so fuzzy-match outcomes were being shifted in both directions |

## 5. LOW findings

| # | Site | Defect |
|---|---|---|
| **L1** | `helpers/maintenance/snapshot_db.py:821` | SQLite parquet export has no `ORDER BY` while the DuckDB export gained `ORDER BY ALL` in this same range. Asymmetry observed; **not proven to churn** (a full scan is normally rowid-ordered) |
| **L2** | `helpers/pdf/verify_extraction.py:189` | Dead disjunct — `fail_below <= doc_coverage < WARN_DOC_BELOW` is subsumed by the preceding test |
| **L3** | `helpers/pdf/verify_extraction.py:176` | `MIN_SIGNIFICANT_NUM_DIGITS` measured with `len()` on the raw token, so `"1.5"` counts as 3 digits |
| **L4** | `helpers/graph/derive_insights.py:1668,1728` | `apply_quotes`/`apply_metrics` annotated `-> int`, return `ReplaceResult`. `ty` passes — **not gate-covered** |
| **L5** | `app.py:197-204` | `read_only=True` comment omits that a cold/stale cache sends a GET down the read-write build path under an exclusive flock |
| **L6** | `frontend/src/views/graph.ts` — 12 sites | `escapeHtml`-only inside `title=` / `data-*`; every consumer re-encodes downstream, so LOW |
| **L7** | `frontend/src/core/markdown.ts:112` | TOC `href` without `encodeURIComponent`. `ripwire` confirms **0 callers** — dead code |
| **L8** | `frontend/package.json` (`scripts.build`) | `--entry-names=[name].bundle` unquoted — a POSIX character class, subject to glob expansion |
| **L9** | `.opencodereview/rule.json` | OCR's `default_path` class silently ate all 12 `tests/fixtures/**` goldens — **closed in this proposal, §6** |
| **L10** | `.opencodereview/rule.json` | `.mojo` not in the default extension allow-list → `Makefile.mojo` unreviewable while `.gitignore` is reviewable — **closed in this proposal, §6** |
| **L11** | `review_scan.py` roster semantics | Line anchors are range-end relative; adjudicating against a moved tree reads the wrong code. Wants a line in the roster header |
| **L12** | `review_scan.py` bandit leg | Keeps 5 `B110` the repo has already adjudicated with `# noqa: S110` |
| **L13** | OCR accounting | `total_files` 532 vs git's 534 — two `snapshots/**/*.parquet` appear in neither list |
| **L14** | `integrity_check.mojo:106-127` (also `:797-800`, `:821-825`) | `filename_ok`/stem derivation diverges from `_check_filename_format`; unreachable on the live corpus, so goldens still match |
| **L15** | `integrity_check.mojo:303` | `file_exists` is cwd-relative; the original resolves against `self.base_path`. `run_bench.py:102` pins the cwd, so only a bare invocation is affected |
| **L16** | `.opencodereview/rule.json` / `vendor/llamacpp/health.py` | `provider_directory` is the one built-in class a `rule.json` `include` **cannot** override (`unsupported_ext` and `default_path` both can), and 1.12.13 has no `--include` escape hatch. 130 lines of first-party probe code sat under `vendor/` behind an include entry that read as coverage but did nothing — **closed in this proposal, §6** |

## 6. Review-apparatus fixes (applied in this proposal)

Five of the six LOW tooling findings are properties of the **selection and reporting rules**, not
of any file's contents — no lane and no scanner could find them, because they are only visible when
you compare the roster against the procedure that claims to describe it.

**A. `tests/fixtures/**` re-admitted** (L9). OCR 1.12.13's `default_path` built-in class dropped
all 12 fixtures this range added — including the golden evidence for the Google-Finance fallback
(`245430507`). **This is the fixture spine, not the test spine: check 1 in `ocr_review.md` §1
stayed green the whole time**, because `tests/**/*.py` selected fine. The same class had already
dropped `frontend/tests/guards.test.ts` on 1.12.11.

- `rule.json`: `"tests/fixtures/**"` added to `include`.
- `review_selection.py`: `"tests/fixtures/"` added to `PRODUCT_FAMILIES`.
- **Verified:** reviewable 207 → 220; `default_path` 12 → 0 in the `exclude_reason` census.
- **Mutation-verified:** deleting the include makes the teeth fire —
  `SELECTION DEFECT: rule-covered families changed but nothing from them was selected: tests/fixtures/`
  with the excluded paths listed.

**B. `Makefile.mojo` re-admitted** (L10). `.mojo` is not in the default extension allow-list, so
the 3 694-byte file defining the entire `mojo-build`/`mojo-test`/`mojo-bench` rule graph was
`unsupported_ext`. The include is load-bearing: removing it drops 20 `Mojo/` files, which the teeth
catch. No product family is possible for a root-level file — the teeth are directory-prefixed.

**C. `review_freshness.py` — one ledger store across worktrees.** `REPO_ROOT` was derived from
`Path(__file__).parents[2]`, so a worktree (a full checkout with its own `outputs/`) got a private
ledger. Measured on this review: a `--record` from the worktree wrote 1 row while the canonical
11-row ledger was untouched, requiring a manual merge — and the canonical ledger kept claiming
`UNREVIEWED`, so the next session would re-pay all 48 commits.

- Fix: `LEDGER` resolves from `git rev-parse --path-format=absolute --git-common-dir`'s parent,
  which is the same directory in a main checkout and in every linked worktree
  (`…/.git/worktrees/<name>` is the per-worktree dir and is exactly what must not be followed).
  The ledger, its diffcache and its flock all follow together.
- Git commands still run in the **worktree** (`REPO_ROOT` unchanged for `cwd=`), because the tree
  under review is the one whose HEAD and diff matter.
- Bare-repo guard asks git (`--is-bare-repository`), not a `HEAD` sniff — a non-bare git dir also
  has `HEAD`, so that heuristic always tripped the fallback (found and fixed during this work).
- 4 regression tests in `tests/test_review_tooling.py`: worktree → main checkout; main → itself
  (not its parent); bare repo → falls back; not-a-repo → falls back.

**D. `doc/procedures/ocr_review.md` re-stamped** to `v1.12.13 (fabbdb2)`, with the three
re-derived selection facts recorded in §S9, the `exclude_reason` per-entry correction, the
`make review-patch` Known-limits row updated, and a forward-pointer added to the §1 check-1
paragraph. The stamp now states explicitly which claims are **still** 1.12.11 and unverified: the
managed-mode tool inventory, `ocr review`'s flags, the `api_key_cmd` route and key bridge, the
`-B` injection behaviour, and the two silently-ignored schemas.

**E. `vendor/llamacpp/health.py` → `helpers/misc/llamacpp_health.py`** (L16). Found by a 10-path
regression guard added to the rule.json audit probe, not by the range review. `provider_directory` is
the one built-in class a `rule.json` `include` **cannot** override — the opposite of `default_path`
and `unsupported_ext`, which the A and B fixes both flipped — and 1.12.13 exposes no `--include`
escape hatch (`ocr delegate preview --help` has only `--exclude`, which merges *into* the excludes).
The file carried an exact-path `include` entry, so `rule.json` read as if it were covered; it was
excluded anyway. 130 lines of first-party probe code (`/v1/models` identity checks, `SERVER_DIMS
768`, `local_embedder.DIM 384`), silently unreviewable.

- Moved to `helpers/misc/` — already covered by `helpers/**/*.py`, and the file's own docstring
  already pointed at its sibling `helpers/misc/llama_servers.sh`. `vendor/llamacpp/` is left holding
  only genuinely vendored artefacts (`PROVENANCE.md`, `build.sh`, `patches/`).
- Updated: `Makefile` target body + both help texts, and the file's own usage line.
- Dropped the now-inert `"vendor/llamacpp/health.py"` include.
- **Verified:** reviewable 918 → 919; `provider_directory` 4 → 3 (the 3 remaining are genuinely
  vendored); `make -n llamacpp-health` resolves to the new path; the script runs.
- **Pointer sweep:** no live references to the old path. The two hits in `completed.md` and
  `archive/tooling/range_4fd3a20f_review_remediation.md` are left alone — they are records of what
  was true then, and rewriting them would falsify the history.
- `ocr_review.md` §S9 now carries the precedence table, so the next session does not have to
  rediscover that the three classes are not equal.

### 6-F. Mojo↔Python parity: the gate (H10), then the sites

**Why the copies drifted** — measured, not inferred:

| | born | commits since | last touched |
|---|---|---|---|
| `helpers/misc/database_integrity_check.py` | 2026-08-15 (`2587a3a17`) | 18 | 2026-10-05 |
| `Mojo/src/common/integrity_check.mojo` | 2026-08-30 (`33308f489`, *"integrity port"*) | 4 | 2026-09-18 |

A hand-maintained port with no re-derivation. Mojo froze on 2026-09-18; Python moved eight times
in the seventeen days after. The drift mechanism is **H10** — the differential existed and was
never installed as a gate.

**Which side is the oracle** — verified rather than assumed, because it inverts the obvious
reading. The parity report shows `python=0` on nearly every error key, which looks like a broken
oracle. Run standalone, with no Mojo in the process, `DatabaseIntegrityChecker` reports
`valid=26162 invalid=0`, `orphan_companies: 0`, `relations.unknown_type: 0`, every `errors: 0`.
**Python is sound; all mismatches were Mojo staleness.** Converge Mojo → Python.

**The differential is complete** — `0 MISSING-in-mojo`, `0 EXTRA-in-mojo`. Note the corollary
that constrains the port: the fixture's per-check key list is authoritative, so emitting a key the
fixture does not collect (e.g. `two_way_exempt`) reads as `EXTRA in mojo` and fails the gate.

**Order matters.** H10 was done **first**, so each site fix is verified by the thing that will
guard the rest. Sequence: gate → 7 mechanical sites → H5 (needs a relations query ported across).

- **Gate (H10), applied.** New `make mojo-parity` in `Makefile.mojo` + a delegating target in
  `Makefile` + `Step("mojo-parity", (_MAKE, "mojo-parity"))` in the `qa` gate's step list. Two
  non-obvious requirements, both found by running it:
  - the bridge resolves libpython from `python3` **on PATH**, so the recipe prepends `.venv/bin` —
    without it the leg aborts `symbol not found: Py_Initialize` (the trap `_venv_env()` documents);
  - the checker emits multi-megabyte single-line dumps, so output is grepped and capped at 300
    chars, with the **binary's** exit code taken rather than grep's.

**Two findings this work surfaced that the original review did not have** — M20 and M21 in §4.
Both are latent encoding bugs, not data drift: they would have been reported as "Python and Mojo
disagree about fuzzy/ticker data" forever, because the cause is a delimiter collision and a
set/list confusion in the Mojo port itself.

**Result: `parity: 89 / 89 keys match` — GOLDEN PARITY OK**, from `65 / 89` with 24 mismatches.
The 24 reduced to 11 root causes:

| Cause | Keys recovered | Finding |
|---|---|---|
| fileless-entity exemption missing 3 of 5 classes | 8 `hdr.*` | H5 |
| `KNOWN` edge-type list 15 vs 21 | 2 `relations.*` | H6 |
| circular query missing the `_TWO_WAY_TYPES` guard | 2 `relations.*` | H8 |
| orphan query missing both `AND NOT (...)` exemptions | 2 `orphan_companies.*` | H9 |
| `super_sector_orphans` missing the curated `childless_ok` set | 2 `hierarchy.*` | H16 |
| `v_node` vertex kinds 7 vs 10 → phantom cache drift | 2 `cache_consistency.*` | M15 |
| duplicate-normalization missing `file_path IS NOT NULL`; `nm_warn` missing `fileless_duplicate_groups` | 3 `normalization.*` | M12, M13 |
| `_TICKER_DUP_SUPPRESSED` absent + `;`-delimiter round-trip | 2 `duplicate_tickers.*` | M14, **M20** |
| tokenizer returned a list where the original returns a set | (count, in `warnings`) | **M21** |
| canonical pair ORDER sorted the rendered string, not the `(name_a, name_b)` tuple | 1 `fuzzy_duplicates.pairs` | — |

The last one is worth naming because it is invisible to any count-based check: the string had the
**same pairs and the same total length (623,961 chars)** and diverged only in sequence, so
`warnings` matched while `pairs` did not.

**The gate is load-bearing — mutation-verified, not assumed.** Deleting the `rel_homed` arm of
the H5 exemption drops parity to `81 / 89` with 8 `hdr.*` mismatches and the target exits
non-zero. A second mutation, removing the `country` arm, left parity **green at 89/89** — because
`country`/`index` entities are also `listed_in`/`listed_on_index` endpoints, so the `rel_homed`
arm already covers them. Those three arms are redundant on this corpus; they are kept because the
Python original keeps them and the differential's value is in matching it, not in matching it
minimally.

## 7. Slice plan

| Slice | Contents | Gate-visibility |
|---|---|---|
| **S1** | H1 containment in `enrich_relations.py` | needs a traversal regression test (write outside `findata/` must be refused) |
| **S2** | H2/H3/M5/M6 escaping + sanitiser ordering | needs the `frontend/tests/` harness M9 says does not exist — **S2 and M9 are one slice** |
| **S3** | H5–H9, **H10**, M12–M16, **M20, M21**, L14, L15 — Mojo parity restoration | **H10 (the gate) was done FIRST**, before any site fix — see §6-F |
| **S4** | H4 + M10 — `noise_target` short names + drop counter | **extractor rule → eval-gate bullet required** |
| **S5** | M1 lock path, M7 symmetric canonicalisation, M2/M3/L2/L3 PDF verifier, M4 symlink catalog, M8/M11/L4/L5, L1 | independent; group by file |
| **S6** | L11/L12/L13 scan-roster hygiene | roster header line; bandit noqa handling |

**S2 before S3** is recommended: M9 (no frontend harness) is what let H2/H3 through a green gate,
and the same absence will hide the fixes.

## 7-B. Remediation outcomes (2026-10-14)

All 43 findings plus the 2 added by the convergence work were actioned. The
columns distinguish **fixed here** from **already fixed downstream** — several
findings were recorded against the range end and the operator closed them in a
later commit, so re-fixing them would have been churn.

| Slice | Findings | Outcome |
|---|---|---|
| **S1** | H1 | **Fixed.** `_vault_note_path()` guards the read twin and the write twin (`enrich_relations.py:292`, `:2352`), mirroring `derive_insights.py:2124-2134`. 6 new tests; mutation-verified — disabling the guard fails 4 of them. One pre-existing fixture had to be re-shaped: it leaned on pathlib's "absolute right-hand side wins the join" to stay hermetic, which is exactly the hole the guard closes. |
| **S2** | H2, H3, M5, M6, M9 | **Fixed.** `escapeAttr` promoted to an exported single source in `core/dom.ts`; `core/markdown.ts`'s private duplicate removed. **29** attribute-context sites converted, not the 3 recorded — a source sweep found `docs.ts`, `entity.ts` and `graph.ts` all affected. H3's two `href`s `encodeURI`'d. M6: `processExternalContent` moved **before** `DOMPurify.sanitize` (its own comment already claimed that ordering) and the YouTube regex anchored to a standalone `<p>`. M9: new `frontend/tests/escaping.test.ts` (9 tests). |
| **S3** | H5–H10, M12–M16, M20, M21, L14, L15 | **Parity gate installed and 89/89 green** — see §6-F. |
| **S4** | H4, M10 | **H4 fixed, M10 already fixed.** `noise_target`'s length rule now discriminates on **shape**: an all-caps 2–3 char token is an acronym (entity), a lowercase stub is a fragment. The prior three-name allow-list (`rbi`/`sebi`/`mhp`) left 10 of 28 real entities still classified as noise; all 30 probes now pass and 10 fragment probes still fail correctly. `_GENERIC_ACRONYMS` keeps `ABC`/`CDMO` noise **by decision**. M10 was already wired at HEAD — `_SKIP_STATS["noise_gate"]` feeds both the stderr summary and `--counts-json`. |
| **S5** | M1–M4, M8, M11, L1–L5 | **Mixed.** Already fixed downstream: M1 (`io_lock` on the live path), M7 (`canonical_pair`), M4's premise, L4, L5. Fixed here: M2/L2/L3/M3 (PDF verifier), M4, M8, M11, L1. |
| **S6** | L11, L12, L13 | **Fixed.** Roster header states that anchors are relative to the range head and gives the `git show` incantation; `B110 → S110` added to `NOQA_MAP` with its rot note; the `total_files` vs `git diff` delta recorded in `ocr_review.md` with the set-difference instruction. |

**Two findings were partly wrong, and that is worth recording:**

- **M8's premise is false.** It claimed the curly→ASCII quote normalisation was
  applied "to every line". The guard `if "“" in ln or "”" in ln` was present at the
  range end — verified with `git show 6182cd2c7:`. The real defect is its
  **inverse**: the guard hardcodes 2 of the 4 characters `_CURLY_QUOTES` maps, so a
  line carrying only `„` or `‟` was never normalised. Fixed to
  `any(c in ln for c in _CURLY_QUOTES)`.
- **M5 understated its own blast radius** by 10× (3 recorded sites, 29 actual). That
  is the argument for the M9 harness rather than a one-off patch: a hand-fixed
  count is a snapshot, a sweep is a control.

**New findings surfaced by the fixes themselves** — not in the original 43:

- **M20** (`;`-delimiter round-trip corrupts any name containing `;`; 17 live
  `&amp;` names fabricated phantom duplicate-ticker groups), **M21** (tokenizer
  returned a list where the original returns a set; 658 companies have a repeated
  token), **H10** (the parity differential was never a gate). All three are in
  §3/§4 and detailed in §6-F.

**Not done / deliberately out of scope:** the proposal's own acceptance items that
need an operator decision — the empty `review_fixes` commit message, and
`make search-fresh` in the main checkout.

## 8. Acceptance criteria

1. `make qa` green (ruff, md-lint, ty, pytest, static_checks).
2. **Mutation-verified tests** — each fix neutered, its new test RED, restored. A fix whose test
   cannot fail is a docstring (the c83a11d9 lesson).
3. **Fixture reality re-checked** for any new test — the trigger phrasings must match the
   PATTERNS they claim to exercise; referenced constants must be consumed.
4. **Eval gate (required — this proposal touches an extractor rule).** H4's fix changes
   `noise_target`, which gates what relations reach `graph_edges`. Run
   `helpers/misc/ontology_eval_gate.py` over the frozen question set between dry-run and canonical
   apply, and record the before/after numbers in the completed entry. S7's `semantic_peer`
   symmetric-flag change (M7) is query-visible for the same reason and rides the same gate.
5. **`Mojo/bin/integrity_check` parity green**: `MOJO_INTEGRITY_PARITY=1` reports
   `89/89 keys match` and exits 0 on the live tree. Until then `make mojo-bench` is a known-red
   gate and should be labelled as such rather than discovered.
6. `selection-teeth OK` from `make review-patch`, **mutation-verified** against each new
   `PRODUCT_FAMILIES` entry.
7. Pointer sweep after every fix that moves or deletes a path.
8. Re-review the same ref after applying — **a fix is not closed until it has been re-reviewed**
   (`doc/procedures/ocr_review.md` step 8).

## 9. Carried forward

- **A parity gate both sides can satisfy by being equally wrong is not a parity gate** (M19). The
  Mojo corpus legs truncate identically on both sides, so their check is green by construction.
  Worth a general rule: when comparing two implementations, the comparison must be able to fail
  from an input both sides mis-handle.
- **A curation decision encoded as an absence is the thing a re-implementation drops.** Every one
  of H5–H9 is a curated exemption in `database_integrity_check.py` that the Mojo port left out.
  Porting by re-reading the code loses them; porting from the *set of decisions* keeps them.
- **"The one place X happens" is a claim that needs a sweep behind it.** `completed.md:8823` made
  that claim about `PROJECT_ROOT / file_path` five weeks after a second instance landed
  (`91e341621`). The audit that wrote it was correct about what it checked and wrong about what
  existed.
- **A fixture spine and a test spine fail independently.** `tests/**/*.py` was selected the whole
  time 12 goldens were invisible. Check 1 was green and wrong.
- **"I annotated it" is not "the finding is adjudicated"** (completed.md #380) — reinforced here by
  L12 (bandit ignoring `# noqa: S110`) and by the three-tool anchor family.

## 10. Not filed, deliberately

The review report, both rosters and the freshness row live in `outputs/reviews/` (gitignored,
machine-local) per the 2026-10-11 operator ruling. **This proposal cites that directory and does not
restate its evidence tables** — the evidence must be copied there, never into `doc/`.
`doc/procedures/ocr_review.md` was re-stamped (§6 D) but carries no finding tables.
