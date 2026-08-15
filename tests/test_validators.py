"""
Tier 1: self-tests for the two load-bearing validators.

Each case seeds a specific defect into the synthetic vault/DB and asserts the
validator flags exactly that defect, and that good notes/entities pass clean.
This proves the tools actually catch what they claim to catch.
"""

from pathlib import Path


# --------------------------------------------------------------------------- #
# NotesVerifier (verify_notes.py)                                             #
# --------------------------------------------------------------------------- #
def _issue_files(verifier, bucket: str) -> set[str]:
    """Return the set of file paths flagged in a given issue bucket."""
    return {Path(i["file"]).name for i in verifier.issues[bucket]}


def test_missing_type_field_flagged(notes_verifier, fake_vault):
    f = fake_vault / "findata" / "Companies" / "Banking" / "Missing_Type.md"
    notes_verifier.check_yaml_structure(str(f))
    assert "Missing_Type.md" in _issue_files(notes_verifier, "yaml_structure")


def test_thin_content_flagged(notes_verifier, fake_vault):
    f = fake_vault / "findata" / "Companies" / "Banking" / "Thin_Content.md"
    notes_verifier.check_content_quality(str(f))
    assert "Thin_Content.md" in _issue_files(notes_verifier, "content_minimal")


def test_missing_heading_flagged(notes_verifier, fake_vault):
    f = fake_vault / "findata" / "Companies" / "Banking" / "No_Heading.md"
    notes_verifier.check_content_quality(str(f))
    assert "No_Heading.md" in _issue_files(notes_verifier, "content_missing_structure")


def test_bad_sector_permalink_flagged(notes_verifier, fake_vault):
    f = fake_vault / "findata" / "Sectors" / "Bad_Permalink_Sector.md"
    notes_verifier.check_yaml_structure(str(f))
    assert "Bad_Permalink_Sector.md" in _issue_files(notes_verifier, "yaml_structure")


def test_duplicate_tags_flagged(notes_verifier, fake_vault, tmp_path):
    f = tmp_path / "Dup_Tags.md"
    f.write_text(
        "---\n"
        "title: Dup Tags\n"
        "type: company\n"
        "tags:\n"
        "- entity_type/company\n"
        "- entity_type/company\n"
        "---\n"
        "# Dup Tags\n\nline one.\nline two.\n",
        encoding="utf-8",
    )
    notes_verifier.check_yaml_structure(str(f))
    assert "Dup_Tags.md" in _issue_files(notes_verifier, "yaml_structure")


def test_good_notes_pass_clean(notes_verifier, fake_vault):
    """A good company and a good sector must produce zero issues."""
    for rel in ["findata/Companies/Banking/Good_Bank.md", "findata/Sectors/Banking.md"]:
        f = fake_vault / rel
        notes_verifier.check_yaml_structure(str(f))
        notes_verifier.check_content_quality(str(f))
    all_flagged = set().union(*notes_verifier.issues.values())
    assert all(
        Path(i["file"]).name not in {"Good_Bank.md", "Banking.md"} for i in all_flagged
    ), "Good notes were incorrectly flagged"


def test_verify_all_returns_nonzero_on_defects(notes_verifier):
    """End-to-end over the whole synthetic vault: defects => nonzero issue count."""
    total = notes_verifier.verify_all()
    assert total > 0
    # The synthetic vault scanned exactly 6 files.
    assert notes_verifier.stats["total_files"] == 6


# --------------------------------------------------------------------------- #
# DatabaseIntegrityChecker (database_integrity_check.py)                      #
# --------------------------------------------------------------------------- #
def test_validate_file_path_unit(integrity_db):
    _, checker = integrity_db
    ok, msg = checker.validate_file_path("findata/Companies/Banking/Good_Bank.md")
    assert ok, msg
    ok, _ = checker.validate_file_path("findata/Companies/Banking/Missing.md")
    assert not ok
    ok, _ = checker.validate_file_path("")
    assert not ok
    ok, _ = checker.validate_file_path("findata/Companies/Banking/Bad-Name.md")
    assert not ok  # hyphen fails the PascalCase/underscore filename rule


def test_check_integrity_flags_seeded_defects(integrity_db):
    _, checker = integrity_db
    r = checker.check_integrity()

    # 5 entities total: 2 valid (Good Bank, Banking), 3 invalid.
    assert r["total_entities"] == 5
    assert r["valid_entities"] == 2
    assert r["invalid_entities"] == 3

    # Each defect category is hit exactly once.
    assert r["file_not_found"] == 1  # Ghost Co
    assert r["missing_file_paths"] == 1  # No Path Co
    assert r["invalid_filename"] == 1  # Bad Name Co

    flagged_names = {e["name"] for e in r["invalid_entities_list"]}
    assert flagged_names == {"Ghost Co", "No Path Co", "Bad Name Co"}


def test_clean_subset_hits_100_percent(fake_vault, tmp_path):
    """A DB with only valid entities must report a 100% validation rate."""
    import sqlite3

    from misc.database_integrity_check import DatabaseIntegrityChecker

    db_path = tmp_path / "clean.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        "CREATE TABLE entities (name TEXT PRIMARY KEY, entity_type TEXT, "
        "file_path TEXT, normalized_name TEXT, sector_classification TEXT, "
        "ticker TEXT);"
    )
    conn.executemany(
        "INSERT INTO entities (name, entity_type, file_path, normalized_name, "
        "sector_classification, ticker) VALUES (?,?,?,?,?,?)",
        [
            (
                "Good Bank",
                "company",
                "findata/Companies/Banking/Good_Bank.md",
                "Good_Bank",
                "Banking",
                "GOODBANK.NS",
            ),
            (
                "Banking",
                "sector",
                "findata/Sectors/Banking.md",
                "Banking",
                None,
                None,
            ),
        ],
    )
    conn.commit()
    conn.close()

    checker = DatabaseIntegrityChecker(db_path=str(db_path), base_path=str(fake_vault))
    r = checker.check_integrity()
    assert r["summary"]["validation_rate"] == 100.0
    assert r["invalid_entities"] == 0


