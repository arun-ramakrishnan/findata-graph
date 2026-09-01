---
title: "Adopt markdownlint-cli2 — markdown lint gate for doc/ and the findata defect tier"
status: executed
filed: "2026-09-01"
executed: "2026-09-01"
completed_md: "191"
area: make
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# Adopt markdownlint-cli2 — markdown lint gate for doc/ and the findata defect tier

**Date:** 2026-09-01 · **Status:** EXECUTED 2026-09-01 (S1–S5 same day) ·
**Area:** make (advisory wiring) · doc/ (one-time remediation) · findata Tier-1 writers

## 1. Motivation

Markdown is the only first-party language in the corpus without a lint or
format gate. The language matrix (`doc/templates/README.md`) records ruff
for Python, prettier for TypeScript, `mojo format` for Mojo — and "—" for
the Markdown-notes row. No prior markdown linter was ever evaluated
(doc/script indexes: zero hits; the lint history is the 2026-08-13
ruff-replaces-flake8 arc plus corpus_uniformity S7).

The gap is not cosmetic. The 2026-09-01 probe (§2, §3) found **81 tables
that render with silently lost data** in the findata corpus ("extra data
will be missing"), a uniform generator artifact (`**…`) repeated across
dozens of company notes, and 372 notes written without a trailing
newline — none of which any existing gate sees, because verify_notes /
static_checks validate frontmatter, wikilinks, and rosters but nothing
checks markdown *structure*.

Trigger: user question 2026-08-31 — "in our lint support for markdowns in
our proposals, have we evaluated markdownlint-cli2?" Answer was no; this
proposal is the evaluation plus the adoption plan.

## 2. Evidence — tool selection (measured 2026-09-01, this box)

| Configuration | Result | Verdict |
|---|---|---|
| markdownlint-cli2 v0.23.2 | published 2026-07-27; bundles markdownlint v0.41.1; engine node ≥22 | **adopt** |
| markdownlint-cli v0.49.1 | published 2026-07-14; same engine, same rules | active but loses on ergonomics |
| findings parity | both CLIs, equivalent configs over `doc/**`: **identical 492 issues / 52 files** | engine identical |
| speed (warm, 3 runs each) | cli2 2.72–2.81 s vs cli 2.83–3.44 s over doc/ | near-parity; cli2 parallelism pays only on huge trees |
| local node | v24.15.0 (nvm) — satisfies both engines' node ≥22 | fine |

**Why cli2, given the engine is the same:** its config file
(`.markdownlint-cli2.jsonc`) carries rules **+ globs + ignores in one
file**, so the make recipe is argument-free; it supports per-glob
`overrides` (doc/ ruleset vs findata defect-only ruleset in one file) and
per-directory nesting; it is where new investment lands (formatters,
custom-rule plugins); and its config formats are the ones the
vscode-markdownlint extension reads, so editor and gate stay in lockstep.

Ruled out, measured, do not re-audit:

- **markdownlint-cli** — would work; no capability we need that cli2
  lacks. Its extra legacy formats (`.markdownlintrc`, INI configs,
  `.markdownlintignore`) are things we don't have anywhere (verified:
  no `.*markdownlint*` file exists in the repo).
- **prettier for markdown** — a *formatter*, reflows prose; wrong tool
  for prose (and for wikilink-heavy notes). lint-audit S-category
  precedent: we lint markdown structure, we don't reflow it.
- **config-envelope trap (operational):** cli2 config files wrap rules
  in a top-level `"config"` key; markdownlint-cli expects flat rule
  config and **silently runs defaults** on the cli2 envelope (observed:
  2,836 issues instead of 492, exit 1, no error). Never share one file
  between the two tools.

## 3. Evidence — corpus probes (measured 2026-09-01, this box)

### 3.1 doc/ (hand-written surface)

| Configuration | Result | Verdict |
|---|---|---|
| default rules | 2,836 issues — MD013 line-length 1,210, MD060 table-style 1,045 | defaults fight house prose; reject |
| tuned config (§4.1) | **492 issues / 52 files** | baseline for the gate |

Disposition of the tuned 492 (revised at S2 execution — the fixer trial
rewrote this table; see Appendix S2 rows):

