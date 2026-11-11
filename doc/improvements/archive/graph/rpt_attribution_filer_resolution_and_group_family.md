---
title: "RPT attribution and filer resolution: entity-subject edges, ticker-bridged filers, and a curated family lane"
status: executed
filed: "2026-11-11"
executed: "2026-11-12"
completed_md: '507'
area: "RPT loader (helpers/maintenance/) and the ownership/group derivations (helpers/graph/)"
---

# RPT attribution and filer resolution: entity-subject edges, ticker-bridged filers, and a curated family lane

**Date:** 2026-11-11 · **Status:** EXECUTED (completed.md #507) ·
**Area:** the RPT loader (helpers/maintenance/related_party_sync.py,
helpers/maintenance/shareholding_sync.py) and the graph derivations that
read its edges (helpers/graph/derive_relational_insights.py,
helpers/graph/derive_hyperedges.py, helpers/graph/metapath.py)

## 1. Motivation

Trigger was a wrong answer, not an audit. Asked for the ultimate parent of
Reliance Industries, the store returns **Reliance Lithium Werks B.V** — a
Belgian battery subsidiary — and 238 downstream rows inherit that root.
Asked why the group hyperedge family shows **Adani = 2 members, Bajaj = 2,
Hinduja = 3**, review found the label scheme is one set per RPT filer, so a
corporate group has no single set at all.

Chasing those two reports found **three independent defects**, one of which
nobody had reported and which is larger than either: **136 of the 744 RPT
filers never resolve to an entity**, so 20.4% of the related-party corpus
never reaches the graph. This proposal records the measurement of all three,
the option space that was tested and rejected, and the change that fixes
them. The findings are not obvious from the data surface — each defect
mimics a data-quality problem in a different lane — which is why they are
written down here rather than fixed in passing.

All numbers below are measured on this box 2026-11-11 against
memory/research.db and memory/data/sources.duckdb, reproducible via the
scripts in bench_data/vigil_attribution/ (see Appendix). SQLite live and
DuckDB-over-Parquet were compared and agree; quoted values were
cross-checked against the live store.

| # | Defect | Mechanism | Blast radius |
|---|---|---|---|
| **C** | filer resolution | exact name join between the long legal listing name and the short entity name fails | 136 of 744 filers; 72,846 of 356,473 rows (20.4%) |
| **A** | attribution | the edge subject is the FILER, but 61.9% of rows disclose a different party as the subject | 220,549 rows; 3,021 of 22,146 comparable ultimate-parent roots move |
| **A-prime** | step-down polarity | step-down subsidiary rows are classed as forward but mean the opposite direction | 1 gate pin re-roots without it (365Squared) |
| **B** | group semantics | one hyperedge label per filer, so no corporate group exists as a set | 498 of 506 labels; Adani/Tata/Reliance split across flagships |

C is upstream of A and B: an unresolved filer contributes no rows to either.

## 2. Evidence (measured 2026-11-11, this box)

### 2.1 Defect C — 136 of 744 filers never resolve

`_resolve_companies` (helpers/maintenance/shareholding_sync.py:1149-1172)
resolves an RPT symbol to an entity by exact `_normalize()` equality
between `exchange_listings.name` and `entities.normalized_name`.
The two registries disagree in shape:

| exchange_listings.name (long legal) | entities.name (short) | _normalize() of the left |
|---|---|---|
| Adani Ports and Special Economic Zone Ltd. | Adani Ports and SEZ | Adani_Ports_and_Special_Economic_Zone |
| Hindalco Industries Ltd. | Hindalco | Hindalco_Industries |
| Titan Company Ltd. | Titan | Titan_Company |
| Indian Hotels Co. Ltd. | Indian Hotels | Indian_Hotels_Co |
| Larsen & Toubro Ltd. | Larsen and Toubro | Larsen_and_Toubro |
| Ambuja Cements Ltd. | Ambuja Cement | Ambuja_Cements |
| Procter & Gamble Hygiene and Health Care Limited | P&G Hygiene and Healthcare | Procter_and_Gamble_Hygiene_and_Health_Care |

The join therefore misses for every such symbol. Measured: **136 of 744**
distinct RPT filers resolve to nothing, carrying **72,846 of 356,473 rows
(20.4%)** and **9,129 distinct counter-parties** that are never mentioned by
any resolved filer. Affected filers include ADANIPORTS, HDFCBANK, ICICIBANK,
TITAN, LT, CIPLA, ONGC, DMART, LICI, VEDL, HINDALCO, INDHOTEL, PNB, IRCTC,
MPHASIS, DLF, LUPIN, TATAPOWER, UNITDSPR, NAUKRI, PAYTM, ZENSARTECH (full
list in bench_data/vigil_attribution/measurements.json).

**C1 fix — ticker bridge.** `entities.ticker` already carries the
exchange suffix (ADANIPORTS.BO, TITAN.NS, LT.NS, ARE&M.NS) while
`exchange_listings.symbol` is bare. Bridge on the ticker base,
preferring a .NS/.BO candidate when a base is ambiguous:

| measure | value |
|---|---|
| entities with a ticker | 6,079 (6,044 distinct bases) |
| ambiguous bases after NS/BO preference | 29 |
| ambiguous bases among the 136 | **0** |
| filers fixed by ticker | **134** |
| filers fixed only by an rpt_transactions.company_name fallback | 1 (LTIM) |
| filers still unresolved | **1 (GSPL, 417 rows)** |
| rows recovered | 72,429 of 72,846 |

GSPL has no entity at all: the registry holds GSPL Transmission (a different
company), GSPL India Gasnet and GSPL India Transco, but not **Gujarat State
Petronet**, which is what the filings name. That is an entity-coverage gap,
not a join defect, and is recorded as a residual rather than fixed here.

### 2.2 Defect A — the subject is the filer, not the disclosed party

`build_pairs` (helpers/maintenance/related_party_sync.py:352-377)
selects `entity_name` from every RPT row and then **never reads it**;
`apply_pairs` (:402-489) takes `companies.get(p["symbol"])` as
the subject and treats the counter-party as the other endpoint.
`company_name` is likewise never read (DDL column only).

Measured: entity_name differs from the filer on **220,549 of 356,473 rows
(61.9%)**. Classification over the differing subset: same_group 83,751 /
sub_fwd 40,205 / personnel-drop 38,057 / sub_rev 6,932 / unclassified 6,041
/ jv_with 3,886.

**Worked example — the reported row.** Stored edge:

    source 'Reliance Industries', target 'Reliance Lithium Werks B.V',
    edge_type 'subsidiary_of', source_ref 'vigil:rpt:RELIANCE',
    properties { relationship 'Holding Company',
                 rel_group 'Group Companies', period_end '31-MAR-2026',
                 amount_during_period 140000000.0 }

Three RPT rows match (symbol RELIANCE, counter_party Reliance Lithium Werks
B.V) — 31-MAR-2026, 31-MAR-2025, 30-SEP-2025 — and **all three carry
entity_name 'Lithium Werks China Manufacturing Co., Ltd.'**. The filing
says: the entity making the disclosure is Lithium Werks China, and its
holding company is Reliance Lithium Werks B.V. The stored edge inverts the
relationship: it claims Reliance Industries is a subsidiary of the battery
subsidiary. That single row is why ultimate_parent(Reliance Industries)
returns Reliance Lithium Werks B.V, and why 238 rows inherit it. Under
corrected attribution Reliance Industries becomes a self-root.

**A4 fix — subject = entity_name, with one carve-out and minting.** The
subject becomes the resolved RPT entity_name (minting where needed, the
existing max_new_entities cap applies); rows whose normalized relationship
contains **"of the company"** keep the filer as subject, because that phrase
means "relative to the filer" by construction.

Two options were measured and rejected:

- **A1** (entity_name always, no carve-out) — the "of the company" rows
  describe the filer's own relationships and would be re-attributed to a
  subsidiary, creating a fresh inversion of the same kind.
- **A2** (entity_name only when it already resolves, else filer) — proposed
  in the first draft menu and **refuted by measurement**: it leaves the
  reported Reliance inversion in place, because the subject string here
  *does* resolve (Lithium Werks China Manufacturing Co is a known entity).
  It re-attributes only the half of the corpus whose entities were already
  minted, which is not the half that is broken.

**Post-filing refinements — the attribution discriminator (measured
during implementation, 2026-11-11).** Implementation surfaced a question A4
alone does not answer: when the disclosed entity is some OTHER group
company, is the relationship text authoritative for the filer, for the
disclosed entity, or for the group? Three rounds were measured:

1. **Flatten every subject≠filer ownership pair to same_group** —
   refuted: same_group inflated 10,373 to 33,270 (3.2x) and destroyed real
   parent claims (Redington in-edges 69 to 7).
2. **Self-disclosure test** (trust ownership only when the filer wrote the
   row about itself, company_name == entity_name) — refuted: VOLTAS rows
   naming Universal MEP (entity == company_name == the filer) are still
   entity-relative truths; the text is not a filer-relative claim.
3. **Entity-relative reading (landed).** The decisive row is (entity_name
   REDINGTON RWANDA LTD, counter Redington Gulf FZE, relationship Holding
   company): Gulf FZE *is* Rwanda's parent — true only relative to
   entity_name. So sub_fwd/sub_rev/jv_with orientation is kept as the
   entity-relative reading implies, and ONLY generic holding-company/promoter
   texts (Subsidiary of Holding Company, Joint venture of Holding Company,
   Promoter Group) are flattened to same_group when the subject is not the
   filer itself (`_is_group_relative_text`). Bare Holding Company / Ultimate
   Holding Company stays a direct parent claim about the disclosed entity.

Two more implementation findings landed with it:

- **Trust-preserving edge dedupe.** The fold registry collapses long
  subsidiary names onto their parents (tata consultancy services saudi
  arabia to Tata Consultancy Services), so a subsidiary's UNTRUSTED Ultimate
  Holding Company row can key the SAME (src, dst, edge_type) as the filer's
  trusted Holding Company row and last-write-wins overwrites trust. Dedupe
  now never downgrades a trust-passing ownership edge — this is what kept
  TCS to Tata sons alive.
- **Loose-key filer variants.** Filing names like The Indian Hotels Company
  Limited resolved to a minted twin of the filer; a legal-tail and
  leading-The-stripping loose key maps them back to the filer (Indian
  Hotels: 4 entities to 1).

Measured replay (C1 + A4 + A-prime + existing trust filters):

| measure | live | corrected |
|---|---|---|
| distinct pairs | 48,391 (symbol, counter_party) | 90,870 (symbol, entity_name, counter_party) |
| ownership edges | 7,374 total (7,283 vigil) | 21,631 (21,139 after cycle rule) |
| root kinds (self / chain / cycle-guard) | 15,691 / 6,434 / 21 | 15,313 / 5,431 / 1,496 |
| comparable roots | — | 22,146 compared: 19,125 same, 3,021 changed, 94 new |
| group labels | 506 (498 vigil + 8 curated) | 469 |

### 2.3 Defect A-prime — step-down polarity

The classification ladder routes any text containing "subsidiar" to sub_fwd,
and step-down rows fall into it. But a "step down subsidiary" row means
**the subject is a step-down subsidiary OF the counterparty** — it must be
sub_rev.

Without the fix, ruling A alone re-roots the 365Squared gate pin:
365Squared would go to Route Mobile (UK) by cycle-guard
(365Squared > Route Mobile > Proximus Global SA/NV > Route Mobile (UK))
instead of its current Proximus Global SA/NV. That is a regression
disguised as a rule change.

**A-prime fix** — one guard in `classify_relationship` and its
canonical SQL mirror `_CLASSIFY_SQL` (they are pinned to agree by
`test_classify_mirror_agrees_with_sql`):

    POLARITY_REV = ('step down subsidiar', 'stepdown subsidiar', 'step-down subsidiar')

when the normalized text contains one of these and the class is sub_fwd,
flip to sub_rev. With it all four existing ultimate-parent gate pins hold,
and HDFC AMC International (IFSC) improves (§2.7).

**Post-landing finding (2026-11-12, family-curation pass) — the flip is
only half-right.** Re-reading the raw rows shows the SAME bare text is used
in BOTH directions across filings: Route Mobile files
(entity='Route Mobile (UK) Limited', counter='Proximus Global SA/NV',
'Step down subsidiary') — the ENTITY is the step-down sub, so sub_rev is
correct there; Ashok Leyland files (entity='Hinduja Tech Limited',
company_name='Ashok Leyland Limited', counter='Drive System Design Limited,
UK', 'Step down subsidiary') — the COUNTER is the entity's step-down sub,
so sub_fwd is correct and the flip INVERTS it (measured store artifact:
Ashok Leyland ⇄ Hinduja Leyland Finance ⇄ Hinduja Housing Finance closed a
cycle and rooted Ashok Leyland at Hinduja Leyland Finance, cycle-guard).
Population: 6,193 step-down rows across 141 filers; bare ambiguous texts
3,572 ('step down subsidiary' 2,202 + 'stepdown subsidiary' 500 + others),
plus directional texts that need no ruling ('step-down subsidiary of the
listed entity' 959, 'subsidiary including step-down subsidiaries' 352).
Resolution needs a per-row role discriminator (candidate signals: the
counter appearing elsewhere in the same symbol's rows under Holding
Company toward the entity, or the entity==filer self-disclosure split —
1,884 self vs 1,688 non-self among bare rows). NOT FIXED in this arc: the
Ashok Leyland store cycle was neutralized by the ruled Hinduja Automotive
fact (manual ownership edge; `min()` closure picks Hinduja Automotive over
Hinduja Leyland Finance deterministically). LANDED 2026-11-12: the per-row
rule WAS built as a measured follow-up slice — the polarity-slice and
fork-guard records below supersede this "parked" note.

#### 2.3.1 Polarity slice — landed 2026-11-12

**Polarity slice — LANDED 2026-11-12** (operator go, session m05105: 'we can proceed
with item 1 aka the polarity slice correction'). Measured rule
(`bench_data/stepdown_polarity/measure.py` + `measurements.json`): bare step-down
texts are decided PER PAIR, not per text — a filer's own rows re-route to sub_fwd
(the filer listing its own step-downs, ADVENZYMES anchor); a counter already declared
as the filer's ancestor elsewhere in the same symbol's filings keeps sub_rev (Route
Mobile (UK) -> Proximus Global SA/NV, ROUTE anchor); everything else defaults DOWN to
sub_fwd (Hinduja Tech -> Drive System Design , UK, AL anchor). 'of the listed entity'
non-self rows flatten to same_group via the group-relative rule; 'including step-down
subsidiaries' always points down. Population: 942 bare pairs -> 524 self->fwd / 192
ancestor->rev / 226 default->fwd. Loader: a `filer_ancestors` precompute and a
post-flatten re-route block in `apply_pairs` — `classify_relationship`'s text-level
default is unchanged, so the `_CLASSIFY_SQL` mirror stays clause-identical; three
verdict tests pin the three bare-text outcomes.

One residual defect surfaced by the replay: DISTINCT ON can hand a stale
columns-swapped row to the pair (Ashok Leyland filed 'Step Down Subsidiary' with the
COLUMNS swapped in a 30-SEP-2025 filing — entity=Drive System Design (UK),
counter=Hinduja Tech), and after two down-reroutes of the same pair the cross-ref
vote TIED (1 url vs 1 url) and the old tie rule kept both directions, minting an
ownership cycle (Ashok Leyland rooted via a Drive System Design loop). Fixed with a
surgical recency tiebreak: only REROUTE-MADE mutual conflicts resolve (an explicit
text, rank 2, beats a filer-self reroute, rank 1, beats a default-down reroute, rank
0; filing period '%d-%b-%Y' as the final tiebreak) — ties between EXPLICIT claims
still keep co-control, so the pre-existing 1,788 vigil-vigil mutual pairs are
untouched. n_vote_dropped 275 -> 316 (+41). Test:
`test_reroute_made_mutual_conflict_resolves_by_recency`.

Gate vs the polarity replay: REJECT with exactly 2 undeclared reasons, both real —
`uparent-hdfc-amc-international` ['HDFC AMC'] -> ['HDFC Bank'] (the earlier pin had
been keyed off a duplicate-entity artifact: 'HDFC AMC', 'HDFC Asset Management
Company' and 'HDFC Asset Management Co' are THREE entities; the cand replay resolved
the HDFCAMC filer's own rows to the long form, whose ->HDFC Bank edge now completes
closure at depth 2 — the short-form duplicate self-root was the artifact, and the
name-resolution debt itself is out of scope here) and
`edge-rallis-india-same-group` gaining 'Titan Company'. Declared:
`--expect-change edge-rallis-india-same-group,uparent-hdfc-amc-international` ->
ACCEPT, 182 questions, 0 reason(s). Post-fix probes on the replay: Reliance Industries
self-root; Ashok Leyland -> Hinduja Automotive (chain-root); Hinduja Tech -> Ashok
Leyland -> Hinduja Automotive; 365Squared -> Route Mobile -> Proximus Global SA/NV;
Rallis -> Tata Chemicals; TCS -> Tata sons — all correct.

#### 2.3.2 Replay-mint fork guard — landed 2026-11-12

**Replay-follow-up fix — replay-mint fork guard (2026-11-12, same arc).** The first
polarity replay minted three Indian-Hotels legal variants ('The Indian Hotels Company',
'Indian Hotels Company', 'THE INDIAN HOTELS CO' — counterparty rows of OTHER filers
whose exact normalized spelling hit nothing). The SECOND replay then bound filer
INDHOTEL to the mint: the NSE listing 'THE INDIAN HOTELS CO.' (segment NULL, the
'main' listing 'Indian Hotels Co. Ltd.' normalizes to nothing) exact-matched the mint
the moment it existed, so `_resolve_companies` re-bound the filer away from the
canonical 'Indian Hotels' and re-attributed every INDHOTEL edge to the fork — Indian
Hotels' 10 jv_with edges vanished (bank pin bank-sql-rel-jv-indian-hotels 10 -> 0),
its supplier edges moved to the mint, and the duplicate-entity debt fed on itself.
Fix: listing precedence stays (test_listing_resolution_still_wins), but when the
listing-normalization hit and exactly one ticker carrier for the symbol are the same
company under different spellings (loose key equal), the loader binds to the curated
carrier. INDHOTEL -> 'Indian Hotels' again; LTIM/CCL unchanged; GSPL now resolves
(the counter-path-minted 'Gujarat State Petronet' loosely equals its listing spelling),
recovering the ~417 formerly-lost rows with a single consistent node. The duplicate
entities themselves remain in the store — that is the name-resolution debt recorded in
§2.2 (fold/dedupe is a separate lane), but filer binding no longer follows it.

### 2.4 Not a defect — Adani and Tata have no disclosed parent

The operator also reported that group sizes are far below reality. Before
treating that as an algorithm problem, the filings were read per filer:

| filer | RPT rows | parent-side rows | what it actually discloses |
|---|---|---|---|
| ADANIPORTS | 6,643 | **0** | no holding/parent relationship anywhere |
| ADANIENT | 0 | 0 | the apex does not file RPT at all |
| ADANIGREEN | 5,215 | own subsidiaries | "Holding Company" rows name AGEL's subsidiaries, correctly sub_rev |
| ADANIPOWER | 1,395 | 36 "Immediate Holding" | little |
| ADANIENSOL | 3,897 | 1,655 co-membership rows naming **no controller** | "Entities over which ultimate controlling entity or KMP above have control" |
| ATGL | 548 | 3 "Fellow Subsidiary" | little |
| TATASONS | 0 | — | unlisted; never a filer |
| TCS | — | 189 "Ultimate Holding Company" | names Tata Sons Private Limited — the one reason Tata partly coalesces |

Only **106 of 744 filers** name an explicit controller, and only one filer
(ADANIENSOL, 133 rows) uses the co-membership phrasing at all.

Conclusion: **no algorithm recovers a parent that was never disclosed.** The
family question is a curation question, not a derivation question. That is a
finding, and it is the reason ruling B is split into "document what the
family is" (B0) and "add a curated lane" (B4) rather than "fix the merge".

### 2.5 The group family today

506 labels = 498 RPT disclosure sets (one per filer, 10,810 incidences) + 8
curated sets. Members 9,391; **12,849 of 22,240 companies (57.8%) are in no
group set.** Size distribution 2-5: 173 · 6-20: 198 · 21-100: 116 · 100+: 19.

Curated sets and their exact membership (properties.group):

| label | size | members |
|---|---|---|
| Tata | 7 | Rallis India, Tata Chemicals, TCS, Tata Technologies, Titan, Trent, Voltas |
| Aditya Birla | 4 | Grasim, Hindalco, Thomas Cook (India), UltraTech Cement |
| Hinduja | 3 | Ashok Leyland, Gulf Oil International, Hinduja Global Solutions |
| Muthoot Pappachan Group | 3 | Muthoot Capital, Muthoot Finance, Muthoot Microfin |
| Adani | 2 | Adani Enterprises, Adani Power |
| Bajaj | 2 | Bajaj Consumer Care, Bajel Projects |
| RP Sanjiv Goenka | 2 | Saregama India, TCI Express |
| TVS | 2 | Lucas Indian Service, TVS Supply Chain Solutions |

The union of RPT sets across the 12 Adani-ish labels is 124 members; across
the 16 Tata-ish labels, 209. So the *content* is largely present — it is
spread over per-filer sets and never reconciled into one.

### 2.6 Group-merge options measured and rejected

Every option below was implemented and measured over the corrected edge set,
not argued:

| option | result | verdict |
|---|---|---|
| **B1** re-key by corrected ultimate_parent | 469 groups; Adani still **4** (157/90/83/3), Tata **5** (226/51/27/26), Reliance 3 (179/17/12) | rejected — a different wrong answer |
| **B2** Jaccard 0.05 union-find | 398 families; Tata merges to 492, Adani to 3 | rejected — over-merges unrelated firms |
| **B3** Jaccard 0.00 | 341 families; one 891-member blob rooted at PFC | rejected — unusable |
| **B5** listed-company bridge | 368 families; TCS 556, "Reliance" 483 dominated by Reliance Foundation Institution of Education and Research, Tata 556 mixing Sir Dorabji Tata Trust | rejected — trusts and foundations are not groups |
| **B6** qualified bridge (group-ish relationship, ≤6 filers) | top family **1,492** from generic subsidiary-name collisions | rejected — worse |
| **B4-alpha** controller-keyed (label = the controller the filing names) | 140 groups, but only 76 of 498 filers name one; Adani, Hinduja, Reliance produce **nothing** | insufficient alone — kept as an *input* to curation |
| **B0** keep as-is, document the semantics | no merge; Adani 2, Tata 7 stay small | **recommended as the base** |
| **B4** add a curated family lane | 8 seed labels today | **recommended alongside B0** |

The measured mechanism for the failure: co-occurrence cannot distinguish a
corporate family from a shared trust, a shared ESOP vehicle, an unrelated JV,
or two subsidiaries of the same foreign parent. With 12,849 companies in no
set and unlisted shell names dominating the tail, every union-find variant
over-merges in exactly those places.

### 2.7 The gate cannot see any of this

helpers/misc/ontology_questions.json v7 has 179 questions. The four pins that
touch ownership are ultimate_parent_of for Rallis India (Tata Chemicals), TCS
(Tata sons), 365Squared (Proximus Global SA/NV) and HDFC AMC International
(IFSC) (HDFC Bank). **None is Reliance-based**, so the 238-row re-root passes
silently. The group pins cover `hyper-industry-*` and `hyper-sub-sector-*`; the
only group-membership pin is hyper-group-tata (7 curated members), which the
498 vigil labels do not touch.

Two supporting facts verified in code and data:

- `_STRICT_OWNERSHIP_REL` (helpers/graph/metapath.py:80-95) excludes
  "ultimate holding company", so several disclosed controllers are already
  filtered out of the closure.
- The live HDFC AMC International (IFSC) chain runs
  HDFC AMC International (IFSC) > HDFC AMC > HDFC Asset Management Company >
  HDFC Bank (depth 3) — it reaches HDFC Bank through a "Holding Company" row
  filed by a different entity in the chain. Under the corrected replay it
  re-roots to **HDFC AMC** (depth 1), which is the declared improvement the
  gate will be told to expect.

## 3. Decisions requested

Nothing is implemented until these are ruled; the recommendation column is
what the measurements support. The full ruling surface with every option and
its measurement is bench_data/vigil_attribution/menu.md (machine-local,
gitignored) and rulings.json; this section is the git-tracked record.

| # | Question | Recommendation | Why |
|---|---|---|---|
| **C** | How to resolve the 136 dropped filers | **C1** — ticker bridge (NS/BO preference), plus the company_name fallback | 134 + 1 of 136 recovered, 0 ambiguous bases among them |
| **A** | What is the subject of an RPT row | **A4** — entity_name with minting, "of the company" carve-out keeps the filer | fixes the reported inversion; A2 measured and refuted |
| **A-prime** | Flip step-down polarity | **on** | required for A; without it the 365Squared pin re-roots |
| **B** | What a group label means | **B0 + B4** — document it as a disclosure-scope lane, add a curated family lane | no algorithmic merge survives measurement; curation is the only path |

## 4. Design

Slices are independently landable and ordered by dependency. S1-S2 must
precede S3-S4; S5-S6 are independent of S1-S4 and can run in parallel.

**S0 — gate pins first (per doc/procedures/ontology-gate.md: "New SHAPES
land in the gate runner + tests first").** No gate *shape* is new here; the
existing ultimate_parent_of shape gains pins, and the group family gains one
membership pin. Add to helpers/misc/ontology_questions.json:

- uparent-reliance-industries — expected ["Reliance Industries"]: the pin
  that would have caught the report;
- uparent-adani-ports-sez — expected ["Adani Ports and SEZ"]: the pin that
  proves defect C is fixed (that entity appears in 459 vigil edges as a
  counter-party today but files none of its own);
- hyper-family-tata (only after B4 defines the lane).

**S1 — defect C (filer resolution).** In
helpers/maintenance/shareholding_sync.py:_resolve_companies, after the
existing normalized-name lookup fails:

1. build a ticker-base map from entities.ticker (strip the exchange suffix;
   prefer .NS then .BO when a base carries several Indian candidates; skip a
   base whose candidates are ambiguous);
2. fall back to rpt_transactions.company_name for the symbol when the ticker
   base misses (measured: fixes LTIM, the only case it is needed for).

Constant-time per symbol; no new table; no schema change. GSPL stays
unresolved and is counted, not silently dropped — the loader already
reports n_unresolved_filers, so the count drops 136 to 1.

**S2 — defect A (attribution).** In
helpers/maintenance/related_party_sync.py:

- _PAIRS_SQL (:324-348) — DISTINCT ON keys gain entity_name, so
  (symbol, entity_name, counter_party) is the pair identity instead of
  (symbol, counter_party). This is what makes the three Reliance Lithium
  Werks rows three pairs rather than one.
- build_pairs (:352-377) — stop selecting-and-discarding entity_name; carry
  it as the pair's subject candidate.
- apply_pairs (:402-489) — resolve the subject from entity_name (reuse the
  counter-party resolution ladder, including minting and the cap); keep the
  filer as subject when the normalized relationship contains "of the
  company"; when the subject resolves to the filer, keep today's behaviour.
- attribution guard (landed, refined by the 2.2 post-filing refinements):
  entity-relative orientation kept; generic holdco/promoter texts flattened
  to same_group only when the subject is not the filer
  (`_is_group_relative_text`); edge dedupe never downgrades a trust-passing
  ownership edge; a loose legal-name key maps filer variants back to the filer.

**S3 — defect A-prime (polarity).** One guard in classify_relationship and
its canonical SQL mirror `_CLASSIFY_SQL`; add the three `POLARITY_REV` prefixes
and flip sub_fwd to sub_rev after classification.

**S4 — re-derive and re-pin.** Run the pipeline in order:
refresh-vigil (APPLY=1), then derive-hyperedges, then
derive-relational-insights, then the gate with --expect-improve for the pins
declared to move (§5), then --rebaseline. The existing cure path
(--cure-subsidiary, cure_subsidiary_backfill) is the precedent for one-shot
reclassification; S4 uses the full replay, not the cure path, because
attribution changes pair identity, not just class.

**S5 — B0 (document the lane).** doc/design/ontology.md §2.5 already says
the RPT sets are "everything declared in one company's related-party
filing". Strengthen it into the contract: the group family carries TWO
populations — a disclosure-scope lane keyed vigil:rpt:<SYMBOL> (not
corporate families; do not read it as one) and a curated family lane keyed
by $.group. Nothing is retired; the 2026-11-08 ruling (keep both) stands.

**S6 — B4 (curated family lane).** Add a family lane as **a new label
namespace** (family:<name> inside the group family) rather than re-keying
existing labels, so the existing hyper-group-tata pin and the 498 disclosure
sets are untouched. Seed from the 8 properties.group sets, then curate the
top groups the measurements make obvious (Tata 209, Adani 124, Bajaj 27,
Hinduja 6 unions are already measured). Curation is operator work; the slice
ships the mechanism, the seed and a validator that a family set's members
resolve. — **LANDING (S6, 2026-11-12):** `helpers/misc/family_seeds.json`
(version 1: the 8 curated $.group sets, 23 members) +
`derive_hyperedges.collect_family_seeds / validate_family_seeds` +
the main()-merge (labels `family:<name>`, unknown members dropped and
printed, prune-converged); 8 tests in tests/test_family_seeds_lane.py; the
live apply wrote 8 group hyperedges / 23 incidences and the frozen set grew
v8→v9 with pin hyper-family-tata (gate --expect-improve hyper-family-tata →
ACCEPT). The measured unions (Tata 209/16 filers, Adani 124/12) stay open as
operator curation of the seed file — the mechanism is done, the membership
is a choice.

## 5. Acceptance criteria & shakedown

1. make refresh-vigil APPLY=1 reports unresolved filers 136 to ≤1 (GSPL
   only), and attributable rows rise by ~72k.
2. SELECT COUNT(*) FROM ultimate_parent WHERE
   ultimate_parent='Reliance Lithium Werks B.V' returns 0, and
   ultimate_parent('Reliance Industries') = 'Reliance Industries'
   (self-root).
3. The four pre-existing ultimate_parent pins are evaluated individually:
   Rallis India to Tata Chemicals (hold), TCS to Tata sons (hold), 365Squared
   to Proximus Global SA/NV (hold, and provably fails without S3), HDFC AMC
   International (IFSC) to **HDFC AMC** (declared improvement, from HDFC
   Bank).
4. Group label count stays 506, and hyper-group-tata still returns its 7
   curated members (the disclosure lane is untouched).
5. **Eval gate (mandatory — this changes query-visible semantics):**
   helpers/misc/ontology_eval_gate.py over helpers/misc/ontology_questions.json
   between the dry-run copy and the canonical apply, with
   --expect-improve uparent-reliance-industries,uparent-adani-ports-sez,uparent-hdfc-amc-international
   — zero regressions, no undeclared changes, every declared improvement
   materializes. Exit 0 before any apply.
6. helpers/misc/answerability_probe.py --parity reports 0 mismatches (live
   and Parquet must agree; the metric_value shape from #506 is the precedent
   for keeping both dialects in step).
7. Targeted tests only (tests/test_related_party_sync.py,
   tests/test_shareholding_sync.py, tests/test_derive_relational_insights.py,
   tests/test_metapath.py, tests/test_merger_hyperedges.py and the gate's own
   suite) — full gates are operator-owned.

| Projected outcome | Today | After |
|---|---|---|
| RPT filers resolving | 608 of 744 | 743 of 744 |
| RPT rows attributable to a resolved filer | 283,627 | 356,056 |
| ultimate_parent root of the Reliance family | Reliance Lithium Werks B.V (238 rows) | Reliance Industries |
| ownership edges | 7,374 | ~21,600 (21,139 after the cycle vote) |
| gate pins on the ownership lane | 4 (none Reliance-based) | 6 |
| group family | one lane, misread as corporate families | two documented lanes |

## 6. Risks

- **Attribution inflates entity count.** A4 mints counter-parties and
  subjects; the corrected replay mints 5,070 events over 1,024 distinct names
  (measured). The existing max_new_entities=60000 cap applies unchanged, and
  entity resolution (fold/census/name_key) runs first, so minting is the
  last resort — the same ladder counter-parties already use.
- **Pins move where they should not.** The 3,021 changed roots include
  legitimate restatements as well as corrections. Mitigation: the gate run
  before apply, with only the declared improvements; anything else that moves
  blocks the apply and gets inspected rather than rebaselined.
- **Cycle guards rise.** Corrected ownership has 1,496 cycle-guard roots
  against 21 live, because mutual pairs are real (2,103 pairs carry both
  directions and 0 of them contradict — they are the same claim stated from
  both ends). The vote rule (more distinct filing URLs wins, tie to sub_fwd)
  is what the measurement supports; the alternative (drop) changes 47 vs 171
  roots, so this is a real design choice, not a detail.
- **GSPL stays invisible** (417 rows). Recorded as a residual: the entity
  'Gujarat State Petronet' does not exist in the registry at all. Fixing
  that is an entity-coverage task, not this arc.
- **Group curation is unbounded work.** B4 ships mechanism + seed; the
  family list will grow by operator decisions. That is the point — it is the
  only lane where a human can be right and an algorithm cannot.

## 7. Non-goals

- No change to the 498 RPT disclosure sets, their labels, or their role
  assignments (B0 explicitly keeps them; the 2026-11-08 "keep both
  populations" ruling stands).
- No re-keying of existing group labels — B4 adds a namespace, it does not
  rewrite Tata or vigil:rpt:*.
- No new gate shape: only new pins on existing shapes.
- No entity-registry work beyond the subject/counter-party minting the
  loader already does (no GSPL-style coverage sweep, no alias fold round).
- No supplier_to redesign: that lane is a separate pass
  (related_party_sync.py:601-718) and its direction rules are unchanged by
  this arc; it follows attribution automatically because it reads the same
  companies map.
- No change to company_metrics or the #506 grain view.

## Appendix — raw measurement log

Scripts (all read-only; written 2026-11-11) live in
bench_data/vigil_attribution/ with their JSON outputs:

| script | what it measures |
|---|---|
| measure.py | RPT corpus shape, per-filer parent-side evidence, group label inventory |
| sim_full.py | the composed corrected replay (C1 + A4 + A-prime + filters + cycle vote) |
| sim_polarity.py | A-prime on/off, with the four gate pins evaluated under each |
| sim_edgediff.py | corrected replay vs live edge sets (kept / gained / lost) |
| sim_cycles.py | mutual-pair topology, cycle-rule variants (none / drop / vote) |
| sim_families.py | ultimate-parent families under corrected edges |
| sim_groups_final.py | group sets re-keyed on corrected roots (the B1 measurement) |
| sim_group_bridge.py | B5 listed-company bridge |
| sim_group_b6.py | B6 qualified bridge |
| measure_bridge.py | ticker bridge: 136 to 134 (ticker) to 1 (with company_name fallback) |

| run | command | result |
|---|---|---|
| 2026-11-11 | .venv/bin/python3 bench_data/vigil_attribution/measure.py | 356,473 rows; 744 filers; 8,744 distinct entity_name; 220,549 differing (61.9%) |
| 2026-11-11 | .venv/bin/python3 bench_data/vigil_attribution/sim_full.py | 90,870 pairs; 21,631 ownership edges; roots 15,313/5,431/1,496; 469 groups |
| 2026-11-11 | .venv/bin/python3 bench_data/vigil_attribution/sim_edgediff.py | sim 56,151 (44,822 distinct) vs live 34,654; both 11,872 |
| 2026-11-11 | .venv/bin/python3 bench_data/vigil_attribution/measure_bridge.py | fixed 134; missing [LTIM, GSPL]; ambiguous bases among unresolved: none |
| 2026-11-11 | sqlite memory/research.db | uparent 23,697 rows; Reliance to Reliance Lithium Werks B.V (depth 1); 238 rows rooted there; 365Squared to Proximus Global SA/NV (depth 2) |