# --------------------------------------------------------------------------- #
# New ERROR checks: name_sync, filename_format, type value                   #
# --------------------------------------------------------------------------- #
def _note(tmp_path, name, yaml_body, body="# X\n\nline one.\nline two.\n"):
    p = tmp_path / name
    p.write_text(f"---\n{yaml_body}\n---\n{body}", encoding="utf-8")
    return p


def test_name_sync_mismatch_is_error(notes_verifier, tmp_path):
    f = _note(
        tmp_path,
        "Acme_Corp.md",
        "title: Acme\ntype: company\nnormalized_name: Wrong_Name\npermalink: /companies/x/acme\n",
    )
    notes_verifier.check_yaml_structure(str(f))
    assert any(
        "does not match filename" in i["description"]
        for i in notes_verifier.issues["name_sync"]
    )


def test_missing_normalized_name_is_error(notes_verifier, tmp_path):
    f = _note(
        tmp_path,
        "Acme_Corp.md",
        "title: Acme\ntype: company\npermalink: /companies/x/acme\n",
    )
    notes_verifier.check_yaml_structure(str(f))
    assert any(
        "Missing 'normalized_name'" in i["description"]
        for i in notes_verifier.issues["name_sync"]
    )


def test_name_sync_passes_when_matching(notes_verifier, tmp_path):
    f = _note(
        tmp_path,
        "Acme_Corp.md",
        "title: Acme\ntype: company\nnormalized_name: Acme_Corp\npermalink: /companies/x/acme\n",
    )
    notes_verifier.check_yaml_structure(str(f))
    assert notes_verifier.issues["name_sync"] == []


def test_bad_type_value_is_error(notes_verifier, tmp_path):
    f = _note(
        tmp_path,
        "Acme_Corp.md",
        "title: Acme\ntype: compny\nnormalized_name: Acme_Corp\npermalink: /companies/x/acme\n",
    )
    notes_verifier.check_yaml_structure(str(f))
    assert any(
        "Invalid type" in i["description"]
        for i in notes_verifier.issues["yaml_structure"]
    )


def test_bad_filename_format_is_error(notes_verifier, tmp_path):
    # filename with a hyphen violates the PascalCase/underscore rule
    f = _note(
        tmp_path,
        "Bad-Name.md",
        "title: Bad\ntype: company\nnormalized_name: Bad-Name\npermalink: /companies/x/bad\n",
    )
    notes_verifier.check_filename_format(str(f))
    assert any(
        "Bad-Name" in i["description"] for i in notes_verifier.issues["filename_format"]
    )


def test_good_filename_passes(notes_verifier, tmp_path):
    f = _note(
        tmp_path,
        "Good_Name.md",
        "title: Good\ntype: company\nnormalized_name: Good_Name\npermalink: /companies/x/good\n",
    )
    notes_verifier.check_filename_format(str(f))
    assert notes_verifier.issues["filename_format"] == []


# --------------------------------------------------------------------------- #
# WARNING-level checks are advisory (do not appear in issues)                 #
# --------------------------------------------------------------------------- #
def test_company_bad_permalink_is_error(notes_verifier, tmp_path):
    """A company permalink NOT starting with '/companies/' is a gate-failing
    ERROR (all permalinks across the hierarchy are now strict)."""
    f = _note(
        tmp_path,
        "Acme_Corp.md",
        "title: Acme\ntype: company\nnormalized_name: Acme_Corp\npermalink: /wrong/place\n",
    )
    notes_verifier.check_yaml_structure(str(f))
    assert any(
        "permalink" in i["description"].lower()
        for i in notes_verifier.issues["yaml_structure"]
    )


def test_company_good_permalink_passes(notes_verifier, tmp_path):
    """A company permalink with the canonical '/companies/' prefix passes clean."""
    f = _note(
        tmp_path,
        "Acme_Corp.md",
        "title: Acme\ntype: company\nnormalized_name: Acme_Corp\npermalink: /companies/x/acme\n",
    )
    notes_verifier.check_yaml_structure(str(f))
    assert not any(
        "permalink" in i["description"].lower()
        for i in notes_verifier.issues["yaml_structure"]
    )


def test_bad_tag_format_is_warning(notes_verifier, tmp_path):
    f = _note(
        tmp_path,
        "Acme_Corp.md",
        "title: Acme\ntype: company\nnormalized_name: Acme_Corp\npermalink: /companies/x/acme\n"
        "tags:\n- Retail\n",
    )
    notes_verifier.check_yaml_structure(str(f))
    assert any(
        "Retail" in w["description"] for w in notes_verifier.warnings["tag_format"]
    )


def test_unknown_tag_value_is_warning(notes_verifier, tmp_path):
    """D3: a tag VALUE outside the known-good set for a controlled namespace
    must produce a tag_value warning (drift prevention)."""
    f = _note(
        tmp_path,
        "Acme_Corp.md",
        "title: Acme\ntype: company\nnormalized_name: Acme_Corp\npermalink: /companies/x/acme\n"
        "tags:\n- risk_investment/nonsense_value\n",
    )
    notes_verifier.check_yaml_structure(str(f))
    assert any(
        "nonsense_value" in w["description"]
        for w in notes_verifier.warnings["tag_value"]
    ), "unknown controlled-namespace value should warn"


