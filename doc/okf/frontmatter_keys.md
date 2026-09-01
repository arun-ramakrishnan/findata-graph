# Note frontmatter keys (GENERATED)

Generated from doc/okf/frontmatter.*.v1.json by
`python3 -m helpers.validators.frontmatter_schema --emit-doc`.
Do not edit by hand — edit the schema and regenerate.
Relational rules (normalized_name == filename, permalink sector ==
directory) live in helpers/validators/verify_notes.py + static_checks.py.

## company

Source: [`frontmatter.company.v1.json`](frontmatter.company.v1.json)

| key | required | type | constraint | description |
|---|---|---|---|---|
| `business_model` | no | string | pattern `^[a-z0-9_]+$` | Optional business-model classification (b2b, b2c, ...). |
| `created` | yes | string | pattern `^\d{4}-\d{2}-\d{2}$` | ISO calendar date (YYYY-MM-DD). NOTE: unquoted YAML dates are auto-parsed into date objects by PyYAML; the validator normalizes these to ISO strings before checking. |
| `exchange` | no | string | one of `NSE`, `NASDAQ`, `NYSE` | Optional listing exchange (observed set; extend the schema when a new one appears). |
| `file_path` | no | string | pattern `^findata/` | Legacy vault-relative path recorded by early writers; optional. |
| `generated` | no | object | — | OKF v0.2 §5.2 — who wrote this content and when (provenance). |
| `geography` | no | string | pattern `^[a-z_]+$` | Optional lowercase geography tag value (india, usa, global, south_korea, ...). |
| `index_membership` | no | null | — | Dropped key (2026-07-28); tolerated as null on legacy notes, absent on new ones. |
| `industry` | no | string? | min length 1 | Optional GICS-style industry description (present on ~87% of notes). |
| `last_modified` | yes | string | pattern `^\d{4}-\d{2}-\d{2}$` | ISO calendar date (YYYY-MM-DD). NOTE: unquoted YAML dates are auto-parsed into date objects by PyYAML; the validator normalizes these to ISO strings before checking. |
| `listed` | no | boolean | — | Optional explicit listing flag (used to record known-unlisted companies). |
| `market_cap` | yes | string? | one of `large_cap`, `mid_cap`, `small_cap`, `micro_cap` | Cap classification used for tags and search facets, or null when unknown. |
| `normalized_name` | yes | string | pattern `^[A-Za-z0-9&._\- ]+$` | Filename-derived canonical name; MUST match the filename minus .md char-for-char (enforced by verify_notes). |
| `permalink` | yes | string | pattern `^/companies/[a-z0-9_]+/[a-z0-9_]+$` | Stable lowercased identifier: /companies/<sector_slug>/<company_slug> (underscores, leading slash). |
| `risk_investment` | no | string | min length 1 | Optional risk/investment classification (growth, medium_risk, ...). |
| `sector` | yes | string | min length 1 | Canonical CAPITALIZED sector name; must equal the parent directory title (relational check). |
| `sources` | no | array | items: string | OKF v0.2 §5.1 — materials this concept derives from. |
| `stale_after` | no | string | pattern `^\d{4}-\d{2}-\d{2}$` | OKF v0.2 §5.5 — absolute stale date (YYYY-MM-DD); today >= stale_after => stale. |
| `status` | no | string | one of `draft`, `stable`, `deprecated` | OKF v0.2 §5.4 — lifecycle state; absent = stable. |
| `tags` | yes | array | items: pattern `^[a-z0-9_]+/[a-z0-9_]+$`; min 1 item(s) | Namespaced lowercase tags (prefix/value), e.g. entity_type/company, sector/pharma, market_cap/mid_cap, geography/india. |
| `ticker` | yes | string? | pattern `^([A-Z0-9&\-]{1,20}\.(NS/BO)/[A-Z0-9.\-]{1,10})$` | Exchange ticker, or null when unlisted. Indian: SYMBOL.NS / SYMBOL.BO (NSE/BOM). US: bare uppercase. Never the literal string 'N/A' — use null. |
| `title` | yes | string | min length 1 | Human-readable company name (display title). |
| `type` | yes | string | always `company` | Note type discriminator — always 'company' in Companies/. |
| `verified` | no | array | items: string | OKF v0.2 §5.2/§5.3 — independent confirmations. Array (never bare map) at write time. |

