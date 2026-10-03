"""Queue hygiene for the pending-relations sidecar — S4 + S5 of
doc/improvements/archive/pipeline/markdown_parse_procedure_audit.md.

S4 (identity + dedupe + provenance): the queue was append-only with no id,
so re-running the extractor duplicated rows (measured on the live queue:
31 lines, 3 distinct) and every operator decision was keyed on a
report-time hash of the VERBATIM mention, so 8/8 live decisions had
orphaned. Rows now carry a canonical id stamped at write time, dedupe on
append, and origin/method provenance; decisions carry forward across a
--report rewrite.

S5 (sentence integrity): a newsletter bold LABEL (`**MHP acquired from
Porsche:**`) is not a prose sentence, and the regex matched inside it. Such
captures are dropped at write time with a counted reason.

Hermetic: tmp sidecar/decisions files, no DB, no network.
"""

import json

import pytest

from helpers.graph import extract_relations as xr
from helpers.graph import triage_pending_relations as tpr

NAMES = {"Acme Corp", "Tata Consultancy Services", "Dixon Technologies"}

# Verbatim from the live queue (2026-10-03), reproduced as the regression key.
PORSCHE_ROW = (
    "acquired",
    "Tata Consultancy Services",
    "Porsche",
    "dian Bank, Amagi, CMR Green & More* ## The Chatter — Dr. Rohit on Indian "
    "Energy, TCS, Max Life, Tempsens & More **MHP acquired from Porsche:** TCS tak",
)


def _json_row(edge, source, target, **extra):
    row = {
        "edge_type": edge,
        "source": source,
        "target_mention": target,
        "quote": "q",
        "edition": "ed",
        "direction": "forward",
    }
    row.update(extra)
    return json.dumps(row)


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


def _unresolved(edge, source, target, edition="ed"):
    return xr.Unresolved(
        edge_type=edge,
        source=source,
        target_mention=target,
        quote="q",
        edition=edition,
    )


class TestS4RowId:
    """The canonical id is the decisions key. It must not move when the
    capture moves, or decisions orphan."""

    def test_stable_across_case_and_punctuation(self):
        assert tpr.row_id("acquired", "TCS", "Porsche") == tpr.row_id(
            "acquired", "tcs", "Porsche's"
        )
        assert tpr.row_id("acquired", "TCS", "Porsche") == tpr.row_id("acquired", "TCS", "Porsche.")

    def test_distinguishes_a_different_fact(self):
        assert tpr.row_id("acquired", "TCS", "Porsche") != tpr.row_id("acquired", "TCS", "MHP")
        assert tpr.row_id("acquired", "TCS", "X") != tpr.row_id("jv_with", "TCS", "X")

    def test_unresolved_stamps_its_own_id(self):
        u = _unresolved("acquired", "Acme Corp", "Porsche")
        assert u.id == tpr.row_id("acquired", "Acme Corp", "Porsche")
        assert u.origin == "extract"

    def test_stamped_id_is_preferred_on_read(self, paths):
        stamped = "deadbeef01"
        paths.write_text(
            _json_row("acquired", "Acme Corp", "Porsche", id=stamped) + "\n",
            encoding="utf-8",
        )
        triage = tpr.build_triage(paths.read_text().splitlines(), NAMES)
        assert triage["prose"][0]["id"] == stamped

    def test_legacy_row_without_id_computes_the_same_id(self, paths):
        triple = ("acquired", "Acme Corp", "Porsche")
        paths.write_text(_json_row(*triple) + "\n", encoding="utf-8")
        legacy = tpr.build_triage(paths.read_text().splitlines(), NAMES)["prose"][0]
        stamped = tpr.build_triage([_json_row(*triple, id=tpr.row_id(*triple))], NAMES)["prose"][0]
        assert legacy["id"] == stamped["id"]

    def test_punctuation_variant_cannot_collide_on_one_id(self, paths):
        """Two rows differing only in punctuation would share an id and make
        one decision ambiguous between two rows."""
        paths.write_text(
            _json_row("acquired", "Acme Corp", "Porsche")
            + "\n"
            + _json_row("acquired", "Acme Corp", "Porsche's")
            + "\n",
            encoding="utf-8",
        )
        triage = tpr.build_triage(paths.read_text().splitlines(), NAMES)
        assert len(triage["prose"]) == 1
        assert triage["dupes"] == 1


