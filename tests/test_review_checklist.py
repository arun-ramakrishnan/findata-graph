"""S4 checklist-walker tests (review_menu_prompt_driven proposal)."""

from __future__ import annotations

import json

import pytest

from helpers.core.review_checklist import CHECKLISTS, build_entries, review


def _tap(tape):
    def _read(prompt=None):
        return tape.pop(0) if tape else "q"

    return _read


def test_surfaces_have_stable_ids_and_provenance():
    # every surface: unique ids, every item carries a procedure anchor
    for surface, spec in CHECKLISTS.items():
        ids = [item["id"] for item in spec["items"]]
        assert len(ids) == len(set(ids)), f"{surface}: duplicate item ids"
        assert all(item["section"] for item in spec["items"]), f"{surface}: missing provenance"
        assert spec["hints"], f"{surface}: missing post-batch hints"


def test_entries_shape():
    for surface in CHECKLISTS:
        entries = build_entries(surface)
        assert entries
        for e in entries:
            assert e["label"]
            assert any(line["kind"] == "provenance" for line in e["evidence"])


def test_y_n_p_walk_records_decisions(tmp_path):
    tape = ["y", "n|image thumbnails were 404s", "p", "y", "y", "y"]
    out = review(
        "markdown-parse",
        journal_dir=tmp_path / "j",
        decisions=tmp_path / "dec.jsonl",
        limit=5,
        input_fn=_tap(tape),
    )
    assert out["approved"] == 4
    lines = tmp_path.joinpath("dec.jsonl").read_text().splitlines()
    assert len(lines) == 4
    decisions = [json.loads(x) for x in lines]
    assert decisions[0] == {
        "surface": "markdown-parse",
        "item_id": "mdp-01",
        "decision": "done",
        "note": "",
    }
    assert decisions[1]["decision"] == "needs_attention"
    assert decisions[1]["note"] == "image thumbnails were 404s"
    # parked mdp-03 is skipped: not in the decisions file, journaled as skip
    assert decisions[2]["item_id"] == "mdp-04"
    assert [d["item_id"] for d in decisions] == ["mdp-01", "mdp-02", "mdp-04", "mdp-05"]
    journal = tmp_path.joinpath("j", "markdown-parse_checklist_review", "journal.jsonl").read_text()
    assert '"id": "mdp-03"' in journal and '"action": "skip"' in journal
    assert all(d["surface"] == "markdown-parse" for d in decisions)


def test_n_without_note_retries(tmp_path):
    tape = ["n", "n|needs a probe", "y", "y", "y"]
    out = review(
        "ocr",
        journal_dir=tmp_path / "j",
        decisions=tmp_path / "dec.jsonl",
        limit=3,
        input_fn=_tap(tape),
    )
    assert out["approved"] == 3
    lines = tmp_path.joinpath("dec.jsonl").read_text().splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0])["decision"] == "needs_attention"


def test_bad_verb_retries(tmp_path):
    tape = ["z", "y"]
    out = review(
        "ocr",
        journal_dir=tmp_path / "j",
        decisions=tmp_path / "dec.jsonl",
        limit=1,
        input_fn=_tap(tape),
    )
    assert out["approved"] == 1


def test_quit_abort_write_nothing(tmp_path):
    for q in ("q", "x"):
        tape = [q]
        out = review(
            "ocr",
            journal_dir=tmp_path / "j",
            decisions=tmp_path / "dec.jsonl",
            limit=2,
            input_fn=_tap(tape),
        )
        assert out["approved"] == 0
        assert not tmp_path.joinpath("dec.jsonl").exists()


def test_prompt_mode_packets_and_answers(tmp_path):
    items = build_entries("ocr")
    answers = ["y"] * len(items) + ["y"]
    answers_file = tmp_path / "answers.txt"
    answers_file.write_text("\n".join(answers) + "\n")
    packets = tmp_path / "packets.jsonl"
    out = review(
        "ocr",
        journal_dir=tmp_path / "j",
        decisions=tmp_path / "dec.jsonl",
        prompt=True,
        answers_path=answers_file,
        packets_out=packets,
    )
    assert out["approved"] == len(items)
    pkt_lines = packets.read_text().splitlines()
    assert len(pkt_lines) == len(items)
    first = json.loads(pkt_lines[0])
    assert first["schema"] == "review_menu/v1"
    assert first["surface"] == "ocr"
    assert first["item_id"] == "ocr-01"
    assert first["choices"][0]["key"] == "y"
    # idempotent serialization: same item -> identical packet
    again = json.loads(pkt_lines[0])
    assert again == first


def test_flags_mutually_exclusive(tmp_path):
    with pytest.raises(ValueError):
        review("ocr", menu=True, prompt=True)
    with pytest.raises(ValueError):
        review("ocr", prompt=True)


def test_unknown_surface():
    with pytest.raises(ValueError):
        review("nope")


def test_dry_run_journals_preview_no_park(tmp_path):
    tape = ["y", "p"]
    out = review(
        "ocr",
        journal_dir=tmp_path / "j",
        decisions=tmp_path / "dec.jsonl",
        limit=2,
        dry_run=True,
        input_fn=_tap(tape),
    )
    assert out["approved"] == 1
    # dry-run: no decision file written, journal keeps preview-* non-terminal
    assert not tmp_path.joinpath("dec.jsonl").exists()
    journal = tmp_path.joinpath("j", "ocr_checklist_review", "journal.jsonl").read_text()
    assert "preview-approve" in journal
    assert "preview-skip" in journal