def test_known_tag_value_does_not_warn(notes_verifier, tmp_path):
    """D3: a value IN the known-good set must NOT trip the tag_value check.
    Guards against the regression where _KNOWN_TAG_VALUES misses a real value
    (the bug that fired 37 false tag_value warnings during D3 development)."""
    f = _note(
        tmp_path,
        "Acme_Corp.md",
        "title: Acme\ntype: company\nnormalized_name: Acme_Corp\npermalink: /companies/x/acme\n"
        "tags:\n- risk_investment/growth\n- geography/india\n- business_model/b2b\n"
        "- investment_theme/renewable_energy\n",
    )
    notes_verifier.check_yaml_structure(str(f))
    assert not notes_verifier.warnings["tag_value"], (
        f"known-good values should not warn: {notes_verifier.warnings['tag_value']}"
    )


def test_free_vocabulary_namespace_is_not_checked(notes_verifier, tmp_path):
    """D3: open-vocabulary namespaces (industry/*, industry_characteristics/*,
    ...) are intentionally NOT pinned — any value should pass without a
    tag_value warning."""
    f = _note(
        tmp_path,
        "Acme_Corp.md",
        "title: Acme\ntype: company\nnormalized_name: Acme_Corp\npermalink: /companies/x/acme\n"
        "tags:\n- industry/anything_goes_here\n- confidence/high\n",
    )
    notes_verifier.check_yaml_structure(str(f))
    assert not notes_verifier.warnings["tag_value"]


# --------------------------------------------------------------------------- #
# D3-remainder: company title-quote + listed-missing checks                   #
# --------------------------------------------------------------------------- #

def test_company_quoted_title_is_warning(notes_verifier, tmp_path):
    """D3: a company title wrapped in double quotes must produce a
    company_title_quoted warning (mirrors the long-standing sector check). The
    canonical style is UNQUOTED; 421 titles were normalized in this pass."""
    f = _note(
        tmp_path,
        "Acme_Corp.md",
        'title: "Acme"\ntype: company\nticker: ACME\nnormalized_name: Acme_Corp\n'
        "permalink: /companies/x/acme\n",
    )
    notes_verifier.check_yaml_structure(str(f))
    assert notes_verifier.warnings["company_title_quoted"], (
        "quoted company title should warn"
    )


def test_company_unquoted_title_does_not_warn(notes_verifier, tmp_path):
    """D3: an unquoted company title must NOT trip company_title_quoted
    (the post-normalization steady state)."""
    f = _note(
        tmp_path,
        "Acme_Corp.md",
        "title: Acme\ntype: company\nticker: ACME\nnormalized_name: Acme_Corp\n"
        "permalink: /companies/x/acme\n",
    )
    notes_verifier.check_yaml_structure(str(f))
    assert not notes_verifier.warnings["company_title_quoted"]


def test_ticker_null_without_listed_is_warning(notes_verifier, tmp_path):
    """D3: ticker:null marks an unlisted company, which is a meaningful
    category. It must be made explicit with `listed: false`; absence is a
    listed_missing warning. Covers both `ticker: null` and a missing ticker
    field (data.get returns None for both)."""
    f = _note(
        tmp_path,
        "Acme_Corp.md",
        "title: Acme\ntype: company\nticker: null\nnormalized_name: Acme_Corp\n"
        "permalink: /companies/x/acme\n",
    )
    notes_verifier.check_yaml_structure(str(f))
    assert notes_verifier.warnings["listed_missing"], (
        "ticker:null without listed:false should warn"
    )


def test_ticker_null_with_listed_false_does_not_warn(notes_verifier, tmp_path):
    """D3: once `listed: false` is present, listed_missing must NOT fire
    (the post-normalization steady state for unlisted companies)."""
    f = _note(
        tmp_path,
        "Acme_Corp.md",
        "title: Acme\ntype: company\nticker: null\nlisted: false\n"
        "normalized_name: Acme_Corp\npermalink: /companies/x/acme\n",
    )
    notes_verifier.check_yaml_structure(str(f))
    assert not notes_verifier.warnings["listed_missing"]


# --------------------------------------------------------------------------- #
# DatabaseIntegrityChecker: relations + normalization (new checks)           #
# --------------------------------------------------------------------------- #
def test_relations_clean_for_well_formed_pair(integrity_db):
    """The fixture's Good Bank <-> Banking pair is fully bidirectional => 0 errors."""
    _, checker = integrity_db
    rel = checker.check_relations()
    assert rel["total"] == 2
    assert rel["errors"] == 0
    assert rel["part_of_without_has_company"] == 0
    assert rel["has_company_without_part_of"] == 0
    assert rel["orphaned"] == 0


def test_missing_bidirectional_pair_flagged(integrity_db):
    """A part_of without a matching has_company is a relation error."""
    import sqlite3

    db_path, checker = integrity_db
    conn = sqlite3.connect(db_path)
    # add an orphaned direction: a company part_of a sector with no has_company back
    conn.execute(
        "INSERT INTO entities (name, entity_type, file_path, normalized_name) "
        "VALUES ('Lonely Co','company','findata/Companies/Banking/Good_Bank.md','Lonely_Co')"
    )
    conn.execute(
        "INSERT INTO relations (source,target,relation_type) VALUES ('Lonely Co','Banking','part_of')"
    )
    conn.commit()
    conn.close()
    rel = checker.check_relations()
    assert rel["part_of_without_has_company"] >= 1
    assert rel["errors"] >= 1


def test_orphaned_relation_flagged(integrity_db):
    """A relation pointing at a non-existent entity is flagged as orphaned."""
    import sqlite3

    db_path, checker = integrity_db
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO relations (source,target,relation_type) "
        "VALUES ('Good Bank','Ghost Sector','has_company')"
    )  # Ghost Sector not an entity
    conn.commit()
    conn.close()
    rel = checker.check_relations()
    assert rel["orphaned"] >= 1


