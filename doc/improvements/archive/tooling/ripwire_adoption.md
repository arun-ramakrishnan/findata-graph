---
title: "ripwire adoption — measured nav-tooling swap + Mojo lane scope"
status: executed
filed: "2026-09-07"
executed: "2026-09-08"
completed_md: "213"
area: "AGENTS.md, codebase-memory-cli skill, doc/notes/scripts query layer"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change.
     Takes completed.md #213 on archival (latest at filing: #212). -->
# ripwire adoption — measured nav-tooling swap + Mojo lane scope

**Date:** 2026-09-07 · **Status:** PROPOSED · **Mode:** evaluate-first,
adopt in slices, losses-first throughout.
**Area:** `AGENTS.md` (map-before-read posture) · `codebase-memory-cli`
skill (retire from daily use) · `doc/notes/scripts` query layer (partial
replacement) · possible upstream Mojo lane (W1–W7).
**Evaluation pin:** ripwire v0.4.0 linux-x64 (sha256
`fd0bd0fa…21bbfc8`), binary at
`/tmp/opencode/ripwire-bin/ripwire-0.4.0-linux-x64/ripwire`, source clone
at `/tmp/opencode/ripwire`, raw loop outputs at `/tmp/opencode/loop/`.

## 0. Context and directive

Structural code discovery currently goes through codebase-memory-mcp
(graph DB, daemon-backed), intent search through hand-built
`doc_query`/`script_query` indexes, and everything else through bare
`rg` + reads. ripwire (redhat-et/ripwire) — a single offline C++23
binary, deterministic call graph + BM25/PageRank bundles — was evaluated
as a replacement for the first and a partial replacement for the second,
head-to-head on this repo, before adopting anything. User directive
(2026-09-07): record the evaluation, run the loop, study the result,
scope the Mojo gap. This proposal is that record plus the adoption
slices it justifies. Scratch log `doc/local/ripwire_eval.txt` was folded
here in full and removed.

## 1. What was reviewed (source, not marketing)

`README.md`, `docs/ARCHITECTURE.md`, `docs/CODEX_ORCHESTRATION.md`,
`skills/ripwire-{router,mcp,orient,efficient,graph-query}/SKILL.md`,
`prompts/{README,head-to-head,ranking-eval-loop}.md`. Pipeline
`ingest → graph → rank → serialize → cli/mcp`; deterministic
(sorted-crawl node IDs, byte-identical reruns gated); tree-sitter per
language; token-budgeted XML with self-reported `est_tokens`,
confidence+margin, cap disclosure. Cold parse of our tree 2.6 s (1661
files, 34k symbols); warm CLI 0.2–0.5 s via on-disk cache. No API key,
no embeddings, no daemon.

Staleness machinery (from source): CLI re-crawls per run with
incremental per-file content-hash re-parse (`--cache`) plus committable
`.lean`/`.rich` artifacts; the MCP server re-sweeps per-file
(mtime-ns, size, ctime-ns) + every directory mtime before EVERY verb and
warm-rebuilds only changed files (kqueue fast-path where present; Linux
inotify an explicit TODO with the full sweep as fallback — same
contract). Handles are content-hash pinned: stale `fetch_body`/edit
refuses instead of serving shifted bytes. Staleness fails closed —
never a silent wrong answer. git history (`cochange`) and working-set
signals ride the same rebuild path.

## 2. Head-to-head loop (their `prompts/head-to-head.md` discipline)

Three arms, pre-registered questions, golds by reading source FIRST (one
correction caught mid-loop and recorded: `bench_pool.mojo` DOES import
`row_cosine` — the pre-registered gold wrongly excluded it). Repo pin:
this worktree @ `d294cd93`, tree clean at loop time.

- **Arm A** — ripwire v0.4.0, plain CLI, warm on-disk cache (primed by one
  `--report`; exactly our opencode usage shape).
- **Arm B** — codebase-memory-mcp CLI. Deviation disclosed: the only
  indexed project points at the main checkout @ `112cf93e`, not this
  worktree (no re-index — skill forbids unasked full index). All golds
  are symbol-anchored and verified present there, so grading stands; the
  registry/commit mismatch is itself finding F-B1. Wall ≈ 7.1 s on EVERY
  query = cold-daemon startup tax (no warm daemon running).
- **Arm C** — bare `rg` + Read, expert-operator upper bound (right pattern
  first try; treat bytes as a floor).

