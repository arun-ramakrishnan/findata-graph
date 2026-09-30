#!/usr/bin/env python3
"""Deterministic OCR delegation selection for the stgit stack.

ocr_review_pipeline S3's deterministic remainder (the brief generator and
host-review automation were deliberately demoted — the host writes the
brief from the four indexes). This is the part with teeth: map the stgit
stack to a ref range, run `ocr delegate preview`, print the roster, and
ASSERT that every rule-covered family the diff touches actually has a
selected file. The assertion exists because the defect class that shipped
the inert CSR lane was exactly this: tests changed, tests not selected,
nobody noticed. Advisory only — never a `make qa` leg.

Usage:
    review_selection.py                # top patch (--commit HEAD)
    review_selection.py --stack 3      # whole applied stack (--from HEAD~3 --to HEAD)
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

RULE_JSON = REPO_ROOT / ".opencodereview" / "rule.json"

# The checked families are HARDCODED, not derived from rule.json: the whole
# point is to catch rule.json dropping one. Deriving them from the rule made
# the mutation self-masking — remove "tests/" from the rule and the check
# lost the family instead of firing. These are the product-critical roots
# whose exclusion shipped defects (inert CSR lane; Mojo reviewed by hand).
PRODUCT_FAMILIES = ("Mojo/src/", "Mojo/tests/", "tests/")


def _families() -> tuple[list[str], list[str]]:
    """(include prefixes, exclude globs) from rule.json.

    Prefixes extend to the first WILDCARD segment, not the first segment:
    `Mojo/src/**/*.mojo` -> `Mojo/src/`, so a `Mojo/` collapse cannot let
    `Mojo/tests/` traffic satisfy a `Mojo/src/` gap (or vice versa). The
    PRODUCT_FAMILIES union stays hardcoded: dropping one from rule.json must
    make the check fire, not silently shrink it (the self-masking lesson).
    Excludes are kept so rule-excluded paths (Mojo/vendor/**) don't count as
    diff traffic an include family failed to show.
    """
    cfg = json.loads(RULE_JSON.read_text())
    includes = set(PRODUCT_FAMILIES)
    for pattern in cfg.get("include", []):
        if "*" not in pattern:
            includes.add(pattern)  # e.g. app.py (exact path, no trailing slash)
        else:
            # prefix before the first segment containing a wildcard
            segs = pattern.split("/")
            prefix_segs = []
            for seg in segs:
                if "*" in seg:
                    break
                prefix_segs.append(seg)
            includes.add("/".join(prefix_segs) + "/")
    return sorted(includes), list(cfg.get("exclude", []))


def _rule_excluded(path: str, exclude_globs: list[str]) -> bool:
    from fnmatch import fnmatch

    return any(fnmatch(path, g) or fnmatch(path, g.rstrip("/*") + "/*") for g in exclude_globs)


def _ref_args(stack: int) -> list[str]:
    if stack <= 1:
        head = subprocess.run(  # noqa: S603  # fixed argv, no shell
            [  # noqa: S607  # git from PATH by design
                "git",
                "rev-parse",
                "HEAD",
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=True,
        ).stdout.strip()
        return ["--commit", head]
    return ["--from", f"HEAD~{stack}", "--to", "HEAD"]


def main(argv: list[str] | None = None) -> int:  # noqa: C901  # pass-per-flag CLI
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--stack",
        type=int,
        default=1,
        help="number of applied stgit patches to map (1 = top patch; N = HEAD~N..HEAD)",
    )
    args = ap.parse_args(argv)

    ocr = shutil.which("ocr")
    if ocr is None:
        print(
            "ocr not on PATH (bun global); see doc/procedures/ocr_review.md Invocation",
            file=sys.stderr,
        )
        return 2
    proc = subprocess.run(  # noqa: S603  # resolved absolute path, fixed argv, no shell
        [ocr, "delegate", "preview", *_ref_args(args.stack), "--format", "json"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    if proc.returncode != 0:
        print(f"ocr delegate preview failed: {proc.stderr.strip()}", file=sys.stderr)
        return 2
    # `[ocr] …` progress lines interleave with the JSON on stdout (procedure
    # §"(-o, not >)": strip them before parsing — never skip the strip).
    clean = "\n".join(ln for ln in proc.stdout.splitlines() if not ln.startswith("[ocr]"))
    payload = json.loads(clean)

    reviewable = [f["path"] for f in payload["reviewable_files"]]
    excluded = [f["path"] for f in payload["excluded_files"]]
    scope = f"HEAD~{args.stack}..HEAD" if args.stack > 1 else payload.get("commit", "HEAD")
    print(
        f"OCR delegation selection for {scope}: "
        f"{payload['reviewable_count']}/{payload['total_files']} reviewable, "
        f"{payload['total_insertions']}+/−{payload['total_deletions']}"
    )
    for path in reviewable:
        print(f"  + {path}")
    for path in excluded:
        print(f"  - {path} (excluded)")

    include_roots, exclude_globs = _families()
    violations = []
    for root in include_roots:
        candidates = [
            p for p in reviewable + excluded if p == root.rstrip("/") or p.startswith(root)
        ]
        # rule-excluded paths (Mojo/vendor/**) are DECLARED invisible — they
        # are not diff traffic the include family failed to show.
        touched = [p for p in candidates if not _rule_excluded(p, exclude_globs)]
        selected = [p for p in reviewable if p == root.rstrip("/") or p.startswith(root)]
        if touched and not selected:
            violations.append(root)
    if violations:
        print(
            "\nSELECTION DEFECT: rule-covered families changed but nothing from them "
            "was selected: " + ", ".join(violations) + "\n"
            "  This is the shipped-defect class (tests changed, tests invisible in the\n"
            "  review) — fix .opencodereview/rule.json include globs before reviewing.",
            file=sys.stderr,
        )
        return 1
    print("selection-teeth OK: every rule-covered family with diff traffic is visible")
    # Review freshness (ocr_review_pipeline gap: was this diff already
    # reviewed, and did it change since?). Advisory context only — a stale
    # or unreviewed verdict never fails the run.
    try:
        from helpers.misc import review_freshness as rf

        state, row, _ = rf.status(args.stack)
        line = f"review-freshness: {state}"
        if row is not None:
            line += f" (reviewed {row['reviewed_at']} legs={row.get('legs')}"
            if row.get("note"):
                line += f", {row['note']}"
            line += ")"
        print(line)
    except (Exception, SystemExit) as e:  # noqa: BLE001  # freshness is context, never a failure
        print(f"review-freshness: unavailable ({type(e).__name__}: {e})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
