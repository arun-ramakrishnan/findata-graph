"""Tests for helpers/validators/frontmatter_schema.py (B1).

Covers: schema loading, the date-object normalization quirk, every violation
class the schemas exist to catch (rogue keys, wrong types, bad enums, 'N/A'
tickers, unparsable dates, missing required keys), the corpus walker on a
synthetic tree, and the determinism/content of the generated key doc.
"""

from __future__ import annotations

import json
import datetime as _dt
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from helpers.validators import frontmatter_schema as FMS

jsonschema = pytest.importorskip("jsonschema")


GOOD_COMPANY = {
    "title": "Aarti Drugs",
    "type": "company",
    "sector": "Pharma",
    "normalized_name": "Aarti_Drugs",
    "permalink": "/companies/pharma/aarti_drugs",
    "tags": ["entity_type/company", "sector/pharma", "market_cap/mid_cap"],
    "created": "2025-11-16",
    "last_modified": "2026-07-07",
    "ticker": "AARTIDRUGS.NS",
    "market_cap": "mid_cap",
}

GOOD_SECTOR = {
    "title": "Logistics",
    "type": "sector",
    "super_sector": "Industrials",
    "normalized_name": "Logistics",
    "permalink": "/sectors/logistics",
    "tags": ["entity_type/sector", "sector/logistics", "geography/global"],
    "created": "2025-11-28",
    "last_modified": "2026-07-11",
}

GOOD_SUPER = {
    "title": "Consumer Discretionary",
    "type": "super_sector",
    "normalized_name": "Consumer_Discretionary",
    "file_path": "findata/Super_Sectors/Consumer_Discretionary.md",
    "permalink": "/super_sectors/consumer_discretionary",
    "tags": ["entity_type/super_sector", "super_sector/consumer_discretionary"],
    "created": "2026-08-10",
    "last_modified": "2026-08-10",
}

GOOD_NEWSLETTER = {
    "title": "The Chatter: SBI, Delhivery, Titan & More",
    "type": "newsletter",
    "tags": ["series/the_chatter", "publisher/zerodha"],
}


# OKF v0.2 provenance overlay (doc/okf/README.md §3.2) — every key optional.
OKF_GENERATED: dict = {"by": "derive_insights.py/v1", "at": "2026-08-18T09:00:00Z"}
OKF_SOURCE: dict = {
    "id": "bosch-amara-zydus",
    "resource": "/Reports/Bosch_Amara_Zydus.pdf",
    "title": "The Chatter: Bosch, Amara, Zydus & More",
    "author": "process:pdf_conv_md",
    "last_modified": "2026-08-13",
}
OKF_KEYS = {
    "generated": OKF_GENERATED,
    "verified": [{"by": "human:user", "at": "2026-08-18T12:00:00Z"}],
    "sources": [OKF_SOURCE],
    "status": "stable",
    "stale_after": "2027-02-14",
}