def test_entity_tags_clean_when_all_referenced(integrity_db):
    """R1: check_entity_tags reports 0 orphans when every tag's entity_name
    exists in entities."""
    import sqlite3

    _, checker = integrity_db
    # Seed a clean tag referencing an existing entity.
    db_path = integrity_db[0]
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO entity_tags (entity_name, tag) VALUES (?, ?)",
        ("Good Bank", "sector/banking"),
    )
    conn.commit()
    conn.close()
    et = checker.check_entity_tags()
    assert et["orphaned"] == 0
    assert et["total"] >= 1


def test_entity_tags_flags_orphaned_tag(integrity_db):
    """R1: a tag whose entity_name doesn't exist in entities is flagged as
    orphaned. Simulates a rename-without-cascade (FK-off regression)."""
    import sqlite3

    db_path, checker = integrity_db
    conn = sqlite3.connect(db_path)
    # Insert with FK OFF so the orphan row survives (matches the pattern
    # used by test_orphaned_relation_flagged above).
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO entity_tags (entity_name, tag) VALUES (?, ?)",
        ("Nonexistent Entity", "sector/banking"),
    )
    conn.commit()
    conn.close()
    et = checker.check_entity_tags()
    assert et["orphaned"] >= 1
    assert et["errors"] >= 1


def test_entity_tags_in_check_integrity(integrity_db):
    """R1: check_integrity() must include the entity_tags result so it
    shows up in the report + gate."""
    _, checker = integrity_db
    r = checker.check_integrity()
    assert "entity_tags" in r
    assert "orphaned" in r["entity_tags"]
    assert "total" in r["entity_tags"]


def test_orphan_companies_clean_for_seeded_company(integrity_db):
    """R2: the fixture's Good Bank has a part_of edge to Banking, so it is
    NOT counted as an orphan. The fixture also seeds three defect companies
    (Ghost Co, No Path Co, Bad Name Co) that have no part_of edge — those
    ARE legitimately orphaned by this check's definition (a company with no
    sector attachment). The contract pinned here: Good Bank is excluded
    from the orphan count, and the count equals the number of companies
    lacking a part_of edge (here, the 3 defect companies)."""
    _, checker = integrity_db
    oc = checker.check_orphan_companies()
    # Good Bank is attached; the 3 defect companies are not.
    assert oc["orphan_companies"] == 3
    assert oc["errors"] == 3
    assert oc["total_companies"] == 4  # Good Bank + 3 defects


def test_orphan_companies_flagged(integrity_db):
    """R2: a company with no part_of edge is flagged as orphaned. Simulates
    a company that slipped through parse_newsletter without a sector
    assignment, or a part_of edge deleted between syncs."""
    import sqlite3

    db_path, checker = integrity_db
    conn = sqlite3.connect(db_path)
    # Rival Co is a company with no part_of edge (the fixture only wires
    # Good Bank <-> Banking).
    conn.execute(
        "INSERT INTO entities (name, entity_type, file_path, normalized_name) "
        "VALUES ('Lonely Co','company','findata/Companies/Banking/Lonely_Co.md','Lonely_Co')"
    )
    conn.commit()
    conn.close()
    oc = checker.check_orphan_companies()
    assert oc["orphan_companies"] >= 1
    assert oc["errors"] >= 1


def test_orphan_companies_in_check_integrity(integrity_db):
    """R2: check_integrity() must include the orphan_companies result so it
    shows up in the report + the exit-code gate."""
    _, checker = integrity_db
    r = checker.check_integrity()
    assert "orphan_companies" in r
    assert "orphan_companies" in r["orphan_companies"]
    assert "total_companies" in r["orphan_companies"]
    """B1: the relations view/integrity gate must see Phase-2 edge types
    (competes_with, jv_with, etc.), not just part_of/has_company. Before
    the view-widening fix, these were filtered out and invisible to the
    integrity gate. Seeds a competes_with edge between two existing
    companies and asserts it shows up in `total` without tripping
    `unknown_type`."""
    import sqlite3

    db_path, checker = integrity_db
    conn = sqlite3.connect(db_path)
    # Add a second company so the competes_with edge has two valid endpoints.
    conn.execute(
        "INSERT INTO entities (name, entity_type, file_path, normalized_name) "
        "VALUES ('Rival Co','company','findata/Companies/Banking/Rival_Co.md','Rival_Co')"
    )
    conn.execute(
        "INSERT INTO relations (source,target,relation_type) "
        "VALUES ('Good Bank','Rival Co','competes_with')"
    )
    conn.commit()
    conn.close()
    rel = checker.check_relations()
    # Fixture baseline is 2 (the Good Bank <-> Banking pair); +1 for competes_with.
    assert rel["total"] == 3
    # competes_with is a known Phase-2 type — must NOT be flagged unknown.
    assert rel["unknown_type"] == 0
    assert rel["errors"] == 0


def test_unknown_edge_type_flagged(integrity_db):
    """B1: a typo'd edge_type (e.g. 'competes_wth') must be flagged by
    unknown_type. Before the fix this check was structurally dead — the
    view filtered to part_of/has_company, so the NOT IN allowlist could
    never match. Now that the view is unfiltered, the check is live."""
    import sqlite3

    db_path, checker = integrity_db
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO entities (name, entity_type, file_path, normalized_name) "
        "VALUES ('Rival Co','company','findata/Companies/Banking/Rival_Co.md','Rival_Co')"
    )
    conn.execute(
        "INSERT INTO relations (source,target,relation_type) "
        "VALUES ('Good Bank','Rival Co','competes_wth')"  # typo: missing 'i'
    )
    conn.commit()
    conn.close()
    rel = checker.check_relations()
    assert rel["unknown_type"] >= 1
    assert rel["errors"] >= 1


