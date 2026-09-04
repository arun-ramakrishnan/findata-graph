---
title: "CLI test seam for the five out-of-census mains"
status: executed
filed: "2026-09-03"
executed: "2026-09-03"
completed_md: "198"
area: "helpers/maintenance (rename_entity, move_sector), helpers/validators (static_checks), helpers/misc (embed_eval, database_integrity_check), tests (argv seam probe)"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# CLI test seam for the five out-of-census mains

**Date:** 2026-09-03 · **Status:** EXECUTED (same day) ·
**Area:** `helpers/maintenance/rename_entity.py` · `helpers/maintenance/move_sector.py` ·
`helpers/validators/static_checks.py` · `helpers/misc/embed_eval.py` ·
`helpers/misc/database_integrity_check.py` · follows #196 (W4 argv adoption, 41 census parsers)

## 1. Motivation

Roll #196 (W4) took every parser in the 41-`ArgumentParser` census to the house
argv seam (`def main(argv: list[str] | None = None) -> int` +
`parse_args(argv)`). A post-arc sweep found five remaining bare
`def main()` signatures — all **outside that census, because none of them
carries an `ArgumentParser`**. They had no seam at all: tests must drive
them through `sys.argv` mutation or subprocess, both clumsy. This proposal
gives all five the same seam without changing any behavior.

Correction of record: the deleted `shared_routines_pass3` draft listed
these five as "argv stragglers" of the argparse census. They are not —
the census never covered them; three hand-roll `sys.argv` and two are
flag-less.

## 2. Survey evidence (measured 2026-09-03)

| File | `main` today | Argument style | Safe in-process probe? |
|---|---|---|---|
| `helpers/maintenance/rename_entity.py:57` | `def main() -> int` | hand-rolled positionals (`old new [--sector S] [--ticker T]`); usage error prints `__doc__`, rc 2, no writes | yes — `main([])` hits the usage branch |
| `helpers/maintenance/move_sector.py:237` | `def main() -> int` | `args = sys.argv[1:]` hand-rolled incl. `--batch`; same usage path | yes — `main([])` hits the usage branch |
| `helpers/misc/embed_eval.py:250` | `def main() -> int` | `mode = sys.argv[1] if len(sys.argv) > 1 else "all"` | **no** — `main([])` runs the full eval |
| `helpers/validators/static_checks.py:937` | `def main() -> int` (`# noqa: C901`) | flag-less (driven by `make static-checks`) | no — runs the whole gate |
| `helpers/misc/database_integrity_check.py:2086` | `def main():` (returns `None`; `sys.exit(main())` = rc 0) | flag-less | no — runs the full check |

All five `__main__` guards are already `sys.exit(main())` — unchanged.

## 3. Design

Uniform seam, one shape per style, zero behavior change:

- **Flag-less** (`static_checks`, `database_integrity_check`):
  `def main(argv: list[str] | None = None)` accepting-and-ignoring `argv`.
  `database_integrity_check` keeps its unannotated return (it returns
  `None` today; adding `-> int` would lie).
- **Hand-rolled** (`rename_entity`, `move_sector`, `embed_eval`):
  same signature, body relocals once at the top —
  `raw = sys.argv[1:] if argv is None else argv` — and shifts its indices
  by one (`sys.argv[1]` → `raw[0]`, `args = sys.argv[3:]` → `raw[2:]`, …).
  `argv` follows the house `parse_args(argv)` convention: post-script-name
  args.

## 4. Non-goals

- **No argparse conversion** — that changes usage text, error paths, and
  rc surfaces for zero operator value; the seam is the whole point.
- **No shared argparse builder** — rejected on evidence, #196 §4.
- **No flag additions** (`--help` etc.) — these CLIs' contracts are
  stable; `__doc__`-as-usage stays.

## 5. Gates

New `tests/test_cli_argv_seam.py` (qa suite, fast, no DB writes):

- signature probe ×5: `inspect.signature(main)` has parameter `argv`
  defaulted to `None`;
- invocation probe ×2: `rename_entity.main([])` and `move_sector.main([])`
  return 2 (usage branch) — proves the relocal actually routes argv;
- deliberately NO invocation probe for `embed_eval` (runs the eval),
  `static_checks`, and `database_integrity_check` (run full checks) —
  their seam is pinned by the signature probe alone.

Plus `ruff` on touched files and `make qa` once at arc end.

## 6. Risks

- **Index-shift bugs in the relocal** — mitigated by the two usage probes
  (they exercise the shifted length checks) and by the shift being
  mechanical (`sys.argv[N]` → `raw[N-1]`).
- **Caller breakage** — none: the parameter is optional; every existing
  caller (make targets, maint steps, subprocess invocations) keeps going
  through `sys.argv`.

## 7. Scale

Now — mechanical, no hot path, no DB/vault writes. Nothing deferred.