class TestOkfKeys:
    """OKF v0.2 provenance/trust/lifecycle keys (all optional, all types)."""

    @pytest.mark.parametrize("base,note_type", [
        (GOOD_COMPANY, "company"),
        (GOOD_SECTOR, "sector"),
        (GOOD_SUPER, "super_sector"),
        (GOOD_NEWSLETTER, "newsletter"),
    ])
    def test_okf_overlay_validates_every_type(self, base, note_type):
        assert FMS.validate_frontmatter(dict(base, **OKF_KEYS), note_type) == []

    def test_notes_without_okf_keys_still_valid(self):
        # gradual rollout: absence carries meaning, never an error (OKF §5)
        assert FMS.validate_frontmatter(dict(GOOD_COMPANY), "company") == []

    def test_minimal_generated_only(self):
        fm = dict(GOOD_COMPANY, generated={"by": "pdf_conv_md.py/PP-StructureV3",
                                           "at": "2026-08-18T09:00:00Z"})
        assert FMS.validate_frontmatter(fm, "company") == []

    def test_empty_generated_by_rejected(self):
        fm = dict(GOOD_COMPANY, generated={"by": "", "at": "2026-08-18T09:00:00Z"})
        assert FMS.validate_frontmatter(fm, "company") != []

    def test_generated_missing_at_rejected(self):
        fm = dict(GOOD_COMPANY, generated={"by": "derive_insights.py/v1"})
        assert FMS.validate_frontmatter(fm, "company") != []

    def test_non_iso_verified_at_rejected(self):
        fm = dict(GOOD_COMPANY, verified=[{"by": "human:user",
                                           "at": "16/11/2025"}])
        assert FMS.validate_frontmatter(fm, "company") != []

    def test_verified_entry_missing_by_rejected(self):
        fm = dict(GOOD_COMPANY, verified=[{"at": "2026-08-18T12:00:00Z"}])
        assert FMS.validate_frontmatter(fm, "company") != []

    def test_bare_map_verified_rejected(self):
        # deliberate stricter-than-spec deviation: array at write time only
        fm = dict(GOOD_COMPANY, verified={"by": "human:user",
                                          "at": "2026-08-18T12:00:00Z"})
        assert FMS.validate_frontmatter(fm, "company") != []

    def test_source_missing_resource_rejected(self):
        fm = dict(GOOD_COMPANY, sources=[{"id": "x"}])
        assert FMS.validate_frontmatter(fm, "company") != []

    def test_source_missing_id_rejected(self):
        # second deliberate deviation: id required (footnote join safety)
        fm = dict(GOOD_COMPANY, sources=[{"resource": "/Reports/x.pdf"}])
        assert FMS.validate_frontmatter(fm, "company") != []

    def test_source_bad_last_modified_rejected(self):
        fm = dict(GOOD_COMPANY, sources=[dict(OKF_SOURCE, last_modified="2026/08/13")])
        assert FMS.validate_frontmatter(fm, "company") != []

    def test_bad_status_enum_rejected(self):
        fm = dict(GOOD_COMPANY, status="archived")
        assert FMS.validate_frontmatter(fm, "company") != []

    def test_all_status_values_valid(self):
        for s in ("draft", "stable", "deprecated"):
            fm = dict(GOOD_COMPANY, status=s)
            assert FMS.validate_frontmatter(fm, "company") == []

    def test_bad_stale_after_rejected(self):
        fm = dict(GOOD_COMPANY, stale_after="2027-02-14T00:00:00Z")
        assert FMS.validate_frontmatter(fm, "company") != []

    def test_rogue_key_inside_generated_rejected(self):
        fm = dict(GOOD_COMPANY, generated=dict(OKF_GENERATED, okf_version="0.2"))
        assert FMS.validate_frontmatter(fm, "company") != []

    def test_yaml_timestamp_verified_at_normalizes(self):
        # Hand-written `at: 2026-08-18T12:00:00` (no Z) parses as a datetime
        # object; _normalize_nested must stringify it before the pattern check.
        import yaml as _yaml
        block = "---\ntitle: T\nverified:\n- by: human:user\n  at: 2026-08-18T12:00:00\n---\n"
        raw = _yaml.safe_load(block.split("\n---\n")[0][4:])
        assert isinstance(raw["verified"][0]["at"], _dt.datetime)
        fm = dict(GOOD_COMPANY, verified=raw["verified"])
        assert FMS.validate_frontmatter(fm, "company") == []


class TestSchemasSelfValidate:
    def test_all_three_schemas_are_valid_draft2020(self):
        for fname in FMS.SCHEMA_FILES.values():
            schema = json.loads((FMS.SCHEMA_DIR / fname).read_text())
            jsonschema.Draft202012Validator.check_schema(schema)

    def test_dir_to_type_covers_all_schema_files(self):
        # "proposal" is deliberately NOT in DIR_TO_TYPE (corpus_uniformity
        # S3): proposals live under doc/improvements, walked by the
        # decoupled second loop in check_frontmatter_schema.
        expected = set(FMS.SCHEMA_FILES) - {"proposal"}
        assert set(FMS.DIR_TO_TYPE.values()) == expected


