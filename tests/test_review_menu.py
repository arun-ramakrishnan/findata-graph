"""Review-menu tests — packet stability, rendering, answer normalization,
menu/scripted input backends (S1 of review_menu_prompt_driven.md)."""

from __future__ import annotations

import io
import json

import pytest

from helpers.core.review_menu import (
    Choice,
    MenuSpec,
    build_packet,
    load_packets,
    make_menu_input,
    make_scripted_input,
    normalize_answer,
    packet_hash,
    packet_json,
    render_packet,
    write_packets,
)

QUOTES_SPEC = MenuSpec(
    name="quotes",
    prompt="Review the quote canonical and decide:",
    choices=(
        Choice("1", "alias to suggestion 1"),
        Choice("2", "alias to suggestion 2"),
        Choice("3", "alias to suggestion 3"),
        Choice("al NAME", "alias to an existing entity by exact name"),
        Choice("st", "stub (entity created by hand later)"),
        Choice("sc CIN", "stub + record CIN now"),
        Choice("d", "discard — never asked again"),
        Choice("p", "park — skip, reopen via --skipped"),
        Choice("?", "legend + evidence"),
        Choice("q/x", "quit-and-review / abort walk"),
    ),
)

ENTRY = {
    "label": "sha256:abcd1234",
    "canonical": "Kuantum Papers — joint venture with Packaging Co",
    "bucket": "alias_candidate",
    "suggestions": ["Packaging Co", "International Packaging"],
    "notes": ["word-overlap with existing entity"],
    "vss_hint": "",
}


class TestPackets:
    def test_packet_deterministic(self) -> None:
        p1 = build_packet(ENTRY, QUOTES_SPEC)
        p2 = build_packet(ENTRY, QUOTES_SPEC)
        assert packet_json(p1) == packet_json(p2)
        assert packet_hash(p1) == packet_hash(p2)
        # Rebuild from the JSON round-trip is identical too.
        p3 = json.loads(packet_json(p1))
        assert packet_json(p3) == packet_json(p1)

    def test_packet_schema_surface(self) -> None:
        p = build_packet(ENTRY, QUOTES_SPEC)
        assert p["schema"] == "review_menu/v1"
        assert p["surface"] == "quotes"
        assert p["item_id"] == "sha256:abcd1234"
        assert p["evidence"] == ENTRY
        assert len(p["choices"]) == len(QUOTES_SPEC.choices)
        assert p["choices"][6] == {"key": "d", "label": "discard — never asked again", "hint": ""}

    def test_render_contains_id_and_choices(self) -> None:
        text = render_packet(build_packet(ENTRY, QUOTES_SPEC))
        assert "quotes" in text
        assert "sha256:abcd1234" in text
        assert "Kuantum Papers" in text
        assert "1) 1 — alias to suggestion 1" in text
        assert "8) p — park — skip, reopen via --skipped" in text
        assert "?) show full evidence again" in text

    def test_write_load_roundtrip(self, tmp_path) -> None:
        out = tmp_path / "packets.jsonl"
        n = write_packets([ENTRY, {**ENTRY, "label": "sha256:eeff"}], QUOTES_SPEC, out)
        assert n == 2
        packets = load_packets(out)
        assert len(packets) == 2
        assert packets[0]["item_id"] == "sha256:abcd1234"
        assert packets[1]["item_id"] == "sha256:eeff"


def _relations_spec() -> MenuSpec:
    return MenuSpec(
        name="relations",
        prompt="Review the relation row and decide:",
        choices=(
            Choice("a[/EDGE_TYPE[/TARGET]]", "accept (edge type + target overrideable)"),
            Choice("d", "reject — noise gate (discard)"),
            Choice("al NAME", "alias the mention to an existing entity NAME"),
            Choice("st", "stub (entity via follow-up chain)"),
            Choice("p", "park — never re-asked (reopen via --skipped)"),
            Choice("?", "legend + evidence"),
            Choice("q/x", "quit-and-review / abort walk"),
        ),
    )


