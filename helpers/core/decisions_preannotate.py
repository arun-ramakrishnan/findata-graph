#!/usr/bin/env python3
"""decisions-preannotate — carrier pre-annotation for every review surface.

One producer, five surfaces. For each review surface below (the same
surfaces the review-kit menu system drives), this CLI walks the live
candidate rows, asks a System One carrier (``mercury-decide`` by default)
the two noul questions used by the relations lane, and writes ADVISORY
annotation rows to a per-surface JSONL file. The human review gate is
unchanged: nothing here writes domain data, and every review loop renders
the verdicts only as a second opinion beside the evidence
(``render_carrier_for``).

Surfaces and their row sources (the live queues, never the frozen eval):

  relations    SIDECAR (findata/Misc/_pending_relations.txt) — key is the
               (edge_type, source, target_mention) triple; existing
               annotations live at MERCURY_ANNOTATIONS
  quotes       WORKLIST open canonicals (quote_entity_worklist.json) — key
               is the sha256 canonical id
  coverage     coverage queue gap records (_gap_records) — key is
               ``surface/attack_class``
  findings     needs_validation findings — key is the finding fingerprint
  checklist    checklist items (ocr / markdown-parse) — key is the item id

Rows already annotated (same key present in the output file) are skipped,
so re-runs are idempotent and cheap. ``--limit N`` processes only the
first N candidate rows (useful for a smoke pass).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core import typed_judgment as tj  # noqa: E402

# -- questions (shared across surfaces; criteria mirror the relations lane)
Q1_FACTUAL_QUESTION = "Is this claimed relation real in the world?"
Q2_ADMIT_QUESTION = "Admit this row to the knowledge graph?"

Q1_CRITERIA = {
    "true": "The relation occurred as stated (right parties, type, direction)",
    "false": "It did not occur, or occurred with the wrong type/counterparty/direction",
}
Q2_CRITERIA = {
    "true": "All four conditions hold - admit to the graph",
    "false": "At least one condition fails - keep out",
}

# -- per-surface annotation output paths (defaults)
RELATIONS_OUT = _REPO_ROOT / "findata" / "Misc" / "_pending_annotations_mercury.jsonl"
QUOTES_OUT = _REPO_ROOT / "findata" / "Misc" / "_quote_annotations_mercury.jsonl"
COVERAGE_OUT = _REPO_ROOT / "outputs" / "coverage_review" / "mercury_annotations.jsonl"
FINDINGS_OUT = _REPO_ROOT / "outputs" / "findings_review" / "mercury_annotations.jsonl"
CHECKLIST_OUT = _REPO_ROOT / "outputs" / "{surface}_checklist_review" / "mercury_annotations.jsonl"


def load_id_annotations(path: Path) -> dict[str, dict]:
    """Key annotations by their ``id``/``key`` field (string key surfaces)."""
    out: dict[str, dict] = {}
    if not Path(path).exists():
        return out
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            a = json.loads(line)
        except ValueError:
            continue
        key = a.get("key") or a.get("id")
        if isinstance(key, str) and key:
            out[key] = a
    return out


def load_tuple_annotations(path: Path) -> dict[tuple[str, str, str], dict]:
    """Key annotations by the (edge_type, source, target_mention) triple."""
    out: dict[tuple[str, str, str], dict] = {}
    if not Path(path).exists():
        return out
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            a = json.loads(line)
        except ValueError:
            continue
        eid = str(a.get("id", ""))
        parts = eid.split(":", 2)
        if len(parts) == 3:
            out[(parts[0], parts[1], parts[2])] = a
    return out


@dataclass(frozen=True)
class SurfaceSpec:
    """One review surface: how to enumerate candidates and key them."""

    name: str
    iter_rows: Callable[[], list[dict]]
    key_of: Callable[[dict], str]
    state_of: Callable[[dict], str]
    default_out: Path
    load_existing: Callable[[Path], dict] = load_id_annotations  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Surface providers
# ---------------------------------------------------------------------------


def _relations_rows() -> list[dict]:
    from helpers.graph.triage_preannotate import parse_rows

    sidecar = _REPO_ROOT / "findata" / "Misc" / "_pending_relations.txt"
    if not sidecar.exists():
        return []
    return parse_rows(sidecar.read_text(encoding="utf-8").splitlines())


def _relations_key(row: dict) -> str:
    return f"{row['edge_type']}:{row['source']}:{row['target_mention']}"


def _relations_existing(path: Path) -> dict[str, dict]:
    """Load existing relations annotations by producer key (colon-joined
    edge_type:source:target). Legacy hand-placed rows carry ``id`` in that
    exact form, so load_tuple_annotations and load_id_annotations agree."""
    out = load_id_annotations(path)  # key field matches producer output
    for k, v in load_tuple_annotations(path).items():
        out["{0[0]}:{0[1]}:{0[2]}".format(k)] = v
    return out


def _relations_state(row: dict) -> str:
    ctx = row.get("quote") or ""
    return (
        f"Claimed relation: {row['source']} --{row['edge_type']}--> {row['target_mention']}\n"
        f"context: {ctx if ctx else '(none)'}\n"
        "The context is evidence, not truth: it is our vault's text, which may be "
        "garbled, a bare co-mention with no assertion, or may assert the claim."
    )


def _quotes_rows() -> list[dict]:
    from helpers.graph.triage_pending_quotes import WORKLIST, build_decisions

    if not WORKLIST.exists():
        return []
    return build_decisions(json.loads(WORKLIST.read_text()))


def _quotes_key(row: dict) -> str:
    return row["id"]


def _quotes_state(row: dict) -> str:
    sugg = "; ".join(row.get("suggestions", [])) or "(none)"
    notes = "; ".join(row.get("notes", [])[:3]) or "(none)"
    return (
        f"Open quote entity: {row['canonical']} (bucket {row['bucket']})\n"
        f"suggestions: {sugg}\nnotes: {notes}\n"
        "The context is our vault's text; verify the entity name and the best alias target."
    )


def _coverage_rows() -> list[dict]:
    from helpers.validators.coverage_ledger import DEFAULT_APP, DEFAULT_LEDGER, _gap_records

    try:
        return _gap_records(DEFAULT_APP, DEFAULT_LEDGER)
    except FileNotFoundError:
        return []


def _coverage_key(row: dict) -> str:
    return f"{row['surface']}/{row['attack_class']}"


def _coverage_state(row: dict) -> str:
    methods = ",".join(row.get("methods") or []) or "(n/a)"
    return (
        f"Coverage gap: surface={row['surface']} attack_class={row['attack_class']}\n"
        f"reason={row.get('reason', '')} methods={methods}\n"
        "Admit only if this class is genuinely unreviewed on this surface (gap is real)."
    )


_FINDINGS_PATH: Path | None = None  # set by main() via --findings


def _findings_rows() -> list[dict]:
    from helpers.validators.security_findings import DEFAULT_FINDINGS, _unwrap

    path = _FINDINGS_PATH or DEFAULT_FINDINGS
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError, ValueError:
        return []
    findings, _ = _unwrap(data, path)
    if findings is None:
        return []
    return [f for f in findings if isinstance(f, dict) and f.get("verdict") == "needs_validation"]


def _findings_key(row: dict) -> str:
    return str(row.get("fingerprint") or "")


def _findings_state(row: dict) -> str:
    return (
        f"Finding {row.get('fingerprint', '?')}: {row.get('title', '')}\n"
        f"surface={row.get('surface', '')} attack_class={row.get('attack_class', '')}\n"
        f"claimed_root_cause={row.get('claimed_root_cause', '')}\n"
        "Admit only if the claim is credible and the decisive facts are present."
    )


def _checklist_rows(surface: str) -> Callable[[], list[dict]]:
    def _rows() -> list[dict]:
        from helpers.core.review_checklist import build_entries

        return build_entries(surface)

    return _rows


def _checklist_key(row: dict) -> str:
    return str(row["label"])


def _checklist_state(row: dict) -> str:
    evidence = row.get("evidence", [])
    desc = "; ".join(str(e.get("description", "")) for e in evidence)
    return f"Checklist item {row['label']}: {desc}\nIs this item satisfied for the current work?"


def _checklist_default_out(surface: str) -> Path:
    return Path(str(CHECKLIST_OUT).format(surface=surface))


def make_surface_specs() -> dict[str, SurfaceSpec]:
    return {
        "relations": SurfaceSpec(
            "relations",
            _relations_rows,
            _relations_key,
            _relations_state,
            RELATIONS_OUT,
            load_existing=_relations_existing,
        ),
        "quotes": SurfaceSpec("quotes", _quotes_rows, _quotes_key, _quotes_state, QUOTES_OUT),
        "coverage": SurfaceSpec(
            "coverage", _coverage_rows, _coverage_key, _coverage_state, COVERAGE_OUT
        ),
        "findings": SurfaceSpec(
            "findings", _findings_rows, _findings_key, _findings_state, FINDINGS_OUT
        ),
    }


def checklist_specs() -> dict[str, SurfaceSpec]:
    out: dict[str, SurfaceSpec] = {}
    for surface in ("ocr", "markdown-parse"):
        out[surface] = SurfaceSpec(
            surface,
            _checklist_rows(surface),
            _checklist_key,
            _checklist_state,
            _checklist_default_out(surface),
        )
    return out


ALL_SPECS = {**make_surface_specs(), **checklist_specs()}


# ---------------------------------------------------------------------------
# Render helper
# ---------------------------------------------------------------------------


def carrier_verdict(a: dict) -> str | None:
    """Canonical carrier verdict from an annotation row: ``admit`` when
    ``p_rubric`` crosses 0.5, ``keep-out`` below, ``None`` when the row
    carries no rubric probability. Single source of truth shared by the
    evidence renderer and the recommended-menu-hint mappers."""
    pr = a.get("p_rubric")
    if pr is None:
        return None
    try:
        p = float(pr)
    except TypeError, ValueError:
        return None
    return "admit" if p > 0.5 else "keep-out"


def render_carrier_for(
    key: Any,
    *,
    c_map: dict,  # id-keyed (or tuple-keyed) annotation map
    print_fn=print,
    label: str = "mercury",
) -> None:
    """Print one advisory carrier line for an entry, if it has an annotation.

    Used by every review loop so the second opinion renders uniformly:
    ``ab mercury: fact p=0.41 / admission p=0.32 -> keep-out``. Missing
    annotation -> no output (silent)."""

    a = c_map.get(key) if isinstance(c_map, dict) else None
    if not a:
        return
    pf = a.get("p_fact")
    pr = a.get("p_rubric")
    pf_t = f"{float(pf):.2f}" if pf is not None else "(none)"
    pr_t = f"{float(pr):.2f}" if pr is not None else "(none)"
    verdict = carrier_verdict(a) or "(no p_rubric)"
    esc = " [ESC]" if a.get("escalated") else ""
    print_fn(f"     ab {label}: fact p={pf_t} / admission p={pr_t} -> {verdict}{esc}")


# ---------------------------------------------------------------------------
# Producer
# ---------------------------------------------------------------------------


def annotate_surface(
    spec: SurfaceSpec,
    *,
    model: str = "mercury-decide",
    limit: int = 0,
    out: Path | None = None,
    no_escalate: bool = False,
) -> tuple[int, int, Path]:
    """Ask the carrier about each un-annotated candidate; append advisory rows.

    Returns (todo, annotated, output_path). Idempotent: rows whose key is
    already present in the output are skipped. Never writes domain data.
    """
    out_path = out or spec.default_out
    rows = spec.iter_rows()
    if limit:
        rows = rows[:limit]
    existing = spec.load_existing(out_path)
    todo: list[dict] = []
    for r in rows:
        key = spec.key_of(r)
        if not key or key in existing:
            continue
        todo.append(r)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    annotated = 0
    with out_path.open("a", encoding="utf-8") as f:
        for r in todo:
            key = spec.key_of(r)
            questions = {
                "q1_fact": tj.noul(Q1_FACTUAL_QUESTION, Q1_CRITERIA),
                "q2_rubric": tj.noul(Q2_ADMIT_QUESTION, Q2_CRITERIA),
            }
            c = tj.ask(spec.state_of(r), questions, model, timeout=120)
            if not c.answers:
                print(f"  [{key}] FAILED: {c.error or 'empty answers'}", flush=True)
                continue
            p1 = float(c.answers["q1_fact"]["noul"])
            p2 = float(c.answers["q2_rubric"]["noul"])
            row = {
                "key": key,
                "id": key,
                "p_fact": p1,
                "factually_accurate": p1 >= 0.5,
                "p_rubric": p2,
                "rubric_admit": p2 > 0.5,
                "escalated": False,
                "evidence_url": None,
                "source": f"{model}:free",
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            annotated += 1
            print(
                f"  [{annotated}/{len(todo)}] {key} p_fact={p1:.2f} p_rubric={p2:.2f}", flush=True
            )
    return len(todo), annotated, out_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="decisions_preannotate", description=__doc__)
    ap.add_argument(
        "--surface",
        default="all",
        help="surface to annotate (all|relations|quotes|coverage|findings|ocr|markdown-parse)",
    )
    ap.add_argument("--limit", type=int, default=0, help="annotate only first N rows (0 = all)")
    ap.add_argument("--out", type=Path, default=None, help="override annotation output path")
    ap.add_argument("--model", default="mercury-decide")
    ap.add_argument("--no-escalate", action="store_true", help="(accepted for parity; no-op)")
    ap.add_argument(
        "--findings",
        type=Path,
        default=None,
        help="findings document to annotate (default: security_findings.DEFAULT_FINDINGS)",
    )
    args = ap.parse_args(argv)

    global _FINDINGS_PATH
    if args.findings is not None:
        _FINDINGS_PATH = args.findings

    surfaces = list(ALL_SPECS) if args.surface == "all" else [args.surface]
    unknown = [s for s in surfaces if s not in ALL_SPECS]
    if unknown:
        print(f"unknown surface(s): {unknown} (have {sorted(ALL_SPECS)})")
        return 1
    total_todo = total_done = 0
    for name in surfaces:
        spec = ALL_SPECS[name]
        print(f"== {name} ==")
        todo, annotated, out_path = annotate_surface(
            spec, model=args.model, limit=args.limit, out=args.out
        )
        total_todo += todo
        total_done += annotated
        print(f"  {name}: {todo} todo, {annotated} annotated -> {out_path}")
    print(f"\nannotated {total_done}/{total_todo} rows across {len(surfaces)} surface(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
