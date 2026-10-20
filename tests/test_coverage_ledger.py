"""Tests for helpers/validators/coverage_ledger.py — the coverage gate.

The ledger is the cumulative-coverage record: one row per (surface,
attack class). Before the A1/A2/A3 port the completeness invariant was
PER SURFACE — one row per route was enough, so a route reviewed only for
injection looked as covered as one reviewed for every class. The status
enum and the `not_applicable`/`out_of_scope` values close that gap:
a class with no row at all is now visible.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from helpers.validators import coverage_ledger as cl  # noqa: E402

APP = """
from flask import Flask
app = Flask(__name__)


@app.route("/api/thing", methods=["POST"])
def thing():
    return "thing"


@app.route("/api/other")
def other():
    return "other"
"""


@pytest.fixture
def app(tmp_path):
    path = tmp_path / "app.py"
    path.write_text(APP)
    return path


def _row(surface="/api/thing", **over):
    row = {
        "surface": surface,
        "kind": "route",
        "attack_class": "injection",
        "reviewed": "2026-10-20",
        "method": "source+live",
        "verdict": "clean",
        "fingerprint": "deadbeefdeadbeef",
        "status": "covered",
    }
    row.update(over)
    return row


def _write(tmp_path, rows):
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(rows, indent=1))
    return path


def _run(app, path, strict=False):
    """Call cl.check() and capture its stdout."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cl.check(app, path, strict=strict)
    return code, buf.getvalue()


# ---------------------------------------------------------------------------
# the status enum (A1)
# ---------------------------------------------------------------------------


def test_a_status_outside_the_enum_fails(app, tmp_path):
    path = _write(tmp_path, [_row(status="probably-fine")])
    code, out = _run(app, path)
    assert code == 1
    assert "INVALID STATUS" in out


@pytest.mark.parametrize("status", cl.STATUS_ENUM)
def test_every_enum_value_is_accepted(app, tmp_path, status):
    path = _write(tmp_path, [_row(status=status, blocked_reason="x", unresolved="y")])
    _, out = _run(app, path)
    assert "INVALID STATUS" not in out


# ---------------------------------------------------------------------------
# blocked / candidate need their fields (A3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["blocked", "candidate"])
def test_blocked_and_candidate_need_a_reason_and_an_unresolved_fact(app, tmp_path, status):
    path = _write(tmp_path, [_row(status=status)])
    code, out = _run(app, path)
    assert code == 1
    assert "MISSING blocked_reason" in out
    assert "MISSING unresolved" in out


@pytest.mark.parametrize("status", ["blocked", "candidate"])
def test_blocked_and_candidate_pass_with_both_fields(app, tmp_path, status):
    path = _write(
        tmp_path,
        [
            _row(
                status=status,
                blocked_reason="the handler calls a vendored SDK",
                unresolved="the SDK version",
            )
        ],
    )
    _, out = _run(app, path)
    assert "MISSING blocked_reason" not in out
    assert "MISSING unresolved" not in out


# ---------------------------------------------------------------------------
# the per-surface attack-class accounting (A2)
# ---------------------------------------------------------------------------


def test_a_class_with_no_row_at_all_is_warned(app, tmp_path):
    """The gap the port exists for: a route reviewed only for injection
    looked fully covered before.

    The class must be known to the ledger (it appears on some other
    surface) for the gap to be visible — the rule compares each surface's
    class set against the classes the ledger knows about.
    """
    path = _write(
        tmp_path,
        [
            _row(surface="/api/thing", attack_class="injection"),
            _row(surface="/api/other", attack_class="availability"),
        ],
    )
    _, out = _run(app, path)
    assert "UNREVIEWED CLASS /api/thing / availability" in out
    assert "UNREVIEWED CLASS /api/other / injection" in out


def test_a_class_marked_not_applicable_is_not_warned(app, tmp_path):
    path = _write(
        tmp_path,
        [
            _row(attack_class="injection"),
            _row(
                attack_class="availability",
                status="not_applicable",
                note="pure JSON read, no file or subprocess sink",
            ),
        ],
    )
    _, out = _run(app, path)
    assert "UNREVIEWED CLASS /api/thing / availability" not in out


def test_a_class_marked_out_of_scope_is_not_warned(app, tmp_path):
    path = _write(
        tmp_path,
        [
            _row(attack_class="injection"),
            _row(
                attack_class="availability",
                status="out_of_scope",
                note="handled by the ingest worker, not this route",
            ),
        ],
    )
    _, out = _run(app, path)
    assert "UNREVIEWED CLASS /api/thing / availability" not in out


def test_an_exclusion_without_a_reason_is_an_error(app, tmp_path):
    """A2: an unexplained exclusion is indistinguishable from a forgotten one."""
    path = _write(
        tmp_path,
        [
            _row(attack_class="injection"),
            _row(attack_class="availability", status="not_applicable"),
        ],
    )
    code, out = _run(app, path)
    assert code == 1
    assert "EXCLUSION WITHOUT REASON /api/thing / availability" in out