def test_part_of_specific_checks_still_fire_post_widening(integrity_db):
    """B1 regression guard: widening the view to all edge types must not
    break the part_of/has_company-specific checks (type_mismatch,
    po_no_hc, hc_no_po). Seeds a part_of edge with a swapped direction
    (company target instead of sector) and asserts type_mismatch fires."""
    import sqlite3

    db_path, checker = integrity_db
    conn = sqlite3.connect(db_path)
    # part_of where target is a company, not a sector — direction violation.
    conn.execute(
        "INSERT INTO entities (name, entity_type, file_path, normalized_name) "
        "VALUES ('Rival Co','company','findata/Companies/Banking/Rival_Co.md','Rival_Co')"
    )
    conn.execute(
        "INSERT INTO relations (source,target,relation_type) "
        "VALUES ('Good Bank','Rival Co','part_of')"  # target is company, not sector
    )
    conn.commit()
    conn.close()
    rel = checker.check_relations()
    assert rel["type_mismatch"] >= 1
    assert rel["errors"] >= 1


def test_normalization_clean_for_fixture(integrity_db):
    _, checker = integrity_db
    norm = checker.check_normalization()
    assert norm["missing"] == 0
    assert norm["duplicates"] == {}
    # The fixture intentionally includes one bad-format name (Bad-Name, hyphen).
    assert [b["normalized_name"] for b in norm["bad_format"]] == ["Bad-Name"]
    assert norm["errors"] == 1


def test_normalization_flags_duplicate(integrity_db):
    import sqlite3

    db_path, checker = integrity_db
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO entities (name, entity_type, file_path, normalized_name) "
        "VALUES ('Dup','company','findata/Companies/Banking/Good_Bank.md','Good_Bank')"
    )
    conn.commit()
    conn.close()
    norm = checker.check_normalization()
    assert "Good_Bank" in norm["duplicates"]
    assert norm["errors"] >= 1


def test_normalization_flags_bad_format_trailing_underscore(integrity_db):
    import sqlite3

    db_path, checker = integrity_db
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO entities (name, entity_type, file_path, normalized_name) "
        "VALUES ('Trailing','company','findata/Companies/Banking/Good_Bank.md','Trailing_Name_')"
    )
    conn.commit()
    conn.close()
    norm = checker.check_normalization()
    assert any(b["normalized_name"] == "Trailing_Name_" for b in norm["bad_format"])
    assert norm["errors"] >= 1


def test_orphaned_files_detected_as_warning(integrity_db, fake_vault):
    """A markdown file on disk with no matching normalized_name is an advisory warning."""
    (fake_vault / "findata" / "Companies" / "Banking" / "Mystery_Co.md").write_text(
        "# x\n", encoding="utf-8"
    )
    _, checker = integrity_db
    norm = checker.check_normalization()
    assert any("Mystery_Co.md" in f for f in norm["orphaned_files"])
    # orphaned files are warnings, not errors
    assert "Mystery_Co" not in str(norm["bad_format"])


# --------------------------------------------------------------------------- #
# Super-sector note checks (verify_notes.py — findata/Super_Sectors)          #
# --------------------------------------------------------------------------- #
_SUPER_SECTOR_GOOD = """\
---
title: Financials
type: super_sector
normalized_name: Financials
file_path: findata/Super_Sectors/Financials.md
permalink: /super_sectors/financials
tags:
- entity_type/super_sector
- super_sector/financials
created: '2026-08-04'
last_modified: '2026-08-04'
---
# Financials

A GICS-style super-sector grouping the following sectors.
"""

_SUPER_SECTOR_BAD_PERMALINK = """\
---
title: Energy_Super
type: super_sector
normalized_name: Energy_Super
file_path: findata/Super_Sectors/Energy_Super.md
permalink: sectors/energy_super
tags:
- entity_type/super_sector
created: '2026-08-04'
last_modified: '2026-08-04'
---
# Energy_Super

Overview text line one.
"""

_SUPER_SECTOR_UNEXPECTED_FIELD = """\
---
title: Industrials
type: super_sector
normalized_name: Industrials
file_path: findata/Super_Sectors/Industrials.md
permalink: /super_sectors/industrials
super_sector: Industrials
tags:
- entity_type/super_sector
created: '2026-08-04'
last_modified: '2026-08-04'
---
# Industrials

Overview text line one.
"""


def test_super_sector_good_note_passes(notes_verifier, tmp_path):
    """A well-formed super_sector note produces zero issues."""
    f = tmp_path / "Financials.md"
    f.write_text(_SUPER_SECTOR_GOOD, encoding="utf-8")
    notes_verifier.check_yaml_structure(str(f))
    all_flagged = set().union(*notes_verifier.issues.values())
    assert all(Path(i["file"]).name != "Financials.md" for i in all_flagged), \
        "Good super_sector note was incorrectly flagged as an ERROR"


def test_super_sector_bad_permalink_flagged(notes_verifier, tmp_path):
    """A super_sector permalink NOT starting with super_sectors/ is an ERROR."""
    f = tmp_path / "Energy_Super.md"
    f.write_text(_SUPER_SECTOR_BAD_PERMALINK, encoding="utf-8")
    notes_verifier.check_yaml_structure(str(f))
    assert "Energy_Super.md" in _issue_files(notes_verifier, "yaml_structure")


def test_super_sector_missing_leading_slash_is_error(notes_verifier, tmp_path):
    """A super_sector permalink WITHOUT the leading slash (super_sectors/x) is
    now a gate-failing ERROR. The generator was normalized to emit the
    canonical /super_sectors/ form, so the bare form is a regression to flag."""
    f = tmp_path / "Financials.md"
    f.write_text(_SUPER_SECTOR_GOOD.replace("/super_sectors/", "super_sectors/"),
                 encoding="utf-8")
    notes_verifier.check_yaml_structure(str(f))
    assert "Financials.md" in _issue_files(notes_verifier, "yaml_structure")