| Bucket | Rules (counts) | Remediation | S2 outcome |
|---|---|---|---|
| auto-fixable | MD032 104, MD022 80, MD031 30, MD012 27, MD007 17, MD037 16, MD049 4, MD009 2 | `--fix` sweep over `doc/` only (280) | **split**: MD031/MD012/MD007/MD037/MD049/MD009 fixed cleanly; **MD032/MD022 rules OFF** — their blank-insertions manufactured headings/lists out of wrapped prose |
| style-align via config | MD004 100, MD029 22, MD050 4, MD055 2 | pin the rule's style to the corpus-dominant marker at execution time; hand-fix stragglers | **MD050/MD055 pinned ✓; MD004/MD029 OFF** — their fixers flipped list markers both directions and renumbered completed.md entry numbers (`72.` became `1.` with an injected leading space); doc/ restored from HEAD, rules disabled |
| manual | MD040 28 (fence language tags), MD034 31 (bare URLs → wrap), MD018 10, MD056 3, MD028 3, MD046 2, MD038 2, MD026 2, MD041 1, MD025 1, MD047 1 | per-case hand fixes (~53) | done — MD034/MD018 proved fixer-fixable; the rest + 6 MD018-manufactured-heading escapes by hand |

### 3.2 findata/ (1,244 generated + curated notes)

Default rules: **77,955 issues in 1,244 files — every file** (MD013
39,798, MD022 7,429, MD032 6,852, MD033 5,305, MD025 3,349, MD024 3,013,
MD036 2,713, MD012 2,407, MD049 2,328, MD009 1,584). Defaults measure the
generators' house style, not defects — reject for findata.

Defects-only ruleset (§4.2): **14,574 issues in 1,072 files**, in three
tiers:

| Tier | Rules (counts) | Attribution | Disposition |
|---|---|---|---|
| 1 — real, render-affecting | MD056 81, MD037 269, MD047 372, MD018 6 | see below | gate these; fix writers, then backfill |
| 2 — cosmetic block-assembly | MD032 6,852, MD012 2,407, MD009 1,584, MD029 36, MD031 5 | block-assembly seams in the ~6 note writers; renders fine | rules stay **off** permanently |
| 3 — structural by design | MD025 1,506, MD024 1,456 | quoted edition titles; repeated Chatter headings | rules stay **off** (`front_matter_title: ""` already removed the 1,843 YAML-title phantoms: 3,349 → 1,506) |

Tier-1 attribution:

- **MD056 (81 broken tables):** 71 in `Points_And_Figures/` reprint
  editions (substack conversion leaves unescaped pipes) — these ride the
  already-planned reprint-recovery effort; the rest are company notes
  (Lead_Reclaim_Rubber_Products:35 — a row with 8 cells against an
  expected 3, "extra data will be missing"; JSW_Steel:60; Candour_Techtex).
- **MD037 (269):** one uniform artifact — `space **…` at the same column
  (~144) across dozens of company notes (samples: UPL.md:214,
  Apollo_Tyres.md:95). One writer emits this; identify and fix at source.
- **MD047 (372):** missing EOF newline, one per edition/Chatter note —
  the newsletter-parser writer doesn't terminate files.
- **MD018 (6):** `#9` / `#2` / `#Towards Small Finance Bank` in reprints —
  render as literal text; benign but signals reprint quality.

## 4. Design

One repo-root `.markdownlint-cli2.jsonc`; the gate invokes
`npx -y markdownlint-cli2@<pinned>` with no glob arguments (globs live in
the file). Explicit globs are the exclusion mechanism (no
`.markdownlintignore`, which cli2 dropped anyway).

### 4.1 The config (verbatim — this is the artifact S1 lands)