def test_a_class_known_only_in_another_scope_is_not_demanded(app, tmp_path):
    """A2: the required class set is per scope, so app's classes stay in app."""
    path = _write(
        tmp_path,
        [
            _row(surface="/api/thing", attack_class="injection"),
            _row(surface="/api/thing", attack_class="availability"),
            _row(surface="/api/thing", attack_class="web-protocol/auth"),
            _row(surface="/api/thing", attack_class="concurrency"),
            _row(surface="/api/other", attack_class="injection"),
            _row(surface="/api/other", attack_class="availability"),
            _row(surface="/api/other", attack_class="web-protocol/auth"),
            _row(surface="/api/other", attack_class="concurrency"),
            _row(
                surface="helpers/pdf/parse.py",
                kind="ingestion",
                attack_class="availability",
                scope="extractors",
            ),
            _row(
                surface="helpers/pdf/parse.py",
                kind="ingestion",
                attack_class="parser-input",
                scope="extractors",
            ),
        ],
    )
    _, out = _run(app, path)
    assert "UNREVIEWED CLASS" not in out


def test_an_exclusion_in_one_scope_does_not_excuse_another(app, tmp_path):
    """A2: app excluding availability must not suppress an extractors gap."""
    path = _write(
        tmp_path,
        [
            _row(surface="/api/thing", attack_class="injection"),
            _row(surface="/api/other", attack_class="injection"),
            # the declared taxonomy (A10) also demands web-protocol/auth and
            # concurrency in app; answer them so the scope test stays about
            # the availability exclusion crossing families.
            _row(surface="/api/thing", attack_class="web-protocol/auth"),
            _row(surface="/api/thing", attack_class="concurrency"),
            _row(surface="/api/other", attack_class="web-protocol/auth"),
            _row(surface="/api/other", attack_class="concurrency"),
            _row(
                surface="/api/thing",
                attack_class="availability",
                status="not_applicable",
                note="pure JSON read, no file or subprocess sink",
            ),
            _row(
                surface="/api/other",
                attack_class="availability",
                status="not_applicable",
                note="pure JSON read, no file or subprocess sink",
            ),
            _row(
                surface="helpers/pdf/a.py",
                kind="ingestion",
                attack_class="availability",
                scope="extractors",
            ),
            _row(
                surface="helpers/pdf/a.py",
                kind="ingestion",
                attack_class="injection",
                scope="extractors",
            ),
            _row(
                surface="helpers/pdf/a.py",
                kind="ingestion",
                attack_class="parser-input",
                scope="extractors",
            ),
            _row(
                surface="helpers/pdf/b.py",
                kind="ingestion",
                attack_class="availability",
                scope="extractors",
            ),
            _row(
                surface="helpers/pdf/b.py",
                kind="ingestion",
                attack_class="injection",
                scope="extractors",
            ),
            _row(
                surface="helpers/pdf/c.py",
                kind="ingestion",
                attack_class="injection",
                scope="extractors",
            ),
        ],
    )
    _, out = _run(app, path)
    assert "UNREVIEWED CLASS /api/thing" not in out
    assert "UNREVIEWED CLASS helpers/pdf/a.py" not in out
    assert "UNREVIEWED CLASS helpers/pdf/c.py / availability (scope=extractors)" in out


def test_strict_promotes_unreviewed_class_to_an_error(app, tmp_path):
    path = _write(
        tmp_path,
        [
            _row(surface="/api/thing", attack_class="injection"),
            _row(surface="/api/other", attack_class="availability"),
        ],
    )
    code, out = _run(app, path, strict=True)
    assert code == 1
    assert "UNREVIEWED CLASS /api/thing / availability" in out


def test_plan_lists_exclusions_with_their_reason(app, tmp_path):
    path = _write(
        tmp_path,
        [
            _row(attack_class="injection"),
            _row(attack_class="availability", status="not_applicable", note="pure JSON read"),
        ],
    )
    _, out = _plan(app, path)
    assert "EXCLUDED availability @ /api/thing — status=not_applicable: pure JSON read" in out


# ---------------------------------------------------------------------------
# the pre-existing contract still holds
# ---------------------------------------------------------------------------


def test_a_route_with_no_row_is_unreviewed(app, tmp_path):
    path = _write(tmp_path, [_row(surface="/")])
    code, out = _run(app, path)
    assert code == 1
    assert "UNREVIEWED /api/thing" in out


def test_a_row_for_a_vanished_route_is_orphaned(app, tmp_path):
    path = _write(tmp_path, [_row(surface="/api/gone")])
    code, out = _run(app, path)
    assert code == 1
    assert "ORPHANED row for /api/gone" in out


def test_a_stale_fingerprint_warns_but_does_not_fail(app, tmp_path):
    path = _write(tmp_path, [_row(surface=s) for s in ("/api/thing", "/api/other")])
    code, out = _run(app, path)
    assert code == 0
    assert "STALE /api/thing / injection" in out


def test_a_stale_fingerprint_fails_under_strict(app, tmp_path):
    path = _write(tmp_path, [_row(surface=s) for s in ("/api/thing", "/api/other")])
    code, out = _run(app, path, strict=True)
    assert code == 1
    assert "STALE /api/thing / injection" in out


def test_the_fingerprint_is_recomputed_by_seed(app, tmp_path):
    path = _write(tmp_path, [_row()])
    cl.seed(app, path)
    rows = json.loads(path.read_text())
    live = cl.route_inventory(app)["/api/thing"]["fingerprint"]
    assert rows[0]["fingerprint"] == live