def test_super_sector_unexpected_field_flagged(notes_verifier, tmp_path):
    """A super_sector note with a `super_sector` uplink field (it IS the top
    level — no uplink expected) is flagged as an unexpected field ERROR."""
    f = tmp_path / "Industrials.md"
    f.write_text(_SUPER_SECTOR_UNEXPECTED_FIELD, encoding="utf-8")
    notes_verifier.check_yaml_structure(str(f))
    flagged = _issue_files(notes_verifier, "yaml_structure")
    assert "Industrials.md" in flagged


def test_super_sector_type_is_valid(notes_verifier, tmp_path):
    """type: super_sector must be in VALID_TYPES (not rejected as bad type)."""
    f = tmp_path / "Financials.md"
    f.write_text(_SUPER_SECTOR_GOOD, encoding="utf-8")
    notes_verifier.check_yaml_structure(str(f))
    # No yaml_structure error about the type value should fire.
    type_errors = [
        i for i in notes_verifier.issues["yaml_structure"]
        if "Invalid type" in i["description"]
    ]
    assert type_errors == []


# --------------------------------------------------------------------------- #
# check_hierarchy (database_integrity_check.py — sector hierarchy)
# --------------------------------------------------------------------------- #
def _seed_hierarchy(conn):
    """Seed a minimal clean 3-level hierarchy into the fixture DB.

    super_sector Financials <- sector Banking <- sub_sector Private_Sector.
    All belongs_to edges present and correctly typed.

    Uses INSERT OR IGNORE so it's idempotent against the fixture's existing
    rows (Banking is already seeded as a sector by integrity_db)."""
    conn.executemany(
        "INSERT OR IGNORE INTO entities (name, entity_type, file_path, normalized_name) "
        "VALUES (?, ?, ?, ?)",
        [
            ("Financials", "super_sector",
             "findata/Super_Sectors/Financials.md", "Financials"),
            ("Banking", "sector", "findata/Sectors/Banking.md", "Banking"),
            ("Private_Sector", "sub_sector", None, "Private_Sector"),
        ],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO relations (source, target, relation_type) VALUES (?, ?, ?)",
        [
            ("Banking", "Financials", "belongs_to"),
            ("Private_Sector", "Banking", "belongs_to"),
        ],
    )
    conn.commit()


def test_hierarchy_clean(integrity_db):
    """A complete, single-parent, acyclic hierarchy: all STRUCTURAL checks
    are 0 (orphans, multi_parent, cycles).

    Note: taxonomy_drift is NOT asserted here — it compares the live edges
    against the FULL curated taxonomy (build_sector_hierarchy.SUPER_SECTORS,
    ~63 mappings), and this minimal fixture only seeds 2, so drift is
    legitimately large. The structural correctness is what this test pins;
    taxonomy-drift-to-zero requires the full production graph."""
    import sqlite3
    db_path, checker = integrity_db
    conn = sqlite3.connect(db_path)
    _seed_hierarchy(conn)
    conn.close()
    hie = checker.check_hierarchy()
    assert hie["total_belongs_to"] == 2
    assert hie["sub_sector_orphans"] == 0
    assert hie["sector_orphans"] == 0
    assert hie["super_sector_orphans"] == 0
    assert hie["multi_parent"] == 0
    assert hie["cycles"] == 0
    # Structural errors (everything except taxonomy_drift) must be 0.
    structural_errors = (
        hie["sub_sector_orphans"] + hie["sector_orphans"]
        + hie["super_sector_orphans"] + hie["multi_parent"] + hie["cycles"]
    )
    assert structural_errors == 0


def test_hierarchy_sub_sector_orphan_flagged(integrity_db):
    """A sub_sector with no belongs_to parent is flagged as an orphan."""
    import sqlite3
    db_path, checker = integrity_db
    conn = sqlite3.connect(db_path)
    _seed_hierarchy(conn)
    # Add an orphan sub_sector (no belongs_to edge).
    conn.execute(
        "INSERT INTO entities (name, entity_type, file_path, normalized_name) "
        "VALUES ('Orphan_Sub','sub_sector',NULL,'Orphan_Sub')"
    )
    conn.commit()
    conn.close()
    hie = checker.check_hierarchy()
    assert hie["sub_sector_orphans"] >= 1
    assert hie["errors"] >= 1


def test_hierarchy_sector_orphan_flagged(integrity_db):
    """A sector with no belongs_to edge to a super_sector is flagged."""
    import sqlite3
    db_path, checker = integrity_db
    conn = sqlite3.connect(db_path)
    _seed_hierarchy(conn)
    # Add a sector with no super_sector uplink.
    conn.execute(
        "INSERT INTO entities (name, entity_type, file_path, normalized_name) "
        "VALUES ('Orphan_Sector','sector','findata/Sectors/Orphan.md','Orphan_Sector')"
    )
    conn.commit()
    conn.close()
    hie = checker.check_hierarchy()
    assert hie["sector_orphans"] >= 1
    assert hie["errors"] >= 1


def test_hierarchy_super_sector_orphan_flagged(integrity_db):
    """A super_sector with no incoming belongs_to edge is flagged."""
    import sqlite3
    db_path, checker = integrity_db
    conn = sqlite3.connect(db_path)
    _seed_hierarchy(conn)
    # Add a childless super_sector.
    conn.execute(
        "INSERT INTO entities (name, entity_type, file_path, normalized_name) "
        "VALUES ('Lonely_SS','super_sector','findata/Super_Sectors/Lonely.md','Lonely_SS')"
    )
    conn.commit()
    conn.close()
    hie = checker.check_hierarchy()
    assert hie["super_sector_orphans"] >= 1
    assert hie["errors"] >= 1