class TestS4AppendDedupe:
    """The queue was append-only; re-running the extractor duplicated rows."""

    def test_rerun_appends_nothing(self, tmp_path):
        sidecar = tmp_path / "_pending_relations.txt"
        rows = [_unresolved("acquired", "Acme Corp", "Porsche")]
        assert xr.write_sidecar(rows, sidecar) == 1
        assert xr.write_sidecar(rows, sidecar) == 0
        assert len(sidecar.read_text().strip().splitlines()) == 1

    def test_batch_internal_duplicates_collapse(self, tmp_path):
        sidecar = tmp_path / "_pending_relations.txt"
        rows = [
            _unresolved("jv_with", "Acme Corp", "Dixon Technologies", edition="e1"),
            _unresolved("jv_with", "Acme Corp", "Dixon Technologies", edition="e2"),
        ]
        assert xr.write_sidecar(rows, sidecar) == 1

    def test_live_shape_31_lines_collapse_to_3(self, tmp_path):
        """The measured live queue: 31 lines, 3 distinct rows."""
        sidecar = tmp_path / "_pending_relations.txt"
        shapes = [
            (
                "jv_with",
                "Nippon Life India Asset Management",
                "European giant DWS Group l divesting a minority stake",
            ),
            ("acquired", "Tata Consultancy Services", "Porsche"),
            ("jv_with", "TVS Supply Chain Solutions", "Sankyu Corporation involving"),
        ]
        rows = [_unresolved(*s, edition=f"ed{i}") for i in range(11) for s in shapes]
        assert len(rows) == 33
        assert xr.write_sidecar(rows, sidecar) == 3

    def test_legacy_rows_in_the_file_are_deduped_against(self, tmp_path):
        sidecar = tmp_path / "_pending_relations.txt"
        triple = ("acquired", "Acme Corp", "Porsche")
        sidecar.write_text(_json_row(*triple) + "\n", encoding="utf-8")
        assert xr.write_sidecar([_unresolved(*triple)], sidecar) == 0

    def test_unparseable_line_does_not_block_append(self, tmp_path):
        sidecar = tmp_path / "_pending_relations.txt"
        sidecar.write_text("not json at all\n", encoding="utf-8")
        assert xr.write_sidecar([_unresolved("acquired", "Acme Corp", "Porsche")], sidecar) == 1

    def test_written_row_carries_provenance_and_id(self, tmp_path):
        """The producer stamps identity + provenance; the file keeps them."""
        sidecar = tmp_path / "_pending_relations.txt"
        capture = acquired_capture(PROSE_BODY)
        assert capture is not None, "PROSE_BODY must yield an acquired capture"
        unresolved: list = []
        xr.reset_skip_stats()
        xr._queue_unresolved(
            unresolved,
            edge_type="acquired",
            source_entity="Tata Consultancy Services",
            target_mention=capture.target,
            body=PROSE_BODY,
            m=capture.match,
            edition_title="ed",
            direction=capture.direction,
        )
        assert len(unresolved) == 1, xr.skip_stats()
        assert xr.write_sidecar(unresolved, sidecar) == 1
        row = json.loads(sidecar.read_text().strip())
        assert row["id"] == tpr.row_id("acquired", "Tata Consultancy Services", capture.target)
        assert row["origin"] == "extract"
        assert row["method"] == "regex:acquired"


