#!/usr/bin/env python3
"""Review kit — the journaled sitting workflow shared by every triage queue.

Extracted from ``seed_nic2008.review`` (proposal
``doc/improvements/proposals/review_kit.md`` S1, follows completed.md #246).
The kit owns the WORKFLOW SPINE and never writes domain data:

  * session-bounded append-only JSONL journal (``sitting-start`` /
    per-decision lines / ``sitting-end``);
  * lane assembly — open (``suggested``) walk by default, ``--redecide``
    adds decided (``promoted``), ``--skipped`` adds parked, ``--labels``
    pulls named items from any lane;
  * the per-item keypress loop (render -> ask -> dispatch), where ``ask``
    is a domain callback that re-asks on invalid input and may journal
    ``bad-*`` attempt lines via ``note()``;
  * batch confirm as the ONLY write gate, then delegation to the domain's
    existing apply machinery via ``apply_batch``.

Journal convention (stable across domains): one consolidated JSONL per
queue, append-only, never rewritten. Every line carries ``ts`` and
``session`` (sitting id); decision lines additionally carry the item key
(domain-named, e.g. ``label``) and ``action`` (``approve`` / ``skip`` /
``quit`` / ``abort`` / domain attempt lines). Read-back
(:func:`latest_action_by`) keys on the item id with LATEST terminal action
wins — that is what parks a decided item when its producer refills the
queue. Item ids must be stable across producer cycles; parking is only as
good as the id.

Replays are new sittings: the journal is evidence, never an undo log.
"""

from __future__ import annotations

import json
import pathlib
import time
from collections.abc import Callable

__all__ = ["Journal", "park_items", "latest_action_by", "assemble_entries", "ReviewSession"]


class Journal:
    """Session-bounded, append-only JSONL journal for one queue.

    Lines are buffered and flushed as one sitting block on :meth:`end` —
    the on-disk format matches the original single-``open("a")`` write of
    ``seed_nic2008.review``: ``sitting-start``, decision lines (domain
    dicts, ``ts``/``session`` added), ``sitting-end`` with the sitting's
    approved count.
    """

    def __init__(self, path: pathlib.Path, *, session: str | None = None) -> None:
        self.path = path
        self.session = session or time.strftime("%Y%m%d_%H%M%S")
        self._lines: list[str] = []
        path.parent.mkdir(parents=True, exist_ok=True)

    def _stamp(self, payload: dict) -> str:
        return json.dumps({"ts": self.session, "session": self.session, **payload})

    def start(self) -> None:
        self._lines.append(self._stamp({"action": "sitting-start"}))

    def write(self, payload: dict) -> None:
        self._lines.append(self._stamp(payload))

    def end(self, *, approved: int) -> None:
        self._lines.append(self._stamp({"action": "sitting-end", "approved": approved}))
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(self._lines) + "\n")


def park_items(journal_path: pathlib.Path, ids: list[str], *, key_field: str = "id") -> int:
    """Record one park sitting: N ``skip`` lines under one session block.

    The export-side parking lane (review-kit S4): producers with no
    in-tool apply lane (worklists consumed by hand elsewhere) still get
    decided-once semantics — their export filters items whose latest
    terminal action is ``skip`` (see :func:`latest_action_by`).
    """
    if not ids:
        return 0
    j = Journal(journal_path)
    j.start()
    for item in ids:
        j.write({key_field: item, "action": "skip"})
    j.end(approved=0)
    return len(ids)


def latest_action_by(
    journal_path: pathlib.Path, *, key_field: str, terminal: tuple[str, ...] = ("approve", "skip")
) -> dict[str, str]:
    """Latest terminal journal decision per item id ({} when no journal).

    Only terminal actions count (default ``approve``/``skip``); the latest
    line per id wins. This is the parking read-back: an item whose latest
    action is terminal is NOT re-asked by :func:`assemble_entries`.
    """
    if not journal_path.exists():
        return {}
    latest: dict[str, str] = {}
    for line in journal_path.read_text().splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        key, act = d.get(key_field), d.get("action")
        if key and act in terminal:
            latest[key] = act
    return latest


def assemble_entries(
    worklist: dict,
    *,
    labels_filter: set[str] | None,
    redecide: bool,
    skipped: bool,
    sort_key: Callable[[dict], tuple],
    limit: int | None = None,
) -> list[dict]:
    """Lane assembly with the standing inclusion rules.

    Default walk = OPEN items only (``suggested`` lane). ``redecide`` adds
    decided items (``promoted``); ``skipped`` adds parked ones; a
    ``labels_filter`` pulls named items from either lane without opening
    the whole lane. Filter wins last: entries end filtered to the set.
    """
    worklist.setdefault("skipped", [])
    entries = list(worklist["suggested"])
    if redecide:
        entries += list(worklist["promoted"])
    elif labels_filter is not None:
        entries += [e for e in worklist["promoted"] if e["label"] in labels_filter]
    if skipped:
        entries += list(worklist["skipped"])
    elif labels_filter is not None:
        entries += [e for e in worklist["skipped"] if e["label"] in labels_filter]
        entries += [e for e in worklist.get("no_signal", []) if e["label"] in labels_filter]
    if labels_filter is not None:
        entries = [e for e in entries if e["label"] in labels_filter]
    entries.sort(key=sort_key)
    if limit:
        entries = entries[:limit]
    return entries


