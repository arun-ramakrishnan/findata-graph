#!/usr/bin/env python3
"""Review-freshness ledger for OCR reviews of the stgit stack.

ocr_review_pipeline's last operator-only gap: nothing recorded *whether the
patch under review was already reviewed, and whether it changed since*. This
ledger does for review rounds what coverage_ledger.py does for security
reviews — a JSON array in gitignored outputs/reviews/ with a content fingerprint
per row, checked against the live diff.

Fingerprint: sha256[:16] over the range's DIFF TEXT (`git diff HEAD~N..HEAD`),
not the commit SHA — stgit refreshes mint new SHAs for identical content, and
review freshness is about CONTENT, not patch identity. Same diff after a
refresh stays FRESH; any content change goes STALE.

Modes (advisory only — never a `make qa` leg; OCR output is never
gate-coupled):
    review_freshness.py                    # status of the top patch
    review_freshness.py --stack N          # status of HEAD~N..HEAD
    review_freshness.py --from X --to Y    # status of a LANDED pair (S11, 2026-10-11)
    review_freshness.py --from X --to Y --record \
        --leg delegation --leg scan \
        --note "verdict pointer: code_review.md §…"   # close the review (runbook 6c)
    review_freshness.py --from X --to Y --show    # full JSON rows for this scope
    review_freshness.py --all                     # EVERY completion record, newest
                                                  #   first — find the last scanned
                                                  #   head here, never guess

Recipes:
    # resume: X..Y reviewed, HEAD moved — review only the NEW leg
    Y=$(git rev-parse --short <last-scanned-head>)   # from --all or the roster names
    H=$(git rev-parse --short HEAD)
    review_freshness.py --from $Y --to $H            # UNREVIEWED = new work
    make review-scan FROM=$Y TO=$H                   # … review it …
    review_freshness.py --from $Y --to $H --record --leg scan --note "…"

    # a subrange of a reviewed superset reads SUBSUMED (same base, asked head
    # an ancestor of the recorded head) — not a lying UNREVIEWED (S11b)

    # any argument spelling of the same pair reads the same row (scope labels
    # are canonical from RESOLVED SHAs); --leg without --record is refused

Perf: the first lookup of a range computes its diff (~60s on a 1293-file
range, regardless of --diff-algorithm; `git range-diff` is a two-range
compare, not a fingerprint) and memoizes the fp by resolved SHA pair —
repeat lookups are ~0.1s. `--no-renames` keeps the hash config-independent.

Rows:
    {"scope", "stack", "fingerprint", "reviewed_at", "legs", "note",
     "base_sha", "head_sha"}   # SHAs on ranged rows (S11b) — they drive
                               # SUBSUMED containment; stack is null for
                               # a landed --from/--to row
Upsert by fingerprint: a re-review of the same diff updates the row.

ONE store across worktrees: the ledger, its diffcache and its flock all live
under the MAIN checkout's outputs/reviews/, resolved via
`git rev-parse --git-common-dir` — never the worktree's. A review run in a
worktree records into the same rows the main checkout reads, so a worktree
session cannot leave the canonical ledger claiming UNREVIEWED. Git commands
still run in the worktree (REPO_ROOT), because the tree under review is the
one whose HEAD and diff matter.
Loop context and rules live in doc/procedures/ocr_review.md §1b + runbook
step 6c; this docstring carries the CLI surface only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from helpers.core import jsonio  # noqa: E402  (orjson parse cutover; dumps stay stdlib per db_scaling_tuning §6.3 criteria)

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _main_checkout_root() -> Path:
    """The MAIN checkout, not the worktree this script was invoked from.

    The ledger is one store for the whole repository, so it must not fork when
    the review runs in a worktree. `Path(__file__).parents[2]` gets this wrong:
    a worktree is a full checkout with its own `outputs/`, so every
    `--record` from a worktree wrote a private ledger and the canonical one
    kept claiming UNREVIEWED — the next session re-pays the whole range.
    Measured 2026-10-14 on the 1fda0c158..6182cd2c7 review: a worktree record
    landed 1 row while the main ledger's 11 rows were untouched, needing a
    manual merge.

    `git rev-parse --git-common-dir` is the same directory in a main checkout
    and in every linked worktree (`…/.git/worktrees/<name>` is the PER-worktree
    dir, which is exactly what we must not follow), so its parent is the main
    checkout root in both cases. Falls back to REPO_ROOT when git is
    unavailable, the repo is bare (common dir IS the repo, so `.parent` would
    be its parent), or the shape is unexpected — a wrong-but-working ledger
    beats an import-time crash.
    """
    try:
        proc = subprocess.run(  # noqa: S603  # fixed argv, no shell
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],  # noqa: S607  # git from PATH by design
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )
        common = Path(proc.stdout.strip()) if proc.returncode == 0 else None
    except OSError:
        common = None
    if common is None:
        return REPO_ROOT
    root = common.resolve().parent
    # Bare repo: the common dir IS the repo (HEAD/objects/refs at its top),
    # so `.parent` is not a checkout. Ask git rather than sniffing for HEAD —
    # a NON-bare git dir also has HEAD, so that test always fired.
    try:
        bare = subprocess.run(  # noqa: S603  # fixed argv, no shell
            ["git", "--git-dir", str(common), "rev-parse", "--is-bare-repository"],  # noqa: S607  # git from PATH by design
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        ).stdout.strip()
    except OSError:
        bare = "true"
    if bare == "true" or not (root / "helpers").is_dir():
        return REPO_ROOT
    return root


# The store root is deliberately NOT REPO_ROOT — see _main_checkout_root.
LEDGER_ROOT = _main_checkout_root()

# Review artifacts share ONE store (review_scan_leg S6 closure): the
# durable rosters and the freshness ledger that tracks them live together
# under outputs/reviews/ (gitignored, machine-local — same class as the
# gate reports in outputs/). Was memory/data/review-freshness.json.
# Ledger, its diffcache and its flock all derive from this path, so they all
# follow the main checkout together.
LEDGER = LEDGER_ROOT / "outputs" / "reviews" / "review-freshness.json"


def _rev_sha(ref: str) -> str:
    proc = subprocess.run(  # noqa: S603  # fixed argv, no shell
        ["git", "rev-parse", ref],  # noqa: S607  # git from PATH by design
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git rev-parse {ref} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _diff_text(stack: int) -> str:
    range_args = ["HEAD~1..HEAD"] if stack <= 1 else [f"HEAD~{stack}", "HEAD"]
    proc = subprocess.run(  # noqa: S603  # fixed argv, no shell
        # --no-renames: the fingerprint must be a function of CONTENT, not of
        # the local diff.renames config — rename detection also costs ~nothing
        # here (measured 2026-10-11: 67s vs 62s on the 1293-file range; the
        # cost is patch-text generation itself, ~12.6 MB over the file pairs).
        ["git", "diff", "--no-renames", *range_args],  # noqa: S607  # git from PATH by design
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git diff failed: {proc.stderr.strip()}")
    return proc.stdout


# SHA-pair-keyed fingerprint memo (2026-10-11): generating the range's patch
# text is the whole cost (~60s for a 1293-file range; measured myers vs
# histogram vs patience all 62-66s, name-only tree walk 0.06s), and identical
# SHAs PROVABLY mean identical diff text (content-addressed), so the fp for a
# (base, head) pair never needs recomputing. Derive the path from LEDGER at
# call time so conftest tmp-redirects propagate.
_DIFF_CACHE_KEEP = 64


def _diff_cache_path():
    return LEDGER.with_name("review-freshness.diffcache.json")


def _diff_cache_load() -> dict:
    try:
        cache = jsonio.loads(_diff_cache_path().read_text())
    except FileNotFoundError:
        return {}
    except ValueError as e:
        sys.exit(f"diff cache corrupt ({e}): fix or delete {_diff_cache_path()}")
    return cache if isinstance(cache, dict) else {}


def _diff_cache_save(cache: dict) -> None:
    import fcntl

    cache_path = _diff_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    pruned = dict(list(cache.items())[-_DIFF_CACHE_KEEP:])
    lock_path = LEDGER.with_suffix(".lock")
    with open(lock_path, "w") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            cache_path.write_text(json.dumps(pruned, indent=1) + "\n")
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def _fingerprint_cached(base_sha: str, head_sha: str, compute) -> str:
    """fp for a resolved SHA pair, memoized — a repeat range never pays the
    ~60s diff again. Keyed on FULL resolved SHAs: content-addressed, so a
    hit is equivalence, not a heuristic."""
    key = f"{base_sha}:{head_sha}"
    cache = _diff_cache_load()
    hit = cache.get(key)
    if hit and isinstance(hit, dict) and hit.get("fp"):
        return hit["fp"]
    fp = compute()
    cache[key] = {"fp": fp}
    _diff_cache_save(cache)
    return fp


def fingerprint(stack: int) -> tuple[str, str]:
    """(fingerprint, scope label) for the applied-stack diff."""
    if stack < 1:
        raise RuntimeError(
            f"--stack must be >= 1 (got {stack}); stack 0 would alias "
            "the top patch under a lying scope label"
        )
    applied = subprocess.run(  # fixed argv, no shell; stg from PATH by design
        ["stg", "series", "--applied"],  # noqa: S607  # stg from PATH by design
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
    lo = "HEAD~1" if stack <= 1 else f"HEAD~{stack}"
    lo_sha, head_sha = _rev_sha(lo), _rev_sha("HEAD")
    fp = _fingerprint_cached(
        lo_sha,
        head_sha,
        lambda: hashlib.sha256(_diff_text(stack).encode()).hexdigest()[:16],
    )
    return fp, f"HEAD~{stack}..HEAD"


def _diff_text_refs(base: str, head: str) -> str:
    proc = subprocess.run(  # noqa: S603  # fixed argv, no shell
        # --no-renames: config-independent fingerprint (see _diff_text)
        ["git", "diff", "--no-renames", f"{base}..{head}"],  # noqa: S607  # git from PATH by design
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git diff {base}..{head} failed: {proc.stderr.strip()}")
    return proc.stdout


def fingerprint_range(base: str, head: str) -> tuple[str, str]:
    """(fingerprint, scope) for a LANDED, non-HEAD-relative pair (S11).

    Same sha256[:16] over the range's diff text the stack mode uses — stgit
    refreshes mint new SHAs for identical content, and freshness is about
    content. Scope label truncates to 12 for readable ledger rows; the
    fingerprint remains the only match key. Memoized by the resolved SHA
    pair (_fingerprint_cached): repeat lookups skip the ~60s diff entirely."""
    if not base or not head:
        raise RuntimeError("--from and --to go together (a half range is not a range)")
    base_sha, head_sha = _rev_sha(base), _rev_sha(head)
    fp = _fingerprint_cached(
        base_sha,
        head_sha,
        lambda: hashlib.sha256(_diff_text_refs(base, head).encode()).hexdigest()[:16],
    )
    # Canonical scope from RESOLVED SHAs, never the argument spelling —
    # `4fd3a20f..0805d829` and `4fd3a20f5..0805d829c` are the same range and
    # must label (and STALE-group) identically (bug found 2026-10-11: --show
    # missed rows recorded under a longer spelling).
    return fp, f"{base_sha[:12]}..{head_sha[:12]}"


def _is_ancestor(asked: str, recorded: str) -> bool:
    """True when `asked` is an ancestor of (or equal to) `recorded` —
    i.e. the asked-for range's head commit is contained in the recorded
    range's history. merge-base --is-ancestor: rc 0 yes / 1 no."""
    proc = subprocess.run(  # noqa: S603  # fixed argv, no shell
        ["git", "merge-base", "--is-ancestor", asked, recorded],  # noqa: S607
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    return proc.returncode == 0


def status_range(base: str, head: str) -> tuple[str, dict | None, str]:
    """(state, row, scope) for a landed pair — state in FRESH / STALE /
    SUBSUMED / UNREVIEWED (S11b: SUBSUMED = the asked head is an ancestor
    of a recorded head over the same base, so this range's diff is
    contained in an already-reviewed superset)."""
    fp, scope = fingerprint_range(base, head)
    base_sha, head_sha = _rev_sha(base), _rev_sha(head)
    rows = _load()
    live = next((r for r in rows if r.get("fingerprint") == fp), None)
    if live is not None:
        return "FRESH", live, scope
    scoped = [r for r in rows if r.get("scope") == scope]
    if scoped:
        return "STALE", scoped[-1], scope
    for r in rows:
        rhs = r.get("head_sha")
        if not rhs or r.get("base_sha") != base_sha:
            continue
        if _is_ancestor(head_sha, rhs):
            return "SUBSUMED", r, scope
    return "UNREVIEWED", None, scope


def _load() -> list[dict]:
    try:
        rows = jsonio.loads(LEDGER.read_text())
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


def _print_all_records(args) -> int:
    """--all: the whole-ledger visibility view, newest first. Takes no
    --record/--leg/--show/--from/--to (those are scope-scoped)."""
    if args.record or args.leg or args.show or args.from_ref or args.to_ref:
        sys.exit("--all is a whole-ledger view — it takes no --record/--leg/--show/--from/--to")
    rows_all = sorted(_load(), key=lambda r: r.get("reviewed_at", ""), reverse=True)
    if not rows_all:
        print("ledger empty — no completion records yet")
        return 0
    for r in rows_all:
        legs = ",".join(r.get("legs", []))
        print(
            f"{r.get('reviewed_at', '?')}  {r.get('scope', '?')}  "
            f"legs={legs}  fp={r.get('fingerprint', '?')}"
        )
        note = r.get("note", "")
        if note:
            print(f"    {note if len(note) <= 140 else note[:137] + '…'}")
    print(f"\n{len(rows_all)} record(s), newest first — full JSON per scope: --show")
    return 0


def _resolve_scope(
    args,
) -> tuple[str, str, bool, int | None, tuple[str | None, str | None]]:
    """fp/scope + the ledger-row context for one diff identity (ranged
    pair vs stack depth)."""
    if args.from_ref is not None:
        fp, scope = fingerprint_range(args.from_ref, args.to_ref)
        return fp, scope, True, None, (_rev_sha(args.from_ref), _rev_sha(args.to_ref))
    fp, scope = fingerprint(args.stack)
    return fp, scope, False, args.stack, (None, None)


def _record_verdict(args, rows, fp, scope, ranged, stack_for_row, range_shas) -> int:
    """--record: upsert the verdict row (union legs on a live
    fingerprint; backfill SHAs on rows recorded before S11b)."""
    legs = sorted(set(args.leg)) or ["delegation"]
    live = next((r for r in rows if r.get("fingerprint") == fp), None)
    if live is not None:
        live["legs"] = sorted(set(live.get("legs", [])) | set(legs))
        live["reviewed_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        if args.note:
            live["note"] = args.note
        if ranged:
            live["base_sha"], live["head_sha"] = range_shas
        row = live
    else:
        row = {
            "scope": scope,
            "stack": stack_for_row,
            "fingerprint": fp,
            "reviewed_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "legs": legs,
            "note": args.note,
            "base_sha": range_shas[0],
            "head_sha": range_shas[1],
        }
        rows.append(row)
    _save(rows)
    print(f"recorded {scope} fp={fp} legs={row['legs']}")
    return 0


def _print_verdict(args, state, row, scope, fp) -> None:
    """The status print: FRESH / STALE / SUBSUMED / nothing recorded."""
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
    elif state == "SUBSUMED" and row is not None:
        print(
            f"  contained in recorded range {row.get('scope')} — reviewed "
            f"{row['reviewed_at']} legs={row.get('legs')}"
            + (f" note={row['note']}" if row.get("note") else "")
        )
    else:
        print("  no recorded review for this diff")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stack", type=int, default=1, help="applied-patch depth (1 = top patch)")
    ap.add_argument(
        "--from",
        dest="from_ref",
        help="landed range base (with --to) — outranks --stack, per REVIEW_RANGE_ARGS",
    )
    ap.add_argument("--to", dest="to_ref", help="landed range head (with --from)")
    ap.add_argument("--record", action="store_true", help="record this diff as reviewed")
    ap.add_argument(
        "--leg",
        choices=("delegation", "managed", "scan"),
        action="append",
        default=[],
        help="which review leg produced the verdict (scan = the native "
        "bandit/shellcheck/sqlfluff/semgrep/osv leg; managed = OCR's paid "
        "LLM pass — record ONLY the legs that actually ran)",
    )
    ap.add_argument("--note", default="", help="verdict pointer (e.g. code_review.md section)")
    ap.add_argument("--show", action="store_true", help="print the ledger rows for this scope")
    ap.add_argument(
        "--all",
        action="store_true",
        help="list EVERY completion record in the ledger, newest first "
        "(--show is scope-scoped; this is the whole-ledger visibility view)",
    )
    args = ap.parse_args(argv)

    if bool(args.from_ref) != bool(args.to_ref):
        sys.exit("--from and --to go together (a half range is not a range)")
    if args.leg and not args.record:
        sys.exit("--leg only matters with --record (a status lookup records nothing)")
    if args.all:
        return _print_all_records(args)
    fp, scope, ranged, stack_for_row, range_shas = _resolve_scope(args)
    rows = _load()

    if args.show:
        scoped = [r for r in rows if r.get("scope") == scope]
        print(json.dumps(scoped, indent=2) if scoped else f"no rows for {scope}")
        return 0

    if args.record:
        return _record_verdict(args, rows, fp, scope, ranged, stack_for_row, range_shas)

    state, row, _ = status_range(args.from_ref, args.to_ref) if ranged else status(args.stack)
    _print_verdict(args, state, row, scope, fp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