# ---------------------------------------------------------------------------
# A9 — the ledger as review input (`plan`) and the miniature history record
# ---------------------------------------------------------------------------


def _plan(app, path, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cl.plan(app, path, **kw)
    return code, buf.getvalue()


def test_plan_is_advisory_even_when_the_ledger_is_incomplete(app, tmp_path):
    path = _write(tmp_path, [_row(surface="/")])
    code, out = _plan(app, path)
    assert code == 0
    assert "GAP injection @ /api/thing — no ledger row" in out
    assert "GAP injection @ /api/other — no ledger row" in out


def test_plan_lists_open_rows_with_their_reason(app, tmp_path):
    path = _write(
        tmp_path,
        [
            _row(
                surface="/api/thing",
                status="blocked",
                blocked_reason="no auth harness",
                unresolved="whether /api/thing is auth-gated",
            ),
            _row(surface="/api/other"),
        ],
    )
    _, out = _plan(app, path)
    assert "OPEN injection @ /api/thing — status=blocked: no auth harness" in out
    assert "unresolved: whether /api/thing is auth-gated" in out
    assert "1 open row(s)" in out


def test_plan_reports_stale_rows(app, tmp_path):
    path = _write(tmp_path, [_row(surface="/api/thing"), _row(surface="/api/other")])
    _, out = _plan(app, path)
    assert "STALE injection @ /api/thing — handler changed since 2026-10-20" in out
    assert "2 stale" in out


def test_plan_reads_the_history_array(app, tmp_path):
    rows = [_row(surface="/api/thing"), _row(surface="/api/other")]
    path = tmp_path / "ledger.json"
    path.write_text(
        json.dumps(
            {
                "rows": rows,
                "history": [
                    {"date": "2026-10-18", "scope": "app.py routes", "summary": "49 rows"},
                    {"date": "2026-10-20", "scope": "cloudflare arc", "summary": "A1-A4"},
                ],
            },
            indent=1,
        )
    )
    code, out = _plan(app, path, recent=1)
    assert code == 0
    assert "history: 2 prior run(s); showing 1" in out
    assert "2026-10-20 cloudflare arc — A1-A4" in out
    assert "2026-10-18" not in out


def test_plan_on_a_bare_array_reports_no_history(app, tmp_path):
    path = _write(tmp_path, [_row(surface="/api/thing"), _row(surface="/api/other")])
    _, out = _plan(app, path)
    assert "history: none recorded" in out


def test_plan_on_a_missing_ledger_is_empty_but_advisory(app, tmp_path):
    code, out = _plan(app, tmp_path / "absent.json")
    assert code == 0
    assert "prior: 0 rows over 0 surfaces" in out
    assert "history: none recorded" in out


def test_check_accepts_the_object_form(app, tmp_path):
    rows = [_row(surface="/api/thing"), _row(surface="/api/other")]
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({"rows": rows, "history": []}, indent=1))
    code, out = _run(app, path)
    assert code == 0
    assert "ok: ledger complete" in out


# ---------------------------------------------------------------------------
# scope (Q1/Q4): repo-wide coverage is only meaningful per surface family
def test_invalid_scope_is_an_error(app, tmp_path):
    path = _write(tmp_path, [_row(scope="nope"), _row(surface="/api/other")])
    code, out = _run(app, path)
    assert code == 1
    assert "INVALID SCOPE /api/thing / injection" in out


def test_scope_filter_narrows_the_plan_to_one_family(app, tmp_path):
    path = _write(
        tmp_path,
        [
            _row(scope="app"),
            _row(surface="/api/other", scope="app"),
            _row(
                surface="helpers/pdf/parse.py",
                kind="ingestion",
                attack_class="availability",
                scope="extractors",
                verdict="note:low",
            ),
        ],
    )
    code, out = _plan(app, path, scope="extractors")
    assert code == 0
    assert "prior: 1 rows over 1 surfaces reviewed 2026-10-20; scope=extractors" in out
    # No route inventory exists outside the app scope, so a non-app scope
    # must not inherit the Flask app's 2-route queue. The extractors
    # scope DOES have an inventory (security_scopes.py): its three areas
    # are the surfaces this scope demands, and the fixture's row names a
    # surface the inventory does not, so all three areas are open.
    assert "/api/thing" not in out
    assert "work queue: 3 uncovered pair(s)" in out


def test_plan_tallies_scopes_and_keeps_the_route_queue_for_app(app, tmp_path):
    path = _write(tmp_path, [_row(scope="app")])
    code, out = _plan(app, path)
    assert "scopes: app=1" in out
    assert "GAP injection @ /api/other — no ledger row" in out


def test_scope_defaults_to_app_for_legacy_rows(app, tmp_path):
    path = _write(tmp_path, [_row(surface="/api/thing"), _row(surface="/api/other")])
    code, out = _plan(app, path, scope="app")
    assert code == 0
    assert (
        "prior: 2 rows over 2 surfaces (2 routes in app.py); reviewed 2026-10-20; scope=app" in out
    )
    _, out = _plan(app, path, scope="graph")
    assert "prior: 0 rows over 0 surfaces reviewed no review dates; scope=graph" in out


def _run_scoped(app, path, scope, strict=True):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cl.check(app, path, strict=strict, scope=scope)
    return code, buf.getvalue()