## sector

Source: [`frontmatter.sector.v1.json`](frontmatter.sector.v1.json)

| key | required | type | constraint | description |
|---|---|---|---|---|
| `created` | yes | string | pattern `^\d{4}-\d{2}-\d{2}$` | ISO calendar date (YYYY-MM-DD); PyYAML date objects are normalized before checking. |
| `generated` | no | object | — | OKF v0.2 §5.2 — who wrote this content and when (provenance). |
| `last_modified` | yes | string | pattern `^\d{4}-\d{2}-\d{2}$` | ISO calendar date (YYYY-MM-DD); PyYAML date objects are normalized before checking. |
| `normalized_name` | yes | string | pattern `^[A-Za-z0-9&._\- ]+$` | Filename-derived canonical name; matches filename minus .md. |
| `permalink` | yes | string | pattern `^/sectors/[a-z0-9_]+$` | Stable identifier: /sectors/<sector_slug>. |
| `sources` | no | array | items: string | OKF v0.2 §5.1 — materials this concept derives from. |
| `stale_after` | no | string | pattern `^\d{4}-\d{2}-\d{2}$` | OKF v0.2 §5.5 — absolute stale date (YYYY-MM-DD); today >= stale_after => stale. |
| `status` | no | string | one of `draft`, `stable`, `deprecated` | OKF v0.2 §5.4 — lifecycle state; absent = stable. |
| `super_sector` | yes | string | min length 1 | Parent super-sector title; must reference a Super_Sectors/ note (relational check). |
| `tags` | yes | array | items: pattern `^[a-z0-9_]+/[a-z0-9_]+$`; min 1 item(s) | Namespaced lowercase tags (entity_type/sector, sector/<slug>, geography/global, ...). |
| `title` | yes | string | min length 1 | Canonical CAPITALIZED sector name (matches directory name). |
| `type` | yes | string | always `sector` | Note type discriminator — always 'sector' in Sectors/. |
| `verified` | no | array | items: string | OKF v0.2 §5.2/§5.3 — independent confirmations. Array (never bare map) at write time. |

## super_sector

Source: [`frontmatter.super_sector.v1.json`](frontmatter.super_sector.v1.json)

| key | required | type | constraint | description |
|---|---|---|---|---|
| `created` | yes | string | pattern `^\d{4}-\d{2}-\d{2}$` | ISO calendar date (YYYY-MM-DD); PyYAML date objects are normalized before checking. |
| `file_path` | yes | string | pattern `^findata/Super_Sectors/[A-Za-z0-9_.\-]+\.md$` | Vault-relative path of this note (always present on super-sector notes). |
| `generated` | no | object | — | OKF v0.2 §5.2 — who wrote this content and when (provenance). |
| `last_modified` | yes | string | pattern `^\d{4}-\d{2}-\d{2}$` | ISO calendar date (YYYY-MM-DD); PyYAML date objects are normalized before checking. |
| `normalized_name` | yes | string | pattern `^[A-Za-z0-9&._\- ]+$` | Filename-derived canonical name; matches filename minus .md. |
| `permalink` | yes | string | pattern `^/super_sectors/[a-z0-9_]+$` | Stable identifier: /super_sectors/<super_sector_slug>. |
| `sources` | no | array | items: string | OKF v0.2 §5.1 — materials this concept derives from. |
| `stale_after` | no | string | pattern `^\d{4}-\d{2}-\d{2}$` | OKF v0.2 §5.5 — absolute stale date (YYYY-MM-DD); today >= stale_after => stale. |
| `status` | no | string | one of `draft`, `stable`, `deprecated` | OKF v0.2 §5.4 — lifecycle state; absent = stable. |
| `tags` | yes | array | items: pattern `^[a-z0-9_]+/[a-z0-9_]+$`; min 1 item(s) | Namespaced lowercase tags (entity_type/super_sector, super_sector/<slug>). |
| `title` | yes | string | min length 1 | Canonical super-sector title (e.g. 'Consumer Discretionary'). |
| `type` | yes | string | always `super_sector` | Note type discriminator — always 'super_sector' in Super_Sectors/. |
| `verified` | no | array | items: string | OKF v0.2 §5.2/§5.3 — independent confirmations. Array (never bare map) at write time. |

