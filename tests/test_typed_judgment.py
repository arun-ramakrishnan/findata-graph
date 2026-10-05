"""Hermetic tests for helpers/core/typed_judgment.py — no network.

The transport is injected, so every path (including retries, cache and
spend cap) is exercised with a fake.
"""

from __future__ import annotations

import json

import pytest

from helpers.core import typed_judgment as tj


def _q() -> dict:
    return {
        "is_acq": tj.noul("Did A acquire B?", {"true": "acquired", "false": "not acquired"}),
        "kind": tj.choice("What kind of event?", {"acq": "acquisition", "jv": "joint venture"}),
        "intensity": tj.score("How strong?", ["weak", "medium", "strong"]),
    }


def _ok(usage: dict | None = None) -> tuple[int, dict]:
    return 200, {
        "answers": {
            "is_acq": {"type": "noul", "noul": 0.93},
            "kind": {"type": "choice", "choice": "acq", "probabilities": {"acq": 0.97, "jv": 0.03}},
            "intensity": {"type": "score", "score": "strong"},
        },
        "usage": usage or {"prompt_tokens": 120, "completion_tokens": 8},
    }


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "CACHE_DIR", tmp_path / "cache")


class TestQuestionShapes:
    def test_typed_builders_emit_api_shapes(self):
        q = _q()
        assert q["is_acq"] == {
            "type": "noul",
            "instructions": "Did A acquire B?",
            "criteria": {"true": "acquired", "false": "not acquired"},
        }
        assert q["kind"]["type"] == "choice"
        assert q["intensity"] == {
            "type": "score",
            "instructions": "How strong?",
            "criteria": ["weak", "medium", "strong"],
        }


class TestNormalizeAnswers:
    """Both lanes must land on {question: answer} or A/B silently loses a carrier."""

    def test_decisions_lane_passes_through(self):
        resp = {"answers": {"is_acq": {"type": "noul", "noul": 0.9}}}
        assert tj.normalize_answers("decisions", resp, _q()) == {
            "is_acq": {"type": "noul", "noul": 0.9}
        }

    def test_chat_lane_extracts_json_from_content(self):
        resp = {"choices": [{"message": {"content": 'sure:\n{"kind": {"choice": "acq"}}\n'}}]}
        assert tj.normalize_answers("chat", resp, _q()) == {"kind": {"choice": "acq"}}

    def test_chat_lane_drops_unknown_questions_and_junk(self):
        assert (
            tj.normalize_answers(
                "chat", {"choices": [{"message": {"content": '{"nope": 1}'}}]}, _q()
            )
            == {}
        )
        assert (
            tj.normalize_answers(
                "chat", {"choices": [{"message": {"content": "no json here"}}]}, _q()
            )
            == {}
        )
        assert tj.normalize_answers("chat", {"choices": []}, _q()) == {}
        assert tj.normalize_answers("chat", {}, _q()) == {}
        fenced = {"choices": [{"message": {"content": '```json\n{"is_acq": {"noul": 0.4}}\n```'}}]}
        assert tj.normalize_answers("chat", fenced, _q()) == {"is_acq": {"noul": 0.4}}


