---
title: Mojo regex via Python `regex` interop
status: executed
filed: '2026-08-29'
executed: '2026-08-29'
completed_md: '180'
area: Mojo tooling / `Mojo/src/bench/mojo_regex_probe.mojo` +
---

# Mojo regex via Python `regex` interop

**Date:** 2026-08-29 · **Status:** EXECUTED 2026-08-29 (completed.md #180) ·
**Area:** Mojo tooling / `Mojo/src/bench/mojo_regex_probe.mojo` +
`Mojo/bench/mojo_regex_*`

Closes the deferred `mojo-regex` task (supersedes the VERIFIED/RESOLVED
interim state of the same day).

## Context / problem
- The Mojo subsystem (`Mojo/`) needed regex. The community `mojo-regex`
  conda package (prefix.dev/modular-community) is **dead**: it pins
  `max >=25.4,<26.0` with no candidates, and the whole pixi/conda route is
  dead (see `doc/local/mojo_pilot.md` §pixi/conda route: DEAD). A native-Mojo
  regex via that lib is therefore unavailable.
- Deferred item: obtain regex capability in Mojo.

## Solution
Use Mojo's Python interop to call the third-party `regex` module directly:

```mojo
from std.python import Python

def main() raises:
    var regex = Python.import_module("regex")
    var m = regex.search(r"(?<word>\w+)\s+(?<num>\d+)", "abc 42")
    print(m.group("word"), m.group("num"))   # abc 42
```

Install once into the Python env Mojo uses (the repo `.venv`, python
3.14.4):
```
uv pip install regex
```

## Why `regex` over stdlib `re`
- `re` works identically through the bridge (`Python.import_module("re")`)
  but lacks Unicode **property classes** (`\p{L}`, `\p{N}`, `\p{Greek}`),
  POSIX classes, possessive quantifiers, `\K`, etc.
- `regex` (installed) provides all of those — exactly the features Mojo has
  no native equivalent for. Prefer `regex` when Unicode/script-aware
  matching is needed; fall back to `re` only if you must avoid the extra dep.

## Verification (Mojo 1.0.0, .venv python 3.14.4, regex 2026.7.19)
Correctness battery — **51/51 checks PASS** (each case run BOTH natively
in CPython and from Mojo through the bridge; probe compares the two
sides and the expected value — direct == python == expected). Cases live
as DATA in `Mojo/bench/mojo_regex_cases.json` — the single source both
sides execute (patterns, inputs, modes, golden expecteds; regenerate
with `build_mojo_regex_cases.py`, `--check` detects drift after a
`regex` upgrade). Coverage: named/branch-reset groups, pos/neg/variable-
width lookaround, Unicode property + POSIX classes, possessive/atomic/
recursive constructs (`*+`, `(?>…)`, `(?&rec)` balanced parens), `\K`,
conditionals, backrefs, findall/finditer/search/split/sub/subn modes,
and realistically big patterns (RFC-5322-style mailbox, 7-group URI,
12-group Apache log line, 26-word alternation):

| Feature | Pattern | Result |
|---|---|---|
| Named groups | `(?<word>\w+)\s+(?<num>\d+)` | `{'word':'abc','num':'42'}` |
| Case-insensitive | `(?i)abc` fullmatch vs `aBc` | True |
| Non-greedy `*?` | `<.*?>` on `<a><b><c>` | `['<a>','<b>','<c>']` |
| Non-greedy `+?` | `a+?` on `aaaa` | `a` |
| Word boundary `\b` | `\bcat\b` | `['cat','cat']` |
| Non-boundary `\B` | `\Bcat` | `['cat','cat']` |
| Unicode `\p{L}` | `\p{L}+` | `['Hello','αβγ','XYZ']` |
| Unicode `\p{N}` | `\p{N}+` | `['123','456']` |
| Unicode `\p{Greek}` | `\p{Greek}+` | `['αβγ']` |
| Multiline `(?m)` | `^(?m)Line\d` | `['Line1','Line2']` |
| Dotall `(?s)` | `(?s)a.b` vs `a\nb` | match True |
| Lookahead `+` | `\d+(?=\s*dollars)` | `['100','300']` |
| Lookbehind `+` | `(?<=\$)\d+` | `['50','60']` |
| Lookahead `-` | `foo(?!bar)` | `['foo','foo']` |
| Negated `\S` | `\S+` | `['a','b','c','d']` |
| Negated `\D` | `\D+` | `['a','b','c']` |
| Negated `\W` | `\W+` | `['!','@','#']` |
| Complex combined | `(?i)(?<name>[\p{L}.]+)@(?<domain>[\p{L}\d.\-]+\.[\p{L}]{2,})` | `[('Αλίκη.Papa','Ελλάδα.GR'),('bob','Example.com')]` |

### Performance
- **Pure-Python** (100,000 battery case calls cycling the 51 cases,
  timed inside Python — the real interop workload of compile+match+
  result-build, not a toy loop): `1.006 s` → **~99k calls/s**.
- **Mojo bridge** (the same 100,000 calls, each crossing
  Mojo→Python): `1.030 s` → **~97k calls/s**.
- Bridge overhead ≈ **2.5%** on real work — each case call does ~10 µs
  of actual regex work that dwarfs the ~0.4 µs marshaling cost. (An
  earlier toy-`findall` comparison measured ~27% overhead only because
  the per-call work was tiny enough for marshaling to dominate; scale
  history: 4.2k/20k/100k iters → 1.6%/4.7%/2.5%, i.e. within noise of
  the ~0.4 µs/call fixed cost.)
  Negligible for parsing/validation; for hot loops, call ONE Python function
  that runs the whole loop rather than crossing per element.

## Mojo-1.0 gotchas (why the naive snippet fails)
1. **Import path**: `from python import Python` is pre-1.0 → use
   `from std.python import Python`.
2. **`raises`**: `Python.import_module` / `.compile` / `.search` are
   `raises` → wrap in `try` or mark `def main() raises:`.
3. **`match` is a reserved keyword** in Mojo (pattern matching) → never
   name a variable `match`; use `m`.
4. **`Python.evaluate` is `eval`** (single expression) → wrap multi-statement
   Python in a function and call it, e.g. `Python.evaluate("run()")`.
5. **Raw strings work**: `r"..."` is fine in Mojo for regex literals.

## Integration notes
- Regex in Mojo suits one-off text parsing/validation (filenames, note
  frontmatter, report scraping) in the `Mojo/` tools.
- For throughput paths (e.g. scanning thousands of vault notes), keep the
  loop in Python: write a small Python helper that takes the corpus and
  returns results, call it once from Mojo.
- A regression test can live in `Mojo/tests/` via the existing
  `TestSuite.discover_tests` runner if desired.

## Reproduce
The verification artifacts live in the Mojo tree:
- `Mojo/bench/mojo_regex_cases.json` — the 51 cases as DATA (single
  source for both sides: patterns, inputs, modes, golden expecteds).
- `Mojo/bench/build_mojo_regex_cases.py` — regenerates the goldens via
  the reference `regex` module; `--check` exits 1 on drift.
- `Mojo/bench/mojo_regex_battery.py` — file-driven dispatcher (9 modes)
  + native self-check (`run()`) + perf harness (`run_cases`,
  `bench_report`).
- `Mojo/src/bench/mojo_regex_probe.mojo` — Mojo driver: imports the
  battery via the bridge, compares every case direct-vs-python-vs-
  expected, then times 100k identical case calls on both sides.

Setup (one-time; `regex` is already in the .venv):
```
uv pip install regex          # into the .venv Mojo uses
```
Standing runs (from the repo root):
```
make mojo-bench MOJO_BENCH_ARGS='--leg regex-bridge'          # full leg
.venv/bin/python3 -c "import sys; sys.path.insert(0, 'Mojo/bench'); \
  import mojo_regex_battery as b; print(b.run())"             # native only
```
Expected: **51/51 direct-vs-python checks passed**; pure-python
~99k calls/s, mojo-bridge ~97k calls/s (100,000 case calls each side).

## References
- `doc/local/mojo_pilot.md` §pixi/conda route: DEAD (mojo-regex conda dead)
- `doc/local/mojo_build_recipe.md` (deferred rattler-build — regex now
  sourced via Python, excluded from any conda packaging plan)
- `Mojo/src/bench/analyzer.mojo:155` (precedent: `Python.import_module("numpy")`)
