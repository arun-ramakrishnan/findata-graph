"""
Tests for helpers/graph/triage_pending_relations.py — the encapsulated
_pending_relations queue workflow (pending_relations_triage proposal).
Hermetic: tmp sidecar/decisions files, monkeypatched entity names.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (REPO_ROOT, REPO_ROOT / "helpers"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from helpers.graph import triage_pending_relations as tpr  # noqa: E402
from helpers.graph import extract_relations as xr  # noqa: E402

NAMES = {
    "Acme Corp", "Colgate Palmolive India", "Dixon Technologies",
    "Graphite India", "Jupiter Wagons",
}


def _row(edge, source, target, quote="q", edition="ed"):
    return json.dumps({
        "edge_type": edge, "source": source, "target_mention": target,
        "quote": quote, "edition": edition,
    })


@pytest.fixture
def paths(tmp_path, monkeypatch):
    sidecar = tmp_path / "_pending_relations.txt"
    monkeypatch.setattr(tpr, "SIDECAR", sidecar)
    monkeypatch.setattr(tpr, "SUGGESTIONS", tmp_path / "_pending_suggestions.txt")
    monkeypatch.setattr(tpr, "ALIAS_FILE", tmp_path / "relation_aliases.json")
    monkeypatch.setattr(tpr, "REPORT", tmp_path / "report.md")
    monkeypatch.setattr(tpr, "DECISIONS", tmp_path / "decisions.jsonl")
    monkeypatch.setattr(tpr, "load_entity_names", lambda: set(NAMES))
    return sidecar


class TestNoiseTarget:
    def test_countries_generic_fragments(self):
        for t in ["Japan", "Germany", "Ecuador", "Oman", "US"]:
            assert tpr.noise_target(t), t
        for t in ["Vendor Partners", "EPC contractors but", "Army",
                  "Electric Arc Furnace operators", "Fortune 500 firms",
                  "CDMO", "ABC"]:
            assert tpr.noise_target(t), t

    def test_real_names_pass(self):
        for t in ["HKC Display", "Kubota Corporation", "BharatNet",
                  "Heidelberg Materials South Asia B.V."]:
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
            _row("supplier_to", "Colgate Palmolive India",
                 "Colgate-Palmolive Company"),
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
        triage = tpr.build_triage(
            [_row("supplier_to", "Ghost Co", "Kubota Corporation")], NAMES)
        assert triage["prose"][0]["bucket"] == "bad_source"


class TestCliFlow:
    def _seed(self, sidecar):
        sidecar.write_text("\n".join([
            _row("suggested", "Acme Corp", "Dixon Technologies"),
            _row("supplier_to", "Graphite India", "Japan"),
            _row("jv_with", "Acme Corp", "Kubota Corporation"),
            _row("jv_with", "Ghost Source", "Kubota Corporation"),
        ]) + "\n", encoding="utf-8")

    def test_report_then_annotated_apply(self, paths, capsys):
        self._seed(paths)
        assert tpr.main([]) == 0
        decisions = [
            json.loads(line)
            for line in tpr.DECISIONS.read_text().splitlines()
        ]
        assert all(d["decision"] is None for d in decisions)
        for d in decisions:
            if d["target_mention"] == "Japan":
                d["decision"] = "discard"
            elif (d["target_mention"] == "Kubota Corporation"
                  and d["source"] == "Acme Corp"):
                d["decision"] = "alias:Colgate Palmolive India"
        tpr.DECISIONS.write_text(
            "\n".join(json.dumps(d) for d in decisions) + "\n",
            encoding="utf-8")

        # dry-run first: nothing written
        assert tpr.main(["--apply-decisions"]) == 0
        assert not tpr.ALIAS_FILE.exists()

        assert tpr.main(["--apply-decisions", "--write"]) == 0
        aliases = json.loads(tpr.ALIAS_FILE.read_text())
        assert aliases == {"kubota corporation": "Colgate Palmolive India"}
        remaining = [
            json.loads(line) for line in
            tpr.SIDECAR.read_text().splitlines() if line.strip()
        ]
        # Only the unannotated bad_source row remains; suggested moved out.
        assert [r["source"] for r in remaining] == ["Ghost Source"]
        moved = [
            json.loads(line) for line in
            tpr.SUGGESTIONS.read_text().splitlines() if line.strip()
        ]
        assert len(moved) == 1 and moved[0]["edge_type"] == "suggested"

    def test_apply_rejects_bad_alias_target(self, paths):
        self._seed(paths)
        tpr.main([])
        decisions = [
            json.loads(line)
            for line in tpr.DECISIONS.read_text().splitlines()
        ]
        for d in decisions:
            if d["target_mention"] == "Japan":
                d["decision"] = "alias:No Such Entity"
        tpr.DECISIONS.write_text(
            "\n".join(json.dumps(d) for d in decisions) + "\n",
            encoding="utf-8")
        assert tpr.main(["--apply-decisions", "--write"]) == 1
        assert not tpr.ALIAS_FILE.exists()

    def test_apply_rejects_unknown_decision(self, paths):
        self._seed(paths)
        tpr.main([])
        decisions = [
            json.loads(line)
            for line in tpr.DECISIONS.read_text().splitlines()
        ]
        decisions[0]["decision"] = "maybe"
        tpr.DECISIONS.write_text(
            "\n".join(json.dumps(d) for d in decisions) + "\n",
            encoding="utf-8")
        assert tpr.main(["--apply-decisions", "--write"]) == 1

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


class TestExtractorIntegration:
    def test_alias_overrides_loaded_and_case_canonicalized(
            self, tmp_path, monkeypatch):
        af = tmp_path / "relation_aliases.json"
        af.write_text(json.dumps(
            {"kubota corporation": "colgate palmolive india"}), encoding="utf-8")
        monkeypatch.setattr(xr, "ALIAS_OVERRIDES_PATH", af)
        xr._alias_overrides.cache_clear()
        try:
            assert xr._lookup_alias("kubota corporation") == \
                "colgate palmolive india"
            # _ALIASES still wins for its own keys.
            assert xr._lookup_alias("iocl") == xr._ALIASES["iocl"]
        finally:
            xr._alias_overrides.cache_clear()

    def test_absent_alias_file_degrades(self, tmp_path, monkeypatch):
        monkeypatch.setattr(xr, "ALIAS_OVERRIDES_PATH",
                            tmp_path / "nope.json")
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
