#!/usr/bin/env python3
"""Route-skeleton validator — the PUBLISHED half of the coverage ledger.

The security coverage ledger (`helpers/validators/coverage_ledger.py`, #247b
S3) holds the per-route verdicts: which attack classes were reviewed, on what
date, with what method, and what was found. That is an attack-surface map and
it stays operator-local in gitignored `doc/local/security/`.

The problem with local-only: on a fresh clone — yours or a collaborator's —
`check_coverage_ledger` finds no ledger and returns an ADVISORY SKIP. So the
completeness half of #247b silently stops holding for everyone except the
operator, and a 39th route can ship with no security review and a green gate.
That is the exact failure #247b was created to prevent.

This module covers that gap with the one artifact that is safe to publish: the
SHAPE of the public surface. Route paths and methods are already public — they
are literals in `app.py`, which is tracked. So a tracked skeleton discloses
nothing the repository does not already state. What it adds is a commitment
device: the surface is enumerated, so changing it is a deliberate diff rather
than an accident, and the gate fails when app.py and the skeleton disagree.

Deliberately NOT in the skeleton — all operator-local, all sensitive:
  - attack_class / verdict  (which routes lack auth, lack byte caps, ...)
  - reviewed / method       (who reviewed what, when, and how)
  - fingerprint             (a per-handler review-freshness hash)

So this check is deliberately WEAKER than the ledger: it enforces
completeness and shape, never review freshness. A route can be listed here
and be entirely unreviewed. The ledger remains the only thing that can say
"reviewed", and only the operator can read it.

Contract:
  agreement   — the set of (path, methods) in app.py must equal the skeleton's.
    A route in app.py that is not in the skeleton is UNLISTED (exit 1): the
    moment a new endpoint lands, someone has to consciously add it. A skeleton
    entry with no matching route is STALE (exit 1): the route was removed or
    renamed and the skeleton must be corrected. A method change (GET -> POST,
    i.e. read-only becoming mutating) is also exit 1, because that is a
    security-relevant shape change even though no new path appeared.
  bootstrap    — absent skeleton is a FATAL missing-required-file condition,
    not a skip. The whole point is that its absence is visible.

READ-ONLY in check mode; `seed` regenerates the skeleton from app.py.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_APP = REPO_ROOT / "app.py"
DEFAULT_SKELETON = REPO_ROOT / "doc" / "security" / "routes.json"

SCHEMA = "route-skeleton.v1"


def route_inventory(app_path: Path) -> dict[str, list[str]]:
    """Parse app.py for @app.route decorators -> {path: sorted methods}.

    Uses ast, so routes carrying a `methods=` argument are counted — the ones a
    literal `@app.route("<literal>")` grep silently drops.
    """
    text = app_path.read_text()
    tree = ast.parse(text)
    out: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            fn = dec.func
            name = getattr(getattr(fn, "attr", None), "id", None) or getattr(fn, "attr", None)
            if getattr(fn, "value", None) is None or name != "route":
                continue
            if not dec.args or not isinstance(dec.args[0], ast.Constant):
                continue
            path = dec.args[0].value
            if not isinstance(path, str):
                continue
            methods = sorted(
                str(el.value)
                for kw in dec.keywords
                if kw.arg == "methods" and isinstance(kw.value, ast.List)
                for el in kw.value.elts
                if isinstance(el, ast.Constant)
            )
            if not methods:
                methods = ["GET"]
            prev = out.get(path)
            methods = sorted(set(methods) | set(prev or []))
            out[path] = methods
    return out


def skeleton_payload(app_path: Path) -> dict:
    """Build the canonical skeleton payload for the current app.py."""
    inv = route_inventory(app_path)
    return {
        "schema": SCHEMA,
        "source": app_path.name,
        "note": (
            "Public surface SHAPE only — route paths and methods, which are "
            "already literals in the tracked, public app.py. Deliberately "
            "excludes attack_class, verdict, reviewed, method-of-review and "
            "fingerprint: those live in the operator-local coverage ledger "
            "(gitignored doc/local/security/coverage-ledger.json) and are an "
            "attack-surface map. This file is a commitment device so a "
            "surface change is a deliberate diff — it does NOT assert that any "
            "route was security-reviewed. Regenerate: "
            "python3 helpers/validators/route_skeleton.py --write"
        ),
        "count": len(inv),
        "routes": [{"path": p, "methods": inv[p]} for p in sorted(inv)],
    }


def load_skeleton(skeleton_path: Path) -> dict[str, list[str]] | None:
    """Skeleton -> {path: methods}. None if absent/unreadable/malformed."""
    if not skeleton_path.is_file():
        return None
    try:
        data = json.loads(skeleton_path.read_text())
    except OSError, json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    rows = data.get("routes")
    if not isinstance(rows, list):
        return None
    out: dict[str, list[str]] = {}
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("path"), str):
            return None
        ms = r.get("methods")
        if not isinstance(ms, list) or not all(isinstance(m, str) for m in ms):
            return None
        out[r["path"]] = sorted(ms)
    return out


def check(app_path: Path, skeleton_path: Path) -> tuple[list[str], list[str]]:
    """Return (fatal, advisory). rc 0 iff fatal is empty."""
    fatal: list[str] = []
    advisory: list[str] = []

    skel = load_skeleton(skeleton_path)
    if skel is None:
        return (
            [
                f"route skeleton missing or malformed at {skeleton_path} — this "
                f"check has no published surface to compare against. Create it "
                f"with: python3 helpers/validators/route_skeleton.py --write"
            ],
            [],
        )

    inv = route_inventory(app_path)

    unlisted = sorted(set(inv) - set(skel))
    for p in unlisted:
        fatal.append(
            f"UNLISTED route {p} ({'+'.join(inv[p])}) is in {app_path.name} but not in "
            f"{skeleton_path.name} — add it deliberately (and security-review it; the "
            f"operator-local coverage ledger is the record)"
        )

    stale = sorted(set(skel) - set(inv))
    for p in stale:
        fatal.append(
            f"STALE skeleton entry {p} ({'+'.join(skel[p])}) has no matching route in "
            f"{app_path.name} — removed or renamed; correct the skeleton "
            f"(python3 helpers/validators/route_skeleton.py --write)"
        )

    for p in sorted(set(inv) & set(skel)):
        if inv[p] != skel[p]:
            fatal.append(
                f"METHOD CHANGE {p}: app.py says {'+'.join(inv[p])}, skeleton says "
                f"{'+'.join(skel[p])} — a read-only surface becoming mutating is a "
                f"security-relevant change; update the skeleton deliberately"
            )

    if not fatal:
        advisory.append(
            f"route skeleton agrees with {app_path.name}: {len(inv)} routes "
            f"(shape only — no security verdict is implied)"
        )
    return fatal, advisory


def seed(app_path: Path, skeleton_path: Path) -> int:
    """Regenerate the skeleton from app.py. Never invents coverage — it only
    enumerates what is already there. Does not touch the operator-local
    coverage ledger, and running it is NOT a substitute for reviewing a new
    route."""
    payload = skeleton_payload(app_path)
    skeleton_path.parent.mkdir(parents=True, exist_ok=True)
    skeleton_path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"seed: wrote {skeleton_path} ({payload['count']} routes)")
    return 0


def main(argv: list[str]) -> int:
    if "--write" in argv:
        return seed(DEFAULT_APP, DEFAULT_SKELETON)
    fatal, advisory = check(DEFAULT_APP, DEFAULT_SKELETON)
    for line in fatal:
        print(f"FAIL: {line}")
    for line in advisory:
        print(f"  {line}")
    return 1 if fatal else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