class TestAsk:
    def test_returns_typed_answers_with_telemetry(self):
        r = tj.ask("state text", _q(), "mercury-decide", key="k", transport=lambda *_: _ok())
        assert r.answers["is_acq"]["noul"] == 0.93
        assert r.answers["kind"]["choice"] == "acq"
        assert r.answers["intensity"]["score"] == "strong"
        assert r.prompt_tokens == 120 and r.completion_tokens == 8
        assert r.attempts == 1 and r.cached is False
        assert r.wall_s >= 0.0
        assert r.context_used_pct > 0

    def test_decisions_body_shape(self):
        seen: dict = {}

        def spy(url, body, timeout):
            seen.update({"url": url, "body": body})
            return _ok()

        tj.ask("S", _q(), "mercury-decide", key="k", transport=spy)
        assert seen["url"] == tj.DECISIONS_URL
        assert seen["body"]["model"] == "inception/mercury-decide:free"
        assert set(seen["body"]) == {"model", "state", "questions"}

    def test_chat_carrier_uses_chat_url(self):
        seen: dict = {}

        def spy(url, body, timeout):
            seen.update({"url": url, "body": body})
            return 200, {"usage": {"prompt_tokens": 5, "completion_tokens": 5}, "x": 1}

        tj.ask("S", _q(), "mercury-2", key="k", transport=spy)
        assert seen["url"] == tj.CHAT_URL
        assert seen["body"]["messages"][0]["role"] == "user"

    def test_unknown_model_id_passes_through(self):
        seen: dict = {}

        def spy(url, body, timeout):
            seen.update({"body": body})
            return _ok()

        tj.ask("S", _q(), "some/raw-model", key="k", transport=spy)
        assert seen["body"]["model"] == "some/raw-model"

    def test_missing_key_is_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(tj, "resolve_key", lambda: "")
        r = tj.ask("S", _q(), "mercury-decide", use_cache=False)
        assert r.answers == {} and r.attempts == 0


class TestRetryAndErrors:
    def test_retries_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(tj.time, "sleep", lambda *_: None)
        calls = {"n": 0}

        def flaky(url, body, timeout):
            calls["n"] += 1
            return (500, {"error": {"message": "boom"}}) if calls["n"] == 1 else _ok()

        r = tj.ask("S", _q(), "mercury-decide", key="k", transport=flaky, use_cache=False)
        assert calls["n"] == 2 and r.answers["is_acq"]["noul"] == 0.93

    def test_exhausted_retries_report_error(self, monkeypatch):
        monkeypatch.setattr(tj.time, "sleep", lambda *_: None)
        stats = tj.RunStats()
        r = tj.ask(
            "S",
            _q(),
            "mercury-decide",
            key="k",
            max_attempts=2,
            use_cache=False,
            transport=lambda *_: (429, {"error": {"message": "rate limited"}}),
            stats=stats,
        )
        assert r.answers == {} and r.error and "rate limited" in r.error
        assert r.attempts == 2
        assert stats.errors == 1 and stats.retries == 1

    def test_empty_answers_count_as_failure(self):
        r = tj.ask(
            "S",
            _q(),
            "mercury-decide",
            key="k",
            use_cache=False,
            transport=lambda *_: (200, {"answers": {}}),
        )
        assert r.answers == {}


class TestCache:
    def test_second_identical_call_is_a_cache_hit(self):
        n = {"n": 0}

        def counting(url, body, timeout):
            n["n"] += 1
            return _ok()

        a = tj.ask("S", _q(), "mercury-decide", key="k", transport=counting)
        stats = tj.RunStats()
        b = tj.ask("S", _q(), "mercury-decide", key="k", transport=counting, stats=stats)
        assert n["n"] == 1, "second call must not hit the network"
        assert b.cached is True and b.answers == a.answers
        assert stats.cache_hits == 1

    def test_no_cache_forces_the_call(self):
        n = {"n": 0}

        def counting(url, body, timeout):
            n["n"] += 1
            return _ok()

        tj.ask("S", _q(), "mercury-decide", key="k", transport=counting)
        tj.ask("S", _q(), "mercury-decide", key="k", transport=counting, use_cache=False)
        assert n["n"] == 2

    def test_different_question_text_is_a_different_key(self):
        n = {"n": 0}

        def counting(url, body, timeout):
            n["n"] += 1
            return _ok()

        tj.ask("S", _q(), "mercury-decide", key="k", transport=counting)
        other = {"is_acq": tj.noul("Did B acquire A?", {"true": "y", "false": "n"})}
        tj.ask("S", other, "mercury-decide", key="k", transport=counting)
        assert n["n"] == 2

    def test_cache_blob_carries_timestamp_and_carrier(self, tmp_path):
        tj.ask("S", _q(), "mercury-decide", key="k", transport=lambda *_: _ok())
        blobs = list((tj.CACHE_DIR).glob("*.json"))
        assert len(blobs) == 1
        blob = json.loads(blobs[0].read_text())
        assert blob["carrier"] == "mercury-decide" and blob["model"]
        assert blob["ts"]  # parsable timestamp, not decoration

    def test_empty_answers_never_cached(self):
        tj.ask(
            "S",
            _q(),
            "mercury-decide",
            key="k",
            transport=lambda *_: (200, {"answers": {}}),
        )
        assert list(tj.CACHE_DIR.glob("*.json")) == []

    def test_cache_age_days(self):
        assert tj.cache_age_days("mercury-decide", "S", _q()) is None
        tj.ask("S", _q(), "mercury-decide", key="k", transport=lambda *_: _ok())
        age = tj.cache_age_days("mercury-decide", "S", _q())
        assert age is not None and 0 <= age < 1
        assert tj.cache_age_days("mercury-decide", "S", _q(), use_cache=False) is None


