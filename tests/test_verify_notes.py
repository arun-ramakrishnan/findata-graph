# Tests for helpers/validators/verify_notes.py
"""Unit tests for NotesVerifier checks."""

from pathlib import Path
from helpers.validators.verify_notes import NotesVerifier


# ---------------------------------------------------------------------------
# Helper: create a verifier and return it
# ---------------------------------------------------------------------------
def make_verifier():
    return NotesVerifier(project_root=Path("/tmp"))  # noqa: S108  # test-only throwaway path/fixture


# ---------------------------------------------------------------------------
# check_filename_format
# ---------------------------------------------------------------------------
class TestFilenameFormat:
    def test_valid_filename(self):
        v = make_verifier()
        v.check_filename_format("/path/My_Company.md")
        assert len(v.issues["filename_format"]) == 0

    def test_leading_digit(self):
        v = make_verifier()
        v.check_filename_format("/path/360_ONE_WAM.md")
        assert len(v.issues["filename_format"]) == 0

    def test_consecutive_underscores(self):
        v = make_verifier()
        v.check_filename_format("/path/My__Company.md")
        assert len(v.issues["filename_format"]) == 1
        assert "Consecutive underscores" in v.issues["filename_format"][0]["description"]

    def test_trailing_underscore(self):
        v = make_verifier()
        v.check_filename_format("/path/My_Company_.md")
        assert len(v.issues["filename_format"]) == 1
        assert "Trailing underscore" in v.issues["filename_format"][0]["description"]

    def test_too_long(self):
        v = make_verifier()
        v.check_filename_format("/path/" + "A" * 101 + ".md")
        assert len(v.issues["filename_format"]) == 1
        assert "exceeds 100 chars" in v.issues["filename_format"][0]["description"]

    def test_special_chars(self):
        v = make_verifier()
        v.check_filename_format("/path/My-Company.md")
        assert len(v.issues["filename_format"]) == 1
        assert "Invalid filename" in v.issues["filename_format"][0]["description"]


# ---------------------------------------------------------------------------
# check_name_sync
# ---------------------------------------------------------------------------
class TestNameSync:
    def test_matching(self):
        v = make_verifier()
        v.check_name_sync("/path/Test_Co.md", {"normalized_name": "Test_Co"})
        assert len(v.issues["name_sync"]) == 0

    def test_missing(self):
        v = make_verifier()
        v.check_name_sync("/path/Test_Co.md", {})
        assert len(v.issues["name_sync"]) == 1
        assert "Missing" in v.issues["name_sync"][0]["description"]

    def test_mismatch(self):
        v = make_verifier()
        v.check_name_sync("/path/Test_Co.md", {"normalized_name": "Other_Co"})
        assert len(v.issues["name_sync"]) == 1
        assert "does not match" in v.issues["name_sync"][0]["description"]