class TestValidFrontmatter:
    @pytest.mark.parametrize(
        ("fm", "note_type"),
        [
            (GOOD_COMPANY, "company"),
            (GOOD_SECTOR, "sector"),
            (GOOD_SUPER, "super_sector"),
            (GOOD_NEWSLETTER, "newsletter"),
        ],
    )
    def test_good_fixtures_pass(self, fm, note_type):
        assert FMS.validate_frontmatter(fm, note_type) == []

    def test_company_optionals_and_nulls(self):
        fm = dict(
            GOOD_COMPANY,
            ticker=None,
            market_cap=None,
            industry=None,
            geography="usa",
            listed=False,
            exchange="NASDAQ",
            business_model="b2b",
            risk_investment="growth",
            index_membership=None,
        )
        assert FMS.validate_frontmatter(fm, "company") == []

    def test_us_bare_ticker_and_bo_suffix(self):
        assert FMS.validate_frontmatter(dict(GOOD_COMPANY, ticker="AAPL"), "company") == []
        assert FMS.validate_frontmatter(dict(GOOD_COMPANY, ticker="KOTAKBANK.BO"), "company") == []


class TestNewsletterSchema:
    """frontmatter.newsletter.v1.json (newsletter_notes_adoption.md S1)."""

    def test_minimal_title_type_only(self):
        assert FMS.validate_frontmatter(
            {"title": "Ed", "type": "newsletter"}, "newsletter"
        ) == []

    def test_full_producer_shape(self):
        fm = dict(
            GOOD_NEWSLETTER,
            permalink="the-chatter/edition-1-output",
            visibility="public",
            language="en",
            last_updated="2025-10-25",
            **OKF_KEYS,
        )
        assert FMS.validate_frontmatter(fm, "newsletter") == []

    def test_flat_tags_rejected(self):
        errs = FMS.validate_frontmatter(
            dict(GOOD_NEWSLETTER, tags=["zerodha", "chatter"]), "newsletter"
        )
        assert errs

    def test_company_tags_not_valid_here(self):
        # entity_type/company is grammar-valid but these are not entity
        # notes; only the schema's pattern applies (it passes the pattern —
        # the vocabulary restriction is semantic, not structural).
        errs = FMS.validate_frontmatter(
            dict(GOOD_NEWSLETTER, tags=["entity_type/company"]), "newsletter"
        )
        assert errs == []

    def test_missing_title_rejected(self):
        assert FMS.validate_frontmatter(
            {"type": "newsletter"}, "newsletter"
        ) != []

    def test_wrong_type_const(self):
        assert FMS.validate_frontmatter(
            dict(GOOD_NEWSLETTER, type="company"), "newsletter"
        ) != []

    def test_rogue_key_rejected(self):
        errs = FMS.validate_frontmatter(
            dict(GOOD_NEWSLETTER, send_to_kindle="yes"), "newsletter"
        )
        assert any("send_to_kindle" in e for e in errs)


class TestViolationClasses:
    def test_rogue_key_rejected(self):
        errs = FMS.validate_frontmatter(dict(GOOD_COMPANY, reindex="true"), "company")
        assert any("reindex" in e for e in errs)

    def test_wrong_type_const(self):
        errs = FMS.validate_frontmatter(dict(GOOD_COMPANY, type="sector"), "company")
        assert errs

    def test_na_ticker_rejected_the_original_drift(self):
        errs = FMS.validate_frontmatter(dict(GOOD_COMPANY, ticker="N/A"), "company")
        assert any("ticker" in e for e in errs)

    def test_bad_market_cap_enum(self):
        errs = FMS.validate_frontmatter(dict(GOOD_COMPANY, market_cap="MEGA"), "company")
        assert any("market_cap" in e for e in errs)

    def test_hyphenated_permalink_segment_rejected(self):
        errs = FMS.validate_frontmatter(
            dict(GOOD_COMPANY, permalink="/companies/defense/apollo-micro-systems"), "company"
        )
        assert any("permalink" in e for e in errs)

    def test_missing_required_key(self):
        errs = FMS.validate_frontmatter(
            {k: v for k, v in GOOD_COMPANY.items() if k != "last_modified"}, "company"
        )
        assert any("last_modified" in e for e in errs)

    def test_bad_date_format(self):
        errs = FMS.validate_frontmatter(dict(GOOD_COMPANY, created="16/11/2025"), "company")
        assert any("created" in e for e in errs)

    def test_uppercase_tag_rejected(self):
        errs = FMS.validate_frontmatter(dict(GOOD_COMPANY, tags=["Sector/Pharma"]), "company")
        assert any("tags" in e for e in errs)

    def test_bad_exchange_enum(self):
        errs = FMS.validate_frontmatter(dict(GOOD_COMPANY, exchange="BSE"), "company")
        assert any("exchange" in e for e in errs)

    def test_sector_schema_rejects_company_keys(self):
        errs = FMS.validate_frontmatter(dict(GOOD_SECTOR, ticker="X.NS"), "sector")
        assert any("ticker" in e for e in errs)