class TestSpendCap:
    def test_cap_blocks_further_calls(self):
        stats = tj.RunStats(max_cents=1.0)
        stats.cents = 1.0
        r = tj.ask("S", _q(), "mercury-2", key="k", use_cache=False, max_cents=1.0, stats=stats)
        assert r.attempts == 0 and r.answers == {}

    def test_budget_flag_in_stats(self):
        s = tj.RunStats(max_cents=0.5)
        s.call(cents=0.5)
        assert s.as_dict()["budget_exceeded"] is True
        assert tj.RunStats(max_cents=0).as_dict()["budget_exceeded"] is False


class TestAb:
    @staticmethod
    def _chat(noul: float) -> tuple[int, dict]:
        content = json.dumps({"is_acq": {"noul": noul}})
        return 200, {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        }

    def test_agreement_on_matching_nouls(self):
        def transport(url, body, timeout):
            if body["model"].startswith("inception/mercury-decide"):
                return 200, {"answers": {"is_acq": {"noul": 0.93}}}
            return self._chat(0.90)

        v, ag = tj.ab(
            "S",
            {"is_acq": _q()["is_acq"]},
            ["mercury-decide", "mercury-2"],
            key="k",
            transport=transport,
        )
        assert ag["is_acq"]["agree"] is True
        assert set(ag["is_acq"]["values"]) == {"mercury-decide", "mercury-2"}
        assert all(x.ok for x in v.values())

    def test_disagreement_flagged_on_p_gap(self):
        def transport(url, body, timeout):
            if body["model"].startswith("inception/mercury-decide"):
                return 200, {"answers": {"is_acq": {"noul": 0.95}}}
            return self._chat(0.10)

        _, ag = tj.ab(
            "S",
            {"is_acq": _q()["is_acq"]},
            ["mercury-decide", "mercury-2"],
            key="k",
            transport=transport,
        )
        assert ag["is_acq"]["agree"] is False

    def test_disagreement_flagged_on_choice_argmax(self):
        def transport(url, body, timeout):
            if body["model"].startswith("inception/mercury-decide"):
                return 200, {"answers": {"kind": {"choice": "acq", "probabilities": {}}}}
            content = '{"kind": {"choice": "jv"}}'
            return 200, {"choices": [{"message": {"content": content}}]}

        _, ag = tj.ab(
            "S",
            {"kind": _q()["kind"]},
            ["mercury-decide", "mercury-2"],
            key="k",
            transport=transport,
        )
        assert ag["kind"]["agree"] is False
        assert ag["kind"]["values"] == {"mercury-decide": "acq", "mercury-2": "jv"}

    def test_one_broken_carrier_does_not_sink_the_other(self):
        def transport(url, body, timeout):
            if body["model"].startswith("inception/mercury-2"):
                return 400, {"error": {"message": "nope"}}
            return _ok()  # decisions lane

        v, _ = tj.ab(
            "S", _q(), ["mercury-decide", "mercury-2"], key="k", transport=transport, max_attempts=1
        )
        assert v["mercury-decide"].ok is True
        assert v["mercury-2"].ok is False and v["mercury-2"].error

    def test_single_survivor_is_unknown_not_agreement(self):
        def transport(url, body, timeout):
            if body["model"].startswith("inception/mercury-2"):
                return 400, {"error": {"message": "nope"}}
            return _ok()  # decisions lane

        _, ag = tj.ab(
            "S",
            {"is_acq": _q()["is_acq"]},
            ["mercury-decide", "mercury-2"],
            key="k",
            transport=transport,
            max_attempts=1,
        )
        assert ag["is_acq"]["agree"] is False
        assert ag["is_acq"].get("single_source") is True

    def test_single_carrier_ask_still_agrees_with_itself(self):
        _, ag = tj.ab(
            "S",
            {"is_acq": _q()["is_acq"]},
            ["mercury-decide"],
            key="k",
            transport=lambda *_: _ok(),
        )
        assert ag["is_acq"]["agree"] is True
        assert "single_source" not in ag["is_acq"]


