---
title: "Collapse single-letter runs in name_key — the normaliser decision behind the duplicate-name groups"
status: executed
filed: "2026-11-16"
executed: "2026-11-16"
completed_md: "527"
area: "helpers/core/entity_identity.py + helpers/graph/name_dedup.py + helpers/maintenance/related_party_sync.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

**Date:** 2026-11-16 · **Status:** EXECUTED (S0–S4 landed 2026-11-16; Option A + B) ·
**Area:** `helpers/core/entity_identity.py` (`name_key`), consumed by
`helpers/graph/name_dedup.normalise` and `helpers/maintenance/related_party_sync._loose_key`

## 1. Motivation

The entity store carries a class of duplicates that no census can see: the same
counterparty minted twice because the source wrote its legal suffix two ways.
`Service Centre Maastricht BV` and `Service Centre Maastricht B.V` are one
company; so are `Universal Voltas LLC` / `L.L.C`, `Layde Steel SL` / `S.L`,
`Tata Steel Norway Byggsystemer As` / `A/s`, `Orchid Netherlands No1 BV` /
`No.1 B.V`, `K.T.V. Health Food` / `KTV Health Food`.

The verified count of such groups stood at **14 groups / 29 rows** before the
2026-11-16 fold (audit of that date, all `file_path IS NULL`, all minted by
the RPT/VIGIL ingestion on 2026-09-22, 175 edge-endpoints total). They were
repaired by a one-off fold (see the sibling proposal
`database/entity_fold_fk_cascade_repair.md`), which merged **12 company
variants**; the 3 person variants had already been folded on 2026-10-16.

After that fold the fileless duplicate count is **3 groups / 6 rows**, and
they are a *different* class — person initials, not legal suffixes:
`B.V.K. Reddy` / `BVK Reddy`, `D.K. Sunil` / `DK Sunil`,
`J.P. Chalasani` / `JP Chalasani`.

The repair was correct but it is a **sweep, not a fix**. The mechanism that
created these duplicates is untouched, so the next VIGIL refresh can mint the
same pairs again, and the next session will re-derive the same table by hand.
The question this proposal decides is whether the *normaliser* should be
taught to see them.

That question turns out to be much larger than the 14 groups that prompted
it, which is why it is a proposal and not a patch.

## 2. Evidence (measured 2026-11-16, this box)

### 2.1 Why `name_key` cannot see these variants

`entity_identity.name_key` (`helpers/core/entity_identity.py:56`) replaces
punctuation with a **space** (via `_PUNCT_RE = re.compile(r"[^\w\s]")` at
`helpers/core/entity_identity.py:50`, then whitespace collapse). So a dotted
suffix widens the key instead of collapsing it:

| Name | `name_key` |
|---|---|
| `Service Centre Maastricht BV` | `service centre maastricht bv` |
| `Service Centre Maastricht B.V` | `service centre maastricht b v` |
| `UNO MINDA EUROPE GMBH` | `uno minda europe gmbh` |
| `Designit A S` | `designit a s` |
| `B.V.K. Reddy` | `b v k reddy` |

Running the community's own `name_dedup.cluster_names()` over all 29 names
forms **0 clusters**. The groups are invisible to the census that is supposed
to find them.

### 2.2 The store has *two* normalisation homes, and they disagree

| Home | Function | `B.V` vs `BV` | Used by |
|---|---|---|---|
| `helpers/core/entity_identity.py:56` | `name_key` | **differs** (`b v` vs `bv`) | `name_dedup.normalise` → the dup census |
| `helpers/core/parse_newsletter.py:633` | `normalize_name` | **equal** (dots are non-alnum → dropped, not spaced) | `shareholding_sync._normalize` → `related_party_sync._loose_key` |

`_loose_key` (`helpers/maintenance/related_party_sync.py:484`) also strips a
legal-form tail (`_LEGAL_TAIL_RE` at `related_party_sync.py:481`:
`(PRIVATE|PVT|LIMITED|LTD|COMPANY|CORPORATION|CORP|INC|LLP|CO)$`) and a
leading `THE`. That is exactly why the ingestion produced
`normalized_name`-level collisions (the two rows share a `normalized_name`)
while `name_key` saw nothing: **the census and the writer use different keys.**