Questions (golds in brief): Q1 callers of `vss_match` (gold:
`resolve_entity:632`, `embed_eval:215` + tests); Q2 `_net_income_series`
contract + callers (`:233,234`); Q3 Mojo `row_cosine` importers
(corrected gold: bench_cosine, bench_cosine_max, bench_cosine_max_parallel,
bench_pool); Q4 doc rationale — why `make_company_note` was excluded from
fixture consolidation (gold: dates/no-dates semantic fork); Q5 blast
radius of `resolve_entity` (gold: `display_ticker:898` + tests); Q6
covering tests of `derive_insights.py` (core-3 + integrations); Q7 literal
`mutualfund_holders` (gold: `_fund_holders_frame` chain + tests); Q8
concept "retry logic" (gold: `capture_newsletter_images`,
`enrich_relations` retry policy).

Results (bytes before gold reachable, follow-ups included; wall
median-of-3):

| q | A ripwire | B code-memory | C rg+Read |
|---|---|---|---|
| Q1 callers | PASS 4121B 0.25s | PASS 386B 7.1s | PASS 1706B 0.01s |
| Q2 contract | PASS+1fup 3948B 0.46s | **FAIL** — "No nodes match" for an on-disk symbol (F-B2) | PASS 1045B |
| Q3 mojo | FAIL-honest (low-conf, 0 cand) | PARTIAL 2/4, import mislabeled as Function def (F-B3) | PASS 1627B |
| Q4 doc | RANK-PASS/ANSWER-PARTIAL 14kB — right docs #1/#2, rationale cut by 8k ceiling | FAIL (unrelated test methods) | **PASS 909B — baseline wins outright** |
| Q5 blast | PASS 4692B (reaches=8 + tested lens) | PASS-prod 287B (no tests) | PASS 1100B |
| Q6 tests | PASS-core 2381B + run recipes | PARTIAL (symbols, not covering list) | PASS 436B — baseline wins on bytes |
| Q7 literal | PARTIAL→PASS w/ `--grep-in=any --legend=compact` (3753B, §4) | N/A-by-design (routes to rg) | PASS 1203B — baseline wins |
| Q8 concept | PARTIAL (1/2, validator noise) | FAIL (0 hits) | PASS 337B — baseline wins |

## 3. Losses first

Ripwire's own (not buried): **L1** Mojo — thin map, honest
low-confidence refusal (until §5); **L2** default grep tier hides
string-span code hits (fix in §4); **L3** concept-query BM25 head noise
("retry" → validator clutter, 1/2 golds); **L4** `--recall` ranks docs
but does not extract the sentence (14kB bundle, right docs #1/#2, the
rationale never in-budget — recall ≠ extraction); **L5** legend tax
(~9.6kB vs rg ~1kB on the same questions — fixed by compact, §4).

Arm B's (recorded, not buried): **F-B1** fixed registry @ wrong commit,
no worktree awareness; **F-B2** SILENT staleness — September symbols
(`_net_income_series`, VssRunIndex — since renamed `_VssRunIndex`) on
disk in the indexed root → 0
hits, no warning, while old `display_ticker` still resolves (dates the
index). Disqualifying for a map tool. **F-B3** Mojo import-as-Function
mislabel + 2 missed importers (`index_status` shows `parse_partial`
error ranges over Mojo files — error-recovery parsing without a real
grammar); **F-B4** no doc lane; **F-B5** 7.1 s cold-daemon tax per CLI
call. It beat A on nothing but Q1 bytes (386B vs 4121B at 28× the wall).

## 4. Q7 follow-up (same day — closes L2/L5)

`--legend=compact`: 9634B → 3244B, every evidence row byte-identical
(`schema="ripwire.grep/v1"`) — compact becomes the default posture.
Tier mechanism found: ALL source occurrences of the literal sit in
STRING spans (docstring prose, dict key), so zero code-span hits exist
and the default ladder never descends to them. Standing rule:
**literals always go `--grep-in=any`** (exhaustive, no tiering) —
3753B, 0.21 s, all 9 hits with enclosing symbols. Remaining gap vs rg is
legend overhead, not missing evidence.

## 5. Mojo lane scope (unlocks full-repo coverage)

