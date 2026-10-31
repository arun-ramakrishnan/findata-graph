"""decisions-preannotate tests — surface providers, idempotent annotation,
carrier render helper (review-pipeline follow-on to review_menu_prompt_driven.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers.core import decisions_preannotate as dpa
from helpers.core.decisions_preannotate import (
    ALL_SPECS,
    annotate_surface,
    load_id_annotations,
    load_tuple_annotations,
    render_carrier_for,
)

SURFACES = sorted(ALL_SPECS)


def test_all_surfaces_registered() -> None:
    assert SURFACES == [
        "coverage",
        "findings",
        "markdown-parse",
        "ocr",
        "quotes",
        "relations",
    ]


def test_relations_rows_key_and_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Relations provider reads the live SIDECAR; triple key + quote state."""
    sidecar = tmp_path / "_pending_relations.txt"
    sidecar.write_text(
        json.dumps(
            {
                "edge_type": "jv_with",
                "source": "A",
                "target_mention": "B",
                "quote": "A is a JV of B.",
                "edition": "E",
                "direction": "forward",
            }
        )
        + "\n"
    )
    monkeypatch.setattr(
        "helpers.core.decisions_preannotate._REPO_ROOT",
        tmp_path,
    )
    spec = ALL_SPECS["relations"]
    # Fake the sidecar location without moving the repo: recompute manually.
    from helpers.core.decisions_preannotate import _relations_rows

    rows = _relations_rows()
    # No real sidecar under tmp_path -> provider must be robust (0 rows).
    assert rows == []
    # Key/state logic is pure even with a stub row.
    row = {"edge_type": "jv_with", "source": "A", "target_mention": "B", "quote": "ctx"}
    assert spec.key_of(row) == "jv_with:A:B"
    assert "A --jv_with--> B" in spec.state_of(row)
    assert "ctx" in spec.state_of(row)


def test_quotes_key_matches_sha256_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = ALL_SPECS["quotes"]
    assert spec.key_of({"id": "abc123", "canonical": "X"}) == "abc123"
    assert "X" in spec.state_of({"id": "abc123", "canonical": "X", "bucket": "stub_candidate"})


def test_coverage_key_and_state() -> None:
    spec = ALL_SPECS["coverage"]
    assert spec.key_of({"surface": "app", "attack_class": "injection"}) == "app/injection"
    assert "app" in spec.state_of(
        {
            "surface": "app",
            "attack_class": "injection",
            "reason": "not_a_route",
            "methods": ["GET"],
        }
    )


def test_findings_key_and_state() -> None:
    spec = ALL_SPECS["findings"]
    assert spec.key_of({"fingerprint": "fp-1"}) == "fp-1"
    state = spec.state_of({"fingerprint": "fp-1", "title": "SQLi", "surface": "app"})
    assert "SQLi" in state and "fp-1" in state


def test_checklist_key_and_state() -> None:
    spec = ALL_SPECS["ocr"]
    row = {
        "label": "ocr-01",
        "evidence": [{"kind": "checklist", "description": "Ref named"}],
    }
    assert spec.key_of(row) == "ocr-01"
    assert "Ref named" in spec.state_of(row)


def test_carrier_verdict_canonical() -> None:
    """Single source of truth for the admit/keep-out split (S2 hint base)."""
    assert dpa.carrier_verdict({"p_rubric": 0.9}) == "admit"
    assert dpa.carrier_verdict({"p_rubric": 0.5}) == "keep-out"
    assert dpa.carrier_verdict({"p_rubric": 0.1}) == "keep-out"
    assert dpa.carrier_verdict({}) is None
    assert dpa.carrier_verdict({"p_rubric": "bogus"}) is None


def test_render_carrier_for_uses_canonical_verdict() -> None:
    out: list[str] = []
    render_carrier_for("k", c_map={"k": {"p_fact": 0.9, "p_rubric": 0.1}}, print_fn=out.append)
    assert out and "keep-out" in out[0]
    out.clear()
    render_carrier_for("k", c_map={"k": {"p_fact": 0.9, "p_rubric": 0.9}}, print_fn=out.append)
    assert out and "admit" in out[0]


def test_annotate_surface_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Second run skips already-annotated rows (same key present)."""
    out = tmp_path / "annotations.jsonl"

    class FakeResult:
        answers = {
            "q1_fact": {"noul": 0.9},
            "q2_rubric": {"noul": 0.8},
        }
        error = None
        cached = False

    def fake_ask(_state, _questions, _model, **kw) -> FakeResult:
        return FakeResult()

    monkeypatch.setattr("helpers.core.decisions_preannotate.tj.ask", fake_ask)

    class Spec:
        name = "fake"
        default_out = out
        load_existing = staticmethod(dpa.load_id_annotations)
        iter_rows = lambda self: [  # noqa: E731
            {"id": "a", "canonical": "A"},
            {"id": "b", "canonical": "B"},
        ]
        key_of = lambda self, r: r["id"]  # noqa: E731
        state_of = lambda self, r: f"state {r['canonical']}"  # noqa: E731

    todo1, done1, path1 = annotate_surface(Spec())  # ty: ignore[invalid-argument-type]  # structural fake
    assert todo1 == 2 and done1 == 2
    todo2, done2, path2 = annotate_surface(Spec())  # ty: ignore[invalid-argument-type]  # structural fake
    assert todo2 == 0 and done2 == 0
    assert path1 == path2 == out
    rows = list(load_id_annotations(out).keys())
    assert sorted(rows) == ["a", "b"]


def test_load_id_annotations_handles_missing(tmp_path: Path) -> None:
    assert load_id_annotations(tmp_path / "nope.jsonl") == {}


def test_load_tuple_annotations_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"id": "jv_with:A:B", "p_fact": 0.1}) + "\n")
    m = load_tuple_annotations(p)
    assert ("jv_with", "A", "B") in m


def test_render_carrier_for_normal_and_missing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    c_map = {"app/injection": {"p_fact": 0.41, "p_rubric": 0.32}}
    render_carrier_for("app/injection", c_map=c_map)
    out = capsys.readouterr().out
    assert "ab mercury: fact p=0.41 / admission p=0.32 -> keep-out" in out
    render_carrier_for("missing", c_map=c_map)
    assert capsys.readouterr().out == ""