This is the root defect. The duplicate groups are a symptom of it, and any
fix that
only edits the normaliser without reconciling the two homes will drift again.

### 2.3 A letter-run collapse rule, measured against the live store

The obvious rule is: collapse a run of single alphanumeric tokens into one
token, so `b v` → `bv` and `b v k` → `bvk`. Candidate implementation (JOIN
semantics — the run's tokens concatenate; see the caveat below):

```text
re.sub(r"\b((?:[a-z0-9] ){1,4}[a-z0-9])\b",
       lambda m: m.group(1).replace(" ", ""), s)   # applied post-name_key
```

> **Snippet caveat (measured 2026-11-16).** The intuitive `re.sub(...,
> r"\1", ...)` replacement does **not** join the run — `\1` captures only the
> run's LAST token, so `b v` → `v` and `s p a` → `a` (deletion, not
> concatenation). The pre-fold 66/133 measurement was taken under that
> deletion semantics, which is why its false-merge list carried deletion
> artifacts (`ABB S.A`/`S.p.A`, `3B Films`/`P. B. Films`,
> `Siemens Energy S.r.l`/`W.L.L`, `Sub-Total (A)(1)`/`(B)(1)`,
> `H S India`/`SVS India`). Under the JOIN semantics none of those merge:
> legal-form abbreviations (`sa`, `spa`, `srl`, `wll`, `sar l`) stay distinct
> keys. The residual risk of the real rule is therefore smaller than the
> pre-fold list implies — but §S2's triage is still the gate.

Measured blast radius against the live entity table — **re-measured after the
2026-11-16 fold**, so these are the numbers a decision today would face:

| Configuration | `name_key` collision clusters | Names involved | Cross-`entity_type` merges |
|---|---|---|---|
| today (status quo, post-fold) | **2 clusters / 4 names** | 4 | 0 |
| with letter-run collapse | **57 clusters / 114 names** | 114 | **0** |

**55 clusters would newly merge, pulling in 110 names** (pre-fold the same
rule measured 66/133 with 64 newly merging — the fold removed 9 clusters from
the blast radius). The rule does not select only the known-bad groups; it
also collapses genuinely distinct entities. Partial list of what it would
newly merge:

`N.A. Estate` / `N A Estate` · `Central U.P. Gas` · `K.T.V. Health Food` ·
`Layde Steel S.L` · `S A B Profiel B.V` / `SAB Profiel BV` ·
`Designit A S` / `Designit AS` / `Designit Denmark A S` / `Designit Oslo A S` ·
`UNO MINDA EUROPE GMBH` / `UNOMINDA EUROPE GMBH` ·
`Tata Steel Belgium Services N V` / `NV` · `TC Inter-Informatics a.s` ·
`Tech-Mahindra Panama, S.A` · `Vesuvius Mexico S.A.de C.V` ·
`Tata Communications (Poland) SP. ZOO` · `Diageo Relay B V` ·
`Applied Value Technologies B V` · `Eltec IT Services S L U` ·
`Ricochet Spain S L Spain` · `TVS Logistics Iberia S L Spain` ·
`BVK Reddy` / `DK Sunil` / `JP Chalasani` / `TV Narendran` /
`MM Venkatachalam` · `R R KABEL` · `Castrol U.K` · `D.A.Stuart India` /
`Vega Industries U.S.A` · `HONEYWELL BAHRAIN W L L` ·
`Colgate Palmolive Company U S A` · `SS NATURAL RESOURCES` ·
`THTL Trading LLC` · `OB Infrastructure` · `VM Foundation` / `AMM Foundation` ·
`SVS India` · `RSM Pharma` · `AR Landcraft LLP` · `MS Ramaiah Ventures LLP` ·
`JB Mangharam Foods` · `G D Foods Manufacturing India` ·
`J S Auto Cast Foundry India` · `DB Contractors & Builders` ·
`D B Hi-Sky Constructions` / `D B Man Realty` /
`D B Realty and Shreepati Infrastructures LLP` ·
`Shreepati Skies ( RR Chaturvedi )`

Most of those are **correct** merges of a real spelling variant. But the set
is not reviewable item-by-item from a rule change alone, and it is 9× the
batch the fold just handled. Zero cross-`entity_type` merges is the one
reassuring number: the rule does not conflate a company with a person.