# ---------------------------------------------------------------------------
# check_yaml_structure
# ---------------------------------------------------------------------------
class TestYamlStructure:
    def test_valid_company_yaml(self):
        v = make_verifier()
        content = (
            "---\n"
            "title: Test Co\n"
            "type: company\n"
            "normalized_name: Test_Co\n"
            "market_cap: small_cap\n"
            "permalink: /companies/banking/test_co\n"
            "tags:\n"
            "- entity_type/company\n"
            "created: '2025-01-01'\n"
            "---\n\n# Test Co\n"
        )
        v.check_yaml_structure("/findata/Companies/Banking/Test_Co.md", content=content)
        assert v.stats["errors"] == 0 or sum(len(s) for s in v.issues.values()) == 0

    def test_missing_delimiters(self):
        v = make_verifier()
        v.check_yaml_structure("/path/Test.md", content="No YAML here")
        assert len(v.issues["yaml_structure"]) == 1

    def test_empty_yaml(self):
        v = make_verifier()
        v.check_yaml_structure("/path/Test.md", content="---\n---\n\n# Body")
        assert len(v.issues["yaml_structure"]) == 1
        assert "Empty YAML" in v.issues["yaml_structure"][0]["description"]

    def test_missing_required_fields(self):
        v = make_verifier()
        v.check_yaml_structure(
            "/path/Test.md",
            content="---\nnormalized_name: Test\n---\n\n# Body",
        )
        assert len(v.issues["yaml_structure"]) >= 1

    def test_invalid_type(self):
        v = make_verifier()
        v.check_yaml_structure(
            "/path/Test.md",
            content="---\ntitle: T\ntype: nonsense\nnormalized_name: Test\n---\n# Body",
        )
        descs = [i["description"] for i in v.issues["yaml_structure"]]
        assert any("Invalid type" in d for d in descs)

    def test_tags_not_list(self):
        v = make_verifier()
        v.check_yaml_structure(
            "/path/Test.md",
            content="---\ntitle: T\ntype: company\ntags: not_a_list\nnormalized_name: Test\n---\n# Body",
        )
        descs = [i["description"] for i in v.issues["yaml_structure"]]
        assert any("not properly formatted" in d for d in descs)

    def test_duplicate_tags(self):
        v = make_verifier()
        v.check_yaml_structure(
            "/path/Test.md",
            content=(
                "---\ntitle: T\ntype: company\nnormalized_name: Test\n"
                "tags:\n- sector/banking\n- sector/banking\n---\n# Body"
            ),
        )
        descs = [i["description"] for i in v.issues["yaml_structure"]]
        assert any("Duplicate tags" in d for d in descs)

    def test_bad_yaml_syntax(self):
        v = make_verifier()
        v.check_yaml_structure(
            "/path/Test.md",
            content="---\ntitle: T\n: bad: yaml: colons\ntype: company\n---\n# Body",
        )
        # YAML error should be caught
        assert len(v.issues["yaml_structure"]) >= 1

    def test_tag_format_warning(self):
        v = make_verifier()
        v.check_yaml_structure(
            "/path/Test.md",
            content=(
                "---\ntitle: T\ntype: company\nnormalized_name: Test\n"
                "tags:\n- BadFormat/Value\n---\n# Body"
            ),
        )
        assert len(v.warnings["tag_format"]) >= 1

    def test_tag_value_drift_warning(self):
        v = make_verifier()
        v.check_yaml_structure(
            "/path/Test.md",
            content=(
                "---\ntitle: T\ntype: company\nnormalized_name: Test\n"
                "tags:\n- market_cap/huge_cap\n---\n# Body"
            ),
        )
        assert len(v.warnings["tag_value"]) >= 1


