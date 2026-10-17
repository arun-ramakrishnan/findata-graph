---
title: "Index git history — SHA-set reconciled commit/diff search (git leg)"
status: proposed
filed: "2026-10-17"
executed: null
completed_md: null
area: "helpers/maintenance, helpers/misc, master_query git leg"
---

# Index git history — SHA-set reconciled commit/diff search (git leg)

**Date:** 2026-10-17 · **Status:** PROPOSED (S1–S5 + gemma adoption implemented same day; amended 2026-10-17, see §7–§8) ·
**Area:** new rebuilder + query CLI under helpers/maintenance and
helpers/misc; the federated front door in helpers/misc/master_query.py;
a new sqlite sidecar under memory/ (gitignored).

## 1. Motivation

Commit messages are the densest un-indexed prose in this repo. They are
operator-authored WHY/WHAT write-ups (median 1,568 chars, p90 3,504 —
measured §2) that record gotchas, root causes and mechanism decisions in
their final, landed form; the file diffs beside them carry the exact code
and vault motion. No search leg covers them: docs hold conclusions,
convo holds in-flight sessions, script_search holds the *current* code
surface — nothing answers "when did this land, why, and what moved with
it". Per-query git mechanisms are not a substitute — the operator had
already written pickaxe (`git log -G/-S`) off from experience at much
smaller history sizes: a full-history sweep per query, and on THIS repo
patch generation is pathological whenever the parquet textconv filter
engages
(a plain `git log -p` over main was killed at >7 minutes, §2 — every
parquet blob is decoded through helpers/misc/parquet_textconv.py per
diff side). Indexing inverts the cost: pay extraction once at rebuild,
query in milliseconds.

Trigger: operator proposal 2026-10-17 — "we capture a lot of
quirks/gotchas/resolution detail in our commits and the file diffs", to
complement the convo leg. Scope ruling same day: **main ref of the main
checkout only — no worktrees, no other branches, no stgit patch refs.**

## 2. Evidence (measured 2026-10-17, this box)

Corpus shape (all numbers from the measurement script in the Appendix):

| Metric | Value |
|---|---|
| commits on main | 414 (6 merges) |
| history span | 2025-03-09 → 2026-10-17 |
| message text | 739,553 chars total; median 1,568; p90 3,504; max 8,196 |
| patch text, whole history (--no-textconv) | 165.6 MB |
| patch text after noise-exclude set | 74.5 MB (−55%) |
| patch text by class (MB) | static 53.8 · findata 42.2 · venv 37.3 · doc 13.9 · helpers 8.4 · tests 6.5 · snapshots 0.4 |
| full extraction sweep (numstat, --no-textconv) | 3.9 s; patch-text pass 6.2 s |
| textconv-inclusive `git log -p` | >7 min, killed — parquet textconv decodes every blob |

Two findings the exclude list is built on, neither guessable:

- **venv/ is tracked in this repo's history** — 3,456 file-touches,
  second-largest path class, 37.3 MB of patch text. Machine noise, not
  signal.
- **static/ carries 53.8 MB of built frontend assets** — the single
  largest patch-text class, and pure churn.

The gold is findata (42.2 MB of vault motion — note history note_query
cannot see), doc (13.9 MB of decision history), helpers and tests
(14.9 MB of code motion). Message text embeds at prior-measured sidecar
rates (~0.7–2.7 rows/s by row length): 414 messages ≈ 4–10 min first
embed, cached after (embed-gc and the content-addressed cache own
retention; the per-row-commit durability fix from the CachedEmbed OOM
incident applies verbatim).

Verdict: indexed hybrid search over commit messages + filtered diff
text; pickaxe is not a fallback but a non-starter (operator: it was
unusable for them at much smaller history sizes — never a leg here).

## 3. Design

Decisions, with the alternative each one beat:

- **Main-ref set-reconciliation, not a high-water cursor.** Every
  refresh walks `git rev-list main` (seconds, no extraction) and
  set-diffs against the indexed SHA set: extract + embed only NEW SHAs;
  tombstone rows whose SHAs dropped out. stgit amend/fold/refresh
  rewrites mid-history, so a cursor either misses rewrites or keys on
  dead SHAs; the reachable set is the source of truth, reconciliation is
  idempotent, and a stale or corrupted index self-heals on the next
  APPLY. Incremental is a cost-containment requirement (embedding is not
  free — operator ruling 2026-10-17), and this shape makes it
  rewrite-proof. `--full` exists as the opt-out, per the
  default_incremental_rebuilds doctrine.
