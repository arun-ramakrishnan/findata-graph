#!/usr/bin/env python3
"""NIC-2008 scorer benchmark — industry_coding_completion S1 (attested top-3 harness).

Ground truth = the tracked ``xwalk-*`` eval-gate pins in
``helpers/misc/ontology_questions.json`` (one per operator-attested label,
landed with the promotions at #246 close-out). Each pin's ``expected``
encodes the label's attested NIC-2008 lanes as ``nic2008:CODE:matchtype``;
the single closeMatch/exactMatch entry is the primary answer the suggest
lane must surface.

For every pinned label with a primary, each scorer variant ranks the FULL
subclass vocabulary (the suggest lane's own contract: top-k among
positive-affinity rows) and the harness reports hit@1, hit@3 and MRR, plus
the per-label miss table (--misses) with the primary's true rank when it
falls outside the top-k. Read-only: no DB, no writes.

Variants (all share the landed token/containment base; the code argument
enables ranking-level experiments):
- ``landed`` — the current ``seed_nic2008._lex_score`` (imported live).
- ``g2``     — difflib arm gated behind a >=2-token overlap precondition.
- ``gd``     — difflib dropped entirely (renormalized 0.75 F1 + 0.25 containment).
- ``f25``    — difflib arm gated behind F1 >= 0.25.
- ``n08``    — landed score x0.8 for n.e.c./trailing-99 rows (the #246
  live-tried-and-reverted demotion, now measured against attested answers).
- ``n07``    — same with a 0.7 factor.

Landing rule (proposal section 1): a variant lands into
``seed_nic2008._lex_score`` ONLY on strict improvement over the landed
baseline across hit@1/hit@3 with no MRR regression.

CLI:
    .venv/bin/python3 helpers/misc/nic_scorer_bench.py                # summary
    .venv/bin/python3 helpers/misc/nic_scorer_bench.py --misses landed
    .venv/bin/python3 helpers/misc/nic_scorer_bench.py --labels Airlines
"""

from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import pathlib
import re
import sys

HELPERS_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HELPERS_DIR.parent.parent
SEED_PATH = HELPERS_DIR / "nic2008_seed.json"
QUESTIONS_PATH = HELPERS_DIR / "ontology_questions.json"

_SPEC = importlib.util.spec_from_file_location("seed_nic2008", HELPERS_DIR / "seed_nic2008.py")
assert _SPEC is not None and _SPEC.loader is not None  # noqa: S101  # fixed repo-relative path
_sn = importlib.util.module_from_spec(_SPEC)
sys.modules["seed_nic2008"] = _sn
_SPEC.loader.exec_module(_sn)

_K = 3
_NEC_RE = re.compile(r"n\.e\.c|not elsewhere classified", re.I)

# Every variant has the ranking-level signature (label, desc, code) -> float.


def _tokens(text: str) -> set[str]:
    return _sn._nic_tokens(text)


def _landed(label: str, desc: str, code: str) -> float:
    return _sn._lex_score(label, desc)


def _base_parts(label: str, desc: str) -> tuple[float, float, float, str, str] | None:
    """Shared (overlap, f1, containment, joined-label, joined-desc); None = no signal."""
    lt, dt = _tokens(label), _tokens(desc)
    if not lt or not dt:
        return None
    overlap = sum(
        1
        for t in lt
        if t in dt or any(len(u) >= 3 and (t.startswith(u) or u.startswith(t)) for u in dt)
    )
    if overlap == 0:
        return None
    f1 = 2 * overlap / (len(lt) + len(dt))
    ls, ds = " ".join(sorted(lt)), " ".join(sorted(dt))
    contain = 1.0 if (ls in ds or ds in ls) else 0.0
    return overlap, f1, contain, ls, ds


def _g2(label: str, desc: str, code: str) -> float:
    """difflib ratio counts only when >=2 content tokens overlap."""
    parts = _base_parts(label, desc)
    if parts is None:
        return 0.0
    overlap, f1, contain, ls, ds = parts
    score = 0.6 * f1 + 0.2 * contain
    if overlap >= 2:
        score += 0.2 * difflib.SequenceMatcher(None, ls, ds).ratio()
    return round(score, 4)


def _gd(label: str, desc: str, code: str) -> float:
    """difflib-free renormalization."""
    parts = _base_parts(label, desc)
    if parts is None:
        return 0.0
    _overlap, f1, contain, _ls, _ds = parts
    return round(0.75 * f1 + 0.25 * contain, 4)


