"""
Tests for helpers/graph/triage_pending_relations.py — the encapsulated
_pending_relations queue workflow (pending_relations_triage proposal).
Hermetic: tmp sidecar/decisions files, monkeypatched entity names.
"""

import json
import sqlite3
from pathlib import Path

import pytest


from helpers.graph import triage_pending_relations as tpr  # noqa: E402
from helpers.graph import extract_relations as xr  # noqa: E402

NAMES = {
    "Acme Corp",
    "Colgate Palmolive India",
    "Dixon Technologies",
    "Graphite India",
    "Jupiter Wagons",
}


def _row(edge, source, target, quote="q", edition="ed"):
    return json.dumps(
        {
            "edge_type": edge,
            "source": source,
            "target_mention": target,
            "quote": quote,
            "edition": edition,
        }
    )


@pytest.fixture
def paths(tmp_path, monkeypatch):
    sidecar = tmp_path / "_pending_relations.txt"
    monkeypatch.setattr(tpr, "SIDECAR", sidecar)
    monkeypatch.setattr(tpr, "SUGGESTIONS", tmp_path / "_pending_suggestions.txt")
    monkeypatch.setattr(tpr, "ALIAS_FILE", tmp_path / "relation_aliases.json")
    monkeypatch.setattr(tpr, "REPORT", tmp_path / "report.md")
    monkeypatch.setattr(tpr, "DECISIONS", tmp_path / "decisions.jsonl")
    monkeypatch.setattr(tpr, "NOISE_FILE", tmp_path / "relation_noise.json")
    monkeypatch.setattr(tpr, "load_entity_names", lambda: set(NAMES))
    return sidecar


class TestNoiseTarget:
    def test_countries_generic_fragments(self):
        for t in ["Japan", "Germany", "Ecuador", "Oman", "US"]:
            assert tpr.noise_target(t), t
        for t in [
            "Vendor Partners",
            "EPC contractors but",
            "Army",
            "Electric Arc Furnace operators",
            "Fortune 500 firms",
            "CDMO",
            "ABC",
        ]:
            assert tpr.noise_target(t), t

    def test_real_names_pass(self):
        for t in [
            "HKC Display",
            "Kubota Corporation",
            "BharatNet",
            "Heidelberg Materials South Asia B.V.",
        ]:
            assert not tpr.noise_target(t), t


class TestBuildTriage:
    def test_split_dedupe_and_buckets(self):
        lines = [
            _row("suggested", "Acme Corp", "Dixon Technologies"),
            _row("suggested", "Acme Corp", "Dixon Technologies"),  # dupe
            _row("supplier_to", "Graphite India", "Japan"),
            _row("supplier_to", "Graphite India", "Electric Arc Furnace operators"),
            _row("supplier_to", "Jupiter Wagons", "Indian Railways"),
            _row("jv_with", "Acme Corp", "Kubota Corporation"),
            _row("jv_with", "Acme Corp", "Brookfield effectively"),
            _row("supplier_to", "Colgate Palmolive India", "Colgate-Palmolive Company"),
            "not json at all",
        ]
        triage = tpr.build_triage(lines, NAMES)
        assert len(triage["suggested"]) == 1
        assert triage["dupes"] == 1
        assert len(triage["unparseable"]) == 1
        buckets = {r["target_mention"]: r["bucket"] for r in triage["prose"]}
        assert buckets["Japan"] == "discard"
        assert buckets["Electric Arc Furnace operators"] == "discard"
        assert buckets["Indian Railways"] == "discard"  # generic list
        assert buckets["Brookfield effectively"] == "discard"
        assert buckets["Kubota Corporation"] == "stub_candidate"
        assert buckets["Colgate-Palmolive Company"] == "alias_candidate"

    def test_unknown_source_flagged(self):
        triage = tpr.build_triage([_row("supplier_to", "Ghost Co", "Kubota Corporation")], NAMES)
        assert triage["prose"][0]["bucket"] == "bad_source"

    def test_word_overlap_alias_flag(self):
        """G2 guard: alias_candidates resolved by word_overlap carry the
        confirm marker; non-alias rows never do.

        ``Colgate-Palmolive Company`` resolves to ``Colgate Palmolive
        India`` via word_overlap (the known false-positive family: shared
        tokens say "same sector", not "same company" — 20 Microns,
        Sailing_the_Tide, Circle, American_Express are the live examples).
        """
        triage = tpr.build_triage(
            [_row("supplier_to", "Graphite India", "Colgate-Palmolive Company")],
            NAMES,
        )
        row = triage["prose"][0]
        assert row["bucket"] == "alias_candidate"
        assert row["word_overlap"] is True

    def test_non_alias_rows_not_flagged(self):
        triage = tpr.build_triage(
            [
                _row("supplier_to", "Graphite India", "Japan"),
                _row("jv_with", "Acme Corp", "Kubota Corporation"),
            ],
            NAMES,
        )
        for r in triage["prose"]:
            assert r.get("word_overlap") is False

    def test_report_renders_confirm_marker(self, paths, capsys):
        paths.write_text(
            _row("supplier_to", "Graphite India", "Colgate-Palmolive Company") + "\n",
            encoding="utf-8",
        )
        assert tpr.main([]) == 0
        report = tpr.REPORT.read_text(encoding="utf-8")
        assert "confirm? word-overlap alias" in report