```jsonc
// .markdownlint-cli2.jsonc — markdownlint-cli2 v0.23.2 (markdownlint v0.41.1)
{
  "globs": [
    "doc/**/*.md",
    "findata/**/*.md"
  ],
  "noBanner": true,
  "noProgress": true,
  "config": {
    // doc/ + corpus prose base — tuned 2026-09-01 (492-issue baseline).
    // S2 fixer trial: MD004/MD022/MD029/MD032 fixers CORRUPT wrap-heavy
    // prose (markers flipped, completed.md entry numbers renumbered,
    // false headings manufactured) — rules OFF, findings stay.
    "default": true,
    "MD004": false,                        // ul-style: flags wrap continuations; fixer unsafe here
    "MD013": false,                        // line-length: prose/tables run long by design
    "MD022": false,                        // blanks-around-headings: same wrap-continuation class
    "MD029": false,                        // ol-prefix: renumbered real entry numbers in the S2 trial
    "MD032": false,                        // blanks-around-lists: same wrap-continuation class
    "MD050": "asterisk",                   // strong-style: **x** not __x__ (dunders pre-backticked, S2)
    "MD055": "leading_and_trailing",       // table-pipe-style: | a | b |
    "MD060": false,                        // table-column-style: cosmetic
    "MD036": false,                        // emphasis-as-heading: generator house style
    "MD033": false,                        // inline HTML: reprints/citations
    "MD024": { "siblings_only": true },    // repeated headings across sibling sections OK
    "MD025": { "front_matter_title": "" }  // YAML title is not an H1
  },
  "overrides": [
    {
      "filter": ["findata/**/*.md"],  // NOT "globs" — wrong key silently no-ops
      "combine": "replace",           // else the doc/ base bleeds into findata
      "config": {
        // Tier-1 defect rules only — LINT-ONLY over findata, never --fix
        "default": false,
        "MD018": true,   // heading missing space after # (reprint artifacts)
        "MD037": true,   // spaces inside emphasis markers (single-writer bug)
        "MD047": true,   // file must end with a newline (parser writer)
        "MD056": true    // table column-count mismatch (silently loses data)
      }
    }
  ],
  "ignores": [
    "findata/_pending_triage_report.md"  // run artifact, not corpus
  ]
}
```

Notes: root-level `*.md` (README, AGENTS) are deliberately out of scope —
the 492 baseline was measured over `doc/**` only; widening is a separate
measured decision. MD019/MD023 cost nothing today (0 findings) and can be
added to the findata override later as free regression coverage.
Override semantics (verified against the v0.23.2 README during S1):
override objects key on **`filter`** (not `globs`) and a wrong key
silently no-ops the override; **`combine: "replace"`** is what makes
findata Tier-1-only — the default `merge` would keep the doc/ base rules
(MD024/MD025 alone would add ~3k style findings over findata).

### 4.2 Wiring

- New advisory step (parallel `make advisory` row, appends to
  `advisory_report.txt` like the others): locates node via
  `shutil.which` (nvm-managed, NOT under `.venv`) and **silently skips**
  when absent — same convention as the S6 TS search footprint and
  `make frontend-check`.
- Version pinned in the helper constant (`markdownlint-cli2@0.23.2`);
  npx warm-cache makes runs ~3 s. Rejected: adding a frontend devDep —
  couples a root-corpus lint to frontend install state.

### 4.3 Slices

- **S1 — config + advisory wiring (small diff, no content changes):**
  land `.markdownlint-cli2.jsonc` (§4.1), the advisory helper + make
  wiring, pin the version. Gate is red at this point (expected);
  advisory-only.
- **S2 — doc/ remediation (medium diff, doc/ only):** `--fix` sweep over
  `doc/**` (280 auto-fixable) + style-align config for MD004/MD029/MD050/
  MD055 + ~53 hand fixes (28 fence-language tags, 31 URL wraps, small
  structural). `doc/templates/README.md` is guard-checked
  (test_templates) — guards must stay green after the sweep. Advisory
  doc/ side goes green.
- **S3 — findata Tier-1 writer fixes (small diffs each):** parser EOF
  newline (MD047 372 stops growing); identify + fix the MD037 writer;
  escape pipes in the company-note table writers (MD056 outside
  reprints). Reprint tables ride the separate reprint-recovery arc.
  EXECUTED 2026-09-01 — with two attribution corrections discovered at
  execution (Appendix S3 rows): MD047's writers are derive_insights
  (262 company notes) + pdf_conv_md (108 editions) + 2 manual sector
  tails, and the 9 non-reprint MD056 company rows are one-off
  scrape/OCR-paste content with NO live writer — they move to S4.
- **S4 — findata backfill (PERMISSION-GATED, huge diff):** one-time
  lint-only remediation of residual Tier-1 findings across ~450 notes —
  mechanical, reviewed as a changeset; **never `--fix` over findata**
  (§5). Full cascade after: maint-full, search-fresh, snapshot.