class TestS4DecisionsSurvive:
    """--report used to REGENERATE the decisions file with decision=None,
    so annotating before a re-run lost the work. Decisions now carry
    forward on the normalized triple (id-independent)."""

    def _annotate(self, decision="discard"):
        rows = [json.loads(x) for x in tpr.DECISIONS.read_text().splitlines()]
        rows[0]["decision"] = decision
        rows[0]["note"] = "operator said so"
        tpr.DECISIONS.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    def _first(self):
        return json.loads(tpr.DECISIONS.read_text().splitlines()[0])

    def test_annotation_survives_a_report_rerun(self, paths):
        paths.write_text(_json_row("acquired", "Acme Corp", "Porsche") + "\n", encoding="utf-8")
        assert tpr.main([]) == 0
        self._annotate("discard")
        assert tpr.main([]) == 0
        assert self._first()["decision"] == "discard"
        assert self._first()["note"] == "operator said so"

    def test_carry_forward_is_id_independent(self, paths):
        """A decision keyed under a DIFFERENT id than the row's canonical
        one still joins — this is the 8/8 orphan case, where decisions were
        keyed on a report-time hash of the verbatim mention."""
        paths.write_text(_json_row("acquired", "Acme Corp", "Porsche") + "\n", encoding="utf-8")
        assert tpr.main([]) == 0
        self._annotate("discard")
        # Re-key the decision under a stale/legacy id, as an extractor change
        # would have done, and confirm the row still picks the decision up.
        rows = [json.loads(x) for x in tpr.DECISIONS.read_text().splitlines()]
        rows[0]["id"] = "0000000000"
        tpr.DECISIONS.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        assert tpr.main([]) == 0
        after = self._first()
        # Re-keyed to the row's canonical id, and the decision rode along.
        assert after["id"] != "0000000000"
        assert after["decision"] == "discard"
        assert after["note"] == "operator said so"

    def test_recapture_that_only_shifts_punctuation_keeps_the_decision(self, paths):
        paths.write_text(_json_row("acquired", "Acme Corp", "Porsche") + "\n", encoding="utf-8")
        assert tpr.main([]) == 0
        self._annotate("discard")
        paths.write_text(_json_row("acquired", "Acme Corp", "Porsche's") + "\n", encoding="utf-8")
        assert tpr.main([]) == 0
        assert self._first()["target_mention"] == "Porsche's"
        assert self._first()["decision"] == "discard"

    def test_unrelated_row_gets_no_carry_forward(self, paths):
        paths.write_text(
            _json_row("acquired", "Acme Corp", "Porsche")
            + "\n"
            + _json_row("jv_with", "Acme Corp", "Dixon Technologies")
            + "\n",
            encoding="utf-8",
        )
        assert tpr.main([]) == 0
        decided = [json.loads(x) for x in tpr.DECISIONS.read_text().splitlines()]
        target = next(r for r in decided if r["target_mention"] == "Porsche")
        target["decision"] = "discard"
        tpr.DECISIONS.write_text("\n".join(json.dumps(r) for r in decided) + "\n", encoding="utf-8")
        assert tpr.main([]) == 0
        again = {
            r["target_mention"]: r
            for r in (json.loads(x) for x in tpr.DECISIONS.read_text().splitlines())
        }
        assert again["Porsche"]["decision"] == "discard"
        assert again["Dixon Technologies"]["decision"] is None


# Verbatim from the live queue (2026-10-03), the two predicate-absorbing
# shapes. Both are relations the operator adjudicated TRUE
# (accept:jv_with:DWS, accept:jv_with:Sankyu Corporation).
DWS_MENTION = "European giant DWS Group l divesting a minority stake"
SANKYU_MENTION = "Sankyu Corporation involving"
# Real prose, verbatim: MazDock_Mphasis_Redington.md:1153 (main tree). The
# capture overruns because the pattern terminates on the hard newline.
SANKYU_BODY = (
    "The company has formed a strategic alliance with Sankyu Corporation involving a\n"
    "0.5% equity stake in the listed entity.\n"
)