class TestNoiseGate:
    """G3: `discard` decisions persist as a runtime noise gate so plain
    discards do NOT re-enter the sidecar on the next full-corpus extract
    (the #169 / #217 re-entry lesson: 10 discarded noise rows came
    straight back)."""

    def test_discard_persists_as_noise_gate(self, paths, monkeypatch):
        paths.write_text(
            _row("supplier_to", "Graphite India", "Japan") + "\n",
            encoding="utf-8",
        )
        assert tpr.main([]) == 0
        decisions = [json.loads(line) for line in tpr.DECISIONS.read_text().splitlines()]
        decisions[0]["decision"] = "discard"
        tpr.DECISIONS.write_text(
            "\n".join(json.dumps(d) for d in decisions) + "\n", encoding="utf-8"
        )
        assert tpr.main(["--apply-decisions"]) == 0
        noise = json.loads(tpr.NOISE_FILE.read_text())
        # Normalized (lowercased, boundary-stripped) so re-spellings hit.
        assert ["supplier_to", "Graphite India", "japan"] in noise

    def test_noise_gate_is_exact_not_prefix(self, paths, monkeypatch):
        """A gate entry for a different triple must not suppress an
        unrelated row — the gate matches (edge_type, source, target)."""
        paths.write_text(
            _row("supplier_to", "Graphite India", "Japan") + "\n",
            encoding="utf-8",
        )
        tpr.NOISE_FILE.write_text(
            json.dumps([["supplier_to", "Other Source", "japan"]]) + "\n",
            encoding="utf-8",
        )
        assert tpr.main([]) == 0
        prose = [
            r["target_mention"]
            for r in tpr.build_triage(tpr.SIDECAR.read_text().splitlines(), set())["prose"]
        ]
        assert "Japan" in prose

    def test_no_noise_file_when_no_discards(self, paths):
        paths.write_text(
            _row("supplier_to", "Graphite India", "Kubota Corporation") + "\n",
            encoding="utf-8",
        )
        assert tpr.main([]) == 0
        decisions = [json.loads(line) for line in tpr.DECISIONS.read_text().splitlines()]
        decisions[0]["decision"] = "stub"
        tpr.DECISIONS.write_text(
            "\n".join(json.dumps(d) for d in decisions) + "\n", encoding="utf-8"
        )
        assert tpr.main(["--apply-decisions"]) == 0
        assert not tpr.NOISE_FILE.exists()