def test_hierarchy_multi_parent_flagged(integrity_db):
    """A sector linked to two different super_sectors violates the strict
    forest invariant (each child has exactly one parent)."""
    import sqlite3
    db_path, checker = integrity_db
    conn = sqlite3.connect(db_path)
    _seed_hierarchy(conn)
    # Banking already -> Financials. Add a second super_sector and link
    # Banking to it too (UNIQUE constraint allows different targets).
    conn.execute(
        "INSERT INTO entities (name, entity_type, file_path, normalized_name) "
        "VALUES ('Materials','super_sector','findata/Super_Sectors/Materials.md','Materials')"
    )
    conn.execute(
        "INSERT INTO relations (source, target, relation_type) "
        "VALUES ('Banking','Materials','belongs_to')"
    )
    conn.commit()
    conn.close()
    hie = checker.check_hierarchy()
    assert hie["multi_parent"] >= 1
    assert hie["errors"] >= 1


def test_hierarchy_cycle_flagged(integrity_db):
    """A belongs_to cycle (A->B->A) is structural corruption — the hierarchy
    is a strict 3-level forest, so any cycle must be caught."""
    import sqlite3
    db_path, checker = integrity_db
    conn = sqlite3.connect(db_path)
    _seed_hierarchy(conn)
    # Financials already <- Banking. Add a reverse edge Financials -> Banking
    # to create a cycle (Banking->Financials->Banking).
    conn.execute(
        "INSERT INTO relations (source, target, relation_type) "
        "VALUES ('Financials','Banking','belongs_to')"
    )
    conn.commit()
    conn.close()
    hie = checker.check_hierarchy()
    assert hie["cycles"] >= 1
    assert hie["errors"] >= 1


def test_hierarchy_in_check_integrity(integrity_db):
    """check_integrity() must include the hierarchy result so it shows up
    in the report + gate."""
    import sqlite3
    db_path, checker = integrity_db
    conn = sqlite3.connect(db_path)
    _seed_hierarchy(conn)
    conn.close()
    r = checker.check_integrity()
    assert "hierarchy" in r
    assert "errors" in r["hierarchy"]


# --------------------------------------------------------------------------- #
# check_cache_consistency (database_integrity_check.py — DuckDB vs SQLite)
# --------------------------------------------------------------------------- #
def test_cache_consistency_skips_when_file_abscent(integrity_db):
    """No graph.duckdb cache file present -> the check SKIPS (advisory
    WARNING, not an error). Keeps the gate green in DuckDB-less CI."""
    _, checker = integrity_db
    # The fake_vault fixture has no memory/graph.duckdb.
    cc = checker.check_cache_consistency()
    assert cc["skipped"] is True
    assert cc["errors"] == 0


def test_cache_consistency_schema_version_drift(integrity_db, monkeypatch):  # noqa: C901
    """_reconcile_cache returns schema_version_drift=1 when the cache's
    _build_meta.schema_version differs from the code constant. Tested via
    a fake DuckDB connection so no real .duckdb file is needed.

    The fake DuckDB con reports a drifted schema_version ('999') but
    MATCHING row counts (all zero). The SQLite side is stubbed to also
    report zero for every count, so the ONLY error is the drift."""

    class _FakeDuckCon:
        def execute(self, sql, *a, **kw):
            class _R:
                def __init__(self, row): self._row = row
                def fetchone(self): return self._row
                def fetchall(self): return [self._row] if self._row else []
            # _build_meta schema_version lookup -> drifted value.
            if "FROM _build_meta" in sql and "schema_version" in sql:
                return _R(("999",))  # drift vs code constant (7)
            # ATTACH is a no-op on the fake.
            if sql.strip().startswith("ATTACH"):
                return _R(None)
            # All row counts return 0 — matches the stubbed SQLite side.
            if "COUNT(*)" in sql:
                return _R((0,))
            return _R(None)
        def close(self): pass

    _, checker = integrity_db

    class _FakeSqliteCon:
        """Stubbed SQLite connection — returns 0 for every COUNT query and
        reports graph_edges as present (so the e_* reconciliation runs)."""
        def execute(self, sql, *a, **kw):
            class _R:
                def __init__(self, row): self._row = row
                def fetchone(self): return self._row
            if "FROM sqlite_master" in sql:
                return _R((1,))  # table exists (graph_edges / entities)
            return _R((0,))  # every COUNT(*) -> 0

    # Inject the stubbed connection so _reconcile_cache's SQLite lookups
    # don't touch the real fixture DB (which lacks graph_edges).
    monkeypatch.setattr(checker, "_conn", _FakeSqliteCon())
    cc = checker._reconcile_cache(_FakeDuckCon())
    assert cc["skipped"] is False
    assert cc["schema_version"] == "999"
    assert cc["schema_version_drift"] == 1
    assert cc["errors"] >= 1
    # Row counts all matched (both sides 0), so no row mismatches.
    assert cc["row_mismatches"] == []


# --------------------------------------------------------------------------- #
# Check registry conformance (Part C1)
# --------------------------------------------------------------------------- #
def test_registry_every_check_has_valid_severity():
    """Every Check in _CHECKS declares a severity of 'error' or 'warning'."""
    from helpers.misc.database_integrity_check import _CHECKS
    for chk in _CHECKS:
        assert chk.severity in ("error", "warning"), \
            f"{chk.name}: bad severity {chk.severity!r}"


def test_registry_every_method_resolves():
    """Every Check.method resolves to a callable on the checker instance."""
    from helpers.misc.database_integrity_check import (
        DatabaseIntegrityChecker, _CHECKS,
    )
    checker = DatabaseIntegrityChecker.__new__(DatabaseIntegrityChecker)
    for chk in _CHECKS:
        assert hasattr(checker, chk.method), \
            f"{chk.name}: method {chk.method!r} missing on checker"
        assert callable(getattr(checker, chk.method)), \
            f"{chk.name}: {chk.method!r} is not callable"