def test_scoped_check_hides_other_families_routes(app, tmp_path):
    """`check --scope extractors` must not demand the Flask route inventory."""
    path = _write(
        tmp_path,
        [
            _row(surface="/extract", kind="ingestion", scope="extractors"),
            _row(
                surface="/extract",
                kind="ingestion",
                attack_class="availability",
                scope="extractors",
            ),
            _row(
                surface="/extract",
                kind="ingestion",
                attack_class="parser-input",
                scope="extractors",
            ),
            _row(surface="/api/thing", scope="app"),
        ],
    )
    code, out = _run_scoped(app, path, "extractors")
    assert code == 0, out
    assert "scope: extractors — 1 surface(s), 3 row(s)" in out
    assert "/api/thing" not in out
    assert "coverage:" not in out


def test_scoped_check_still_demands_routes_for_app(app, tmp_path):
    path = _write(tmp_path, [_row(surface="/extract", kind="ingestion", scope="extractors")])
    code, out = _run_scoped(app, path, "app")
    assert code == 1
    assert "UNREVIEWED /api/thing" in out
    assert "scope: app — 0 surface(s), 0 row(s)" in out


# --- A7: the coverage critic -------------------------------------------------


def _critic(app, path, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cl.critic(app, path, **kw)
    return code, buf.getvalue()


def _covered_row(app, surface, attack_class, **over):
    """A row whose fingerprint matches the live route, so only coverage drifts."""
    fp = cl.route_inventory(app)[surface]["fingerprint"]
    return _row(surface=surface, attack_class=attack_class, fingerprint=fp, **over)


def test_critic_lists_pairs_with_no_row_and_no_exclusion(app, tmp_path):
    path = _write(
        tmp_path,
        [
            _covered_row(app, "/api/thing", "injection"),
            _row(
                surface="/api/other",
                attack_class="availability",
                status="not_applicable",
                note="no query work",
            ),
        ],
    )
    code, out = _critic(app, path)
    assert code == 0
    assert "verdict: INCOMPLETE" in out
    assert "GAP availability @ /api/thing — no row and no exclusion" in out
    assert "GAP injection @ /api/other — no row and no exclusion" in out
    assert "GAP availability @ /api/other" not in out
    assert "EXCLUDED availability @ /api/other — status=not_applicable: no query work" in out


def test_critic_reports_one_scope_without_inheriting_anothers_routes(app, tmp_path):
    path = _write(
        tmp_path,
        [
            _covered_row(app, "/api/thing", "injection"),
            _row(surface="/api/thing", attack_class="availability", scope="graph"),
        ],
    )
    code, out = _critic(app, path, scope="graph")
    assert code == 0
    # The graph scope has a scope inventory (security_scopes.py): three
    # hand-enumerated areas, none of which the app's routes leak into.
    assert "critic: graph — 4 surface(s), 1 row(s), 2 required class(es)" in out
    # The inventory caveat is a CAVEAT, not a blocker: it bounds the
    # verdict (a hand-enumerated family) without making the scope
    # permanently INCOMPLETE.
    assert "caveat: surfaces for scope graph come from the scope inventory (3 area(s))" in out
    assert "note: surfaces for scope graph" not in out
    assert "/api/other" not in out


def test_critic_flags_a_drifted_fingerprint_as_stale(app, tmp_path):
    path = _write(
        tmp_path,
        [
            _row(surface="/api/thing", attack_class="injection"),  # deadbeef -> stale
            _covered_row(app, "/api/other", "injection"),
            # declared taxonomy: answer the other three app classes on both
            # surfaces so the test isolates the one stale row.
            _covered_row(app, "/api/thing", "availability"),
            _covered_row(app, "/api/other", "availability"),
            _covered_row(app, "/api/thing", "web-protocol/auth"),
            _covered_row(app, "/api/other", "web-protocol/auth"),
            _covered_row(app, "/api/thing", "concurrency"),
            _covered_row(app, "/api/other", "concurrency"),
        ],
    )
    code, out = _critic(app, path)
    assert code == 0
    assert "STALE injection @ /api/thing — reviewed 2026-10-20" in out
    assert "verdict: INCOMPLETE — 0 uncovered pair(s), 1 stale, 0 orphaned" in out
    assert _critic(app, path, strict=True)[0] == 1


def test_critic_reports_a_vanished_route_as_orphaned(app, tmp_path):
    path = _write(
        tmp_path,
        [
            _covered_row(app, "/api/thing", "injection"),
            _row(surface="/api/gone", attack_class="injection"),
        ],
    )
    code, out = _critic(app, path)
    assert code == 0
    assert "ORPHANED injection @ /api/gone — not in app.py" in out


def test_critic_flags_a_confirmed_claim_the_ledger_does_not_back(app, tmp_path):
    ledger = _write(tmp_path, [_covered_row(app, "/api/thing", "injection")])
    findings = tmp_path / "2026-10-21-app-1.json"
    findings.write_text(
        json.dumps(
            {
                "run": "2026-10-21-app-1",
                "scope": ["app"],
                "findings": [
                    {
                        "surface": "/api/thing",
                        "attack_class": "availability",
                        "verdict": "confirmed",
                    }
                ],
            }
        )
    )
    code, out = _critic(app, ledger, findings_path=findings)
    assert code == 0
    assert "UNBACKED availability @ /api/thing" in out
    assert "verdict: INCOMPLETE" in out


def test_critic_verdict_is_complete_when_every_pair_is_accounted(app, tmp_path):
    path = _write(
        tmp_path,
        [
            _covered_row(app, "/api/thing", "injection"),
            _covered_row(app, "/api/other", "injection"),
            _covered_row(app, "/api/thing", "availability"),
            _covered_row(app, "/api/other", "availability"),
            _covered_row(app, "/api/thing", "web-protocol/auth"),
            _covered_row(app, "/api/other", "web-protocol/auth"),
            _covered_row(app, "/api/thing", "concurrency"),
            _covered_row(app, "/api/other", "concurrency"),
        ],
    )
    code, out = _critic(app, path)
    assert code == 0, out
    assert "verdict: COMPLETE — 0 uncovered pair(s), 0 stale, 0 orphaned" in out
    assert _critic(app, path, strict=True)[0] == 0


def test_critic_json_is_machine_readable(app, tmp_path):
    path = _write(tmp_path, [_covered_row(app, "/api/thing", "injection")])
    code, out = _critic(app, path, as_json=True)
    assert code == 0
    payload = json.loads(out)
    assert payload["schema"] == "coverage-critic.v1"
    assert payload["scope"] == "app"
    assert payload["verdict"] == "INCOMPLETE"
    # 4 declared classes x 2 surfaces - the one accounted thing/injection.
    assert payload["counts"]["uncovered"] == 7
    assert payload["uncovered"][0]["surface"] == "/api/other"


# ---------------------------------------------------------------------------
# the coverage work queue (taxonomy arc: the triage mechanism's idiom)
# ---------------------------------------------------------------------------

APP2 = """
from flask import Flask
app = Flask(__name__)


@app.route("/api/mut", methods=["POST"])
def mut():
    return "mut"


@app.route("/api/get")
def get():
    return "get"
"""


@pytest.fixture
def app2(tmp_path):
    path = tmp_path / "app2.py"
    path.write_text(APP2)
    return path


def _queue(app_path, ledger_path, scope="app", out=None):
    """Call cl.queue() and return (code, stdout, parsed-queue-lines)."""
    out = out or (app_path.parent / "coverage_queue.jsonl")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cl.queue(app_path, ledger_path, scope=scope, out=out)
    lines = [json.loads(line) for line in out.read_text().splitlines()] if out.exists() else []
    return code, buf.getvalue(), lines


def _decisions(tmp_path, *records):
    path = tmp_path / "decisions.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n")
    return path