class TestS6TruncateMention:
    """S6: truncate the mention at the predicate. NEVER discard — the old
    slice wording ("all three live rows classify as noise") would have
    suppressed two facts the operator accepted."""

    def test_live_shapes_truncate_to_the_name(self):
        assert xr.truncate_mention(SANKYU_MENTION) == ("Sankyu Corporation", SANKYU_MENTION)
        assert xr.truncate_mention(DWS_MENTION) == (
            "European giant DWS Group",
            DWS_MENTION,
        )

    def test_clean_names_are_no_ops(self):
        for name in (
            "Kubota Corporation",
            "Colgate Palmolive India",
            "DWS Group",
            # A blanket -ing rule would destroy these.
            "Sterling Industries",
            "Genting Malaysia",
            "Huntington Bancshares",
            # Leading tokens are name elements, never stripped.
            "Indian Bank",
            "European Airlines",
        ):
            assert xr.truncate_mention(name) == (name, ""), name

    def test_idempotent(self):
        once, _ = xr.truncate_mention(SANKYU_MENTION)
        assert xr.truncate_mention(once) == (once, "")

    def test_known_entity_mention_is_a_resolver_gap_not_a_stub(self):
        """After truncation the name is an EXACT existing entity, so the
        stub lane would invite a duplicate stub."""
        names = {"Sankyu Corporation", "DWS", "Acme Corp"}
        bucket, detail, _ = tpr._bucket("jv_with", "Sankyu Corporation", names)
        assert bucket == "manual"
        assert "resolver gap" in detail

    def test_dws_still_buckets_to_the_right_entity(self):
        bucket, detail, overlap = tpr._bucket("jv_with", "European giant DWS Group", {"DWS"})
        assert bucket == "alias_candidate"
        assert "DWS" in detail
        assert overlap is True

    def test_row_is_kept_and_records_the_original(self):
        """The acceptance gate: truncate, never drop."""
        capture = None
        for pat, edge_type, _sym, direction in xr.PATTERNS:
            if edge_type != "jv_with":
                continue
            m = pat.search(SANKYU_BODY)
            if m:
                target, _stake = xr._extract_target_mention(m, edge_type)
                capture = Capture(m, target, direction)
                break
        assert capture is not None, "jv_with pattern must match the real prose"
        assert capture.target == SANKYU_MENTION, capture.target

        unresolved: list = []
        xr.reset_skip_stats()
        xr._queue_unresolved(
            unresolved,
            edge_type="jv_with",
            source_entity="TVS Supply Chain Solutions",
            target_mention=capture.target,
            body=SANKYU_BODY,
            m=capture.match,
            edition_title="ed",
            direction=capture.direction,
        )
        assert len(unresolved) == 1, xr.skip_stats()
        row = unresolved[0]
        assert row.target_mention == "Sankyu Corporation"
        assert row.truncated_from == SANKYU_MENTION
        assert xr.skip_stats() == {}, "truncation must not register as a skip"

    def test_stamped_id_follows_the_truncated_name(self):
        """The decisions key must key on what the reviewer sees."""
        u = _unresolved("jv_with", "Acme Corp", SANKYU_MENTION)
        assert u.id == tpr.row_id("jv_with", "Acme Corp", SANKYU_MENTION)


class TestS6NoSuffixBlacklist:
    """Guard against the rejected S6 wording: `corporation`/`group` must NOT
    become noise suffixes, or two accepted facts die silently."""

    def test_accepted_targets_are_not_noise(self):
        for name in ("Sankyu Corporation", "DWS Group"):
            assert not tpr.noise_target(name), name

    def test_short_accepted_target_survives_resolution_and_the_gate(self):
        """`noise_target("MHP")` was True — the length rule (`<4` chars, with
        only rbi/sebi exempted) discarded it. MEASURED 2026-10-04
        (extract_target_binding, gate3 item 9e2f51c07d): the stub-creation
        case IS live — MHP has no entity yet, the corrected breadcrumb row
        (`MHP acquired from Porsche` -> target MHP) goes unresolved, and the
        length rule killed it before the operator could stub it. The rule now
        exempts measured corporate acronyms by exact name
        (`tpr._CORPORATE_SHORT_NAMES`); resolution still runs first for
        existing entities. Pinned so a future reordering cannot silently
        start dropping short real names again.
        """
        resolver = xr.EntityResolver(["MHP", "Sankyu Corporation"])
        assert not tpr.noise_target("MHP"), "exemption: measured corporate acronym"
        assert xr._resolve_target("MHP", "acquired", resolver) == "MHP"


# The real line, verbatim, from Tata_Consultancy_Services.md:1105 (main tree).
# The capture that produced the live queue row is PATTERNS[4] — the REVERSE
# lane, "acquired from X" — matched INSIDE the bold label.
LABEL_BODY = (
    "**MHP acquired from Porsche:** TCS takes over Porsche's automotive-consulting "
    "arm MHP — ~EUR 700 mn revenue at double-digit margins."
)
PROSE_BODY = "## Results\n\nThe automotive arm was acquired from Porsche last week.\n"