def test_registry_names_unique():
    """Check names are unique (they key the results dict)."""
    from helpers.misc.database_integrity_check import _CHECKS
    names = [c.name for c in _CHECKS]
    assert len(names) == len(set(names)), f"duplicate names: {names}"


def test_registry_check_integrity_includes_all_checks(integrity_db):
    """check_integrity() populates a result key for EVERY registered check."""
    _, checker = integrity_db
    from helpers.misc.database_integrity_check import _CHECKS
    r = checker.check_integrity()
    for chk in _CHECKS:
        assert chk.name in r, f"{chk.name}: missing from check_integrity results"


def test_graph_summary_present_and_advisory(integrity_db):
    """check_graph_summary returns the shape snapshot with zero errors
    (advisory only — never gate-failing)."""
    _, checker = integrity_db
    gs = checker.check_graph_summary()
    assert gs["errors"] == 0
    assert "entity_counts" in gs
    assert "edge_counts" in gs
    assert "sector_size_summary" in gs
    assert "market_cap_distribution" in gs


# --------------------------------------------------------------------------- #
# check_market_cap_conflicts (database_integrity_check.py — data quality)
# --------------------------------------------------------------------------- #
def test_market_cap_conflicts_clean(integrity_db):
    """An entity with a single market_cap tag -> 0 conflicts."""
    import sqlite3
    db_path, checker = integrity_db
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO entity_tags (entity_name, tag) VALUES (?, ?)",
        ("Good Bank", "market_cap/large_cap"),
    )
    conn.commit()
    conn.close()
    mc = checker.check_market_cap_conflicts()
    assert mc["errors"] == 0
    assert mc["conflicts"] == []


def test_market_cap_conflicts_flagged(integrity_db):
    """An entity with two market_cap/* tags is flagged — the DuckDB MIN()
    tiebreak would silently pick the wrong tier."""
    import sqlite3
    db_path, checker = integrity_db
    conn = sqlite3.connect(db_path)
    conn.executemany(
        "INSERT INTO entity_tags (entity_name, tag) VALUES (?, ?)",
        [
            ("Good Bank", "market_cap/large_cap"),
            ("Good Bank", "market_cap/mid_cap"),
        ],
    )
    conn.commit()
    conn.close()
    mc = checker.check_market_cap_conflicts()
    assert mc["errors"] == 1
    assert len(mc["conflicts"]) == 1
    assert mc["conflicts"][0]["entity"] == "Good Bank"
    assert set(mc["conflicts"][0]["tags"]) == {
        "market_cap/large_cap", "market_cap/mid_cap"}


def test_market_cap_conflicts_in_check_integrity(integrity_db):
    """check_integrity() must include the market_cap_conflicts result."""
    _, checker = integrity_db
    r = checker.check_integrity()
    assert "market_cap_conflicts" in r
    assert "conflicts" in r["market_cap_conflicts"]


# --------------------------------------------------------------------------- #
# check_validity_window (database_integrity_check.py — advisory coverage)
# --------------------------------------------------------------------------- #
def test_validity_window_reports_coverage(integrity_db):
    """Seeded edges with and without valid_from are counted correctly per
    edge type. The fixture's part_of edge has no valid_from; a seeded
    acquired edge with one should be counted separately."""
    import sqlite3
    db_path, checker = integrity_db
    conn = sqlite3.connect(db_path)
    # graph_edges isn't in the default fixture schema; add a row directly
    # to relations (the view the checker queries) won't work for valid_from.
    # Instead, create a minimal graph_edges table for this test.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS graph_edges ("
        "  source TEXT, target TEXT, edge_type TEXT,"
        "  valid_from TEXT, valid_to TEXT)"
    )
    conn.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, valid_from) "
        "VALUES (?, ?, ?, ?)",
        [
            ("Good Bank", "Rival Co", "acquired", "2024-01-15"),
            ("Good Bank", "Banking", "part_of", None),
        ],
    )
    conn.commit()
    conn.close()
    vw = checker.check_validity_window()
    assert vw["errors"] == 0  # advisory, never gate-failing
    by_type = vw["by_type"]
    assert by_type["acquired"]["total"] == 1
    assert by_type["acquired"]["with_valid_from"] == 1
    assert by_type["acquired"]["missing_valid_from"] == 0
    assert by_type["part_of"]["total"] == 1
    assert by_type["part_of"]["missing_valid_from"] == 1
    # acquired is a "should-be-dated" type, but its single edge HAS a date,
    # so warnings stays 0 here.
    assert vw["warnings"] == 0


def test_validity_window_warnings_count_should_be_dated(integrity_db):
    """A missing valid_from on 'acquired' (a should-be-dated type) counts
    toward warnings; missing on 'part_of' (never dated) does not."""
    import sqlite3
    db_path, checker = integrity_db
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS graph_edges ("
        "  source TEXT, target TEXT, edge_type TEXT,"
        "  valid_from TEXT, valid_to TEXT)"
    )
    conn.executemany(
        "INSERT INTO graph_edges (source, target, edge_type) VALUES (?, ?, ?)",
        [
            ("Good Bank", "Rival Co", "acquired"),       # missing date -> warns
            ("Good Bank", "Banking", "part_of"),         # missing date -> no warn
        ],
    )
    conn.commit()
    conn.close()
    vw = checker.check_validity_window()
    assert vw["warnings"] == 1  # only the acquired edge
    assert vw["by_type"]["acquired"]["missing_valid_from"] == 1
    assert vw["by_type"]["part_of"]["missing_valid_from"] == 1


def test_validity_window_in_check_integrity(integrity_db):
    """check_integrity() includes the validity_window result."""
    _, checker = integrity_db
    r = checker.check_integrity()
    assert "validity_window" in r
    assert "by_type" in r["validity_window"]