class TestNormalize:
    def test_exact_verb(self) -> None:
        assert normalize_answer("d", QUOTES_SPEC) == ("d", "")
        assert normalize_answer("p", QUOTES_SPEC) == ("p", "")
        assert normalize_answer("st", QUOTES_SPEC) == ("st", "")

    def test_prefix_verb_accept(self) -> None:
        # "a" is a prefix of the single-token verb "a[/EDGE_TYPE[/TARGET]]"
        assert normalize_answer("a", _relations_spec()) == ("a", "")
        assert normalize_answer("a//kec", _relations_spec()) == ("a//kec", "")

    def test_free_note_suffix(self) -> None:
        assert normalize_answer("d|noise row", QUOTES_SPEC) == ("d", "noise row")
        assert normalize_answer("al Kuantum|mangled mention", QUOTES_SPEC) == (
            "al Kuantum",
            "mangled mention",
        )

    def test_parametrized_verb_token_match(self) -> None:
        assert normalize_answer("al Kuantum Papers", QUOTES_SPEC) == ("al Kuantum Papers", "")
        assert normalize_answer("sc ABC123", QUOTES_SPEC) == ("sc ABC123", "")

    def test_numeric_shortcut(self) -> None:
        assert normalize_answer("1", QUOTES_SPEC) == ("1", "")
        assert normalize_answer("7", QUOTES_SPEC) == ("d", "")
        assert normalize_answer("99", QUOTES_SPEC) is None

    def test_unrecognized(self) -> None:
        assert normalize_answer("", QUOTES_SPEC) is None
        assert normalize_answer("hello world", QUOTES_SPEC) is None
        assert normalize_answer("d d", QUOTES_SPEC) is None

    def test_case_insensitive_exact(self) -> None:
        assert normalize_answer("D", QUOTES_SPEC) == ("d", "")


class TestBackends:
    def test_menu_input_reads_number(self, monkeypatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _prompt: "7")
        out = io.StringIO()
        ask = make_menu_input(QUOTES_SPEC, print_fn=out.write, input_fn=lambda _p: "7")
        assert ask() == "d"
        assert "1) 1 — alias to suggestion 1" in out.getvalue()

    def test_menu_input_reprompts_on_garbage(self, monkeypatch) -> None:
        answers = iter(["not-a-verb", "3"])
        out = io.StringIO()
        ask = make_menu_input(QUOTES_SPEC, print_fn=out.write, input_fn=lambda _p: next(answers))
        assert ask() == "3"
        assert "unrecognized" in out.getvalue()

    def test_menu_input_confirm_passthrough(self, monkeypatch) -> None:
        ask = make_menu_input(QUOTES_SPEC, print_fn=lambda _s: None, input_fn=lambda _p: "n")
        assert ask("apply 3 decision row(s)? y/N: ") == "n"

    def test_menu_input_free_note(self) -> None:
        ask = make_menu_input(QUOTES_SPEC, print_fn=lambda _s: None, input_fn=lambda _p: "d|noise")
        assert ask() == "d|noise"

    def test_scripted_input_consumes(self) -> None:
        ask = make_scripted_input(["d|note1", "al X", "q"], QUOTES_SPEC)
        assert ask() == "d|note1"
        assert ask() == "al X"
        assert ask() == "q"

    def test_scripted_input_eof(self) -> None:
        ask = make_scripted_input(["d"], QUOTES_SPEC)
        assert ask() == "d"
        with pytest.raises(EOFError):
            ask()

    def test_scripted_strict_rejects_garbage(self) -> None:
        ask = make_scripted_input(["bogus"], QUOTES_SPEC, strict=True)
        with pytest.raises(ValueError):
            ask()

    def test_scripted_non_strict_passthrough(self) -> None:
        ask = make_scripted_input(["bogus"], QUOTES_SPEC, strict=False)
        assert ask() == "bogus"

    def test_scripted_confirm_line(self) -> None:
        ask = make_scripted_input(["d", "y"], QUOTES_SPEC)
        assert ask() == "d"
        assert ask("apply 1 decision row(s)? y/N: ") == "y"