class TestCliFlow:
    def _seed(self, sidecar):
        sidecar.write_text(
            "\n".join(
                [
                    _row("suggested", "Acme Corp", "Dixon Technologies"),
                    _row("supplier_to", "Graphite India", "Japan"),
                    _row("jv_with", "Acme Corp", "Kubota Corporation"),
                    _row("jv_with", "Ghost Source", "Kubota Corporation"),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def test_report_then_annotated_apply(self, paths, capsys):
        self._seed(paths)
        assert tpr.main([]) == 0
        decisions = [json.loads(line) for line in tpr.DECISIONS.read_text().splitlines()]
        assert all(d["decision"] is None for d in decisions)
        for d in decisions:
            if d["target_mention"] == "Japan":
                d["decision"] = "discard"
            elif d["target_mention"] == "Kubota Corporation" and d["source"] == "Acme Corp":
                d["decision"] = "alias:Colgate Palmolive India"
        tpr.DECISIONS.write_text(
            "\n".join(json.dumps(d) for d in decisions) + "\n", encoding="utf-8"
        )

        # --apply-decisions writes (the --write co-flag was folded in); the
        # validate-only pass lives on tpr.apply_decisions(..., write=False).
        assert tpr.apply_decisions(tpr.DECISIONS, False, tpr.load_entity_names()) == 0
        assert not tpr.ALIAS_FILE.exists()

        assert tpr.main(["--apply-decisions"]) == 0
        aliases = json.loads(tpr.ALIAS_FILE.read_text())
        assert aliases == {"kubota corporation": "Colgate Palmolive India"}
        # G3: the discarded "Japan" row persists as a runtime noise gate so
        # plain discards do NOT re-enter the sidecar on the next extract.
        noise = json.loads(tpr.NOISE_FILE.read_text())
        assert ["supplier_to", "Graphite India", "japan"] in noise
        remaining = [
            json.loads(line) for line in tpr.SIDECAR.read_text().splitlines() if line.strip()
        ]
        # Only the unannotated bad_source row remains; suggested moved out.
        assert [r["source"] for r in remaining] == ["Ghost Source"]
        moved = [
            json.loads(line) for line in tpr.SUGGESTIONS.read_text().splitlines() if line.strip()
        ]
        assert len(moved) == 1 and moved[0]["edge_type"] == "suggested"

    def test_apply_rejects_bad_alias_target(self, paths):
        self._seed(paths)
        tpr.main([])
        decisions = [json.loads(line) for line in tpr.DECISIONS.read_text().splitlines()]
        for d in decisions:
            if d["target_mention"] == "Japan":
                d["decision"] = "alias:No Such Entity"
        tpr.DECISIONS.write_text(
            "\n".join(json.dumps(d) for d in decisions) + "\n", encoding="utf-8"
        )
        assert tpr.main(["--apply-decisions"]) == 1
        assert not tpr.ALIAS_FILE.exists()

    def test_apply_rejects_unknown_decision(self, paths):
        self._seed(paths)
        tpr.main([])
        decisions = [json.loads(line) for line in tpr.DECISIONS.read_text().splitlines()]
        decisions[0]["decision"] = "maybe"
        tpr.DECISIONS.write_text(
            "\n".join(json.dumps(d) for d in decisions) + "\n", encoding="utf-8"
        )
        assert tpr.main(["--apply-decisions"]) == 1

    def test_clear(self, paths):
        self._seed(paths)
        assert tpr.main(["--clear"]) == 0
        assert tpr.SIDECAR.read_text() == ""

    def test_split_json_row_named_in_error(self, paths):
        self._seed(paths)
        tpr.main([])
        text = tpr.DECISIONS.read_text(encoding="utf-8")
        # Simulate an editor hard-wrap: split the first row mid-object.
        first, rest = text.split("\n", 1)
        mid = first.index('"target_mention"')
        broken = first[:mid] + "\n" + first[mid:] + "\n" + rest
        tpr.DECISIONS.write_text(broken, encoding="utf-8")
        with pytest.raises(SystemExit, match="is not valid JSON"):
            tpr._read_decisions(tpr.DECISIONS)


class TestReviewSitting:
    """review-kit S2: journaled sitting -> tool-written decision rows."""

    def _sidecar(self, sidecar, rows):
        sidecar.write_text("\n".join(_row(*r) for r in rows) + "\n", encoding="utf-8")

    def _review(self, answers, sidecar, apply=True, **kw):
        it = iter(answers)
        out = []
        rep = tpr.review(
            input_fn=lambda _p: next(it),
            print_fn=out.append,
            apply=apply,
            journal_dir=sidecar.parent,
            **kw,
        )
        return rep, out

    def _file_rows(self):
        return [json.loads(x) for x in tpr.DECISIONS.read_text().splitlines() if x.strip()]

    def test_accept_appends_row_the_validator_consumes(self, paths):
        self._sidecar(
            paths, [("supplier_to", "Acme Corp", "Dixon Technologies", "supplies batteries")]
        )
        rep, out = self._review(["a", "y"], paths)
        assert rep["approved"] == 1 and rep["applied"] == 1
        rows = self._file_rows()
        assert [r["decision"] for r in rows] == ["accept:supplier_to"]
        assert rows[0]["note"] == "" and rows[0]["bucket"]
        # the sitting's output must satisfy the EXISTING validator end-to-end
        plan = tpr._validate_decisions(rows, tpr.load_entity_names())
        assert plan is not None and len(plan["accepts"]) == 1
        assert plan["accepts"][0]["source"] == "Acme Corp"
        assert plan["accepts"][0]["target"] == "Dixon Technologies"
        # journal block + follow-up hint
        assert any("--apply-decisions" in ln for ln in out)
        assert Path(rep["journal"]).exists()

    def test_accept_with_note_and_target_override(self, paths):
        self._sidecar(paths, [("jv_with", "Acme Corp", "Dixn Tech", "typo'd mention")])
        rep, _ = self._review(["a/jv_with/Dixon Technologies | fix typo", "y"], paths)
        assert rep["approved"] == 1
        rows = self._file_rows()
        assert rows[0]["decision"] == "accept:jv_with:Dixon Technologies"
        assert rows[0]["note"] == "fix typo"

    def test_bad_accept_reasks_then_discard(self, paths):
        self._sidecar(paths, [("supplier_to", "Graphite India", "Japan", "sourced from Japan")])
        rep, out = self._review(["a", "d | country not entity", "y"], paths)
        assert rep["approved"] == 1
        rows = self._file_rows()
        assert rows[0]["decision"] == "discard" and rows[0]["note"] == "country not entity"
        assert any("not an existing entity" in ln for ln in out)

    def test_alias_validates_target(self, paths):
        self._sidecar(paths, [("jv_with", "Acme Corp", "Kubota Corporation", "jv with Kubota")])
        rep, out = self._review(["al Bogus Co", "al Colgate Palmolive India", "y"], paths)
        assert rep["approved"] == 1
        rows = self._file_rows()
        assert rows[0]["decision"] == "alias:Colgate Palmolive India"
        assert any("no entity matches" in ln for ln in out)

    def test_park_never_reasks(self, paths):
        self._sidecar(paths, [("supplier_to", "Acme Corp", "Dixon Technologies", "q")])
        rep, _ = self._review(["p"], paths)
        assert rep["approved"] == 0 and not tpr.DECISIONS.exists()
        j = json.loads(Path(rep["journal"]).read_text().splitlines()[1])
        assert j["action"] == "skip"
        # second sitting: parked row is not re-asked (would exhaust the
        # answer iterator if walked)
        rep2, _ = self._review([], paths)
        assert rep2["approved"] == 0
        # ...but --skipped re-opens it
        rep3, _ = self._review(["d", "y"], paths, skipped=True)
        assert rep3["approved"] == 1

    def test_decided_rows_excluded_unless_redecide(self, paths):
        self._sidecar(
            paths,
            [
                ("supplier_to", "Acme Corp", "Dixon Technologies", "q1"),
                ("supplier_to", "Graphite India", "Japan", "q2"),
            ],
        )
        rep, _ = self._review(["a", "y"], paths, limit=1)  # walks ONE row (manual bucket first)
        assert rep["approved"] == 1
        rep2, _ = self._review(["d", "y"], paths)
        rows = self._file_rows()
        assert len(rows) == 2 and rows[0]["target_mention"] != rows[1]["target_mention"]
        rep3, _ = self._review(["d", "d", "y"], paths, redecide=True)
        assert rep3["approved"] == 2  # redecide re-walked BOTH decided rows

    def test_dry_run_journals_but_writes_no_rows(self, paths):
        self._sidecar(paths, [("supplier_to", "Acme Corp", "Dixon Technologies", "q")])
        rep, _ = self._review(["a", "y"], paths, apply=False)
        assert rep["approved"] == 1 and rep["applied"] == 0
        assert not tpr.DECISIONS.exists()
        assert Path(rep["journal"]).exists()

    def test_cli_review_flag_wiring(self, paths, monkeypatch, capsys):
        self._sidecar(paths, [("supplier_to", "Acme Corp", "Dixon Technologies", "q")])
        calls = []

        def fake_review(**kw):
            calls.append(kw)
            return {"approved": 0, "applied": 0, "decisions": 0, "journal": "x"}

        monkeypatch.setattr(tpr, "review", fake_review)
        assert tpr.main(["--review", "--limit", "3", "--redecide", "--skipped", "--dry-run"]) == 0
        assert calls == [
            {
                "limit": 3,
                "redecide": True,
                "skipped": True,
                "apply": False,
            }
        ]
        assert tpr.main(["--review"]) == 0 and calls[-1] == {
            "limit": None,
            "redecide": False,
            "skipped": False,
            "apply": True,
        }

    def test_accept_target_db_lookup(self, paths):
        # a//QUERY keeps the row's edge type, resolves the target via DB
        # lookup (acronym/substring/token-prefix), numbered pick if ambiguous
        self._sidecar(paths, [("supplier_to", "Acme Corp", "EPC contractors", "q")])
        rep, out = self._review(["a//dixon", "y"], paths, limit=1)
        assert rep["approved"] == 1
        rows = self._file_rows()
        assert rows[-1]["decision"] == "accept:supplier_to:Dixon Technologies"
        assert any("matched: Dixon Technologies" in ln for ln in out)

    def test_accept_target_lookup_numbered_pick(self, paths):
        self._sidecar(paths, [("supplier_to", "Acme Corp", "EPC contractors", "q")])
        rep, out = self._review(["a//india", "2", "y"], paths, limit=1)
        assert rep["approved"] == 1
        rows = self._file_rows()
        assert rows[-1]["decision"].startswith("accept:supplier_to:")
        assert any("candidates:" in ln for ln in out)

    def test_accept_plain_mention_not_entity_still_reasks(self, paths):
        # plain 'a' (no override) against a non-entity mention keeps the
        # guided error — the mention itself is never DB-guessed
        self._sidecar(paths, [("supplier_to", "Graphite India", "Japan", "q")])
        rep, out = self._review(["a", "d", "y"], paths, limit=1)
        assert rep["approved"] == 1
        assert any("not an existing entity" in ln for ln in out)

    def test_alias_db_lookup_acronym(self, paths):
        self._sidecar(paths, [("jv_with", "Acme Corp", "Kubota Corporation", "jv with Kubota")])
        rep, out = self._review(["al colgate", "y"], paths, limit=1)
        rows = [json.loads(x) for x in tpr.DECISIONS.read_text().splitlines() if x.strip()]
        assert rows[-1]["decision"] == "alias:Colgate Palmolive India"
        assert any("matched:" in ln for ln in out)

    def test_alias_db_lookup_numbered_pick(self, paths):
        self._sidecar(paths, [("jv_with", "Acme Corp", "Kubota Corporation", "jv")])
        rep, out = self._review(["al india", "2", "y"], paths, limit=1)
        rows = [json.loads(x) for x in tpr.DECISIONS.read_text().splitlines() if x.strip()]
        assert rows[-1]["decision"].startswith("alias:")
        assert any("candidates:" in ln for ln in out)

    def test_quit_and_abort(self, paths):
        self._sidecar(paths, [("supplier_to", "Acme Corp", "Dixon Technologies", "q")])
        rep, _ = self._review(["q"], paths)
        assert rep["approved"] == 0
        rep2, _ = self._review(["x"], paths)
        assert rep2["approved"] == 0
        jlines = Path(rep2["journal"]).read_text().splitlines()
        assert json.loads(jlines[-1])["action"] == "sitting-end"


class TestExtractorIntegration:
    def test_alias_overrides_loaded_and_case_canonicalized(self, tmp_path, monkeypatch):
        af = tmp_path / "relation_aliases.json"
        af.write_text(
            json.dumps({"kubota corporation": "colgate palmolive india"}), encoding="utf-8"
        )
        monkeypatch.setattr(xr, "ALIAS_OVERRIDES_PATH", af)
        xr._alias_overrides.cache_clear()
        try:
            assert xr._lookup_alias("kubota corporation") == "colgate palmolive india"
            # _ALIASES still wins for its own keys.
            assert xr._lookup_alias("iocl") == xr._ALIASES["iocl"]
        finally:
            xr._alias_overrides.cache_clear()

    def test_absent_alias_file_degrades(self, tmp_path, monkeypatch):
        monkeypatch.setattr(xr, "ALIAS_OVERRIDES_PATH", tmp_path / "nope.json")
        xr._alias_overrides.cache_clear()
        try:
            assert xr._alias_overrides() == {}
        finally:
            xr._alias_overrides.cache_clear()

    def test_suggestions_path_split(self):
        from helpers.graph import suggest_relations as sr

        assert sr.SUGGESTIONS_PATH.name == "_pending_suggestions.txt"
        assert sr.SIDECAR_PATH == sr.SUGGESTIONS_PATH
        # No longer coupled to the extraction sidecar.
        assert sr.SUGGESTIONS_PATH != xr.SIDECAR_PATH


class TestAcceptDecisions:
    """`accept:<edge_type>[:<Target>]` — the suggested_relations_accept (S4)
    exit from the suggestions file / mangled-mention prose rows."""

    @pytest.fixture
    def edge_db(self, tmp_path, monkeypatch):
        from tests.conftest import _UNIT_SCHEMA

        db = tmp_path / "research.db"
        conn = sqlite3.connect(db)
        conn.executescript(_UNIT_SCHEMA)
        conn.commit()
        conn.close()
        monkeypatch.setattr(tpr, "EDGE_DB_PATH", db)
        return db

    @staticmethod
    def _rows(db):
        conn = sqlite3.connect(db)
        try:
            return conn.execute(
                "SELECT source, target, edge_type, properties, source_ref, "
                "symmetric FROM graph_edges"
            ).fetchall()
        finally:
            conn.close()

    def test_accept_suggestion_writes_symmetric_edge(self, paths, edge_db):
        tpr.SUGGESTIONS.write_text(
            json.dumps(
                {
                    "edge_type": "suggested",
                    "source": "Acme Corp",
                    "target_mention": "Dixon Technologies",
                    "quote": "",
                    "edition": "link-prediction/jaccard/2026-08-27",
                    "origin": "link_prediction",
                    "score": 1.0,
                    "method": "jaccard",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        decisions = [
            {
                "id": "x1",
                "edge_type": "suggested",
                "source": "Acme Corp",
                "target_mention": "Dixon Technologies",
                "edition": "link-prediction/jaccard/2026-08-27",
                "origin": "link_prediction",
                "score": 1.0,
                "method": "jaccard",
                "decision": "accept:competes_with",
            }
        ]
        tpr.DECISIONS.write_text(
            "\n".join(json.dumps(d) for d in decisions) + "\n", encoding="utf-8"
        )

        assert tpr.main(["--apply-decisions"]) == 0
        rows = self._rows(edge_db)
        assert len(rows) == 1
        src, tgt, etype, props, sref, sym = rows[0]
        assert (src, tgt, etype, sref, sym) == (
            "Acme Corp",
            "Dixon Technologies",
            "competes_with",
            "triage:accept",
            1,
        )
        assert json.loads(props)["score"] == 1.0
        # The decided row left the suggestions file.
        assert not tpr.SUGGESTIONS.read_text().strip()

    def test_accept_semantic_peer_writes_symmetric_edge(self, paths, edge_db):
        """semantic_peer accepts are symmetric too (matches the VSS rewriter,
        which stores all 7.7k semantic_peer rows with symmetric=1)."""
        tpr.SUGGESTIONS.write_text(
            json.dumps(
                {
                    "edge_type": "suggested",
                    "source": "Acme Corp",
                    "target_mention": "Dixon Technologies",
                    "quote": "",
                    "edition": "link-prediction/jaccard/2026-09-09",
                    "origin": "link_prediction",
                    "score": 0.9,
                    "method": "jaccard",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        decisions = [
            {
                "id": "x2",
                "edge_type": "suggested",
                "source": "Acme Corp",
                "target_mention": "Dixon Technologies",
                "decision": "accept:semantic_peer",
            }
        ]
        tpr.DECISIONS.write_text(
            "\n".join(json.dumps(d) for d in decisions) + "\n", encoding="utf-8"
        )
        assert tpr.main(["--apply-decisions"]) == 0
        rows = self._rows(edge_db)
        assert len(rows) == 1
        sym = rows[0][5]
        assert sym == 1

    def test_accept_rerun_is_idempotent(self, paths, edge_db):
        tpr.SUGGESTIONS.write_text(
            json.dumps(
                {
                    "edge_type": "suggested",
                    "source": "Acme Corp",
                    "target_mention": "Dixon Technologies",
                    "quote": "",
                    "edition": "ed",
                    "origin": "link_prediction",
                    "score": 0.9,
                    "method": "jaccard",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        decisions = [
            {
                "id": "x1",
                "edge_type": "suggested",
                "source": "Acme Corp",
                "target_mention": "Dixon Technologies",
                "edition": "ed",
                "origin": "link_prediction",
                "score": 0.9,
                "method": "jaccard",
                "decision": "accept:competes_with",
            }
        ]
        tpr.DECISIONS.write_text(
            "\n".join(json.dumps(d) for d in decisions) + "\n", encoding="utf-8"
        )
        assert tpr.main(["--apply-decisions"]) == 0
        assert tpr.main(["--apply-decisions"]) == 0
        assert len(self._rows(edge_db)) == 1

    def test_accept_explicit_target_for_mangled_mention(self, paths, edge_db):
        paths.write_text(
            _row("acquired", "Acme Corp", "Colgate acquisition of the toothpaste rival") + "\n",
            encoding="utf-8",
        )
        assert tpr.main([]) == 0  # regenerate decisions skeleton
        decisions = [json.loads(line) for line in tpr.DECISIONS.read_text().splitlines()]
        decisions[0]["decision"] = "accept:acquired:Colgate Palmolive India"
        tpr.DECISIONS.write_text(
            "\n".join(json.dumps(d) for d in decisions) + "\n", encoding="utf-8"
        )
        assert tpr.main(["--apply-decisions"]) == 0
        rows = self._rows(edge_db)
        assert [(r[0], r[1], r[2], r[5]) for r in rows] == [
            ("Acme Corp", "Colgate Palmolive India", "acquired", 0)
        ]
        assert json.loads(rows[0][3])["origin"] == "manual_triage"
        # The applied prose row left the sidecar too.
        assert not paths.read_text().strip()

    def test_accept_reverse_direction_swaps_source_target(self, paths, edge_db):
        # 2026-09-05: sidecar rows carry the pattern's forward/reverse flag.
        # A reverse capture ("parent company of X") must write X -> section
        # company — not the inverted edge the bare row shape implied.
        row = json.loads(_row("subsidiary_of", "Acme Corp", "Dixon Technologies"))
        row["direction"] = "reverse"
        paths.write_text(json.dumps(row) + "\n", encoding="utf-8")
        assert tpr.main([]) == 0  # regenerate decisions skeleton
        decisions = [json.loads(line) for line in tpr.DECISIONS.read_text().splitlines()]
        assert decisions[0]["direction"] == "reverse"  # flag surfaces in decisions
        report = tpr.REPORT.read_text()
        assert "captured reversed" in report  # eyeball marker for the annotator
        decisions[0]["decision"] = "accept:subsidiary_of"
        tpr.DECISIONS.write_text(
            "\n".join(json.dumps(d) for d in decisions) + "\n", encoding="utf-8"
        )
        assert tpr.main(["--apply-decisions"]) == 0
        rows = self._rows(edge_db)
        assert [(r[0], r[1], r[2]) for r in rows] == [
            ("Dixon Technologies", "Acme Corp", "subsidiary_of")
        ]

    def test_accept_rejects_bad_edge_type(self, paths, edge_db):
        tpr.SUGGESTIONS.write_text(
            json.dumps(
                {
                    "edge_type": "suggested",
                    "source": "Acme Corp",
                    "target_mention": "Dixon Technologies",
                    "quote": "",
                    "edition": "ed",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        decisions = [
            {
                "id": "x1",
                "edge_type": "suggested",
                "source": "Acme Corp",
                "target_mention": "Dixon Technologies",
                "decision": "accept:capitalism",
            }
        ]
        tpr.DECISIONS.write_text(
            "\n".join(json.dumps(d) for d in decisions) + "\n", encoding="utf-8"
        )
        assert tpr.main(["--apply-decisions"]) == 1
        assert not self._rows(edge_db)
        # Nothing consumed from the suggestions file either.
        assert tpr.SUGGESTIONS.read_text().strip()

    def test_accept_rejects_unknown_target(self, paths, edge_db):
        tpr.SUGGESTIONS.write_text(
            json.dumps(
                {
                    "edge_type": "suggested",
                    "source": "Acme Corp",
                    "target_mention": "Nonexistent Corp",
                    "quote": "",
                    "edition": "ed",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        decisions = [
            {
                "id": "x1",
                "edge_type": "suggested",
                "source": "Acme Corp",
                "target_mention": "Nonexistent Corp",
                "decision": "accept:competes_with",
            }
        ]
        tpr.DECISIONS.write_text(
            "\n".join(json.dumps(d) for d in decisions) + "\n", encoding="utf-8"
        )
        assert tpr.main(["--apply-decisions"]) == 1
        assert not self._rows(edge_db)

    def test_discard_drops_suggestion_without_writing_edge(self, paths, edge_db):
        tpr.SUGGESTIONS.write_text(
            json.dumps(
                {
                    "edge_type": "suggested",
                    "source": "Acme Corp",
                    "target_mention": "Dixon Technologies",
                    "quote": "",
                    "edition": "ed",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        decisions = [
            {
                "id": "x1",
                "edge_type": "suggested",
                "source": "Acme Corp",
                "target_mention": "Dixon Technologies",
                "decision": "discard",
            }
        ]
        tpr.DECISIONS.write_text(
            "\n".join(json.dumps(d) for d in decisions) + "\n", encoding="utf-8"
        )
        assert tpr.main(["--apply-decisions"]) == 0
        assert not self._rows(edge_db)
        assert not tpr.SUGGESTIONS.read_text().strip()


# --------------------------------------------------------------------------- #
# Country-layer guard (C1): country entities never alias-target               #
# --------------------------------------------------------------------------- #
def test_load_entity_names_excludes_countries():
    """Country entities are excluded from the alias-candidate name set —
    a country named 'india' would otherwise fuzzy-match every
    'Bank of India'-shaped mention (the substring-containment family)."""
    from tests.schema import ENTITIES_MINIMAL

    conn = sqlite3.connect(":memory:")
    conn.executescript(ENTITIES_MINIMAL)
    try:
        conn.executemany(
            "INSERT INTO entities (name, entity_type) VALUES (?, ?)",
            [
                ("Bank of India", "company"),
                ("SEBI", "institution"),
                ("india", "country"),
                ("usa", "country"),
            ],
        )
        conn.commit()
        names = tpr.load_entity_names(conn)
        assert "Bank of India" in names
        assert "SEBI" in names  # institutions stay (legitimate targets)
        assert "india" not in names
        assert "usa" not in names
    finally:
        conn.close()


def test_noise_target_institution_short_names_exempt():
    """RBI (3 chars) must survive the fragment-length rule so regulator
    mentions reach the sidecar; the rest of the fragment class still dies."""
    assert not tpr.noise_target("RBI")
    assert not tpr.noise_target("SEBI")
    assert tpr.noise_target("and")
    assert tpr.noise_target("the")
    assert tpr.noise_target("of")
