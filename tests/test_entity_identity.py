"""entity_identity.name_key — S0 guarded camel split (name_key_letter_run_collapse §2.5 class 3).

The old unconditional ``_CAMEL_RE_1/_CAMEL_RE_2`` subs fractured legal-form
suffixes and abbreviations; :func:`_camel_split` refuses a boundary that
would leave a single-character fragment on either side.
"""

from helpers.core.entity_identity import _camel_split, name_key


def test_legal_form_suffixes_no_longer_split():
    assert name_key("Acme GmbH") == name_key("Acme Gmbh") == "acme gmbh"
    assert name_key("Acme SpA") == name_key("Acme Spa") == "acme spa"
    assert name_key("Acme ApS") == "acme aps"
    assert name_key("Acme KGaA") == "acme kgaa"


def test_abbreviations_no_longer_shred():
    assert name_key("ITeS") == "ites"
    assert name_key("EDandS") == "edands"
    assert name_key("GenX") == "genx"
    assert name_key("eBay") == "ebay"


def test_real_compounds_still_split():
    assert name_key("TechMahindra") == "tech mahindra"
    assert name_key("USSteel") == "us steel"
    assert name_key("HDFCBank") == "hdfc bank"
    assert name_key("McDonalds") == "mc donalds"
    assert name_key("IndianOil - Adani Gas") == "indian oil adani gas"
    assert name_key("NetwebTechnologiesIndia") == "netweb technologies india"
    assert _camel_split("TeleVijayGlobal") == "Tele Vijay Global"


def test_digit_adjacent_tokens_keep_their_casing_key():
    assert name_key("TATA 1MG") == name_key("Tata 1mg") == "tata 1mg"
    assert name_key("3M Company") == "3m company"


def test_fold_census_pairs_merge():
    assert name_key("Hille & Muller GmbH") == name_key("Hille Muller Gmbh")
    assert name_key("S A B Profil Gmbh") == name_key("S.A.B. Profil GmbH")
    assert name_key("Tata Technologies GmbH") == name_key("Tata Technologies Gmbh")
    assert name_key("TECOSIM , Gmbh") == name_key("Tecosim GmbH")
    assert name_key("Jio BLAST eSports") == name_key("Jio Blast Esports")


def test_short_fragment_guard_boundary():
    # a literal word-split legal form ('Gmb H') stays APART from the
    # glued suffix — the guard removes splits, it does not merge words
    assert name_key("Acme Gmb H") != name_key("Acme GmbH")


def test_letter_run_collapse_class1_legal_forms():
    """S1 — the 5 Class-1 subsidiary label pairs (name_key_letter_run_collapse §2.4).

    Runs of <=4 single alphanumeric tokens are joined post-casefold so a
    letter-spaced legal form keys identically to its glued twin.
    """
    pairs = [
        ("Designit A S", "Designit AS"),
        ("Designit Denmark A S", "Designit Denmark AS"),
        ("Designit Oslo A S", "Designit Oslo AS"),
        ("S A B Profiel BV", "SAB Profiel BV"),
        ("Tata Steel Belgium Services N V", "Tata Steel Belgium Services NV"),
    ]
    for spaced, glued in pairs:
        assert name_key(spaced) == name_key(glued), (spaced, glued)
    # exact expected keys pin the JOIN semantics (lambda, not \1 deletion)
    assert name_key("Designit A S") == "designit as"
    assert name_key("S A B Profiel BV") == "sab profiel bv"
    assert name_key("Tata Steel Belgium Services N V") == ("tata steel belgium services nv")


def test_letter_run_collapse_does_not_merge_distinct_entities():
    """The collapse is bounded (<=4 single-char tokens); ordinary multi-char
    words and genuine word boundaries must stay apart."""
    assert name_key("HDFC Bank") != name_key("HDFC Securities")
    assert name_key("Tech Mahindra") != name_key("Techmahindra")  # 2 real words
    assert name_key("Acme Gmb H") != name_key("Acme GmbH")
    assert name_key("Indian Oil Adani Gas") != name_key("Indian Oil Adani")  # no tail drop
    assert name_key("Jio Blast Esports") != name_key("Jioblast Esports")


def test_s4_normalisation_tiers_are_intentionally_distinct():
    """S4 (name_key_letter_run_collapse): the four normalisation homes are
    three deliberate tiers, not competing identity keys. Pin the contract so
    a future "converge the homes" refactor cannot silently over-merge.

    - name_key (Tier 1 identity) unifies dotted legal suffixes (S1) but keeps
      legal tails - it is NOT a filer detector.
    - normalize_name is a storage format (PascalCase underscores), idempotent.
    - _loose_key is a writer-side filer detector: it strips legal tails + THE.
    """
    from helpers.core.parse_newsletter import normalize_name
    from helpers.maintenance.related_party_sync import _loose_key

    # (1) S1 closed the genuine 2.2 defect: identity agrees with the writer
    # on dotted legal suffixes.
    assert name_key("Service Centre Maastricht B.V") == name_key("Service Centre Maastricht BV")

    # (2) identity KEEPS legal tails - stripping them would over-merge.
    assert name_key("The Indian Hotels Company Limited") != name_key("Indian Hotels")

    # (3) _loose_key IS the filer detector: the same pair collapses there.
    assert _loose_key("The Indian Hotels Company Limited") == _loose_key("Indian Hotels")

    # (4) normalize_name is a storage format, not an identity key.
    assert normalize_name("Designit A S") == "Designit_A_S"
    assert normalize_name(normalize_name("Designit A S")) == normalize_name("Designit A S")

    # (5) inner-space letter forms differ at the STORAGE tier while identity
    # unifies them - the two-tier model working as designed.
    assert normalize_name("Designit A S") != normalize_name("Designit AS")
    assert name_key("Designit A S") == name_key("Designit AS")