def _apply(app_path, ledger_path, decisions_path, **over):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cl.apply(app_path, ledger_path, decisions_path, **over)
    return code, buf.getvalue()


def test_queue_records_each_reason_and_the_histogram_sums(app2, tmp_path):
    """Rows carry a reason; the printed histogram parts sum to the length."""
    path = _write(
        tmp_path,
        [
            # `injection` is covered on the POST route only — the GET route
            # and the two ledger-only surfaces must answer for the class.
            _row(surface="/api/mut", attack_class="injection"),
            # availability: excluded twice with the same note on surfaces
            # outside the inventory -> exclusion_pattern precedence.
            _row(
                surface="S1/x",
                kind="other",
                attack_class="availability",
                verdict=None,
                status="not_applicable",
                note="No stdlib surface here",
            ),
            _row(
                surface="S2/y",
                kind="other",
                attack_class="availability",
                verdict=None,
                status="out_of_scope",
                note="no STDLIB surface HERE",
            ),
        ],
    )
    code, out, lines = _queue(app2, path)
    assert code == 0
    assert f"queue: app — {len(lines)} gap(s); reasons: " in out
    assert (
        "exclusion_pattern=2 not_a_route=6 mutating_route=2 read_only_route=1 query_arg_sink=2"
        in out
    )
    by_pair = {(r["surface"], r["attack_class"]): r for r in lines}
    assert by_pair[("/api/get", "injection")]["reason"] == "query_arg_sink"
    assert by_pair[("/api/get", "web-protocol/auth")]["reason"] == "query_arg_sink"
    # the class-aware fork: concurrency on a GET route is a closed exclusion
    assert by_pair[("/api/get", "concurrency")]["reason"] == "read_only_route"
    assert by_pair[("/api/get", "injection")]["methods"] == ["GET"]
    assert by_pair[("/api/mut", "availability")]["reason"] == "exclusion_pattern"
    assert by_pair[("/api/mut", "availability")]["exemplar_note"] == "no stdlib surface here"
    assert by_pair[("/api/mut", "availability")]["siblings"] == 2
    assert by_pair[("S1/x", "injection")]["reason"] == "not_a_route"
    # facts ride with the reason: routes in the inventory carry their HTTP
    # methods only when the reason is a route fact (query_arg_sink is one
    # too — the pair stays demanded because the GET handler still reads
    # query arguments).
    assert all(
        ("methods" in r) == (r["reason"] in ("mutating_route", "read_only_route", "query_arg_sink"))
        for r in lines
    )
    # the class-aware fork: on the same GET-only route the terminal class
    # closes as a route fact while the query-reachable class stays demanded.
    assert by_pair[("/api/get", "web-protocol/auth")]["reason"] == "query_arg_sink"
    assert by_pair[("/api/get", "concurrency")]["reason"] == "read_only_route"


