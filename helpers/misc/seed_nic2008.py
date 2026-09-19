#!/usr/bin/env python3
"""NIC-2008 seed — build the tracked vocabulary JSON and converge the nic2008 table.

Build mode (operator-run, once per source refresh) parses the primary MoSPI
PDF (`nic_2008_17apr09.pdf` — the file the MoSPI NIC-2008 codefinder itself
serves) into ``helpers/misc/nic2008_seed.json``; vendored data is reviewed in
patches like code (the ``embed_eval_questions.json`` precedent).

Converge mode (maint-wired PRE_FULL) projects the ``nic2008`` table from the
tracked JSON: idempotent, roster-owned rows insert active, roster-dropped rows
supersede (#244 S1 converger semantics — never delete).

Primary-source PDF defects handled here (each verified against parent
context and recorded in ``defect_fixes`` in the seed):
- ``Division16:Manufactureofwood...`` — header line with collapsed spaces;
  title recovered from the document's own summary part.
- ``division 25`` — a wrapped NOTE continuation, not a header (headers carry
  a colon and a title).
- ``20203`` printed for ``20303``; ``65020`` for ``65200``; ``88230`` for
  ``84230``; ``95494`` for ``85494``; ``96903..96908`` for ``96093..96098``.
- bare code lines (``2920``, ``45402``) where PDF text extraction dropped
  the title; recovered from the next text line or the parent group title.
"""

from __future__ import annotations

import argparse
import hashlib
import difflib
import json
import pathlib
import re
import subprocess
import sys

HELPERS_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HELPERS_DIR.parent.parent
SEED_PATH = HELPERS_DIR / "nic2008_seed.json"
COMPANY_CODES_PATH = HELPERS_DIR / "nic2008_company_codes.json"
SOURCE_URL = (
    "https://www.mospi.gov.in/uploads/documents/publicDocuments/1760616645687-nic_2008_17apr09.pdf"
)
SOURCE_MD5 = "b28ab14f2bc151b1c58b3146b23df7fc"
VERSION = "NIC-2008"

# printed-code typos in the primary PDF -> canonical code. ONLY these two:
# every other odd-looking code (20203, 65020, 96903..96908) is OFFICIAL as
# printed — cross-checked against the DGE queryable tables (dge.gov.in), which
# list them verbatim. 20303/65200/96093..96098 do not exist in NIC-2008.
DEFECT_MAP: dict[str, str] = {
    "88230": "84230",  # Public order and safety activities (class 8423)
    "95494": "85494",  # Motor driving school non-professional (class 8549)
}

# Codes printed by BOTH official artifacts (MoSPI PDF and the DGE queryable
# tables, verbatim identical) whose prefix does not address their real class.
# Parent = the class whose block prints them in the PDF (DGE's own class column
# is a mechanical prefix slice — it names nonexistent classes 6502/9690).
PARENT_OVERRIDE: dict[str, str] = {
    "20203": "2030",  # filament yarn — printed between 20302 and 20304
    "65020": "6520",  # Reinsurance
    "96903": "9609",
    "96904": "9609",
    "96905": "9609",
    "96906": "9609",
    "96907": "9609",
    "96908": "9609",  # 9609x other personal services (Indian extensions)
}

# rows the parser must reproduce byte-equal (primary-source attestation;
# NOTE: the proposal's original "62090 IT services" pin was a misattestation —
# 62090 does not exist in NIC-2008; 62099 is the real IT-services row)
ATTESTED_SUBCLASSES = {
    "01111": "Growing of wheat",
    "10728": "Manufacture of molasses",
    "62099": "Other information technology and computer service activities n.e.c",
}

EXPECTED_COUNTS = {
    "sections": 21,
    "divisions": 88,
    "groups": 238,
    "classes": 419,
    # 1301, measured 2026-09-17 by full reconciliation of the MoSPI PDF against
    # the DGE queryable tables: 1295 shared codes + 6 zero-ending single
    # subclasses the PDF prints and DGE drops (01420 30120 31001 37001 47640
    # 81100). The proposal's "1,304" (and classification.codes' claim) is NOT
    # reproducible from either official source; 1301 is the primary-source
    # attestation actually backed by both.
    "subclasses": 1301,
}


