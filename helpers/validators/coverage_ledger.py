#!/usr/bin/env python3
"""Coverage-ledger validator — the cumulative-coverage gate (S3 of the
security-coverage-expansion arc, completed.md #247b, archived at
doc/improvements/archive/security/post_review_api_reaudit.md).

The security evaluation (doc/local/security/security_evaluation.md) is a
SNAPSHOT: 13 routes shipped after it closed with no review, and a later
inventory nearly mis-recorded /api/graph/refresh as removed because a
literal-match scan drops routes whose decorator carries a `methods=`
argument. This validator turns the coverage claim into a deterministic,
machine-checked structure instead of prose.

Contract:
  completeness — every route in app.py must have >=1 ledger row, else the
    route is UNREVIEWED (exit 1). This is the check that catches a new
    39th route the day it lands, not at the next manual recount.
  freshness   — each row carries a fingerprint of its handler's source at
    review time. A mismatch is STALE (exit 1 with --strict, warning
    otherwise): the handler changed after the recorded review, so the
    coverage no longer applies and the surface must be re-reviewed.
    Scope note: the fingerprint hashes the handler BODY (def line down),
    not the decorator — a changed route path or methods list does NOT
    trip STALE. Route-shape changes are caught by consistency only when
    the path string itself changes or the route disappears.
  consistency — a ledger row naming a route that no longer exists in
    app.py is ORPHANED (exit 1): the route was removed or renamed and the
    ledger must be corrected.
  validity — every row carries a `status` from the machine-checkable enum
    {planned, not_applicable, out_of_scope, in_progress, covered, candidate,
    blocked, deferred}. Rows with status `blocked` or `candidate` must also
    carry `blocked_reason` (why review cannot proceed) and `unresolved`
    (the exact open fact). Rows with status `not_applicable` or
    `out_of_scope` record an attack class deliberately NOT reviewed on
    that surface — this is how completeness stays honest: a surface is
    "covered" only when every attack class is either `covered`/`candidate`
    or explicitly `not_applicable`/`out_of_scope`. An exclusion must carry a
    `note` saying why the class was not reviewed here: EXCLUSION WITHOUT
    REASON is an error, because an unexplained exclusion is indistinguishable
    from a forgotten one.
  scope       — attack classes are accounted PER SCOPE. The required class
    set for a surface is the set observed inside that surface's own scope,
    so an exclusion recorded for the Flask app cannot silently excuse a Mojo,
    harness, or desktop surface, and a class that never applies to a scope is
    not demanded there. A class missing from a surface is UNREVIEWED CLASS
    (warning; error with --strict — the real ledger still carries a large
    uncovered queue, so the default stays advisory).

Row shape (one row per surface x attack class):
  {surface, kind, attack_class, reviewed, method, verdict, fingerprint,
   status, blocked_reason, unresolved}

  planning    — `plan` prints the prior-coverage brief a review should start
    from: rows by attack class, (surface, class) pairs with no row, rows
    whose status is still open (planned/in_progress/candidate/blocked/
    deferred), STALE rows, and the run `history` when the ledger records
    one. It is advisory (exit 0): the point is to make the previous run's
    conclusions an input to the next plan, not to gate.

  critic      — `critic` argues AGAINST the coverage claim: every (surface,
    class) pair with no row and no exclusion, every drifted fingerprint,
    every row whose surface left the inventory, every declared exclusion
    (so the claim is auditable), and — with `--findings` — every `confirmed`
    claim the ledger does not back with a covered row. Advisory by default
    (the operator's work queue); `--strict` exits 1 on an INCOMPLETE verdict.
    For `app` the surface set is the route inventory union the ledger; for
    every other family it is the ledger only, and the verdict says so rather
    than implying completeness.

  queue       — `queue` regenerates the coverage work queue as reason-coded
    JSONL (one record per (surface, attack_class) pair with no row and no
    exclusion) and prints a reason histogram that sums exactly to the queue
    length. The codes are the triage mechanism's pre-annotation, not
    verdicts: `exclusion_pattern` quotes the sibling note this pair almost
    certainly wants repeated, `not_a_route`/`mutating_route`/
    `read_only_route` carry the route-inventory fact. The queue file is
    derived state, regenerated wholesale on every run; the decisions
    journal the operator fills in from it is the durable input to `apply`.

  apply       — `apply` closes queue rows from
    doc/local/security/coverage_decisions.jsonl (dry-run by default;
    commit with `--write`). Every decision must name a pair the current
    queue still carries — closing only what the queue names keeps the
    ledger from moving under a queue that no longer exists. `cover`
    stamps the live handler fingerprint exactly as `seed` would, `exclude`
    demands an exclusion status plus a note, `defer` demands a note, and
    `bad_pair` is echoed but never applied: a pair that should not exist
    is fixed in app.py or the inventory, not papered over in the ledger.

Ledger shape: a bare JSON array of rows (legacy), or an object
`{"rows": [...], "history": [...]}` where `history` is the miniature record
of prior review runs — one `{date, scope, summary}` object per run.

`kind` is "route" (completeness- and consistency-checked against app.py),
"ingestion" (operator-triggered, not HTTP-reachable), or "shared"
(cross-cutting HTTP-reachable surfaces spanning many routes, e.g. the
shared graph connection). Only "route" rows are matched against app.py.
READ-ONLY in check, critic, queue, plan and dry-run apply modes; `seed`
recomputes fingerprints only; `apply --write` appends decision rows.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

# security_scopes lives in this package; boot the repo path first because the
# module also runs as a script (python3 helpers/validators/coverage_ledger.py),
# where the interpreter's sys.path holds only the script's own directory.
if __name__ == "__main__" and len(sys.argv) > 0:
    _repo = Path(__file__).resolve().parents[2]
    if str(_repo) not in sys.path:
        sys.path.insert(0, str(_repo))

from helpers.validators.security_scopes import member_surfaces  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DEFAULT_APP = REPO / "app.py"
DEFAULT_LEDGER = REPO / "doc/security/coverage-ledger.json"

# The coverage work queue, adopted from the graph-side triage mechanism
# (helpers/graph/triage_pending_relations.py): a sidecar queue the
# operator closes one row at a time, a decisions journal validated before
# it is applied, and a report whose parts sum to the totals already
# printed. The queue is derived state (regenerated wholesale); the
# ledger and the decisions journal are the durable records.
DEFAULT_QUEUE = REPO / "doc" / "local" / "security" / "coverage_queue.jsonl"
DEFAULT_DECISIONS = REPO / "doc" / "local" / "security" / "coverage_decisions.jsonl"

# What a decision may close a queue row AS. `bad_pair` is the escape
# hatch for a pair that should not exist (the surface is mis-named or
# the class does not apply to the scope at all): it is echoed, never
# applied — the fix belongs in app.py or the inventory, not the ledger.
DECISION_KINDS = ("cover", "exclude", "defer", "bad_pair", "amend")

# Reason codes behind a queue row — one per row, precedence in this
# order, so a reason histogram sums exactly to the queue length:
#   exclusion_pattern — the class already sits excluded with the SAME
#     note on >= 2 sibling surfaces; this pair almost certainly wants
#     the same exclusion, quoted with its exemplar note and sibling
#     count (the per-scope narrowing signal the taxonomy arc consumes).
#   not_a_route       — the surface is not in the route inventory (a
#     shared singleton or ledger-only naming); no inventory fact exists.
#   mutating_route    — the route accepts a non-GET method.
#   read_only_route   — the route is GET-only and the class needs body
#     volume or state mutation to apply (availability, concurrency);
#     the pair is a closed exclusion, not an open demand.
#   query_arg_sink    — the route is GET-only but the class is still
#     reachable via query arguments (injection, web-protocol/auth;
#     availability when the handler reads request.args — the AVAIL-1
#     guard); the pair remains demanded, downgraded from terminal.
GAP_REASONS = (
    "exclusion_pattern",
    "not_a_route",
    "mutating_route",
    "read_only_route",
    "query_arg_sink",
)

# Declared (machine-readable) per-scope attack-class applicability (A10).
# Merged with the classes observed in ledger rows: a class earns demand
# for a scope either because a row already carries it or because this file
# declares it applicable — so a class the operator wants swept shows up in
# `critic` and `queue` even before the first row exists.
DEFAULT_TAXONOMY = REPO / "doc" / "security" / "class-taxonomy.json"

# Classes that remain demandable on a GET-only route: injection rides
# query-string parameters and web-protocol/auth protects information
# disclosure on reads, so their (route, class) pair is still an open
# demand there (`query_arg_sink`), not a terminal `read_only_route`.
QUERY_REACHABLE_CLASSES = frozenset({"injection", "web-protocol/auth"})

# Classes that need body volume or state mutation to apply: on a GET-only
# route their pair is a closed exclusion — terminal `read_only_route`.
READ_ONLY_TERMINAL_CLASSES = frozenset({"availability", "concurrency"})

# AVAIL-1 near-miss guard: a GET-only route that reads query-string input
# keeps its availability demand open (query_arg_sink, not terminal). The
# proven counterexample is AVAIL-1 itself — the quadratic join, 52.8 s
# over 9,282 docs, lived on a GET route and was driven entirely by
# request.args volume. Concurrency stays terminal even with args: a race
# needs shared state to mutate, which request.args does not create.
ARGS_INPUT_CLASSES = frozenset({"availability"})
ARGS_INPUT_RE = re.compile(r"request\.(?:args|values)\b")

# Row-level scope: which surface family the row belongs to. The ledger is
# repo-wide (Q4), so "uncovered" only means something inside a scope — a
# concurrency gap on the Flask app is not comparable to one in the Mojo graph.
SCOPE_ENUM = (
    "app",
    "extractors",
    "graph",
    "harness",
    "desktop",
    "frontend",
    "data",
)
DEFAULT_SCOPE = "app"

# Machine-checkable status enum (mirrors cloudflare/security-audit-skill's
# STATUSES). `verdict` remains as free-text for backward compatibility;
# `status` is the field `check()` validates.
STATUS_ENUM = (
    "planned",
    "not_applicable",
    "out_of_scope",
    "in_progress",
    "covered",
    "candidate",
    "blocked",
    "deferred",
)


def route_inventory(app_path: Path) -> dict[str, dict]:
    """Parse app.py for @app.route decorators -> {path: meta}.

    Uses ast, so routes with a `methods=` argument (the ones a literal
    `@app.route("<literal>")` grep silently drops) are counted. The route
    path is the FIRST string literal of the decorator call.
    """
    text = app_path.read_text()
    tree = ast.parse(text)
    out: dict[str, dict] = {}
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
            ) or ["GET"]
            seg = ast.get_source_segment(text, node)
            # Same truthiness as ``dec.lineno and seg or ""`` but type-stable:
            # hash the handler's source when the decorator has a real line,
            # else a constant empty string.
            snippet = (seg or "") if dec.lineno else ""
            fp = hashlib.sha256(snippet.encode()).hexdigest()[:16]
            out[path] = {
                "handler": node.name,
                "line": dec.lineno,
                "methods": methods,
                "fingerprint": fp,
                # The handler's own source, kept so the gap classifier can
                # reason about what the handler reads without re-parsing
                # app.py (the AVAIL-1 guard greps it for request.args).
                "source": snippet,
            }
    return out


def _load(path: Path) -> object:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return []


def load_ledger(path: Path) -> list[dict]:
    """Rows from a bare array (legacy) or {"rows": [...], "history": [...]}."""
    data = _load(path)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        rows = data.get("rows")
        if isinstance(rows, list):
            return rows
    sys.exit(f"ledger must be a JSON array or an object with a rows array: {path}")


def load_history(path: Path) -> list[dict]:
    """The miniature prior-run record; empty for a bare-array ledger."""
    data = _load(path)
    if not isinstance(data, dict):
        return []
    history = data.get("history")
    if not isinstance(history, list):
        return []
    return [h for h in history if isinstance(h, dict)]


def _row_scope(row: dict) -> str:
    """The row's scope family; legacy rows predate the field and are `app`."""
    return str(row.get("scope") or DEFAULT_SCOPE)


