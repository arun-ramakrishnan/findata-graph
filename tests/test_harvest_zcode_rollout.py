#!/usr/bin/env python3
"""zcode_conversation_ingest_repair S1+S2 tests — harvest-side fixtures.

Synthetic rollout fixtures only (no harness data enters tracked files).
Pins: dict-model records yield plain-string model rows (S1); a
responseId-less error record becomes a searchable error row instead of
being skipped (S2); full-kind requests are preferred for the
conversation and tail/delta fallbacks are marked partial in meta (S2).

Proposal: doc/improvements/proposals/zcode_conversation_ingest_repair.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.maintenance import harvest_conversations as hc  # noqa: E402


def _rec(**kw: object) -> dict:
    rec = {
        "type": "model_io",
        "requestId": "req-0001",
        "sessionId": "sess-aaa",
        "startedAt": "2026-10-06T12:00:00.000Z",
        "model": {"modelId": "GLM-5.3-Flash", "providerId": "account:zai-start-plan"},
        "request": {
            "messages": [{"role": "user", "content": "hello"}],
            "messageCount": 3,
            "messagesKind": "tail",
            "messageOffset": 1,
        },
        "response": {
            "finishReason": "stop",
            "text": "hi",
            "reasoningText": "because",
            "responseId": "resp-1",
            "modelId": "GLM-5.3-Flash",
        },
    }
    rec.update(kw)
    return rec


def _write_dir(tmp_path: Path, recs: list[dict]) -> list[Path]:
    d = tmp_path / "rollout"
    d.mkdir()
    f = d / "model-io-sess-aaa.jsonl"
    with open(f, "w") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    return [f]


def test_dict_model_yields_string_rows(tmp_path: Path) -> None:
    files = _write_dir(tmp_path, [_rec()])
    rows = hc._harvest_zcode_file(files, "s1", "live")
    assert rows, "no rows harvested"
    for r in rows:
        assert r["model"] == "GLM-5.3-Flash"  # string, never a dict
        assert not r["model"].startswith("{")


def test_model_falls_back_to_record_dict(tmp_path: Path) -> None:
    rec = _rec(response={"text": "x", "reasoningText": "", "responseId": "resp-2"})
    files = _write_dir(tmp_path, [rec])
    rows = hc._harvest_zcode_file(files, "s1", "live")
    assert all(r["model"] == "GLM-5.3-Flash" for r in rows)


def test_error_record_without_responseid_searchable(tmp_path: Path) -> None:
    rec = _rec(
        requestId="req-err",
        response={"toolCalls": []},
        error={"name": "TerminalStreamChunkError", "message": "exceed quota limit"},
    )
    files = _write_dir(tmp_path, [rec])
    rows = hc._harvest_zcode_file(files, "s1", "live")
    errs = [r for r in rows if r["part_type"] == "error"]
    assert len(errs) == 1
    assert errs[0]["part_id"] == "zc:s1:err:req-err"
    assert "TerminalStreamChunkError" in errs[0]["text"]
    assert "exceed quota limit" in errs[0]["text"]
    assert errs[0]["model"] == "GLM-5.3-Flash"  # record-dict fallback (no response.modelId)


def test_full_kind_preferred_over_larger_tail(tmp_path: Path) -> None:
    tail = _rec()  # messageCount 3, kind tail
    full = _rec(
        requestId="req-full",
        response={**tail["response"], "responseId": "resp-full"},
        request={
            "messages": [{"role": "user", "content": "whole conversation"}],
            "messageCount": 1,
            "messagesKind": "full",
            "messageOffset": 0,
        },
    )
    files = _write_dir(tmp_path, [tail, full])
    rows = hc._harvest_zcode_file(files, "s1", "live")
    req_rows = [r for r in rows if r["part_type"] == "req-msg"]
    assert len(req_rows) == 1
    assert req_rows[0]["text"] == "whole conversation"
    meta = json.loads(req_rows[0]["meta"])
    assert meta["contextKind"] == "full"
    assert "partial" not in meta


def test_tail_only_session_marked_partial(tmp_path: Path) -> None:
    files = _write_dir(tmp_path, [_rec()])
    rows = hc._harvest_zcode_file(files, "s1", "live")
    req_rows = [r for r in rows if r["part_type"] == "req-msg"]
    meta = json.loads(req_rows[0]["meta"])
    assert meta["contextKind"] == "tail"
    assert meta["partial"] is True


def test_model_value_shapes() -> None:
    assert hc._zc_model_value({"modelId": "GLM-5.3-Flash", "providerId": "p"}) == "GLM-5.3-Flash"
    assert hc._zc_model_value('{"modelId": "GLM-5.3-Flash", "providerId": "p"}') == "GLM-5.3-Flash"
    assert hc._zc_model_value("glm-4.5") == "glm-4.5"
    assert hc._zc_model_value('{"broken"') == '{"broken"'  # unparsable: kept verbatim
    assert hc._zc_model_value(None) == ""
    assert hc._zc_model_value({"noModelId": 1}) == ""
