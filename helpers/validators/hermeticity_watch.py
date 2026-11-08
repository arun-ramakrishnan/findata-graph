#!/usr/bin/env python3
"""Hermeticity watch — advisory leg over the qa suite's live-store touches.

Reads the per-worker recordings the always-on pytest recorder
(tests/hermeticity_watch.py) wrote during the most recent qa run and
reports:

- tier A (FIRE, exit 1): WRITE-CAPABLE connections from non-live tests
  to the live ``memory/`` stores. This is the class that cost the gate
  twice (the 404 MB live-DB clone arc #478 fixed; the 2.5 GB
  convo_search backup the maint-chain shim paid every run until
  2026-11-08) — a write-capable connect from a non-live test is how
  both incidents started.
- tier B (report-only): read-only connects and file opens under the
  live tree. Sanctioned donors (tests/_tmp_hygiene.py template builds)
  never appear — the recorder exempts them.

SKIPs (exit 0) when no recording exists — the leg needs a qa run to
have executed in this tree first. Advisory-first per operator ruling
2026-11-08; promotion to a hard qa failure after burn-in.

Usage:
    python3 helpers/validators/hermeticity_watch.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = REPO_ROOT / "outputs" / ".junit" / "hermeticity"


def _load_recordings(report_dir: Path) -> tuple[list[dict], list[dict], list[str]]:
    """Read every worker recording; return (violations, reads, workers)."""
    violations: list[dict] = []
    reads: list[dict] = []
    workers: list[str] = []
    for rec in sorted(report_dir.glob("*.json")):
        try:
            payload = json.loads(rec.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            print(f"hermeticity-watch: unreadable recording {rec.name}: {exc}")
            continue
        workers.append(payload.get("worker", rec.stem))
        for touch in payload.get("touches", []):
            if touch.get("tier") == "A":
                violations.append(touch)
            else:
                reads.append(touch)
    return violations, reads, workers


def main(report_dir: Path = REPORT_DIR) -> int:
    if not report_dir.is_dir() or not any(report_dir.glob("*.json")):
        print("hermeticity-watch: SKIP — no qa recording found (run make qa first)")
        return 0

    violations, reads, workers = _load_recordings(report_dir)

    by_node: dict[str, list[dict]] = {}
    for v in violations:
        by_node.setdefault(v["node"], []).append(v)

    print(
        f"hermeticity-watch: {len(workers)} recording(s) — "
        f"{len(violations)} write-touch violation(s), "
        f"{len(reads)} read-only touch(es) (report-only)"
    )
    for node, vs in sorted(by_node.items()):
        for v in vs:
            print(f"  FIRE {node}: {v['kind']} -> {v['path']}")
    if violations:
        print(
            "hermeticity-watch: FAIL — non-live test(s) opened live store(s) "
            "write-capable. Point the fixture at a tmp clone or pass "
            "backup_sidecars=False; live-marked tests are the sanctioned surface."
        )
        return 1
    for r in reads[:10]:
        print(f"  read: {r['node'][:90]} {r['kind']} -> {r['path']}")
    if len(reads) > 10:
        print(f"  ... and {len(reads) - 10} more read-only touch(es)")
    print("hermeticity-watch: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
