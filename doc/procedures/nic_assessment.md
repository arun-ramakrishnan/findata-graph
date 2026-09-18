# NIC-2008 assessment — coding, promotion, and vintage discipline

The operator procedure for the vendored NIC-2008 vocabulary
(`nic2008_seed_table`, completed.md #246): how industry labels get NIC
codes, how promotions work, and how to read CIN evidence without
falling for the legacy-series trap. Normative context:
`doc/design/ontology.md` §2.7 (schemes); the change gate:
`doc/procedures/ontology-gate.md`.

## The surfaces

| Surface | What it is | Owner |
|---|---|---|
| `helpers/misc/nic2008_seed.json` | Vendored parse of the primary MoSPI PDF (md5-attested), reviewed in patches like code | machine (build mode) |
| `nic2008` table | 1,301 subclasses × chain columns + scope notes, SQLite schema v13 | machine (converger) |
| `nic2008` concept scheme | 2,067 concepts, five-level broader chain, `notation`=code, `subtree()`-native | machine (converger) |
| candidate `concept_mappings` | 109 labels × top-3 lexical suggestions, `closeMatch`, `status='candidate'`, `seed:nic2008-candidates/v1` — INERT (queries read ACTIVE only) | machine (candidates mode) |
| `findata/Misc/nic_worklist.json` | Browsable export: suggestions + CIN evidence per label | machine (worklist mode) |
| ACTIVE `concept_mappings` | Promoted label→code crosswalks — the queryable surface | operator (review / `--promote-map`) |
| note frontmatter `industry_code` etc. | Per-company codes in the writer's vault; measured by `verify_notes` `industry_coded` | operator (writer) |

Machine rows carry `seed:*` source refs and are converged (re-created,
never deleted — lapsed rows flip `superseded`). Operator rows
(`op:nic2008-review`, `agent:*`) are never touched by any converger.

## Daily driver: the review tool

```bash
.venv/bin/python3 helpers/misc/seed_nic2008.py review            # all labels, strongest first
.venv/bin/python3 helpers/misc/seed_nic2008.py review --labels 'Banks - Regional,Lodging'
.venv/bin/python3 helpers/misc/seed_nic2008.py review --limit 20 --dry-run   # decide, apply nothing
```

One screen per label:

```text
[1/109] Insurance - Life  (8 member(s))
  1) 65110 1.00 Life insurance
  2) 65120 0.85 Non-life insurance
  3) 66220 0.27 Activities of insurance agents and brokers
     *65110 x1 (post-2008: 0, pre-2008: 1) [HDFC Life]
      24223 x1 (post-2008: 0, pre-2008: 1) [Max Financial Services]
     (* = code exists in the NIC-2008 table)
  approve? 1-3 / c CODE / s / ? / q / x:
```

- numbered rows — the lexical candidates: NIC subclass code, affinity
  score (label tokens vs the 1,301 descriptions), description
- indented rows — the CIN evidence: each distinct `cin_nic5` among the
  label's members, with the company count and the registration-vintage
  split
- `*` — the code exists in the NIC-2008 table

### Keys

| Key | Action |
|---|---|
| `1`/`2`/`3` | approve that candidate — promotes it to ACTIVE |
| `c CODE` | override with any NIC-2008 subclass (validated; re-prompts on a bad code). Overrides insert operator-owned rows (`op:nic2008-review`) |
| `c CODE:matchtype` | stack a second lane on the label — `narrowMatch` for umbrella labels covering several subclasses (e.g. Integrated Freight & Logistics: 49231 closeMatch + 53200/52101/49120 narrowMatch). Re-decision supersedes only its own lane |
| `s` | skip — parks the label (operator judgement: low-quality label, not classifiable with current suggestions). Parked labels leave the default walk and the worklist `suggested` lane; they land in the `skipped` lane. Revisit via `review --skipped` or `--labels NAME` |
| `?` | re-print the full evidence |
| `q` | stop reviewing, keep this session's approvals, go to batch confirm |
| `x` | abort — discard everything approved this session |

The batch confirm (`apply N promotion(s)? y/N`) is the only write gate;
anything but `y` applies nothing. Every decision is journaled to
`outputs/nic_review/journal.jsonl` (gitignored, audit only) — and read
back only to park skips: the latest action per label decides its lane
(`approve` → promoted, `skip` → parked). The parked pool plus
`no_signal` is the needs-better-labels backlog (candidate scorer v2).

### Reading the evidence — the vintage rule (D-O6)

The same 5-digit nic5 can mean different things in different NIC
series. Example: `65110` is **monetary intermediation (banks)** in
NIC-98 but **life insurance** in NIC-2008. Reading rule:

1. `*` + `post-2008 > 0` — hard evidence; the company registered under
   NIC-2008 with that code. Trust it (modulo ROC quirks: a handful of
   post-2008 stamps carry codes the published table does not print —
   the integrity check reports them as census signal).
2. `*` + only `pre-2008` — soft confirm; the meaning may have shifted
   between series. Cross-check the lexical suggestion and the
   description text before trusting the code.
3. no `*` — legacy series (NIC-87/98/2004); informational only. Never
   join, never promote on it alone.

Live census (2026-09-18): 93 native / 113 post-2008-but-absent /
522 pre-2008 among 728 CIN carriers.

### One active crosswalk per label

The promote lane blocks a second ACTIVE mapping with the same
(source label, match_type): pick ONE code per label per sitting. The
losing candidates stay dormant; the worklist marks promoted labels
done. If a label genuinely spans codes, either stack match types
(`closeMatch` winner + `broadMatch` second via `--promote-map`) or —
better — carry the distinction per company in note frontmatter.

## Manual promotion (scripted/batch)

```bash
.venv/bin/python3 helpers/misc/seed_concepts.py --promote-map \
  'industry:Insurance - Life->nic2008:65110:closeMatch,industry:Lodging->nic2008:55101:closeMatch'

(`--promote-map` is its own write gate — plan-then-apply, never combined
with `--apply`. It can only flip EXISTING candidate/superseded rows;
new lanes (`narrowMatch` etc.) are inserted by the review override
`c CODE:matchtype` instead.)
```

Spec grammar: `src_scheme:src->tgt_scheme:tgt[:match_type]` — the
match_type suffix is REQUIRED for review candidates (they are
`closeMatch`; the CLI default is `exactMatch`). Plan-then-apply: any
invalid spec blocks the whole batch.

## Note stamping and the migration metric

Promotion populates the DB crosswalk. Separately, the writer stamps
company notes (per the worklist):

```yaml
industry: Banks - Regional
industry_code: "64191"
industry_label: Monetary intermediation of commercial banks...
industry_source: nic2008
industry_version: NIC-2008
```

`verify_notes` reports the migration metric per run (never a gate):
`industry_carriers` (ceiling 942 today) / `industry_nonnull` /
`industry_coded` (notes carrying `industry_code:` — starts at 0 and
rises as notes are stamped).

## Convergers and maintenance

- `seed_nic2008.py converge` (maint PRE_FULL step 8, also manual):
  table + scheme, idempotent, dropped rows REPORTED and lapsed rows
  superseded — never deleted. A roster shrink means a new source
  version: handle at source-refresh time.
- `seed_nic2008.py candidates` (NOT maint-wired — suggestions are a
  sitting, not a projection): deterministic top-k; labels with an
  active mapping are skipped and their leftover candidates retire.
- `seed_nic2008.py worklist`: regenerate the export after candidates
  or promotions change.
- Integrity: `check_cin_nic2008` (WARNING severity) — see the vintage
  census above; surfaced in `outputs/database_integrity_report.md`.

## Refreshing the source (rare — per MoSPI republication)

```bash
# fetch the official PDF the MoSPI codefinder itself serves, then:
.venv/bin/python3 helpers/misc/seed_nic2008.py build <nic_2008.pdf>
.venv/bin/python3 helpers/misc/seed_nic2008.py converge          # dry-run — expect update counts only
.venv/bin/python3 helpers/misc/seed_nic2008.py converge --apply
```

The build validates counts (21/88/238/419/1,301) and attested rows
before writing. Known primary-source quirks are handled in the parser
(prefix-quirk parents via `PARENT_OVERRIDE`, two attested typos,
page-break header reprints); a new print run may add quirks — the
count validation catches structural drift, and the DGE queryable
tables remain the alternate cross-check. After any source refresh:
run the eval gate per `doc/procedures/ontology-gate.md`, then
rebaseline.

## The crosswalk pin

The question set carries two NIC subtree pins (`subtree-nic2008-*`,
v3). The industry→nic2008 `xwalk-*` pin lands with the FIRST operator
promotion: it freezes an ACTIVE mapping's answer, and every later
gate run guards it. Add it by shape `concept_mappings_for`
(params: source scheme `industry`, the promoted source concept),
compute `expected` via `answer_question`, verify a sample by raw SQL,
self-check live-vs-live, then follow the rebaseline flow in
`doc/procedures/ontology-gate.md`.
