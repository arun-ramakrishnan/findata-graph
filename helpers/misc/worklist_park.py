#!/usr/bin/env python3
"""Park worklist items — the export-side lane of the review kit (S4).

Producers whose worklists are consumed by hand elsewhere (country /
subsector / counterparty assignments) have no in-tool apply lane; this
tool records PARK decisions (append-only journal, kit format) so the
next producer run suppresses those items: decided once, never re-asked.

Usage:
    python3 helpers/misc/worklist_park.py --queue countries --id "AWL Agri Business"
    python3 helpers/misc/worklist_park.py --queue subsector --id "Banks - Regional"
    python3 helpers/misc/worklist_park.py --list            # parked per queue

Journals live at ``outputs/worklist_parking/<queue>/journal.jsonl``
(key field: the worklist item id — company name, subsector label, or
counterparty name). Reopen = remove the sitting block (journal is
append-only evidence, not an undo log).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.review_kit import latest_action_by, park_items  # noqa: E402

PARKING_ROOT = _REPO_ROOT / "outputs" / "worklist_parking"

# queue -> (journal dir name, item key field, what an id names)
QUEUES: dict[str, tuple[str, str, str]] = {
    "countries": ("countries", "name", "company name (country_worklist.json)"),
    "subsector": ("subsector", "label", "subsector label (subsector_worklist.json)"),
    "counterparty": ("counterparty", "name", "counterparty name (counterparty_worklist.json)"),
}


def _journal_path(queue: str) -> Path:
    return PARKING_ROOT / QUEUES[queue][0] / "journal.jsonl"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--queue", choices=sorted(QUEUES), help="which worklist queue to park in")
    p.add_argument("--id", nargs="+", help="item id(s) to park (worklist key)")
    p.add_argument("--list", action="store_true", help="show parked items per queue")
    args = p.parse_args(argv)

    if args.list:
        for q in sorted(QUEUES):
            parked = latest_action_by(_journal_path(q), key_field=QUEUES[q][1])
            print(f"{q}: {len(parked)} parked")
            for item in sorted(parked):
                print(f"  {item}")
        return 0
    if not args.queue or not args.id:
        p.error("--queue and --id are required (or --list)")
    key_field = QUEUES[args.queue][1]
    already = latest_action_by(_journal_path(args.queue), key_field=key_field)
    fresh = [i for i in args.id if i not in already]
    n = park_items(_journal_path(args.queue), fresh, key_field=key_field)
    skipped = len(args.id) - n
    print(f"parked {n} item(s) in {args.queue} -> {_journal_path(args.queue)}")
    if skipped:
        print(f"({skipped} already parked — unchanged)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