class Capture:
    """One production capture: the match, the mention the extractor derives,
    and the lane's direction."""

    def __init__(self, match, target, direction):
        self.match = match
        self.target = target
        self.direction = direction


def acquired_capture(body):
    """First acquired-pattern capture in `body`, walking PATTERNS exactly as
    `_process_pattern_matches` does — no test-local regex, so the gate is
    tested against the production patterns."""
    for pat, edge_type, _symmetric, direction in xr.PATTERNS:
        if edge_type != "acquired":
            continue
        m = pat.search(body)
        if m:
            target, _stake = xr._extract_target_mention(m, edge_type)
            if target:
                return Capture(m, target, direction)
    return None


def _run(body, *, entity="Tata Consultancy Services", list_shaped=False, mention=None):
    """Drive the real write path for the first acquired capture in `body`."""
    capture = acquired_capture(body)
    assert capture is not None, f"no acquired capture in probe body: {body[:70]!r}"
    unresolved: list = []
    xr.reset_skip_stats()
    xr._queue_unresolved(
        unresolved,
        edge_type="acquired",
        source_entity=entity,
        target_mention=mention if mention is not None else capture.target,
        body=body,
        m=capture.match,
        edition_title="ed",
        direction=capture.direction,
        list_shaped=list_shaped,
    )
    return unresolved, xr.skip_stats()


class TestS5SentenceIntegrity:
    """Acceptance gate: the "Porsche" shape is dropped at write time with a
    reason, and the drop is counted rather than silent."""

    def test_probe_body_reproduces_the_live_shape(self):
        """Guard the fixture itself: the label body must still be what the
        production patterns capture, else this file tests nothing."""
        capture = acquired_capture(LABEL_BODY)
        assert capture is not None
        assert capture.target == "Porsche"
        assert capture.direction == "reverse"

    def test_label_line_is_dropped_with_a_reason(self):
        unresolved, skips = _run(LABEL_BODY)
        assert unresolved == []
        assert skips == {"label_not_prose": 1}

    def test_prose_sentence_is_kept(self):
        unresolved, skips = _run(PROSE_BODY)
        assert len(unresolved) == 1
        assert unresolved[0].target_mention == "Porsche"
        assert unresolved[0].direction == "reverse"
        assert skips == {}

    def test_skips_are_counted_not_silent(self):
        _run(LABEL_BODY)
        assert sum(xr.skip_stats().values()) == 1
        xr.reset_skip_stats()
        assert xr.skip_stats() == {}

    def test_table_row_is_dropped(self):
        # No trailing pipe: the capture lookahead requires punctuation or
        # EOL, and " |" is not an accepted boundary — so the cell value ends
        # the line, exactly as the real tables do.
        body = "| Counterparty | Note |\n| --- | --- |\n| TCS acquired from Porsche\n"
        unresolved, skips = _run(body)
        assert unresolved == []
        assert skips == {"table_row": 1}

    def test_heading_line_is_dropped(self):
        body = "## Tata Consultancy Services\n\nacquired from Porsche in March.\n"
        # The capture is in the PROSE here (the heading has no verb), so put
        # the verb in the heading to exercise the heading rule.
        body = "## The arm was acquired from Porsche\n\nSome prose follows.\n"
        unresolved, skips = _run(body)
        assert unresolved == []
        assert skips == {"heading_line": 1}

    def test_list_shaped_may_cross_a_sentence_boundary(self):
        body = "The arm was acquired from Porsche. It also covers software.\n"
        unresolved, skips = _run(body, list_shaped=True, mention="Porsche. It also covers software")
        assert len(unresolved) == 1
        assert skips == {}

    def test_non_list_mention_spanning_sentences_is_dropped(self):
        body = "The arm was acquired from Porsche. Later it was sold onward.\n"
        unresolved, skips = _run(
            body, list_shaped=False, mention="Porsche. Later it was sold onward"
        )
        assert unresolved == []
        assert skips == {"mention_spans_sentences": 1}