# ---------------------------------------------------------------------------
# check_sector_yaml_consistency
# ---------------------------------------------------------------------------
class TestSectorYamlConsistency:
    def test_valid_sector(self):
        v = make_verifier()
        v.check_sector_yaml_consistency(
            "/path/Banking.md",
            {
                "title": "Banking",
                "type": "sector",
                "normalized_name": "Banking",
                "permalink": "/sectors/banking",
                "created": "2025-01-01",
                "last_modified": "2025-01-02",
            },
            "title: Banking\ntype: sector\npermalink: /sectors/banking\n"
            "created: '2025-01-01'\nlast_modified: '2025-01-02'",
        )
        assert sum(len(s) for s in v.issues.values()) == 0

    def test_bad_permalink(self):
        v = make_verifier()
        v.check_sector_yaml_consistency(
            "/path/Banking.md",
            {"permalink": "/wrong/banking"},
            "permalink: /wrong/banking",
        )
        assert len(v.issues["yaml_structure"]) >= 1

    def test_unquoted_date(self):
        v = make_verifier()
        v.check_sector_yaml_consistency(
            "/path/Banking.md",
            {"created": "2025-01-01"},
            "created: 2025-01-01",  # no quotes
        )
        descs = [i["description"] for i in v.issues["yaml_structure"]]
        assert any("quoted" in d for d in descs)

    def test_unexpected_field(self):
        v = make_verifier()
        v.check_sector_yaml_consistency(
            "/path/Banking.md",
            {"random_field": "x"},
            "random_field: x",
        )
        descs = [i["description"] for i in v.issues["yaml_structure"]]
        assert any("Unexpected fields" in d for d in descs)

    def test_field_order_warning(self):
        v = make_verifier()
        v.check_sector_yaml_consistency(
            "/path/Banking.md",
            {"type": "sector", "title": "Banking"},  # reversed order
            "type: sector\ntitle: Banking",
        )
        assert len(v.warnings["field_order"]) >= 1

    def test_quoted_title_issue(self):
        v = make_verifier()
        v.check_sector_yaml_consistency(
            "/path/Banking.md",
            {"title": "Banking"},
            'title: \"Banking\"',
        )
        descs = [i["description"] for i in v.issues["yaml_structure"]]
        assert any("should not be quoted" in d for d in descs)

    def test_okf_fields_allowed(self):
        # OKF v0.2 provenance keys are schema-optional on sector notes; the
        # strict allowlist must mirror the schemas (backfill stamps them).
        v = make_verifier()
        v.check_sector_yaml_consistency(
            "/path/Banking.md",
            {
                "title": "Banking",
                "type": "sector",
                "normalized_name": "Banking",
                "permalink": "/sectors/banking",
                "created": "2025-01-01",
                "last_modified": "2025-01-02",
                "generated": {"by": "process:okf_backfill",
                              "at": "2026-08-19T03:03:17Z"},
                "verified": [{"by": "human:arun",
                              "at": "2026-06-01T10:00:00Z"}],
                "sources": [{"id": "x", "resource": "/Reports/x.pdf"}],
                "status": "stable",
                "stale_after": "2027-02-15",
            },
            "title: Banking\ntype: sector\npermalink: /sectors/banking\n"
            "created: '2025-01-01'\nlast_modified: '2025-01-02'\n"
            "generated:\n  by: process:okf_backfill\n"
            "  at: '2026-08-19T03:03:17Z'\nstale_after: '2027-02-15'",
        )
        descs = [i["description"] for i in v.issues["yaml_structure"]]
        assert not any("Unexpected fields" in d for d in descs)


# ---------------------------------------------------------------------------
# check_super_sector_yaml_consistency
# ---------------------------------------------------------------------------
class TestSuperSectorYaml:
    def test_valid(self):
        v = make_verifier()
        v.check_super_sector_yaml_consistency(
            "/path/Financials.md",
            {
                "title": "Financials",
                "type": "super_sector",
                "normalized_name": "Financials",
                "file_path": "findata/Super_Sectors/Financials.md",
                "permalink": "/super_sectors/financials",
                "tags": ["entity_type/super_sector"],
                "created": "2025-01-01",
                "last_modified": "2025-01-02",
            },
            "title: Financials\ntype: super_sector\npermalink: /super_sectors/financials\n"
            "created: '2025-01-01'\nlast_modified: '2025-01-02'",
        )
        assert sum(len(s) for s in v.issues.values()) == 0

    def test_bad_permalink(self):
        v = make_verifier()
        v.check_super_sector_yaml_consistency(
            "/path/Financials.md",
            {"permalink": "/sectors/financials"},
            "permalink: /sectors/financials",
        )
        assert len(v.issues["yaml_structure"]) >= 1

    def test_unexpected_field(self):
        v = make_verifier()
        v.check_super_sector_yaml_consistency(
            "/path/Financials.md",
            {"sector": "Banking"},
            "sector: Banking",
        )
        descs = [i["description"] for i in v.issues["yaml_structure"]]
        assert any("Unexpected fields" in d for d in descs)

    def test_okf_fields_allowed(self):
        # Mirror of the sector test: OKF keys are schema-optional here too.
        v = make_verifier()
        v.check_super_sector_yaml_consistency(
            "/path/Financials.md",
            {
                "title": "Financials",
                "type": "super_sector",
                "normalized_name": "Financials",
                "file_path": "findata/Super_Sectors/Financials.md",
                "permalink": "/super_sectors/financials",
                "tags": ["entity_type/super_sector"],
                "created": "2025-01-01",
                "last_modified": "2025-01-02",
                "generated": {"by": "process:okf_backfill",
                              "at": "2026-08-19T03:03:17Z"},
                "stale_after": "2027-02-15",
            },
            "title: Financials\ntype: super_sector\npermalink: /super_sectors/financials\n"
            "created: '2025-01-01'\nlast_modified: '2025-01-02'\n"
            "generated:\n  by: process:okf_backfill\n"
            "  at: '2026-08-19T03:03:17Z'\nstale_after: '2027-02-15'",
        )
        descs = [i["description"] for i in v.issues["yaml_structure"]]
        assert not any("Unexpected fields" in d for d in descs)