- **S5 — promotion (EXECUTED 2026-09-01, user-directed):** the md-lint
  step was promoted INTO the blocking qa gate (adjacent to ruff's lint
  row; removed from advisory to avoid double-running), the
  `doc/templates/README.md` language matrix Markdown row moved to qa
  (format gate stays "—" deliberately — formatters reflow prose, §2),
  completed.md entry #191 filed, and the proposal archived here.

## 5. Risks

- **`--fix` over findata is forbidden** — markdownlint's fixer rewrites
  whitespace wholesale, and the vault's sentinel/auto-block machinery is
  exactly what produced the 2026-08-19 marker-collision incident (4
  deletion/misplacement bugs). findata is lint-only in the gate; fixes
  land via writers (S3) and the reviewed S4 sweep.
- **Gate before green = permanently red wall** — findata is co-owned by
  ~6 writers, so S4 must land before any gate promotion; the S3/S4 order
  is not negotiable.
- **Config envelope ambiguity** — the cli2 `"config": {}` wrapper vs
  flat rule config is silently mishandled across tools (§2), and cli2's
  own `overrides[].filter` key silently no-ops when misspelled (§4.1,
  S1 measured). One tool, one file; never convert formats by hand.
- **Node dependence** — the gate is Node-gated like frontend-check:
  advisory rows SKIP (not FAIL) without node, so the advisory stays
  green on node-less boxes.
- **Makefile help-line alphabetical gate** — if a standalone
  `make md-lint` target is added, its help line must sort correctly
  (`md-` < `mojo-`).
- **diff churn in doc/** — S2 touches ~52 files of prose; the embed
  caches are content-hash keyed, so the doc-search re-embed cost is
  proportional to changed files only (warm refresh otherwise).

## 6. Non-goals

- Tier-2 cosmetic rules (MD032/MD012/MD009/MD029/MD031) over findata —
  off permanently; rendering is unaffected and the churn is not worth it.
- Tier-3 design rules (MD025/MD024) over findata — house design.
- Root-level `*.md` (README, AGENTS) linting — separate measured decision.
- prettier-for-markdown, custom JS rules, editor-plugin rollout — none
  needed; vscode-markdownlint picks up the same config file if the user
  installs it.
- The substack reprint recovery itself (separate, already planned arc) —
  this proposal only gates its table output.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-01 | `npx -y markdownlint-cli2 "doc/**/*.md"` (defaults) | 2,836 issues | MD013 1,210, MD060 1,045, MD032 104, MD004 100, MD022 80 |