class TestS3DecisionBrief:
    """S3: one consolidated brief, contested items only, worst gap first.

    The live queue (2026-10-04) is the shape that motivated ranking on
    BOTH questions: the carriers agree to reject the Sankyu row
    (admission gap 0.04) while disagreeing about whether the relation
    happened at all (factual gap 0.83). Ranking on admission lean alone
    would have printed "no contested rows" and hidden the only live
    dispute in the corpus.
    """

    @staticmethod
    def _row(edge, source, target, row_id=None):
        return {
            "id": row_id or tpr.row_id(edge, source, target),
            "edge_type": edge,
            "source": source,
            "target_mention": target,
        }

    @staticmethod
    def _key(row):
        return (row["edge_type"], row["source"], row["target_mention"])

    def test_agreement_prints_no_cards(self):
        r = self._row("acquired", "Tata Consultancy Services", "Porsche")
        glm = {self._key(r): {"p_fact": 0.9, "p_rubric": 0.05}}
        merc = {self._key(r): {"p_fact": 0.88, "p_rubric": 0.01}}
        out = tpr.decision_brief_for_rows([r], glm, merc)
        assert len(out) == 1
        assert "no contested rows" in out[0]
        assert "0.2" in out[0]

    def test_live_shape_contested_fact_only(self):
        """The measured live pair: agree on admission, split on factuality."""
        r = self._row("subsidiary", "TVS", "Sankyu Corporation")
        glm = {self._key(r): {"p_fact": 0.9, "p_rubric": 0.05}}
        merc = {self._key(r): {"p_fact": 0.07, "p_rubric": 0.011}}
        out = tpr.decision_brief_for_rows([r], glm, merc)
        text = "\n".join(out)
        assert out[0].startswith("### ")
        assert "Q1 factual gap 0.83, admission gap 0.04" in out[0]
        # Only the factual card is rendered — one brief, not two.
        assert text.count("Is this claimed relation real in the world?") == 1
        assert "Admit this row to the knowledge graph?" not in text
        assert "DISAGREE" in text

    def test_both_cards_when_both_questions_split(self):
        r = self._row("acquired", "Tata Consultancy Services", "Porsche")
        glm = {self._key(r): {"p_fact": 0.9, "p_rubric": 0.85}}
        merc = {self._key(r): {"p_fact": 0.1, "p_rubric": 0.1}}
        out = tpr.decision_brief_for_rows([r], glm, merc)
        text = "\n".join(out)
        assert text.count("QUESTION:") == 2
        assert "Is this claimed relation real in the world?" in text
        assert "Admit this row to the knowledge graph?" in text

    def test_worst_gap_ranks_first(self):
        rows = [
            self._row("subsidiary", "TVS", "Sankyu Corporation", row_id="aaa_small"),
            self._row("acquired", "Tata Consultancy Services", "Porsche", row_id="zzz_big"),
        ]
        glm = {
            self._key(rows[0]): {"p_fact": 0.9, "p_rubric": 0.5},
            self._key(rows[1]): {"p_fact": 0.99, "p_rubric": 0.9},
        }
        merc = {
            self._key(rows[0]): {"p_fact": 0.8, "p_rubric": 0.45},
            self._key(rows[1]): {"p_fact": 0.02, "p_rubric": 0.05},
        }
        out = tpr.decision_brief_for_rows(rows, glm, merc)
        assert "zzz_big" in out[0]
        assert "aaa_small" not in out[0]

    def test_single_carrier_annotation_is_not_contested(self):
        """One carrier's opinion is a scout note, never a dispute — and a
        GLM-only row must not manufacture a card out of p=0.0 defaults."""
        r = self._row("subsidiary", "TVS", "Sankyu Corporation")
        glm = {self._key(r): {"p_fact": 0.9, "p_rubric": 0.05}}
        out = tpr.decision_brief_for_rows([r], glm, {})
        assert len(out) == 1
        assert "no contested rows" in out[0]

    def test_brief_never_names_a_winner(self):
        """The carriers do not get a vote on the write; the card must say so."""
        r = self._row("subsidiary", "TVS", "Sankyu Corporation")
        glm = {self._key(r): {"p_fact": 0.9, "p_rubric": 0.05}}
        merc = {self._key(r): {"p_fact": 0.07, "p_rubric": 0.011}}
        text = "\n".join(tpr.decision_brief_for_rows([r], glm, merc))
        assert "do not get a vote on the write" in text


