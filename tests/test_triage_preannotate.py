"""Tests for helpers/graph/triage_preannotate.py — advisory pre-annotation
for the triage queue (triage_preannotation_escalation proposal). Hermetic:
judge + web search monkeypatched, tmp queue/annotation files.
"""

import json

import pytest

from helpers.graph import triage_preannotate as tp


def _qrow(edge="acquired", source="Acme Corp", target="Beta Ltd", quote="Acme acquired Beta.", edition="2026-01-15"):
    return json.dumps(
        {
            "edge_type": edge,
            "source": source,
            "target_mention": target,
            "quote": quote,
            "edition": edition,
        }
    )


class TestParseRows:
    def test_dedupe_skips_suggested_and_unparseable(self):
        lines = [
            _qrow(),
            _qrow(),  # exact dupe
            json.dumps({"edge_type": "suggested", "source": "Acme Corp", "target_mention": "Acme", "quote": ""}),
            "not json at all",
            "",
        ]
        rows = tp.parse_rows(lines)
        assert len(rows) == 1
        assert rows[0]["id"] == "acquired:Acme Corp:Beta Ltd"
        assert rows[0]["quote"] == "Acme acquired Beta."  # full quote, untruncated

    def test_different_target_not_deduped(self):
        rows = tp.parse_rows([_qrow(), _qrow(target="Gamma Ltd")])
        assert len(rows) == 2


class TestLoadAnnotations:
    def test_keys_by_row_triple_skips_junk(self, tmp_path):
        p = tmp_path / "ann.jsonl"
        p.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "id": "acquired:Acme Corp:Beta Ltd",
                            "p_fact": 0.85,
                            "factually_accurate": True,
                            "rubric_admit": False,
                            "p_rubric": 0.30,
                        }
                    ),
                    json.dumps({"id": "no-triple"}),  # dropped: not 3 parts
                    "not json",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        ann = tp.load_annotations(p)
        assert set(ann) == {("acquired", "Acme Corp", "Beta Ltd")}
        assert ann[("acquired", "Acme Corp", "Beta Ltd")]["p_fact"] == 0.85

    def test_missing_file_is_empty(self, tmp_path):
        assert tp.load_annotations(tmp_path / "nope.jsonl") == {}


class TestFreshFlag:
    def test_recent_edition_sets_flag(self):
        assert tp.fresh_flag({"edition": "2026-09-30"})

    def test_old_edition_clear(self):
        assert not tp.fresh_flag({"edition": "2020-01-01"})

    def test_edition_without_date(self):
        assert not tp.edition_is_recent("edition-42")

    def test_edition_malformed_date(self):
        assert not tp.edition_is_recent("2026-13-99")


class TestJudgeRows:
    def _row(self, target="Beta Ltd", quote="Acme acquired Beta."):
        return tp.parse_rows([_qrow(target=target, quote=quote)])[0]

    def test_batch_prompt_booleans_verbatim(self, monkeypatch):
        seen = {}

        def fake_call(prompt, key, model):
            seen["prompt"] = prompt
            # confidence-in-boolean semantics: confident REJECT (false/0.98)
            return '[{"id":1,"factually_accurate":false,"p_fact":0.98,"rubric_admit":false,"p_rubric":0.9}]'

        monkeypatch.setattr(tp, "call_glm", fake_call)
        a = tp.judge_rows([self._row()], "k", "m")[self._row()["id"]]
        assert "Acme acquired Beta." in seen["prompt"]
        assert "specific named company" in seen["prompt"]  # counterparty rule pinned
        assert "Return ONLY a JSON array" in seen["prompt"]
        # booleans verbatim — deriving from p (>=0.5) would invert this reject
        assert not a["factually_accurate"] and not a["rubric_admit"]
        assert a["p_fact"] == 0.98 and a["context_chars"] == len("Acme acquired Beta.")

    def test_decode_dropout_row_absent(self, monkeypatch):
        monkeypatch.setattr(tp, "call_glm", lambda p, k, m: "[]")
        assert tp.judge_rows([self._row()], "k", "m") == {}

    def test_judge_row_raises_on_dropout(self, monkeypatch):
        monkeypatch.setattr(tp, "call_glm", lambda p, k, m: "[]")
        with pytest.raises(ValueError):
            tp.judge_row(self._row(), "k", "m")

    def test_needs_escalation_gate(self):
        assert tp.needs_escalation({"p_fact": 0.3})
        assert not tp.needs_escalation({"p_fact": 0.8})


