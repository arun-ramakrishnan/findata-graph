#!/usr/bin/env python3
"""Review-freshness ledger for OCR reviews of the stgit stack.

ocr_review_pipeline's last operator-only gap: nothing recorded *whether the
patch under review was already reviewed, and whether it changed since*. This
ledger does for review rounds what coverage_ledger.py does for security
reviews — a JSON array in gitignored memory/data/ with a content fingerprint
per row, checked against the live diff.

Fingerprint: sha256[:16] over the range's DIFF TEXT (`git diff HEAD~N..HEAD`),
not the commit SHA — stgit refreshes mint new SHAs for identical content, and
review freshness is about CONTENT, not patch identity. Same diff after a
refresh stays FRESH; any content change goes STALE.

Modes (advisory only — never a `make qa` leg; OCR output is never
gate-coupled):
    review_freshness.py                    # status of the top patch
    review_freshness.py --stack N          # status of HEAD~N..HEAD
    review_freshness.py --record --leg delegation --note "verdict pointer"
    review_freshness.py --show

Rows:
    {"scope", "stack", "fingerprint", "reviewed_at", "legs", "note"}
Upsert by fingerprint: a re-review of the same diff updates the row.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

LEDGER = REPO_ROOT / "memory" / "data" / "review-freshness.json"


def _diff_text(stack: int) -> str:
    range_args = ["HEAD~1..HEAD"] if stack <= 1 else [f"HEAD~{stack}", "HEAD"]
    proc = subprocess.run(  # noqa: S603  # fixed argv, no shell
        ["git", "diff", *range_args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git diff failed: {proc.stderr.strip()}")
    return proc.stdout


def fingerprint(stack: int) -> tuple[str, str]:
    """(fingerprint, scope label) for the applied-stack diff."""
    if stack < 1:
        raise RuntimeError(
            f"--stack must be >= 1 (got {stack}); stack 0 would alias "
            "the top patch under a lying scope label"
        )
    text = _diff_text(stack)
    applied = subprocess.run(  # noqa: S603
        ["stg", "series", "--applied"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    if applied.returncode != 0:
        raise RuntimeError(
            f"stg series failed (rc={applied.returncode}): {applied.stderr.strip()} "
            "— not an stgit checkout? cannot bound the stack depth"
        )
    n_applied = len([ln for ln in applied.stdout.splitlines() if ln.strip()])
    if stack > n_applied:
        raise RuntimeError(
            f"stack depth {stack} exceeds the applied stack ({n_applied}); refusing a "
            "range that reaches past the patch stack into foreign history"
        )
    return hashlib.sha256(text.encode()).hexdigest()[:16], f"HEAD~{stack}..HEAD"


def _load() -> list[dict]:
    try:
        rows = json.loads(LEDGER.read_text())
    except FileNotFoundError:
        return []
    except ValueError as e:
        # A corrupt ledger must name itself, not traceback (delegation A/B
        # round 2026-09-29, checklist catch the managed leg missed).
        sys.exit(f"ledger corrupt ({e}): fix or delete {LEDGER}")
    if not isinstance(rows, list):
        sys.exit(f"ledger must be a JSON array: {LEDGER}")
    return rows


def _save(rows: list[dict]) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    # flock the record path: two concurrent recorders would otherwise
    # last-writer-clobber rows (checklist concurrency category; advisory
    # tool, so a blocking lock is fine).
    import fcntl

    lock_path = LEDGER.with_suffix(".lock")
    with open(lock_path, "w") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            LEDGER.write_text(json.dumps(rows, indent=2) + "\n")
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def status(stack: int) -> tuple[str, dict | None, str]:
    """(state, row, scope) — state in FRESH / STALE / UNREVIEWED."""
    fp, scope = fingerprint(stack)
    rows = _load()
    live = next((r for r in rows if r.get("fingerprint") == fp), None)
    if live is not None:
        return "FRESH", live, scope
    scoped = [r for r in rows if r.get("scope") == scope]
    if scoped:
        return "STALE", scoped[-1], scope
    return "UNREVIEWED", None, scope


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stack", type=int, default=1, help="applied-patch depth (1 = top patch)")
    ap.add_argument("--record", action="store_true", help="record this diff as reviewed")
    ap.add_argument("--leg", choices=("delegation", "managed"), action="append", default=[])
    ap.add_argument("--note", default="", help="verdict pointer (e.g. code_review.md section)")
    ap.add_argument("--show", action="store_true", help="print the ledger rows for this scope")
    args = ap.parse_args(argv)

    fp, scope = fingerprint(args.stack)
    rows = _load()

    if args.show:
        scoped = [r for r in rows if r.get("scope") == scope]
        print(json.dumps(scoped, indent=2) if scoped else f"no rows for {scope}")
        return 0

    if args.record:
        legs = sorted(set(args.leg)) or ["delegation"]
        live = next((r for r in rows if r.get("fingerprint") == fp), None)
        if live is not None:
            live["legs"] = sorted(set(live.get("legs", [])) | set(legs))
            live["reviewed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if args.note:
                live["note"] = args.note
            row = live
        else:
            row = {
                "scope": scope,
                "stack": args.stack,
                "fingerprint": fp,
                "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "legs": legs,
                "note": args.note,
            }
            rows.append(row)
        _save(rows)
        print(f"recorded {scope} fp={fp} legs={row['legs']}")
        return 0

    state, row, _ = status(args.stack)
    print(f"{scope} fp={fp} -> {state}")
    if state == "FRESH" and row is not None:
        print(
            f"  reviewed {row['reviewed_at']} legs={row.get('legs')}"
            + (f" note={row['note']}" if row.get("note") else "")
        )
    elif state == "STALE" and row is not None:
        print(
            f"  last review {row['reviewed_at']} legs={row.get('legs')} no longer matches "
            "the diff — the stack changed since it was reviewed"
        )
    else:
        print("  no recorded review for this diff")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