class TestS2Verify:
    """S2: one expected-output contract for the queue surfaces."""

    @staticmethod
    def _sidecar_row(**extra):
        row = {
            "edge_type": "acquired",
            "source": "Acme Corp",
            "target_mention": "Target Co",
            "quote": "q",
            "edition": "ed",
            "direction": "forward",
            "id": "abc123",
            "origin": "extract",
            "method": "regex:acquired",
            "truncated_from": None,
        }
        row.update(extra)
        return row

    @staticmethod
    def _decision_row(**extra):
        row = {
            "id": "abc123",
            "edge_type": "acquired",
            "source": "Acme Corp",
            "target_mention": "Target Co",
            "direction": "forward",
            "bucket": "manual",
            "word_overlap": False,
            "vss_hint": "",
            "origin": "extract",
            "method": "regex:acquired",
            "decision": None,
            "note": None,
        }
        row.update(extra)
        return row

    def _write(self, tmp_path, sidecar_rows=(), decisions_rows=()):
        sidecar = tmp_path / "_pending_relations.txt"
        decisions = tmp_path / "decisions.jsonl"
        sidecar.write_text("".join(json.dumps(r) + "\n" for r in sidecar_rows), encoding="utf-8")
        decisions.write_text(
            "".join(json.dumps(r) + "\n" for r in decisions_rows), encoding="utf-8"
        )
        return sidecar, decisions

    def test_clean_queue_passes(self, tmp_path):
        s, d = self._write(tmp_path, [self._sidecar_row()], [self._decision_row()])
        assert tpr.verify_queue_files(s, d) == []

    def test_missing_id_is_a_violation(self, tmp_path):
        row = self._sidecar_row()
        del row["id"]
        s, d = self._write(tmp_path, [row], [])
        assert any("missing 'id'" in v for v in tpr.verify_queue_files(s, d))

    def test_duplicate_id_is_a_violation(self, tmp_path):
        s, d = self._write(tmp_path, [self._sidecar_row(), self._sidecar_row()], [])
        assert any("duplicate row id" in v for v in tpr.verify_queue_files(s, d))

    def test_missing_origin_is_a_violation(self, tmp_path):
        row = self._sidecar_row()
        del row["origin"]
        s, d = self._write(tmp_path, [row], [])
        assert any("missing 'origin'" in v for v in tpr.verify_queue_files(s, d))

    def test_truncated_from_must_be_string_or_null(self, tmp_path):
        row = self._sidecar_row(truncated_from=12)
        s, d = self._write(tmp_path, [row], [])
        assert any("truncated_from" in v for v in tpr.verify_queue_files(s, d))

    def test_unknown_bucket_is_a_violation(self, tmp_path):
        s, d = self._write(
            tmp_path,
            [self._sidecar_row()],
            [
                self._decision_row(bucket="mystery"),
            ],
        )
        assert any("unknown bucket" in v for v in tpr.verify_queue_files(s, d))

    def test_orphan_decision_is_flagged(self, tmp_path):
        s, d = self._write(tmp_path, [], [self._decision_row()])
        assert any("orphan" in v for v in tpr.verify_queue_files(s, d))

    def test_non_json_line_is_flagged_not_silently_dropped(self, tmp_path):
        sidecar = tmp_path / "_pending_relations.txt"
        decisions = tmp_path / "decisions.jsonl"
        sidecar.write_text("this is not json\n", encoding="utf-8")
        decisions.write_text("", encoding="utf-8")
        violations = tpr.verify_queue_files(sidecar, decisions)
        assert any("not JSON" in v for v in violations)


