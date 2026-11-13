# Gate arc protocol

An arc = preflight once → baseline gates once → targeted fixes → close-out.
Full gates run ONCE per arc, on the user's explicit go — never a second full
run to "confirm green".

**One run. Not two. Not "just to check it's green now."** This is the
single most-violated rule in this file and the most expensive: each full
battery is ~15 min of a 4-core box (and far longer on a loaded one), and a
second run re-pays it to learn what the targeted legs already told you.
The failure mode is always the same — a baseline red gets fixed, the agent
feels the tree is now different from the one that was measured, and reaches
for `make qa` again. Don't. Validate the exact fixed nodes and the
individual leg target (Phase 2), then trust the baseline plus those
targeted results; the trailer reports BOTH, honestly, and that is a
complete gate record. If a second full run genuinely seems necessary, that
is an operator decision to ASK about, not to take. (2026-11-09: a second
`make qa` was started and aborted — the operator's reply was "you cant
keep rerunning the gates. Read the procedure".)

Two dependencies:
`doc/procedures/commit-messages.md` (messages, trailer grammar) and
`doc/local/engineering/split_patch.md` (collector, push-down machinery).
Set by the operator 2026-10-02 ("our gates run arc is very problematic").

## Phase 0 — preflight (once, arc start)

1. `make search-fresh APPLY=1` + `make convo-fresh APPLY=1` — the ONLY APPLY
   this arc; never re-APPLY mid-arc. Check-mode reds on these advisory legs
   afterwards are expected noise, not failures — say so in the trailer.
   **Pass `ARGS="--allow-stale"` whenever the arc's HEAD has moved off the
   shared index's stamp** — which is the normal case, since the stamp is
   written on main and this tree carries unlanded patches. Without the flag
   the rebuild REFUSES (`worktree_fresh_trample_guard`: "this tree has
   DIVERGED from it … nothing was written") and the arc starts with a stale
   index nobody can fix later, because mid-arc re-APPLY is forbidden. Same
   for any per-index rebuild invoked from the fix loop
   (`rebuild_doc_search.py --allow-stale`, etc.). The guard exists to stop
   an ACCIDENTAL trample, not a deliberate arc-start refresh — pass the flag
   on purpose, and note in the trailer which indexes were refreshed this
   way.
2. `make maint` — regenerate derived state (snapshot, DuckDB graph cache,
   centrality stamps, CSR substrate) so the baseline gates compare fresh
   state, not last arc's. Data-writing arcs (anything that touched
   `memory/research.db`) MUST run it; a pure-code arc that closed green may
   skip. Wired 2026-10-21 after the arc paid for its absence: qa
   snapshot-fresh + snapshot_check, four graph perf legs and
   csr-fresh-check all failed on stale derived state one maint run would
   have refreshed.
3. Quiet-box check before any timing runs: sibling sessions, desktop load
   (`witr`, load average). A loaded box inflates every leg uniformly —
   report the confound, don't record the numbers.
4. `stg series -id` — read the stack; know which patches are yours
   (commit-messages.md § Inspecting stack state).

## Phase 1 — baseline (once, on the user's go)

    make qa && make perf && make advisory

Advisory runs last: its informational legs never block. Read results via
`gate_query` (`gate_query failures`, `gate_query latest --gate <name>`),
not raw tails.

### The full gate surface (why some names are missing from the battery)

`outputs/gate_runs.duckdb` indexes SEVEN gate names — `qa`, `integration`,
`perf`, `advisory`, `maint`, `integrity`, `verify`. Only the first four are
Phase 1 targets; the other three are reporting surfaces the same scripts
already cover from inside the battery, so the arc does not run them twice:

| gate name | report | runs in the arc as |
|---|---|---|
| `qa` | `qa_report.md` | Phase 1 target |
| `integration` | `integration_report.md` | Phase 1 target (see the redundancy note below) |
| `perf` | `perf_report.md` | Phase 1 target |
| `advisory` | `advisory_report.md` | Phase 1 target |
| `maint` | `maint_report.md` | **Phase 0** `make maint` — steps + timings land in `maint_runs`/`maint_phases` |
| `integrity` | `database_integrity_report.md` | the `integrity_check` leg **inside qa** |
| `verify` | `verify_notes_report.md` | the `verify_notes` leg **inside qa** |

So `make integrity` / `make verify-notes` / `make maint` are not missing
gates — their content is already paid for. Do not add them to the battery;
do read their digests when diagnosing (`gate_query latest --gate maint`,
`--gate integrity`), because those runs carry phase-level timing
(`maint_query phases --cmd <cmd>`) that the qa legs collapse into one line.

Measured cost map (medians since 2026-11-01, `outputs/gate_runs.duckdb`
`runs.elapsed_s` / `legs.seconds`; live source is `gate_query timing --leg`
— re-read it, do not trust this table as current):

| gate | wall | who owns it |
|---|---|---|
| qa | ~348 s | `pytest` ~333 s (exclusive: runs alone after the short legs drain) |
| integration | ~141 s | one `pytest-integration` leg, 783 tests |
| perf | ~92 s | 31 benches, longest ~10 s (plus ~20 s of qa-leg preflight re-runs) |
| advisory | ~352 s | `live-invariants` ~352 s IS the wall; everything else packs under it |

Three levers, in descending payoff:

1. **integration is redundant work** — every file it runs (164) already
   ran in qa's `-m "not live"` leg in the same arc; the dedicated leg buys
   the isolated verbose report + its own `gate_query` digest, not extra
   coverage. Source the digest from qa's junit instead and the arc saves
   ~141 s without losing a single tested node.
2. **qa is one leg** — pytest alone; md-lint is a ~0.5 s cached run
   (its 52 s outliers are `npx` refetch on a cache miss, not corpus
   size). Optimize inside the suite, never by reordering the gate.
3. **advisory is one leg** — `live-invariants` at 249-266 tests with real
   per-file cost (`TestGraphEndpointsLive` alone ~252 s serial). Its wall
   tracks the slowest live test module; shrinking it means fewer live
   tests, an operator call, not a scheduling trick.

On 4 cores the gates must stay sequential — each saturates every core and
perf needs a quiet box — so the lever is scope, not concurrency: run only
the gates the arc's change class needs (the pure-code carve-out above),
and never re-run a gate to "confirm".

`make qa` included a **`mojo-parity`** leg (added 2026-10-14, **RETIRED
from qa 2026-10-26 — Mojo lane frozen** by operator ruling: nothing in
production consumes Mojo, so the leg was pure mirroring tax on a second
implementation with no consumers; `make mojo-parity` survives as a
manual drift check): the Mojo
`integrity_check` port diffed against the Python
`database_integrity_check.py` over 89 canonical keys. It is the only
drift detector between the two implementations, and it is why H10 exists —
it previously lived only in the `mojo-bench` perf harness, was never a gate,
and so never ran. Two practical consequences: the leg **needs the Mojo
toolchain and a build**, so a bare `make qa` on a machine without
`.venv/bin/mojo` fails there and nowhere else; and on red it prints the
mismatched keys with the full log path, which is the fastest route to the
offending site. Its analogue for perf regressions is `make mojo-bench`
(still a separate, non-blocking surface; also FROZEN 2026-10-26 —
manual runs only).

The qa battery also runs a **`group-growth-check`** leg (group_growth_gate,
2026-11-13): the group-discovery growth monitor in `--check` mode against
the tracked reference `helpers/maintenance/group_discovery_reference.json`
(post-v7, 657 components). It fails only on erosion of the curated-group
signal — family-hit loss, removed merge flags, a gone component hosting
curated family members, or an empty dyad set — and reports growth/new
components without failing. Intended family-structure changes (e.g. seed
edits) must move the reference deliberately in the same change
(`--refresh-reference`), the same doctrine as the ontology-questions
rebaseline.

qa also runs a **`family-label-coverage`** leg
(`family_label_coverage_check.py --check`, tata_seed_coverage_invariant,
2026-11-13): read-only filer-label maximality guard for complete families
(`COMPLETE_FAMILIES = {"Tata"}`, currently 238 members). Every `vigil:rpt:*`
label intersecting a complete family must be fully contained in its seed —
a PARTIAL label means a company carries the family's root token
(tata/taj/trent/titan/voltas) in ≥2 filer contexts but is missing from the
curated seed, i.e. a curation gap. Context-multi names without a root token
(suppliers, JV counters — HyperVault, Stryder Cycle, TP* Solar SPVs, …) are
advisory and never block. The deliberate path for a legitimate expansion is
to edit `family_seeds.json` in the same change; the gate then passes
(`family:Tata` went 230 → 238 this way and the leg is green).

qa also runs a **`schema-check`** leg — tracked `schema/` DDL vs the live
`memory/` DBs, exit 1 on drift, fix is `make schema-dump` — and advisory
runs four security/compliance legs: `secret-scan` (incremental git-history
scan), `cargo-audit` (SKIPs without the binary), `fuzz` (Hypothesis suites, deterministic
seed; contends with the pytest-scale legs, so advisory wall time grows),
`license-check` (AGPL + third-party inventory). Wired 2026-10-21;
`mojo-test` retired from the roster 2026-10-26 (Mojo freeze);
`analytics-fresh` was deliberately left out — it reads external harness
state and carries the same chronic-red risk class as convo-fresh.

`make integration` is NO LONGER a standing arc leg (dropped
2026-11-09, operator go). It was the battery's one piece of **redundant
execution**: all 783 `-m integration` tests also run inside qa's pytest
`-m "not live"`. Verified against `gate_runs.duckdb`: qa run 3520 ran
1,007 files / 5,838 tests, integration run 3529 ran 164 files / 783 tests,
and the set difference is EMPTY — neither a testcase nor a metadata
`node_id` in the integration run is absent from qa's. `-m "integration and
live"` collects zero tests. The leg cost ~141 s per arc and bought no
coverage.

Precedent: the *advisory* gate had the identical duplicate removed on
2026-08-31 (commit `d91e73c45`, "drop the duplicate integration run from
the advisory gate … ~94s to ~45s"; recorded in `tests/run_gate_report.py`
as a user directive). That commit also said integration "stays explicitly
runnable via `make integration`" — a manual target, which is what it is
again now.

What the arc gives up is a **name**, not a verdict: qa's pytest leg is one
leg, so an integration-test failure is indistinguishable from any other
suite failure in qa's digest, whereas `make integration` fails as its own
named gate with `-v` output and its own junit. When that distinction
matters, run `make integration` on demand — it is still a `GATES` entry,
still indexed, still reports its own digest.

Do not put it back in the Phase 1 line without measuring the overlap
again: if integration and qa ever diverge (a test that is `integration`
but `live`, say), the redundancy argument stops holding. See
`doc/improvements/proposals/gate_output_fidelity_and_integration_leg.md`
S2 for the decision record and S3 for the per-test output this depends
on.

Two watch legs (2026-11-08 gates-layer regression sweep, advisory-first
with promotion to hard-fail on operator go) guard the recurring
slowness class at the cycle that causes it:

- **`hermeticity-watch`** — the qa pytest suite records, per test, every
  connection or file open reaching into the live `memory/` stores
  (always-on recorder, `tests/hermeticity_watch.py`; sub-second
  overhead; `live`-marked tests and the `_tmp_hygiene` template donors
  are exempt by design). The leg fires (exit 1) when a NON-live test
  made a write-capable live-store connect — the exact opening move of
  the 404 MB live-DB clone (#478) and the 2.5 GB convo_search backup
  the maint-chain paid per run. Read-only touches report-only.
- **`gate-timing-watch`** — per-leg median of the last 5 gate runs vs
  the prior 10 from `outputs/gate_runs.duckdb`; fires above 1.5× AND
  +5 s so sub-second legs can't noise-fire. Turns the slow creep that
  previously surfaced weeks later via `gate_query` into an in-cycle
  advisory red. Investigate via `gate_query timing --leg <leg>`; the
  loaded-box confound (Phase 0) applies before recording numbers.

## Phase 2 — fix loop (targeted only)

- Before treating ANY red as live work, verify the report is CURRENT:
  `git merge-base --is-ancestor <report-commit> HEAD`. A gate report from
  a commit that is not an ancestor of HEAD describes history that was
  amended/rebased away — its failures may already be fixed (2026-10-30:
  qa run 2048's static_checks/pytest/schema-check reds were all fixed by
  an upstream commit; ~30 min of re-derivation skipped once the check
  was known). Stale report ≠ green tree: re-run the leg on HEAD after
  confirming.
- Diagnose with the indexes before grepping. A gate red is usually a
  question about history — did this leg fail in a past arc, is the behavior
  a recorded decision, what landed here and why — and the indexes answer
  that faster than re-derivation: `master_query` (all seven, federated),
  `doc_query` (design/decision history), `convo_query` (past sessions:
  dead ends, discarded options), `git_query` (what landed, when, why),
  `ripwire --for/--callers/--impact` (structure + blast radius), `rg` for
  literals. Plain `grep`/`find` are the fallback, never the opening move.
  State no claim about what the repo has or lacks until a search backs it.
- Pre-existing reds are THIS arc's work, not the next arc's. A red that
  predates the arc — pre-existing lint/type debt, a bug landed by an
  earlier arc, a leg nobody owns — is fixed in this arc's collector.
  The baseline already paid to discover it; deferring only makes the next
  arc pay again (2026-11-09 arc: two pre-existing Makefile rule defects
  from the co-supply commit and a PRE-alias-fold tracked snapshot, all
  fixed with the arc's own fallout — operator ruling "all of qa failures
  have to be fixed").
- NEVER `make pytest` / `make test` — the suite is ~5,800 tests. Fix, then
  validate the exact nodes:
  `.venv/bin/python3 -m pytest tests/test_x.py -k <name>`.
- Other red legs: re-run their individual make target only (lint, md-lint,
  types, deptry, static-checks, integrity, snapshot, …).
- Perf: TIMING failures are PARKED — list them in the trailer, never rerun
  perf for timing. A run the contention guard marks CONTENDED (it prints
  the load average and which rows are untrusted) is parked the same way:
  the numbers are not evidence, so rerunning on the same busy box only
  buys a second untrusted table. Non-timing perf failures (a crash or
  correctness assertion in a bench leg) are real fallout: fix, validate
  that specific bench only.
- `convo-fresh-check` is chronically RED by design and is PARKED, not
  chased: it compares the dsh tool index against the LIVE harness stores,
  which drift mid-session every session. That is indexer lag, not corpus
  drift — the remedy is the dsh indexer catching up, and no helper run
  converges it. Same treatment as the Phase 0 expected-noise advisory
  legs: name it in the trailer, move on. Never spend a fix loop on it.
- Cross-file regressions from fixes surface at the NEXT arc's baseline —
  accepted trade-off (operator ruling 2026-10-02: targeted-only).
- Arc classification, narrowed by the pre-existing-reds rule above: a
  pure-code arc does not re-stamp derived state merely because it is
  stale, BUT when a qa leg fails on it (`snapshot-fresh`,
  `snapshot_check`, `integrity_check`) that is a qa failure and gets
  fixed — `make snapshot` / `make maint` in the arc's collector, with the
  row deltas recorded in the message. A snapshot that fails because a
  later arc legitimately shrank the live DB (the 2026-11-09 #494 alias
  folds) is exactly this case: triage, re-capture, note the delta. Data
  arcs still regenerate snapshots under their own procedure.

## Phase 3 — close-out

1. Green-first (split_patch.md GATE-DONE rule): all gates green-or-parked
   BEFORE distributing. Fixes collect in the top `gate_fixes` collector
   (`stg new gate_fixes` — the one authorized `stg new`).
2. ASK the operator: distribute into owning patches, or keep the collector?
   Distribution is optional every arc.
3. If distributing — group fixes by OWNING patch, then route per patch:
   - **Fast path** — paths disjoint from every patch ABOVE P (probe with
     `stg files <each patch above P>`; disjoint means refresh -p cannot
     conflict): `stg refresh -p <P> --force -a "gate fix: <what/why>" -- <paths>`
   - **Fallback** — refresh -p conflicted (a `refresh-temp` patch is left
     behind): `stg undo` (one step undoes the merge, a second also drops
     the temp patch), then the goto -k round-trip per split_patch.md:
     `stg spill -r -- <paths>` / stash-aside / `stg goto -k <P>` /
     `stg refresh --force -- <paths>` / goto back.
   - **Straight to goto -k/spill** when paths are shared with patches above
     or a hunk-level split is needed (spill acts on the TOPMOST patch only).
   Mechanics for every route: new/ignored files `git add -f` FIRST (refresh
   skips untracked; the staged index is why --force); deletions UNSTAGED
   (`git reset HEAD -- <path>`) or they silently no-op through refresh;
   renames ride as D-side worktree dirt, never stashed. After EVERY
   refresh: marker-grep the pushed blob (`git show <P>:<path> | grep -c
   <marker>` — exit status is not proof) and `stg show <P> --stat`. Re-read
   `stg series -id` AFTER operations, never during (the refresh-temp marker
   burst is the operation's own log, not series state).
4. Backup export before ANY surgery: `stg export -d
   ~/Research/patches/work/<dated-dir>/` — /tmp dies on reboot.
5. Messages: every touched patch (collector included, if it stays) gets a
   `doc/templates/commit_message.md` seed via `stg edit -f <file> <patch>`
   — WHY-first body, one concrete number per bullet.
6. Trailer — two parts, so it never claims more than ran:
   `Gates: make qa 9/9, make perf 22/22 (2 timing
   parked), make advisory 9/11 (freshness expected-red); post-fix
   targeted: pytest tests/test_x.py::<id>`
   (`make integration` is no longer in the battery — cite it only if you
   ran it by hand, and then say "manual".)
7. Close the books: per-patch stat reconciliation (split_patch.md rule 10),
   disjointness zero, golden `git diff PRE..HEAD --stat` empty (PRE =
   pre-distribution tree, fixes already in it); delete the emptied
   collector and free the name.

## Hard lines

- No `stg new` except the collector; no `stg delete` without the step-0
  export; no `git commit`/`amend`/`rebase`; no full-suite runs mid-arc.
- The close-out distribution rides the arc's explicit go — AGENTS.md's
  "never `stg refresh` unprompted" still holds for everything outside this
  protocol.