## newsletter

Source: [`frontmatter.newsletter.v1.json`](frontmatter.newsletter.v1.json)

| key | required | type | constraint | description |
|---|---|---|---|---|
| `generated` | no | object | — | OKF v0.2 §5.2 — who wrote this content and when (provenance). |
| `language` | no | string | — | Obsidian-publish language code (e.g. en); producers never emit it. |
| `last_updated` | no | string | pattern `^\d{4}-\d{2}-\d{2}$` | ISO calendar date (YYYY-MM-DD). NOTE: unquoted YAML dates are auto-parsed into date objects by PyYAML; the validator normalizes these to ISO strings before checking. |
| `permalink` | no | string | — | Obsidian-publish permalink on hand-managed notes; producers never emit it. |
| `sources` | no | array | items: string | OKF v0.2 §5.1 — materials this note derives from (the source PDF when kept under Reports/). |
| `stale_after` | no | string | pattern `^\d{4}-\d{2}-\d{2}$` | OKF v0.2 §5.5 — absolute stale date (YYYY-MM-DD); today >= stale_after => stale. |
| `status` | no | string | one of `draft`, `stable`, `deprecated` | OKF v0.2 §5.4 — lifecycle state; absent = stable. |
| `tags` | no | array | items: pattern `^[a-z0-9_]+/[a-z0-9_]+$`; min 1 item(s) | Machine-written tags (series/publisher always; company/ coverage optional). Flat tags are rejected. |
| `title` | yes | string | min length 1 | Edition title; frontmatter title, else first markdown heading, else file stem (producer rule). |
| `type` | yes | string | always `newsletter` | Note type discriminator — always 'newsletter' in the source trees. |
| `verified` | no | array | items: string | OKF v0.2 §5.2/§5.3 — independent confirmations. Array (never bare map) at write time. |
| `visibility` | no | string | — | Obsidian-publish visibility (e.g. public); producers never emit it. |

## proposal

Source: [`frontmatter.proposal.v1.json`](frontmatter.proposal.v1.json)

| key | required | type | constraint | description |
|---|---|---|---|---|
| `area` | yes | string | min length 1 | Primary code/doc surface (first segment of the bold-line Area header; archive topic dir as fallback). |
| `completed_md` | yes | string? | pattern `^\d+[a-z]?(?:\+\d+[a-z]?)?$` | The completed.md entry number as a string ('189'; suffix form '105b' for resolved duplicate numbers; '145+146' when one proposal spans several entries). Null while proposed. |
| `executed` | yes | ? | — | Null while proposed; the execution date once archived. |
| `filed` | yes | string | pattern `^\d{4}-\d{2}-\d{2}$` | ISO calendar date (YYYY-MM-DD). NOTE: unquoted YAML dates are auto-parsed into date objects by PyYAML; the validator normalizes these to ISO strings before checking. |
| `status` | yes | string | one of `proposed`, `executed` | 'proposed' while in proposals/; 'executed' once archived (directory agreement enforced by static_checks). |
| `title` | yes | string | min length 1 | From the # heading; imperative, names the mechanism. |