# ---------------------------------------------------------------------------
# check_company_yaml_consistency
# ---------------------------------------------------------------------------
class TestCompanyYamlConsistency:
    def test_valid(self):
        v = make_verifier()
        v.check_company_yaml_consistency(
            "/path/Test_Co.md",
            {
                "title": "Test Co",
                "type": "company",
                "normalized_name": "Test_Co",
                "market_cap": "small_cap",
                "permalink": "/companies/banking/test_co",
                "ticker": "TEST.NS",
                "tags": ["entity_type/company"],
            },
            "title: Test Co\ntype: company\nmarket_cap: small_cap\npermalink: /companies/banking/test_co",
        )
        assert sum(len(s) for s in v.issues.values()) == 0

    def test_bad_permalink(self):
        v = make_verifier()
        v.check_company_yaml_consistency(
            "/path/Test_Co.md",
            {"permalink": "/sectors/banking/test_co"},
            "permalink: /sectors/banking/test_co",
        )
        assert len(v.issues["yaml_structure"]) >= 1

    def test_invalid_market_cap(self):
        v = make_verifier()
        v.check_company_yaml_consistency(
            "/path/Test_Co.md",
            {"market_cap": "huge_cap"},
            "market_cap: huge_cap",
        )
        # The bucket key is "market_cap_value" which is a setdefault key
        all_issues = []
        for bucket in v.issues.values():
            all_issues.extend(bucket)
        assert any("Invalid market_cap" in i["description"] for i in all_issues)

    def test_missing_market_cap_warning(self):
        v = make_verifier()
        v.check_company_yaml_consistency(
            "/path/Test_Co.md",
            {"ticker": "TEST.NS"},
            "ticker: TEST.NS",
        )
        assert len(v.warnings["missing_field"]) >= 1

    def test_ticker_null_no_listed_warning(self):
        v = make_verifier()
        v.check_company_yaml_consistency(
            "/path/Test_Co.md",
            {"ticker": None},
            "ticker: null",
        )
        assert len(v.warnings["listed_missing"]) >= 1

    def test_sector_non_canonical_casing(self):
        v = make_verifier()
        v.check_company_yaml_consistency(
            "/path/Test_Co.md",
            {"sector": "banking"},  # lowercase, should warn
            "sector: banking",
        )
        assert len(v.warnings["sector_scalar"]) >= 1

    def test_sector_completely_unknown(self):
        v = make_verifier()
        v.check_company_yaml_consistency(
            "/path/Test_Co.md",
            {"sector": "Quantum_Computing"},
            "sector: Quantum_Computing",
        )
        assert len(v.warnings["sector_scalar"]) >= 1

    def test_quoted_title_warning(self):
        v = make_verifier()
        v.check_company_yaml_consistency(
            "/path/Test_Co.md",
            {"title": "Test Co"},
            'title: \"Test Co\"',
        )
        assert len(v.warnings["company_title_quoted"]) >= 1