def _pdf_to_text(pdf_path: pathlib.Path) -> str:
    res = subprocess.run(  # noqa: S603 — fixed binary, fixed args
        [  # noqa: S607 — pdftotext is the MoSPI extraction helper, PATH-resolved by design
            "pdftotext",
            "-layout",
            str(pdf_path),
            "-",
        ],  # noqa: S603 — fixed binary, fixed args
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout


def parse_pdf_text(txt: str) -> dict:  # noqa: C901 — PDF layout state machine, irreducibly branchy
    """Stateful parse of the NIC-2008 PDF text into the five-level tree."""
    lines = txt.splitlines()
    n = len(lines)
    stop = next((j for j, ln in enumerate(lines) if re.search(r"NIC-2008\s+NIC-2004", ln)), n)
    detailed_start = next(j for j, ln in enumerate(lines) if re.match(r"^\s*SECTION [A-U]\s*:", ln))

    # ---- summary part: Section/Division/Group titles (repair source)
    summary: dict = {"sections": [], "divisions": {}, "groups": {}}
    cur_sec = None
    sec_re = re.compile(r"^\s*Section ([A-U])\s+(.*)$")
    div_re = re.compile(r"^\s*Division (\d{2})\s{2,}(.+)$")
    grp_re = re.compile(r"^\s*Group (\d{3})\s{2,}(.+)$")
    for j in range(detailed_start):
        ln = lines[j]
        m = sec_re.match(ln)
        if m:
            cur_sec = m.group(1)
            summary["sections"].append({"code": m.group(1), "description": m.group(2).strip()})
            continue
        nxt = lines[j + 1] if j + 1 < n else ""
        m2 = re.match(r"^(\s{10,})(\S.*)$", nxt)
        wrap = m2 and not re.match(r"^\s*(Division|Group|Section|\d)", nxt)
        m = div_re.match(ln)
        if m and m.group(1) not in summary["divisions"]:
            title = m.group(2).strip()
            if wrap:
                title += " " + m2.group(2).strip()  # type: ignore[union-attr]
            summary["divisions"][m.group(1)] = {"section": cur_sec, "description": title}
            continue
        m = grp_re.match(ln)
        if m and m.group(1) not in summary["groups"]:
            title = m.group(2).strip()
            if wrap:
                title += " " + m2.group(2).strip()  # type: ignore[union-attr]
            summary["groups"][m.group(1)] = {"division": m.group(1)[:2], "description": title}

    # ---- detailed part
    skip_res = [
        re.compile(r"^\s*(Group\s+Class|Sub-|class$|Description$)\s*$"),
        # page-break column header, printed as ONE line mid-table (56 glued
        # rows + 29 glued notes before this landed); wrapped 'class' fragment
        # is covered above
        re.compile(r"^\s*Group\s+Class\s+Sub-\s+Description\s*$"),
        re.compile(r"^\s*SECTION [A-U]\s*$"),
        re.compile(r"^\s*[\d\s]*$"),
        re.compile(r"^\s*NIC[\s-]*2008"),
    ]
    dsec_re = re.compile(r"^\s*SECTION\s+([A-U])\s*:\s*(\S.*)$")
    ddiv_re = re.compile(r"^\s*Division\s*(\d{2})\s*:\s*(\S.*)$")
    code_re = re.compile(
        r"^\s{0,20}(\d{5}|\d{4}|\d{3})[ \t]*(\S.*)$"
    )  # some rows print code+title with no space (17022Manufacture...)
    bare_re = re.compile(r"^(\s{4,20}(\d{4})|\s{11,}(\d{5}))\s*$")
    note_start = (
        "this class includes",
        "this class excludes",
        "this class also includes",
        "this subclass includes",
        "this group includes",
        "this division includes",
        "this section includes",
        "this includes",
    )
    out: dict = {
        "sections": [],
        "divisions": [],
        "groups": [],
        "classes": [],
        "subclasses": [],
        "notes": {},
        "defect_fixes": [],
    }
    seen = {k: set() for k in ("sections", "divisions", "groups", "classes", "subclasses")}
    cur = {"section": None, "division": None, "group": None, "class": None}
    last: tuple[str, str] | None = None
    note_key: str | None = None
    note_buf: list[str] = []

    def flush_note() -> None:
        nonlocal note_key, note_buf
        if note_key and note_buf:
            out["notes"].setdefault(note_key, " ".join(note_buf).strip())
        note_key, note_buf = None, []

    def emit(level: str, row: dict) -> bool:
        code = row["code"]
        if code in seen[level]:
            return False
        seen[level].add(code)
        out[level].append(row)
        return True

    def join_desc(level: str, code: str, text: str) -> None:
        for row in out[level]:
            if row["code"] == code:
                prev = row["description"] or ""
                row["description"] = (prev + " " + text).strip()
                return

    for j in range(detailed_start, stop):
        ln = lines[j]
        mb = bare_re.match(ln)
        if mb:
            code = mb.group(2) or mb.group(3)
            flush_note()
            if len(code) == 4:
                fallback = summary["groups"].get(code[:3], {}).get("description")
                emit(
                    "classes",
                    {
                        "code": code,
                        "group": code[:3],
                        "division": cur["division"],
                        "section": cur["section"],
                        "description": fallback,
                    },
                )
                cur["group"] = code[:3]
                cur["class"] = code
                last = ("classes", code)
                out["defect_fixes"].append((code, "bare class line, title from group"))
            elif cur["class"] == code[:4] or code[:4] in seen["classes"]:
                cur["class"] = code[:4]
                emit("subclasses", {"code": code, "class": code[:4], "description": None})
                last = ("subclasses", code)
                out["defect_fixes"].append((code, "bare subclass line, title follows"))
            continue
        if any(rx.match(ln) for rx in skip_res):
            continue
        m = dsec_re.match(ln)
        if m:
            flush_note()
            cur["section"] = m.group(1)
            if m.group(1) not in seen["sections"]:
                out["sections"].append(
                    {"code": m.group(1), "description": m.group(2).strip().title()}
                )
                seen["sections"].add(m.group(1))
            last = None
            continue
        m = ddiv_re.match(ln)
        if m:
            flush_note()
            dcode = m.group(1)
            cur["division"] = dcode
            cur["group"] = None
            cur["class"] = None
            title = m.group(2).strip()
            if " " not in title[:40]:
                title = summary["divisions"].get(dcode, {}).get("description", title)
                out["defect_fixes"].append((f"division {dcode}", "collapsed title, from summary"))
            emit("divisions", {"code": dcode, "section": cur["section"], "description": title})
            last = ("divisions", dcode)
            continue
        m = code_re.match(ln)
        if m:
            code, rest = m.group(1), m.group(2).strip()
            if code in DEFECT_MAP:
                out["defect_fixes"].append((code, DEFECT_MAP[code]))
                code = DEFECT_MAP[code]
            if code in PARENT_OVERRIDE:
                out["defect_fixes"].append(
                    (code, f"parent {PARENT_OVERRIDE[code]} (prefix quirk, both official sources)")
                )
            length = len(code)
            if length == 5:
                want = PARENT_OVERRIDE.get(code, code[:4])
                if cur["class"] != want and want in seen["classes"]:
                    cur["class"] = want  # adopt emitted class (page-order drift)
                if cur["class"] == want:
                    flush_note()
                    if emit("subclasses", {"code": code, "class": want, "description": rest}):
                        last = ("subclasses", code)
                    continue
            elif length == 4:
                want = code[:3]
                if want in summary["groups"] and want not in seen["groups"]:
                    meta = summary["groups"][want]
                    emit(
                        "groups",
                        {
                            "code": want,
                            "division": meta["division"],
                            "section": summary["divisions"]
                            .get(meta["division"], {})
                            .get("section"),
                            "description": meta["description"],
                        },
                    )
                    out["defect_fixes"].append((f"group {want}", "header lost, from summary"))
                if cur["group"] != want and want in summary["groups"]:
                    cur["group"] = want
                if cur["group"] == want:
                    flush_note()
                    emit(
                        "classes",
                        {
                            "code": code,
                            "group": want,
                            "division": cur["division"],
                            "section": cur["section"],
                            "description": rest,
                        },
                    )
                    cur["class"] = code
                    last = ("classes", code)
                    continue
            else:
                want = code[:2]
                if want in summary["divisions"] and want not in seen["divisions"]:
                    meta = summary["divisions"][want]
                    emit(
                        "divisions",
                        {
                            "code": want,
                            "section": meta["section"],
                            "description": meta["description"],
                        },
                    )
                    out["defect_fixes"].append((f"division {want}", "header lost, from summary"))
                if cur["division"] != want and want in summary["divisions"]:
                    cur["division"] = want
                    cur["group"] = None
                    cur["class"] = None
                if cur["division"] == want:
                    flush_note()
                    emit(
                        "groups",
                        {
                            "code": code,
                            "division": want,
                            "section": cur["section"],
                            "description": rest or None,
                        },
                    )
                    cur["group"] = code
                    cur["class"] = None
                    last = ("groups", code)
                    continue
            continue
        text = ln.strip()
        if not text:
            continue
        low = text.lower()
        if last and low.startswith(note_start):
            flush_note()
            note_key = last[1]
            note_buf = [text]
            continue
        if note_key is not None:
            note_buf.append(text)
            continue
        if last:
            join_desc(last[0], last[1], text)
    flush_note()

    for lvl in ("sections", "divisions", "groups", "classes", "subclasses"):
        out[lvl].sort(key=lambda r: r["code"])
    return out


def _validate(parsed: dict) -> list[str]:  # noqa: C901 — attestation checks, one branch per invariant
    errors: list[str] = []
    for level, expected in EXPECTED_COUNTS.items():
        got = len(parsed[level])
        if got != expected:
            errors.append(f"{level}: {got} rows, expected {expected}")
    for code, want in ATTESTED_SUBCLASSES.items():
        row = next((r for r in parsed["subclasses"] if r["code"] == code), None)
        if row is None:
            errors.append(f"attested {code} missing")
        elif row["description"] != want:
            errors.append(f"attested {code}: {row['description']!r} != {want!r}")
    classes = {r["code"] for r in parsed["classes"]}
    groups = {r["code"] for r in parsed["groups"]}
    divisions = {r["code"] for r in parsed["divisions"]}
    sections = {r["code"] for r in parsed["sections"]}
    for row in parsed["subclasses"]:
        if row["class"] not in classes:
            errors.append(f"subclass {row['code']}: unknown class {row['class']}")
    for row in parsed["classes"]:
        if row["group"] not in groups:
            errors.append(f"class {row['code']}: unknown group {row['group']}")
    for row in parsed["groups"]:
        if row["division"] not in divisions:
            errors.append(f"group {row['code']}: unknown division {row['division']}")
    for row in parsed["divisions"]:
        if row["section"] not in sections:
            errors.append(f"division {row['code']}: unknown section {row['section']}")
    return errors


def build(pdf_path: pathlib.Path, out_path: pathlib.Path = SEED_PATH) -> dict:
    """Parse the primary PDF and write the tracked vocabulary JSON."""
    raw = pdf_path.read_bytes()
    md5 = hashlib.md5(raw, usedforsecurity=False).hexdigest()  # attestation tag, not security
    if md5 != SOURCE_MD5:
        print(f"WARNING: PDF md5 {md5} != attested {SOURCE_MD5} (source changed?)")
    parsed = parse_pdf_text(_pdf_to_text(pdf_path))
    errors = _validate(parsed)
    if errors:
        for e in errors:
            print(f"VALIDATION: {e}", file=sys.stderr)
        raise SystemExit("build failed validation (see above)")
    seed = {
        "version": VERSION,
        "source": {"file": pdf_path.name, "md5": md5, "url": SOURCE_URL},
        "counts": {k: len(parsed[k]) for k in EXPECTED_COUNTS},
        **{k: parsed[k] for k in EXPECTED_COUNTS},
        "notes": parsed["notes"],
        "defect_fixes": parsed["defect_fixes"],
    }
    out_path.write_text(json.dumps(seed, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {out_path} ({out_path.stat().st_size:,} bytes)")
    print("counts:", seed["counts"])
    print(f"defect fixes: {len(seed['defect_fixes'])}")
    return seed


# ---------------------------------------------------------------------------
# converge mode — project the nic2008 table from the tracked seed JSON
# ---------------------------------------------------------------------------

NIC2008_DDL = """
CREATE TABLE IF NOT EXISTS nic2008 (
  subclass    CHAR(5) PRIMARY KEY,  -- '01111'
  class       CHAR(4) NOT NULL,     -- '0111' (== ISIC Rev.4 class)
  grp         CHAR(3) NOT NULL,     -- '011' ('group' is reserved)
  division    CHAR(2) NOT NULL,     -- '01'
  section     CHAR(1) NOT NULL,     -- 'A'..'U'
  description TEXT NOT NULL,        -- 'Growing of wheat'
  isic4       CHAR(4) NOT NULL,     -- == class here
  nace21      TEXT,                 -- reserved, filled op-paced
  gics        TEXT,                 -- opaque peer code, never our own
  wikidata    TEXT,                 -- QID for the closeMatch hub
  scope_note  TEXT,                 -- PDF inclusion/exclusions (class-grain)
  version     TEXT NOT NULL DEFAULT 'NIC-2008'
)
"""


def ensure_schema(conn) -> None:
    """Create the nic2008 table where missing (idempotent)."""
    conn.execute(NIC2008_DDL)
    conn.commit()


def _wanted_rows(seed: dict) -> list[dict]:
    """Flatten the seed JSON into nic2008 table rows (scope notes denormalised
    from class grain onto every subclass of the noted class)."""
    classes = {r["code"]: r for r in seed["classes"]}
    groups = {r["code"]: r for r in seed["groups"]}
    divisions = {r["code"]: r for r in seed["divisions"]}
    rows = []
    for sub in seed["subclasses"]:
        cls = classes[sub["class"]]
        grp = groups[cls["group"]]
        div = divisions[grp["division"]]
        rows.append(
            {
                "subclass": sub["code"],
                "class": cls["code"],
                "grp": grp["code"],
                "division": div["code"],
                "section": div["section"],
                "description": sub["description"] or cls["description"],
                "isic4": cls["code"],
                "scope_note": seed["notes"].get(cls["code"]),
                "version": seed["version"],
            }
        )
    rows.sort(key=lambda r: r["subclass"])
    return rows


NIC2008_SCHEME_ID = "nic2008"
NIC2008_VERSION = "NIC-2008"
# seed-owned prefix (seed_concepts carves this out of ITS supersede pass —
# two convergers, two rosters, one prefix family, explicit ownership)
NIC2008_SOURCE_REF = f"seed:nic2008/{NIC2008_VERSION}"
NIC2008_SOURCE_URI = (
    "https://www.mospi.gov.in/uploads/documents/publicDocuments/1760616645687-nic_2008_17apr09.pdf"
)

# concept row = (concept_id, scheme_id, concept_code, pref_label, alt_label,
#                notation, broader_id, scope_note, source_ref)
_LEVELS = (
    ("sections", "code", "description", None),
    ("divisions", "code", "description", "section"),
    ("groups", "code", "description", "division"),
    ("classes", "code", "description", "group"),
    ("subclasses", "code", "description", "class"),
)


def scheme_concepts(seed: dict) -> dict[str, tuple]:
    """Five-level concept roster (S2): section→division→group→class→subclass.

    notation = the code itself (queryable ``notation = code`` per the
    proposal), pref_label = the primary-source description, scope_note =
    the class-grain This class includes/excludes statements (subclasses
    inherit through the broader chain — subtree() is the accessor).
    """

    rows: dict[str, tuple] = {}
    notes = seed.get("notes", {})
    for level, code_key, desc_key, parent_key in _LEVELS:
        for r in seed[level]:
            code = r[code_key]
            cid = f"{NIC2008_SCHEME_ID}:{code}"
            broader = f"{NIC2008_SCHEME_ID}:{r[parent_key]}" if parent_key else None
            scope = notes.get(code) if level == "classes" else None
            rows[cid] = (
                cid,
                NIC2008_SCHEME_ID,
                code,
                r[desc_key],
                None,
                code,
                broader,
                scope,
                NIC2008_SOURCE_REF,
            )
    return rows


def converge_scheme(conn, *, apply: bool) -> dict[str, int]:
    """Converge the nic2008 concept scheme (S2) — lifecycle-aware upsert.

    Same idiom as seed_concepts.seed(): roster rows land ``active``
    (insert or update), roster-lapsed seed-owned rows flip ``superseded``
    (never delete), foreign-owned rows (any other source_ref) untouched.
    The five-level chain self-references concepts.broader_id, so the
    apply pass defers FKs for the batch (sections land before divisions
    reference them).
    """
    sys.path.insert(0, str(HELPERS_DIR.parent.parent))
    from helpers.misc.seed_concepts import ensure_schema as ensure_concept_tables

    ensure_concept_tables(conn)  # concept_schemes/concepts DDL home (no copy)
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    roster = scheme_concepts(seed)
    # ownership domain = every nic2008 seed vintage (seed:nic2008/%), not
    # just the current one — a lapsed OLD-version row supersedes too
    live = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT concept_id, status FROM concepts WHERE source_ref LIKE 'seed:nic2008/%'",
        )
    }
    tracked_fields = ("pref_label", "notation", "broader_id", "scope_note")
    inserted = sum(1 for cid in roster if cid not in live)
    updated = 0
    for cid in roster:
        if cid not in live:
            continue
        row = conn.execute(
            "SELECT pref_label, notation, broader_id, scope_note FROM concepts"
            " WHERE concept_id = ?",
            (cid,),
        ).fetchone()
        # sqlite3.Row != tuple is always True — compare field-wise
        want = (roster[cid][3], roster[cid][5], roster[cid][6], roster[cid][7])
        if row is None or any(row[i] != want[i] for i in range(len(tracked_fields))):
            updated += 1
    superseded = sum(1 for cid, st in live.items() if st != "superseded" and cid not in roster)
    resurrected = sum(1 for cid in roster if live.get(cid) == "superseded")
    report = {
        "scheme_row": 1,
        "concepts": len(roster),
        "inserted": inserted,
        "updated": updated,
        "superseded": superseded,
        "resurrected": resurrected,
    }
    if apply:
        conn.execute("PRAGMA defer_foreign_keys = ON")
        conn.execute(
            "INSERT INTO concept_schemes (scheme_id, label, scheme_type, version,"
            " source_uri, license, attribution, active)"
            " VALUES (?,?,?,?,?,?,?,1)"
            " ON CONFLICT(scheme_id) DO UPDATE SET label=excluded.label,"
            " scheme_type=excluded.scheme_type, version=excluded.version,"
            " source_uri=excluded.source_uri, attribution=excluded.attribution,"
            " active=1",
            (
                NIC2008_SCHEME_ID,
                "National Industrial Classification 2008 (India)",
                "classification",
                NIC2008_VERSION,
                NIC2008_SOURCE_URI,
                None,
                "Central Statistics Office, MoSPI (Government of India)",
            ),
        )
        for cid, row in roster.items():
            conn.execute(
                "INSERT INTO concepts (concept_id, scheme_id, concept_code, pref_label,"
                " alt_label, notation, broader_id, scope_note, source_ref, status)"
                " VALUES (?,?,?,?,?,?,?,?,?, 'active')"
                " ON CONFLICT(concept_id) DO UPDATE SET pref_label=excluded.pref_label,"
                " alt_label=excluded.alt_label, notation=excluded.notation,"
                " broader_id=excluded.broader_id, scope_note=excluded.scope_note,"
                " status='active'",
                row,
            )
        lapsed = sorted(cid for cid, st in live.items() if st != "superseded" and cid not in roster)
        if lapsed:
            conn.executemany(
                "UPDATE concepts SET status='superseded' WHERE concept_id = ?",
                [(cid,) for cid in lapsed],
            )
        conn.commit()
    return report


# ---- S3: deterministic industry-label -> NIC candidate lane ------------------

CANDIDATE_SOURCE_REF = "seed:nic2008-candidates/v1"
OPERATOR_REVIEW_REF = "op:nic2008-review"  # operator-authored overrides (never seed-owned)
# SKOS mapping lanes — the crosswalk conflict gate is per (label, match_type),
# so an umbrella label can stack one code per lane (closeMatch + narrowMatch...)
MATCH_TYPES = ("exactMatch", "closeMatch", "narrowMatch", "broadMatch", "relatedMatch")

_STOPWORDS = frozenset(
    {
        "and",
        "of",
        "the",
        "for",
        "other",
        "others",
        "n",
        "e",
        "c",
        "nec",
        "except",
        "general",
        "miscellaneous",
        "various",
        "not",
        "elsewhere",
        "classified",
        "see",
        "any",
        "all",
        "a",
        "in",
        "to",
        "or",
    }
)


def _nic_tokens(text: str) -> set[str]:
    """Content tokens for lexical scoring (lowercase, punctuation-stripped)."""
    low = text.lower().replace("&", " and ")
    return {t for t in re.split(r"[^a-z0-9]+", low) if len(t) > 1 and t not in _STOPWORDS}


def _lex_score(label: str, desc: str) -> float:
    """Deterministic lexical affinity in [0, 1]; 0 = no signal.

    Token F1 (exact content-token overlap, 0.6) + substring containment
    (0.2) + difflib ratio on the normalized strings (0.2). The concept
    EMBEDDING lane does not exist for concepts (company_embeddings is
    company-grain), so S3 ships the lexical arm of the proposal's
    "concept-embedding/lexical lane" — no model load, no API.
    """
    lt, dt = _nic_tokens(label), _nic_tokens(desc)
    if not lt or not dt:
        return 0.0
    # prefix pairs (>=3 chars) catch morphology: airlines~air,
    # banks~bank — exact sets alone miss "Air transport" for "Airlines"
    overlap = sum(
        1
        for t in lt
        if t in dt or any(len(u) >= 3 and (t.startswith(u) or u.startswith(t)) for u in dt)
    )
    if overlap == 0:
        return 0.0  # no shared content token -> no signal, no filler rows
    f1 = 2 * overlap / (len(lt) + len(dt))
    ls = " ".join(sorted(lt))
    ds = " ".join(sorted(dt))
    contain = 1.0 if (ls in ds or ds in ls) else 0.0
    ratio = difflib.SequenceMatcher(None, ls, ds).ratio()
    return round(0.6 * f1 + 0.2 * contain + 0.2 * ratio, 4)


def suggest_for_label(
    label: str, subclasses: list[dict], *, k: int = 3
) -> list[tuple[str, float, str]]:
    """Top-k NIC subclasses for one industry label, deterministic order.

    Zero-affinity labels return [] (no filler) — the worklist names them
    with reason ``no lexical signal`` alongside the CIN second signal.

    Scorer v2 candidates (2026-09-18 sitting, both tried live and
    REVERTED): n.e.c. demotion 0.8x reshuffled noise rather than
    removing it (difflib long tail surfaced — education 85499 into
    Credit Services); a member-company-name vote carried identity
    words (Bharat Electronics -> electric motors). The skip class
    stays operator-owned: ``c CODE`` with CIN vintage evidence.
    """
    scored = [
        (_lex_score(label, r["description"]), r["code"], r["description"]) for r in subclasses
    ]
    scored = [s for s in scored if s[0] > 0]
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [(code, score, desc) for score, code, desc in scored[:k]]


def industry_labels(conn) -> list[tuple[str, int]]:
    """Distinct live industry labels with MEMBER counts (incidence-based).

    Each label is one hyper_edge row with N incidence members — counting
    edge rows would report 1 for every label; the incidence join is the
    real membership (falls back to edge-row count when the incidence
    table is absent).
    """
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='hyper_edges'"
    ).fetchone()
    if not has:
        return []
    has_inc = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='hyper_incidences'"
    ).fetchone()
    if not has_inc:
        return [
            (row[0], row[1])
            for row in conn.execute(
                "SELECT label, COUNT(*) FROM hyper_edges WHERE edge_type='industry'"
                " GROUP BY label ORDER BY label"
            )
        ]
    return [
        (row[0], row[1])
        for row in conn.execute(
            "SELECT e.label, COUNT(DISTINCT i.entity_name) FROM hyper_edges e"
            " LEFT JOIN hyper_incidences i ON i.edge_id = e.id"
            " WHERE e.edge_type='industry' GROUP BY e.label ORDER BY e.label"
        )
    ]