> **List corrections (post-fold re-verification, 2026-11-16).** Several
> entries above are carried over from the pre-fold list and no longer hold:
>
> - **Already folded** — `Central U.P. Gas`, `K.T.V. Health Food`,
>   `Layde Steel S.L`, `S A B Profiel B.V`, `Service Centre Maastricht B.V`,
>   `Universal Voltas L.L.C`, `Tata Steel Norway Byggsystemer A/s`,
>   `Orchid Netherlands No.1 BV`, `Tata Steel Netherlands Holdings B.V`,
>   `Tata Steel Belgium Packaging Steels N.V` — the variant spelling is gone
>   from the store; these clusters cannot re-form except by re-minting
>   (option B's target).
> - **Not merges of this rule** — `UNO MINDA EUROPE GMBH`/`UNOMINDA` is
>   Class 2 (inner-space variant; `collapse-unifies=False` measured), and the
>   foundation cluster is `A M M Foundation`/`AMM Foundation` — `VM
>   Foundation` is a separate entity and stays separate under the rule.
> - `N.A. Estate`/`N A Estate` was already a status-quo collision (it is one
>   of today's 2 clusters, merely re-keyed by the collapse).

### 2.4 Four pairs that remain unfixable at the entity level

Measured against the fold ledger's own scope invariant
(`tests/test_name_dedup.py:334`, which requires every ledger row to satisfy
`normalise(v) == normalise(c)` **or** `_loose_key(v) == _loose_key(c)`):

| Pair | strict `name_key` | `_loose_key` | Entity-foldable? |
|---|---|---|---|
| `SAB Profiel BV` → `S A B Profiel BV` | differs | **differs** | **no** |
| `Designit A S` → `Designit AS` | differs | **differs** | **no** |
| `Tata Steel Belgium Services N V` → `NV` | differs | **differs** | **no** |
| `UNO MINDA EUROPE GMBH` → `UNOMINDA EUROPE GMBH` | differs | **differs** | **no** |

All 14 originally-folded groups pass via `_loose_key`; these four fail
**both** rules, so the gated `apply_plan` path cannot touch them without
weakening a live invariant. All four entities still exist post-fold (verified:
each spelling has `entity_exists=1`), so this is not a residue of the fold —
it is the class the fold structurally cannot reach.

They surface as `subsidiary` **hyperedge label** collisions and need either
the normaliser change below or a hyperedge-label converge step — not a fold.

Post-fold there are **9 subsidiary label collisions / 18 rows**, and the
letter-run rule would fix only **5 of the 9**:

| Collision | Letter-run rule fixes it? |
|---|---|
| `Designit A S` / `Designit AS` | **yes** |
| `Designit Denmark A S` / `Designit Denmark AS` | **yes** |
| `Designit Oslo A S` / `Designit Oslo AS` | **yes** |
| `S A B Profiel BV` / `SAB Profiel BV` | **yes** |
| `Tata Steel Belgium Services N V` / `NV` | **yes** |
| `Quess Corp Holdings Pte` / `Quesscorp Holdings Pte` | no |
| `S A B Profil Gmbh` / `S.A.B. Profil GmbH` | no |
| `UNO MINDA EUROPE GMBH` / `UNOMINDA EUROPE GMBH` | no |
| `Yung Hsin Contain Industry Co` / `Yunghsin Contain Industry Co` | no |

The 4 it cannot fix split into two classes (§2.5): three are word-segmentation
variants outside the letter-run rule's reach, and one (`S.A.B. Profil GmbH`) is
a genuine camel-split bug in `name_key` — further evidence that a purely
normaliser-aggressiveness change is not a complete answer.

### 2.5 The residue, classified by WHY a rule cannot reach it

After the 2026-11-16 fold, 9 `subsidiary` label collisions remain. Passing them
through the letter-run rule sorts them into three distinct classes — and the
classes do not share a fix. Measured with
`/mnt/data/tmp/opencode/classify_unfixable.py`.

**Class 1 — fixed by the letter-run rule (5 of 9).** Single-letter runs in a
dotted or spaced abbreviation:

| Collision | `name_key` today | after collapse |
|---|---|---|
| `Designit A S` / `Designit AS` | `designit a s` / `designit as` | both → `designit as` |
| `Designit Denmark A S` / `Designit Denmark AS` | `… denmark a s` / `… denmark as` | both → `… denmark as` |
| `Designit Oslo A S` / `Designit Oslo AS` | `… oslo a s` / `… oslo as` | both → `… oslo as` |
| `S A B Profiel BV` / `SAB Profiel BV` | `s a b profiel bv` / `sab profiel bv` | both → `sab profiel bv` |
| `Tata Steel Belgium Services N V` / `… NV` | `… services n v` / `… services nv` | both → `… services nv` |

**Class 2 — word-segmentation variants (3 of 9), NOT reachable by any
letter-run rule.** The two spellings differ by an **inner space inside a
multi-letter word**, not by a run of single letters:

| Collision | keys | diff |
|---|---|---|
| `Quess Corp Holdings Pte` / `Quesscorp Holdings Pte` | `quess corp holdings pte` / `quesscorp holdings pte` | `delete: ' ' -> ''` (`corp`) |
| `UNO MINDA EUROPE GMBH` / `UNOMINDA EUROPE GMBH` | `uno minda europe gmbh` / `unominda europe gmbh` | `delete: ' ' -> ''` (`uno minda`) |
| `Yung Hsin Contain Industry Co` / `Yunghsin Contain Industry Co` | `yung hsin contain industry co` / `yunghsin contain industry co` | `delete: ' ' -> ''` (`yung hsin`) |

A rule that additionally deletes inner spaces between any two ≥2-character
tokens *would* fix these three — but measured against the live table it is
catastrophic: collisions go **2 → 143 clusters / 288 names** (141 newly
merging), because it merges every `Foo Bar` with `FooBar` in the store. This
is where the normaliser stops being a policy choice and becomes a
combinatorial identity problem (`UNO MINDA` vs `UNOMINDA` vs `UNO-MINDA`
vs `UNOMINDA`), and it is **out of scope for this proposal**.

The same class extends beyond the 9 `subsidiary` labels: `Orchid Netherlands
No1 BV` / `Orchid Netherlands No.1 BV` are both still live entity rows
(post-fold), and the letter-run rule cannot reach them either — `no1 bv` vs
`no 1 bv` fails because a collapse run needs ≥2 single-character tokens and
`no` is two characters. Digit-adjacency (`No1`/`No. 1`) is word segmentation,
not a letter run.

**Class 3 — a genuine BUG in `name_key`, not a policy limit (1 of 9).**
`S A B Profil Gmbh` / `S.A.B. Profil GmbH` and 3 further pairs are split
wrongly by the camel-case regexes. For `GmbH` the culprit is
`_CAMEL_RE_1 = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")`
(`helpers/core/entity_identity.py`): it fires at the lower→upper boundary
`b|H`, giving `Gmb` + `H` (the `_CAMEL_RE_2` upper→`Upper+lower` regex is
what fractures `KGaA` → `K Ga A`, `ITeS` → `I Te S`, `EDandS` → `E Dand S`,
`MGroup` → `M Group`), so:

```text
name_key('Acme GmbH')       -> 'acme gmb h'
name_key('Acme Gmbh')       -> 'acme gmbh'
name_key('S.A.B. Profil GmbH') -> 's a b profil gmb h'
name_key('S A B Profil Gmbh')  -> 's a b profil gmbh'
```

**359 entities** carry a camel-splittable legal suffix (`GmbH` 244, `SpA` 24,
`ApS` 2, `KGaA` 1, plus `Vertriebs-GmbH`, and the abbreviations `EDandS`,
`GenX`, `ITeS`). Splitting them is never right for a legal form, and fixing it
is both cheap and safe: collapsing the spurious split takes collisions from
**2 → 6 clusters**, and the 4 newly merging pairs are *all correct* —
`Hille & Muller GmbH`/`Hille Muller Gmbh`, `S A B Profil Gmbh`/`S.A.B.
Profil GmbH`, `Tata Technologies GmbH`/`Tata Technologies Gmbh`,
`TECOSIM , Gmbh`/`Tecosim GmbH`.

This class should be fixed **regardless** of whether Option A or B is chosen —
it is a defect in the existing key, not a grey area about how aggressive
normalisation should be.

**Implemented 2026-11-16 (S0, §3).** The guarded splitter measured **2 → 9
clusters / 7 new merges, all correct** — the 4 pairs above plus three more of
the same short-token class: `Jio BLAST eSports`/`Jio Blast Esports`
(`eSports` → `e sports`) and the two `TATA 1MG`/`Tata 1mg` pairs (`1MG` →
`1 mg`). Zero unmerges (no pair that keyed equal before keys apart now), and
the real fold ledger's normalise/loose-key invariant holds on all 184 rows.

## 3. Design

Three options, with what each costs. **This proposal records the decision
space; it does not presume an answer.**

### Option A — change `name_key` to collapse letter runs (recommended shape, gated)

Add the collapse to `name_key` behind the existing census gate:

1. `name_key` collapses runs of ≤4 single alphanumeric tokens into one token,
   **after** the existing punctuation→space pass.
2. The live consequence is absorbed deliberately, not blindly: the dup census
   (`make dup-census`) jumps from 2 to 57 clusters, and the baseline file
   `findata/Misc/_name_dedup_baseline.txt` (currently `0`) is raised to the new
   count **only after** the 55 new clusters are triaged. Until triaged, the
   gate fails — which is the correct signal.
3. `related_party_sync._loose_key` and `parse_newsletter.normalize_name` are
   audited for the same class of divergence (§2.2) so the two homes converge
   or their difference is documented as intentional.

Cost: a 114-name curation arc, in reviewable batches. Benefit: the census
finally sees the class, so the duplicates stop being invisible.

### Option B — leave `name_key` alone; fix the *writer*

The duplicates are minted by `related_party_sync`, not by the census. If the
writer keyed entities on `_loose_key` (or on `normalize_name`) instead of on
the raw `name`, the pairs would never be created — measured evidence that this
is the real source is at `helpers/maintenance/related_party_sync.py:520-535`,
where within-run twins are already indexed by
`ck = name_key(FOOTNOTE_RE.sub("", raw.strip()))`.

Cost: needs a backfill decision for the rows already created. Benefit: stops
re-minting, no census churn, no 114-name review.

> **Implemented 2026-11-16 (this option).** `_resolve_party`
> (`helpers/maintenance/related_party_sync.py`) now indexes within-run mints
> by `_loose_key` as well as the census key (`new_entities_lk`), so two
> suffix spellings of one company minted in the same run resolve to one
> entity; `tests/test_related_party_sync.py::TestLooseKeyTwinIndex` pins the
> §4.4 fixture (both spellings → one mint, end-to-end through
> `build_pairs`/`apply_pairs`), plus non-merge of distinct legal forms
> (`HDFC Bank B.V` vs `LLC`) and the unchanged no-index behaviour. The
> backfill cost above is nil as of today: the store was folded clean
> immediately before this change, so there are no known twins left to
> collapse.

### Option C — status quo + keep sweeping

Fold the residual groups, do not touch the normaliser, re-audit when the count
grows. Cost: the sweep recurs. Benefit: zero blast radius today.

### Recommendation

**B before A.** Fixing the writer is where the duplicates come from; A is the
detector. Doing A first would create a 114-name review to fix damage B would
stop producing. C is acceptable only if B is explicitly deferred — in which
case the baseline should carry a note that the count is expected to grow.

**S0 is unconditional.** Class 3 (§2.5) is a bug in the existing key, not a
normalisation-policy choice, and should be fixed whichever option is taken —
including C.

### Slices

- **S0 (unconditional) — fix the camel-split of legal suffixes.**
  **Implemented 2026-11-16** in `helpers/core/entity_identity.py`:
  `_camel_split()` replaces the two unconditional subs — token-wise
  camel boundary scan that **refuses any split leaving a <2-char fragment
  on either side** (`GmbH` `Gmb|H`, `SpA` `Sp|A`, `ApS` `Ap|S`, `KGaA`
  `K|GaA` all stop splitting; real compounds keep both fragments ≥2 and
  still split: TechMahindra, USSteel, HDFCBank, McDonald, IndianOil).
  Chosen over an exemption-list of legal forms because it also covers the
  abbreviation class (`ITeS`, `EDandS`, `GenX`) and digit-adjacency
  (`1MG`/`1mg`, `3M`) with no hand-maintained list. Effect measured on the
  live entity table: collisions **2 → 9 clusters**, all 7 newly merging
  pairs correct (the 4 listed + `Jio BLAST eSports`/`Jio Blast Esports`,
  `TATA 1MG`/`Tata 1mg` ×2); **zero unmerges**; real fold-ledger
  invariant green (184 rows). Tests: `tests/test_entity_identity.py`;
  full name_dedup/related_party_sync/person/extract_relations/speaker/
  shareholding suites pass. 359 entities carry such a suffix; no
  cross-type merges. Census gate: 9 > baseline `0` → raise the baseline
  to the post-S0 count (S3), or fold the 7 first.
- **S1** — `name_key` letter-run collapse + unit tests for the 5 Class-1
  label pairs. Census gate goes red (57 > baseline); that is expected and
  must be recorded as such.
- **S2** — triage the 55 new clusters in reviewable batches; each batch either
  folds (via the now-repaired `apply_plan`) or is added to a
  `never_merge` list with a reason.
- **S3** — raise `_name_dedup_baseline.txt` to the residual count; `make
  dup-census` green again.
- **S4** — audit `_loose_key` / `normalize_name` against the new `name_key`;
  document or converge.

#### S4 result (2026-11-16) — documented as INTENTIONAL divergence, no convergence

Four normalisation homes exist; after S1 the letter-run class was re-measured
across all of them (`/mnt/data/tmp/s4_audit.py`, live functions):

| Home | Surface | Contract | Role |
|---|---|---|---|
| `entity_identity.name_key` (`helpers/core/entity_identity.py:97`) | `designit as`, lowercase spaces | NFKD + footnote/apostrophe + guarded camel split + punctuation→**space** + S1 letter-run join | **Tier 1 identity** — census + registry + resolver; non-fuzzy |
| `name_dedup.normalise` (`helpers/graph/name_dedup.py:74`) | same as `name_key` | delegates to `name_key` | already converged (archived `entity_identity_home.md`) |
| `parse_newsletter.normalize_name` (`helpers/core/parse_newsletter.py:633`) | `Designit_AS`, PascalCase underscores | `^[A-Za-z0-9][A-Za-z0-9_]*$`, idempotent | `entities.normalized_name` **storage format** (note parser) |
| `related_party_sync._loose_key` (`helpers/maintenance/related_party_sync.py:484`) | `DESIGNIT_AS`, upper underscores | `normalize_name(clean_name(name)).upper()` + legal-tail + leading `THE` strip | writer-side **filer-is-subject detector** + within-run twin index |

Measured on the divergence class from §2.2 (post-S1):

| Pair | `name_key` | `normalize_name` | `_loose_key` | Verdict |
|---|---|---|---|---|
| `Service Centre Maastricht B.V` / `BV` | **equal** | equal | equal | S1 closed the original §2.2 defect — census and writer now agree |
| `Universal Voltas L.L.C` / `LLC` | **equal** | equal | equal | same (dotted legal suffix) |
| `Designit A S` / `Designit AS` | **equal** | differs | differs | intentional layering (below) |
| `S A B Profiel BV` / `SAB Profiel BV` | **equal** | differs | differs | intentional layering (below) |
| `Tata Steel … N V` / `NV` | **equal** | differs | differs | intentional layering (below) |
| `The Indian Hotels Company Limited` / `Indian Hotels` | differs | differs | **equal** | intentional — `_loose_key` tail-strip is the filer detector |
| `HDFC Bank` / `HDFC Securities` | differs | differs | differs | correct non-merge (pinned by `test_normalise_folds_case_punct_footnotes`) |

**Decision — do NOT merge the homes into one function.** They are three
deliberately different tiers, not three competing identity keys:

1. `normalise` is already the identity tier (delegates to `name_key`); the
   archived `entity_identity_home.md` consolidated five older normalisers
   here. S1's letter-run join makes this tier agree with the writer on dotted
   legal suffixes (`B.V` ≡ `BV`), which was the one genuine §2.2 disagreement.
2. `normalize_name` cannot be replaced by `name_key`: its contract is a
   *storage format* (`PascalCase_underscores`, idempotent, no spaces), which a
   casefolded-with-spaces identity key does not satisfy. Its callers
   (`rename_entity`, `enrich_relations`, `speaker_resolution`, `shareholding_sync`)
   write/read `entities.normalized_name`, not identity decisions.
3. `_loose_key` is a writer-side *detector*, not an identity key: dropping
   legal tails and a leading `THE` is what spots a disclosed counterparty that
   is really the filer (A4 fragmentation). `name_key` deliberately keeps legal
   tails — stripping them would over-merge distinct entities. This difference
   is load-bearing and stays.

For inner-space forms (`A S`/`AS`, `S A B`/`SAB`) the storage keys still differ,
but **no duplicate entity results**: `_resolve_party` indexes within-run mints
by the census key `ck = name_key(...)` (`related_party_sync.py:530,553`), so
both spellings share one `ck` and resolve to a single mint. Pinned by
`tests/test_related_party_sync.py::TestLooseKeyTwinIndex` (5 tests, green).
This is the two-tier model working as designed — identity unifies, storage
keeps the surface spelling.

No code convergence applied; the divergence is documented here and pinned by
a targeted regression test. S4 complete.

### Explicitly out of scope — Class 2

The three inner-space pairs (`Quess Corp`/`Quesscorp`, `UNO MINDA`/`UNOMINDA`,
`Yung Hsin`/`Yunghsin`) need word-segmentation equivalence, and the only
general rule tried (delete inner spaces between ≥2-char tokens) explodes
collisions to **143 clusters / 288 names**. If these must be resolved, the
correct mechanism is **not** a normaliser rule but a writer-side fix plus a
curated alias ledger entry — the same shape as the `kind: "alias"` +
`ruled_by` exemption the ledger already supports at
`tests/test_name_dedup.py:334`.

## 4. Acceptance criteria & shakedown

1. Whichever option is chosen, `python3 helpers/validators/static_checks.py`
   and `python3 helpers/misc/markdown_lint.py` stay clean.
2. If A: `make dup-census` fails with a count that matches the measured 66
   clusters (±0), and every one of the 55 new clusters is either folded or
   listed as `never_merge` before the baseline is raised.
3. If A: zero cross-`entity_type` merges across the whole triage — the
   measured property in §2.3 must be preserved, and enforced by an assertion,
   not by review.
4. If B: a fresh VIGIL/RPT ingestion over a fixture with both spellings mints
   exactly **one** entity, proven by a targeted test (not a full suite run).
5. The 4 pairs in §2.4 are resolved by whichever mechanism the chosen option
   uses, or are explicitly recorded as accepted residue with a reason.

| Projected outcome | Today | After |
|---|---|---|
| `normalized_name` duplicate groups visible to the census | 0 (invisible) | 3 seen / foldable (post-fold; was 14) |
| `name_key` collision clusters | 2 | 57 under Option A; 2 under B/C |
| Names needing triage under Option A | 0 | 114 |
| Re-minting by the RPT writer | undetected | stopped under B |

## 5. Risks

- **Option A widens an identity boundary.** 114 names is not a cosmetic
  change; a bad merge inside it fuses two real counterparties. Mitigated by
  the `apply_plan` FK repair (now landed) and by triaging in small batches
  with the ledger as the audit trail.
- **The two normalisation homes may be intentionally different.** `name_key`
  serves entity identity; `normalize_name` serves note parsing and may carry
  note-specific tolerances. Converging them blind could regress the ingest
  path. §S4 must establish intent before merging them.
- **Option B may not be sufficient alone.** The writer fix stops *new*
  duplicates but the existing groups still need the fold (done for the RPT
  batch). B is a
  prevention measure, A is detection; neither alone is complete.
- **The 66-cluster measurement is a property of today's entity table.** As the
  store grows the count moves; the acceptance criterion pins it to the
  measured date, not to a fixed number forever.

## 6. Non-goals

- Does **not** change `apply_plan`, the fold path, or any `_REFOLD_COLUMNS`
  behaviour — that repair is the sibling proposal and is already landed.
- Does **not** repair the residual `subsidiary` hyperedge label collisions by
  a DB edit. The 5 Class-1 pairs would converge automatically once S1 lands
  (the next `derive_hyperedges` run re-labels them); the 3 Class-2 pairs are
  explicitly out of scope (§3); the 1 Class-3 pair converges once S0 lands.
- Does **not** add word-segmentation equivalence (Class 2). Measured cost:
  collisions **2 → 143 clusters / 288 names**. Not a normaliser decision.
- Does **not** decide the fuzzy-matching question. `name_key` is deliberately
  non-fuzzy (`test_normalise_folds_case_punct_footnotes` asserts
  `normalise("HDFC Bank") != normalise("HDFC Securities")`); nothing here
  proposes edit-distance matching.
- Does **not** implement anything. Status stays `proposed` until an operator
  picks A, B, or C — **S0 excepted**, which is a bug fix and is recommended
  unconditionally.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-11-16 | `name_dedup.cluster_names()` over the 29 duplicate names | 0 clusters | §2.1 — the census cannot see them |
| 2026-11-16 | `name_key` collisions over the whole live entity table | 2 clusters / 4 names | `CyberTech Systems and Software`, `N A Estate` |
| 2026-11-16 | same, with letter-run collapse applied (**pre-fold**) | 66 clusters / 133 names | 64 newly merge, 0 cross-type |
| 2026-11-16 | **re-measured post-fold** (`/mnt/data/tmp/opencode/reaudit_proposal.py`) | 2 → **57 clusters / 114 names** | 55 newly merge, 0 cross-type (fold removed 9 clusters from the radius) |
| 2026-11-16 | fileless duplicate `normalized_name` groups, post-fold | **3 groups / 6 rows** (`BVK_REDDY`, `DK_SUNIL`, `JP_CHALASANI`) | was 14/29; all remain foldable by the collapse rule |
| 2026-11-16 | `normalise(v) == normalise(c)` OR `_loose_key(v) == _loose_key(c)` on all 15 pairs | 14 pass (via `_loose_key`), **4 fail both** | §2.4 |
| 2026-11-16 | `PRAGMA` key-constraint audit on `entity_identifiers` | two UNIQUE keys, incl. `(identifier_type, identifier_value)` | relevant to any re-key |
| 2026-11-16 | 9 `subsidiary` label collisions post-fold (`/mnt/data/tmp/opencode/reaudit2.py`) | letter-run rule fixes **5 of 9** | the other 4 are hyphen/whitespace variants, a different rule class |
| 2026-11-16 | per-collision mechanism classification (`/mnt/data/tmp/opencode/classify_unfixable.py`) | 5 Class-1 (letter run), 3 Class-2 (inner-space `delete: ' ' -> ''`), 1 Class-3 (`insert: '' -> ' '`) | §2.5 |
| 2026-11-16 | camel-split audit over the live entity table | **359** entities carry a split suffix (`GmbH` 244, `SpA` 24, `ApS` 2, `KGaA` 1) | `_CAMEL_RE_2` splits `GmbH` → `Gmb`+`H` |
| 2026-11-16 | `name_key` collisions with the suffix split collapsed | 2 → **6** clusters (delta **+4**), 0 cross-type | all 4 new pairs correct: `Hille & Muller GmbH`, `S A B Profil Gmbh`, `Tata Technologies GmbH`, `TECOSIM , Gmbh` |
| 2026-11-16 | **S0 implemented** — `_camel_split` <2-char-fragment guard (`tests/test_entity_identity.py`) | 2 → **9** clusters (delta **+7**), 0 cross-type, **0 unmerges** | attribution corrected: `_CAMEL_RE_1` splits `GmbH` at `b\|H` (not `_CAMEL_RE_2`); the 3 extra merges (`Jio BLAST eSports`, `TATA 1MG` ×2) are the same short-token class; real ledger invariant green (184 rows) |
| 2026-11-16 | letter-run + inner-space-deletion combined | **143 clusters / 288 names** (141 newly merging) | Class-2 general rules are unusable |
| 2026-11-16 | subsidiary family ownership audit | 1464/1464 rows carry `source_ref`, 0 have `agent_id`, `role` NULL on all 15,097 incidences | fully derived → safe to regenerate |