class TestEscalate:
    def _row(self):
        return tp.parse_rows([_qrow()])[0]

    def test_batch_flip_requires_quote_and_url(self, monkeypatch):
        monkeypatch.setattr(tp, "web_search", lambda q, retries=1: [{"title": "t", "url": "https://x/n", "snippet": "s"}])
        for reply, expect in (
            (
                '[{"id":1,"factually_accurate":true,"p_fact":0.95,"support_quote":"Acme buys Beta","evidence_url":"https://x/n"}]',
                True,
            ),
            ('[{"id":1,"factually_accurate":true,"p_fact":0.95,"support_quote":"","evidence_url":""}]', False),
        ):
            monkeypatch.setattr(tp, "call_glm", lambda p, k, m, reply=reply: reply)
            a = {"p_fact": 0.2, "factually_accurate": False, "rubric_admit": True, "p_rubric": 0.9}
            n = tp.escalate_rows([self._row()], {self._row()["id"]: a}, "k", "m")
            assert n == 1 and a["escalated"]
            assert a["factually_accurate"] is expect
            assert (a["evidence_url"] == "https://x/n") is expect
            # rubric clause (d) is an AND with the fact: quoted true keeps the
            # admit; an unquoted flip changes nothing (rubric stays at prior)
            assert a["rubric_admit"] is True

    def test_batch_one_evidence_call_for_many_rows(self, monkeypatch):
        calls = []
        monkeypatch.setattr(tp, "web_search", lambda q, retries=1: [{"title": "t", "url": "https://x/n", "snippet": "s"}])
        monkeypatch.setattr(
            tp,
            "call_glm",
            lambda p, k, m: calls.append(p)
            or '[{"id":1,"factually_accurate":false,"p_fact":0.4,"support_quote":"","evidence_url":""},'
            '{"id":2,"factually_accurate":false,"p_fact":0.4,"support_quote":"","evidence_url":""}]',
        )
        rows = [self._row(), tp.parse_rows([_qrow(target="Gamma Ltd")])[0]]
        anns = {
            rows[0]["id"]: {"p_fact": 0.2, "factually_accurate": False, "rubric_admit": False, "p_rubric": 0.9},
            rows[1]["id"]: {"p_fact": 0.3, "factually_accurate": False, "rubric_admit": False, "p_rubric": 0.9},
        }
        assert tp.escalate_rows(rows, anns, "k", "m") == 2
        assert len(calls) == 1 and calls[0].count("retrieved results:") == 2

    def test_search_failure_marks_needs_retry_no_judge_call(self, monkeypatch):
        def boom(q, retries=1):
            raise RuntimeError("web search failed")

        monkeypatch.setattr(tp, "web_search", boom)
        monkeypatch.setattr(tp, "call_glm", lambda *a: pytest.fail("no evidence call when search failed"))
        row = self._row()
        a = {"p_fact": 0.2, "factually_accurate": False, "rubric_admit": False, "p_rubric": 0.9}
        tp.escalate_rows([row], {row["id"]: a}, "k", "m")
        assert a["needs_retry"] and not a["factually_accurate"]


class TestSearchParsing:
    def test_ddg_extract_and_redirect_unwrap(self):
        html = (
            '<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fnews.example%2Fa&amp;rut=1">Acme buys Beta</a>'
            '<a class="result__snippet" href="#">Acme completed the acquisition of Beta</a>'
        )
        out = tp._parse_ddg(html)
        assert out[0]["url"] == "https://news.example/a"
        assert "acquisition of Beta" in out[0]["snippet"]

    def test_ddg_captcha_page_yields_empty(self):
        assert tp._parse_ddg("<html>anomaly challenge captcha</html>") == []

    def test_bing_extract_and_redirect_unwrap(self):
        b64 = "aHR0cHM6Ly93d3cudGNzLmNvbS8"  # https://www.tcs.com/
        html = (
            '<li class="b_algo" data-id iid=SERP.1><h2 class=""><a target="_blank" '
            f'href="https://www.bing.com/ck/a?!&&p=abc&amp;u=a1{b64}&amp;ntb=1" h="ID=SERP">T</a></h2>'
            '<div class="b_caption"><p class="b_lineclamp2">Acme acquired Beta Ltd</p></div></li>'
        )
        out = tp._parse_bing(html)
        assert out[0]["url"] == "https://www.tcs.com/"
        assert "Beta" in out[0]["snippet"]


class TestRunCli:
    def test_end_to_end_mocked_writes_only_annotations(self, tmp_path, monkeypatch):
        queue = tmp_path / "q.jsonl"
        queue.write_text(_qrow() + "\n" + _qrow(source="Acme Corp", target="Gamma Ltd", quote="Acme and Gamma.", edition="2026-09-20") + "\n")
        out = tmp_path / "ann.jsonl"

        replies = iter(
            [
                '[{"id":1,"factually_accurate":true,"p_fact":0.9,"rubric_admit":true,"p_rubric":0.9},'
                '{"id":2,"factually_accurate":false,"p_fact":0.2,"rubric_admit":true,"p_rubric":0.9}]',
                # escalation re-judge (batched): quoted evidence confirms the fact
                '[{"id":1,"factually_accurate":true,"p_fact":0.95,"support_quote":"q","evidence_url":"https://x/n"}]',
            ]
        )
        monkeypatch.setattr(tp, "call_glm", lambda p, k, m: next(replies))
        monkeypatch.setattr(tp, "web_search", lambda q, retries=1: [{"title": "t", "url": "https://x/n", "snippet": "s"}])
        monkeypatch.setattr(tp, "load_memory_env", lambda: None)
        monkeypatch.setattr(tp, "require_env", lambda name, what="": "test-key")
        assert tp.run(["--queue", str(queue), "--out", str(out)]) == 0
        rows = [json.loads(x) for x in out.read_text().splitlines()]
        assert len(rows) == 2 and rows[0]["rubric_admit"]
        # low-confidence fact (p 0.2) escalated; evidence confirmed -> admit survives
        assert rows[1]["escalated"] and rows[1]["rubric_admit"]
        assert sorted(p.name for p in tmp_path.iterdir()) == ["ann.jsonl", "q.jsonl"]

    def test_empty_queue(self, tmp_path, capsys):
        assert tp.run(["--queue", str(tmp_path / "missing.txt"), "--out", str(tmp_path / "a.jsonl")]) == 0
        assert "no candidate rows" in capsys.readouterr().out