def _scope_of(rows: list[dict]) -> str:
    return _row_scope(rows[0]) if rows else DEFAULT_SCOPE


def classes_by_scope(
    rows: list[dict], *, declared: dict[str, set[str]] | None = None
) -> dict[str, set[str]]:
    """Attack classes demanded per scope (A2 + A10).

    The required set is per scope, not global: an exclusion recorded for the
    Flask app must not silently excuse a Mojo or harness surface, and a class
    that never applies to a scope (e.g. HTTP protocol auth on a desktop app)
    is not demanded there. Demand is declared classes union observed classes:
    a class earns demand for a scope either because this taxonomy file
    declares it applicable or because a ledger row already carries it — so a
    class the operator wants swept shows up even before the first row exists.
    """
    out: dict[str, set[str]] = {}
    if declared:
        for scope, classes in declared.items():
            out.setdefault(scope, set()).update(classes)
    for r in rows:
        cls = r.get("attack_class")
        if cls:
            out.setdefault(_row_scope(r), set()).add(str(cls))
    return out


def _load_taxonomy(path: Path | None = None) -> dict[str, set[str]]:
    """Per-scope declared attack-class applicability (A10).

    Missing file keeps the row-emergent behavior; a malformed file is a
    loud error so a hand-edited taxonomy never silently widens or narrows
    coverage.
    """
    try:
        data = json.loads((path or DEFAULT_TAXONOMY).read_text())
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        sys.exit(f"class-taxonomy: {path} is not JSON: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("scopes"), dict):
        sys.exit(f"class-taxonomy: {path} must be an object with a 'scopes' map: {path}")
    out: dict[str, set[str]] = {}
    for scope, classes in data["scopes"].items():
        if scope not in SCOPE_ENUM:
            sys.exit(f"class-taxonomy: unknown scope {scope!r} in {path}")
        if not isinstance(classes, list) or not all(isinstance(c, str) for c in classes):
            sys.exit(f"class-taxonomy: scope {scope!r} must map to a list of class names in {path}")
        out[scope] = set(classes)
    return out


def check(  # noqa: C901
    app_path: Path,
    ledger_path: Path,
    *,
    strict: bool,
    scope: str | None = None,
    taxonomy: Path | None = None,
) -> int:
    """Validate the ledger, optionally for one surface family only (`--scope`).

    Without a scope this is the whole-repo gate. With a scope it is the
    close-out gate for one family: rows, class accounting and freshness are
    filtered to that family, and the Flask route inventory is only demanded for
    `app` — no other family has a route inventory to diff against.
    """
    routes = route_inventory(app_path)
    all_rows = load_ledger(ledger_path)
    rows = [r for r in all_rows if scope is None or _row_scope(r) == scope]
    by_surface: dict[str, list[dict]] = {}
    for r in rows:
        by_surface.setdefault(r["surface"], []).append(r)

    errors: list[str] = []
    warns: list[str] = []

    # 0. validity: status enum + required fields per status
    for r in rows:
        status = r.get("status")
        rscope = r.get("scope")
        if rscope is not None and rscope not in SCOPE_ENUM:
            errors.append(
                f"INVALID SCOPE {r.get('surface')} / {r.get('attack_class')} — "
                f"scope={rscope!r} not in {SCOPE_ENUM}; "
                f"repo-wide coverage is only meaningful per surface family"
            )
        if status is not None and status not in STATUS_ENUM:
            errors.append(
                f"INVALID STATUS {r.get('surface')} / {r.get('attack_class')} — "
                f"status={status!r} not in {STATUS_ENUM}; "
                f"row lacks review coverage classification"
            )
        if status in ("blocked", "candidate"):
            if not r.get("blocked_reason"):
                errors.append(
                    f"MISSING blocked_reason {r.get('surface')} / {r.get('attack_class')} — "
                    f"status={status} requires blocked_reason"
                )
            if not r.get("unresolved"):
                errors.append(
                    f"MISSING unresolved {r.get('surface')} / {r.get('attack_class')} — "
                    f"status={status} requires unresolved (the exact open fact)"
                )
        if status in ("not_applicable", "out_of_scope") and not str(r.get("note") or "").strip():
            errors.append(
                f"EXCLUSION WITHOUT REASON {r.get('surface')} / {r.get('attack_class')} — "
                f"status={status} records a deliberate exclusion and must carry a note "
                f"saying why the class was not reviewed here"
            )

    # 1. completeness: every route has >=1 row (the route inventory is `app`'s)
    route_paths = sorted(routes) if scope in (None, "app") else []
    for path in route_paths:
        if path not in by_surface:
            errors.append(
                f"UNREVIEWED {path} (handler {routes[path]['handler']} "
                f"app.py:{routes[path]['line']}) — no ledger row; "
                f"review it or seed a row"
            )

    # 2. per-surface attack-class accounting WITHIN the row's own scope (A2):
    #    a surface missing a class must carry an exclusion row with a reason,
    #    and a class observed only in another scope is not demanded here.
    #    Declared applicability (A10) widens the demand beyond the classes
    #    the ledger has already observed.
    classes_for_scope = classes_by_scope(all_rows, declared=_load_taxonomy(taxonomy))
    surfaces = set(by_surface) | (set(routes) if scope in (None, "app") else set())
    for surface in sorted(surfaces):
        rs = by_surface.get(surface, [])
        surface_scope = _scope_of(rs)
        accounted_classes = {r.get("attack_class") for r in rs if r.get("attack_class")}
        for cls in sorted(classes_for_scope.get(surface_scope, set())):
            if cls in accounted_classes:
                continue
            msg = (
                f"UNREVIEWED CLASS {surface} / {cls} (scope={surface_scope}) — no ledger row; "
                f"review it or add a not_applicable/out_of_scope row with a reason"
            )
            (errors if strict else warns).append(msg)

    # 2b. freshness: fingerprints match the reviewed handler source
    for surface, rs in sorted(by_surface.items()):
        live = routes.get(surface)
        for r in rs:
            if r.get("kind") != "route":
                continue
            if live is None:
                errors.append(
                    f"ORPHANED row for {surface} — not in app.py "
                    "(removed or renamed); correct the ledger"
                )
                continue
            if r.get("fingerprint") != live["fingerprint"]:
                msg = (
                    f"STALE {surface} / {r.get('attack_class')} — handler "
                    f"changed since review {r.get('reviewed')}; re-review"
                )
                (errors if strict else warns).append(msg)

    for w in warns:
        print(f"warn: {w}")
    for e in errors:
        print(f"FAIL: {e}")
    if scope in (None, "app"):
        n = len(routes)
        covered = sum(1 for p in routes if p in by_surface)
        print(f"coverage: {covered}/{n} routes have a ledger row")
    if scope is not None:
        print(f"scope: {scope} — {len(by_surface)} surface(s), {len(rows)} row(s)")
    if errors:
        return 1
    print("ok: ledger complete, no orphaned rows")
    return 0


def _status_label(row: dict) -> str:
    """`status` when present, else the verdict's leading token.

    The legacy rows carry free-text `verdict` sentences; only the first token
    ("clean", "note:low", "REMEDIATED") is a classification, so the tally uses
    that rather than echoing prose.
    """
    status = str(row.get("status") or "").strip()
    if status:
        return status
    verdict = str(row.get("verdict") or "?").strip()
    return verdict.split()[0] if verdict else "?"


# A row that records an actual review. Legacy rows carry a free-text verdict
# ("clean", "note:low", "REMEDIATED") instead of a `status`, so both spellings
# count; planned/blocked/deferred/excluded rows do not.
COVERED_LABELS = frozenset({"covered", "candidate", "clean", "note:low", "remediated"})
EXCLUDED_STATUSES = ("not_applicable", "out_of_scope")


def _is_covered(row: dict) -> bool:
    return _status_label(row).lower() in COVERED_LABELS


def _age_days(reviewed: object, today: date | None = None) -> int | None:
    """Days since a row was reviewed; None when the date is unusable."""
    try:
        when = date.fromisoformat(str(reviewed)[:10])
    except ValueError:
        return None
    return ((today or date.today()) - when).days


def _confirmed_claims(path: Path) -> list[tuple[str, str]]:
    """(surface, attack_class) pairs a run's findings claim as confirmed.

    Accepts a bare findings array or the run envelope. A `confirmed` claim
    asserts the surface was reviewed on that class, so the ledger must carry a
    covered row for the pair; the critic reports the ones it does not.
    """
    data = _load(path)
    if isinstance(data, dict):
        data = data.get("findings")
    if not isinstance(data, list):
        sys.exit(f"findings must be a JSON array or an envelope with findings: {path}")
    return [
        (str(f.get("surface") or ""), str(f.get("attack_class") or ""))
        for f in data
        if isinstance(f, dict) and f.get("verdict") == "confirmed"
    ]


def plan(  # noqa: C901
    app_path: Path,
    ledger_path: Path,
    *,
    recent: int = 5,
    scope: str | None = None,
) -> int:
    """Print the prior-coverage brief a review plan starts from (A9).

    Advisory: always returns 0. The ledger is the previous run's memory —
    this surfaces what it settled, what it left open, and what has drifted,
    so the next plan does not re-review settled surfaces or silently drop a
    class the last run excluded.
    """
    routes = route_inventory(app_path)
    rows = load_ledger(ledger_path)
    history = load_history(ledger_path)
    if scope:
        rows = [r for r in rows if (r.get("scope") or DEFAULT_SCOPE) == scope]
    by_surface: dict[str, list[dict]] = {}
    for r in rows:
        by_surface.setdefault(r["surface"], []).append(r)

    dates = sorted({str(r["reviewed"]) for r in rows if r.get("reviewed")})
    span = (
        dates[0] if len(dates) == 1 else f"{dates[0]}..{dates[-1]}" if dates else "no review dates"
    )
    print(
        f"prior: {len(rows)} rows over {len(by_surface)} surfaces "
        + (f"({len(routes)} routes in app.py); " if scope in (None, "app") else "")
        + f"reviewed {span}"
        + (f"; scope={scope}" if scope else "")
    )
    if not scope:
        scopes = Counter(str(r.get("scope") or DEFAULT_SCOPE) for r in rows)
        print("  scopes: " + ", ".join(f"{k}={v}" for k, v in sorted(scopes.items())))

    classes = sorted({str(r["attack_class"]) for r in rows if r.get("attack_class")})
    for cls in classes:
        rs = [r for r in rows if r.get("attack_class") == cls]
        counts = Counter(_status_label(r) for r in rs)
        tally = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        print(f"  {cls}: {len(rs)} rows — {tally}")

    accounted = {(r.get("surface"), r.get("attack_class")) for r in rows if r.get("attack_class")}
    by_scope_classes = classes_by_scope(rows)
    target = scope or DEFAULT_SCOPE
    required = sorted(by_scope_classes.get(target, set()))
    if target == "app":
        # The Flask app has a route inventory to diff against; every other
        # scope is inventoried by the surfaces the ledger already names.
        surfaces = sorted(
            set(routes) | {s for s, rs in by_surface.items() if _scope_of(rs) == "app"}
        )
    else:
        # Non-app scopes get their surfaces from the gitignored inventories
        # file (seeded by security_scopes.py) union the ledger's own names.
        surfaces = sorted(
            set(member_surfaces(target))
            | {s for s, rs in by_surface.items() if _scope_of(rs) == target}
        )
    gaps = [(s, c) for s in surfaces for c in required if (s, c) not in accounted]
    exclusions = [
        r
        for r in rows
        if _row_scope(r) == target and r.get("status") in ("not_applicable", "out_of_scope")
    ]
    open_status = ("planned", "in_progress", "candidate", "blocked", "deferred")
    open_rows = [r for r in rows if r.get("status") in open_status]
    stale = [
        (surface, str(r.get("attack_class")), str(r.get("reviewed")))
        for surface, rs in sorted(by_surface.items())
        if surface in routes
        for r in rs
        if r.get("kind") == "route" and r.get("fingerprint") != routes[surface]["fingerprint"]
    ]
    orphans = sorted(
        s
        for s, rs in by_surface.items()
        if s not in routes and any(r.get("kind") == "route" for r in rs)
    )

    print(
        f"work queue: {len(gaps)} uncovered pair(s), {len(open_rows)} open row(s), "
        f"{len(stale)} stale, {len(orphans)} orphaned surface(s)"
    )
    if not scope:
        tally = []
        for sc in sorted(by_scope_classes):
            sc_surfaces = sorted(s for s, rs in by_surface.items() if _scope_of(rs) == sc)
            missing = sum(
                1 for s in sc_surfaces for c in by_scope_classes[sc] if (s, c) not in accounted
            )
            tally.append(f"{sc}={missing}")
        if tally:
            print("  queue by scope: " + ", ".join(tally))
    for surface, cls in gaps:
        print(f"  GAP {cls} @ {surface} — no ledger row")
    for r in open_rows:
        why = str(r.get("blocked_reason") or r.get("note") or "(no reason recorded)")
        line = f"  OPEN {r.get('attack_class')} @ {r.get('surface')} — "
        line += f"status={r.get('status')}: {why}"
        if r.get("unresolved"):
            line += f"; unresolved: {r['unresolved']}"
        print(line)
    for surface, cls, reviewed in stale:
        print(f"  STALE {cls} @ {surface} — handler changed since {reviewed}")
    for surface in orphans:
        print(f"  ORPHANED {surface} — not in app.py; correct the ledger")
    for r in exclusions:
        print(
            f"  EXCLUDED {r.get('attack_class')} @ {r.get('surface')} — "
            f"status={r.get('status')}: {r.get('note') or '(no reason recorded)'}"
        )

    if history:
        take = max(recent, 1)
        shown = history[-take:]
        print(f"history: {len(history)} prior run(s); showing {len(shown)}")
        for h in shown:
            line = f"  {h.get('date', '?')} {h.get('scope', '?')}"
            summary = h.get("summary") or h.get("result")
            if summary:
                line = f"{line} — {summary}"
            print(line)
    else:
        print("history: none recorded")
    return 0


def critic(  # noqa: C901
    app_path: Path,
    ledger_path: Path,
    *,
    scope: str | None = None,
    findings_path: Path | None = None,
    as_json: bool = False,
    strict: bool = False,
    exclude_tripwire: float | None = None,
    taxonomy: Path | None = None,
) -> int:
    """Emit the coverage critic's work queue for one surface family (A7).

    The critic argues against the coverage claim instead of restating it: every
    (surface, attack_class) pair with no row and no exclusion, every fingerprint
    that drifted since the row's review, every row whose surface left the
    inventory, every declared exclusion (so the claim is auditable), and — when
    a run's findings are supplied — every `confirmed` claim the ledger does not
    back with a covered row.

    Advisory by default (exit 0): it is the operator's work queue, not a gate.
    `--strict` exits 1 on an INCOMPLETE verdict so an arc cannot close on a
    claim the critic rejects. For `app` the surface set is the route inventory
    union the ledger; for every other family it is the ledger only, because no
    inventory exists yet — the verdict says so rather than implying completeness.
    `--exclude-tripwire PCT` adds the taxonomy arc's advisory tripwire: warn on
    stderr when the share of accounted pairs closed by exclusion crosses PCT —
    a scope can look complete because everything was excluded rather than
    reviewed, which is the coverage analogue of a silent mass discard.
    """
    target = scope or DEFAULT_SCOPE
    routes = route_inventory(app_path)
    all_rows = load_ledger(ledger_path)
    rows = [r for r in all_rows if _row_scope(r) == target]
    by_surface: dict[str, list[dict]] = {}
    for r in rows:
        by_surface.setdefault(r["surface"], []).append(r)

    accounted = {(r.get("surface"), r.get("attack_class")) for r in rows if r.get("attack_class")}
    required = sorted(classes_by_scope(rows, declared=_load_taxonomy(taxonomy)).get(target, set()))
    surfaces = (
        sorted(set(routes) | set(by_surface))
        if target == "app"
        else sorted(set(member_surfaces(target)) | set(by_surface))
    )

    uncovered = [
        {"surface": s, "attack_class": c, "scope": target}
        for s in surfaces
        for c in required
        if (s, c) not in accounted
    ]
    zero_row = [s for s in surfaces if s not in by_surface]

    stale: list[dict] = []
    orphaned: list[dict] = []
    for surface in sorted(by_surface):
        live = routes.get(surface)
        for r in by_surface[surface]:
            if r.get("kind") != "route":
                continue
            if live is None:
                orphaned.append({"surface": surface, "attack_class": r.get("attack_class")})
                continue
            if r.get("fingerprint") != live["fingerprint"]:
                stale.append(
                    {
                        "surface": surface,
                        "attack_class": r.get("attack_class"),
                        "reviewed": r.get("reviewed"),
                        "age_days": _age_days(r.get("reviewed")),
                    }
                )

    excluded = [
        {
            "surface": r.get("surface"),
            "attack_class": r.get("attack_class"),
            "status": r.get("status"),
            "note": r.get("note"),
        }
        for r in rows
        if r.get("status") in EXCLUDED_STATUSES
    ]

    never_covered = [
        c for c in required if not any(r.get("attack_class") == c and _is_covered(r) for r in rows)
    ]

    covered_pairs = {
        (r.get("surface"), r.get("attack_class"))
        for r in rows
        if r.get("attack_class") and _is_covered(r)
    }
    unbacked = (
        [
            {"surface": s, "attack_class": c}
            for s, c in sorted(set(_confirmed_claims(findings_path)))
            if (s, c) not in covered_pairs
        ]
        if findings_path is not None
        else []
    )

    # Exclusion share (taxonomy arc): of the pairs the ledger accounts for,
    # how many are closed by exclusion rather than review. A share near
    # 100% means the scope is "complete" because everything was excluded —
    # the coverage analogue of a silent mass discard.
    accounted_pairs = covered_pairs | {(e["surface"], e["attack_class"]) for e in excluded}
    excl_share = round(100 * len(excluded) / len(accounted_pairs), 1) if accounted_pairs else 0.0

    # A note BLOCKS the verdict; a caveat is printed and does not. The
    # split matters: the app scope is machine-enumerated (the route walk
    # catches a new route the day it lands), every other family is
    # hand-enumerated AREAS. That is a caveat about how the surface set
    # was obtained — it does not make an accounted pair unaccounted, so
    # it must not make a scope permanently INCOMPLETE (that would leave
    # an arc with no way to close).
    notes: list[str] = []
    caveats: list[str] = []
    if not rows:
        notes.append(f"no ledger rows in scope {target}")
    if not required:
        notes.append(
            f"no attack classes recorded for scope {target} — nothing can be judged covered"
        )
    if target != "app":
        caveats.append(
            f"surfaces for scope {target} come from the scope inventory "
            f"({len(member_surfaces(target))} area(s)) — hand-enumerated areas, "
            f"not a machine walk: a new surface in this family is not detected "
            f"the way a new app route is"
        )

    counts = {
        "uncovered": len(uncovered),
        "stale": len(stale),
        "orphaned": len(orphaned),
        "excluded": len(excluded),
        "excluded_share_pct": excl_share,
        "zero_row_surfaces": len(zero_row),
        "never_covered_classes": len(never_covered),
        "unbacked_claims": len(unbacked),
    }
    complete = not any(counts[k] for k in ("uncovered", "stale", "orphaned", "unbacked_claims"))
    verdict = "COMPLETE" if complete and not notes else "INCOMPLETE"

    if as_json:
        print(
            json.dumps(
                {
                    "schema": "coverage-critic.v1",
                    "scope": target,
                    "ledger": str(ledger_path),
                    "findings": str(findings_path) if findings_path is not None else None,
                    "generated": date.today().isoformat(),
                    "verdict": verdict,
                    "counts": counts,
                    "uncovered": uncovered,
                    "stale": stale,
                    "orphaned": orphaned,
                    "excluded": excluded,
                    "zero_row_surfaces": zero_row,
                    "never_covered_classes": never_covered,
                    "unbacked_claims": unbacked,
                    "notes": notes,
                    "caveats": caveats,
                },
                indent=1,
                ensure_ascii=False,
            )
        )
    else:
        print(
            f"critic: {target} — {len(surfaces)} surface(s), {len(rows)} row(s), "
            f"{len(required)} required class(es)"
        )
        print(
            f"verdict: {verdict} — {counts['uncovered']} uncovered pair(s), "
            f"{counts['stale']} stale, {counts['orphaned']} orphaned, "
            f"{counts['unbacked_claims']} unbacked claim(s)"
        )
        for gap in uncovered:
            print(f"  GAP {gap['attack_class']} @ {gap['surface']} — no row and no exclusion")
        for row in stale:
            age = f"{row['age_days']}d ago" if row["age_days"] is not None else "age unknown"
            print(
                f"  STALE {row['attack_class']} @ {row['surface']} — "
                f"reviewed {row['reviewed']} ({age})"
            )
        for row in orphaned:
            print(f"  ORPHANED {row['attack_class']} @ {row['surface']} — not in app.py")
        for row in excluded:
            print(
                f"  EXCLUDED {row['attack_class']} @ {row['surface']} — "
                f"status={row['status']}: {row['note'] or '(no reason recorded)'}"
            )
        if excluded:
            print(
                f"  exclusion share: {excl_share}% of {len(accounted_pairs)} accounted pair(s) "
                f"closed by exclusion"
            )
        for row in unbacked:
            print(
                f"  UNBACKED {row['attack_class']} @ {row['surface']} — "
                f"a confirmed finding the ledger does not back with a covered row"
            )
        if never_covered:
            print("  never covered: " + ", ".join(never_covered))
        if zero_row:
            print(f"  no row at all: {len(zero_row)} surface(s)")
        for note in notes:
            print(f"  note: {note}")
        for caveat in caveats:
            print(f"  caveat: {caveat}")

    if exclude_tripwire is not None and excl_share > exclude_tripwire:
        print(
            f"!!! EXCLUDE-TRIPWIRE tripped: exclusion share {excl_share}% "
            f"({len(excluded)}/{len(accounted_pairs)}) exceeds threshold {exclude_tripwire}%",
            file=sys.stderr,
        )
    return 1 if strict and verdict != "COMPLETE" else 0


def _exclusion_patterns(cls: str, rows: list[dict]) -> list[tuple[str, int]]:
    """Sibling exclusions of one class, collapsed by normalized note.

    Returns (note, count) groups with count >= 2, most common first: a
    decision that repeats identically across siblings is quoted later as
    the exemplar for the sibling surface that has no row yet.
    """
    groups: Counter[str] = Counter()
    for r in rows:
        if r.get("attack_class") != cls or r.get("status") not in EXCLUDED_STATUSES:
            continue
        note = " ".join(str(r.get("note") or "").split()).lower()
        if note:
            groups[note] += 1
    return [(note, count) for note, count in groups.most_common() if count >= 2]


def _gap_records(
    app_path: Path,
    ledger_path: Path,
    *,
    scope: str | None = None,
    taxonomy: Path | None = None,
) -> list[dict]:
    """Reason-coded records for every (surface, attack_class) pair with no row.

    Mirrors the exact pair selection the `critic` verdict reports, so a
    queue row always names a pair the critic would flag. Precedence is
    GAP_REASONS order; each record carries the underlying facts
    (`methods`, `exemplar_note`, `siblings`) so the operator closes the
    row from evidence, not from the code alone.
    """
    target = scope or DEFAULT_SCOPE
    routes = route_inventory(app_path)
    rows = [r for r in load_ledger(ledger_path) if _row_scope(r) == target]
    accounted = {(r.get("surface"), r.get("attack_class")) for r in rows if r.get("attack_class")}
    required = sorted(classes_by_scope(rows, declared=_load_taxonomy(taxonomy)).get(target, set()))
    by_surface: dict[str, list[dict]] = {}
    for r in rows:
        by_surface.setdefault(r["surface"], []).append(r)
    surfaces = (
        sorted(set(routes) | set(by_surface))
        if target == "app"
        else sorted(set(member_surfaces(target)) | set(by_surface))
    )

    records: list[dict] = []
    for surface in surfaces:
        for cls in required:
            if (surface, cls) in accounted:
                continue
            live = routes.get(surface)
            patterns = _exclusion_patterns(cls, rows)
            record: dict = {
                "scope": target,
                "surface": surface,
                "attack_class": cls,
                "reason": "",  # filled by the precedence chain below
            }
            if patterns:
                record["reason"] = "exclusion_pattern"
                record["exemplar_note"], record["siblings"] = patterns[0]
            elif live is None:
                record["reason"] = "not_a_route"
            elif live["methods"] != ["GET"]:
                record["reason"] = "mutating_route"
                record["methods"] = live["methods"]
            elif cls in READ_ONLY_TERMINAL_CLASSES:
                if cls in ARGS_INPUT_CLASSES and ARGS_INPUT_RE.search(live.get("source") or ""):
                    # AVAIL-1 guard: query-string volume drives the work,
                    # so this GET route is NOT terminal for the class
                    # (AVAIL-1 was exactly such a route).
                    record["reason"] = "query_arg_sink"
                else:
                    # GET-only ends these classes: availability without
                    # query input needs body or alloc volume, concurrency
                    # needs a state mutation to race.
                    record["reason"] = "read_only_route"
                record["methods"] = live["methods"]
            else:
                record["reason"] = "query_arg_sink"
                record["methods"] = live["methods"]
            records.append(record)
    return records


def queue(
    app_path: Path,
    ledger_path: Path,
    *,
    scope: str | None = None,
    out: Path = DEFAULT_QUEUE,
    taxonomy: Path | None = None,
) -> int:
    """Regenerate the coverage work queue as reason-coded JSONL (taxonomy arc).

    One JSON line per uncovered (surface, attack_class) pair, the same
    selection the critic's GAP lines report, pre-annotated with a reason
    the way the graph-side triage pre-annotates decisions. Prints a reason
    histogram whose parts sum exactly to the queue length. The queue is
    derived state — regenerated wholesale on every run (an empty queue
    unlinks the file); the durable records are the ledger and the
    decisions journal filled from this file. Advisory: exit 0.
    """
    records = _gap_records(app_path, ledger_path, scope=scope, taxonomy=taxonomy)
    target = scope or DEFAULT_SCOPE
    histogram = Counter(r["reason"] for r in records)
    out.parent.mkdir(parents=True, exist_ok=True)
    if records:
        out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
    elif out.exists():
        out.unlink()
    tally = " ".join(f"{code}={histogram.get(code, 0)}" for code in GAP_REASONS)
    print(f"queue: {target} — {len(records)} gap(s); reasons: {tally}")
    print(f"wrote {out}")
    print(
        f"decisions: one JSON object per line in {DEFAULT_DECISIONS} — keys: "
        f"surface, attack_class, decision in {DECISION_KINDS}, plus the fields "
        f"that decision kind demands; then: coverage_ledger.py apply [--write]"
    )
    return 0


def _read_decisions(path: Path) -> list[dict]:
    """One JSON object per line; blank lines skipped, bad lines fatal."""
    if not path.exists():
        sys.exit(f"decisions journal not found: {path}")
    out: list[dict] = []
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            sys.exit(f"decisions line {lineno} is not JSON: {path}: {exc}")
        if not isinstance(obj, dict):
            sys.exit(f"decisions line {lineno} must be a JSON object: {path}")
        out.append(obj)
    return out


def apply(  # noqa: C901
    app_path: Path,
    ledger_path: Path,
    decisions_path: Path = DEFAULT_DECISIONS,
    *,
    write: bool = False,
    scope: str | None = None,
    taxonomy: Path | None = None,
) -> int:
    """Close queue rows onto the ledger from the decisions journal.

    Every decision must name a pair the current queue still carries:
    closing only what the queue names keeps `--write` from moving the
    ledger under a queue that no longer exists. `cover` stamps the live
    handler fingerprint (the same contract `seed` honors) and demands a
    verdict plus a review date; `exclude` demands an exclusion status and
    a note; `defer` demands a note; `bad_pair` is echoed, never applied.
    `amend` revises an existing ledger row's verdict/status/note/reviewed
    (the re-review path): it demands an already-accounted pair and, when
    `reviewed` is given, an ISO date. Dry-run by default — nothing is
    written without `--write`.
    """
    routes = route_inventory(app_path)
    decisions = _read_decisions(decisions_path)
    open_pairs = {
        (r["surface"], r["attack_class"]): r
        for r in _gap_records(app_path, ledger_path, scope=scope, taxonomy=taxonomy)
    }
    ledger_rows = load_ledger(ledger_path)
    existing_rows: dict[tuple[str, str], dict] = {}
    for r in ledger_rows:
        surface, cls = r.get("surface"), r.get("attack_class")
        if cls and isinstance(surface, str):
            existing_rows[(surface, cls)] = r
    existing = set(existing_rows)

    errors: list[str] = []
    accepted: list[tuple[dict, dict]] = []
    bad_pairs: list[str] = []
    for d in decisions:
        surface, cls, kind = d.get("surface"), d.get("attack_class"), d.get("decision")
        label = f"{surface} / {cls}"
        pair = (surface, cls)
        if kind not in DECISION_KINDS:
            errors.append(f"INVALID DECISION {label} — decision={kind!r} not in {DECISION_KINDS}")
            continue
        if kind == "amend":
            # The re-review path: amend demands an already-accounted pair,
            # so it is checked BEFORE the queue-membership gate (which only
            # pairs with no row can pass).
            base = existing_rows.get(pair)
            if base is None:
                errors.append(f"NOT ACCOUNTED {label} — decide=amend needs an existing ledger row")
                continue
            raw_reviewed = d.get("reviewed")
            if raw_reviewed:
                try:
                    date.fromisoformat(str(raw_reviewed))
                except ValueError:
                    errors.append(
                        f"INVALID DATE {label} — amend demands reviewed=YYYY-MM-DD "
                        f"(the row's freshness hangs on it), got {raw_reviewed!r}"
                    )
                    continue
            row = dict(base)
            row["reviewed"] = str(raw_reviewed or base.get("reviewed") or date.today().isoformat())
            if d.get("verdict") is not None:
                row["verdict"] = d["verdict"]
            if d.get("status") is not None:
                row["status"] = d["status"]
            if d.get("note") is not None:
                row["note"] = d["note"]
            if d.get("method") is not None:
                row["method"] = d["method"]
            accepted.append((d, row))
            continue
        if pair in existing:
            errors.append(
                f"ALREADY ACCOUNTED {label} — the ledger already carries a row for this pair"
            )
            continue
        if pair not in open_pairs:
            errors.append(
                f"NOT A QUEUE ROW {label} — close only pairs the queue names; "
                f"regenerate it with: coverage_ledger.py queue --app <app.py> --ledger <ledger>"
            )
            continue
        if kind == "bad_pair":
            bad_pairs.append(label)
            continue
        if kind == "exclude":
            if d.get("status") not in EXCLUDED_STATUSES:
                errors.append(
                    f"INVALID STATUS {label} — decide=exclude demands status ∈ "
                    f"{EXCLUDED_STATUSES}, got {d.get('status')!r}"
                )
                continue
            if not str(d.get("note") or "").strip():
                errors.append(
                    f"EXCLUSION WITHOUT REASON {label} — an exclusion records a "
                    f"deliberate non-review and must carry a note saying why"
                )
                continue
        if kind == "defer" and not str(d.get("note") or "").strip():
            errors.append(f"DEFER WITHOUT REASON {label} — a deferral must carry a note")
            continue
        if kind == "cover":
            if not str(d.get("verdict") or "").strip():
                errors.append(f"COVER WITHOUT VERDICT {label} — a covered row records a verdict")
                continue
            try:
                date.fromisoformat(str(d.get("reviewed") or ""))
            except ValueError:
                errors.append(
                    f"INVALID DATE {label} — cover demands reviewed=YYYY-MM-DD "
                    f"(the row's freshness hangs on it), got {d.get('reviewed')!r}"
                )
                continue

        live = routes.get(surface)
        row: dict = {
            "scope": (open_pairs[pair].get("scope") if pair in open_pairs else scope)
            or DEFAULT_SCOPE,
            "surface": surface,
            "kind": "route" if live else "other",
            "attack_class": cls,
            "reviewed": d.get("reviewed") or date.today().isoformat(),
            "method": d.get("review_method") or "source",
        }
        # Stamp the live fingerprint for every decision kind whose surface
        # is in the inventory: an exclusion or deferral names the handler
        # state it was decided against, and a row without a fingerprint is
        # born STALE.
        if live:
            row["fingerprint"] = d.get("fingerprint") or live["fingerprint"]
        if kind == "exclude":
            row["status"] = d["status"]
            row["note"] = d["note"]
        elif kind == "defer":
            row["status"] = "deferred"
            row["note"] = d["note"]
        else:  # cover
            row["verdict"] = d["verdict"]
        accepted.append((d, row))

    for d, row in accepted:
        what = {
            "cover": "would-cover",
            "exclude": "would-exclude",
            "defer": "would-defer",
            "amend": "would-amend",
        }[d["decision"]]
        detail = row.get("note") or row.get("verdict") or ""
        print(
            f"  {what} {row['attack_class']} @ {row['surface']}"
            + (f" — {detail}" if detail else "")
        )
    for label in bad_pairs:
        print(f"  bad-pair {label} — nothing written; correct app.py or the inventory")

    counts = f"{len(accepted)} decision(s) valid, {len(bad_pairs)} bad_pair, {len(errors)} error(s)"
    if not write:
        print(f"apply: dry-run — {counts}; pass --write to commit")
        for e in errors:
            print(f"FAIL: {e}")
        return 1 if errors else 0

    if errors:
        print(f"apply: refused --write — {counts}")

        for e in errors:
            print(f"FAIL: {e}")
        return 1
    data = _load(ledger_path)
    if not isinstance(data, (list, dict)):
        sys.exit(f"ledger must be a JSON array or an object with a rows array: {ledger_path}")
    # amend rows REPLACE their existing row (the re-review path); every
    # other decision kind appends a new row.
    amend_by_pair = {
        (d.get("surface"), d.get("attack_class")): row
        for d, row in accepted
        if d["decision"] == "amend"
    }
    appended = [row for d, row in accepted if d["decision"] != "amend"]
    next_rows: list[dict] = []
    for r in ledger_rows:
        key = (r.get("surface"), r.get("attack_class"))
        next_rows.append(amend_by_pair.pop(key) if key in amend_by_pair else r)
    next_rows.extend(appended)
    payload = {**data, "rows": next_rows} if isinstance(data, dict) else next_rows
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    print(f"apply: --write — {counts}; ledger now {len(next_rows)} row(s)")
    print("next: coverage_ledger.py critic — the pairs closed here should have left the queue")
    return 0


def seed(app_path: Path, ledger_path: Path) -> int:
    """Recompute fingerprints for existing rows and write them back.

    Run after a deliberate re-review whose verdict did not change but
    whose handler source did (formatting, refactor). Verdicts are curated
    by hand — this never invents coverage.
    """
    routes = route_inventory(app_path)
    rows = load_ledger(ledger_path)
    updated = 0
    for r in rows:
        live = routes.get(r["surface"])
        if live and r.get("fingerprint") != live["fingerprint"]:
            r["fingerprint"] = live["fingerprint"]
            updated += 1
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
    print(f"seed: updated {updated} fingerprints; {len(rows)} rows")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["check", "critic", "queue", "apply", "plan", "seed"])
    ap.add_argument("--app", type=Path, default=DEFAULT_APP)
    ap.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    ap.add_argument(
        "--out", type=Path, default=DEFAULT_QUEUE, help="queue: where the work queue is written"
    )
    ap.add_argument(
        "--decisions",
        type=Path,
        default=DEFAULT_DECISIONS,
        help="apply: the decisions journal to apply (dry-run; --write commits)",
    )
    ap.add_argument(
        "--strict", action="store_true", help="stale rows fail; critic verdict must be COMPLETE"
    )
    ap.add_argument(
        "--write", action="store_true", help="apply: commit the decisions to the ledger"
    )
    ap.add_argument("--recent", type=int, default=5, help="history entries to show in plan")
    ap.add_argument(
        "--scope",
        choices=SCOPE_ENUM,
        help="restrict check/critic/queue/apply/plan to one surface family "
        "(repo-wide coverage is per-scope)",
    )
    ap.add_argument(
        "--findings",
        type=Path,
        help="critic: a run's findings file (array or envelope) to cross-check confirmed claims",
    )
    ap.add_argument("--json", action="store_true", dest="as_json", help="critic: emit JSON")
    ap.add_argument(
        "--exclude-tripwire",
        type=float,
        help="critic: warn on stderr when the exclusion share crosses this percent",
    )
    a = ap.parse_args(argv)
    if a.mode == "check":
        return check(a.app, a.ledger, strict=a.strict, scope=a.scope)
    if a.mode == "critic":
        return critic(
            a.app,
            a.ledger,
            scope=a.scope,
            findings_path=a.findings,
            as_json=a.as_json,
            strict=a.strict,
            exclude_tripwire=a.exclude_tripwire,
        )
    if a.mode == "queue":
        return queue(a.app, a.ledger, scope=a.scope, out=a.out)
    if a.mode == "apply":
        return apply(a.app, a.ledger, a.decisions, write=a.write, scope=a.scope)
    if a.mode == "plan":
        return plan(a.app, a.ledger, recent=a.recent, scope=a.scope)
    return seed(a.app, a.ledger)


if __name__ == "__main__":
    raise SystemExit(main())
