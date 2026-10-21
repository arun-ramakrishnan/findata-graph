#!/usr/bin/env python3
"""Prompt-driven review menu — a crisp-choice input backend for review_kit.

One backend, two surfaces:

  * ``--menu``    — interactive in-terminal numbered menu. A strict
    superset of today's keypress loops: same verbs, printed as numbered
    choices, answer by number or by the verb itself.
  * ``--prompt``  — agent-driven. Each item renders as a stable JSONL
    prompt packet (id, evidence card, choice list, prompt string). The
    host agent pastes the card into the conversation; the operator
    answers with a menu choice; the agent feeds the answer back. Same
    item -> same packet (idempotent serialization).

Design rules (proposal review_menu_prompt_driven.md, S1):

* **No queue semantics change.** This module only replaces *how a
  decision is entered*. ``spec_of``/``apply_batch`` and the write gate
  stay with the queue; ``ReviewSession`` stays the spine.
* **Stable ids.** Parking and packet keys use the queue's stable item id
  (``label`` or ``id``) — same rule as ``review_kit``.
* **Verbatim vocabulary.** Choice keys are the queue's own documented
  verbs (``al X``, ``d``, ``st``, ``1/2/3``, …). ``normalize_answer``
  splits a trailing ``| note`` (the queues' free-note suffix) without
  mangling the verb.
* **Advisory only.** Nothing here writes domain data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, TextIO

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Choice:
    """One menu choice. ``key`` is the queue's own verb (e.g. ``al X``)."""

    key: str
    label: str
    hint: str = ""


@dataclass
class MenuSpec:
    """The vocabulary for one review surface. Generated from the queue's
    own legend so the menu can never diverge from the documented keys."""

    name: str
    prompt: str
    choices: Sequence[Choice] = field(default_factory=tuple)

    def key_index(self) -> dict[str, Choice]:
        return {c.key: c for c in self.choices}


# ---------------------------------------------------------------------------
# Prompt packets
# ---------------------------------------------------------------------------

PACKET_SCHEMA = "review_menu/v1"


def build_packet(entry: dict, spec: MenuSpec, *, prompt_text: str | None = None) -> dict:
    """Deterministic packet for one item. Same entry + spec -> byte-identical
    JSON (sort_keys + fixed separators). ``evidence`` is the queue's full
    card; the renderer decides how much is shown."""

    item_id = entry.get("label") or entry.get("id") or "<no-id>"
    return {
        "schema": PACKET_SCHEMA,
        "surface": spec.name,
        "item_id": str(item_id),
        "evidence": entry,
        "choices": [{"key": c.key, "label": c.label, "hint": c.hint} for c in spec.choices],
        "prompt": prompt_text if prompt_text is not None else spec.prompt,
    }


def packet_json(packet: dict) -> str:
    return json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def packet_hash(packet: dict) -> str:
    return hashlib.sha256(packet_json(packet).encode("utf-8")).hexdigest()


def _flatten(obj: Any, prefix: str = "", depth: int = 3) -> Iterator[tuple[str, str]]:
    """Flatten ``evidence`` into ``(path, string)`` pairs for the text card.
    Depth-limited; scalars render inline."""

    if depth <= 0:
        yield (prefix, str(obj))
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (dict, list)):
                yield from _flatten(v, key, depth - 1)
            else:
                yield (key, "" if v is None else str(v))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            key = f"{prefix}[{i}]" if prefix else f"[{i}]"
            if isinstance(v, (dict, list)):
                yield from _flatten(v, key, depth - 1)
            else:
                yield (key, "" if v is None else str(v))
    else:
        yield (prefix, str(obj))


