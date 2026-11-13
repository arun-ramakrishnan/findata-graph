"""Group-discovery growth monitor (S4 of group_discovery_scale_lane).

Re-runs the inventory on a target store and diffs against a reference
report, producing a drift signal: which components grew/shrunk, which new
components appeared, how seed coverage changed, and how the component-size
distribution moved. Read-only; takes two inventory JSON reports (the
``--json`` output of group_discovery_inventory.py).

Usage::

    .venv/bin/python3 helpers/maintenance/group_discovery_growth_monitor.py \
        --reference bench_data/group_discovery_inventory/report.json \
        --current /tmp/report.current.json \
        --json /tmp/growth.json

The report includes:

- component count/size-sum delta;
- largest-grown / largest-shrunk components (top 10 each);
- new components (in current, absent from reference);
- seed-coverage delta (family-hit members);
- per-size-band histogram delta (e.g. [2,4), [4,8), ...).

Exit code 0 always (advisory tool).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def _band(size: int) -> str:
    if size < 4:
        return "2-3"
    if size < 8:
        return "4-7"
    if size < 16:
        return "8-15"
    if size < 32:
        return "16-31"
    if size < 64:
        return "32-63"
    if size < 128:
        return "64-127"
    if size < 256:
        return "128-255"
    return "256+"


def load_report(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    if isinstance(data, dict) and "components" in data:
        data = data["components"]
    return data


def compute(reference: list[dict], current: list[dict]) -> dict:
    # inventory reports carry members_sample (cap) + family_members, not full
    # member lists — count what we have for coverage direction
    def coverage(comps):
        s = set()
        for c in comps:
            s.update(c.get("members_sample", []))
            s.update(c.get("family_members", []))
        return s

    ref_members = coverage(reference)
    cur_members = coverage(current)

    # Components are identified by their filer-ref signature (top_refs),
    # not by size — size varies across runs, the disclosure mix is stable.
    def sig(c):
        return tuple(sorted(r for r, _ in c.get("top_refs", [])))

    ref_sigs = {sig(c) for c in reference}
    cur_sigs = {sig(c) for c in current}

    new_comps = [c for c in current if sig(c) not in ref_sigs]
    gone_comps = [c for c in reference if sig(c) not in cur_sigs]

    def bands(comps):
        out = {}
        for c in comps:
            b = _band(c["size"])
            out[b] = out.get(b, 0) + 1
        return out

    ref_hits = sum(c.get("family_hit_count", 0) for c in reference)
    cur_hits = sum(c.get("family_hit_count", 0) for c in current)

    # merged labels present now but not before (per component, unioned)
    def flags(comps):
        s = set()
        for c in comps:
            s.update(c.get("merge_flags", []))
        return s

    ref_flags = flags(reference)
    cur_flags = flags(current)

    return {
        "reference_components": len(reference),
        "current_components": len(current),
        "reference_companies_sampled": len(ref_members),
        "current_companies_sampled": len(cur_members),
        "new_components": [
            {"size": c["size"], "top_refs": c.get("top_refs", [])[:2]}
            for c in sorted(new_comps, key=lambda c: -c["size"])[:10]
        ],
        "gone_components": [
            {"size": c["size"], "top_refs": c.get("top_refs", [])[:2]}
            for c in sorted(gone_comps, key=lambda c: -c["size"])[:10]
        ],
        "family_hit_delta": cur_hits - ref_hits,
        "merge_flags_added": sorted(cur_flags - ref_flags),
        "merge_flags_removed": sorted(ref_flags - cur_flags),
        "size_bands_reference": bands(reference),
        "size_bands_current": bands(current),
    }


def render(report: dict) -> str:
    lines = ["# Group-discovery growth monitor", ""]
    lines.append(
        f"- components: {report['reference_components']} → "
        f"{report['current_components']}"
    )
    lines.append(
        f"- companies in components (sampled): {report['reference_companies_sampled']} → "
        f"{report['current_companies_sampled']}"
    )
    lines.append(f"- family-hit delta: {report['family_hit_delta']:+d}")
    if report["merge_flags_added"]:
        lines.append(
            f"- merge flags added: {', '.join(report['merge_flags_added'])}"
        )
    if report["merge_flags_removed"]:
        lines.append(
            f"- merge flags removed: {', '.join(report['merge_flags_removed'])}"
        )
    lines.append("")
    if report["new_components"]:
        lines.append("## New components")
        lines.append("| size | top refs |")
        lines.append("|---|---|")
        for g in report["new_components"]:
            refs = ", ".join(r[0] for r in g["top_refs"])
            lines.append(f"| {g['size']} | {refs} |")
        lines.append("")
    if report["gone_components"]:
        lines.append("## Gone components")
        lines.append("| size | top refs |")
        lines.append("|---|---|")
        for g in report["gone_components"]:
            refs = ", ".join(r[0] for r in g["top_refs"])
            lines.append(f"| {g['size']} | {refs} |")
        lines.append("")
    lines.append("## Size-band histogram delta")
    lines.append("| band | reference | current |")
    lines.append("|---|---|---|")
    for band in sorted(
        set(report["size_bands_reference"]) | set(report["size_bands_current"]),
        key=lambda b: int(b.split("-")[0]) if b != "256+" else 256,
    ):
        lines.append(
            f"| {band} | {report['size_bands_reference'].get(band, 0)} | "
            f"{report['size_bands_current'].get(band, 0)} |"
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reference", required=True, type=Path)
    ap.add_argument("--current", required=True, type=Path)
    ap.add_argument("--json", dest="json_out", type=Path, default=None)
    args = ap.parse_args()

    ref = load_report(args.reference)
    cur = load_report(args.current)
    report = compute(ref, cur)
    print(render(report))
    if args.json_out:
        args.json_out.write_text(json.dumps(report, indent=2) + "\n")
        print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
