#!/usr/bin/env python3
"""merger/demerger triage mapping (merger_demerger_hyperedges S3) + the
eval-gate family pins (S4).

Pins:

* the extractor routes the three M&A verb forms onto the n-ary families
  (``demerger``/reverse, ``merger``/forward, ``merger``/resultant) instead
  of folding them into reverse ``acquired``;
* the triage lane accepts n-ary rows — the structured member fields ride
  the journal row through ``apply_batch`` — and REJECTS dyad verbs on
  them (contradiction: those rows route to the incidence store);
* AC#1 end-to-end: one accepted merger row -> exactly one ``hyper_edges``
  row + role-tagged ``hyper_incidences`` (constituent x2 + resultant),
  written by the S2 derive lane from the journal the sitting produced;
* the ontology gate gains the ``hyperedge_labels`` shape and pins both
  (currently empty) families — a first landed fact needs --expect-improve.
"""

from __future__ import annotations

import json


from helpers.core.db import connect
from helpers.graph import extract_relations as xr
from helpers.graph import triage_pending_relations as tpr
from helpers.graph.derive_merger_hyperedges import apply_derivation, collect
from helpers.misc.ontology_eval_gate import answer_question

NAMES = {
    "LTIMindtree",
    "Larsen & Toubro Infotech",
    "Mindtree",
    "Reliance Industries",
    "Jio Financial Services",
}


def _row(**kw) -> dict:
    row = {
        "id": "row-1",
        "edge_type": "merger",
        "source": "Larsen & Toubro Infotech",
        "target_mention": "Mindtree",
        "direction": "forward",
        "bucket": "manual",
        "word_overlap": False,
        "vss_hint": "",
        "origin": "extract",
        "method": "regex:merger",
        "decision": "accept",
        "note": None,
    }
    row.update(kw)
    return row


def _match(sentence: str) -> tuple[str, str, str] | None:
    """First PATTERNS hit for a sentence -> (edge_type, direction, group)."""
    for pat, edge_type, _symmetric, direction in xr.PATTERNS:
        m = pat.search(sentence)
        if m:
            return edge_type, direction, m.group(1)
    return None


# --------------------------------------------------------------------------- #
# S3 — extractor routing                                                       #
# --------------------------------------------------------------------------- #
class TestExtractorRouting:
    def test_demerged_from_is_a_reverse_demerger(self):
        hit = _match("The bank demerged from Lakshmi Vilas Bank in 2020.")
        assert hit is not None
        assert hit[0] == "demerger"
        assert hit[1] == "reverse"
        assert hit[2] == "Lakshmi Vilas Bank"

    def test_merged_with_is_a_forward_merger(self):
        hit = _match("Vesuvius merged with Pitch Technologies in 2025.")
        assert hit is not None
        assert hit[0] == "merger"
        assert hit[1] == "forward"
        assert hit[2] == "Pitch Technologies"

    def test_formed_through_merger_is_resultant_and_stops_at_and(self):
        hit = _match(
            "LTIMindtree, formed through the merger of Larsen & Toubro Infotech "
            "and Mindtree, began trading in 2023."
        )
        assert hit is not None
        assert hit[0] == "merger"
        assert hit[1] == "resultant"
        # capture stops at " and " — the second constituent stays for the
        # reviewer to name (a/merger/<NAME>)
        assert hit[2] == "Larsen & Toubro Infotech"

    def test_ma_rows_never_reach_the_acquired_rewrite(self):
        """The reverse-acquired predecessor rewrite is `acquired`-anchored;
        the n-ary families must not match it by edge_type."""
        for sentence in (
            "The bank demerged from Lakshmi Vilas Bank in 2020.",
            "Vesuvius merged with Pitch Technologies in 2025.",
        ):
            for pat, edge_type, _sym, direction in xr.PATTERNS:
                if not pat.search(sentence):
                    continue
                assert (edge_type, direction) != ("acquired", "reverse"), sentence