class TestDateNormalization:
    def test_yaml_date_object_normalized_to_iso(self):
        import datetime

        fm = dict(GOOD_COMPANY, created=datetime.date(2025, 11, 16))
        assert FMS.validate_frontmatter(fm, "company") == []

    def test_yaml_datetime_normalized_to_date_iso(self):
        import datetime

        fm = dict(GOOD_COMPANY, last_modified=datetime.datetime(2026, 7, 7, 12, 0))
        assert FMS.validate_frontmatter(fm, "company") == []


class TestCorpusWalker:
    def _tree(self, tmp_path: Path) -> Path:
        root = tmp_path / "repo"
        co = root / "findata" / "Companies" / "Pharma"
        co.mkdir(parents=True)
        (co / "Good.md").write_text(
            "---\ntitle: G\ntype: company\nsector: Pharma\n"
            "normalized_name: Good\npermalink: /companies/pharma/good\n"
            "tags: [entity_type/company]\ncreated: '2025-01-01'\n"
            "last_modified: '2025-01-01'\nticker: null\nmarket_cap: null\n---\n"
        )
        (co / "Bad.md").write_text(
            "---\ntitle: B\ntype: company\nsector: Pharma\n"
            "normalized_name: Bad\npermalink: /companies/pharma/bad\n"
            "tags: [entity_type/company]\ncreated: '2025-01-01'\n"
            "last_modified: '2025-01-01'\nticker: N/A\nmarket_cap: huge_cap\n---\n"
        )
        (root / "findata" / "The_Chatter").mkdir(parents=True)
        (root / "findata" / "The_Chatter" / "Edition_1.md").write_text(
            "---\ntitle: Edition 1\ntype: newsletter\n"
            "tags:\n- series/the_chatter\n- publisher/zerodha\n---\nbody\n"
        )
        return root

    def test_synthetic_tree_reports_only_the_bad_note(self, tmp_path):
        root = self._tree(tmp_path)
        fatal, advisory = FMS.check_frontmatter_schema(root)
        # Bad.md carries two violations (N/A ticker + bad market_cap enum)
        assert len(fatal) == 2
        assert all("Bad.md" in e for e in fatal)
        assert not any("Good.md" in e for e in fatal)
        assert not any("Edition_1" in e for e in fatal)
        assert advisory == []

    def test_newsletters_validated_chrome_and_images_skipped(self, tmp_path):
        root = self._tree(tmp_path)
        chatter = root / "findata" / "The_Chatter"
        # flat tag = gate-fatal under the namespaced grammar (S3 migrates
        # these on the live corpus; this pins the gate side of the contract)
        (chatter / "Flat.md").write_text(
            "---\ntitle: Flat\ntype: newsletter\ntags:\n- zerodha\n---\n"
        )
        (chatter / "image_map.md").write_text("chrome, no frontmatter\n")
        (chatter / "images").mkdir()
        (chatter / "images" / "x.md").write_text("---\nbad: [\n")
        fatal, _ = FMS.check_frontmatter_schema(root)
        assert len(fatal) == 3  # Bad.md x2 + Flat.md namespaced-tag violation
        assert any("Flat.md" in e for e in fatal)
        assert not any("image_map" in e or "images" in e for e in fatal)

    def test_live_corpus_is_clean(self):
        fatal, advisory = FMS.check_frontmatter_schema()
        assert fatal == []
        assert advisory == []


class TestParseFrontmatter:
    def test_parses_leading_block(self, tmp_path):
        p = tmp_path / "n.md"
        p.write_text("---\ntitle: X\ntype: company\n---\nbody\n")
        assert FMS.parse_frontmatter(p) == {"title": "X", "type": "company"}

    def test_absent_block_returns_none(self, tmp_path):
        p = tmp_path / "n.md"
        p.write_text("# heading only\n")
        assert FMS.parse_frontmatter(p) is None

    def test_unterminated_block_returns_none(self, tmp_path):
        p = tmp_path / "n.md"
        p.write_text("---\ntitle: X\n")
        assert FMS.parse_frontmatter(p) is None