| 2026-09-01 | tuned config (MD013/MD060/MD036/MD033 off, MD024 siblings, MD025 fm-title) over doc/ | **492 / 52 files** | fixable 280; gate baseline |
| 2026-09-01 | `markdownlint-cli` same tuned config, flat format | 492 / 52, identical findings | engine parity |
| 2026-09-01 | warm timing ×3 | cli2 2.72–2.81 s, cli 2.83–3.44 s | npx startup dominates |
| 2026-09-01 | `markdownlint-cli2 "findata/**/*.md"` (defaults) | 77,955 / 1,244 files (all) | style noise; reject |
| 2026-09-01 | defects-only config over findata | **14,574 / 1,072 files** | tier split §3.2 |
| 2026-09-01 | MD056 file attribution | 71/81 in `Points_And_Figures/` | rest: Lead_Reclaim 3, JSW_Steel 2, Candour_Techtex 3 |
| 2026-09-01 | MD037 samples | `**…` at col ~144 across many company notes | single-writer artifact |
| 2026-09-01 | npm registry check | cli2 0.23.2 (2026-07-27), cli 0.49.1 (2026-07-14), both node ≥22 | both actively maintained |
| 2026-09-01 | repo scan for legacy configs | no `.markdownlintrc` / `.markdownlintignore` / INI anywhere | cli2's dropped formats are a non-issue |
| 2026-09-01 (S1) | first wiring run with `overrides[].globs` | override silently ignored — findata ran the doc/ base (26,176 findings, MD022/MD032/… all firing) | wrong key = silent no-op; cli2 keys overrides on `filter` + `combine` |
| 2026-09-01 (S1) | corrected config (`filter` + `combine: replace`) | doc/ ≈ 492 + findata Tier-1 only (expected ≈ 728: MD047 372, MD037 269, MD056 81, MD018 6) | matches the §3.2 Tier-1 measurement |
| 2026-09-01 (S2) | full `--fix` over doc/ (all fixable rules incl. MD004/MD029 pins) | CORRUPTED: markers flipped both directions on wrap continuations (a `+ ".zst")` line became a dash item; dash lists became plus), completed.md entry `72.` lines renumbered to `1.` with an injected leading space, `__main__`/`__pycache__` → `**main**`/`**pycache**`, `8.`→`2.` in a wrapped flow, false `#`-headings manufactured (MD018 fixer) | doc/ restored from HEAD (`git checkout`); every content change had been captured via `git diff -w` audit first |
| 2026-09-01 (S2) | safe fixer pass (MD004/MD022/MD029/MD032 off; dunders pre-backticked) | 3 residual corruptions (MD049 ate `_ALIASES`/`test_fuzz_{…}` identifiers) — repaired by backticking the tokens | the `-w` audit-after-every-bulk-pass discipline is what makes this tooling usable at all |
| 2026-09-01 (S2) | hand fixes: 6 escaped `#`-references (manufactured h1s), 2 table-pipe escapes, 1 split table row joined, 3 blockquote gaps, 2 indented→fenced blocks, 22 fence language tags, MD041 file-disable on completed.md | doc/ side = **0 findings**; findata = 728 Tier-1 (MD047 372, MD037 269, MD056 81, MD018 6 — matches §3.2) | `test_templates` 18 ✓, static_checks ✓, doc-search re-embedded 41 files |
| 2026-09-01 (S3) | Tier-1 attribution by dir | MD047: Companies 262 + Chatter 81 + P&F 24 + PlotLines 3 + Sectors 2; MD037: Companies 263; MD056: P&F 72 + Companies 9; MD018: P&F 6 only | corrects the §3.2 "parser writer" framing |
| 2026-09-01 (S3) | MD037 source found | `derive_insights.py` chatter bullet: `- **{paraphrase[:140]}**` + `…` appended OUTSIDE the emphasis — the cut lands on a space ⇒ space-detached closing marker (broken emphasis) | fix: ellipsis inside (`text…**`) + rstrip; unit test pins it |
| 2026-09-01 (S3) | MD047 writers found | derive_insights wrote rewritten notes without guaranteeing EOF newline (262 companies); pdf_conv_md wrote converted editions from `"\n\n".join(pages)` (108 editions); Sectors Insurance/Logistics = manual appends, no writer | fix: newline guards at both derive_insights write sites + pdf_conv_md write; integration test pins `endswith("\n")` |
| 2026-09-01 (S3) | non-reprint MD056 (9 rows, 4 companies) inspected | all one-off scrape/OCR-paste artifacts (Lead_Reclaim product table split mid-row, JSW OCR chart captions after table rows, Trishakti row missing a cell, Candour multi-line cells without `<br>`) — NO live writer emits broken company tables | moved to the S4 backfill list; writer-escape premise withdrawn |
| 2026-09-01 (S4) | backfill executed (lint-only, never `--fix`): 372 EOF newlines, 269 space-detected `**…` bullets → `…**`, 6 `#NNN` escapes, 9 company table rows repaired (Candour `<br>` joins, Lead_Reclaim grade lists restored + `—` placeholder for a scrape-lost cell, Trishakti cell split, JSW OCR chart fenced as text) | **`make md-lint` → 0 violations, exit 0** — full corpus green | 524 files; chatter-bullet repairs byte-match the S3 writer's render (no derive drift) |
| 2026-09-01 (S4) | 72 reprint-table MD056 (6 P&F editions incl. image_map/Stakes) inspected | tables structurally mangled by the PDF conversion (multi-line cells, flattened merges) — mechanical escapes would fabricate cell boundaries | rule-scoped override quarantine (Tier-1 minus MD056 stays active on those 7 files); removal rides the reprint-recovery arc |
| 2026-09-01 (S4) | promised cascade | maint-full **12/12 PASS** (note-search re-embed, sector gates, embeddings --maint, doc-search, analytics, insights, events, re-snapshot) + search-fresh fresh + verify_notes green | DB writes were derive-side only; note edits are render-neutral |
