"""Advisory: CLI mutation-guard census over helpers/ (shared_routines_cli_guards W9).

House convention (2026-09-03): a helpers script that mutates notes or
research.db is dry-run/report by default and takes an explicit guard flag
(``--apply``, or a read-mode selector like ``--dry-run`` / ``--check``).

This test NEVER fails (advisory, S1d style — a WARNING list, not rc 1): a
new helper that writes state without a guard prints a nudge here instead of
breaking `make qa`. The allowlist pins the documented write-by-design
exceptions (cache backfill on read paths, report writers, the pdf
ingestion tools, the query engine's rebuild subcommands). Removing a guard
from the four W2-flipped scripts (sync_tags, sync_sector_wikilinks,
enrich_from_yfinance, rebuild_schema) is caught by their own tests, not
here.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

WRITE_RE = re.compile(
    r"INSERT INTO|REPLACE INTO|DELETE FROM|UPDATE \w+ SET|write_text|stable_write|os\.replace"
)
GUARD_RE = re.compile(
    r"""add_argument\(\s*("|')(--apply|--dry-run|--check\b|--check-only|--no-apply)"""
)

# Write-by-design exceptions — each entry's reason is the audit trail:
#   vec_search/embeddings  — cache backfill INSERTs on read paths (self-healing
#                            derived state; guarded = useless friction)
#   query                  — the graph engine: rebuild/materialisation IS the
#                            command surface (rebuild/fresh-rebuild subcommands)
#   git_secret_scan        — `--out` writes the scan report; scan input is
#                            read-only
#   markdown_lint          — lint report + remediation output writer
#   frontmatter_schema     — `--emit-doc` writes the reference doc
#   pdf_conv_md/verify_extraction — operator-invoked per-target pdf conversion
ALLOWLIST: dict[str, str] = {
    "helpers/core/vec_search.py": "cache backfill on read path",
    "helpers/graph/embeddings.py": "cache backfill on read path",
    "helpers/graph/query.py": "rebuild/materialisation is the command surface",
    "helpers/misc/git_secret_scan.py": "report writer (--out)",
    "helpers/misc/markdown_lint.py": "lint report/remediation writer",
    "helpers/validators/frontmatter_schema.py": "reference-doc writer (--emit-doc)",
    "helpers/pdf/pdf_conv_md.py": "pdf conversion tool",
    "helpers/pdf/verify_extraction.py": "pdf verification report writer",
}


def test_cli_mutation_guard_census(capsys: object) -> None:
    """Every ArgumentParser-bearing helper that writes state must carry a
    guard flag — or sit on the documented allowlist. ADVISORY: always rc 0;
    offenders print a warning block."""
    offenders: list[str] = []
    for p in sorted((REPO_ROOT / "helpers").rglob("*.py")) + [REPO_ROOT / "app.py"]:
        rel = p.relative_to(REPO_ROOT).as_posix()
        s = p.read_text(encoding="utf-8")
        if "ArgumentParser" not in s:
            continue
        if not WRITE_RE.search(s):
            continue
        if GUARD_RE.search(s):
            continue
        if rel in ALLOWLIST:
            continue
        offenders.append(rel)

    print("\n[cli-guard advisory] unguarded mutator CLIs (informational):")
    if offenders:
        for rel in offenders:
            print(f"  WARNING {rel} — writes state, no --apply/--dry-run/--check guard")
    else:
        print("  (none — census clean)")

    # Hard assertions only for invariants we truly require: allowlist entries
    # must exist and still be CLI-bearing mutators (stale entries fail loudly
    # so the list gets pruned instead of rotting).
    for rel in ALLOWLIST:
        p = REPO_ROOT / rel
        assert p.exists(), f"stale allowlist entry (file gone): {rel}"
        s = p.read_text(encoding="utf-8")
        assert "ArgumentParser" in s and WRITE_RE.search(s), (
            f"stale allowlist entry (no longer a CLI mutator): {rel} — prune it"
        )