class TestS5SecondOpinionSitting:
    """S5 (system_one_typed_judgment_framework.md): the review sitting
    renders the pre-annotation A/B beside the evidence and journals the
    verdict pair as a NON-TERMINAL `second-opinion` line beside the human
    decision. Read-side only — no model calls, never a vote on the write,
    and the line never parks the row."""

    GLM_ID = "acquired:Tata Consultancy Services:Porsche"

    def _carrier_files(self, tmp_path, monkeypatch, glm, mercury):
        ann = tmp_path / "_pending_annotations.jsonl"
        ann.write_text(json.dumps(glm) + "\n", encoding="utf-8")
        merc = tmp_path / "_pending_annotations_mercury.jsonl"
        merc.write_text(json.dumps(mercury) + "\n", encoding="utf-8")
        monkeypatch.setattr(tpr, "ANNOTATIONS", ann)
        monkeypatch.setattr(tpr, "MERCURY_ANNOTATIONS", merc)
        monkeypatch.setattr(tpr, "REVIEW_JOURNAL_DIR", tmp_path / "journal_dir")

    def test_sitting_prints_ab_block_and_journals_pair(self, paths, tmp_path, monkeypatch):
        paths.write_text(
            _json_row("acquired", "Tata Consultancy Services", "Porsche") + "\n",
            encoding="utf-8",
        )
        self._carrier_files(
            tmp_path,
            monkeypatch,
            glm={
                "id": self.GLM_ID,
                "p_fact": 0.9,
                "p_rubric": 0.05,
                "escalated": False,
            },
            mercury={
                "id": self.GLM_ID,
                "p_fact": 0.07,
                "p_rubric": 0.01,
                "escalated": False,
            },
        )
        out: list[str] = []
        result = tpr.review(
            input_fn=lambda _: "d",
            print_fn=out.append,
            apply=False,  # dry-run sitting: preview-*, nothing applied
        )
        text = "\n".join(out)
        assert "ab glm-5.3: fact p=0.90 / admission p=0.05" in text
        assert "ab mercury: fact p=0.07 / admission p=0.01" in text
        assert "ab 2-carrier: DISAGREE (worst gap 0.83)" in text
        assert "do not get a vote" in text
        assert result["decisions"] >= 1

        # The journal carries the verdict pair as a non-terminal line.
        journal = tmp_path / "journal_dir" / "journal.jsonl"
        lines = [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]
        pairs = [d for d in lines if d.get("action") == "second-opinion"]
        assert len(pairs) == 1
        assert pairs[0]["ab"]["p_fact"]["glm"] == pytest.approx(0.9)
        assert pairs[0]["gap_admission"] == pytest.approx(0.04, abs=1e-3)

    def test_second_opinion_line_never_parks_the_row(self, paths, tmp_path, monkeypatch):
        from helpers.core.review_kit import latest_action_by

        paths.write_text(
            _json_row("acquired", "Tata Consultancy Services", "Porsche") + "\n",
            encoding="utf-8",
        )
        self._carrier_files(
            tmp_path,
            monkeypatch,
            glm={"id": self.GLM_ID, "p_fact": 0.5, "p_rubric": 0.5, "escalated": False},
            mercury={"id": self.GLM_ID, "p_fact": 0.5, "p_rubric": 0.5, "escalated": False},
        )
        tpr.review(
            input_fn=lambda _: "p",  # operator parks the row
            print_fn=lambda *_: None,
            apply=False,
        )
        journal = tmp_path / "journal_dir" / "journal.jsonl"
        parked = latest_action_by(journal, key_field="id")
        # `skip` parks; the second-opinion line must not have.
        assert parked == {}


class TestS7QuestionPinning:
    """S7: the typed question text IS the regression surface. These exact
    strings are what the relations surface's eval key (eval-v3) measured —
    any change here invalidates the key and must re-run it before carrier
    verdicts are shown again (typed_judgment.SURFACE_EVAL_KEYS)."""

    def test_relations_question_texts_are_pinned(self):
        assert tpr.Q1_FACTUAL_QUESTION == "Is this claimed relation real in the world?"
        assert tpr.Q2_ADMIT_QUESTION == "Admit this row to the knowledge graph?"

    def test_agreement_tolerance_is_pinned(self):
        """One shared tolerance: the brief card and rank_contentious must
        never disagree with each other about what 'contested' means."""
        from helpers.core import typed_judgment as tj

        assert tj.AGREE_TOL == 0.2
