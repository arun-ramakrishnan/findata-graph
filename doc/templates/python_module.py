#!/usr/bin/env python3
# contract: pyproject.toml [tool.ruff] — make lint (E/F) + lint-audit (S/UP/C901)
"""<One-line purpose — script_query indexes THIS line as the unit's intent.>

<2-6 lines of contract: what it reads/writes, what must run before it,
what it must NEVER do (e.g. never bumps generation / never writes notes).>

Usage (keep the literal command a human/agent can paste):
    python3 helpers/<subdir>/<this>.py <args>            # dry-run
    python3 helpers/<subdir>/<this>.py <args> --apply    # the write

Conventions enforced elsewhere (don't fight them):
  - ruff E/F gates `make lint`; S/UP/C901 audits are `make lint-audit`
    (per-site `# noqa: <code>  # <reason>` comments, never blanket ignores).
  - DB access goes through helpers.core.db.connect — readers pass
    read_only=True (N RO processes coexist; one RW excludes them all).
  - Wall-clock budgets live in `make perf`, never in pytest.
  - S1b/Corpus: full `findata/` walkers (`rglob("*.md")` `1243`) must use
    `helpers.core.corpus.Corpus.load(use_cache=True)` not `Path.rglob` —
    one `DB` `corpus_cache` `per-file mtime` walk (`0.16s` cached `0.02s` `1 file`
    not `0.37s` full, `memory/corpus.db` `29MB` sidecar, not `research.db`).
    Advisory: `rg 'rglob("*.md")' helpers --glob '!corpus.py'` `WARNING` `advisory` not `FAIL` (see `tests/test_corpus_advisory.py`).
  - S1c/Stale: `derive_*` modules that scan `findata` must support `--stale-only`
    `MAX(created_at) WHERE edge_type=...` vs `max(mtime)` `0.12s` `SKIP` `73-80%`
    so `maint --full` no-op is designed in — think `incremental` from `S1`, not retrofitted.
    Advisory: `rg '--stale-only' helpers/graph/derive_*.py` `WARNING` if missing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(
        0, str(REPO_ROOT)
    )  # standalone-run bootstrap (E402 only if imports follow HERE — keep all imports above)

from helpers.core.db import connect, utc_now  # noqa: E402  # after the sys.path bootstrap above

DRY_RUN = "--apply" not in sys.argv  # simple default; prefer argparse below


def gather_rows(db_path: Path | None = None) -> list[tuple[str, int]]:
    """Readers: open read-only, return plain data, close on scope exit.

    Exceptions propagate — never swallow into sentinel values (a missing
    table must fail loudly, not return []).
    """
    con = connect(db_path, read_only=True)
    try:
        rows = con.execute(
            "SELECT name, length(name) FROM entities WHERE entity_type = ?",
            ("company",),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]
    finally:
        con.close()


def write_something(con, payload: str) -> int:
    """Writers: take an RW connection, use ?-parameters, utc_now() for
    DATETIME columns (local dates sort wrong against CURRENT_TIMESTAMP),
    and let callers commit — one transaction boundary per command.
    Identifiers interpolated into SQL are schema constants; values are
    always ?-bound (f-string SQL needs a per-site `# noqa: S608  # reason`).
    """
    cur = con.execute(
        "INSERT OR IGNORE INTO db_meta(key, value) VALUES (?, ?)",
        (f"template_demo/{utc_now()}", payload),
    )
    return cur.rowcount


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="template: dry-run by default")
    p.add_argument("--apply", action="store_true", help="execute writes")
    p.add_argument("--db", type=Path, default=None, help="override DB path")
    args = p.parse_args(argv)

    rows = gather_rows(args.db)
    print(f"[plan] {len(rows)} companies (dry-run; pass --apply to write)")
    if not args.apply:
        return 0
    con = connect(args.db)
    try:
        n = write_something(con, "demo")
        con.commit()
    finally:
        con.close()
    print(f"[apply] wrote {n} row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
