---
title: "Make the BOLD_LINE_RE fuzz guard fail on the failing path"
status: executed
filed: "2026-11-10"
executed: "2026-11-10"
completed_md: "501"
area: "tests/test_fuzz_regex.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Make the BOLD_LINE_RE fuzz guard fail on the failing path

**Date:** 2026-11-10 · **Status:** EXECUTED · **Completed:** completed.md #501 ·
**Area:** `tests/test_fuzz_regex.py` (test-only) against
`helpers/pdf/pdf_local.py:76`

## 1. Motivation

On 2026-11-10 the operator rewrote `BOLD_LINE_RE` from a lazy
`(?:\*\*.+?\*\*\s*)+` to a disjoint inner alternation:

```python
BOLD_LINE_RE = re.compile(r"^(?:\*\*[^*\n]*(?:\*(?!\*)[^*\n]*)*\*\*\s*)+$")
```

The rewrite is correct — verified by corpus differential and adversarial
timing (section 2) — but reviewing it exposed that **the two guard tests
that exist to prevent this exact regression cannot see the regression
they guard against.** Monkeypatching the OLD pattern into
`tests/test_fuzz_regex.py` and running it:

```text
$ .venv/bin/python3 -m pytest tests/test_fuzz_regex.py -k bold -p no:randomly
2 passed, 11 deselected in 0.61s
```

Both guards are green with the exponential pattern installed. The ladder
test is green because it measures the **success** path, where both
patterns are linear; the Hypothesis test is green because random sampling
never assembles the ~25 well-formed segments a blowup needs.

