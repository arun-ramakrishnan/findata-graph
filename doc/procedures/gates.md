# Gate arc protocol

An arc = preflight once → baseline gates once → targeted fixes → close-out.
Full gates run ONCE per arc, on the user's explicit go — never a second full
run to "confirm green". Two dependencies:
`doc/procedures/commit-messages.md` (messages, trailer grammar) and
`doc/local/engineering/split_patch.md` (collector, push-down machinery).
Set by the operator 2026-10-02 ("our gates run arc is very problematic").

## Phase 0 — preflight (once, arc start)

1. `make search-fresh APPLY=1` + `make convo-fresh APPLY=1` — the ONLY APPLY
   this arc; never re-APPLY mid-arc. Check-mode reds on these advisory legs
   afterwards are expected noise, not failures — say so in the trailer.
2. Quiet-box check before any timing runs: sibling sessions, desktop load
   (`witr`, load average). A loaded box inflates every leg uniformly —
   report the confound, don't record the numbers.
3. `stg series -id` — read the stack; know which patches are yours
   (commit-messages.md § Inspecting stack state).

## Phase 1 — baseline (once, on the user's go)

    make qa && make integration && make perf && make advisory

Advisory runs last: its informational legs never block. Read results via
`gate_query` (`gate_query failures`, `gate_query latest --gate <name>`),
not raw tails.

`make integration` is a standing arc leg. Its 551 `-m integration` tests
also run inside qa's pytest `-m "not live"` (`tests/run_gate_report.py`
keeps the overlap deliberately); the dedicated leg buys the isolated
verbose report and its own gate_query digest.

## Phase 2 — fix loop (targeted only)

- NEVER `make pytest` / `make test` — the suite is ~3,941 tests. Fix, then
  validate the exact nodes:
  `.venv/bin/python3 -m pytest tests/test_x.py -k <name>`.
- Other red legs: re-run their individual make target only (lint, md-lint,
  types, deptry, static-checks, integrity, snapshot, …).
- Perf: TIMING failures are PARKED — list them in the trailer, never rerun
  perf for timing. Non-timing perf failures (a crash or correctness
  assertion in a bench leg) are real fallout: fix, validate that specific
  bench only.
- Cross-file regressions from fixes surface at the NEXT arc's baseline —
  accepted trade-off (operator ruling 2026-10-02: targeted-only).
- Arc classification: a pure-code arc treats snapshot/integrity drift as
  triage-then-report, not re-stamp; data arcs regenerate snapshots under
  their own procedure.

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
   `Gates: make qa 9/9, make integration 1/1, make perf 22/22 (2 timing
   parked), make advisory 9/11 (freshness expected-red); post-fix
   targeted: pytest tests/test_x.py::<id>`
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