def converge_candidates(conn, *, apply: bool, k: int = 3) -> dict[str, int]:
    """Converge candidate industry->nic2008 mappings (S3 coding lane).

    Deterministic top-k suggestions land as ``candidate`` closeMatch rows
    (operator re-tags at promote via seed_concepts --promote-map). Labels
    with an already-active nic2008 mapping are DONE — their leftover
    candidates supersede and they are never re-suggested. Idempotent:
    the scorer is pure, so a second run reports 0/0.
    """
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    subclasses = seed["subclasses"]
    labels = industry_labels(conn)
    promoted = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT source_concept FROM concept_mappings"
            " WHERE source_scheme='industry' AND target_scheme='nic2008'"
            " AND status='active'"
        )
    }
    desired: set[tuple[str, str]] = set()
    suggested = 0
    for label, _members in labels:
        if label in promoted:
            continue
        tops = suggest_for_label(label, subclasses, k=k)
        suggested += len(tops)
        desired.update((label, code) for code, _s, _d in tops)
    # ONLY candidate rows are ours to retire — a promoted row keeps this
    # source_ref but belongs to the promote lane now, never superseded here
    live = {
        (row[0], row[1])
        for row in conn.execute(
            "SELECT source_concept, target_concept FROM concept_mappings"
            " WHERE source_ref = ? AND status = 'candidate'",
            (CANDIDATE_SOURCE_REF,),
        )
    }
    inserts = desired - live
    lapsed = live - desired  # includes candidates of since-promoted labels
    report = {
        "labels": len(labels),
        "labels_promoted": len(promoted),
        "suggested_pairs": len(desired),
        "inserted": len(inserts),
        "superseded": len(lapsed),
    }
    if apply:
        conn.executemany(
            "INSERT INTO concept_mappings (source_scheme, source_concept,"
            " target_scheme, target_concept, match_type, source_ref, version, status)"
            " VALUES ('industry', ?, 'nic2008', ?, 'closeMatch', ?, ?, 'candidate')",
            [(lbl, code, CANDIDATE_SOURCE_REF, NIC2008_VERSION) for lbl, code in sorted(inserts)],
        )
        conn.executemany(
            "UPDATE concept_mappings SET status='superseded'"
            " WHERE source_ref = ? AND source_concept = ? AND target_concept = ?"
            " AND status = 'candidate'",
            [(CANDIDATE_SOURCE_REF, lbl, code) for lbl, code in sorted(lapsed)],
        )
        conn.commit()
    return report