class TestCli:
    def test_cli_appends_jsonl(self, tmp_path, monkeypatch, capsys):
        state = tmp_path / "state.txt"
        state.write_text("A acquired B for EUR 320 million.", encoding="utf-8")
        qf = tmp_path / "q.json"
        qf.write_text(json.dumps(_q()), encoding="utf-8")
        out = tmp_path / "runs.jsonl"
        monkeypatch.setattr(tj, "resolve_key", lambda: "k")
        monkeypatch.setattr(
            tj,
            "ab",
            lambda *a, **k: (
                {
                    "mercury-decide": tj.Verdict(
                        "mercury-decide", True, {"is_acq": {"noul": 0.9}}, 1.2, 3.0
                    )
                },
                {"is_acq": {"agree": True, "values": {"mercury-decide": 0.9}}},
            ),
        )
        rc = tj.main(["--state-file", str(state), "--questions-file", str(qf), "--out", str(out)])
        assert rc == 0
        line = json.loads(out.read_text().splitlines()[0])
        assert line["carriers"]["mercury-decide"]["is_acq"]["noul"] == 0.9
        assert "agreement" in line and "stats" in line
        assert "mercury-decide" in capsys.readouterr().out


class TestDecisionBriefs:
    """The human-facing surface: a question in plain English, not JSON."""

    def test_describe_answer_plain_english(self):
        assert tj.describe_answer({"noul": 0.93}) == "93% yes"
        assert tj.describe_answer({"noul": 0.07}) == "93% no"
        assert tj.describe_answer({"choice": "acq", "probabilities": {"acq": 0.97}}) == "acq (97%)"
        assert tj.describe_answer({"score": "strong"}) == "strong"
        assert tj.describe_answer({}) == "no answer"

    def test_gap_of_measures_disagreement(self):
        assert tj.gap_of({"a": {"noul": 0.9}, "b": {"noul": 0.85}}) == pytest.approx(0.05)
        assert tj.gap_of({"a": {"noul": 0.9}, "b": {"noul": 0.85}}) <= tj.AGREE_TOL  # agreeing
        assert tj.gap_of({"a": {"noul": 0.9}, "b": {"noul": 0.1}}) == 0.8
        assert tj.gap_of({"a": {"choice": "acq"}, "b": {"choice": "jv"}}) == 1.0
        assert tj.gap_of({"a": {"noul": 0.9}}) == 0.0  # single carrier: no gap

    def test_brief_leads_with_the_disagreement(self):
        card = tj.brief(
            "Should this edge be admitted to the graph?",
            {"mercury-decide": {"noul": 0.06}, "glm-5.3": {"noul": 0.94}},
            context="Note claims a $370m acquisition announced 2026-08-24.",
        )
        assert card.splitlines()[0] == "the carriers DISAGREE — you decide"
        assert "QUESTION: Should this edge be admitted" in card
        assert "mercury-decide: 94% no" in card
        assert "do not get a vote on the write" in card
        assert "{" not in card and "noul" not in card

    def test_brief_when_agreeing_says_so(self):
        card = tj.brief(
            "Is this a co-mention artifact?", {"a": {"noul": 0.02}, "b": {"noul": 0.05}}
        )
        assert card.splitlines()[0] == "the carriers AGREE"
        assert "DISAGREE" not in card

    def test_brief_single_survivor_renders_contested(self):
        card = tj.brief("Is this a co-mention artifact?", {"a": {"noul": 0.02}})
        assert card.splitlines()[0] == "the carriers DISAGREE — you decide"

    def test_rank_contentious_surfaces_only_contested(self):
        per_item = {
            "row-1": {"a": {"noul": 0.5}, "b": {"noul": 0.52}},  # agree (gap .02)
            "row-2": {"a": {"noul": 0.95}, "b": {"noul": 0.05}},  # max gap
            "row-3": {"a": {"noul": 0.9}, "b": {"noul": 0.6}},  # gap .3
            "row-4": {"a": {"noul": 0.9}, "b": {"noul": 0.88}},  # agree (gap .02)
        }
        top1 = tj.rank_contentious(per_item, top=1)
        assert [i for i, _ in top1] == ["row-2"]
        assert top1[0][1] == pytest.approx(0.9)
        assert tj.rank_contentious(per_item, top=5) == [
            ("row-2", pytest.approx(0.9)),
            ("row-3", pytest.approx(0.3)),
        ]