- **sqlite sidecar, not DuckDB.** The index is FTS5 + blob vectors over
  10^3–10^4 rows — the doc/script/note family's shape. Reuse that
  machinery (embed sidecar cache, stamp-keyed read path, --check
  content-verdict convention — imported from the script rebuilder, not
  forked) and stay out of the single-writer DuckDB class entirely.
  Derived state: delete it and one warm rebuild restores everything.
- **Leg split follows the settled fusion doctrine.** Commit message →
  vector leg (it is prose). File paths + diff text → FTS only:
  identifiers are bm25's home turf (27/27 in the script-leg evals) and
  vector-basis enrichment was REJECTED in the notes trial (structured
  text in embeds hurt every arm). The script leg's identifier-query
  router applies on the read path.
- **Measured noise-exclude list, versioned.** Exclusions: venv,
  snapshots, static, `*.parquet`, `*.lock`, `*.gguf`, vendor, bench_data.
  The exclusion list version rides the per-row fingerprint so retuning
  it re-extracts exactly once. Diff text is capped per commit (64 KB,
  head+tail) with the cap in the fingerprint.
- **Primary-checkout resolution.** The rebuilder resolves the main
  worktree via the common git dir and refuses to build from a worktree —
  worktrees are behind main by construction, and an index built from a
  behind tree would be silently wrong. Same spirit as
  worktree_fresh_trample_guard.
- **Stamp discipline.** The read path is stamp-keyed like the script
  leg; the SCRIPT_EMBEDDER selector is honored, and a gemma-stamped
  index refuses a granite-fallback rebuild unless the env names granite
  explicitly — the top-level demotion guard pattern, landed after the
  2026-10-08 silent un-migration. Final default embedder is decided by
  the S4 bank, not assumed.

Slices (order matters; each independently landable):

- **S1 — extraction + reconciliation core.** New rebuilder
  helpers/maintenance/rebuild_git_search.py: primary-checkout
  resolution, `git rev-list main` walk, per-commit row (sha, author
  date, subject, body, file paths, ins/del), noise-excluded + capped
  diff text via --no-textconv extraction, fingerprint, FTS5 lexical
  index, tombstone-on-dropout, incremental by default, `--check`
  content-verdict without embedding (mtime is a carry hint only — the
  2026-08-30 worktree mtime lesson). Hermetic tests against a fixture
  repo built in a temp dir, including the amend-rewrite case that proves
  tombstoning.
- **S2 — vector leg + query CLI.** helpers/misc/git_query.py: hybrid
  bm25+vector+RRF over the sidecar, script_query CLI conventions
  (--limit, --kind-style scope flags: --msg/--diff/--paths, --since),
  identifier-query router, stamp-keyed degradation note, hit rendering
  as sha + date + subject + files with the `git show <sha>` pointer.
- **S3 — federation + freshness wiring.** master_query.py gains the git
  leg: `_LEGS` entry, `DEFAULT_LEGS` grows six → seven,
  `_SIDECAR_BY_LEG` → memory/git_search.db, `_REFRESH_BY_LEG` → the
  search-fresh APPLY, age-guard scope (24 h default). Makefile:
  rebuilder joins the search-fresh loop; a git-fresh convenience target
  mirrors convo-fresh. doc/procedures/search.md surface section +
  AGENTS.md search-surface table row (doc edits ⇒ search-fresh APPLY).
  TUI lanes stay at 8 — the federated leg covers it (non-goal
  otherwise).
- **S4 — acceptance bank + embedder verdict.** Pre-registered bank
  (~30 queries with expected-SHA ground truth drawn from operator-known
  arcs; archived beside the existing eval banks), arms: bm25-only,
  granite-384, gemma-512 on scratch indexes; pre-registered floors;
  verdict flips the S2 default; cutover measured (cache-hit share, wall
  time). Bank result recorded back into this proposal's §2 table.