def test_queue_regenerates_wholesale_and_unlinks_an_empty_queue(app2, tmp_path):
    path = _write(
        tmp_path,
        [
            _row(surface="/api/mut", attack_class="injection"),
            _row(surface="/api/mut", attack_class="availability"),
            _row(surface="/api/mut", attack_class="web-protocol/auth"),
            _row(surface="/api/mut", attack_class="concurrency"),
            _row(surface="/api/get", attack_class="injection"),
            _row(surface="/api/get", attack_class="availability"),
            _row(surface="/api/get", attack_class="web-protocol/auth"),
            _row(surface="/api/get", attack_class="concurrency"),
        ],
    )
    out = tmp_path / "q.jsonl"
    out.write_text("stale content\n")
    code, out_text, lines = _queue(app2, path, out=out)
    assert code == 0
    assert "0 gap(s)" in out_text
    assert not out.exists()  # wholesale: even the stale sidecar is unlinked
    assert lines == []


def test_apply_dry_run_changes_nothing_but_reports(app, tmp_path):
    decision = _decisions(
        tmp_path,
        {
            "surface": "/api/other",
            "attack_class": "injection",
            "decision": "cover",
            "verdict": "clean",
            "reviewed": "2026-10-21",
        },
    )
    path = _write(tmp_path, [_row(surface="/api/other", attack_class="injection")])  # accounted
    code, out = _apply(app, path, decision)
    # Only the decisions that name a gap survive; an already-covered pair
    # is refused, and nothing reaches disk on a dry run.
    assert code == 1
    assert "ALREADY ACCOUNTED /api/other / injection" in out
    assert "dry-run" in out
    assert json.loads(path.read_text()) == [_row(surface="/api/other", attack_class="injection")]


def test_apply_closes_only_pairs_the_queue_still_names(app, tmp_path):
    decision = _decisions(
        tmp_path,
        {
            "surface": "/api/never",
            "attack_class": "injection",
            "decision": "cover",
            "verdict": "clean",
            "reviewed": "2026-10-21",
        },
    )
    path = _write(tmp_path, [_row()])
    code, out = _apply(app, path, decision, write=True)
    assert code == 1
    assert "NOT A QUEUE ROW /api/never / injection" in out
    assert json.loads(path.read_text()) == [_row()]  # the refused --write wrote nothing


def test_apply_demands_the_fields_each_decision_kind_names(app, tmp_path):
    decisions = _decisions(
        tmp_path,
        {"surface": "/api/other", "attack_class": "injection", "decision": "exclude"},
        {
            "surface": "/api/other",
            "attack_class": "injection",
            "decision": "exclude",
            "status": "covered",
            "note": "seen once, no finding",
        },
        {
            "surface": "/api/other",
            "attack_class": "injection",
            "decision": "exclude",
            "status": "not_applicable",
        },
        {"surface": "/api/other", "attack_class": "injection", "decision": "defer"},
        {"surface": "/api/other", "attack_class": "injection", "decision": "cover"},
        {
            "surface": "/api/other",
            "attack_class": "injection",
            "decision": "cover",
            "verdict": "clean",
            "reviewed": "20-20-20-20",
        },
    )
    # Seed genuine gaps so the pairs are queue rows at all: availability
    # and concurrency rows turn the class set into a three-class demand.
    ledger = _write(
        tmp_path,
        [
            _row(),
            _row(attack_class="availability"),
            _row(attack_class="concurrency"),
        ],
    )
    code, out = _apply(app, ledger, decisions, scope="app")
    assert code == 1
    assert "EXCLUSION WITHOUT REASON /api/other / injection" in out
    assert "INVALID STATUS /api/other / injection" in out
    assert "DEFER WITHOUT REASON /api/other / injection" in out
    assert "COVER WITHOUT VERDICT /api/other / injection" in out
    assert "INVALID DATE /api/other / injection" in out