# ---------------------------------------------------------------------------
# check_content_quality
# ---------------------------------------------------------------------------
class TestContentQuality:
    def test_long_content_ok(self):
        v = make_verifier()
        content = "---\ntitle: T\ntype: company\n---\n\n# Body\n\n" + "x " * 800
        v.check_content_quality("/path/Test.md", content=content)
        assert sum(len(s) for s in v.issues.values()) == 0

    def test_no_content_after_yaml(self):
        v = make_verifier()
        content = "---\ntitle: T\ntype: company\n---\n"
        v.check_content_quality("/path/Test.md", content=content)
        assert len(v.issues["content_minimal"]) >= 1

    def test_placeholder_content(self):
        v = make_verifier()
        content = (
            "---\ntitle: T\ntype: company\n---\n\n"
            "# Test Co\n\n"
            "More information about this company will be added here"
        )
        v.check_content_quality("/path/Test.md", content=content)
        assert len(v.issues["content_minimal"]) >= 1

    def test_missing_heading(self):
        v = make_verifier()
        content = "---\ntitle: T\ntype: company\n---\n\n" + "Real content line.\n" * 5
        v.check_content_quality("/path/Test.md", content=content)
        assert len(v.issues["content_missing_structure"]) >= 1

    def test_blank_after_yaml(self):
        v = make_verifier()
        content = "---\ntitle: T\ntype: company\n---\n\n\n\n"
        v.check_content_quality("/path/Test.md", content=content)
        assert len(v.issues["content_minimal"]) >= 1

    def test_no_delimiters(self):
        v = make_verifier()
        v.check_content_quality("/path/Test.md", content="Just text, no YAML")
        assert sum(len(s) for s in v.issues.values()) == 0  # nothing to check


# ---------------------------------------------------------------------------
# check_heading_duplicates
# ---------------------------------------------------------------------------
class TestHeadingDuplicates:
    def test_exact_duplicate(self):
        v = make_verifier()
        content = (
            "---\ntitle: T\n---\n\n"
            "### Overview\n\nText.\n\n"
            "### Overview\n\nMore text.\n"
        )
        v.check_heading_duplicates("/path/Test.md", content=content)
        assert len(v.issues["duplicates"]) >= 1

    def test_near_duplicate(self):
        v = make_verifier()
        content = (
            "---\ntitle: T\n---\n\n"
            "### Strategic Initiatives\n\nText.\n\n"
            "### Strategic Initiative\n\nMore text.\n"
        )
        v.check_heading_duplicates("/path/Test.md", content=content)
        assert len(v.warnings["near_duplicate_heading"]) >= 1

    def test_case_variant(self):
        v = make_verifier()
        content = (
            "---\ntitle: T\n---\n\n"
            "### Overview\n\nText.\n\n"
            "### OVERVIEW\n\nMore text.\n"
        )
        v.check_heading_duplicates("/path/Test.md", content=content)
        assert len(v.warnings["case_variant_heading"]) >= 1

    def test_no_headings(self):
        v = make_verifier()
        content = "---\ntitle: T\n---\n\nJust text, no headings."
        v.check_heading_duplicates("/path/Test.md", content=content)
        assert sum(len(s) for s in v.issues.values()) == 0

    def test_redundant_yaml_block(self):
        v = make_verifier()
        content = (
            "---\ntitle: T\ntype: company\n---\n\n"
            "# Body\n\n"
            "### Section\n\nText.\n\n"
            "---\ntitle: Duplicate\ncreated: 2025-01-01\n---\n"
        )
        v.check_heading_duplicates("/path/Test.md", content=content)
        assert len(v.warnings["redundant_yaml"]) >= 1

    def test_false_positive_suffix(self):
        """Headings with known false-positive suffixes should not flag."""
        v = make_verifier()
        content = (
            "---\ntitle: T\n---\n\n"
            "### Two Wheelers\n\nText.\n\n"
            "### Three Wheelers\n\nMore text.\n"
        )
        v.check_heading_duplicates("/path/Test.md", content=content)
        assert len(v.warnings["near_duplicate_heading"]) == 0