- **S5 — deep-body section vectors (amendment 2026-10-17, operator
  direction; see §8).** The S4 bank's two misses are both deep-body
  queries: mechanism text past the 400-char embed basis is invisible to
  the vector leg. Notes solved this exact failure with per-section
  vectors (note_section_search #209). S5 ports that pattern: chunk the
  message body past the head into ~600-char sections, embed each, store
  as (sha, chunk_idx) rows beside the head vector, and score a commit's
  vector leg as max(head cosine, best-section cosine). Operator framing
  (2026-10-17): "the message body part is the most valuable to us esp
  when looking back at feature history."

## 4. Acceptance criteria & shakedown

1. Regression pin: the query "_bili_namespace routing namespace quirk"
   over the live index returns 38da7e313 in the top 3.
2. Idempotence: a second APPLY with no new commits re-embeds zero rows
   (414/414 cache hits), tombstones nothing, and finishes in walk+seconds.
3. Rewrite shakedown: amend a commit in a scratch clone → old SHA
   tombstoned, new SHA indexed, zero stale rows after one APPLY.
4. Worktree shakedown: invoking the rebuilder from a worktree either
   resolves the primary checkout or refuses with the named guard —
   never silently builds from a behind tree.
5. Freshness: land a new commit → `make search-fresh` (check mode)
   exits 1 naming the git leg; APPLY=1 heals; second check run rc=0.
6. S5 (pre-registered): both S4 deep-body misses recover into the top 3
   under section vectors, and none of the 14 currently-passing queries
   regress below their S4 rank. Extended bank gains deep-body questions
   drawn from real feature arcs before grading.

## 5. Risks

- **History rewrite churn (stg is the daily driver)** — SHA-set
  reconciliation with no cursor state; embed spend is bounded to
  genuinely new SHAs; tombstones keep the index exact under
  amend/fold/rebase.
- **Silent embedder fallback** — the 2026-10-08 un-migration showed a
  sidecar-down APPLY can rebuild a gemma-stamped index on granite
  quietly. The stamp-refusing guard ships in S2, not later; the S4 bank
  gate is the tripwire.
- **Diff-text bloat drowning the lexical leg** — measured exclude list
  (−55% patch text) + 64 KB per-commit cap, both fingerprint-versioned.
- **parquet textconv engaging during extraction** — extraction pins
  --no-textconv; the S1 test suite asserts a parquet-touching commit
  extracts in bounded time (the >7 min trap is one flag away).
- **Shared memory/ sidecar contention** — sqlite WAL + busy_timeout per
  the registered sidecar estate doctrine; the index joins
  store-sweep/maint via the existing registry if it qualifies.
- **Gate posture** — make qa stays green (targeted tests only for fixes,
  per gate etiquette). Eval-gate bullet: N/A with reason — this arc
  writes its own sidecar and reads git only; it touches no roster,
  crosswalk, hierarchy, or extractor, so no ontology answer can move
  (same N/A reasoning as the bili lane).

## 6. Non-goals

Branches, worktrees and stgit patch refs as index sources (operator
ruling 2026-10-17). Pickaxe/-G: not a replacement target NOR a fallback
— operator experience is that it was unusable even at much smaller
history sizes, so this index is the only viable mechanism for the class
of question, full stop. Blame or
per-file history views. A ninth TUI lane. Remote/GitHub sync. Snapshot
backups of the sidecar (derived state — rebuild restores). Index-time
comprehension of merge diffs beyond first-parent message+stat rows.

## 7. Amendment 2026-10-17 — S1–S3 implemented; embed-basis discovery; guard interactions

S1–S3 landed in one session (16 tests, live index built, federated leg
smoked). S4's role changed by operator ruling BEFORE the run: **granite
is the default embedder** ("assume we are using granite here") — commit
messages are short prose, granite's home turf, no sidecar dependency; the
bank below VALIDATES the choice instead of deciding it.

### 7.1 The embed basis is the message HEAD, not the whole message

First live build (414 commits, granite in-process, 2.5 embeds/s, ~2.7
min) ranked junk above the obviously-right commit for prose queries. The
probe: granite's mean pooling dilutes monotonically with message length —
the on-topic bili message scored **0.47 cos at its full 3,575 chars,
0.59 at 800, 0.72 at 350**, while generic one-word messages ("Improvements")
sit at 0.656. Short texts crowd the centroid; only a concentrated basis
lifts specific messages above them. So the vector leg embeds
`message[:400]` (EMBED_BASIS_CAP), version-stamped (EMBED_BASIS_VERSION
= 2, v1 = the full-message generation) and the corpus re-embedded
(360 unique texts, 10.1/s with the granite sidecar up vs 2.5/s
in-process; 54 texts were already ≤400 chars and cache-hit unchanged).
Two guard rules came out of the repair:

- An UNSTAMPED basis counts as stale (the v1 generation had no stamp) —
  absence forces a whole-corpus pass before the stamp is rewritten.
- The basis stamp is only written when the pass covered every reachable
  row; a partial pass under a moved basis must not brand v1 vectors as
  v2 (hit live: a partial apply stamped v2 over 414 full-message rows,
  and the upgrade could then never fire — repaired by deleting the stamp
  and re-running).

### 7.2 Identifier-shaped queries route lexical-only

`_bili_namespace` returned the RIGHT BM25 page but junk cosine extras
outranked it (0.032 vs 0.017 RRF — bare-identifier queries embed poorly
and short-message vectors crowd the centroid). The script leg's
`is_identifier_query` router now gates the git leg too: identifier-shaped
→ BM25-only. Its live trip: after routing, the origin commit ranks in the
top 2 (rank 1 is a later doc commit whose DIFF quotes the literal — the
diff-text leg earning its keep).

### 7.3 The shared APPLY guard is opted OUT for this index

`stamp_table=None`: the worktree trample guard refuses behind/diverged
HEADs on the theory that the delta would rewrite shared content backward —
unsound for a HISTORY index, whose corpus IS the reachable set of main
and which absorbed three live rewrites on day one (the bili commit was
38da7e313 at filing time, 934bc6bd9 by evening). Worktree tramples stay
impossible via the primary-checkout refusal; the mtime age-guard still
applies.

### 7.4 Acceptance bank (granite validation)

16 operator-style queries, graded by subject fragment (sha pins rot —
the rewrites above moved every expectation). Pre-registered floor:
adopt the granite hybrid default at top-3 ≥ 80%.

| Arm | top-1 | top-3 | ms/query |
|---|---|---|---|
| granite hybrid default (live index) | 13/16 | **14/16 (87.5%)** | 99 |
| floor | — | ≥ 80% | — |

Federation smoke: master_query fans the git leg (grouped results, index
ages bar shows `git 0h00m`); identifier and prose shapes both answer.
The two bank misses are the deep-body tier (mechanism language past the
400-char basis head) — the same tier the notes doctrine predicts needs
harder evaluation, parked with the bank rather than tuned ad hoc.

Slices landed: helpers/maintenance/rebuild_git_search.py (rebuilder +
query core), helpers/misc/git_query.py, the search_tui `git` lane
backend (master_query only — TUI lanes stay 8), master_query seventh leg
(`DEFAULT_LEGS` six → seven, sidecar + refresh maps), Makefile
search-fresh loop + git-fresh, doc/procedures/search.md section, AGENTS.md
surface rows. Tests: tests/test_git_search_index.py (16: reconciliation,
amend-tombstone, excludes, cap, check-without-writes, basis upgrade +
stamp honesty, worktree refusal, model-stamp refusal, merge rows,
filters, ready/stale). One late find, caught live: the shared CLI maps
`--check` to `incremental=False`, so a mode-gated reconcile reported the
whole corpus drifted on every check (414 new + 414 deleted phantoms);
drift is a sha/fp-set property and must be computed mode-independently —
`incremental` scopes only the embed targets.

## 8. Amendment 2026-10-17b — deep-body coverage (S5), the notes playbook

Operator direction after reviewing the S4 bank: the commit-message BODY
carries the feature-history rationale and is the most valuable part for
lookback; the 400-char basis blindspot is not acceptable to park. The
notes surface hit this identical wall and solved it with measured work
worth porting:

- **note_section_search (#209, 2026-09-06)** — the token-cap measurement
  showed the cosine leg saw only the head of each note (79% truncated,
  39% token-mass). Fix: one row per H2 section, each embedded within the
  productive window, search dedups to the winning section. Eval held at
  1.00; row growth 11.7× was absorbed; embed_store's content-hash keys
  gave fresh cache entries for section texts with no invalidation.
- **notes query-side levers trial (2026-10-08)** — lexical-leg
  enrichment (+10) helped; structured context in the VECTOR text hurt
  every arm. Doctrine carries over: git section chunks stay pure message
  prose (sha + files already live in the lexical leg's fields).
- **notes gemma adoption (#369, 2026-10-09)** — notes ultimately moved
  its section vectors to gemma (fourth gemma surface; npz seeder,
  gemma-first resolver, stamp guard, bank 41/75 vs floor 39).

Design (S5):

1. **Chunking**: after the 400-char head, split the remainder into
   ~600-char sections at paragraph/line boundaries (no overlap);
   messages ≤ 400 chars gain no chunks. Census: median body ≈ 1.2 KB →
   ~1,200–1,500 chunk vectors corpus-wide — trivial one-time cost,
   incremental applies touch only rewritten commits' chunks.
2. **Schema**: `git_search_body(sha, chunk_idx, embedding BLOB,
   chunk_fp TEXT)` beside `git_search`; `git_search_meta.fp` already
   covers the full message, so touched commits re-chunk naturally and
   `cached_embed_batch`'s content-hash keys re-absorb unchanged chunk
   texts across the operator's rewrites (the bank-week flux cost ~zero
   re-embeds).
3. **Scoring**: per-sha vector score = max(head cosine, best-section
   cosine), feeding the existing single vector leg + RRF; body-only hits
   render with a `(body)` marker in git_query output.
4. **Basis stamps**: the HEAD basis is untouched (stays v2 — the head text
   did not change, so no head re-embed); chunk rows carry their own stamp
   row (`body_basis`), absent = stale (pre-S5 sidecar chunks everything
   exactly once), written only on full chunk-target coverage.

The embedder axis (operator question: "does this mean that we move to
gemma whose vector was far stronger"): **no — not by itself.** The two
misses are a COVERAGE failure (the text is absent from the embed basis;
no embedder can match what it never receives — gemma at 400 chars misses
them identically), not a vector-quality failure. Gemma's measured
long-context behavior does not rescue whole-doc embedding either
(24k-window re-test bought one recovery; granite@8000 identical ranks).
But the notes precedent makes gemma a live SECOND axis: once sections
exist, a gemma-512 sectioned arm grades against the granite default on
the extended bank, and adoption follows the #369 recipe (sidecar is up,
guard is generic). Model stays a bank verdict, not an assumption.

Non-goals unchanged (main-only, no diff-text vectors, no TUI lane); the
only new surface is the chunk table inside the existing sidecar.

### 8.1 S5 executed same day — results and two fusion findings

Implemented as designed above (chunker `_body_chunks`: 600-cap greedy
line-boundary packing, 80-char tail floor; `git_search_body` table;
per-sha max(head, best-chunk) scoring with a `(body)` provenance marker
in git_query; independent `body_basis` stamp). Live cutover: 1,193
chunk rows over 355 commits (59 messages have no chunkable tail), one
apply at 11.3 embeds/s (granite sidecar), heads untouched, `--check`
FRESH; the same apply absorbed two more operator history rewrites
mid-arc. Tests: 28 (was 16) — chunker, stamps, deep-body vector leg,
idempotence incl. chunks, amend re-chunk, drop parity, absent-stamp full
pass, rare-literal router, extra-fusion invariant.

The bank run then exposed TWO structural fusion defects, both fixed in
`search_commits`/`_fuse_scores` with the evidence below (not knob
tuning — each fix states its invariant and carries a pinning test):

1. **Synthetic bm25 positions for cosine extras.** Union candidates
   outside the BM25 page were handed fabricated second-leg positions, so
   generic body chunks (0.60–0.66 cosine for ANY prose — the §7.1
   centroid crowding, now at chunk scale) outranked the lexical truth:
   `SessionWriter flush writes the touched buffer alone` — bm25 ranked
   the bili commit FIRST at -17.1 vs -9.6 runner-up ("touched buffer"
   and "SessionWriter" exist in exactly that one commit), yet fused ~6th.
   Rule now: an extra fuses on its cosine term ALONE unless its cosine
   OUTLIES the page (beats the best cosine among lexical candidates) —
   outlying extras earn the synthetic leg (live rescue: person-resolver
   query, extra cos 0.785 vs page max 0.673, rank 4 → 2), band-generic
   extras never displace two-leg page evidence.
2. **Rare-literal routing.** The identifier router's rationale made
   symmetric: a ≥4-char query term matching ≤3 commits is near-unique
   lexical evidence, and the query routes lexical-only (measured:
   SessionWriter → 2 rows, bili → 2). Common-word queries stay hybrid.

**Extended bank (24 queries = 16 S4 + 8 deep-body mined from real
long-commit mechanisms past ~char 1500), pre-registered §4.6 criteria:**

| Arm | top-1 | top-3 | ms/query |
|---|---|---|---|
| granite hybrid default (live, S5) | 18/24 | **24/24** | 272 |
| gemma-512 sectioned (scratch, S5) | **20/24** | **24/24** | 164 |

Both S4 misses recovered (SessionWriter → top-1 via rare-literal route;
harvest-fresh → top-3, behind the genuinely co-on-topic convo-search
commit whose body shares the harvest_conversations mechanism). None of
the 14 S4 passers left top-3 (person-resolver dipped to rank 4 under the
first fix draft and was rescued by the outlier-earns-leg rule; it holds
top-1 under granite, top-3 under gemma). All 8 deep-body queries answer.

**Gemma verdict: HOLD granite as default; recipe on record.** The gemma
sectioned arm's +2 top-1s sit entirely in the deep-body tier (3 deep-body
top-1s granite only reaches at rank 2–3; granite's lone top-1 gemma
misses is the person-resolver query). Both arms are 24/24 top-3, so the
surface's acceptance bar is met either way; a model swap costs the
in-process default (operator ruling 2026-10-17), a sidecar dependency at
query time, and a ~27-min full re-embed — too much for +2 on 24. The
§369 recipe (gemma-first resolver, stamp guard, npz-style seed) remains
one slice of wiring if the deep-body tier grows and the gap widens.
Bank scripts + outputs: `bench_data/git_search/` (durable archive with
README: bank script, both arm outputs, gemma build script; originally
$TMPDIR session artifacts).

### 8.2 Gemma adoption EXECUTED same evening (operator ruling)

The §8.1 HOLD was superseded within hours: the operator noted the box's
standing posture — two shared llama-servers (gemma :8732, granite :8733)
already run, and every surface uses the served models — voiding the
query-time-sidecar "cost 1", and directed the move given the deep-body
headroom. Landed:

- `resolve_git_embedder` is GEMMA-FIRST (`GIT_EMBEDDER=auto|gemma|
  granite`; auto = gemma when the sidecar is up, granite in-process
  fallback, pseudo last). Per-model index-side basis via the builder's
  own `_head_embed_text`/`_chunk_embed_text` (gemma carries the
  title/subject prefix contract, byte-parity with the §8.1 bank arm;
  granite/pseudo stay raw). Query side embeds gemma with task="search".
- Guard wiring: `guard_gemma_stamp` refuses fallback-model rebuilds of
  a gemma-stamped index (sidecar-down APPLY aborts — the 2026-10-08
  class), and a model change on an EXISTING index refuses outright in
  both directions (no incremental re-tagging: cutover and un-migration
  both go through delete-the-sidecar + rebuild; derived state, warm
  cache).
- **embed-gc gap fixed en route** (operator report: "embed-gc reports
  git as unverified"): the git cohort had no `Ref` in
  `gc_embed_cache.DEFAULT_REFS`, so its cache rows could never be
  proven live. The Ref unions heads + chunks (chunk_idx −1 marks heads)
  and rebuilds the embed text through the git walk + the builder's own
  helpers (recipe cannot drift from builder). Post-cutover report: git
  3,553 rows — 1,596 referenced (gemma), 1,956 retained (granite-era
  rollback insurance), 1 dead.
- Cutover: sidecar deleted, one rebuild (1,607 embeds @1.6/s ≈ 17 min),
  stamp `embeddinggemma-2-q8_512` / 512-d / body_basis 1, `--check`
  FRESH.
- **Standing tripwire**: `helpers/bench/git_eval_bank.py` — the 24-query
  bank through the production path, floor top-3 ≥ 22/24, drift-skips
  queries whose fragment left the index. Live run post-cutover:
  **top1 19/24, top3 24/24 @170 ms/query** (the one top-1 delta vs the
  scratch arm is a near-tie shuffle; and a same-day wrinkle the bank
  design expects: the proposal's own §8.1 text quoting the SessionWriter
  phrase landed as a commit mid-arc and now matches that query verbatim
  lexically — the §7.2 quoting-commit pattern, not a defect).
- Tests 32 (was 28): resolver preference order + env forcing, per-model
  basis shape, demotion-guard + mix-refusal paths (explicit un-migration
  still requires delete-and-rebuild).

| Projected outcome | Today | After |
|---|---|---|
| commit-history recall | 0 (no leg) | S4 bank floor (pre-registered) |
| federated legs | 6 | 7 |
| cost of a "which commit touched X" question | full-history sweep: 6 s raw, >7 min via textconv | ms-class indexed query |
| first embed cost | — | 414 messages ≈ 4–10 min, one-time |

(The table above was the filing-time projection; measured outcomes are
§7.4 and §8.1.)

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-17 | git rev-list --count main | 414 | 6 merges; span 2025-03-09..2026-10-17 |
| 2026-10-17 | log --format per-record parse | 739,553 chars; median 1,568; p90 3,504; max 8,196 | message corpus |
| 2026-10-17 | log --numstat --no-textconv | files 18,103; ins 1,581,884; del 622,247; binary 1,514 | 3.9 s |
| 2026-10-17 | log -p --no-textconv | 165,575,668 bytes | 6.2 s |
| 2026-10-17 | log -p --no-textconv + exclude set | 164,926,624 bytes (−0.4%) | snapshots-only excludes are NOT the win |
| 2026-10-17 | per-class log -p | static 53.8 MB; findata 42.2; venv 37.3; doc 13.9; helpers 8.4; tests 6.5; snapshots 0.4 | exclude list derived here |
| 2026-10-17 | final exclude set | 74,470,975 bytes (−55%) | venv+static+snapshots+parquets+locks+ggufs+vendor+bench_data |
| 2026-10-17 | git rev-list --count main -- snapshots | 83 (20%) | snapshot churn breadth |
| 2026-10-17 | git log -p (textconv on) | >7 min, killed | parquet_textconv decodes per blob per side — the pickaxe trap |
| 2026-10-17 | live build v1 (full-message basis) | 414 rows, 2.5 embeds/s, ~2.7 min (in-process) | junk outranked on-topic prose — basis probe next |
| 2026-10-17 | basis dilution probe | 0.72 @350c / 0.59 @800 / 0.47 @3575c vs junk 0.656 | granite mean-pool dilution → EMBED_BASIS_CAP 400 |
| 2026-10-17 | live rebuild v2 (head basis) | 360 unique @10.1/s (sidecar up), 54 cache hits | sidecar 4x the in-process rate |
| 2026-10-17 | acceptance bank | top1 13/16, top3 14/16, 99 ms/q | floor top-3 ≥80% met — granite hybrid confirmed |
| 2026-10-17 | S5 cutover (body chunks) | 1,193 chunks / 355 shas, one apply @11.3/s, heads untouched | deep-body leg live; 2 operator rewrites absorbed same apply |
| 2026-10-17 | fusion shakedown (S5 bank) | SessionWriter bm25 -17.1 vs -9.6 yet fused ~6th | synthetic extra positions + missing rare-literal route — both fixed, pinned by tests |
| 2026-10-17 | extended bank (24 q) | granite 18/24 top1, 24/24 top3 @272 ms; gemma 20/24, 24/24 @164 ms | §4.6 criteria met; gemma HELD (deep-body edge +2 too thin for sidecar dependency) |
| 2026-10-17 evening | GEMMA ADOPTION (§8.2) | operator: shared llm servers void the dependency cost | sidecar deleted, rebuilt @1.6/s ≈17 min, stamp embeddinggemma-2-q8_512 |
| 2026-10-17 evening | live bank gate post-cutover | top1 19/24, top3 24/24 @170 ms/q | floor ≥22/24 met; helpers/bench/git_eval_bank.py = standing tripwire |
| 2026-10-17 evening | embed-gc git cohort | 1,596 referenced (gemma), 1,956 retained (granite rollback), 1 dead | git Ref added — was "unverified" (operator report) |