class TestGeneratedKeyDoc:
    def test_emit_is_deterministic(self):
        assert FMS.emit_key_doc() == FMS.emit_key_doc()

    def test_doc_content_and_generated_header(self):
        doc = FMS.emit_key_doc()
        assert doc.startswith("# Note frontmatter keys (GENERATED)")
        assert "## company" in doc and "## sector" in doc and "## super_sector" in doc
        assert "apollo" not in doc
        # every schema key appears
        for fname in FMS.SCHEMA_FILES.values():
            schema = json.loads((FMS.SCHEMA_DIR / fname).read_text())
            for key in schema["properties"]:
                assert f"`{key}`" in doc

    def test_checked_in_doc_is_fresh(self):
        assert (FMS.KEY_DOC).read_text() == FMS.emit_key_doc()

    def test_markdown_tables_not_broken_by_regex_pipes(self):
        for line in FMS.emit_key_doc().splitlines():
            if line.startswith("| `"):
                assert line.count("|") == 6, line


class TestOkfConformanceSweep:
    """--okf mode: OKF §11 sweep + producer shape + provenance census."""

    def _vault(self, root: Path):
        fd = root / "findata" / "The_Chatter"
        fd.mkdir(parents=True)
        (root / "Reports").mkdir()
        (root / "Reports" / "X.pdf").write_bytes(b"%PDF")
        return fd

    def test_z_datetime_objects_are_not_false_positives(self, tmp_path):
        # PyYAML loads even Z-suffixed ISO timestamps as datetime OBJECTS;
        # the sweep must normalize before the string-pattern shape check.
        fd = self._vault(tmp_path)
        (fd / "G.md").write_text(
            "---\ntype: newsletter\ntitle: T\ngenerated:\n"
            "  by: pdf_conv_md.py/PP-StructureV3\n  at: 2026-08-18T09:00:00Z\n"
            "sources:\n- id: x\n  resource: /Reports/X.pdf\n"
            "  last_modified: 2026-08-13\n---\n\n# body\n")
        fatal, advisory = FMS.check_okf_conformance(tmp_path)
        assert fatal == []
        assert not any("G.md" in a for a in advisory)

    def test_shape_issues_are_advisory_and_specific(self, tmp_path):
        fd = self._vault(tmp_path)
        (fd / "B.md").write_text(
            "---\ntype: newsletter\ngenerated:\n  by: ''\n  at: 1/2/34\n"
            "sources:\n- resource: Reports/x.pdf\n---\n")
        fatal, advisory = FMS.check_okf_conformance(tmp_path)
        assert fatal == []  # must-not-reject: shape issues never fatal
        hits = [a for a in advisory if "B.md" in a]
        assert any("malformed" in h for h in hits)
        assert any("bundle-relative" in h for h in hits)

    def test_resource_resolution_against_passed_root(self, tmp_path):
        fd = self._vault(tmp_path)
        (fd / "R.md").write_text(
            "---\ntype: newsletter\nsources:\n- id: x\n"
            "  resource: /Reports/X.pdf\n---\n")
        (fd / "R2.md").write_text(
            "---\ntype: newsletter\nsources:\n- id: y\n"
            "  resource: /Reports/absent.pdf\n---\n")
        fatal, advisory = FMS.check_okf_conformance(tmp_path)
        assert not any("R.md" in a for a in advisory)
        assert any("does not resolve" in a for a in advisory if "R2.md" in a)

    def test_missing_type_is_fatal(self, tmp_path):
        fd = self._vault(tmp_path)
        (fd / "N.md").write_text("---\ntitle: x\n---\n")
        fatal, _ = FMS.check_okf_conformance(tmp_path)
        assert any("N.md" in f and "non-empty `type`" in f for f in fatal)

    def test_pre_adoption_ocr_notes_aggregate_to_one_advisory(self, tmp_path):
        # Unregistered FUTURE source tree: frontmatter-less notes aggregate
        # as one pre-rollout advisory (gradual rollout, Q5).
        self._vault(tmp_path)
        new = tmp_path / "findata" / "New_Series"
        new.mkdir(parents=True)
        (new / "Old1.md").write_text("# no fm\n")
        (new / "Old2.md").write_text("<div>no fm</div>\n")
        fatal, advisory = FMS.check_okf_conformance(tmp_path)
        assert fatal == []
        assert sum(1 for a in advisory if "OCR source notes" in a) == 1
        assert any("2 OCR source notes" in a for a in advisory)

    def test_no_fm_in_registered_tree_is_fatal(self, tmp_path):
        # Since S1 (newsletter_notes_adoption) the source trees are
        # schema-gated: a frontmatter-less note there is a hard §11 break —
        # pre-rollout tolerance applies only to unregistered trees.
        fd = self._vault(tmp_path)
        (fd / "Old.md").write_text("# no fm\n")
        fatal, advisory = FMS.check_okf_conformance(tmp_path)
        assert any("Old.md" in f and "no parseable frontmatter" in f
                   for f in fatal)
        assert not any("OCR source notes" in a for a in advisory)

    def test_reserved_and_chrome_files_skipped(self, tmp_path):
        fd = self._vault(tmp_path)
        (fd / "image_map.md").write_text("# chrome\n")
        (fd / "index.md").write_text("# listing\n")
        (fd / "log.md").write_text("# log\n")
        # triage report artifact: generated chrome, NOT a pre-rollout
        # OCR source note (it must not feed the pre_rollout advisory)
        (fd / "_pending_triage_report.md").write_text("# triage report\n")
        fatal, advisory = FMS.check_okf_conformance(tmp_path)
        assert not any("image_map" in x or "index.md" in x or "log.md" in x
                       for x in fatal + advisory)
        assert not any("OCR source notes" in a for a in advisory)

    def test_trust_tiers_and_census(self, tmp_path):
        fd = self._vault(tmp_path)
        (fd / "H.md").write_text(  # human-reviewed
            "---\ntype: newsletter\ngenerated:\n  by: process:x\n"
            "  at: 2026-08-18T09:00:00Z\nverified:\n- by: human:user\n"
            "  at: 2026-08-18T12:00:00Z\n---\n")
        (fd / "M.md").write_text(  # machine-confirmed (generated, no verified)
            "---\ntype: newsletter\ngenerated:\n  by: process:x\n"
            "  at: 2026-08-18T09:00:00Z\n---\n")
        (fd / "U.md").write_text("---\ntype: newsletter\n---\n")
        _, advisory = FMS.check_okf_conformance(tmp_path)
        # Census is group-scoped: these newsletter-tree notes fall under
        # the "OCR sources" group with per-tier counts in its parens.
        census = next(a for a in advisory if a.startswith("OKF census:"))
        assert "OCR sources" in census
        assert "1 human-reviewed" in census
        assert "1 machine-confirmed" in census
        assert "1 unverified" in census
        assert "derived:" not in census  # no derived notes in this vault

    def test_stale_after_flags_past_due(self, tmp_path):
        fd = self._vault(tmp_path)
        (fd / "S.md").write_text(
            "---\ntype: newsletter\nstale_after: 2020-01-01\n---\n")
        (fd / "F.md").write_text(
            "---\ntype: newsletter\nstale_after: 2099-01-01\n---\n")
        _, advisory = FMS.check_okf_conformance(tmp_path)
        assert any("1 past stale_after" in a for a in advisory)

    def test_cli_okf_mode_runs(self, tmp_path, capsys):
        self._vault(tmp_path)
        (tmp_path / "findata" / "The_Chatter" / "E.md").write_text(
            "---\ntype: newsletter\n---\n")
        rc = FMS.main(["--okf", "--root", str(tmp_path)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "OKF census" in out and "0 fatal" in out

    def test_cli_okf_mode_fatals_on_missing_type(self, tmp_path, capsys):
        self._vault(tmp_path)
        (tmp_path / "findata" / "The_Chatter" / "E.md").write_text(
            "---\ntitle: x\n---\n")
        rc = FMS.main(["--okf", "--root", str(tmp_path)])
        assert rc == 1
        assert "non-empty `type`" in capsys.readouterr().out


class TestOkfVersionAnnotation:
    """x-okf-version: schema-side OKF vocabulary version tracking (Q6:
    metadata in the schemas, never a note key)."""

    OKF_PROPS = ("generated", "verified", "sources", "status", "stale_after")

    def test_every_okf_prop_annotated_in_every_schema(self):
        # The proposal schema (corpus_uniformity S3) deliberately carries
        # NO OKF provenance props — wrong artifact class (§1.1 non-goal);
        # its staleness is the single status bit.
        for note_type, fname in FMS.SCHEMA_FILES.items():
            if note_type == "proposal":
                continue
            schema = json.loads((FMS.SCHEMA_DIR / fname).read_text())
            for prop in self.OKF_PROPS:
                assert schema["properties"][prop].get("x-okf-version") == "0.2", \
                    f"{fname}:{prop}"

    def test_no_okf_version_note_key_wanted(self):
        # Q6 decision: okf_version is NOT a frontmatter key — the schemas
        # must reject it (additionalProperties: false + not enumerated).
        assert "okf_version" not in json.loads(
            (FMS.SCHEMA_DIR / FMS.SCHEMA_FILES["company"]).read_text()
        )["properties"]
        errs = FMS.validate_frontmatter(
            dict(GOOD_COMPANY, okf_version="0.2"), "company")
        assert errs != []


class TestProposalContract:
    """corpus_uniformity S3 — the proposal frontmatter contract: schema
    shape, the decoupled doc/improvements walk (live must carry the block;
    archived proposals too; plain archive docs without a proposal header
    stay outside), and the emitted key doc gaining the section."""

    GOOD = {
        "title": "Widget audit — measure the thing",
        "status": "proposed",
        "filed": "2026-08-31",
        "executed": None,
        "completed_md": None,
        "area": "helpers/misc",
    }

    def test_valid_block_passes_rogue_key_fails(self):
        assert FMS.validate_frontmatter(dict(self.GOOD), "proposal") == []
        errs = FMS.validate_frontmatter(
            dict(self.GOOD, rogue_key=1), "proposal")
        assert errs and "rogue_key" in errs[0]

    def test_executed_shape(self):
        good_exec = dict(self.GOOD, status="executed",
                         executed="2026-08-31", completed_md="190")
        assert FMS.validate_frontmatter(good_exec, "proposal") == []
        bad_num = dict(good_exec, completed_md="not-a-number")
        assert FMS.validate_frontmatter(bad_num, "proposal") != []
        compound = dict(good_exec, completed_md="145+146")
        assert FMS.validate_frontmatter(compound, "proposal") == []

    def test_walk_covers_proposals_and_archive(self, tmp_path):
        root = tmp_path / "repo"
        prop = root / "doc" / "improvements" / "proposals"
        arch = root / "doc" / "improvements" / "archive" / "tooling"
        prop.mkdir(parents=True)
        arch.mkdir(parents=True)
        fm = ("---\ntitle: T\nstatus: proposed\nfiled: '2026-08-31'\n"
              "executed: null\ncompleted_md: null\narea: x\n---\n")
        (prop / "live_one.md").write_text(fm + "body\n")
        (prop / "README.md").write_text("# index\n")
        (arch / "done.md").write_text(
            fm.replace("proposed", "executed").replace(
                "executed: null", "executed: '2026-08-31'").replace(
                "completed_md: null", "completed_md: '7'") + "body\n"
        )
        # Headerless archive doc (triage note) — outside the contract.
        (arch / "plain_note.md").write_text("# just a doc\nno proposal header\n")
        fatal, advisory = FMS.check_frontmatter_schema(root)
        assert fatal == [] and advisory == []
        # A live proposal WITHOUT the block is fatal...
        (prop / "bare.md").write_text("# no frontmatter here\n**Status:** PROPOSED\n")
        fatal, _ = FMS.check_frontmatter_schema(root)
        assert any("bare.md" in e for e in fatal)
        # ...and a rogue key is too.
        (prop / "rogue.md").write_text(
            fm.replace("area: x", "area: x\nrogue: 1") + "body\n")
        fatal, _ = FMS.check_frontmatter_schema(root)
        assert any("rogue.md" in e for e in fatal)

    def test_key_doc_gains_proposal_section(self):
        text = (FMS.SCHEMA_DIR / FMS.KEY_DOC.name).read_text(encoding="utf-8")
        assert "## proposal" in text
        assert "completed_md" in text