# ---------------------------------------------------------------------------
# _norm_heading and _heading_false_positive
# ---------------------------------------------------------------------------
class TestNormHeading:
    def test_lowercase(self):
        assert NotesVerifier._norm_heading("Overview") == "overview"

    def test_strip_punct(self):
        assert NotesVerifier._norm_heading("Key, Metrics!") == "key metrics"

    def test_multiple_spaces(self):
        assert NotesVerifier._norm_heading("A   B") == "a b"


class TestHeadingFalsePositive:
    def test_shared_suffix(self):
        v = make_verifier()
        assert v._heading_false_positive("Two Wheelers", "Three Wheelers") is True

    def test_on_prefix(self):
        v = make_verifier()
        assert v._heading_false_positive("On Technology", "Technology") is True

    def test_no_false_positive(self):
        v = make_verifier()
        assert v._heading_false_positive("Overview", "Financials") is False


# ---------------------------------------------------------------------------
# _totals and generate_report
# ---------------------------------------------------------------------------
class TestTotalsAndReport:
    def test_totals_zero(self):
        v = make_verifier()
        e, w = v._totals()
        assert e == 0
        assert w == 0

    def test_totals_with_issues(self):
        v = make_verifier()
        v.log_issue("yaml_structure", "/path/f.md", "test error")
        v.log_warning("tag_format", "/path/f.md", "test warning")
        e, w = v._totals()
        assert e == 1
        assert w == 1

    def test_generate_report(self, tmp_path):
        v = make_verifier()
        v.log_issue("yaml_structure", "/path/f.md", "test error")
        v.log_warning("tag_format", "/path/f.md", "test warning")
        v.stats["total_files"] = 5
        report_file = tmp_path / "report.txt"
        v.generate_report(str(report_file))
        text = report_file.read_text()
        assert "ERRORS: 1" in text
        assert "WARNINGS: 1" in text
        assert "test error" in text
        assert "test warning" in text

    def test_generate_report_no_errors(self, tmp_path):
        v = make_verifier()
        v.stats["total_files"] = 3
        report_file = tmp_path / "report.txt"
        v.generate_report(str(report_file))
        text = report_file.read_text()
        assert "All notes passed verification" in text


# ---------------------------------------------------------------------------
# process_directory
# ---------------------------------------------------------------------------
class TestProcessDirectory:
    def test_nonexistent_dir(self, capfd):
        v = make_verifier()
        result = v.process_directory("/nonexistent/path", "test")
        assert result is None

    def test_empty_dir(self, tmp_path):
        v = make_verifier()
        v.suppress_progress = True
        result = v.process_directory(str(tmp_path), "test")
        assert result == 0

    def test_with_file(self, tmp_path):
        v = make_verifier()
        v.suppress_progress = True
        (tmp_path / "Test_Co.md").write_text(
            "---\ntitle: Test Co\ntype: company\nnormalized_name: Test_Co\n"
            "market_cap: small_cap\npermalink: /companies/x/test_co\n"
            "tags:\n- entity_type/company\n---\n\n# Test Co\n\nReal content here.\n"
        )
        result = v.process_directory(str(tmp_path), "test")
        assert result == 1
        assert v.stats["total_files"] == 1


# ---------------------------------------------------------------------------
# log_issue / log_warning
# ---------------------------------------------------------------------------
class TestLogging:
    def test_log_issue(self):
        v = make_verifier()
        v.log_issue("yaml_structure", "/path/f.md", "desc")
        assert len(v.issues["yaml_structure"]) == 1
        assert v.issues["yaml_structure"][0]["file"] == "/path/f.md"

    def test_log_warning(self):
        v = make_verifier()
        v.log_warning("tag_format", "/path/f.md", "desc")
        assert len(v.warnings["tag_format"]) == 1

    def test_log_custom_bucket(self):
        v = make_verifier()
        v.log_issue("custom_bucket", "/path/f.md", "desc")
        assert "custom_bucket" in v.issues
