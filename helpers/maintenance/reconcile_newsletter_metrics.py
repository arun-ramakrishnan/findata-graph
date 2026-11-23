#!/usr/bin/env python3
"""Post-parse metric reconciliation — newsletter magnitudes ↔ NSE-XBRL ground truth.

xchange_filings.md §10.3/§10.4 step 3 (the filings lane's last unbuilt
slice): ``derive_insights`` stores newsletter-extracted magnitudes in
``company_metrics`` (``source_ref LIKE 'derive:metrics:%'``) with
best-effort labels and mostly-missing periods; the filing lane already
holds the ground truth (``properties->>'via' = 'nse-xbrl'``, canonical
``FY26Q4`` periods, ``consolidated`` flag). This driver reconciles the
two WITHOUT touching ``company_metrics`` — verdicts ride a sidecar table
keyed on metric ``id`` (ids survive the derive applies; ``properties``
cannot carry the annotation because it is a content-hash column).

Match ladder (each miss → ``unmatched(reason)``, never a guess):

1. entity — exact name (case-sensitive, the store's join key);
2. period — explicit quarters only (`Q3 FY26`/`Q3FY26`/`FY26 Q3` →
   ``FY26Q3``); year-only (`FY26`) and date-ish (`December 2025`) values
   refuse to anchor (`period_ambiguous`) — silently pinning a quarter is
   exactly the mis-anchoring this driver exists to catch;
3. label — ``revenue``/``turnover``/`net sales`/`topline` → ``q_revenue``,
   ``profit``/`pat`/`net profit` → ``q_pat``, ``ebitda`` → ``q_ebitda``;
   NULL/unmapped labels never match;
4. unit — ``crore`` ×1, ``lakh`` ×0.01 crore; anything else (``bn_usd``,
   ``percent``, …) has no filing counterpart in v1;
5. value — nearest filing candidate wins within ``--threshold``
   (default 0.10): ``anchored``; beyond it, ``suspect`` (the parse is
   lying about a number the filings already know). Standalone and
   consolidated candidates both compete; the closer one wins, preferring
   ``consolidated`` on an exact tie.

Usage::

    python3 helpers/maintenance/reconcile_newsletter_metrics.py           # dry run
    python3 helpers/maintenance/reconcile_newsletter_metrics.py --apply   # write sidecar
    python3 helpers/maintenance/reconcile_newsletter_metrics.py --report outputs/metric_reconciliation.md
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.core.db import DEFAULT_DB_PATH, connect  # noqa: E402

NEWSLETTER_PREFIX = "derive:metrics:"

_METRIC_RECON_DDL = """
CREATE TABLE IF NOT EXISTS metric_reconciliation (
    id BIGINT PRIMARY KEY,            -- company_metrics.id of the NEWSLETTER row
    filing_id BIGINT,                 -- company_metrics.id of the matched filing row
    verdict VARCHAR NOT NULL,         -- anchored | suspect | unmatched
    filing_label VARCHAR,
    filing_period VARCHAR,
    filing_value_num DOUBLE,
    filing_unit VARCHAR,
    filing_source_ref VARCHAR,
    consolidated BIGINT,              -- matched filing row's flag
    delta_pct DOUBLE,                 -- |nl - filing| / filing
    reason VARCHAR,                   -- unmatched: why; else NULL
    value_crore DOUBLE,               -- newsletter value normalized to crore
    reconciled_at TIMESTAMP NOT NULL
)
"""

# label map — lowercase newsletter metric_label → filing metric_label.
_LABEL_MAP: dict[str, str] = {}
for _w in ("revenue", "turnover", "net sales", "topline", "top line"):
    _LABEL_MAP[_w] = "q_revenue"
for _w in ("profit", "pat", "net profit", "profit after tax"):
    _LABEL_MAP[_w] = "q_pat"
_LABEL_MAP["ebitda"] = "q_ebitda"

# unit → crore multiplier (v1: INR money units only; percent/bps/bn_usd
# have no filing counterpart).
_UNIT_TO_CRORE: dict[str, float] = {"crore": 1.0, "lakh": 0.01}

_PERIOD_RE = re.compile(
    r"\bQ([1-4])\s*FY\s*(\d{2,4})\b|\bFY\s*(\d{2,4})\s*Q([1-4])\b", re.IGNORECASE
)


def parse_period(period: str | None) -> str | None:
    """Explicit newsletter quarter → canonical ``FY{yy}Q{q}``; else None.

    Mirrors ingest_nse_xbrl's period convention (Indian FY Apr–Mar).
    Year-only or prose periods return None — the caller records
    ``period_missing``/``period_ambiguous``; it never guesses a quarter.
    """
    if not period:
        return None
    m = _PERIOD_RE.search(str(period))
    if not m:
        return None
    if m.group(1):  # "Q3 FY26" / "Q3FY26"
        q, yy = m.group(1), m.group(2)
    else:  # "FY26 Q3" / "FY26Q3"
        yy, q = m.group(3), m.group(4)
    yy = str(int(yy))  # "2026" → "2026"; "26" → "26"
    if len(yy) == 4:
        yy = yy[2:]
    return f"FY{yy}Q{q}"


def map_label(label: str | None) -> str | None:
    """Newsletter metric_label → filing metric_label (None = unmappable)."""
    if not label:
        return None
    return _LABEL_MAP.get(str(label).strip().lower())


def to_crore(value_num: float | None, unit: str | None) -> float | None:
    """Newsletter magnitude → crore (the filing money unit); None = skip."""
    if value_num is None:
        return None
    mult = _UNIT_TO_CRORE.get(str(unit or "").strip().lower())
    return None if mult is None else float(value_num) * mult


@dataclass
class ReconRow:
    id: int
    entity: str
    verdict: str
    reason: str | None = None
    filing_id: int | None = None
    filing_label: str | None = None
    filing_period: str | None = None
    filing_value_num: float | None = None
    filing_unit: str | None = None
    filing_source_ref: str | None = None
    consolidated: int | None = None
    delta_pct: float | None = None
    value_crore: float | None = None
    value_raw: str | None = None
    as_of_edition: str | None = None
    metric_label: str | None = None
    period_raw: str | None = None


def load_newsletter_rows(conn) -> list[tuple]:
    return conn.execute(
        "SELECT id, entity, metric_label, value_num, value_raw, unit, period, "
        "as_of_edition FROM company_metrics "
        "WHERE source_ref LIKE ?",
        (f"{NEWSLETTER_PREFIX}%",),
    ).fetchall()


def load_filing_rows(conn) -> dict[tuple[str, str, str], list[tuple]]:
    """(entity, q_label, period) → candidate rows (id, value, unit, cons, ref)."""
    out: dict[tuple[str, str, str], list[tuple]] = {}
    for r in conn.execute(
        "SELECT id, entity, metric_label, value_num, unit, period, consolidated, "
        "source_ref FROM company_metrics "
        "WHERE properties LIKE '%nse-xbrl%'"
    ).fetchall():
        out.setdefault((r[1], r[2], r[5]), []).append((r[0], r[3], r[4], r[6], r[7]))
    return out


def reconcile(conn, threshold: float = 0.10) -> list[ReconRow]:
    """Run the ladder over every newsletter metric row. Read-only."""
    filings = load_filing_rows(conn)
    rows: list[ReconRow] = []
    for rid, entity, label, value_num, value_raw, unit, period, edition in load_newsletter_rows(
        conn
    ):
        r = ReconRow(
            id=rid,
            entity=entity,
            verdict="unmatched",
            value_raw=value_raw,
            as_of_edition=edition,
            metric_label=label,
            period_raw=period,
        )
        canon = parse_period(period)
        if canon is None:
            r.reason = "period_ambiguous" if period else "period_missing"
            rows.append(r)
            continue
        q_label = map_label(label)
        if q_label is None:
            r.reason = "label_missing" if not label else "label_unmapped"
            r.filing_period = canon
            rows.append(r)
            continue
        crore = to_crore(value_num, unit)
        if crore is None:
            r.reason = "unit_unmapped" if value_num is not None else "value_missing"
            r.filing_period = canon
            rows.append(r)
            continue
        r.value_crore = crore
        candidates = filings.get((entity, q_label, canon), [])
        if not candidates:
            r.reason = "no_filing_rows"
            r.filing_label = q_label
            r.filing_period = canon
            rows.append(r)
            continue
        # nearest candidate wins; consolidated preferred on an exact tie
        best = min(
            candidates,
            key=lambda c: (abs(crore - (c[1] or 0.0)) / (c[1] or 1e-9), -(c[3] or 0)),
        )
        filing_value = best[1]
        delta = abs(crore - filing_value) / filing_value if filing_value else 1.0
        r.filing_id, r.filing_value_num, r.filing_unit = best[0], filing_value, best[2]
        r.consolidated = best[3]
        r.filing_source_ref = best[4]
        r.filing_label, r.filing_period = q_label, canon
        r.delta_pct = delta
        if delta <= threshold:
            r.verdict = "anchored"
            r.reason = None
        else:
            r.verdict = "suspect"
            r.reason = "beyond_threshold"
        rows.append(r)
    return rows


def apply_verdicts(conn, rows: list[ReconRow]) -> int:
    """Replace the sidecar with this run's verdicts (driver-owned table:
    full replace in one txn — ids are the newsletter metric ids, so a
    stale id disappears with its metric row on the next derive apply)."""
    now = datetime.now()
    conn.execute(_METRIC_RECON_DDL)
    with conn:
        conn.execute("DELETE FROM metric_reconciliation")
        for r in rows:
            conn.execute(
                "INSERT INTO metric_reconciliation VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r.id,
                    r.filing_id,
                    r.verdict,
                    r.filing_label,
                    r.filing_period,
                    r.filing_value_num,
                    r.filing_unit,
                    r.filing_source_ref,
                    r.consolidated,
                    r.delta_pct,
                    r.reason,
                    r.value_crore,
                    now,
                ),
            )
    return len(rows)


def _suspect_table(suspects: list[ReconRow]) -> list[str]:
    """Markdown rows for the beyond-threshold section."""
    out = [
        "## Suspect (beyond threshold)",
        "",
        "| entity | edition | newsletter | filing | Δ |",
        "|---|---|---|---|---|",
    ]
    for r in suspects:
        out.append(
            f"| {r.entity} | {r.as_of_edition or '—'} | {r.value_raw or '—'} "
            f"({r.metric_label or 'no label'}) | {r.filing_value_num:,.0f} "
            f"{r.filing_unit or ''} ({r.filing_period}) | {r.delta_pct:.0%} |"
        )
    return out


def _anchored_table(anchored: list[ReconRow]) -> list[str]:
    """Markdown rows for the anchored section."""
    out = [
        "## Anchored",
        "",
        "| entity | edition | newsletter | filing | Δ | consolidated |",
        "|---|---|---|---|---|---|",
    ]
    for r in anchored:
        out.append(
            f"| {r.entity} | {r.as_of_edition or '—'} | {r.value_raw or '—'} | "
            f"{r.filing_value_num:,.0f} ({r.filing_period}) | {r.delta_pct:.1%} "
            f"| {'yes' if r.consolidated else 'no'} |"
        )
    return out


def write_report(rows: list[ReconRow], path: Path, threshold: float) -> None:
    counts: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for r in rows:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
        if r.verdict == "unmatched":
            reasons[r.reason or "?"] = reasons.get(r.reason or "?", 0) + 1
    lines = [
        "# Metric reconciliation — newsletter ↔ NSE-XBRL",
        "",
        f"Generated {datetime.now():%Y-%m-%d %H:%M} · threshold {threshold:.0%} · "
        f"{len(rows)} newsletter rows",
        "",
        "| verdict | rows |",
        "|---|---|",
    ]
    for v in ("anchored", "suspect", "unmatched"):
        lines.append(f"| {v} | {counts.get(v, 0)} |")
    if reasons:
        lines += ["", "Unmatched reasons:", ""]
        # name tiebreak: bare negated-count sort ties render in PYTHONHASHSEED order
        lines += [
            f"- `{k}`: {n}" for k, n in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
    if suspects := [r for r in rows if r.verdict == "suspect"]:
        lines += ["", *_suspect_table(suspects)]
    if anchored := [r for r in rows if r.verdict == "anchored"]:
        lines += ["", *_anchored_table(anchored)]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true", help="write the sidecar (default dry run)")
    p.add_argument("--report", default=None, help="write the markdown report here")
    p.add_argument("--threshold", type=float, default=0.10, help="anchored band (default 0.10)")
    p.add_argument("--db", default=None, help="store path (default canonical)")
    args = p.parse_args()

    from helpers.maintenance.maint_timing import RunTimer

    timer = RunTimer("reconcile_metrics", mode="apply" if args.apply else "check")
    conn = connect(args.db or DEFAULT_DB_PATH)
    try:
        rows = reconcile(conn, threshold=args.threshold)
        counts: dict[str, int] = {}
        for r in rows:
            counts[r.verdict] = counts.get(r.verdict, 0) + 1
        print(
            f"reconcile: {len(rows)} newsletter rows — "
            f"anchored={counts.get('anchored', 0)} "
            f"suspect={counts.get('suspect', 0)} "
            f"unmatched={counts.get('unmatched', 0)}"
        )
        if args.report:
            write_report(rows, Path(args.report), args.threshold)
            print(f"report: {args.report}")
        if args.apply:
            apply_verdicts(conn, rows)
            print(f"sidecar: {len(rows)} verdicts written")
    finally:
        conn.close()
    return timer.finish(0, f"rows={len(rows)}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