class ReviewSession:
    """One sitting over ``entries``: render -> ask -> dispatch -> confirm -> apply.

    Domain callbacks (all required unless noted):

    render(idx, total, entry)
        print the item header (evidence rendering is the domain's).
    ask(entry, note)
        run the domain keypress prompt until a VALID decision is reached;
        return a decision dict with ``action`` in
        ``{"approve", "skip", "quit", "abort"}``. Invalid attempts should be
        journaled by calling ``note({...action: "bad-*"...})`` — the line
        lands in the journal exactly like a decision.
    spec_of(decision, entry)
        approve payload -> the spec string shown in the batch review.
    apply_batch(specs, decisions)
        the ONLY domain write — delegate to the queue's existing validated
        machinery; returns ``(applied_lines, error_lines)``.
    post_approve(decision, entry) -> list[dict]  (optional)
        extra journal lines an approval implies (e.g. superseding a
        previous active pick in the same lane).
    batch_header / apply_noun
        phrasing of the batch review + confirm prompt.
    apply_flag=False (dry-run)
        journals the sitting with ``preview-approve`` / ``preview-skip``
        actions (non-terminal — never parks) and skips the confirm.
    """

    def __init__(
        self,
        *,
        entries: list[dict],
        journal: Journal,
        render: Callable[[int, int, dict], None],
        ask: Callable[[dict, Callable[[dict], None]], dict],
        spec_of: Callable[[dict, dict], str],
        apply_batch: Callable[[list[str], list[dict]], tuple[list[str], list[str]]],
        post_approve: Callable[[dict, dict], list[dict]] | None = None,
        batch_header: str = "batch to promote:",
        apply_noun: str = "promotion(s)",
        input_fn=input,
        print_fn=print,
        apply_flag: bool = True,
    ) -> None:
        self.entries = entries
        self.journal = journal
        self.render = render
        self.ask = ask
        self.spec_of = spec_of
        self.apply_batch = apply_batch
        self.post_approve = post_approve
        self.batch_header = batch_header
        self.apply_noun = apply_noun
        self.input_fn = input_fn
        self.print_fn = print_fn
        self.apply_flag = apply_flag

    def run(self) -> dict:
        specs: list[str] = []
        decisions: list[dict] = []

        def note(decision: dict) -> None:
            decisions.append(decision)

        self.journal.start()
        for idx, e in enumerate(self.entries, 1):
            self.render(idx, len(self.entries), e)
            d = self.ask(e, note)
            if d["action"] == "abort":
                decisions.append(d)
                # stop WALKING only — the sitting still journals and the
                # batch tail still runs (previously-approved specs remain
                # confirmable). Matches seed_nic2008.review exactly.
                self.print_fn("aborted — nothing applied")
                break
            if d["action"] == "quit":
                decisions.append(d)
                break
            if d["action"] == "skip":
                decisions.append(d)
                continue
            specs.append(self.spec_of(d, e))
            decisions.append(d)
            if self.post_approve is not None:
                decisions.extend(self.post_approve(d, e))
        # dry-run sittings are EXPLORATION: approve/skip lines journal as
        # preview-* (non-terminal) so they never park an item — parking
        # only counts decisions that were eligible to apply.
        for d in decisions:
            if not self.apply_flag and d.get("action") in ("approve", "skip"):
                d = {**d, "action": f"preview-{d['action']}"}
            self.journal.write(d)
        self.journal.end(approved=len(specs))
        result: dict = {
            "decisions": len(decisions),
            "approved": len(specs),
            "applied": 0,
            "journal": str(self.journal.path),
        }
        if not specs:
            self.print_fn("nothing approved")
            return result
        self.print_fn(f"\n{self.batch_header}")
        for spec in specs:
            self.print_fn(f"  {spec}")
        if not self.apply_flag:
            return result
        sure = self.input_fn(f"apply {len(specs)} {self.apply_noun}? y/N: ").strip().lower()
        if sure != "y":
            self.print_fn("not confirmed — nothing applied (journal kept)")
            return result
        applied, errors = self.apply_batch(specs, decisions)
        for line in applied:
            self.print_fn(f"  + {line}")
        for line in errors:
            self.print_fn(f"  ! {line}")
        result["applied"] = len(applied)
        result["errors"] = errors
        return result