def render_packet(packet: dict, *, max_evidence: int = 24) -> str:
    """Human-readable card for pasting into a conversation: item id, the
    queue's evidence (flattened), then numbered choices."""

    lines: list[str] = []
    lines.append(f"### {packet['surface']} — item {packet['item_id']}")
    lines.append("")
    seen = 0
    for path, value in _flatten(packet.get("evidence", {})):
        if seen >= max_evidence:
            lines.append("  … (evidence truncated)")
            break
        lines.append(f"  {path}: {value}")
        seen += 1
    lines.append("")
    lines.append(packet["prompt"])
    lines.append("")
    for i, c in enumerate(packet["choices"], 1):
        hint = f" — {c['hint']}" if c["hint"] else ""
        lines.append(f"  {i}) {c['key']} — {c['label']}{hint}")
    lines.append("  ?) show full evidence again")
    lines.append("  q) quit")
    return "\n".join(lines)


def write_packets(
    entries: Iterable[dict],
    spec: MenuSpec,
    out: TextIO | pathlib.Path,
    *,
    prompt_text: str | None = None,
) -> int:
    """Write one JSON line per item (stable, sorted). Returns packet count."""

    if isinstance(out, pathlib.Path):
        handle = out.open("w", encoding="utf-8")
        close = True
    else:
        handle = out
        close = False
    n = 0
    try:
        for entry in entries:
            handle.write(packet_json(build_packet(entry, spec, prompt_text=prompt_text)) + "\n")
            n += 1
    finally:
        if close:
            handle.close()
    return n


def load_packets(src: TextIO | pathlib.Path) -> list[dict]:
    if isinstance(src, pathlib.Path):
        handle = src.open("r", encoding="utf-8")
        close = True
    else:
        handle = src
        close = False
    packets: list[dict] = []
    try:
        for lineno, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                packets.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"packet line {lineno} is not JSON: {exc}") from exc
    finally:
        if close:
            handle.close()
    return packets


# ---------------------------------------------------------------------------
# Answer entry
# ---------------------------------------------------------------------------


def normalize_answer(raw: str, spec: MenuSpec) -> tuple[str, str] | None:
    """Map a raw answer to ``(verb, note)``.

    * ``d``        -> (``d``, "")
    * ``d|note``   -> (``d``, "note")
    * ``al X``     -> (``al X``, "")       (parametrized choice: token match)
    * ``1``        -> the key of choice index 1 (1-based)

    Returns ``None`` when the answer matches nothing (the caller re-prompts).
    """

    line = raw.strip()
    if not line:
        return None
    verb, sep, note = line.partition("|")
    verb = verb.strip()
    note = note.strip() if sep else ""
    # 1-based numeric shortcut.
    if verb.isdigit():
        try:
            choice = spec.choices[int(verb) - 1]
        except IndexError:
            return None
        return (choice.key, note)
    # Exact key match first, then token match for parametrized verbs
    # (``al X``, ``a//kec``): the answer is accepted verbatim when its
    # first word equals the choice's first word.
    lowered = verb.lower()
    first = lowered.split(maxsplit=1)[0] if lowered else ""
    for choice in spec.choices:
        tokens = choice.key.strip().split(maxsplit=1)
        if len(tokens) == 1:
            tok = tokens[0].lower()
            if lowered == tok:
                return (choice.key, note)
            if tok.startswith(lowered):
                return (verb, note)
            # structurally-complex single-token verbs
            # ("a[/EDGE_TYPE[/TARGET]]") also accept any answer sharing the
            # leading alphanumeric stem — e.g. "a//kec" for the accept verb.
            stem = tok.split("[", 1)[0].split("/", 1)[0]
            if stem and lowered.startswith(stem) and ("[" in tok or "/" in tok):
                return (verb, note)
        elif first == tokens[0].lower():
            return (verb, note)
    return None


def make_menu_input(
    spec: MenuSpec,
    *,
    print_fn: Callable[[str], object] = print,
    input_fn: Callable[[str], str] = input,
) -> Callable[..., str]:
    """Interactive numbered-menu input function — a drop-in for the
    queues' ``input_fn`` (and ``ReviewSession.input_fn``).

    Returns the **verb string** the queue already parses (e.g. ``d``,
    ``al X``, ``d|noise row``). The batch-confirm prompt (which starts
    with ``apply ``) passes straight through to the raw reader so
    ``y/N`` works unchanged.
    """

    def _ask(_prompt: str = "") -> str:
        if _prompt.startswith("apply "):
            return input_fn(_prompt)
        print_fn(render_choices(spec))
        while True:
            raw = input_fn("> ").strip()
            if raw in ("?", "help"):
                print_fn(render_choices(spec))
                continue
            if raw in ("q", "x", "quit", "abort"):
                return raw
            parsed = normalize_answer(raw, spec)
            if parsed is not None:
                verb, note = parsed
                return f"{verb}|{note}" if note else verb
            print_fn(f"unrecognized: {raw!r} — pick a number or a verb")

    return _ask


