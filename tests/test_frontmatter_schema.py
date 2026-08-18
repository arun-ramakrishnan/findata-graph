"""Tests for helpers/validators/frontmatter_schema.py (B1).

Covers: schema loading, the date-object normalization quirk, every violation
class the schemas exist to catch (rogue keys, wrong types, bad enums, 'N/A'
tickers, unparsable dates, missing required keys), the corpus walker on a
synthetic tree, and the determinism/content of the generated key doc.
"""

from __future__ import annotations

import json
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


class TestSchemasSelfValidate:
    def test_all_three_schemas_are_valid_draft2020(self):
        for fname in FMS.SCHEMA_FILES.values():
            schema = json.loads((FMS.SCHEMA_DIR / fname).read_text())
            jsonschema.Draft202012Validator.check_schema(schema)

    def test_dir_to_type_covers_all_schema_files(self):
        assert set(FMS.DIR_TO_TYPE.values()) == set(FMS.SCHEMA_FILES)


class TestValidFrontmatter:
    @pytest.mark.parametrize(
        ("fm", "note_type"),
        [
            (GOOD_COMPANY, "company"),
            (GOOD_SECTOR, "sector"),
            (GOOD_SUPER, "super_sector"),
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
        (root / "findata" / "The_Chatter" / "Edition_1.md").write_text("# no frontmatter\n")
        return root

    def test_synthetic_tree_reports_only_the_bad_note(self, tmp_path):
        root = self._tree(tmp_path)
        fatal, advisory = FMS.check_frontmatter_schema(root)
        # Bad.md carries two violations (N/A ticker + bad market_cap enum)
        assert len(fatal) == 2
        assert all("Bad.md" in e for e in fatal)
        assert not any("Good.md" in e for e in fatal)
        assert advisory == []

    def test_newsletters_and_images_skipped(self, tmp_path):
        root = self._tree(tmp_path)
        (root / "findata" / "The_Chatter" / "images").mkdir()
        (root / "findata" / "The_Chatter" / "images" / "x.md").write_text("---\nbad: [\n")
        fatal, _ = FMS.check_frontmatter_schema(root)
        assert len(fatal) == 2  # unchanged — chatter + images not walked

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