def _f25(label: str, desc: str, code: str) -> float:
    """difflib arm gated behind F1 >= 0.25."""
    parts = _base_parts(label, desc)
    if parts is None:
        return 0.0
    _overlap, f1, contain, ls, ds = parts
    score = 0.6 * f1 + 0.2 * contain
    if f1 >= 0.25:
        score += 0.2 * difflib.SequenceMatcher(None, ls, ds).ratio()
    return round(score, 4)


def _nec_factor(code: str, desc: str) -> bool:
    return code.endswith("99") or bool(_NEC_RE.search(desc))


def _demoted(factor: float):
    def score(label: str, desc: str, code: str) -> float:
        base = _sn._lex_score(label, desc)
        if base <= 0.0:
            return 0.0
        return round(base * (factor if _nec_factor(code, desc) else 1.0), 4)

    return score


VARIANTS = {
    "landed": _landed,
    "g2": _g2,
    "gd": _gd,
    "f25": _f25,
    "n08": _demoted(0.8),
    "n07": _demoted(0.7),
}


def _pins() -> list[dict]:
    data = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    questions = data.get("questions", data)
    out = []
    for q in questions:
        if not str(q.get("id", "")).startswith("xwalk-"):
            continue
        lanes = [
            (e.split(":")[1], e.split(":")[2] if len(e.split(":")) > 2 else "closeMatch")
            for e in q.get("expected", [])
            if str(e).startswith("nic2008:")
        ]
        primary = next((c for c, mt in lanes if mt in ("closeMatch", "exactMatch")), None)
        if primary:
            out.append({"id": q["id"], "label": q["params"]["source_concept"], "primary": primary})
    return out


def _ranked(score, label: str, subclasses: list[dict]) -> list[tuple[float, str]]:
    scored = [(score(label, r["description"], r["code"]), r["code"]) for r in subclasses]
    scored = [s for s in scored if s[0] > 0]
    scored.sort(key=lambda s: (-s[0], s[1]))
    return scored


def evaluate(name: str, pins: list[dict], subclasses: list[dict]) -> dict:
    score = VARIANTS[name]
    hit1 = hit3 = 0
    rr_sum = 0.0
    misses: list[tuple[str, str, list[str], int | None]] = []
    for p in pins:
        ranked = _ranked(score, p["label"], subclasses)
        codes = [c for _s, c in ranked[:_K]]
        if codes and codes[0] == p["primary"]:
            hit1 += 1
        if p["primary"] in codes:
            hit3 += 1
            rr_sum += 1.0 / (codes.index(p["primary"]) + 1)
        else:
            allcodes = [c for _s, c in ranked]
            rank = allcodes.index(p["primary"]) + 1 if p["primary"] in allcodes else None
            misses.append((p["label"], p["primary"], codes, rank))
    n = len(pins)
    return {"n": n, "hit1": hit1, "hit3": hit3, "mrr": rr_sum / n, "misses": misses}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--labels", help="comma-separated label substring filter")
    parser.add_argument(
        "--misses",
        metavar="VARIANT",
        help="print the miss table for one variant instead of the summary",
    )
    args = parser.parse_args(argv)

    subclasses = json.loads(SEED_PATH.read_text(encoding="utf-8"))["subclasses"]
    pins = _pins()
    if args.labels:
        subs = [s.lower() for s in args.labels.split(",")]
        pins = [p for p in pins if any(s in p["label"].lower() for s in subs)]
    if not pins:
        print("no pins matched")
        return 1

    if args.misses:
        if args.misses not in VARIANTS:
            parser.error(f"unknown variant {args.misses!r}; choose from {sorted(VARIANTS)}")
        res = evaluate(args.misses, pins, subclasses)
        print(f"{args.misses}: {len(res['misses'])}/{res['n']} labels miss top-{_K}")
        for label, primary, codes, rank in res["misses"]:
            r = rank if rank else "0-affinity"
            print(f"  {label:36s} primary={primary}  rank={r}  top{_K}={codes}")
        return 0

    print(
        f"{'variant':8s} {'hit@1':>6s} {'hit@3':>6s} {'MRR':>7s}   (n={len(pins)} attested labels)"
    )
    base = None
    for name in VARIANTS:
        res = evaluate(name, pins, subclasses)
        if name == "landed":
            base = res
        delta = ""
        if name != "landed" and base is not None:
            d1, d3 = res["hit1"] - base["hit1"], res["hit3"] - base["hit3"]
            delta = f"   delta hit@1 {d1:+d}, hit@3 {d3:+d}"
        print(f"{name:8s} {res['hit1']:6d} {res['hit3']:6d} {res['mrr']:7.4f}{delta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