# --------------------------------------------------------------------------- #
# S3 — _parse_nary_accept                                                      #
# --------------------------------------------------------------------------- #
class TestParseNaryAccept:
    def test_forward_merger_bare_accept(self):
        fact = tpr._parse_nary_accept(_row(), "accept", NAMES)
        assert fact is not None
        assert fact["constituents"] == ["Larsen & Toubro Infotech", "Mindtree"]
        assert "resultant" not in fact
        assert fact["members"] == [
            ("Larsen & Toubro Infotech", "constituent"),
            ("Mindtree", "constituent"),
        ]

    def test_forward_merger_names_the_resultant(self):
        fact = tpr._parse_nary_accept(_row(), "accept:merger:LTIMindtree", NAMES)
        assert fact is not None
        assert fact["resultant"] == "LTIMindtree"
        assert ("LTIMindtree", "resultant") in fact["members"]

    def test_resultant_direction_requires_the_other_constituent(self, capsys):
        row = _row(
            source="LTIMindtree",
            target_mention="Larsen & Toubro Infotech",
            direction="resultant",
        )
        assert tpr._parse_nary_accept(row, "accept:merger", NAMES) is None
        assert "needs the OTHER constituent" in capsys.readouterr().err

    def test_resultant_direction_builds_survivor_plus_two(self):
        row = _row(
            source="LTIMindtree",
            target_mention="Larsen & Toubro Infotech",
            direction="resultant",
        )
        fact = tpr._parse_nary_accept(row, "accept:merger:Mindtree", NAMES)
        assert fact is not None
        assert fact["resultant"] == "LTIMindtree"
        assert fact["constituents"] == ["Larsen & Toubro Infotech", "Mindtree"]

    def test_demerger_mention_is_the_parent(self):
        row = _row(
            edge_type="demerger",
            source="Jio Financial Services",
            target_mention="Reliance Industries",
            decision="accept:demerger",
        )
        fact = tpr._parse_nary_accept(row, "accept:demerger", NAMES)
        assert fact is not None
        assert fact["parent"] == "Reliance Industries"
        assert fact["child"] == "Jio Financial Services"
        assert fact["members"] == [
            ("Reliance Industries", "parent"),
            ("Jio Financial Services", "resultant"),
        ]

    def test_journaled_structured_fields_are_authoritative(self):
        """Bug regression: the sitting resolves the mention to an ENTITY name and
        journals constituents/parent/child — apply must keep those, not re-derive
        members from the raw triple (mention "Sapphire Foods" is not an entity)."""
        row = _row(
            source="Devyani International",
            target_mention="Sapphire Foods",
            constituents=["Devyani International", "Sapphire Foods India"],
        )
        names = NAMES | {"Devyani International", "Sapphire Foods India"}
        fact = tpr._parse_nary_accept(row, "accept:merger", names)
        assert fact is not None
        assert fact["constituents"] == ["Devyani International", "Sapphire Foods India"]
        assert ("Sapphire Foods", "constituent") not in fact["members"]

    def test_journaled_demerger_fields_are_authoritative(self):
        row = _row(
            edge_type="demerger",
            source="Motherson Sumi Wiring India",
            target_mention="Samvardhana Motherson",
            parent="Samvardhana Motherson International",
            child="Motherson Sumi Wiring India",
            decision="accept:demerger",
        )
        names = NAMES | {"Samvardhana Motherson International", "Motherson Sumi Wiring India"}
        fact = tpr._parse_nary_accept(row, "accept:demerger", names)
        assert fact is not None
        assert fact["parent"] == "Samvardhana Motherson International"
        assert fact["child"] == "Motherson Sumi Wiring India"
        assert ("Samvardhana Motherson", "parent") not in fact["members"]

    def test_demerger_child_override(self):
        row = _row(
            edge_type="demerger",
            source="Jio Financial Services",
            target_mention="Reliance Industries",
        )
        fact = tpr._parse_nary_accept(row, "accept:demerger:Jio Financial Services", NAMES)
        assert fact is not None
        assert fact["child"] == "Jio Financial Services"

    def test_dyad_verb_on_nary_row_is_a_contradiction(self, capsys):
        assert tpr._parse_nary_accept(_row(), "accept:jv_with:Someone", NAMES) is None
        assert "names a different family" in capsys.readouterr().err

    def test_wrong_family_verb_is_a_contradiction(self, capsys):
        row = _row(edge_type="demerger")
        assert tpr._parse_nary_accept(row, "accept:merger", NAMES) is None
        assert "names a different family" in capsys.readouterr().err

    def test_merger_of_a_thing_with_itself_is_rejected(self, capsys):
        row = _row(source="Mindtree", target_mention="Mindtree")
        assert tpr._parse_nary_accept(row, "accept", NAMES) is None
        assert "two distinct constituents" in capsys.readouterr().err

    def test_unknown_member_is_rejected_with_stub_hint(self, capsys):
        assert tpr._parse_nary_accept(_row(), "accept:merger:Phantom Co", NAMES) is None
        assert "stub it first" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# S3 — journal round-trip (apply_batch shape -> validation)                    #