def label_members(conn) -> dict[str, list[str]]:
    """Label -> member entity names (incidence join, capped later)."""
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='hyper_incidences'"
    ).fetchone()
    if not has:
        return {}
    out: dict[str, list[str]] = {}
    for label, name in conn.execute(
        "SELECT e.label, i.entity_name FROM hyper_edges e"
        " JOIN hyper_incidences i ON i.edge_id = e.id"
        " WHERE e.edge_type='industry' ORDER BY e.label, i.entity_name"
    ):
        out.setdefault(label, []).append(name)
    return out


def latest_action_by_label(journal_path: pathlib.Path) -> dict[str, str]:
    """Latest journal decision per label ({} when no journal exists).

    Only terminal per-label actions count: ``approve`` and ``skip``.
    Read-back lives in the shared kit (parking semantics, S1 rehome).
    """
    from helpers.core.review_kit import latest_action_by

    return latest_action_by(journal_path, key_field="label")


def _stamp_industry_code_field(text: str, code: str) -> tuple[str, bool]:  # noqa: C901
    """Add/update the ``industry_code:`` frontmatter field; (text, changed).

    Line-level surgery mirroring enrich_from_yfinance._update_frontmatter —
    a full YAML re-render would reflow the writer's whole frontmatter block;
    the vault convention is field-scoped edits only.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return text, False
    fm_end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm_end = i
            break
    if fm_end is None:
        return text, False
    field_re = re.compile(r"^industry_code:\s*(.*)$")
    for i in range(1, fm_end):
        m = field_re.match(lines[i])
        if m:
            if m.group(1).strip() == f'"{code}"':
                return text, False  # idempotent no-op (quoted form is canonical)
            # quoted: YAML must parse it as a STRING (frontmatter convention —
            # bare numerics parse as int and fail the schema's string pattern)
            lines[i] = f'industry_code: "{code}"'
            return "\n".join(lines), True
    # insert as the industry field's sibling: after industry:, else sector:,
    # else ticker:, else right after the opening fence
    insert_at = 1
    for i in range(1, fm_end):
        if lines[i].startswith("industry:"):
            insert_at = i + 1
            break
        if lines[i].startswith(("sector:", "ticker:")):
            insert_at = i + 1
    lines.insert(insert_at, f'industry_code: "{code}"')
    return "\n".join(lines), True


def stamp_company_codes(conn, *, apply: bool, map_path: pathlib.Path | None = None) -> dict:
    """S3 — stamp per-member ``industry_code`` frontmatter for flagged labels.

    The tracked map (``helpers/misc/nic2008_company_codes.json``) names the
    flagged multi-division labels and carries per-company codes (``via: cin``
    vintage-checked seed or ``via: op`` operator-attested). Members present
    in the map get the field stamped; members WITHOUT an entry are reported
    as ``pending`` — the attestation sitting list, never auto-assigned.
    Codes outside the vendored NIC-2008 vocabulary are refused (skipped).
    """
    map_doc = json.loads((map_path or COMPANY_CODES_PATH).read_text(encoding="utf-8"))
    flagged = list(map_doc.get("flagged_labels", []))
    codes = map_doc.get("codes", {})
    vocab = {r["code"] for r in json.loads(SEED_PATH.read_text(encoding="utf-8"))["subclasses"]}
    members_by_label = label_members(conn)
    has_entities = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='entities'"
    ).fetchone()
    paths = dict(conn.execute("SELECT name, file_path FROM entities")) if has_entities else {}
    stamped: list[str] = []
    unchanged: list[str] = []
    pending: list[list[str]] = []
    skipped: list[list[str]] = []
    for label in flagged:
        for name in members_by_label.get(label, []):
            entry = codes.get(name)
            if not entry:
                pending.append([label, name])
                continue
            code = entry.get("code", "")
            if code not in vocab:
                skipped.append([label, name, code])
                continue
            fp = paths.get(name)
            note = (REPO_ROOT / fp) if fp else None
            if not note or not note.exists():
                skipped.append([label, name, "note-missing"])
                continue
            new_text, changed = _stamp_industry_code_field(note.read_text(encoding="utf-8"), code)
            if not changed:
                unchanged.append(name)
                continue
            if apply:
                note.write_text(new_text, encoding="utf-8")
            stamped.append(name)
    return {
        "flagged_labels": flagged,
        "stamped": stamped,
        "unchanged": unchanged,
        "pending_attestation": pending,
        "skipped": skipped,
        "apply": apply,
    }


def export_worklist(conn, out_path=None, *, journal_path: pathlib.Path | None = None) -> dict:
    """Export the operator coding worklist (S4) — suggestions + CIN signal.

    ``findata/Misc/nic_worklist.json``, the ``subsector_worklist.json``
    pattern: labels with top-3 NIC subclass suggestions (each carrying its
    exact promote command), the companies' own post-2008 ``cin_nic5``
    signal per label, and a ``no_signal`` section for labels the lexical
    lane could not score. The vault is writer-owned — this file is the
    sanctioned export surface; nothing auto-writes notes or frontmatter.
    """
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    subclasses = seed["subclasses"]
    desc_by_code = {r["code"]: r["description"] for r in subclasses}
    skip_j = (
        journal_path
        if journal_path is not None
        else REPO_ROOT / "outputs" / "nic_review" / "journal.jsonl"
    )
    skipped_labels = {lbl for lbl, act in latest_action_by_label(skip_j).items() if act == "skip"}
    labels = industry_labels(conn)
    # company membership + CIN facets per label
    cin_by_label: dict[str, dict[str, dict]] = {}
    members_by_label = label_members(conn)
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='hyper_incidences'"
    ).fetchone()
    if has:
        for label, name, nic5, year in conn.execute(
            "SELECT e.label, i.entity_name, en.cin_nic5, en.cin_year"
            " FROM hyper_edges e"
            " JOIN hyper_incidences i ON i.edge_id = e.id"
            " JOIN entities en ON en.name = i.entity_name"
            " WHERE e.edge_type = 'industry' AND en.cin_nic5 IS NOT NULL"
            " ORDER BY e.label, i.entity_name"
        ):
            entry = cin_by_label.setdefault(label, {})
            slot = entry.setdefault(
                nic5, {"count": 0, "companies": [], "nic2008": nic5 in desc_by_code}
            )
            slot["count"] += 1
            if len(slot["companies"]) < 3:
                slot["companies"].append(name)
            try:
                yr = int(str(year)[:4]) if year else 0
            except TypeError, ValueError:
                yr = 0
            # NEVER collapse to one vintage: the same nic5 can be native in
            # BOTH series with different meanings (65110 = banks in NIC-98,
            # life insurance in NIC-2008) — the split is the safety signal
            bucket = "post-2008" if yr >= 2008 else "pre-2008"
            slot.setdefault("vintage_counts", {"post-2008": 0, "pre-2008": 0})
            slot["vintage_counts"][bucket] += 1
    has_mappings = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='concept_mappings'"
    ).fetchone()
    promoted = (
        {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT source_concept FROM concept_mappings"
                " WHERE source_scheme='industry' AND target_scheme='nic2008'"
                " AND status='active'"
            )
        }
        if has_mappings
        else set()
    )
    suggested, no_signal, done, parked = [], [], [], []
    for label, members in labels:
        names = (members_by_label.get(label, []))[:8]
        tops = suggest_for_label(label, subclasses, k=3)
        entry = {
            "label": label,
            "members": members,
            "member_names": names,
            "suggestions": [
                {
                    "code": code,
                    "score": score,
                    "description": desc_by_code[code],
                    "promote": (
                        f"helpers/misc/seed_concepts.py --promote-map"
                        f" 'industry:{label}->nic2008:{code}:closeMatch' --apply"
                    ),
                }
                for code, score, _d in tops
            ],
            "cin_codes": cin_by_label.get(label, {}),
        }
        if label in promoted:
            done.append(entry)  # full shape — review() re-decisions need it
        elif label in skipped_labels:
            parked.append(entry)  # operator marked unsure — out of fresh walks
        elif not tops:
            no_signal.append({"label": label, "members": members, "member_names": names})
        else:
            suggested.append(entry)
    worklist = {
        "_note": (
            "nic2008_seed_table S4 coding worklist — suggestions are candidate"
            " closeMatch rows (concept_mappings, source_ref"
            " seed:nic2008-candidates/v1); promote via the printed command;"
            " cin_codes carry the companies' own CIN vintage signal (D-O6:"
            " pre-2008 codes are legacy series, never false-joined)"
        ),
        "suggested": suggested,
        "skipped": parked,
        "no_signal": no_signal,
        "promoted": done,
        "counts": {
            "labels": len(labels),
            "suggested": len(suggested),
            "skipped": len(parked),
            "no_signal": len(no_signal),
            "promoted": len(promoted),
        },
    }
    if out_path is not None:
        pathlib.Path(out_path).write_text(json.dumps(worklist, indent=2), encoding="utf-8")
    return worklist


def review(  # noqa: C901  # thin config over helpers.core.review_kit (S1 rehome, zero behavior change)
    conn,
    *,
    labels_filter: set[str] | None = None,
    limit: int | None = None,
    input_fn=input,
    print_fn=print,
    apply: bool = True,
    journal_dir: pathlib.Path | None = None,
    redecide: bool = False,
    skipped: bool = False,
) -> dict:
    """Interactive promotion review (S3 operator lane — no hand-edited JSON).

    Walks labels strongest-evidence-first (top lexical score, then CIN
    support), shows the top-3 suggestions with the CIN vintage split
    (the 65110 trap: same nic5, different meaning per series), and takes
    one keypress per label:

      1/2/3  approve suggestion n        s  skip this label
      c CODE approve a custom subclass code (validated against the table)
      ?      full evidence (all CIN codes + member names)
      q      quit, review the batch, apply what was approved
      x      abort, apply nothing

    Approved specs go through the SAME promote lane the CLI uses
    (seed_concepts.promote — plan-then-apply, batch-blocked on any
    invalid spec). Every decision is journaled to
    ``outputs/nic_review/journal.jsonl`` for audit; the journal is
    written by the tool, never read back (replays are new sittings).
    The loop/journal/confirm spine lives in ``helpers.core.review_kit``.
    """
    from helpers.core.review_kit import Journal, ReviewSession, assemble_entries

    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    desc_by_code = {r["code"]: r["description"] for r in seed["subclasses"]}
    members_by_label = label_members(conn)
    journal_path = (journal_dir or (REPO_ROOT / "outputs" / "nic_review")) / "journal.jsonl"
    wl = export_worklist(conn, out_path=None, journal_path=journal_path)
    active_by_label: dict[str, list[tuple[str, str]]] = {}
    for row in conn.execute(
        "SELECT source_concept, target_concept, match_type FROM concept_mappings"
        " WHERE source_scheme='industry' AND target_scheme='nic2008'"
        " AND status='active' ORDER BY target_concept"
    ):
        active_by_label.setdefault(row[0], []).append((row[1], row[2]))

    def show_evidence(e: dict) -> None:
        names = members_by_label.get(e["label"], [])
        if names:
            shown = ", ".join(names[:8])
            more = len(names) - 8
            print_fn(f"     members: {shown}" + (f" (+{more} more)" if more > 0 else ""))
        cin = e.get("cin_codes", {})
        if not cin:
            print_fn("     (no CIN evidence among members)")
            return
        for code, slot in sorted(cin.items(), key=lambda kv: -kv[1]["count"]):
            vc = slot["vintage_counts"]
            flag = "*" if slot["nic2008"] else " "
            print_fn(
                f"     {flag}{code} x{slot['count']} "
                f"(post-2008: {vc['post-2008']}, pre-2008: {vc['pre-2008']})"
                f" [{', '.join(slot['companies'])}]"
            )
        print_fn("     (* = code exists in the NIC-2008 table)")

    def render(idx: int, total: int, e: dict) -> None:
        label = e["label"]
        if label in active_by_label:
            stack = " + ".join(
                f"{c} {desc_by_code.get(c, '')[:38]} [{mt}]" for c, mt in active_by_label[label]
            )
            print_fn(f"[{idx}/{total}] {label}  ({e['members']} member(s))  ACTIVE: {stack}")
        else:
            print_fn(f"[{idx}/{total}] {label}  ({e['members']} member(s))")
        for n, s in enumerate(e.get("suggestions", []), 1):
            print_fn(f"  {n}) {s['code']} {s['score']:.2f} {s['description'][:70]}")
        show_evidence(e)

    def ask(e: dict, note) -> dict:
        label = e["label"]
        while True:
            ans = input_fn("  approve? 1-3 / c CODE / s / ? / q / x: ").strip()
            if ans in {"s", "q", "x"} or ans in {"1", "2", "3"}:
                break
            if ans.startswith("c ") and len(ans) > 2:
                code_try, _, mt_try = ans[2:].strip().partition(":")
                code_try = code_try.strip()
                mt_try = mt_try.strip() or "closeMatch"
                if mt_try not in MATCH_TYPES:
                    print_fn(
                        f"  {mt_try} is not a match type — try again ({'/'.join(MATCH_TYPES)})"
                    )
                    note(
                        {
                            "label": label,
                            "action": "bad-override",
                            "code": code_try,
                            "match_type": mt_try,
                        }
                    )
                    continue
                if code_try not in desc_by_code:
                    print_fn(f"  {code_try} is not a NIC-2008 subclass — try again")
                    note({"label": label, "action": "bad-override", "code": code_try})
                    continue
                return {
                    "label": label,
                    "action": "approve",
                    "code": code_try,
                    "match_type": mt_try,
                }
            if ans == "?":
                show_evidence(e)
                continue
            print_fn("  ? (1-3 / c CODE / s / ? / q / x)")
        if ans in {"s", "q", "x"}:
            return {"label": label, "action": {"s": "skip", "q": "quit", "x": "abort"}[ans]}
        code = e["suggestions"][int(ans) - 1]["code"]
        return {"label": label, "action": "approve", "code": code, "match_type": "closeMatch"}

    def spec_of(d: dict, e: dict) -> str:  # noqa: ARG001 — e unused: d carries both codes
        return f"industry:{d['label']}->nic2008:{d['code']}:{d['match_type']}"

    def post_approve(d: dict, e: dict) -> list[dict]:  # noqa: ARG001 — reads actives, not e
        # a re-decision supersedes only the SAME single-primary lane
        # (closeMatch/exactMatch); set lanes (narrowMatch...) accumulate
        out = []
        for a_code, a_mt in active_by_label.get(d["label"], []):
            if (
                a_mt == d["match_type"]
                and a_code != d["code"]
                and a_mt in ("closeMatch", "exactMatch")
            ):
                out.append(
                    {
                        "label": d["label"],
                        "action": "supersede-previous",
                        "code": a_code,
                        "match_type": a_mt,
                    }
                )
        return out

    def apply_batch(specs: list[str], decisions: list[dict]) -> tuple[list[str], list[str]]:
        from helpers.misc.seed_concepts import promote

        # candidate-backed approvals ride the promote lane; overrides to codes
        # with no candidate row are OPERATOR-authored actives (own source_ref —
        # the seed convergers never touch operator rows)
        candidate_keys = {
            (row[0], row[1])
            for row in conn.execute(
                "SELECT source_concept, target_concept FROM concept_mappings"
                " WHERE source_ref = ? AND status = 'candidate'",
                (CANDIDATE_SOURCE_REF,),
            )
        }
        promote_specs, override_rows = [], []
        for spec in specs:
            left, _, right = spec.partition("->")
            src = left.partition(":")[2]
            tgt, mt = right.split(":")[1], right.split(":")[2]
            if (src, tgt) in candidate_keys and mt == "closeMatch":
                promote_specs.append(spec)
            else:
                override_rows.append((src, tgt, mt))
        applied, errors = [], []
        if promote_specs:
            # a re-decision supersedes the label's previous active pick first,
            # else promote() sees it as a conflicting crosswalk
            redecided = sorted(
                {
                    (d["label"], d["code"], d.get("match_type", "closeMatch"))
                    for d in decisions
                    if d.get("action") == "supersede-previous"
                }
            )
            conn.executemany(
                "UPDATE concept_mappings SET status='superseded'"
                " WHERE source_scheme='industry' AND source_concept=?"
                " AND target_scheme='nic2008' AND target_concept=? AND match_type=?"
                " AND status='active'",
                redecided,
            )
            conn.commit()
            applied, errors = promote(conn, [], promote_specs)
        if override_rows:
            # a re-decision supersedes the label's previous active pick first
            redecided = sorted(
                {
                    (d["label"], d["code"], d.get("match_type", "closeMatch"))
                    for d in decisions
                    if d.get("action") == "supersede-previous"
                }
            )
            conn.executemany(
                "UPDATE concept_mappings SET status='superseded'"
                " WHERE source_scheme='industry' AND source_concept=?"
                " AND target_scheme='nic2008' AND target_concept=? AND match_type=?"
                " AND status='active'",
                redecided,
            )
            # upsert: a lane may already hold a candidate/superseded row with the
            # same key — flip it to active instead of violating the unique key
            for lbl, code, mt in override_rows:
                row = conn.execute(
                    "SELECT rowid FROM concept_mappings WHERE source_scheme='industry'"
                    " AND source_concept=? AND target_scheme='nic2008'"
                    " AND target_concept=? AND match_type=?",
                    (lbl, code, mt),
                ).fetchone()
                active_row = conn.execute(
                    "SELECT rowid FROM concept_mappings WHERE source_scheme='industry'"
                    " AND source_concept=? AND target_scheme='nic2008'"
                    " AND target_concept=? AND match_type=? AND status='active'",
                    (lbl, code, mt),
                ).fetchone()
                if active_row is not None:
                    continue  # already active in this lane — nothing to do
                if row is None:
                    conn.execute(
                        "INSERT INTO concept_mappings (source_scheme, source_concept,"
                        " target_scheme, target_concept, match_type, source_ref, version, status)"
                        " VALUES ('industry', ?, 'nic2008', ?, ?, ?, ?, 'active')",
                        (lbl, code, mt, OPERATOR_REVIEW_REF, NIC2008_VERSION),
                    )
                else:
                    conn.execute(
                        "UPDATE concept_mappings SET status='active', source_ref=?,"
                        " version=? WHERE rowid=?",
                        (OPERATOR_REVIEW_REF, NIC2008_VERSION, row[0]),
                    )
            conn.commit()
            applied += [
                f"mapping {lbl} -> nic2008:{code} [{mt}] (operator override)"
                for lbl, code, mt in override_rows
            ]
        return applied, errors

    entries = assemble_entries(
        wl,
        labels_filter=labels_filter,
        redecide=redecide,
        skipped=skipped,
        sort_key=lambda e: (
            -(e["suggestions"][0]["score"] if e.get("suggestions") else 0),
            e["label"],
        ),
        limit=limit,
    )
    return ReviewSession(
        entries=entries,
        journal=Journal(journal_path),
        render=render,
        ask=ask,
        spec_of=spec_of,
        apply_batch=apply_batch,
        post_approve=post_approve,
        input_fn=input_fn,
        print_fn=print_fn,
        apply_flag=apply,
    ).run()


def converge(conn, *, apply: bool) -> dict[str, int]:
    """Converge the nic2008 table to the tracked seed roster.

    Idempotent: insert rows missing from the table, update rows whose
    tracked fields changed. Rows present in the table but absent from the
    roster are REPORTED, never deleted (#244 S1 converger semantics; a
    roster shrink means a new source version — handle at source-refresh
    time, not silently here). S2 adds the concept-scheme projection
    (five-level broader chain, subtree()-native) in the same pass — one
    CLI, one maint step.
    """
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    ensure_schema(conn)
    wanted = {r["subclass"]: r for r in _wanted_rows(seed)}
    have = {
        row[0]: row
        for row in conn.execute(
            "SELECT subclass, class, grp, division, section, description,"
            " isic4, scope_note, version FROM nic2008"
        )
    }
    fields = (
        "class",
        "grp",
        "division",
        "section",
        "description",
        "isic4",
        "scope_note",
        "version",
    )
    inserts = [r for code, r in wanted.items() if code not in have]
    updates = []
    for code, r in wanted.items():
        if code not in have:
            continue
        changed = {
            f: r[f]
            for i, f in enumerate(fields)
            if have[code][i + 1] != r[f]  # have[code][0] is the subclass key
        }
        if changed:
            updates.append((code, changed))
    dropped = sorted(set(have) - set(wanted))
    report = {
        "inserted": len(inserts),
        "updated": len(updates),
        "dropped_reported": len(dropped),
        "table_rows": len(wanted),
    }
    if apply:
        for r in inserts:
            conn.execute(
                "INSERT INTO nic2008 (subclass, class, grp, division, section,"
                " description, isic4, scope_note, version)"
                " VALUES (:subclass, :class, :grp, :division, :section,"
                " :description, :isic4, :scope_note, :version)",
                r,
            )
        for code, changed in updates:
            sets = ", ".join(f"{f} = :{f}" for f in changed)
            conn.execute(
                f"UPDATE nic2008 SET {sets} WHERE subclass = :code",  # noqa: S608
                {"code": code, **changed},
            )
        conn.commit()
    scheme_report = converge_scheme(conn, apply=apply)
    return {**report, **{f"scheme_{k}": v for k, v in scheme_report.items()}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--db", type=pathlib.Path, default=None, help="SQLite path (default: live research.db)"
    )
    sub = parser.add_subparsers(dest="mode", required=True)
    p_build = sub.add_parser("build", help="parse the primary PDF into the tracked seed JSON")
    p_build.add_argument("pdf", type=pathlib.Path)
    p_build.add_argument("--out", type=pathlib.Path, default=SEED_PATH)
    p_conv = sub.add_parser(
        "converge", help="project the nic2008 table + scheme from the tracked seed JSON"
    )
    p_conv.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    p_cand = sub.add_parser(
        "candidates",
        help="converge candidate industry->nic2008 mappings (S3 lane; NOT maint-wired — suggestions are a sitting, not a projection)",
    )
    p_cand.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    p_cand.add_argument("--k", type=int, default=3, help="top-k suggestions per label (default 3)")
    p_wl = sub.add_parser(
        "worklist", help="export the operator coding worklist (findata/Misc/nic_worklist.json)"
    )
    p_wl.add_argument(
        "--out",
        type=pathlib.Path,
        default=None,
        help="output path (default findata/Misc/nic_worklist.json)",
    )
    p_stamp = sub.add_parser(
        "stamp",
        help="stamp per-member industry_code frontmatter for flagged labels (S3; map: nic2008_company_codes.json)",
    )
    p_stamp.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    p_rev = sub.add_parser(
        "review",
        help="interactive promotion review — approve/reject/override per label, batched via the promote lane",
    )
    p_rev.add_argument(
        "--labels",
        default=None,
        help="comma list of labels to review (default: all, strongest first)",
    )
    p_rev.add_argument("--limit", type=int, default=None, help="review only the first N labels")
    p_rev.add_argument("--dry-run", action="store_true", help="collect decisions, do not apply")
    p_rev.add_argument(
        "--redecide",
        action="store_true",
        help="include already-promoted labels (flip sittings; implied by --labels naming one)",
    )
    p_rev.add_argument(
        "--skipped",
        action="store_true",
        help="walk skip-parked labels only (the needs-better-labels pool)",
    )
    args = parser.parse_args(argv)
    if args.mode == "build":
        build(args.pdf, args.out)
    elif args.mode == "converge":
        sys.path.insert(0, str(HELPERS_DIR.parent.parent))
        from helpers.core.db import connect

        conn = connect(args.db)
        try:
            report = converge(conn, apply=args.apply)
        finally:
            conn.close()
        print(("APPLIED " if args.apply else "DRY-RUN ") + json.dumps(report))
    elif args.mode == "candidates":
        sys.path.insert(0, str(HELPERS_DIR.parent.parent))
        from helpers.core.db import connect

        conn = connect(args.db)
        try:
            report = converge_candidates(conn, apply=args.apply, k=args.k)
        finally:
            conn.close()
        print(("APPLIED " if args.apply else "DRY-RUN ") + json.dumps(report))
    elif args.mode == "worklist":
        sys.path.insert(0, str(HELPERS_DIR.parent.parent))
        from helpers.core.db import connect

        out = args.out or (REPO_ROOT / "findata" / "Misc" / "nic_worklist.json")
        conn = connect(args.db, read_only=True)
        try:
            wl = export_worklist(conn, out_path=out)
        finally:
            conn.close()
        print(f"wrote {out} — {json.dumps(wl['counts'])}")
    elif args.mode == "stamp":
        sys.path.insert(0, str(HELPERS_DIR.parent.parent))
        from helpers.core.db import connect

        conn = connect(args.db)
        try:
            report = stamp_company_codes(conn, apply=args.apply)
        finally:
            conn.close()
        print(("APPLIED " if args.apply else "DRY-RUN ") + json.dumps(report))
    elif args.mode == "review":
        sys.path.insert(0, str(HELPERS_DIR.parent.parent))
        from helpers.core.db import connect

        labels_filter = (
            {x.strip() for x in args.labels.split(",") if x.strip()} if args.labels else None
        )
        conn = connect(args.db)
        try:
            result = review(
                conn,
                labels_filter=labels_filter,
                limit=args.limit,
                apply=not args.dry_run,
                redecide=args.redecide,
                skipped=args.skipped,
            )
        finally:
            conn.close()
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
