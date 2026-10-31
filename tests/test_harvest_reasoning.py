#!/usr/bin/env python3
"""Reasoning-capture tests — prime blocks, opencode model facet, empty-reasoning marker.

Synthetic fixtures only (no harness data enters tracked files).

Three defects, each invisible from the corpus side, are pinned here:

1. the prime lane read only a block's `text`, so a `thinking` block (reasoning
   lives under `thinking`) and a `toolCall` block were dropped — the lane
   archived 0 reasoning and 0 tool rows (measured 2026-10-18: 4,425 thinking
   blocks / 10,535,564 chars and 9,272 toolCall blocks are recoverable);
2. opencode's assistant rows carry `modelID`, not `model`, so the model facet
   was blank (129,430 of 167,274 corpus rows);
3. an OpenAI-Responses *encrypted* reasoning part arrives with an empty `text`
   and its evidence (`metadata.openai.reasoningEncryptedContent`) was thrown
   away, leaving 9,657 corpus rows that read as a harvest defect.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.maintenance import harvest_conversations as hc  # noqa: E402


def _prime_file(tmp_path: Path, *messages: dict) -> Path:
    f = tmp_path / "sess-aaa.jsonl"
    with open(f, "w") as fh:
        for m in messages:
            fh.write(json.dumps(m) + "\n")
    return f


def _msg(*blocks: dict, **kw: object) -> dict:
    rec = {
        "type": "message",
        "id": "m1",
        "timestamp": 1_760_000_000_000,
        "message": {
            "role": "assistant",
            "model": "dots-3-note-preview:free",
            "content": list(blocks),
        },
    }
    rec.update(kw)
    return rec


class TestPrimeBlocks:
    def test_thinking_block_becomes_a_reasoning_row(self) -> None:
        text, ptype, meta = hc._prime_block(
            {
                "type": "thinking",
                "thinking": "the user has not asked yet",
                "thinkingSignature": "reasoning",
            }
        )
        assert (text, ptype) == ("the user has not asked yet", "reasoning")
        assert meta == {"block_type": "thinking", "signature": "reasoning"}

    def test_redacted_thinking_keeps_its_text_and_redaction(self) -> None:
        text, ptype, meta = hc._prime_block(
            {
                "type": "redacted_thinking",
                "thinking": "redacted but present",
                "redacted": True,
            }
        )
        assert (text, ptype) == ("redacted but present", "reasoning")
        assert meta["redacted"] is True

    def test_tool_call_becomes_a_tool_row(self) -> None:
        text, ptype, meta = hc._prime_block(
            {
                "type": "toolCall",
                "id": "call_1",
                "name": "bash",
                "arguments": "{'command': 'ls'}",
            }
        )
        assert ptype == "tool"
        assert text == "{'command': 'ls'}"
        assert meta == {"block_type": "toolCall", "tool": "bash", "call_id": "call_1"}

    def test_tool_call_with_dict_arguments_is_serialised(self) -> None:
        text, ptype, _ = hc._prime_block(
            {"type": "toolCall", "name": "read", "arguments": {"path": "a.py"}}
        )
        assert ptype == "tool"
        assert json.loads(text) == {"path": "a.py"}

    def test_text_block_is_unchanged(self) -> None:
        assert hc._prime_block({"type": "text", "text": "hello"}) == (
            "hello",
            "text",
            {"block_type": "text"},
        )

    def test_harvest_file_keeps_reasoning_and_tools(self, tmp_path: Path) -> None:
        path = _prime_file(
            tmp_path,
            _msg(
                {
                    "type": "thinking",
                    "thinking": "why",
                    "thinkingSignature": "reasoning",
                },
                {"type": "text", "text": "answer"},
                {"type": "toolCall", "id": "c1", "name": "bash", "arguments": "ls"},
            ),
        )
        rows = hc._harvest_prime_file(path, "prime-snap:test")
        assert [r["part_type"] for r in rows] == ["reasoning", "text", "tool"]
        assert rows[0]["text"] == "why"
        assert rows[0]["model"] == "dots-3-note-preview:free"
        assert rows[2]["meta"] == '{"block_type": "toolCall", "tool": "bash", "call_id": "c1"}'


class TestOpencodeModelFacet:
    def test_v2_assistant_row_uses_model_id(self) -> None:
        assert hc._oc_msg_model({"modelID": "opencode/muse-spark-1.3-contributor-free"}) == (
            "opencode/muse-spark-1.3-contributor-free"
        )

    def test_legacy_nested_model_dict_still_resolves(self) -> None:
        m = hc._oc_msg_model(
            {"model": {"id": "opencode/space-bunny-free", "providerID": "opencode"}}
        )
        assert hc._oc_model_value(m) == "opencode/space-bunny-free"

    def test_missing_model_is_empty_not_none(self) -> None:
        assert hc._oc_msg_model({"role": "user"}) == ""

    def test_model_never_lands_as_a_dict(self) -> None:
        """A dict model in the parquet would split the search facet per row."""
        for row in ({"modelID": "a/b"}, {"model": {"id": "c/d"}}, {}, {"model": None}):
            assert isinstance(hc._oc_model_value(hc._oc_msg_model(row)), str)


class TestOpencodeReasoningMeta:
    def test_encrypted_reasoning_records_the_fact_not_the_ciphertext(self) -> None:
        d = {
            "type": "reasoning",
            "text": "",
            "metadata": {
                "openai": {
                    "itemId": "rs_1",
                    "reasoningEncryptedContent": "Q-PaDgF9GRNw4",
                }
            },
        }
        meta = hc._oc_reasoning_meta(d)
        assert meta == {"encrypted": True, "enc_bytes": 13, "item_id": "rs_1"}
        assert "Q-PaDgF9GRNw4" not in json.dumps(meta)

    def test_plaintext_reasoning_carries_no_marker(self) -> None:
        assert hc._oc_reasoning_meta({"type": "reasoning", "text": "real thinking"}) == {}

    def test_empty_reasoning_without_metadata_is_unmarked(self) -> None:
        assert hc._oc_reasoning_meta({"type": "reasoning", "text": ""}) == {}

    def test_part_rows_mark_the_encrypted_reasoning_row(self) -> None:
        data = json.dumps(
            {
                "type": "reasoning",
                "text": "",
                "metadata": {"openai": {"itemId": "rs_9", "reasoningEncryptedContent": "AAAA"}},
            }
        )
        parts = [("p1", "m1", "s1", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", data)]
        rows = hc._oc_part_rows(
            iter(parts),
            {"m1": ("assistant", "", "muse-spark-1.3-contributor-free")},
            "opencode:live",
        )
        assert rows[0]["part_type"] == "reasoning"
        assert rows[0]["text"] == ""
        assert json.loads(rows[0]["meta"]) == {
            "encrypted": True,
            "enc_bytes": 4,
            "item_id": "rs_9",
        }
        assert rows[0]["model"] == "muse-spark-1.3-contributor-free"


class TestForceLane:
    """`--force-lane` is what makes a parser fix reach the archive.

    Watermarks exist so a quiet source is not re-read; a parser change (the
    prime `thinking`/`toolCall` fix) therefore recovers nothing from an
    already-harvested source unless the lane's watermark is bypassed.
    """

    def _env(self, tmp_path, monkeypatch):
        (tmp_path / "nosnaps").mkdir(exist_ok=True)
        f = tmp_path / "prime" / "sess-a.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            json.dumps(
                _msg(
                    {
                        "type": "thinking",
                        "thinking": "why",
                        "thinkingSignature": "reasoning",
                    },
                    {"type": "text", "text": "answer"},
                )
            )
            + "\n"
        )
        monkeypatch.setattr(hc, "_prime_files", lambda: [(f, "prime:live")])
        monkeypatch.setattr(hc, "_opencode_sources", lambda: [])
        monkeypatch.setattr(hc, "_zcode_groups", lambda: {})
        monkeypatch.setattr(hc, "_dsh_home", lambda: tmp_path / "nodsh")
        monkeypatch.setattr(hc, "SNAP_ROOT", tmp_path / "nosnaps")
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home", raising=False)
        return tmp_path / "corpus", tmp_path / "c.duckdb"

    def test_quiet_source_skips_unless_forced(self, tmp_path, monkeypatch) -> None:
        corpus, db = self._env(tmp_path, monkeypatch)
        first = hc.harvest(corpus_root=corpus, db=db)
        assert "prime:live: quiet" not in first["lanes"]
        assert "prime:live: 2 parts" in first["lanes"]

        second = hc.harvest(corpus_root=corpus, db=db)
        assert "prime:live: quiet" in second["lanes"]

        forced = hc.harvest(corpus_root=corpus, db=db, force_lane="prime-rlm")
        assert "prime:live: quiet" not in forced["lanes"]
        assert "prime:live: 2 parts" in forced["lanes"]

    def test_forced_re_read_merges_instead_of_duplicating(self, tmp_path, monkeypatch) -> None:
        import pyarrow.parquet as pq

        corpus, db = self._env(tmp_path, monkeypatch)
        hc.harvest(corpus_root=corpus, db=db)
        hc.harvest(corpus_root=corpus, db=db, force_lane="prime-rlm")
        ids = [
            r["part_id"] for f in corpus.rglob("*.parquet") for r in pq.read_table(f).to_pylist()
        ]
        assert sorted(ids) == ["m1:0", "m1:1"]

    def test_other_lanes_are_not_forced(self, tmp_path, monkeypatch) -> None:
        corpus, db = self._env(tmp_path, monkeypatch)
        hc.harvest(corpus_root=corpus, db=db)
        out = hc.harvest(corpus_root=corpus, db=db, force_lane="zcode")
        assert "prime:live: quiet" in out["lanes"]