That is a false-negative guard, and it is load-bearing: the
`make advisory` `fuzz` leg was red at run 4150 on this very test, and the
2026-10-01 predecessor proposal
(`../archive/testing/bold_line_ratio_floor.md`, #326) already spent one
arc loosening the *measurement* after flakes. Loosening measurement
without checking discrimination is how a guard becomes decorative.

## 2. Evidence (measured 2026-11-10, this box)

### 2.1 The old pattern is exponential, not quadratic

The in-code comment at `tests/test_fuzz_regex.py:265` calls this shape
"quadratic, bounded". It is worse. On a line of well-formed segments with
a **non-closing terminator** — so the overall match fails and the engine
retries every segment boundary — each added segment doubles the time, on
an input only 6 bytes longer:

| segments | bytes | OLD `(?:\*\*.+?\*\*\s*)+` | growth |
|---|---|---|---|
| 10 | 61 | 0.19 ms | — |
| 14 | 85 | 2.99 ms | 1.96x/segment |
| 17 | 103 | 24.35 ms | 2.03x/segment |
| 20 | 121 | 188.04 ms | 1.97x/segment |
| 23 | 139 | 1515.56 ms | 2.00x/segment |
| 24 | 145 | 3038.33 ms | 2.00x/segment |

Doubling per 6 bytes is exponential in *length*: 145 bytes already takes
3 s. A single hostile markdown line is a denial of service against
`pdf_local` ingest.

The replacement is flat at **33 ns/byte** across six doublings
(3 KB to 96 KB): 0.102, 0.207, 0.395, 0.792, 1.522, 3.169 ms.

### 2.2 The corpus does not exercise the difference

All 28 `Reports/*.pdf` extracted through the real pymupdf4llm path,
`_normalize_headings` run twice (old pattern vs new) and diffed page by
page:

| Configuration | Result | Verdict |
|---|---|---|
| 28 PDFs / 538 bold-ish lines, old vs new | 0 differing output lines | behavioural parity on the input domain |
| 111,024 `**` lines in `findata/` + `doc/`, old vs new | 48 match differences | non-PDF markdown |
| those 48 through the real use site (`_fix_heading_line`, which also requires a pipe) | **1** output difference | `findata/Companies/Retail/Vaibhav_Global.md:30` |
| that one line | unbalanced bold (odd count of `**`) | old rescued it by accident |

The documented rescue case still works end-to-end:
`**Logistics** **<u>Delhivery | Large Cap | Logistics</u>**` becomes
`## Logistics` / `## Delhivery | Large Cap | Logistics`, and
`**Some bold sentence.**` is still left alone.

### 2.3 Why each guard is blind

| Guard | Shape it builds | Why it cannot see the bug |
|---|---|---|
| `test_bold_line_regex_scales_subquadratically` | `("**" + "a"*n + "** ") * n`, n = 160/320/640/1280 | **Length grows as n squared**, so each rung quadruples the input. Measured for OLD: 0.36, 1.41, 5.81, 23.62 ms, ratios 3.87, 4.12, 4.07 — i.e. exactly linear. The 8.0 threshold can only catch a *cubic* regression. Its docstring claims "doubling input must not quadruple cost", but `n` is not the input size. |
| `test_bold_line_regex_no_catastrophic_backtracking` | random `st.text` from `BOLD_ALPHABET`, `max_size=900` | The alphabet is adequate (it contains asterisks, letters, spaces and the pipe character) — but the blowup needs ~25 *complete* bold segments before the non-closing terminator. Random text never assembles that; the shapes it does generate terminate in microseconds for both patterns. |

One important negative result: **widening the alphabet would not fix
guard 2.** The blindness is structural — a success-path measurement plus a
shape random sampling cannot assemble — not an alphabet gap. Only a
deterministic adversarial ladder on the failing path closes it.

### 2.4 The replacement for guard 1, calibrated

Failing-path ladder: `("**" + "a"*1000 + "** ") * segments + "|"` — a
long pad keeps each segment cheap for a linear engine while the
non-closing pipe forces the failure path. Rungs at 8 and 32 segments are
8,041 to 32,161 bytes (4x), so a linear engine must score ~4.0:

| Pattern | rung 8 (8 KB) | rung 32 (32 KB) | ratio |
|---|---|---|---|
| NEW | 0.362 ms | 1.439 ms | **4.11** (median of 5 rounds; 10 trials: min 3.82 / max 4.15) |
| OLD | 3.318 ms | **timeout** | fails the cap outright |

Against a cap of 2 s per rung the OLD pattern does not merely score badly
— it is *unable to return*, which is the honest assertion. Separation is
~250x on the rung that does finish and unbounded on the one that does not,
so the threshold cannot flap.

## 3. Design

**S1 — replace the success-path ladder with a failing-path ladder.**
`("**" + "a"*1000 + "** ") * segments + "|"` at segments 8 and 32; a
per-rung `SIGALRM` cap of 2 s; ratio = **median of 5 back-to-back
rounds**, following the `tests/test_performance.py`
`_median_round_ratio` idiom (it times both sizes in the same round, so a
stalled round is discarded rather than averaged in); assert
`ratio < 8.0` across a 4x length step, i.e. linear with 2x headroom. A
rung that exceeds the cap fails immediately with a message naming the
lazy-`.+?` return instead of hanging. SIGALRM has in-repo precedent
(`tests/test_derive_insights.py:317-337`).

**S2 — keep the success-path ladder, tell the truth about it.** Retain
`test_bold_line_regex_scales_subquadratically` as a positive-path guard
against a *cubic* regression, and correct its comment: the shape is
quadratic in length by construction, so ~4x per rung is linear, and the
8.0 budget is a 2x-headroom linearity check — not the AVAIL-2
discriminator the current text claims. Renaming it away would break
gate-run history for no gain.

**S3 — make the Hypothesis guard adversarial-structured.** Replace random
`st.text` with a composed strategy: 1-40 complete bold segments built
from a fixed segment text, plus a non-closing terminator drawn with
`st.sampled_from` (a pipe, `!`, `(`, a letter, and "nothing"). That
exercises the failure path at every segment count without relying on
chance. **Do not rely on Hypothesis' `deadline`** — it fires *between*
examples, so it cannot interrupt a catastrophically backtracking regex; measured
2026-11-10, the structured strategy with `deadline=500` ran >20 s at 30
segments with no deadline violation, i.e. the test HANGS instead of failing
(and hangs hard enough to blow a whole pytest leg's execution timeout).
Guard each call with an explicit SIGALRM cap (`_call_with_cap`, 1 s — a
healthy call is sub-millisecond at every size here) and raise a named
`AssertionError` carrying the segment count and terminator when it fires.
`@settings(deadline=None, max_examples=200)`.

**S4 — pin the semantics deliberately.** Record what the new pattern must
and must not match, in `tests/test_pdf_local.py`: the documented
`**Sector** **<u>Company | Cap | Sector</u>**` rescue (matches), plain
`**Some bold sentence.**` (no rescue), and the newly excluded
**unbalanced** forms `***A | B***` and `**A | B***` (no match).
Unbalanced bold is malformed input; the old pattern's "match" was
accidental, and `pdf_local` never sees it (0 differing lines across the
28-PDF differential). Recording the decision stops a future session from
"restoring" the tolerance as a bug fix.

Slices are independently landable in this order: S1 (closes the hole), S3
(widens coverage), S2 (comment correction, no behaviour change), S4
(decision record). S1 and S2 touch the same region, so they land together.

### As built (2026-11-10)

All four slices landed in one working-tree change; nothing is committed.

- `tests/test_fuzz_regex.py` — module docstring now names `BOLD_LINE_RE` as a
  third guarded regex and states the `deadline` caveat; a new section at the
  bottom holds `_BOLD_GUARD_CAP_S = 1.0`, `_BoldGuardTimeout`,
  `_call_with_cap(fn, text, cap_s)`, the structured
  `test_bold_line_regex_no_catastrophic_backtracking`
  (`@settings(deadline=None, max_examples=200)`), the failing-path ladder
  (`_BOLD_FAIL_PAD = 1000`, `_BOLD_FAIL_RUNGS = (8, 32)`,
  `_BOLD_FAIL_CAP_S = 2.0`, `_BOLD_FAIL_ROUNDS = 5`,
  `_BOLD_FAIL_MAX_RATIO = 8.0`) and the cap self-test. The success-path
  ladder keeps its logic; `_BoldLadderTimeout` was superseded by
  `_BoldGuardTimeout`, which both ladders share.
- `tests/test_pdf_local.py` — `BOLD_LINE_RE` imported; three S4 tests pin the
  rescue shape (matches), the two unbalanced forms (no match), and the
  `Vaibhav_Global.md:30` soup shape (`match` is `None` and
  `_normalize_headings` is a no-op).

Discrimination proved end to end, not just argued: a pytest plugin that swaps
`sys.modules['test_fuzz_regex'].BOLD_LINE_RE` for the old pattern turns
`-k bold` from `4 passed` into `2 failed, 2 passed in 20.68s`. Both new
guards go red; the ladder fails with "did not finish a 32-segment rung within
2s". Driver: `/mnt/data/tmp/boldreview/prove_discriminate.py`. (A `-p
patchplug` plugin that imports the test module does NOT work —
`ModuleNotFoundError`; patch `sys.modules` from
`pytest_collection_modifyitems` instead.)

| Check | Result |
|---|---|
| `-k bold`, shipped pattern | `4 passed, 11 deselected` in 2.94-2.96 s, 5 consecutive runs |
| `-k bold`, OLD pattern injected | `2 failed, 2 passed` in 20.68 s |
| fuzz+pdf trio | `67 passed in 3.98 s` |
| `tests/test_fuzz_*.py -n auto`, 3 runs | `198 passed` each (21.58 / 21.47 / 20.62 s) |
| `ruff check --select S,UP,C901` + `ruff format --check` | clean |
| `python3 helpers/misc/markdown_lint.py` | 0 violations |

## 4. Acceptance criteria and shakedown

1. `BOLD_LINE_RE` monkeypatched back to `^(?:\*\*.+?\*\*\s*)+$`
   makes `pytest tests/test_fuzz_regex.py -k bold` **fail** — the
   discriminator is "the guard catches the old pattern", not "the guard
   passes".
2. With the shipped pattern the same command passes — measured 3.07 s,
   dominated by the failing-path ladder's 10 timed rungs (the success-path
   ladder and the Hypothesis test are each well under a second).
3. `.venv/bin/python3 -m pytest tests/test_fuzz_regex.py tests/test_pdf_local.py tests/test_pdf_conv_md.py -q` green.
4. `make lint-audit` clean (`ruff check --select S,UP,C901 .`) and
   `ruff format --check` clean — a `SIGALRM` import plus a new helper must
   not add a lint error.
5. Timing-shaped criterion, so repeated: the new ladder passes **5
   consecutive runs** on a quiet box, and 3 under `make fuzz` (`-n auto`,
   the contended case that produced advisory run 4150's red).

| Projected outcome | Today | After |
|---|---|---|
| guard catches the exponential pattern | no (2 passed) | yes (fails loudly) |
| guard runtime on the shipped pattern | 0.61 s (blind) | 3.07 s (discriminating) |
| worst-case ingest, 145 B hostile line | 3.0 s | < 1 ms |

## 5. Risks

- **The 2 s cap is generous enough to slow a red gate run** — a
  regression costs one 2 s rung, not a hang. Accepted: the alternative (no
  cap) blocks the `fuzz` leg for the full pytest timeout.
- **SIGALRM is process-global** — safe under `pytest-xdist` process workers
  and restored in `finally`; revisit if the suite grows a threaded runner.
- **Median-of-rounds could mask a genuine one-round stall** — discarding
  one stalled round is the intent; a *systematic* regression stalls every
  round and still fails.
- **S4 freezes a semantic narrowing** — if a future corpus turns out to
  rely on unbalanced-bold rescue, this test looks like the obstacle.
  Mitigation: the case table's comment names the reason (accidental match,
  0/28 PDF differential).

## 6. Non-goals

- No change to `BOLD_LINE_RE` itself — the 2026-11-10 rewrite is correct
  and stays.
- No change to `_normalize_headings`, `_fix_heading_line`,
  `_split_sector_glue` or `CAP_TAIL_RE`.
- No audit of other AVAIL-2-style guards beyond noting that
  `tests/test_performance.py` already uses the correct median-round idiom;
  porting that idiom elsewhere is a separate slice.
- No re-litigation of #326's ratio-floor decisions (min-of-5, >=1 ms
  scored bases, 3 attempts) for the success-path test — they stay.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-11-10 | `pytest tests/test_fuzz_regex.py -k bold -p no:randomly` with OLD monkeypatched | `2 passed, 11 deselected in 0.61s` | the false negative |
| 2026-11-10 | existing ladder, OLD pattern | `[0.36, 1.41, 5.81, 23.62] ms`, ratios `[3.87, 4.12, 4.07]` | PASS — cannot see it |
| 2026-11-10 | existing ladder, NEW pattern | `[0.20, 0.74, 2.99, 12.45] ms`, ratios `[3.79, 4.04, 4.16]` | PASS |
| 2026-11-10 | OLD, failing shape n=10 to 24 | 0.19 to 3038.33 ms, 2.00x/segment | exponential in length |
| 2026-11-10 | NEW, failing shape, 3 KB to 96 KB | 0.102, 0.207, 0.395, 0.792, 1.522, 3.169 ms | 33 ns/byte, linear |
| 2026-11-10 | 28 `Reports/*.pdf`, old vs new `_normalize_headings` | 0 differing lines / 538 bold-ish lines | parity on the input domain |
| 2026-11-10 | 111,024 `**` lines (`findata/`, `doc/`), old vs new | 48 match differences, **1** use-site difference | `Vaibhav_Global.md:30`, unbalanced |
| 2026-11-10 | proposed ladder, NEW, 10 trials | median ratio 3.82-4.15 | stable |
| 2026-11-10 | proposed ladder, OLD | rung 8 = 3.32 ms, rung 32 = timeout | separation unbounded |
| 2026-11-10 | `pytest tests/test_pdf_local.py tests/test_pdf_conv_md.py tests/test_fuzz_pdf_conv_md.py -q` | `53 passed in 2.33s` | with the shipped pattern |
| 2026-11-10 | `make fuzz` (quiet box) | `196 passed in 23.12s` | advisory 4150's red was contention |
| 2026-11-10 | after the rewrite, `tests/test_fuzz_*.py -n auto` x3 | `198 passed` (21.58 / 21.47 / 20.62 s) | two new tests added |
| 2026-11-10 | after the rewrite, `-k bold` with OLD injected | `2 failed, 2 passed in 20.68s` | the discriminator fires |
| 2026-11-10 | after the rewrite, `-k bold` x5 on a quiet box | 2.94-2.96 s each | no flap |