def make_scripted_input(
    answers: Iterable[str],
    spec: MenuSpec,
    *,
    strict: bool = True,
) -> Callable[..., str]:
    """Pre-filled answers input function for ``--prompt``/scripted runs.

    Answers are consumed one per read, in walk order; a final line may
    carry the batch confirm (``y``/``n``). With ``strict=True`` an
    unrecognized answer raises ``ValueError`` (fail fast); otherwise the
    verb is passed through untouched so the queue re-prompts.
    """

    it: Iterator[str] = iter(answers)

    def _ask(_prompt: str = "") -> str:
        try:
            raw = next(it)
        except StopIteration as exc:
            raise EOFError("no more answers for the sitting") from exc
        raw = raw.strip()
        if raw in ("q", "x", "quit", "abort") or _prompt.startswith("apply "):
            return raw
        parsed = normalize_answer(raw, spec)
        if parsed is not None:
            verb, note = parsed
            return f"{verb}|{note}" if note else verb
        if strict:
            raise ValueError(f"unrecognized scripted answer: {raw!r}")
        return raw

    return _ask


def render_choices(spec: MenuSpec) -> str:
    lines = [spec.prompt, ""]
    for i, c in enumerate(spec.choices, 1):
        hint = f" — {c.hint}" if c.hint else ""
        lines.append(f"  {i}) {c.key} — {c.label}{hint}")
    lines.append("  ?) help   q) quit")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _read_entries(src: TextIO | pathlib.Path) -> Iterator[dict]:
    lines = src if isinstance(src, TextIO) else pathlib.Path(src).open("r", encoding="utf-8")
    try:
        for lineno, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"entries line {lineno} is not JSON: {exc}") from exc
            if not isinstance(obj, dict):
                raise ValueError(f"entries line {lineno} must be a JSON object")
            yield obj
    finally:
        if not isinstance(src, TextIO):
            lines.close()  # type: ignore[union-attr]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="review_menu", description=__doc__.splitlines()[0])
    ap.add_argument(
        "mode", choices=("packet", "parse"), help="packet: emit packets; parse: validate answers"
    )
    ap.add_argument("--surface", default="review", help="surface name (packet header)")
    ap.add_argument("--entries", type=pathlib.Path, help="entries JSONL (packet mode)")
    ap.add_argument("--out", type=pathlib.Path, help="packets JSONL output (default stdout)")
    ap.add_argument("--answers", type=pathlib.Path, help="answers file (parse mode)")
    ap.add_argument("--choices", nargs="*", default=[], help="verb=label[|hint] pairs")
    args = ap.parse_args(argv)

    choices = []
    for item in args.choices:
        key, _, rest = item.partition("=")
        label, _, hint = rest.partition("|")
        choices.append(Choice(key=key, label=label or key, hint=hint))
    spec = MenuSpec(
        name=args.surface,
        prompt="Review the item and decide:",
        choices=choices,
    )

    if args.mode == "packet":
        if args.entries is None:
            ap.error("--entries required for packet mode")
        n = write_packets(_read_entries(args.entries), spec, args.out or sys.stdout)
        return 0 if n else 1

    # parse mode
    if args.answers is None:
        ap.error("--answers required for parse mode")
    bad = 0
    with args.answers.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            parsed = normalize_answer(line, spec)
            if parsed is None:
                print(f"{args.answers}:{lineno}: unrecognized: {line!r}", file=sys.stderr)
                bad += 1
            else:
                print(f"{lineno}: {parsed[0]}{' | ' + parsed[1] if parsed[1] else ''}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