Probe 0 — ride the Python grammar (Metal-on-C++ precedent): DEAD.
tree_sitter_python over our 19 Mojo files = 773 ERROR/MISSING nodes
(~40/file); call edges would be fiction. Probe 1 —
`whistlebee/tree-sitter-mojo` @ 0.1.0 (freshest of 8 candidates; builds
clean with gcc against ripwire's vendored runtime headers): **3 ERROR
nodes total**, all one construct — `alias` (compile-time constants; no
alias node in the grammar). Node inventory already carries everything a
`tags.scm` needs (function/struct/call/from-import/decorator/
typed-parameter). Probes at
`/tmp/opencode/probe_{py_on_mojo,mojo_grammar,mojo_errs}.py`; grammar
clone at `/tmp/opencode/ts-mojo-wb`; built `.so` at
`/tmp/opencode/libmojo.so`.

Work items, gated in order: **W1** pin grammar (re-probe dmitry-salin /
oaustegard; Mojo 1.0.0 here vs "Modern Mojo ≥26" claims) + license check;
**W2** `alias` gap — upstream PR preferred, else
`third_party/patches/mojo/` (yaml-scanner precedent). **W2 is the
go/no-go gate**: without alias, module constants vanish from the map
(functions-only lane still answers Q3-class questions — say so upfront
if capped). **W3** vendor + CMake (`ripwire_use_vendored_source`
pattern; pin the generated-files commit, not main — swift lesson);
**W4** `Lang::Mojo` + audit of ~200 `Lang::` sites across 12 headers
(table row `{ ".mojo", Lang::Mojo, &tree_sitter_mojo, "mojo" }`);
**W5** `queries/mojo/tags.scm` with per-shape gates (pyshapecheck
pattern); **W6** `test/mojocheck.sh` + docs (21→22 langs); **W7**
upstream via their `prompts/improve-for-my-language.md`. Estimate: W1–W2
hours (parse half done in this evaluation); W3–W6 days.

## 6. Slices

### S1 — Adopt CLI-first ripwire posture for Python work

`ripwire wrap opencode` config; standing flags `--legend=compact` and
`--grep-in=any` for literals; map-before-read note in `AGENTS.md`
alongside the existing query-layer rules (no replacement of rules, one
added posture line + pointer). Exit: config live, posture line merged,
one demo query per verb family recorded here.

### S2 — Retire codebase-memory-mcp from daily use

Skill file updated: graph-discovery default becomes ripwire; the old CLI
kept as fallback with the F-B2 staleness caveat pinned (check
`index_status` freshness before trusting a zero). No daemon, no
re-index, no deletion — retire, not remove. Exit: skill updated, this
proposal cites the F-findings as reason.

### S3 — Doc/notes recall split (no `doc_query` replacement)

`--recall` becomes the FIND step for doc questions (ranked docs, 8k
ceiling); the closing read stays manual — same as `doc_query` today,
which stays. `script_query` stays fully (flags/make-wiring metadata out
of ripwire's scope). `mentions`/`doc_drift` get a trial run against the
next doc edit that touches code anchors (record hit/miss here). Exit:
one worked example each for recall-find and doc_drift, kept or dropped
on measurement.

### S4 — Mojo lane W1–W2 (gated probe, this repo is the corpus)

Grammar pin + `alias`-gap resolution against OUR 19 files (ERROR=0 or
documented residual). Go/no-go for W3–W6 taken here on measurement, not
enthusiasm. Exit: pinned grammar + alias verdict recorded; W3–W6 filed
as follow-up only on go.

### S5 — Full removal (LAST: references, skill links, docs)

Only after S1–S4 exits are green — removal is the end state, not a
cleanup done early. Retire (S2) becomes remove:

1. **Unlink the skill** per `doc/local/skills_symlink.md` (source-of-truth
   discipline): delete `codebase-memory-cli` from the common store
   `~/.agents/skills`, then remove its link from each harness dir
   (`~/.claude/skills`, `~/.config/opencode/skills`,
   `~/.zcode/skills`, `~/.prime/agent/skills` — same loop as §3/§4 with
   `rm` in place of `ln -s`, one basename, verify counts 16→15
   everywhere). Update that doc's §1 table + counts. Deletion needs the
   user's explicit go at execution time (house closers rule + the doc's
   permission gate on deletes) — this slice authorizes the plan, not the
   keystroke.
2. **Live doc/code references** (9 sites, enumerated 2026-09-07 — re-grep
   at execution; archive/ + `completed.md` history STAYS as record):
   `doc/design/architecture.md` §9 (rewrite the section for ripwire),
   `AGENTS.md:44` (division-of-labor line), `doc/procedures/script-search.md`
   (:12, :71–72), `doc/procedures/diagrams.md:43`,
   `helpers/misc/script_query.py:12` (docstring),
   `helpers/maintenance/rebuild_script_search.py:9,266` (comments).
3. **Explicitly untouched:** the installed binary, its on-disk graph DB,
   and the indexed project entry — removal covers references and skill
   links only. Re-indexing or deleting tool state is out of scope (S2
   rule carries over).
4. **Exit:** `rg "codebase-memory|codebase_memory"` over the repo —
   excluding `doc/improvements/archive/`, `completed.md` history, and
   this proposal's §§2–3 evaluation record — returns zero; skill absent
   from all five dirs; `make search-fresh APPLY=1` converged.

## 7. Non-goals + explicitly deferred

- Replacing `doc_query`/`script_query` wholesale (measured: recall finds,
  extraction stays manual; metadata stays house-owned).
- Vault machinery (`findata/**` sentinels, md-lint scoping) — untouched.
- Full Mojo lane (W3–W7) until S4 gates go.
- Re-indexing or deleting codebase-memory-mcp state (S2 rule carries over).
- MCP-server mode (CLI-first covers opencode; server revisit only if
  warm-verb latency ever matters).

## 8. Definition of Done

- S1 posture live with standing flags; S2 skill retired-with-caveat; S3
  worked examples recorded (keep/drop on measurement); S4 alias verdict
  recorded with go/no-go for the full lane; S5 references zero (per exit
  grep), skill unlinked, history preserved.
- `make qa` + `make advisory` green once at close with user go;
  `make search-fresh` converged; Execution Results section appended with
  the adoption deltas (token/lookup counts before/after on real tasks).

## 9. Execution results (2026-09-08 — S1–S4 executed; S5 pending go)

Adoption-day state: repo @ `5f00d479` +dirty (the quote-capture arc
landed since the eval pin `d294cd93`, so every first-call number below
includes ripwire's incremental re-parse doing its job on real churn).
Timings are single-pass smoke, not perf claims (±20% doctrine).

### S1 — posture live

- Install: tarball digest re-verified = eval pin (`fd0bd0fa…21bbfc8`);
  binary copied to `~/.local/bin/ripwire` (39 MB, bin sha256
  `6a1957b8…`) — on PATH, durable past the /tmp pin path. `ripwire wrap
  opencode` reviewed: its RECOMMENDED wiring is CLI-direct + an
  AGENTS.md paste block, so the adopted config IS the AGENTS.md posture
  section (map-before-read; standing flags `--grep-in=any` and
  `--legend=compact` embedded in the literal-grep line); MCP
  registration left out per §7. The division-of-labor rule line
  re-pointed to ripwire with `rg` as fallback; codebase-memory is not
  named there — S5's exit grep stays clean by construction, the
  retirement note lives in the skill only.
- Demo per verb family (bytes / wall; outputs under `/tmp/opencode/s1/`):

  | verb | result |
  |---|---|
  | `--for` orient (task lens) | 10,103 B / 2.61 s (first call, incl. re-parse) |
  | `--callers=vss_match` | 1,284 B / 1.45 s first call — count=10: `resolve_entity` + `cmd_report` (triage_pending_quotes.py:97 — NEW, the quote-capture edge the eval gold predates) + `run_vss` + 7 tests |
  | `--impact=resolve_entity` | 1,553 B / 0.24 s |
  | `--grep=mutualfund_holders --grep-in=any --legend=compact` | 4,168 B / 0.35 s — hits=10 shown=10 complete=1 (eval gold 9 → 10: the tree itself gained doc mentions), string-span case handled |

  The Q1 re-run doubled as the staleness demo: the new caller appears
  because the crawl is current — exactly the F-B1/F-B2 failure mode of
  the retired tool. One note for the upstream lane:
  parse_degraded="1" appeared on helpers/core/get_tickers.py rows
  (evidence rows still complete; probe + issue at W7 time).

### S2 — codebase-memory-mcp retired to fallback

`~/.agents/skills/codebase-memory-cli/SKILL.md` (common store; all four
harness symlinks verified) rewritten: description now FALLBACK ONLY, the
F-B1…F-B5 findings pinned as the retirement reason, fallback rules added
(`index_status` freshness gate before any answer; never trust a zero),
tool table kept — `get_architecture` / `query_graph` remain its unique
verbs. No daemon touched, no re-index, no deletion: retire ≠ remove.

### S3 — doc-lane trials (keep/drop on measurement)

- **recall-find — KEEP as the FIND lane.** Question: why was
  make_company_note excluded from the shared note template (gold:
  archive testing/consolidate_tests_fixtures.md "differ semantically
  (dates/no-dates, guidance/generic body, bare-sector/full-tag
  params)"). 14,035 B / 0.44 s, est_tokens=5,520 < 8k ceiling; gold doc
  ranked #1 at relevance 11.663 vs 8.676 for #2. The rationale sentence
  was NOT in the emitted slice (per-doc share_bytes=1,674 cut it) — L4
  holds: recall finds, extraction stays manual; `doc_query` unchanged.
- **mentions — KEEP.** `--mentions=vss_match --legend=compact`: 8 docs /
  824 B / 0.20 s (archive docs, completed.md, pending.md, live
  proposals). Honesty check for free: `--mentions=make_company_note`
  refuses "symbol not found" — correct, the name now exists only in
  docs (the fixtures arc renamed the defs; real rot, honestly refused).
- **doc_drift — KEEP.** @ `5f00d479`+dirty: docs=1,410 clean=1,385
  anchors=3,798 checked=1,996 drift=71 dated=71. Why-breakdown:
  range-straddles 40, missing-file 24, undefined 20, line-moved 17,
  past-eof 10, const-value 5, deleted 1; 59 rows correctly
  reclassified as dated-record. Spot-checks are REAL rot: db_maint.py
  moved helpers/core → helpers/maintenance, and `_backup_file` sits at
  :360 vs the doc's :376-380 anchor. The mentions/doc_drift trial
  clause closes with these runs — direct trial on the live tree
  supersedes waiting for the next anchor-bearing edit.

### S4 — Mojo lane W1–W2 (gate taken on measurement)

- **W1 pin:** whistlebee/tree-sitter-mojo — MIT, freshest of the three
  candidates, generated parser + corpus tests in-repo. Re-probes:
  dmitry-salin/tree-sitter-mojo @ 1.0.5 (2026-08-29, MIT) parses our
  19-file corpus with the SAME 3 `alias` ERROR nodes; oaustegard last
  touched 2026-05-17 and has no `alias` keyword either. The eval's
  "Modern Mojo ≥26" README concern dissolves — the measurable
  criterion is ERROR count on the real corpus, and that is what pins.
- **W2 — GO.** Four-line grammar.js patch: `alias` joins `comptime` at
  the three declaration sites (`comptime_declaration` both branches,
  `comptime_member_declaration`) and joins print/async/await in the
  keyword_identifier demotion, so `alias` stays usable as an identifier
  in expression position. `tree-sitter generate` (0.25.8) clean, zero
  conflicts; grammar corpus 84/84 with a new alias test; rebuilt parser
  → ERROR=0 across all 19 Mojo files (was 3). Committed as `a254fb5`
  in the scratch clone `/tmp/opencode/ts-mojo-wb`; the grammar.js diff
  is the W3 vendoring artifact and the W7 upstream-PR payload:

  ```diff
  --- a/grammar.js
  +++ b/grammar.js
  -          "comptime",                    (comptime_declaration, both branches)
  +          choice("comptime", "alias"),
  -        "comptime",                      (comptime_member_declaration)
  +        choice("comptime", "alias"),
  -      prec(-3, alias(choice("print", "async", "await"), $.identifier)),
  +      prec(-3, alias(choice("alias", "print", "async", "await"), $.identifier)),
  ```

- **Consequence:** W3–W6 (vendor + Lang::Mojo + tags.scm + mojockeck)
  are green-lit as follow-up work on this go; the alias patch rides
  with the vendored grammar; W7 upstream PR carries it upstream.

DoD status: S1–S4 exits green as recorded above. S5 (reference removal
+ skill unlink, 9 re-greppable sites) executes only on the user's
explicit go — per this slice's own gate — together with the end-of-arc
gates and `make search-fresh APPLY=1`.

### S5 — removal (executed 2026-09-08, on the user's go)

- **References zero (exit grep green):** re-grep at execution found the
  live set was five files (AGENTS.md was already clean from S1):
  architecture.md §9 rewritten for ripwire (task/verb table, staleness
  contract, code-health open note carried over; the Cypher audit block
  retired to the archived proposals); script-search.md intent line +
  division-of-labor re-pointed; diagrams.md fact-gathering line;
  script_query.py docstring; rebuild_script_search.py module + inline
  comments.
  `rg "codebase-memory|codebase_memory"` excluding
  `doc/improvements/archive/` + `completed.md` history + this proposal:
  **zero**.
- **Skill unlinked:** `~/.agents/skills/codebase-memory-cli` (store)
  deleted; symlink removed from all four harness dirs; counts verified
  16 → 15 in all five dirs; `doc/local/skills_symlink.md` §1/§6 +
  header updated with the removal and its reason. `doc/local/
  codebase-memory-mcp.md` carries a RETIRED banner (fallback mechanics
  preserved: `index_status` gate, never trust a zero). Explicitly
  untouched per §6: the installed binary (`~/.local/bin/ripwire` is the
  ADOPTION install, not the legacy one), the on-disk graph DB, and the
  indexed project entry.
- **Archival:** proposal → `archive/tooling/`, completed.md #213,
  README live-list reset, search-fresh converged.