class TestS6SurfaceKeyRegistry:
    """S6 (system_one_typed_judgment_framework.md): a surface may SHOW
    carrier verdicts only when it has a labeled eval key — no key, no
    show. The registry makes the policy executable: a new adopting
    surface fails loudly (require_surface_key raises) until it files its
    key and registers it, in the same change that ships the question."""

    def test_relations_surface_is_registered(self):
        assert tj.surface_key_registered("relations_triage_queue")
        key = tj.require_surface_key("relations_triage_queue")
        assert key == "doc/local/evaluations/jev_pilot/dataset/eval3/items.json"

    def test_unregistered_surface_raises(self):
        with pytest.raises(KeyError, match="no key, no show"):
            tj.require_surface_key("derive_countries_worklist")


class TestAbPerCarrierKeys:
    """Mixed-registry A/B: carriers on different endpoints carry different
    keys (e.g. a Z.AI-plan chat carrier beside an OpenRouter decisions-lane
    carrier). `keys=` routes auth per carrier; `key=` stays the shared
    single-key form. Routing is observable through the Authorization header
    of the fake _post, in carrier order."""

    def test_keys_map_routes_per_carrier(self):
        calls = []

        def fake_post(url, headers, body, timeout):
            calls.append(headers.get("Authorization", ""))
            return _ok()

        orig = tj._post
        tj._post = fake_post
        try:
            verdicts, agreement = tj.ab(
                "state text",
                {"is_acq": tj.noul("Did A acquire B?", {"true": "acquired", "false": "no"})},
                ["glm-5.3", "mercury-decide"],
                keys={"glm-5.3": "zai-key", "mercury-decide": "or-key"},
                use_cache=False,
            )
        finally:
            tj._post = orig
        assert calls == ["Bearer zai-key", "Bearer or-key"]
        assert "mercury-decide" in verdicts and "glm-5.3" in verdicts
        assert agreement["is_acq"]["agree"] is True

    def test_shared_key_still_applies_to_every_carrier(self):
        calls = []

        def fake_post(url, headers, body, timeout):
            calls.append(headers.get("Authorization", ""))
            return _ok()

        orig = tj._post
        tj._post = fake_post
        try:
            tj.ab(
                "state text",
                {"is_acq": tj.noul("Did A acquire B?", {"true": "acquired", "false": "no"})},
                ["glm-5.3", "mercury-decide"],
                key="shared-key",
                use_cache=False,
            )
        finally:
            tj._post = orig
        assert calls == ["Bearer shared-key", "Bearer shared-key"]