def test_apply_writes_fresh_rows_and_the_pairs_leave_the_queue(app, tmp_path):
    ledger = _write(
        tmp_path,
        [
            _row(),  # /api/thing injection (POST route in APP)
            _row(attack_class="availability"),
            _row(attack_class="concurrency"),
            _row(attack_class="web-protocol/auth"),
        ],
    )
    decisions = _decisions(
        tmp_path,
        {
            "surface": "/api/other",
            "attack_class": "injection",
            "decision": "cover",
            "verdict": "clean",
            "reviewed": "2026-10-21",
        },
        {
            "surface": "/api/other",
            "attack_class": "availability",
            "decision": "exclude",
            "status": "not_applicable",
            "note": "read-only GET; no input to filter",
        },
        {
            "surface": "/api/other",
            "attack_class": "concurrency",
            "decision": "defer",
            "note": "blocked until the Phase 4 deploy gate",
        },
        {
            "surface": "/api/other",
            "attack_class": "web-protocol/auth",
            "decision": "cover",
            "verdict": "clean",
            "reviewed": "2026-10-21",
        },
    )

    live_fp = cl.route_inventory(app)["/api/other"]["fingerprint"]
    code, out = _apply(app, ledger, decisions, write=True, scope="app")
    assert code == 0, out
    assert (
        "apply: --write — 4 decision(s) valid, 0 bad_pair, 0 error(s); ledger now 8 row(s)" in out
    )
    rows = json.loads(ledger.read_text())
    fresh = [r for r in rows if r["surface"] == "/api/other"]
    assert len(fresh) == 4
    assert all(r["fingerprint"] == live_fp for r in fresh)  # every kind stamps the handler state
    covered = next(r for r in fresh if r["attack_class"] == "injection")
    assert covered["verdict"] == "clean" and covered["reviewed"] == "2026-10-21"
    assert (
        next(r for r in fresh if r["attack_class"] == "availability")["status"] == "not_applicable"
    )
    assert next(r for r in fresh if r["attack_class"] == "concurrency")["status"] == "deferred"
    # The closed pairs left the queue; the applied rows are born fresh —
    # the four stale rows here are the ledger's pre-existing deadbeef
    # fingerprints on /api/thing, not the applied ones. One exclusion of
    # seven accounted pairs (six covered + the exclusion; the deferred
    # row is neither) -> a 14.3% exclusion share.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cl.critic(app, ledger, scope="app")
    critic_out = buf.getvalue()
    assert "0 uncovered pair(s)" in critic_out and "exclusion share: 14.3%" in critic_out
    assert not [
        line for line in critic_out.splitlines() if "STALE" in line and "/api/other" in line
    ]
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        cl.check(app, ledger, scope="app", strict=False)
    check_out = buf2.getvalue()
    assert "FAIL" not in check_out
    assert not [line for line in check_out.splitlines() if "STALE /api/other" in line]


def test_apply_bad_pair_is_echoed_never_applied(app, tmp_path):
    # The pair must be an OPEN one (bad_pair means "this pair should not
    # exist"); a pair the ledger already accounts for is refused instead.
    ledger = _write(
        tmp_path,
        [
            # an availability row makes availability required; the injection
            # rows keep injection required, leaving injection@singleton OPEN.
            _row(
                surface="shared singleton",
                kind="other",
                attack_class="availability",
                verdict=None,
                status=None,
            ),
            _row(),
        ],
    )
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cl.queue(app, ledger, scope="app", out=tmp_path / "q.jsonl")  # the pair needs rows
    decision = _decisions(
        tmp_path,
        {"surface": "shared singleton", "attack_class": "injection", "decision": "bad_pair"},
    )
    before = ledger.read_text()
    code, out = _apply(app, ledger, decision, write=True, scope="app")
    assert code == 0
    assert (
        "bad-pair shared singleton / injection — nothing written; correct app.py or the inventory"
        in out
    )
    assert json.loads(ledger.read_text()) == json.loads(
        before
    )  # even with --write, a bad_pair touches nothing


def test_critic_trips_the_exclusion_tripwire_and_says_the_share(app, tmp_path):
    rows = [
        _row(),  # covered
        _row(attack_class="availability", verdict=None, status="not_applicable", note="a"),
        _row(attack_class="concurrency", verdict=None, status="out_of_scope", note="b"),
    ]
    path = _write(tmp_path, rows)
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        code = cl.critic(app, path, exclude_tripwire=50.0)
    assert code == 0
    assert (
        "!!! EXCLUDE-TRIPWIRE tripped: exclusion share 66.7% (2/3) exceeds threshold 50.0%"
        in err.getvalue()
    )
    err2 = io.StringIO()
    with contextlib.redirect_stderr(err2):
        cl.critic(app, path)  # no tripwire -> silent
    assert "EXCLUDE-TRIPWIRE" not in err2.getvalue()


# ---------------------------------------------------------------------------
# A10: the declared taxonomy (class demand, not just class observation)
# ---------------------------------------------------------------------------


def test_a_declared_class_demands_an_answer_with_zero_rows(app2, tmp_path):
    """A10: the taxonomy widens demand before the first row observes it.

    `parser-input` has no ledger row anywhere; declaring it applicable to
    `extractors` is enough to make the critic flag it UNREVIEWED and the
    queue name its pair.
    """
    taxonomy = tmp_path / "class-taxonomy.json"
    taxonomy.write_text(json.dumps({"scopes": {"extractors": ["availability", "parser-input"]}}))
    path = _write(
        tmp_path,
        [
            _row(
                # The surface name is the extractors scope INVENTORY's name
                # for this area: an area and a row that spell the surface
                # differently are two surfaces, and the pair reads as open.
                surface="pdf parse (pymupdf.open sites, helpers/pdf/*)",
                kind="ingestion",
                attack_class="availability",
                scope="extractors",
            ),
        ],
    )
    code, out = _critic(app2, path, scope="extractors", taxonomy=taxonomy)
    assert code == 0
    assert "2 required class(es)" in out
    assert "never covered: parser-input" in out
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cl.queue(app2, path, scope="extractors", out=tmp_path / "q.jsonl", taxonomy=taxonomy)
    recs = [json.loads(line) for line in (tmp_path / "q.jsonl").read_text().splitlines()]
    # The scope inventory names three areas; the fixture covers one of them
    # for availability. So availability stays open on the other two, and
    # parser-input (no row anywhere) is open on all three.
    assert sorted(r["attack_class"] for r in recs) == [
        "availability",
        "availability",
        "parser-input",
        "parser-input",
        "parser-input",
    ]
    assert all(r["reason"] == "not_a_route" for r in recs)


