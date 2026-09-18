#!/usr/bin/env python3
"""Review kit tests — journal convention, lane assembly, session spine.

The NIC client (tests/test_seed_nic2008.py) is the end-to-end harness;
these pin the KIT semantics S2-S4 rehomes will rely on (review_kit
proposal S1, follows completed.md #246).
"""

from __future__ import annotations

import json

from helpers.core.review_kit import Journal, ReviewSession, assemble_entries, latest_action_by


class TestJournal:
    def test_sitting_block_format_and_append_only(self, tmp_path):
        jp = tmp_path / "j.jsonl"
        j = Journal(jp, session="20260919_000000")
        j.start()
        j.write({"label": "Airlines", "action": "approve", "code": "51101"})
        j.end(approved=1)
        lines = [json.loads(x) for x in jp.read_text().splitlines()]
        assert [x["action"] for x in lines] == ["sitting-start", "approve", "sitting-end"]
        assert all(
            x["ts"] == "20260919_000000" and x["session"] == "20260919_000000" for x in lines
        )
        assert lines[1]["label"] == "Airlines" and lines[2]["approved"] == 1
        # second sitting appends, never rewrites
        j2 = Journal(jp, session="20260919_000001")
        j2.start()
        j2.end(approved=0)
        assert len(jp.read_text().splitlines()) == 5

    def test_latest_action_by_parking_read_back(self, tmp_path):
        jp = tmp_path / "j.jsonl"
        jp.write_text(
            json.dumps({"label": "A", "action": "approve"})
            + "\n"
            + json.dumps({"label": "B", "action": "skip"})
            + "\n"
            + json.dumps({"label": "A", "action": "bad-override"})
            + "\n"
        )
        assert latest_action_by(jp, key_field="label") == {"A": "approve", "B": "skip"}
        assert latest_action_by(tmp_path / "missing.jsonl", key_field="label") == {}


def _wl():
    return {
        "suggested": [{"label": "Open1", "suggestions": [{"score": 0.5}]}],
        "promoted": [{"label": "Done1", "suggestions": []}],
        "skipped": [{"label": "Parked1", "suggestions": []}],
        "no_signal": [{"label": "NoSig1"}],
    }


def _key(e):
    return (-(e["suggestions"][0]["score"] if e.get("suggestions") else 0), e["label"])


class TestParkItems:
    def test_park_sitting_block(self, tmp_path):
        from helpers.core.review_kit import park_items

        jp = tmp_path / "p" / "journal.jsonl"
        assert park_items(jp, [], key_field="name") == 0  # no-op on empty
        assert park_items(jp, ["Alpha", "Beta"], key_field="name") == 2
        lines = [json.loads(x) for x in jp.read_text().splitlines()]
        assert [x["action"] for x in lines] == ["sitting-start", "skip", "skip", "sitting-end"]
        assert lines[1]["name"] == "Alpha" and lines[2]["name"] == "Beta"
        assert latest_action_by(jp, key_field="name") == {"Alpha": "skip", "Beta": "skip"}


class TestAssembleEntries:
    def test_default_walk_is_open_only(self):
        assert [
            e["label"]
            for e in assemble_entries(
                _wl(), labels_filter=None, redecide=False, skipped=False, sort_key=_key
            )
        ] == ["Open1"]

    def test_redecide_and_skipped_open_lanes(self):
        got = assemble_entries(
            _wl(), labels_filter=None, redecide=True, skipped=True, sort_key=_key
        )
        assert {e["label"] for e in got} == {"Open1", "Done1", "Parked1"}

    def test_labels_pull_named_without_opening_lanes(self):
        got = assemble_entries(
            _wl(),
            labels_filter={"Done1", "Parked1", "NoSig1"},
            redecide=False,
            skipped=False,
            sort_key=_key,
        )
        assert {e["label"] for e in got} == {"Done1", "Parked1", "NoSig1"}

    def test_limit_caps_after_sort(self):
        got = assemble_entries(
            _wl(), labels_filter=None, redecide=False, skipped=False, sort_key=_key, limit=0
        )
        # limit=0 is falsy -> no cap (matches original `if limit:` semantics)
        assert len(got) == 1


