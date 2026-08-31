# FinData Knowledge Graph — Vault & Note Reference

Three synchronized layers: **SQLite** (`memory/research.db`), **markdown**
(`findata/`), and **Python helpers** (`helpers/`). DB schema (incl. the
DuckDB cache): [`schema.md`](schema.md). Graph engine & algorithms:
[`graph_design.txt`](graph_design.txt). System overview:
[`architecture.md`](architecture.md). **Check existing tools before writing
new ones.**

## Filesystem Layout

```
findata/
├── Companies/{Sector}/{Normalized_Name}.md   # company notes (filename == normalized_name)
├── Sectors/{Sector_Name}.md                  # 42 canonical sector notes
├── Super_Sectors/                            # super-sector notes (hierarchy tier)
├── Points_And_Figures/ The_Chatter/ The_PlotLines/   # OCR'd newsletter INPUTS (gitignored, not synced)
├── _pending_relations.txt                    # review queue: extraction misses (H4) + link-prediction suggestions (C2, origin=link_prediction)
└── verify_notes_report.txt                   # last validator output
```

## YAML Front Matter

**Authoritative key reference: [`doc/okf/frontmatter_keys.md`](schema/frontmatter_keys.md)
(GENERATED from the JSON Schemas in `doc/okf/` — do not edit by hand).**
The schemas are enforced structurally by `make static-checks`
("Frontmatter schema" check → `helpers/validators/frontmatter_schema.py`);
relational rules (normalized_name == filename, permalink sector == directory)
stay in `verify_notes` / `static_checks`. Schema targets are the
frontmatter-bearing note types: Companies (1,068), Sectors (42),
Super_Sectors (9). Newsletter editions (The_Chatter, Points_And_Figures,
The_PlotLines) intentionally carry no frontmatter.

### Company (example — see generated reference for the full contract)
```yaml
---
title: "Aarti Drugs"
type: company
ticker: AARTIDRUGS.NS          # null if unlisted (use `listed: false` to record why)
listed: false                  # optional, for known-unlisted
sector: Pharma                 # CAPITALIZED canonical sector name
industry: Drug Manufacturers - Specialty & Generic   # optional (GICS-style)
market_cap: mid_cap            # large_cap | mid_cap | small_cap | micro_cap | null
normalized_name: Aarti_Drugs   # MUST match filename minus .md, char-for-char
permalink: /companies/pharma/aarti_drugs   # lowercase, leading slash, includes sector
tags: [entity_type/company, sector/pharma, market_cap/mid_cap, geography/india, ...]
created: '2025-11-16'
last_modified: '2026-07-07'
---
```
Body sections: Overview → Financial Profile (Yahoo Finance, dated) →
Product Portfolio / Segments → Management → Key Insights (newsletter, with
edition citation).

### Sector / Super_Sector
Key sets differ (sector adds `super_sector`; super_sector adds `file_path`;
neither has ticker/market_cap) — see the generated reference. Note: the
formerly documented `market_size` key does not exist in live notes and was
removed from this doc 2026-08-17 (schema-era correction).

## Tag System

Namespaces actually in live use: `entity_type/`, `sector/`, `market_cap/`,
`geography/` (india | global | emerging_markets), `business_model/`
(b2b | b2b2c | b2c | platform), `risk_investment/` (low_risk | medium_risk |
high_risk | dividend | high_growth), `industry/`, `financial_tags/`,
`investment_theme/`, `industry_characteristics/`, plus rare one-offs.
Apply relevant ones; abbreviate to save tokens.

`market_cap` buckets: `large_cap` >₹15,000 cr · `mid_cap` 3,000–15,000 ·
`small_cap` 500–3,000 · `micro_cap` <500 · NULL (unlisted). No
`mega_cap`/`nano_cap`/`unknown`/`unlisted` — collapsed 2026-07.

Tags are **not** stored on the `entities` row: note YAML `tags:` are mirrored
into `entity_tags(entity_name, tag)` by `helpers/core/sync_tags.py`
(`make sync-tags` after edits). Nine namespaces are mirrored — `entity_type/`, `sector/`, `market_cap/`,
`subsector/`, `holding_company/`, `geography/`, `business_model/`,
`risk_investment/`, `investment_theme/` — everything else stays note-only.

### Canonical sectors (42)
Carve-outs are checked before their parent catch-all during classification;
precedence rules live in `doc/procedures/markdown_parse.md` and
`helpers/core/parse_newsletter.py::guess_sector_for()`.

Agriculture · Automotive · Aviation · Banking · Building_Materials ·
Capital_Markets · Chemicals · Consumer · Defense · Diagnostics ·
Diversified · EMS_Manufacturing · Education_Training · Electronics ·
Energy · Engineering_Capital_Goods · FMCG · Fertilizer ·
Financial_Services · Fintech_Payments · Healthcare · Hospitals ·
Housing_Finance · Infrastructure · Insurance · International · Logistics ·
Media_Entertainment · Metals · Mining · NBFC · Packaging · Pharma ·
Railways · Real_Estate · Renewables · Retail · Semiconductors ·
Technology · Telecommunications · Textiles · Travel

## Sync Rules (MANDATORY — enforced by the validators)

1. **Filename** = `normalized_name` + `.md`, character-for-character.
2. **Format**: PascalCase, single underscores. No `&`, spaces, `(`, `)`,
   `-`, consecutive `__`, trailing `_`. ≤ 100 chars. Drop redundant
   suffixes (`Ltd`, `Limited`, `Company`).
3. **Entity `name`** must not end in `Ltd`/`Limited`/`Pvt`/`Private`
   (DB CHECK, company-scoped) — store the short form.
4. **`file_path`** (DB column) must resolve to an existing file under
   `findata/`.
5. **Permalink** lowercase with leading slash:
   `/companies/{sector}/{name}` · `/sectors/{name}`.
6. No duplicate `normalized_name`; no orphaned files.
7. Membership edges: create **both** directions — `(company, sector,
   part_of)` + `(sector, company, has_company)` — directly in
   `graph_edges` (never the read-only `relations` VIEW).

| ✅ Correct | ❌ Incorrect | Reason |
|---|---|---|
| `Asian_Paints.md` | `Asian_Paints_Company_Analysis.md` | extra words |
| `Mahindra_Mahindra.md` | `Mahindra_&_Mahindra_Ltd.md` | `&`, `Ltd` |

## Search & Workflow

Prefer **SQLite** for entity queries (ms) over filesystem scans; `rg` for
text; the FTS5 `note_search` table for content search. Tag queries join
`entity_tags` twice for intersections (examples: `schema.md` §entity_tags).

**Create entity:** insert `entities` row (with `normalized_name`,
`file_path`) → write the note at exactly that path → insert the two
`graph_edges` membership rows → run both validators
(`helpers/validators/verify_notes.py` +
`helpers/misc/database_integrity_check.py`). Full procedure:
`doc/procedures/markdown_parse.md`.

---
*Version 8.0 (2026-08-15) — DB section moved to schema.md (single source);
YAML/tag/permalink specs re-verified against the 1,068 live company notes;
entities now 5 entity kinds (see schema.md).*