def test_a_missing_taxonomy_file_keeps_row_emergent_demand(app2, tmp_path):
    taxonomy = tmp_path / "absent.json"  # never written
    path = _write(tmp_path, [_row()])  # /api/thing covered injection; nothing declared
    code, out = _critic(app2, path, taxonomy=taxonomy)
    assert code == 0
    assert "1 required class(es)" in out  # only the observed class is demanded
    assert "parser-input" not in out


def test_a_malformed_taxonomy_file_is_a_loud_error(tmp_path):
    taxonomy = tmp_path / "class-taxonomy.json"
    taxonomy.write_text("not json at all{")
    with pytest.raises(SystemExit, match="class-taxonomy:"):
        cl._load_taxonomy(taxonomy)
    taxonomy.write_text("[1, 2]")  # valid JSON, wrong shape
    with pytest.raises(SystemExit, match="scopes"):
        cl._load_taxonomy(taxonomy)


# ---------------------------------------------------------------------------
# the AVAIL-1 guard: a GET-only route that reads query-string input does
# not get the terminal availability excuse
# ---------------------------------------------------------------------------

APP3 = """
from flask import Flask, jsonify, request

app = Flask(__name__)


@app.route("/api/search")
def search():
    q = request.args.get("q", "")
    return jsonify(results=[q])


@app.route("/api/plain")
def plain():
    return "plain"
"""


@pytest.fixture
def app3(tmp_path):
    path = tmp_path / "app3.py"
    path.write_text(APP3)
    return path


def test_availability_with_query_input_is_demanded_not_excused(app3, tmp_path):
    """AVAIL-1 was a quadratic join on a GET route, driven by request.args
    volume — a GET handler that reads the query string must keep its
    availability pair demanded (`query_arg_sink`), while concurrency on the
    same route stays terminal (a race needs shared-state mutation, which
    request.args does not create) and a no-args GET is excused as before.
    """
    path = _write(
        tmp_path,
        [
            # Seed the class demand on the no-args route; the gaps land on
            # /api/search, whose handler reads request.args.
            _row(surface="/api/plain", attack_class="availability"),
            _row(surface="/api/plain", attack_class="injection"),
            _row(surface="/api/plain", attack_class="concurrency"),
        ],
    )
    code, out, lines = _queue(app3, path)
    assert code == 0
    by_pair = {(r["surface"], r["attack_class"]): r for r in lines}
    assert by_pair[("/api/search", "availability")]["reason"] == "query_arg_sink"
    assert by_pair[("/api/search", "injection")]["reason"] == "query_arg_sink"
    # The tracked taxonomy also declares web-protocol/auth for app: still
    # query-reachable on a GET route.
    assert by_pair[("/api/search", "web-protocol/auth")]["reason"] == "query_arg_sink"
    assert by_pair[("/api/search", "concurrency")]["reason"] == "read_only_route"
    assert "read_only_route=1 query_arg_sink=4" in out


def test_amend_revises_an_existing_row_in_place(app2, tmp_path):
    """amend is the re-review path: an already-accounted pair's verdict,
    status, note or review date changes, the row count does not."""
    path = _write(
        tmp_path,
        [
            _row(
                surface="/api/mut",
                attack_class="availability",
                verdict="note:low",
                reviewed="2026-09-18",
            ),
        ],
    )
    decision = _decisions(
        tmp_path,
        {
            "surface": "/api/mut",
            "attack_class": "availability",
            "decision": "amend",
            "reviewed": "2026-10-21",
            "verdict": "REMEDIATED",
            "note": "re-swept: amplification measured, cap recorded",
        },
    )
    code, out = _apply(app2, path, decision, write=True, scope="app")
    assert code == 0, out
    assert "would-amend availability @ /api/mut" in out
    assert "ledger now 1 row(s)" in out  # replaced, not appended
    rows = json.loads(path.read_text())
    assert len(rows) == 1
    amended = rows[0]
    assert amended["verdict"] == "REMEDIATED"
    assert amended["reviewed"] == "2026-10-21"
    assert "amplification measured" in amended["note"]
    # Untouched fields survive the revision.
    assert amended["fingerprint"] == "deadbeefdeadbeef"


def test_amend_demands_an_existing_row_and_a_real_date(app2, tmp_path):
    path = _write(tmp_path, [_row(surface="/api/mut", attack_class="injection")])
    decisions = _decisions(
        tmp_path,
        {  # a pair with no ledger row cannot be amended
            "surface": "/api/get",
            "attack_class": "injection",
            "decision": "amend",
            "reviewed": "2026-10-21",
            "verdict": "clean",
        },
        {  # a bogus review date is refused
            "surface": "/api/mut",
            "attack_class": "injection",
            "decision": "amend",
            "reviewed": "20-20-20-20",
        },
        {  # an amend that changes nothing is refused
            "surface": "/api/mut",
            "attack_class": "injection",
            "decision": "amend",
        },
    )
    code, out = _apply(app2, path, decisions, scope="app")
    assert code == 1
    assert "NOT ACCOUNTED /api/get / injection" in out
    assert "INVALID DATE /api/mut / injection" in out
    assert json.loads(path.read_text()) == [
        _row(surface="/api/mut", attack_class="injection")
    ]  # the refused journal wrote nothing