class TestReviewSession:
    def _run(self, answers, entries, tmp_path, apply=True, apply_batch=None):
        out = []
        calls = {}

        def ask(e, note):
            return {
                "label": e["label"],
                "action": "approve",
                "code": "51101",
                "match_type": "closeMatch",
            }

        def apply_batch_(specs, decisions):
            calls["specs"], calls["decisions"] = specs, decisions
            return [f"did {s}" for s in specs], []

        sess = ReviewSession(
            entries=entries,
            journal=Journal(tmp_path / "j.jsonl", session="s1"),
            render=lambda i, t, e: out.append(f"[{i}/{t}] {e['label']}"),
            ask=ask,
            spec_of=lambda d, e: f"industry:{d['label']}->{d['code']}",
            apply_batch=apply_batch or apply_batch_,
            input_fn=lambda _p: next(iter(answers)),
            print_fn=out.append,
            apply_flag=apply,
        )
        return sess.run(), out, calls

    def test_approve_confirm_apply_report(self, tmp_path):
        rep, out, calls = self._run(["y"], [{"label": "A"}], tmp_path)
        assert rep == {
            "decisions": 1,
            "approved": 1,
            "applied": 1,
            "journal": str(tmp_path / "j.jsonl"),
            "errors": [],
        }
        assert calls["specs"] == ["industry:A->51101"]
        assert any("batch to promote" in ln for ln in out)
        assert any(ln.strip() == "+ did industry:A->51101" for ln in out)

    def test_decline_keeps_journal_applies_nothing(self, tmp_path):
        rep, out, _ = self._run(["n"], [{"label": "A"}], tmp_path)
        assert rep["applied"] == 0 and "not confirmed — nothing applied (journal kept)" in out
        assert (tmp_path / "j.jsonl").exists()  # journal kept

    def test_dry_run_never_prompts_after_batch(self, tmp_path):
        prompts = []

        def ask(e, note):
            return {
                "label": e["label"],
                "action": "approve",
                "code": "x",
                "match_type": "closeMatch",
            }

        sess = ReviewSession(
            entries=[{"label": "A"}],
            journal=Journal(tmp_path / "j.jsonl", session="s1"),
            render=lambda *_: None,
            ask=ask,
            spec_of=lambda d, e: d["label"],
            apply_batch=lambda s, d: ([], []),
            input_fn=lambda p: prompts.append(p) or "y",
            print_fn=lambda *_: None,
            apply_flag=False,
        )
        rep = sess.run()
        assert prompts == [] and rep["applied"] == 0  # no confirm prompt on dry-run

    def test_note_lines_journal_before_approve_and_post_after(self, tmp_path):
        order = []

        def ask(e, note):
            note({"label": "A", "action": "bad-override", "code": "99999"})
            order.append("ask")
            return {"label": "A", "action": "approve", "code": "51101", "match_type": "closeMatch"}

        sess = ReviewSession(
            entries=[{"label": "A"}],
            journal=Journal(tmp_path / "j.jsonl", session="s1"),
            render=lambda *_: None,
            ask=ask,
            spec_of=lambda d, e: "spec",
            apply_batch=lambda s, d: ([], []),
            post_approve=lambda d, e: [
                {"label": "A", "action": "supersede-previous", "code": "00000"}
            ],
            input_fn=lambda _p: "y",
            print_fn=lambda *_: None,
        )
        sess.run()
        lines = [json.loads(x) for x in (tmp_path / "j.jsonl").read_text().splitlines()]
        acts = [x["action"] for x in lines]
        assert acts == [
            "sitting-start",
            "bad-override",
            "approve",
            "supersede-previous",
            "sitting-end",
        ]

    def test_dry_run_journals_preview_actions_never_parks(self, tmp_path):
        def ask(e, note):
            return {"id": e["label"], "action": "approve", "code": "x"}

        sess = ReviewSession(
            entries=[{"label": "A"}],
            journal=Journal(tmp_path / "j.jsonl", session="s1"),
            render=lambda *_: None,
            ask=ask,
            spec_of=lambda d, e: "spec",
            apply_batch=lambda s, d: ([], []),
            input_fn=lambda _p: "y",
            print_fn=lambda *_: None,
            apply_flag=False,
        )
        sess.run()
        lines = [json.loads(x) for x in (tmp_path / "j.jsonl").read_text().splitlines()]
        assert lines[1]["action"] == "preview-approve"  # non-terminal
        assert latest_action_by(tmp_path / "j.jsonl", key_field="id") == {}

    def test_abort_stops_walk_journals_tail(self, tmp_path):
        out = []
        entries = [{"label": "A"}, {"label": "B"}]

        def ask(e, note):
            return {"label": e["label"], "action": "abort"} if e["label"] == "A" else {}

        sess = ReviewSession(
            entries=entries,
            journal=Journal(tmp_path / "j.jsonl", session="s1"),
            render=lambda *_: None,
            ask=ask,
            spec_of=lambda d, e: "spec",
            apply_batch=lambda s, d: ([], []),
            input_fn=lambda _p: "n",
            print_fn=out.append,
        )
        rep = sess.run()
        assert rep["approved"] == 0 and "aborted — nothing applied" in out
        lines = [json.loads(x) for x in (tmp_path / "j.jsonl").read_text().splitlines()]
        assert [x["action"] for x in lines] == ["sitting-start", "abort", "sitting-end"]
        assert lines[2]["approved"] == 0