# --------------------------------------------------------------------------- #
class TestJournalRoundTrip:
    def test_structured_fields_survive_the_journal(self, tmp_path):
        """A journal row exactly as apply_batch writes it (file fields |
        structured members | decision/note) validates back into
        plan['mergers'] with the member set intact."""
        row = _row(decision="accept:merger:LTIMindtree")
        row |= {
            "constituents": ["Larsen & Toubro Infotech", "Mindtree"],
            "resultant": "LTIMindtree",
        }
        journal = tmp_path / "decisions.jsonl"
        journal.write_text(json.dumps(row) + "\n", encoding="utf-8")
        rows = tpr._read_decisions(journal)
        plan = tpr._validate_decisions(rows, NAMES)
        assert plan is not None
        assert len(plan["mergers"]) == 1
        fact = plan["mergers"][0]
        assert fact["edge_type"] == "merger"
        assert sorted(fact["members"]) == sorted(
            [
                ("Larsen & Toubro Infotech", "constituent"),
                ("Mindtree", "constituent"),
                ("LTIMindtree", "resultant"),
            ]
        )
        assert plan["accepts"] == []

    def test_review_sitting_journals_structured_row(self, tmp_path, monkeypatch):
        """A scripted sitting over a resultant-direction merger row:
        `a/merger/Mindtree` resolves the second constituent, the confirm
        appends the journal row, and the row carries the structured
        members (AC#1 step 1)."""
        sidecar = tmp_path / "_pending_relations.txt"
        sidecar.write_text(
            json.dumps(
                {
                    "edge_type": "merger",
                    "source": "LTIMindtree",
                    "target_mention": "Larsen & Toubro Infotech",
                    "quote": "formed through the merger of Larsen & Toubro Infotech and Mindtree",
                    "edition": "ed",
                    "direction": "resultant",
                    "id": "deadbeef",
                    "origin": "extract",
                    "method": "regex:merger",
                    "truncated_from": None,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        decisions = tmp_path / "decisions.jsonl"
        decisions.write_text("", encoding="utf-8")
        monkeypatch.setattr(tpr, "SIDECAR", sidecar)
        monkeypatch.setattr(tpr, "SUGGESTIONS", tmp_path / "_pending_suggestions.txt")
        monkeypatch.setattr(tpr, "ALIAS_FILE", tmp_path / "relation_aliases.json")
        monkeypatch.setattr(tpr, "REPORT", tmp_path / "report.md")
        monkeypatch.setattr(tpr, "DECISIONS", decisions)
        monkeypatch.setattr(tpr, "NOISE_FILE", tmp_path / "relation_noise.json")
        monkeypatch.setattr(tpr, "ANNOTATIONS", tmp_path / "ann.jsonl")
        monkeypatch.setattr(tpr, "MERCURY_ANNOTATIONS", tmp_path / "merc.jsonl")
        monkeypatch.setattr(tpr, "load_entity_names", lambda: set(NAMES))

        answers = iter(["a/merger/Mindtree", "y"])
        out: list[str] = []
        result = tpr.review(
            input_fn=lambda _prompt: next(answers),
            print_fn=out.append,
            apply=True,
            journal_dir=tmp_path,
        )
        assert result["applied"] == 1
        row = json.loads(decisions.read_text(encoding="utf-8").splitlines()[0])
        assert row["decision"] == "accept:merger:Mindtree"
        assert row["constituents"] == ["Larsen & Toubro Infotech", "Mindtree"]
        assert row["resultant"] == "LTIMindtree"


# --------------------------------------------------------------------------- #
# AC#1 — journal -> derive lane -> incidence store                             #
# --------------------------------------------------------------------------- #
_DDL = """
CREATE TABLE entities (
    name TEXT PRIMARY KEY,
    entity_type TEXT,
    file_path TEXT,
    sector_classification TEXT,
    ticker TEXT,
    last_updated DATETIME
);
CREATE TABLE hyper_edges (
    id INTEGER PRIMARY KEY,
    edge_type TEXT NOT NULL,
    label TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    valid_from DATE,
    valid_to DATE,
    source_ref TEXT NOT NULL,
    properties TEXT NOT NULL DEFAULT '{}',
    agent_id TEXT REFERENCES provenance_agents(agent_id),
    source_tier TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(edge_type, label),
    CHECK (json_valid(properties))
);
CREATE TABLE hyper_incidences (
    edge_id INTEGER NOT NULL REFERENCES hyper_edges(id) ON DELETE CASCADE,
    entity_name TEXT NOT NULL REFERENCES entities(name),
    weight REAL,
    direction TEXT,
    role TEXT,
    valid_from DATE,
    valid_to DATE,
    PRIMARY KEY (edge_id, entity_name)
);
CREATE TABLE provenance_agents (
    agent_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    repo_ref TEXT,
    script TEXT,
    command TEXT,
    as_of TEXT NOT NULL
);
"""


def test_one_accepted_merger_becomes_one_incidence_fact(tmp_path):
    """AC#1 (proposal §5): a merger decision journal -> exactly ONE
    hyper_edges row carrying THREE role-tagged incidences; the dyadic
    graph_edges table is untouched (not even present here)."""
    journal = tmp_path / "decisions.jsonl"
    row = _row(
        decision="accept:merger:LTIMindtree",
        constituents=["Larsen & Toubro Infotech", "Mindtree"],
        resultant="LTIMindtree",
    )
    journal.write_text(json.dumps(row) + "\n", encoding="utf-8")

    db = tmp_path / "research.db"
    con = connect(db)
    try:
        con.executescript(_DDL)
        con.executemany(
            "INSERT INTO entities (name, entity_type) VALUES (?, 'company')",
            [(n,) for n in sorted(NAMES)],
        )
        con.commit()
        out = collect(con, journal)
        assert out["skipped"] == [] and out["unresolved"] == [] and out["conflicts"] == []
        stats = apply_derivation(con, out["facts"], dry_run=False)
        assert stats["facts"] == 1 and stats["members"] == 3

        edges = [
            tuple(r) for r in con.execute("SELECT edge_type, label, agent_id FROM hyper_edges")
        ]
        assert edges == [("merger", "LTIMindtree", "derive_merger_hyperedges")]
        inc = sorted(
            tuple(r) for r in con.execute("SELECT hi.entity_name, hi.role FROM hyper_incidences hi")
        )
        assert inc == [
            ("LTIMindtree", "resultant"),
            ("Larsen & Toubro Infotech", "constituent"),
            ("Mindtree", "constituent"),
        ]
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# S4 — the gate pins                                                           #
# --------------------------------------------------------------------------- #
class TestGateShapes:
    def test_hyperedge_labels_shape_answers_sorted_labels(self, tmp_path):
        db = tmp_path / "g.db"
        con = connect(db)
        try:
            con.executescript(_DDL)
            con.execute(
                "INSERT INTO provenance_agents (agent_id, name, version, as_of)"
                " VALUES ('a', 'a', 'v', '2026-01-01')"
            )
            con.execute(
                "INSERT INTO hyper_edges (edge_type, label, source_ref, agent_id)"
                " VALUES ('merger', 'LTIMindtree', 'derive:merger_hyperedges', 'a')"
            )
            con.commit()
            q = {"shape": "hyperedge_labels", "params": {"edge_type": "merger"}}
            assert answer_question(con, q) == ["LTIMindtree"]
            q2 = {"shape": "hyperedge_labels", "params": {"edge_type": "demerger"}}
            assert answer_question(con, q2) == []
        finally:
            con.close()

    def test_question_set_pins_both_families_and_is_loadable(self):
        from helpers.misc.ontology_eval_gate import DEFAULT_QUESTIONS, load_questions

        data = load_questions(DEFAULT_QUESTIONS)
        by_id = {q["id"]: q for q in data["questions"]}
        # version tracks the roster; bumped by every rebaseline (v4 at the
        # gate's birth → v5 R6a subsidiary cure → v6 R3 insight roster)
        assert data["version"] == 6
        for qid, family in (
            ("hyper-merger-families", "merger"),
            ("hyper-demerger-families", "demerger"),
        ):
            assert by_id[qid]["shape"] == "hyperedge_labels"
            assert by_id[qid]["params"] == {"edge_type": family}
            # baselined since v5: expected holds the live ground truth
            assert isinstance(by_id[qid]["expected"], list)
            assert by_id[qid]["expected"]

    def test_new_shape_is_registered_for_validation(self):
        from helpers.misc.ontology_eval_gate import _SHAPES

        assert "hyperedge_labels" in _SHAPES
