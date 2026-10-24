# Tab-free Makefile: use '>' as the recipe prefix (GNU Make).
.RECIPEPREFIX := >

# types-tests diagnostic format: full (pretty, ~10-14 lines/diagnostic —
# truncates the 60-line gate-report tail past ~5 diagnostics) for direct
# runs; the advisory gate overrides to concise (1 line/diagnostic) so EVERY
# warning lands in the report log (2026-08-26 logging fix).
TYPES_TESTS_FMT ?= full

# Gate parallelism (2026-08-25): advisory runs its steps concurrently
# (DuckDB-reading steps — graph-algos, suggest-relations — connect
# read_only=True so N cross-process readers coexist with any writer;
# verified against a live RW lock holder 2026-08-25. Concurrent pytest
# steps get per-step .pytest_cache/<label> dirs). qa stays
# sequential by default to keep make's abort-at-first-failure semantics;
# override with `make qa QA_JOBS=4`.
GATE_JOBS ?= 4
QA_JOBS ?= 1

# Prefer the project venv if present (so `python3` / `pytest` resolve to
# .venv/bin without needing `source .venv/bin/activate` in the parent shell).
# PREPEND .venv/bin so it takes precedence over the system python3 (which
# lacks duckdb/pytest/etc.). Harmless if .venv doesn't exist — the entry
# is just a no-op directory on PATH and lookup falls through to the system.
export PATH := $(CURDIR)/.venv/bin:$(PATH)

# Scratch on disk, not an unmanaged default temp dir (gate_wall_time_reclaim
# 2026-10-01): pytest basetemp, bench scratch and db-backup staging follow
# TMPDIR, and an unset TMPDIR would dump multi-hundred-MB xdist temp projects
# into the OS temp path with no project-specific hygiene sweep.
# Default to the operator's disk scratch mount when it exists;
# an explicit TMPDIR always wins. tmp_sweep reaps via tempfile.gettempdir(),
# which follows this variable.
export TMPDIR ?= $(shell test -d /mnt/data/tmp && { mkdir -p /mnt/data/tmp/findata; echo /mnt/data/tmp/findata; } || echo /tmp)


.PHONY: help qa test live-invariants perf cover fuzz integration snapshot snapshot-check snapshot-restore sync-tags sync-coverage-tags sync-sector-links static-checks license-check tmp-sweep install-dev triage-quotes gate-fresh graph-smoke graph-stats graph-algos graph-rebuild graph-rebuild-bench update-extensions recompute-graph recompute-hyper search-fresh convo-fresh embed-gc search-tui tier-nudge tier-watch derive-relations derive-co-mentions derive-org-membership derive-event-participants derive-merger-hyperedges dup-census person-census derive-themes derive-events derive-insights derive-indices quote-coverage derive-themes-rebuild derive-cited-in derive-cited-in-rebuild derive-hyperedges derive-all refresh-indices refresh-vigil refresh-shp frontend frontend-check fold-identifiers format maint maint-full md-lint metrics-rebuild mojo-bench mojo-parity mojo-build mojo-test mojo-format relations-enrich lint types types-tests lint-audit deptry advisory ocr-version-check onager-build secret-scan cargo-audit script-search-rebuild memory-search-rebuild triage-relations teleocr-server embgemma-server granite-server llamacpp-build llamacpp-health quack-start quack-stop quack-status quack-restart quack-health csr csr-check csr-rebuild convo-search-rebuild convo-search-check analytics-parquet analytics-fresh schema-check schema-dump bili-status bili-monitor bili-stop-monitor bili-report bili-snap bili-watch-event bili-thresh-replay

help:           ## Show available targets (alphabetical; entries generated from the ## annotations — keep both in sync)
> @echo "FinData targets (alphabetical):"
> @echo "  advisory                 Run advisory (non-gating) checks in PARALLEL (default 4 jobs; override: make advisory -j N): ty on tests, live invariants, frontend, graph algos, analytics, suggestions, doc/script/note/memory-search freshness checks, lint-audit (appends outputs/advisory_report.md)"
> @echo "  analytics                Read-only analytics over the git-tracked Parquet snapshot (A3; arg = report name)"
> @echo "  analytics-fresh          Analytics stores (agent_traces + model_usage) vs harness frontiers — read-only drift report, exit 1 when a completed day is missing; APPLY=1 runs incremental load all on both loaders"
> @echo "  analytics-parquet        Ad-hoc read-only SQL over the Parquet snapshot (--list/--stats/--query; tables are <side>__<name>)"
> @echo "  bili-monitor             Background proxy monitor (read-only): DURATION_S=43200 POLL_S=60 SNAP_S=300"
> @echo "  bili-report             Proxy economics report from the watcher state dir (read-only): REPORT_ARGS=\"<session ids>\""
> @echo "  bili-snap               Snapshot the proxy state now (read-only): SNAP_ARGS=\"<session ids>\""
> @echo "  bili-status             Proxy status report (read-only): effective bars, injections, folds, burn, cache"
> @echo "  bili-stop-monitor       Stop the background proxy monitor (never \`pkill -f\` -- it kills the caller)"
> @echo "  bili-thresh-replay      Replay the tier fire curve offline (read-only): REPLAY_ARGS=\"<floor> <thresholds...>\""
> @echo "  bili-watch-event        Block until the next T1 fire or fold for a session (read-only): EVENT_ARGS=\"<session> [either|t1|fold] [max_wait_s]\""
> @echo "  cargo-audit              RustSec scan of desktop/src-tauri/Cargo.lock (advisory; SKIPs without cargo-audit)"
> @echo "  convo-fresh              Check conversation corpus+index freshness — harvest sources vs parquet corpus vs pointer index (exit 1 on drift; APPLY=1 harvests+rebuilds; also run by make advisory)"
> @echo "  convo-search-check        Report convo_search.duckdb file size vs payload (advisory; swap-rebuild candidate)"
> @echo "  convo-search-rebuild      Rebuild convo_search.duckdb via atomic pid-tagged temp + os.replace swap (compacts; graph-lane pattern)"
> @echo "  cover                    Run all tests with coverage over helpers/ (branch + missing-line report)"
> @echo "  csr                      Build the CSR substrate (memory/csr/; shortest-path fast lane)"
> @echo "  csr-check                Check CSR substrate freshness (exit 1 if stale; also run by make advisory)"
> @echo "  csr-rebuild              Rebuild the CSR substrate regardless of freshness"
> @echo "  decisions-preannotate    Pre-annotate review surfaces with carrier verdicts (advisory; SURFACE=all|relations|quotes|coverage|findings|ocr|markdown-parse)"
> @echo "  deptry                   Run deptry dependency-health scan (unused/undeclared/transitive deps)"
> @echo "  derive-all               READ-ONLY preview of every derive-* step (dry-runs; nothing written, sidecar suppressed)"
> @echo "  derive-cited-in          Derive cited_in (note -> edition) edges from OKF sources[] frontmatter (okf_activation P)"
> @echo "  derive-cited-in-rebuild  derive-cited-in + graph-rebuild — the paired run cited_in requires (writes entities+edges, then rebuilds the DuckDB cache to match)"
> @echo "  derive-co-mentions       Derive co_mentioned_in edges from newsletter enhancement blocks"
> @echo "  derive-countries          Derive listed_in (company -> country) edges from exchange tickers (country layer C1)"
> @echo "  derive-event-participants Materialise the event_participants n-ary store (person_ontology_spine S4): dry-run default, APPLY=1 writes"
> @echo "  derive-events            Promote relation edges + extract guidance/management events into the events timeline table"
> @echo "  derive-hyperedges        Regroup membership dyads into hyper_edges/hyper_incidences (star incidence store)"
> @echo "  derive-indices           Derive index entities + listed_on_index edges from NSE constituents (Wave 1; pair with graph-rebuild)"
> @echo "  derive-insights          DRY-RUN stale-only preview of quotes/company_metrics + auto '## The Chatter' blocks (writes nothing; apply yourself — see comment above)"
> @echo "  derive-merger-hyperedges Derive merger/demerger hyperedges from the triage decisions journal (merger_demerger_hyperedges S2); APPLY=1 writes"
> @echo "  derive-org-membership    Derive org_membership hyperedges from person-to-company invested_in (person_ontology_spine S3); APPLY=1 writes"
> @echo "  derive-relations         Extract jv_with/acquired/subsidiary_of/same_group/supplier_to/customer_of edges from newsletter prose"
> @echo "  derive-themes            Derive exposed_to (company -> theme) edges from company-note prose"
> @echo "  derive-themes-rebuild    derive-themes + graph-rebuild — the paired run themes require (writes edges, then rebuilds the DuckDB cache to match)"
> @echo "  dup-census               Entity-name dup census (entity_name_dedup): --check gate vs baseline, --plan writes the merge plan"
> @echo "  embed-gc                 Evict dead embed-cache rows (trial/spike leftovers) — report-only, exit 1 when dead rows exist; APPLY=1 deletes+VACUUMs"
> @echo "  embgemma-server          Start the EmbeddingGemma-2 llama-server (Q8_0 from models/) on 127.0.0.1:8732 — Ctrl-C to stop; gemma_embedder only ever assumes it is running (D3), never spawns it"
> @echo "  fold-identifiers         Fold exchange ISIN/CIK values into the entity identifier registry"
> @echo "  format                   Normalize Python formatting repo-wide (ruff format; fix for the test_lint_gates.py format gate)"
> @echo "  frontend                 Build the TypeScript frontend bundle into static/findata.bundle.js (needs Bun)"
> @echo "  frontend-check           Type-check + prettier format-check the TypeScript frontend (fast, needs Bun)"
> @echo "  fuzz                     Run Hypothesis property-based tests (deterministic seed for reproducibility)"
> @echo "  gate-fresh               Check gate-run index freshness (gate_query; default reports drift + exit 1 when behind, APPLY=1 force-indexes incrementally; refresh also runs automatically before every gate_query verb)"
> @echo "  git-fresh                Check git-history index freshness — commit/diff search over main (every check runs even if one fails; exit 1 on drift; APPLY=1 reconciles the SHA set incrementally; also run by make search-fresh)"
> @echo "  granite-server           Start the granite-embedding-97m llama-server (Q8_0 from models/) on 127.0.0.1:8733 — Ctrl-C to stop; local_embedder only ever assumes it is running (D3), never spawns it"
> @echo "  graph-algos              Smoke test the Onager algorithm layer (all 14 metrics, no writes)"
> @echo "  graph-rebuild            Rebuild the disk-based DuckDB cache from SQLite, data-only (run after parse_newsletter --apply / derive-relations)"
> @echo "  graph-rebuild-bench      Measure the production graph-rebuild cost ladder (tests/bench_rebuild_scale.py; opt-in, NOT a perf leg)"
> @echo "  graph-smoke              Quick smoke test of the graph query layer (sector-of + neighbors)"
> @echo "  graph-stats              Print a one-shot summary of the graph state (entities, edges, sectors, hygiene)"
> @echo "  hif-export               Export the hypergraph in HIF to snapshots/hif/ (rides make snapshot; SOURCES=... to override)"
> @echo "  install-dev              Install dev dependencies (uv sync; prunes undeclared packages)"
> @echo "  integration              Run end-to-end cross-component pipeline tests (parse_newsletter, API bridge, etc.; appends outputs/integration_report.md)"
> @echo "  license-check            Verify AGPL metadata, root license, and third-party inventory"
> @echo "  lint                     Run ruff linter (replaces flake8)"
> @echo "  lint-audit               Run ruff S/UP/C901 audits (security + modernization + complexity) — Bandit/Refurb/Radon equivs"
> @echo "  live-invariants          Run ONLY the live-marked invariant tests (-m live, xdist -n auto; skip-safe on pristine clone)"
> @echo "  llamacpp-build           Build the unified llama-server into models/llamacpp/bin (vendor/llamacpp recipe: pinned llama.cpp + qwen2vl patch — one binary serves embgemma :8732 AND teleocr :8731)"
> @echo "  llamacpp-health          One-shot probe of the always-on legs: :8732 embgemma (identity + 768d) and :8733 granite (identity + 384d); teleocr :8731 opt-in (llamacpp_health.py --ocr)"
> @echo "  maint                    Routine maintenance: db_maint + snapshot + graph-rebuild + stamp-centrality + csr-rebuild (always-safe)"
> @echo "  maint-full               Post-ingest re-derivation: PRE_FULL index refresh (sync-tags, note-search) + maint + TIER2_STEPS (sector gates, company-embeddings, doc-search, analytics, insights, events, re-snapshot)"
> @echo "  md-lint                  Markdown lint via pinned markdownlint-cli2 — doc/ prose base + findata Tier-1 defect rules (qa-gated; needs Node, SKIP without; proposal: markdown_lint_adoption)"
> @echo "  memory-search-rebuild    Rebuild the harness-memory index (memory_search sidecar; query via helpers/misc/memory_query.py)"
> @echo "  metrics-rebuild          Refresh company financials + note industry sections from yfinance (~1 min, 931 tickers)"
> @echo "  mojo-bench               Run perf-gated Mojo bench legs (cosine-knn, analyzer, pool-4x, regex-bridge, yaml-corpus, regex-corpus, db-access, db-integrity, graph-algos) — measured-time gate per leg, report: Mojo/bench/bench_report.txt"
> @echo "  mojo-build               Compile Mojo/ sources to native binaries in Mojo/bin/ (incremental; machinery in Makefile.mojo)"
> @echo "  mojo-format                 Normalize Mojo/src + Mojo/tests with \`mojo format\` (fix for the tests/test_lint_gates.py format gate)"
> @echo "  mojo-parity             Golden parity: Mojo integrity_check vs the Python original over 89 canonical keys (qa leg; exits 1 on any mismatch)"
> @echo "  mojo-test                Run Mojo/tests/*.mojo test suites via mojo run (machinery in Makefile.mojo)"
> @echo "  near-duplicates          Report near-duplicate note pairs above cosine 0.9 (rename tripwire; READ-ONLY)"
> @echo "  ocr-version-check        Assert the OCR binary matches the pinned version (helpers/misc/ocr_pin.py; advisory)"
> @echo "  onager-build             Build the onager DuckDB extension against duckdb v2.0.0 into models/onager/bin (vendor/onager recipe: pinned CogitatorTech/onager v0.5.0 — the community bucket has no 2.0.0 artifact)"
> @echo "  parity                   Byte-compare registered refactor targets against REF (default HEAD) across pinned hash seeds (warns; STRICT=1 to gate)"
> @echo "  perf                     Run wall-clock perf benchmarks, print timing table, and append to outputs/perf_report.md"
> @echo "  person-census            Person-name dup census (person_name_dedup): --check gate vs baseline, --plan writes the merge plan"
> @echo "  qa                       Run lint + markdown lint + types + deptry + static + pytest + notes + integrity + schema-check + snapshot in PARALLEL (default 4 jobs; override: make qa -j N; run-all — failures reported at the end; appends outputs/qa_report.md)"
> @echo "  quack-health             One-shot wire probe of every resident quack server: SELECT 1 + duckdb_tables resolution per store"
> @echo "  quack-restart            Restart the resident quack servers (same store set unless QUACK_DBS given)"
> @echo "  quack-start              Start resident quack servers, ONE PER STORE — default memory/graph.duckdb, or QUACK_DBS=\"memory/graph.duckdb memory/convo_search.duckdb\"; ports QUACK_PORT_BASE+i; file-mode writes to served stores are refused (split-brain guard)"
> @echo "  quack-status             Resident quack servers status: live pids + per-store wire probe"
> @echo "  quack-stop               Stop all resident quack servers (exact-PID TERM→KILL each, discovery + stop files removed)"
> @echo "  quote-coverage           S0 quote capture coverage audit (advisory, read-only; per-note 95% tripwire + watchlist + salvage measurement)"
> @echo "  recompute-graph          Recompute all graph analytics and persist to graph_analytics"
> @echo "  recompute-hyper          Recompute HGX hyper metrics (hy-MMSBM communities + ho/s centralities)"
> @echo "  refresh-chain            D20 phase 3 parallel — fetch lanes in parallel, apply sequentially (APPLY=1 to write)"
> @echo "  refresh-chain-ser        D20 phase 3 sequential — exchanges → vigil → shp → enrich → CIN web → XBRL --new → snapshot (APPLY=1 to write)"
> @echo "  refresh-exchanges        D19: sync exchange masters + detect new listings (lanes as args; APPLY=1 to write)"
> @echo "  refresh-indices          Sync NSE index constituents into sources.duckdb (Wave 1 broad-based + sectoral; APPLY=1 to write)"
> @echo "  refresh-shp              NSE shareholding-pattern RSS -> invested_in edges (APPLY=1 to write; quarterly cadence, weekly poll)"
> @echo "  refresh-vigil            VIGIL bulk: RPT group/supply + ratings -> edges (APPLY=1 to write; weekly)"
> @echo "  refresh-xbrl             D20: incremental NSE XBRL sweep, unseen filings only (ARGS=--new for IPOs; APPLY=1 to write)"
> @echo "  review-patch             OCR delegation selection roster + native scan (advisory; asserts tests/Mojo visibility per rule.json; STACK=N, COMMIT=<sha>, FROM=<base> TO=<head>, OFFLINE=1)"
> @echo "  review-scan              Native scanner roster over the review diff (advisory; bandit/shellcheck/sqlfluff/semgrep/osv on changed lines; STACK=N, COMMIT=<sha>, FROM=<base> TO=<head>, OFFLINE=1)"
> @echo "  schema-check             Check tracked schema/ DDL against the live memory/ DBs (exit 1 on drift; fix: make schema-dump)"
> @echo "  schema-dump              Regenerate schema/<engine>/<db>.sql from the live memory/ DBs (proposal schema_ddl_review_surface)"
> @echo "  script-search-rebuild    Rebuild the script metadata index (script_search sidecar; query via helpers/misc/script_query.py)"
> @echo "  search-fresh             Check ALL search indexes for staleness — doc/, script metadata, note embeddings, harness memory (every check runs even if one fails; exit 1 on drift; APPLY=1 refreshes them instead; ARGS=\"--allow-stale\"/\"--sandbox\" for the stale-apply guard escape/preview; also run by make advisory)"
> @echo "  search-tui               Full-screen search front door — docs/scripts/notes indexes + ripwire + rg lanes; enter reads markdown via glow"
> @echo "  secret-scan              Incremental git-history secret scan (state under .git/secret-scan/)"
> @echo "  security-apply           Apply gap decisions to the ledger (DECISIONS=<jsonl>, dry-run unless WRITE=1): cover needs verdict+reviewed, exclude/defer need a reason"
> @echo "  security-audit           Security audit lane for one surface family: prior-coverage plan → run findings check → scoped close-out gate (SCOPE=app|extractors|..., RUN=<id> defaults to <today>-<scope>-1, STRICT=1 makes unaccounted classes errors)"
> @echo "  security-critic          Coverage critic: uncovered (surface, class) pairs, stale fingerprints, declared exclusions, and confirmed findings the ledger does not back (SCOPE=..., RUN=<id> to cross-check a run's findings, JSON=1, STRICT=1 to fail on an INCOMPLETE verdict)"
> @echo "  security-queue           Regenerate the gap work queue (triage idiom): JSONL of (surface, class) pairs with no ledger row and no exclusion (SCOPE=..., OUT=<path> defaults to doc/local/security/coverage_queue.jsonl)"
> @echo "  security-scopes          Print the scope-family security inventories (SCOPE=graph|harness|desktop|frontend|data|extractors); sync checks the inventory areas against the ledger's surface names"
> @echo "  security-verify          Verifier role (A6): emit a blind brief for ONE candidate (FP=<fingerprint>, RUN=<id>), or merge a verifier result with RECORD=<file> OUT=<file> (the input findings file is never overwritten)"
> @echo "  snapshot                 Refresh the versioned DB snapshot"
> @echo "  snapshot-check           Verify the snapshot round-trips against the live DB"
> @echo "  snapshot-fresh           Generation-only snapshot freshness (fail fast on drift; fix: make snapshot)"
> @echo "  snapshot-restore         Rebuild memory/ DBs from the git-tracked Parquet snapshot (clobbers live DBs)"
> @echo "  speaker-census           Quote-speaker census (person_ontology_spine S1): --check gate vs baseline, --plan writes the resolution plan"
> @echo "  speaker-resolve          Quote-speaker resolution map (person_ontology_spine S2): dry-run default; APPLY=1 writes the map, MINT=1 also mints persons"
> @echo "  stamp-centrality         Re-stamp v_centrality_* tables (explicit lane; lane-served metrics skipped per ROUTING; graph-rebuild drops them; maint runs it after graph-rebuild)"
> @echo "  static-checks            Fast static checks (syntax, shebangs, YAML, artifacts, merge markers, license metadata)"
> @echo "  store-sweep              Standalone estate sweep: census + reclaim/checkpoint every registered sidecar (ARGS=--dry-run previews)"
> @echo "  suggest-relations        Print link-prediction relation suggestions (C2; append with --append)"
> @echo "  sync-coverage-tags       Converge The Chatter company/<slug> tags from quote coverage"
> @echo "  sync-sector-links        WRITE the auto company index into sector notes (explicit; maint-full only checks staleness)"
> @echo "  sync-tags                Rebuild entity_tags from note YAML (mirrors entity_type/sector/market_cap/subsector)"
> @echo "  teleocr-server           Start the TeleOCR llama-server (Q4_K_M + mmproj from models/) on 127.0.0.1:8731 — Ctrl-C to stop; teleocr_engine only ever assumes it is running (D3), never spawns it"
> @echo "  test                     pytest unit tests only (no live DB, no slow benchmarks)"
> @echo "  tier-nudge               Manual billion-context tier-ladder nudge (read-only): SESSION_ARGS=\"--session <id> --nudge\" prints the state, the nudge text and the exact compress(...) call"
> @echo "  tier-watch               Watch the billion-context tier ladder live (read-only): prints why each rung is or is not ready (bars, pools, ordering, growth floor); --once for a single sample"
> @echo "  tier-watch-auto          Auto-write compress() packets for INJECT T2/T3 (auto-fire); PACKET=<path> defaults to /mnt/data/tmp/opencode/t2_packet.jsonl; WATCH_ARGS for extra flags"
> @echo "  tmp-sweep                Reap this repo's temp-dir residue (scratch DBs, stale bench dirs, TUI log) — 24h age guard, dry-run by default; APPLY=1 removes (proposal: tmpdir_sanitization)"
> @echo "  triage-quotes            Triage the quote entity worklist: report + bucketed decisions file (triage_pending_quotes)"
> @echo "  triage-relations         Triage the _pending_relations queue: report + bucketed decisions file (pending_relations_triage)"
> @echo "  types                    Run ty type checker on helpers + app.py (Astral uv+ruff stack)"
> @echo "  types-tests              Run EXPANDED ty checks over tests/ (advisory-grade, warnings non-blocking; TYPES_TESTS_FMT=concise for one-line-per-diagnostic logs — the make advisory ty-tests step uses that)"
> @echo "  update-extensions        Update all installed DuckDB extensions to latest (weekly cadence)"


static-checks:  ## Fast static checks (syntax, shebangs, YAML, artifacts, merge markers, license metadata)
> python3 helpers/validators/static_checks.py
> python3 helpers/misc/license_check.py

license-check:  ## Verify AGPL metadata, root license, and third-party inventory
> python3 helpers/misc/license_check.py

store-sweep:    ## Standalone estate sweep: census + reclaim/checkpoint every registered sidecar store (ARGS=--dry-run previews)
> .venv/bin/python3 helpers/maintenance/store_sweep.py $(ARGS)

# c901_complexity_debt S1. NOT part of `make qa`: parity is only meaningful
# against a ref, so it is a refactor-time check, not a tree-state gate.
# House ruling: it WARNS on divergence and does not assert -- a false
# positive would only teach people to skip it. STRICT=1 to gate instead.
parity:         ## Byte-compare registered refactor targets against REF (default HEAD) across pinned hash seeds (warns; STRICT=1 to gate)
> python3 helpers/misc/parity_harness.py $(if $(REF),--ref $(REF),) $(if $(STRICT),--strict,)

tmp-sweep:      ## Reap this repo's temp-dir residue (scratch DBs, stale bench dirs, TUI log) — 24h age guard, dry-run by default; APPLY=1 removes (proposal: tmpdir_sanitization)
> python3 helpers/maintenance/tmp_sweep.py $(if $(APPLY),--apply,)

qa:             ## Run lint + markdown lint + types + deptry + static + pytest + notes + integrity + schema-check + snapshot in PARALLEL (default 4 jobs; override: make qa -j N; run-all — failures reported at the end; appends outputs/qa_report.md)
> python3 tests/run_gate_report.py qa
> @echo "✓ QA passed (lint + types + deptry + static + pytest + notes + integrity + snapshot; appended to outputs/qa_report.md)"

test:           ## pytest unit tests only (no live DB, no slow benchmarks; xdist -n auto)
> pytest -m "not live" -n auto

# xdist-safe (gate_xdist_phase2 Slice A): conftest.py redirects the default
# graph cache to a per-worker copy under PYTEST_XDIST_WORKER; tests that
# must hit the real cache carry the real_graph_cache marker.
live-invariants: ## Run ONLY the live-marked invariant tests (-m live, xdist -n auto; skip-safe on pristine clone)
> pytest -m live -n auto --dist=loadgroup
> @echo "✓ live invariant tests passed"

perf:           ## Run wall-clock perf benchmarks, print timing table, and append to outputs/perf_report.md
> python3 tests/run_perf_benchmarks.py
> @echo "✓ performance benchmarks passed (see table above; appended to outputs/perf_report.md)"

# The OCR binary is a global unpinned install and the package ships no
# changelog, so an upgrade changes the review loop silently. The pin is the
# version the roster's assumptions were checked against (delegate JSON shape,
# text|json only, inert rules key) — bump it deliberately, re-reading
# `ocr delegate preview --help` and doc/procedures/ocr_review.md in the same
# patch.
ocr-version-check: ## Assert the OCR binary matches the pinned version (helpers/misc/ocr_pin.py; advisory)
> @.venv/bin/python3 helpers/misc/ocr_pin.py

onager-build:  ## Build the onager DuckDB extension against duckdb v2.0.0 into models/onager/bin (vendor/onager recipe: pinned CogitatorTech/onager v0.5.0, the 2.0.0 port — the community bucket has no 2.0.0 artifact; LOAD verdict goes to vendor/onager/PROVENANCE.md)
> @bash vendor/onager/build.sh

cover:          ## Run all tests with coverage over helpers/ (branch + missing-line report)
> pytest --cov=helpers --cov-branch --cov-report=term-missing --cov-report=html
> @echo "✓ coverage report written to htmlcov/index.html"

fuzz:           ## Run Hypothesis property-based tests (deterministic seed for reproducibility; xdist -n auto)
> pytest tests/test_fuzz_*.py -v -n auto

integration:    ## Run end-to-end cross-component pipeline tests (parse_newsletter, API bridge, etc.; appends outputs/integration_report.md)
> python3 tests/run_gate_report.py integration
> @echo "✓ Integration tests passed (appended to outputs/integration_report.md)"


refresh-exchanges:  ## D19: sync exchange masters + detect new listings (lanes as args; APPLY=1 to write)
> .venv/bin/python3 helpers/maintenance/exchange_sync.py $(filter-out $@,$(filter-out APPLY=1,$(MAKECMDGOALS))) $(if $(APPLY),--apply)

refresh-indices:  ## NSE index constituents -> sources.duckdb (Wave 1 broad-based + sectoral; APPLY=1 to write)
> .venv/bin/python3 helpers/maintenance/index_sync.py $(if $(APPLY),--apply)
> @echo "✓ index_constituents sidecar synced (dry-run unless APPLY=1)"

refresh-xbrl:  ## D20: incremental NSE XBRL sweep — only new filings per entity (APPLY=1 to write; ARGS="--new" for fresh IPOs only)
> .venv/bin/python3 helpers/maintenance/ingest_nse_xbrl.py $(ARGS) $(if $(APPLY),--apply)

refresh-vigil:  ## VIGIL bulk lanes: RPT group/supply + credit ratings -> sources.duckdb + edges (APPLY=1 to write)
> .venv/bin/python3 helpers/maintenance/related_party_sync.py --download --ratings-download $(if $(APPLY),--apply)
> @echo "✓ VIGIL rpt_transactions + credit_ratings synced (dry-run unless APPLY=1)"

refresh-shp:  ## NSE shareholding-pattern RSS lane -> sources.duckdb + invested_in edges (APPLY=1 to write)
> .venv/bin/python3 helpers/maintenance/shareholding_sync.py --rss --bse-rss $(if $(APPLY),--apply)
> @echo "✓ shareholding filings synced (dry-run unless APPLY=1)"

refresh-chain-ser:  ## D20 phase 3: post-refresh hook chain — exchanges → vigil → shp → enrich → CIN web → XBRL --new → snapshot (APPLY=1 to write; all lanes incremental/idempotent)
> .venv/bin/python3 helpers/maintenance/exchange_sync.py $(if $(APPLY),--apply)
> .venv/bin/python3 helpers/maintenance/related_party_sync.py --download --ratings-download $(if $(APPLY),--apply)
> .venv/bin/python3 helpers/maintenance/shareholding_sync.py --rss --bse-rss $(if $(APPLY),--apply)
> .venv/bin/python3 helpers/maintenance/enrich_from_yfinance.py $(if $(APPLY),--apply)
> .venv/bin/python3 helpers/maintenance/mca_cin_resolve.py ingest-web --no-revalidate $(if $(APPLY),--apply)
> .venv/bin/python3 helpers/maintenance/ingest_nse_xbrl.py --new $(if $(APPLY),--apply)
> $(if $(APPLY),.venv/bin/python3 helpers/maintenance/snapshot_db.py,@echo "dry-run: snapshot export skipped (APPLY=1 writes)")

refresh-chain:  ## D20 phase 3 parallel — fetch lanes in parallel, apply sequentially (APPLY=1 to write)
> .venv/bin/python3 helpers/maintenance/related_party_sync.py --download --ratings-download &
> .venv/bin/python3 helpers/maintenance/shareholding_sync.py --rss --bse-rss --fetch-only &
> .venv/bin/python3 helpers/maintenance/enrich_from_yfinance.py --fetch-only &
> .venv/bin/python3 helpers/maintenance/ingest_nse_xbrl.py --new --fetch-only &
> .venv/bin/python3 helpers/maintenance/exchange_sync.py --fetch-only &
> .venv/bin/python3 helpers/maintenance/ticker_hygiene.py --fetch-only &
> wait
> .venv/bin/python3 helpers/maintenance/exchange_sync.py --apply-only $(if $(APPLY),--apply)
> .venv/bin/python3 helpers/maintenance/related_party_sync.py --apply $(if $(APPLY),--apply)
> .venv/bin/python3 helpers/maintenance/shareholding_sync.py --apply-only $(if $(APPLY),--apply)
> .venv/bin/python3 helpers/maintenance/ticker_hygiene.py --apply-only $(if $(APPLY),--apply)
> .venv/bin/python3 helpers/maintenance/enrich_from_yfinance.py --apply-only $(if $(APPLY),--apply)
> .venv/bin/python3 helpers/maintenance/mca_cin_resolve.py ingest-web --no-revalidate $(if $(APPLY),--apply)
> .venv/bin/python3 helpers/maintenance/ingest_nse_xbrl.py --new --apply-only $(if $(APPLY),--apply)
> $(if $(APPLY),.venv/bin/python3 helpers/maintenance/snapshot_db.py,@echo "dry-run: snapshot export skipped (APPLY=1 writes)")

snapshot:       ## Refresh the versioned DB snapshot (+ HIF rider)
> MAINT_TARGET=snapshot python3 helpers/maintenance/snapshot_db.py
> $(MAKE) --no-print-directory hif-export
> @echo "✓ Snapshots refreshed (snapshots/parquet/ [git] + db-backup/*.zst [local] + snapshots/hif/ [git])"

HIF_SOURCES ?= sector,sub_sector,group,jv
hif-export:    ## Export the hypergraph in HIF (snapshots/hif/; SOURCES=..., OUT=... to override)
> MAINT_TARGET=hif-export python3 helpers/graph/hyper_hif.py --sources $(HIF_SOURCES) --out-dir snapshots/hif
> @echo "✓ HIF exported (hif_nodes/edges/incidences .parquet in snapshots/hif/; canonical per architecture.md §10)"

snapshot-check: ## Verify the snapshot round-trips against the live DB
> MAINT_TARGET=snapshot-check python3 helpers/maintenance/snapshot_db.py --check

snapshot-fresh: ## Generation-only snapshot freshness (fail fast on drift)
> MAINT_TARGET=snapshot-fresh python3 helpers/maintenance/snapshot_db.py --quick

snapshot-restore: ## Rebuild memory/ DBs from the git-tracked Parquet snapshot (clobbers live DBs)
> python3 helpers/maintenance/snapshot_db.py --restore --force
> @echo "✓ Live DBs rebuilt from snapshots/parquet/"

maint:          ## Routine maintenance: db_maint + snapshot + graph-rebuild + stamp-centrality + csr-rebuild (always-safe)
> python3 helpers/maintenance/maint.py
> @echo "✓ Routine maintenance complete"

maint-full:     ## Post-ingest re-derivation: PRE_FULL index refresh + maint + TIER2_STEPS (authoritative list: helpers/maintenance/maint.py)
> python3 helpers/maintenance/maint.py --full
> @echo "✓ Full maintenance complete"

metrics-rebuild: ## Refresh company financials + notes from yfinance (~1 min, 931 tickers)
> python3 helpers/maintenance/enrich_from_yfinance.py --apply
> @echo "✓ yfinance enrichment complete (competes_with moved to relations-enrich; run 'make graph-rebuild' to refresh DuckDB edges)"

# Mojo machinery (rule definitions, wildcard discovery, test runner) lives
# in Makefile.mojo — it grows with the Mojo source/test tree. This target
# stays thin so the main Makefile keeps a single annotated entry point.
mojo-bench:      ## Run all Mojo bench legs via Mojo/bench/run_bench.py (MOJO_BENCH_SCALE/REPS, MOJO_BENCH_ARGS='--leg NAME')
> $(MAKE) -f Makefile.mojo mojo-bench

mojo-parity:     ## Golden parity: Mojo integrity_check vs the Python original over 89 canonical keys (qa leg; exits 1 on any mismatch)
> $(MAKE) -f Makefile.mojo mojo-parity

mojo-build:      ## Compile Mojo/ sources to native binaries in Mojo/bin/ (incremental; machinery in Makefile.mojo)
> $(MAKE) -f Makefile.mojo mojo-build

mojo-test:       ## Run Mojo/tests/*.mojo test suites via mojo run (machinery in Makefile.mojo)
> $(MAKE) -f Makefile.mojo mojo-test

mojo-format:        ## Normalize Mojo/src + Mojo/tests with `mojo format` (fix for the tests/test_lint_gates.py format gate)
> $(MAKE) -f Makefile.mojo mojo-format

# GF fallback pass runs AFTER the yfinance pass (it consumes that report's
# [ticker_issues]); curated + tier 1 by default, --tier2 adds BSE
# name-search discovery; dry-run until F4:
#   make relations-enrich ARGS="--source googlefinance --dry-run --tier2"
# Terminal classifications (stop re-probing dead tickers):
#   make relations-enrich ARGS='--classify "Akzo Nobel India" amalgamated "JSW Paints"'
relations-enrich ARGS="--source yfinance --dry-run":
> python3 helpers/maintenance/enrich_relations.py $(ARGS)
> @echo "✓ relations enrichment done (run 'make graph-rebuild' to refresh DuckDB edges)"

sync-tags:      ## Rebuild entity_tags from note YAML (mirrors entity_type/sector/market_cap/subsector)
> python3 helpers/core/sync_tags.py --apply
> @echo "✓ entity_tags synced from notes"

sync-coverage-tags: ## Converge The Chatter company/<slug> tags from quote coverage
> python3 helpers/maintenance/sync_coverage_tags.py --apply
> @echo "✓ edition coverage tags synced from quotes"

sync-sector-links: ## WRITE the auto company index into sector notes (explicit; maint-full only checks staleness)
> python3 helpers/maintenance/sync_sector_wikilinks.py --apply
> @echo "✓ sector wikilinks synced from DB"

install-dev:    ## Install dev dependencies (uv sync; prunes undeclared packages)
> uv sync --extra dev

graph-smoke:    ## Quick smoke test of the graph query layer (sector-of + neighbors)
> python3 helpers/graph/query.py sector-of CEAT
> python3 helpers/graph/query.py sector-members Automotive --limit 3
> python3 helpers/graph/query.py neighbors "Polycab India"
> @echo "✓ Graph layer smoke test passed"

graph-stats:    ## Print a one-shot summary of the graph state (entities, edges, sectors, hygiene)
> python3 helpers/graph/stats.py

analytics:       ## Read-only analytics over the git-tracked Parquet snapshot (A3; arg = report name)
> python3 helpers/graph/analytics.py $(REPORT)

analytics-parquet: ## Ad-hoc read-only SQL over the Parquet snapshot (tables are <side>__<name>; --list/--stats/--query)
> .venv/bin/python3 helpers/maintenance/analytics/cli.py --stats

tier-nudge:     ## Manual billion-context tier-ladder nudge (read-only): SESSION_ARGS="--session <id> --nudge" prints the state, the nudge text and the exact compress(...) call
> .venv/bin/python3 helpers/analytics/bili_tier_nudge.py $(SESSION_ARGS)

tier-watch:     ## Watch the billion-context tier ladder live (read-only): prints why each rung is or is not ready (bars, pools, ordering, growth floor); WATCH_ARGS="--once" for a single sample
> .venv/bin/python3 helpers/analytics/bili_tier_watch.py $(WATCH_ARGS)

tier-watch-auto:    ## Auto-write compress() packets for INJECT T2/T3 (auto-fire); PACKET=<path> defaults to /mnt/data/tmp/opencode/t2_packet.jsonl; WATCH_ARGS for extra flags
> PACKET="$${PACKET:-/mnt/data/tmp/opencode/t2_packet.jsonl}"; .venv/bin/python3 helpers/analytics/bili_tier_watch.py $(WATCH_ARGS) --inject-packet "$$PACKET"
bili-status:    ## Proxy status report (read-only): effective bars, injections, folds, burn, cache
> bash helpers/analytics/bili_status.sh

bili-monitor:   ## Background proxy monitor (read-only): DURATION_S=43200 POLL_S=60 SNAP_S=300
> .venv/bin/python3 helpers/analytics/bili_monitor.py $(DURATION_S) $(POLL_S) $(SNAP_S)

bili-stop-monitor: ## Stop the background proxy monitor (never `pkill -f` -- it kills the caller)
> bash helpers/analytics/bili_stop_monitor.sh

bili-report:    ## Proxy economics report from the watcher state dir (read-only): REPORT_ARGS="<session ids>"
> .venv/bin/python3 helpers/analytics/bili_tunewatch.py report $(REPORT_ARGS)

bili-snap:      ## Snapshot the proxy state now (read-only): SNAP_ARGS="<session ids>"
> .venv/bin/python3 helpers/analytics/bili_tunewatch.py snap $(SNAP_ARGS)

bili-watch-event: ## Block until the next T1 fire or fold for a session (read-only): EVENT_ARGS="<session> [either|t1|fold] [max_wait_s]"
> .venv/bin/python3 helpers/analytics/bili_watch_event.py $(EVENT_ARGS)

bili-thresh-replay: ## Replay the tier fire curve offline (read-only): REPLAY_ARGS="<floor> <thresholds...>"
> .venv/bin/python3 helpers/analytics/bili_thresh_replay.py $(REPLAY_ARGS)

suggest-relations: ## Print link-prediction relation suggestions (C2; append with --append)
> python3 helpers/graph/suggest_relations.py

triage-relations: ## Triage the _pending_relations queue: report + bucketed decisions file (pending_relations_triage)
> python3 helpers/graph/triage_pending_relations.py
> @echo "✓ triage report + decisions file written (annotate decisions, then --apply-decisions)"

triage-quotes: ## Triage the quote entity worklist: report + bucketed decisions file (triage_pending_quotes)
> python3 helpers/graph/triage_pending_quotes.py
> @echo "✓ quote triage report + decisions file written (annotate decisions, then --apply-decisions)"

decisions-preannotate: ## Pre-annotate review surfaces with carrier verdicts (advisory; SURFACE=all|relations|quotes|coverage|findings|ocr|markdown-parse, FINDINGS=<path>)
> python3 helpers/core/decisions_preannotate.py --surface $(SURFACE) $(if $(FINDINGS),--findings $(FINDINGS))
> @echo "✓ decisions-preannotate: advisory verdicts written (human review gate unchanged)"

graph-algos:    ## Smoke test the Onager algorithm layer (all 14 metrics, no writes)
> python3 helpers/graph/algorithms.py --all --no-apply
> @echo "✓ Onager graph-algorithm layer smoke test passed (all metrics, nothing written)"

graph-rebuild-bench:   ## Measure the production graph-rebuild cost ladder (tests/bench_rebuild_scale.py; opt-in, NOT a perf leg)
> .venv/bin/python3 tests/bench_rebuild_scale.py $(if $(ROWS),--rows $(ROWS),) $(if $(REPS),--reps $(REPS),) $(if $(BREAKDOWN),--breakdown,) $(if $(DENSITY),--density-check,)

graph-rebuild:  ## Rebuild the disk-based DuckDB cache from SQLite (run after parse_newsletter --apply / derive-relations)
> python3 helpers/graph/query.py rebuild
> @echo "✓ DuckDB graph cache rebuilt, data-only (memory/graph.duckdb; v_centrality_* dropped — run stamp-centrality to re-stamp)"

stamp-centrality: ## Re-stamp v_centrality_* tables on the warm cache (lane-served metrics skipped per ROUTING; centrality_rebuild_contract explicit lane)
> python3 helpers/graph/query.py stamp-centrality
> @echo "✓ Centrality cache stamped (v_centrality_* tables warm; lane-served metrics skipped)"

csr:             ## Build the CSR substrate (memory/csr/; shortest-path fast lane)
> .venv/bin/python3 helpers/graph/csr.py --db memory/research.db --out memory/csr
> .venv/bin/python3 helpers/graph/csr.py --db memory/research.db --out memory/csr --verify

csr-check:       ## Check CSR substrate freshness (exit 1 if stale; also run by make advisory)
> .venv/bin/python3 helpers/graph/csr.py --db memory/research.db --out memory/csr --check

csr-rebuild:     ## Rebuild the CSR substrate regardless of freshness
> .venv/bin/python3 helpers/graph/csr.py --db memory/research.db --out memory/csr
> .venv/bin/python3 helpers/graph/csr.py --db memory/research.db --out memory/csr --verify

near-duplicates: ## Report near-duplicate note pairs above cosine 0.9 (rename tripwire; READ-ONLY — triage by hand, remediation is user-held)
> python3 helpers/graph/query.py near-duplicates --min-sim 0.9
> @echo "✓ Near-duplicate report complete (nothing written)"

update-extensions: ## Update all installed DuckDB extensions to latest (weekly cadence)
> python3 helpers/graph/query.py update-extensions
> @echo "✓ DuckDB extensions checked/updated"

secret-scan: ## Incremental git-history secret scan (state under .git/secret-scan/)
> python3 helpers/misc/git_secret_scan.py
> @echo "✓ Secret scan complete (incremental; state: .git/secret-scan/state.json)"

cargo-audit: ## RustSec scan of the desktop lockfile (advisory; SKIPs without cargo-audit; security_evaluation Addendum 7 §E)
> @if command -v cargo-audit >/dev/null 2>&1 || [ -x "$$HOME/.cargo/bin/cargo-audit" ]; then \
> 	cd desktop/src-tauri && PATH="$$HOME/.cargo/bin:$$PATH" cargo audit; \
> else \
> 	echo "SKIP cargo-audit: not installed (cargo install cargo-audit --locked)"; \
> fi

script-search-rebuild: ## Rebuild the script metadata index (script_search sidecar; query via helpers/misc/script_query.py)
> python3 helpers/maintenance/rebuild_script_search.py
> @echo "✓ script_search index rebuilt (memory/script_search.db; gate: make search-fresh / advisory)"

memory-search-rebuild: ## Rebuild the harness-memory index (memory_search sidecar; query via helpers/misc/memory_query.py)
> python3 helpers/maintenance/rebuild_memory_search.py
> @echo "✓ memory_search index rebuilt (memory/memory_search.db; gate: make search-fresh / advisory)"

convo-search-rebuild: ## Rebuild convo_search.duckdb via atomic swap (compacts the duckdb; graph-lane pattern)
> MAINT_TARGET=convo-search-rebuild python3 helpers/maintenance/rebuild_convo_search.py --swap
> @echo "✓ convo_search rebuilt via atomic swap (memory/convo_search.duckdb)"

convo-search-check: ## Report convo_search.duckdb file size vs payload (advisory; swap-rebuild candidate)
> @.venv/bin/python3 -c "import duckdb,os;d='memory/convo_search.duckdb';c=duckdb.connect(d,read_only=True);r=c.execute('SELECT COUNT(*) FROM convo_search').fetchone();c.close();size=os.path.getsize(d)/1e6;print(f'  convo_search: {size:.0f} MB, {r[0]:,} rows')"

search-fresh:    ## Check ALL search indexes for staleness — doc/, script metadata, note embeddings, harness memory, git history (every check runs even if one fails; exit 1 on drift; APPLY=1 refreshes them instead; ARGS="--allow-stale"/"--sandbox" reaches the rebuild CLIs for the stale-apply guard escape/preview; also run by make advisory)
> @rc=0; \
>   extra="$(if $(APPLY),,--check)"; \
>   case " $(ARGS) " in *" --sandbox "*|*" --allow-stale "*) extra="";; esac; \
>   echo "search-fresh: $(if $(APPLY),APPLY — refreshing,$(if $(ARGS),ARGS [$(ARGS)],check)) mode"; \
>   for s in rebuild_doc_search rebuild_script_search rebuild_note_search rebuild_memory_search rebuild_git_search; do \
>     t0=$$(date +%s%3N); \
>     echo "--- $$s $$extra $(ARGS)"; \
>     MAINT_TARGET=search-fresh python3 helpers/maintenance/$$s.py $$extra $(ARGS) || rc=1; \
>     echo "    took $$(( $$(date +%s%3N) - t0 ))ms"; \
>   done; \
>   if [ $$rc -eq 0 ]; then echo "✓ all search indexes fresh (doc_search, script_search, note_search, memory_search, git_search)"; fi; \
>   exit $$rc

git-fresh:        ## Check git-history index freshness — commit/diff search over main (every check runs even if one fails; exit 1 on drift; APPLY=1 reconciles the SHA set incrementally; also run by make search-fresh)
> @rc=0; \
>   extra="$(if $(APPLY),,--check)"; \
>   echo "git-fresh: $(if $(APPLY),APPLY — reconciling,check) mode"; \
>   MAINT_TARGET=git-fresh python3 helpers/maintenance/rebuild_git_search.py $$extra $(ARGS) || rc=1; \
>   exit $$rc

gate-fresh: ## Check gate-run index freshness for helpers/misc/gate_query.py (default: report drift, exit 1 when the index is behind — nothing written; APPLY=1 force-indexes incrementally; refresh also runs automatically before every gate_query verb)
> @if [ -n "$(APPLY)" ]; then \
> 	python3 helpers/misc/gate_query.py refresh; \
> 	echo "✓ gate index refreshed (outputs/gate_runs.duckdb; query: gate_query latest|failures|recent)"; \
> else \
> 	python3 helpers/misc/gate_query.py refresh --check; \
> fi

convo-fresh:      ## Check conversation corpus+index freshness — harness sources vs parquet corpus vs pointer index (every check runs even if one fails; exit 1 on drift; APPLY=1 harvests+incrementally rebuilds; also run by make advisory)
> @rc=0; \
>   echo "convo-fresh: $(if $(APPLY),APPLY — harvesting+rebuilding,check) mode"; \
>   t0=$$(date +%s%3N); \
>   echo "--- harvest_conversations $(if $(APPLY),,--check)"; \
>   MAINT_TARGET=convo-fresh python3 helpers/maintenance/harvest_conversations.py $(if $(APPLY),,--check) || rc=1; \
>   echo "    took $$(( $$(date +%s%3N) - t0 ))ms"; \
>   t1=$$(date +%s%3N); \
>   echo "--- rebuild_convo_search $(if $(APPLY),--incremental,--check)"; \
>   MAINT_TARGET=convo-fresh python3 helpers/maintenance/rebuild_convo_search.py $(if $(APPLY),--incremental,--check) || rc=1; \
>   echo "    took $$(( $$(date +%s%3N) - t1 ))ms"; \
>   if [ $$rc -eq 0 ]; then echo "✓ convo corpus+index fresh"; fi; \
>   exit $$rc

analytics-fresh:  ## Analytics stores (agent_traces + model_usage) vs harness frontiers — read-only drift report, exit 1 when a completed day is missing; APPLY=1 runs incremental load all on both loaders
> @rc=0; \
>   echo "analytics-fresh: $(if $(APPLY),APPLY — loading,check) mode"; \
>   if [ -n "$(APPLY)" ]; then \
>     MAINT_TARGET=analytics-fresh python3 helpers/analytics/model_analytics.py load all || rc=1; \
>     MAINT_TARGET=analytics-fresh python3 helpers/analytics/agent_traces.py load all || rc=1; \
>   fi; \
>   MAINT_TARGET=analytics-fresh python3 helpers/misc/analytics_fresh.py || rc=1; \
>   if [ $$rc -eq 0 ]; then echo "✓ analytics stores fresh"; fi; \
>   exit $$rc


llamacpp-build:  ## Build the unified llama-server into models/llamacpp/bin (vendor/llamacpp recipe: pinned llama.cpp + qwen2vl patch — one binary serves embgemma :8732 AND teleocr :8731)
> @bash vendor/llamacpp/build.sh

llamacpp-health:  ## One-shot probe of the always-on legs (:8732 embgemma identity + 768d, :8733 granite identity + 384d); teleocr :8731 via helpers/misc/llamacpp_health.py --ocr
> @python3 helpers/misc/llamacpp_health.py

embgemma-server:  ## Start the EmbeddingGemma-2 llama-server (Q8_0 from models/) on 127.0.0.1:8732 — Ctrl-C to stop; gemma_embedder only ever assumes it is running (D3), never spawns it
> @if [ -n "$$EMBGEMMA_LLAMA_BIN" ]; then bin=$$EMBGEMMA_LLAMA_BIN; \
> elif [ -x models/llamacpp/bin/llama-server ]; then bin=models/llamacpp/bin/llama-server; \
> else echo "unified llama-server not found — make llamacpp-build (vendor/llamacpp, one binary for all legs) or set EMBGEMMA_LLAMA_BIN"; exit 1; fi; \
> echo "embgemma-server: $$bin (Ctrl-C to stop)"; \
> $$bin -m models/embeddinggemma-2-Q8_0.gguf --embeddings --pooling mean --host 127.0.0.1 --port 8732 -c 16384 -b 16384 -ub 16384 -np 2 -t 3


granite-server:  ## Start the granite-embedding-97m llama-server (Q8_0 from models/) on 127.0.0.1:8733 — Ctrl-C to stop; local_embedder only ever assumes it is running (D3), never spawns it
> @if [ -n "$$GRANITE_LLAMA_BIN" ]; then bin=$$GRANITE_LLAMA_BIN; \
> elif [ -x models/llamacpp/bin/llama-server ]; then bin=models/llamacpp/bin/llama-server; \
> else echo "unified llama-server not found — make llamacpp-build (vendor/llamacpp, one binary for all legs) or set GRANITE_LLAMA_BIN"; exit 1; fi; \
> echo "granite-server: $$bin (Ctrl-C to stop)"; \
> $$bin -m models/granite-embedding-97M-multilingual-r2-Q8_0.gguf --embeddings --pooling mean --host 127.0.0.1 --port 8733 -c 16384 -b 16384 -ub 16384 -np 2 -t 3


teleocr-server:  ## Start the TeleOCR llama-server (Q4_K_M + mmproj q8_0 from models/) on 127.0.0.1:8731 — Ctrl-C to stop; teleocr_engine only ever assumes it is running (D3), never spawns it
> @if [ -n "$$TELEOCR_LLAMA_BIN" ]; then bin=$$TELEOCR_LLAMA_BIN; \
> elif [ -x models/llamacpp/bin/llama-server ]; then bin=models/llamacpp/bin/llama-server; \
> else echo "unified llama-server not found — make llamacpp-build (vendor/llamacpp, one binary for all legs) or set TELEOCR_LLAMA_BIN"; exit 1; fi; \
> echo "teleocr-server: $$bin (Ctrl-C to stop)"; \
> $$bin -m models/NaviDC-OCR-Q4_K_M.gguf --mmproj models/NaviDC-OCR-mmproj-q8_0.gguf --host 127.0.0.1 --port 8731 -c 16384 -np 2 -b 8192 -ub 2048 -t 3


quack-start:  ## Start resident quack servers, ONE PER STORE (wire serves only a server's primary db) — default memory/graph.duckdb, or QUACK_DBS="memory/graph.duckdb memory/convo_search.duckdb"; ports QUACK_PORT_BASE+i; file-mode writes to served stores are refused (split-brain guard)
> @bash helpers/misc/quack_servers.sh start $(if $(QUACK_DBS),$(QUACK_DBS),)

quack-stop:  ## Stop all resident quack servers (exact-PID TERM→KILL each, discovery + stop files removed)
> @bash helpers/misc/quack_servers.sh stop

quack-status:  ## Resident quack servers status: live pids + per-store wire probe
> @bash helpers/misc/quack_servers.sh status

quack-restart:  ## Restart the resident quack servers (same store set unless QUACK_DBS given)
> @bash helpers/misc/quack_servers.sh restart $(if $(QUACK_DBS),$(QUACK_DBS),)

quack-health:  ## One-shot wire probe of every resident quack server: SELECT 1 + duckdb_tables resolution per store
> @python3 helpers/misc/quack_health.py


person-census: ## Person-name dup census: --check gate vs baseline / --plan merge plan (person_name_dedup)
> .venv/bin/python3 helpers/graph/person_dedup.py $(or $(ARGS),--check)
> @echo "✓ person-census done"

speaker-census: ## Quote-speaker census: --check gate vs baseline / --plan resolution plan (person_ontology_spine S1)
> .venv/bin/python3 helpers/graph/speaker_census.py $(or $(ARGS),--census)
> @echo "✓ speaker-census done"

speaker-resolve: ## Quote-speaker resolution map (person_ontology_spine S2): dry-run default / APPLY=1 writes the map / MINT=1 mints persons
> .venv/bin/python3 helpers/graph/speaker_resolution.py $(if $(APPLY),--apply,) $(if $(MINT),--mint,) $(or $(ARGS),)
> @echo "✓ speaker-resolve done"

derive-org-membership: ## Derive org_membership hyperedges from person→company invested_in (person_ontology_spine S3): dry-run default, APPLY=1 writes
> .venv/bin/python3 helpers/graph/derive_org_membership.py $(if $(APPLY),--apply,)
> @echo "✓ derive-org-membership done"

derive-event-participants: ## Materialise the event_participants n-ary store (person_ontology_spine S4): dry-run default, APPLY=1 writes
> .venv/bin/python3 helpers/graph/derive_event_participants.py $(if $(APPLY),--apply,)
> @echo "✓ derive-event-participants done"

derive-merger-hyperedges: ## Derive merger/demerger hyperedges from the triage decisions journal (merger_demerger_hyperedges S2): dry-run default, APPLY=1 writes
> .venv/bin/python3 helpers/graph/derive_merger_hyperedges.py $(if $(APPLY),--apply,)
> @echo "✓ derive-merger-hyperedges done"

embed-gc:       ## Evict dead rows from the shared embed cache (trial/spike leftovers); report-only + exit 1 when dead rows exist, APPLY=1 deletes + VACUUMs
> @echo "embed-gc: $(if $(APPLY),APPLY — deleting dead cache rows,report) mode"; \
> if [ -x .venv/bin/python3 ]; then MAINT_TARGET=embed-gc .venv/bin/python3 helpers/maintenance/gc_embed_cache.py $(if $(APPLY),--apply,); \
> else MAINT_TARGET=embed-gc python3 helpers/maintenance/gc_embed_cache.py $(if $(APPLY),--apply,); fi


search-tui:      ## Full-screen search front door — docs/scripts/notes/conversations indexes + ripwire + rg lanes; enter reads markdown via glow
>	@if [ -x .venv/bin/python3 ]; then .venv/bin/python3 helpers/misc/search_tui.py; else python3 helpers/misc/search_tui.py; fi
> @echo "✓ search TUI exited (doc/procedures/search.md §Search TUI)"

recompute-graph: ## Recompute all graph analytics and persist to graph_analytics
> python3 helpers/graph/algorithms.py --all --jobs 4 --apply
> @echo "✓ graph_analytics refreshed (degree, pagerank, betweenness, louvain, ..., link_prediction)"

recompute-hyper: ## Recompute HGX hyper metrics (hy-MMSBM communities + ho/s centralities) into graph_analytics
> python3 helpers/graph/hyper_communities.py --apply
> python3 helpers/graph/hyper_centralities.py --apply
> @echo "✓ hyper metrics refreshed (hypermmsbm_community, ho_pagerank, s_betweenness, s_closeness)"

derive-co-mentions: ## Derive co_mentioned_in edges from newsletter enhancement blocks
> python3 helpers/graph/derive_co_mentions.py --newsletter The_Chatter --apply
> @echo "✓ co_mentioned_in edges refreshed"

dup-census: ## Entity-name dup census: --check gate vs baseline / --plan merge plan (entity_name_dedup)
> .venv/bin/python3 helpers/graph/name_dedup.py $(or $(ARGS),--check)
> @echo "✓ dup-census done"

derive-relations: ## Extract jv_with/acquired/subsidiary_of/same_group/supplier_to/customer_of edges from newsletter prose
> python3 helpers/graph/extract_relations.py findata/The_Chatter findata/Points_And_Figures findata/The_PlotLines --apply
> @echo "✓ structured relation edges refreshed (unresolved -> findata/Misc/_pending_relations.txt; APPLIED, not a dry-run)"

derive-themes: ## Derive exposed_to (company -> theme) edges from company-note prose
> python3 helpers/graph/derive_themes.py --apply
> @echo "✓ theme entities + exposed_to edges refreshed"

derive-countries: ## Derive listed_in (company -> country) edges from exchange tickers (country layer C1)
> python3 helpers/graph/derive_countries.py --apply
> @echo "✓ country entities + listed_in edges refreshed (no-ticker -> findata/Misc/country_worklist.json)"

derive-events: ## Promote relation edges + extract guidance/management events into the events timeline table
> python3 helpers/graph/derive_events.py --apply
> @echo "✓ events table refreshed (acquisition/jv/guidance/management_change)"

derive-hyperedges: ## Regroup membership dyads into hyper_edges/hyper_incidences (sector/theme/country/group/edition)
> python3 helpers/graph/derive_hyperedges.py --apply
> @echo "✓ hyperedge incidence refreshed (hyper_edges + hyper_incidences)"

derive-indices: ## Derive index entities + listed_on_index edges from NSE index constituents (pair with graph-rebuild)
> python3 helpers/graph/derive_indices.py --apply
> @echo "✓ index entities + listed_on_index edges + converged sectors refreshed (pair with graph-rebuild)"

# Note-rendering path — DELIBERATELY DRY-RUN (2026-08-19): a bare `make
# derive-insights` previews what would be written and never mutates notes
# (mass note rewrites must be an explicit decision). The preview runs
# --stale-only (okf_activation I) so it shows the real incremental
# worklist: notes gated as evidence-unchanged vs would-render. To apply:
#   python3 helpers/graph/derive_insights.py findata --apply --stale-only
#       # the usual path: only notes whose evidence moved since their last
#       # render (first run after an OKF backfill re-renders all sourced
#       # notes; gating engages from the second run).
#   python3 helpers/graph/derive_insights.py findata --apply
#       # full re-render of every sourced note (forced pass).
# maint-full runs derive_insights with --apply --no-notes (DB-only) so
# housekeeping never mutates notes.
derive-insights: ## DRY-RUN stale-only preview of quotes/company_metrics + auto `## The Chatter` blocks (writes nothing; apply yourself — see comment above)

quote-coverage:  ## S0 quote capture coverage audit (advisory, read-only; per-note 95% tripwire + watchlist + salvage measurement)
> .venv/bin/python3 helpers/validators/quote_coverage_audit.py $$(QUOTECOV_ARGS)
> python3 helpers/graph/derive_insights.py findata --stale-only
> @echo "✓ dry-run only (nothing written) — apply: python3 helpers/graph/derive_insights.py findata --apply --stale-only"

derive-themes-rebuild: ## derive-themes + graph-rebuild — the paired run themes require (writes edges, then rebuilds the DuckDB cache to match)
> python3 helpers/graph/derive_themes.py --apply
> python3 helpers/graph/query.py rebuild
> @echo "✓ themes derived AND DuckDB cache rebuilt (derive-themes + graph-rebuild)"

derive-cited-in: ## Derive cited_in (note -> edition) edges from OKF sources[] frontmatter (okf_activation P)
> python3 helpers/graph/derive_cited_in.py --apply
> @echo "✓ edition entities + cited_in edges refreshed (pair with graph-rebuild)"

derive-cited-in-rebuild: ## derive-cited-in + graph-rebuild — the paired run cited_in requires (writes entities+edges, then rebuilds the DuckDB cache to match)
> python3 helpers/graph/derive_cited_in.py --apply
> python3 helpers/graph/query.py rebuild
> @echo "✓ editions derived AND DuckDB cache rebuilt (derive-cited-in + graph-rebuild)"

# READ-ONLY preview of the whole derive-* family (2026-08-19): the companion
# to the all-writes-explicit doctrine — every apply is opt-in, so this is the
# one-command "what would change" audit. Nothing is written anywhere:
# derive-insights previews the --stale-only worklist; extract_relations runs
# with --no-write-sidecar (its dry-run would otherwise APPEND to
# findata/_pending_relations.txt). Excluded: metrics-rebuild (no dry-run —
# network fetch + note writes) and suggest-relations (review tool, not a
# derivation preview).
derive-all: ## READ-ONLY preview of every derive-* step (dry-runs; nothing written, sidecar suppressed)
> @echo "=== derive-insights (stale-only worklist) ==="
> python3 helpers/graph/derive_insights.py findata --stale-only
> @echo "=== derive-relations (pending edges; sidecar suppressed) ==="
> python3 helpers/graph/extract_relations.py findata/The_Chatter findata/Points_And_Figures findata/The_Plotlines --no-write-sidecar
> @echo "=== derive-co-mentions ==="
> python3 helpers/graph/derive_co_mentions.py --newsletter The_Chatter
> @echo "=== derive-themes ==="
> python3 helpers/graph/derive_themes.py
> @echo "=== derive-cited-in ==="
> python3 helpers/graph/derive_cited_in.py
> @echo "=== derive-events ==="
> python3 helpers/graph/derive_events.py
> @echo "=== derive-hyperedges ==="
> python3 helpers/graph/derive_hyperedges.py
> @echo "✓ derive-all preview complete — nothing written"

frontend: ## Build the TypeScript frontend bundle into static/findata.bundle.js (needs Bun)
> cd frontend && bun install --frozen-lockfile && bun run build
> @echo "✓ frontend bundle rebuilt (static/findata.bundle.js)"

frontend-check: ## Type-check + prettier + guard behaviour/regeneration checks (fast, needs Bun)
> cd frontend && bun x tsc --noEmit
> cd frontend && bun x prettier --check src types
> cd frontend && bun test tests
> .venv/bin/python3 helpers/misc/gen_api_guards.py --check
> @echo "✓ frontend type-check + prettier + guard behaviour + regeneration passed (strict)"

fold-identifiers: ## Fold exchange ISIN/CIK values into the entity identifier registry
> python3 helpers/maintenance/fold_identifiers.py --apply
> @echo "✓ identifier registry folded from exchange sources"

format:         ## Normalize Python formatting repo-wide (ruff format; fix for the test_lint_gates.py format gate)
> ruff format .
> @echo "✓ ruff format applied (make lint + pytest tests/test_lint_gates.py stay the gates)"

lint:           ## Run ruff linter (replaces flake8)
> ruff check .

types:          ## Run ty type checker on helpers + app.py + Mojo Python (Astral uv+ruff stack)
> ty check helpers app.py Mojo/

# The EXPANDED ty surface (tests/): single source of truth for the exact
# extra-search-path flag soup + ty.tests.toml config — run it standalone
# after a major feature instead of reconstructing the command
# (tests/run_gate_report.py's advisory ty-tests step calls THIS target).
# Distinct from `make types` (qa-gated, production code only): tests use
# sys.path bootstraps + mock proxies ty can't reason about statically, so
# known test idioms are downgraded to warnings (non-blocking, --exit-zero-
# on-warning); real type errors in test code still exit non-zero here.
types-tests:    ## Run EXPANDED ty checks over tests/ (advisory-grade, warnings non-blocking; TYPES_TESTS_FMT=concise for one-line-per-diagnostic logs — the make advisory ty-tests step uses that)
> ty check tests --extra-search-path helpers --extra-search-path helpers/core --extra-search-path helpers/maintenance --extra-search-path helpers/misc --config-file ty.tests.toml --output-format $(TYPES_TESTS_FMT) --exit-zero-on-warning
> @echo "✓ ty expanded test checks passed (warnings non-blocking; config: ty.tests.toml)"

lint-audit:     ## Run ruff S/UP/C901 audits (security + modernization + complexity) — Bandit/Refurb/Radon equivs
> ruff check --select S,UP,C901 .

# ocr_review_pipeline S3 deterministic remainder (advisory, never a qa leg):
# stgit->ref mapping + delegation selection roster + the selection-teeth
# assertion (a rule-covered family with diff traffic must be visible — the
# defect class that shipped the inert CSR lane). --stack N reviews the whole
# applied stack (HEAD~N..HEAD).
# Range args shared by review-patch/review-scan. FROM=<base> TO=<head> is the
# only encoding for a landed pair that is not HEAD-relative: --stack is always
# HEAD~N..HEAD and --commit is a single ref, so 4fd3a20f..bda590995 is
# unencodable without it. FROM without TO aborts here rather than reaching
# argparse with a dangling half-range.
REVIEW_RANGE_ARGS = $(if $(FROM),$(if $(TO),--from $(FROM) --to $(TO),$(error FROM requires TO: the review range is a pair)),$(if $(COMMIT),--commit $(COMMIT),$(if $(STACK),--stack $(STACK),)))

review-patch:   ## OCR delegation selection roster + native scan over the review diff (advisory; asserts tests/Mojo visibility per rule.json; STACK=N for HEAD~N..HEAD, COMMIT=<sha> for one commit, FROM=<base> TO=<head> for a landed pair, OFFLINE=1 to skip the scan's network legs)
> .venv/bin/python3 helpers/misc/review_selection.py $(REVIEW_RANGE_ARGS)
> @.venv/bin/python3 helpers/misc/review_scan.py $(REVIEW_RANGE_ARGS)$(if $(OFFLINE), --offline,)

review-scan:    ## Native scanner roster over the review diff (advisory, exit 0 always; bandit/shellcheck/sqlfluff/semgrep/osv on changed lines; STACK=N / COMMIT=<sha> / FROM=<base> TO=<head> / OFFLINE=1; proposal review_scan_leg)
> .venv/bin/python3 helpers/misc/review_scan.py $(REVIEW_RANGE_ARGS)$(if $(OFFLINE), --offline,)

# security_audit_adoption (A2/A9): the audit lane runs per surface family, not
# per diff — the plan is the work order, the findings file is the run's output,
# the scoped check is the close-out gate.
SCOPE ?= app

security-audit: ## Security audit lane for one surface family: prior-coverage plan → run findings check → scoped close-out gate (SCOPE=app|extractors|..., RUN=<id> defaults to <today>-<scope>-1, STRICT=1 makes unaccounted classes errors)
> .venv/bin/python3 helpers/validators/coverage_ledger.py plan --scope $(SCOPE)
> @f="doc/security/findings/$(or $(RUN),$(shell date +%Y-%m-%d)-$(SCOPE)-1).json"; \
>   if [ -f "$$f" ]; then .venv/bin/python3 helpers/validators/security_findings.py "$$f" || exit 1; \
>   else echo "security-audit: no findings file yet — $$f"; fi
> .venv/bin/python3 helpers/validators/coverage_ledger.py check --scope $(SCOPE)$(if $(STRICT), --strict,)

security-critic: ## Coverage critic: uncovered (surface, class) pairs, stale fingerprints, declared exclusions, and confirmed findings the ledger does not back (SCOPE=..., RUN=<id> to cross-check a run's findings, JSON=1, STRICT=1 to fail on an INCOMPLETE verdict)
> .venv/bin/python3 helpers/validators/coverage_ledger.py critic --scope $(SCOPE)$(if $(RUN), --findings doc/security/findings/$(RUN).json,)$(if $(JSON), --json,)$(if $(STRICT), --strict,)

security-queue: ## Regenerate the gap work queue (triage idiom): JSONL of (surface, class) pairs with no ledger row and no exclusion, reason-coded (SCOPE=..., OUT=<path> defaults to doc/local/security/coverage_queue.jsonl)
> .venv/bin/python3 helpers/validators/coverage_ledger.py queue --scope $(SCOPE)$(if $(OUT), --out $(OUT),)

security-apply: ## Apply gap decisions to the ledger (DECISIONS=<jsonl>, dry-run unless WRITE=1): cover needs verdict+reviewed, exclude/defer need a reason, bad_pair is echoed and never applied
> .venv/bin/python3 helpers/validators/coverage_ledger.py apply$(if $(DECISIONS), --decisions $(DECISIONS))$(if $(WRITE), --write)$(if $(SCOPE), --scope $(SCOPE),)

security-scopes: ## Print the scope-family security inventories (SCOPE=graph|harness|desktop|frontend|data|extractors); sync checks the inventory areas against the ledger's surface names
> .venv/bin/python3 helpers/validators/security_scopes.py $(if $(SYNC), sync, inventory)$(if $(SCOPE), --scope $(SCOPE),)

security-verify: ## Verifier role (A6): emit a blind brief for ONE candidate (FP=<fingerprint>, RUN=<id>), or merge a verifier result with RECORD=<file> OUT=<file> (the input findings file is never overwritten)
> @test -n "$(FP)" || { echo "security-verify: needs FP=<fingerprint> (see security-critic / the findings file)"; exit 2; }
> @f="doc/security/findings/$(or $(RUN),$(shell date +%Y-%m-%d)-$(SCOPE)-1).json"; \
>   if [ -n "$(RECORD)" ]; then \
>     .venv/bin/python3 helpers/validators/security_findings.py brief "$$f" --fingerprint "$(FP)" --record "$(RECORD)" --out "$(or $(OUT),$${f%.json}.merged.json)"; \
>   else \
>     .venv/bin/python3 helpers/validators/security_findings.py brief "$$f" --fingerprint "$(FP)"$(if $(JSON), --json,); \
>   fi

schema-dump:    ## Regenerate schema/<engine>/<db>.sql from the live memory/ DBs (proposal schema_ddl_review_surface)
> .venv/bin/python3 helpers/misc/schema_dump.py

schema-check:   ## Check tracked schema/ DDL against the live memory/ DBs (exit 1 on drift; fix: make schema-dump)
> .venv/bin/python3 helpers/misc/schema_dump.py --check

# markdown_lint_adoption S1: the helper owns node detection (SKIP without
# Node) + the version pin + digest output; LINT-ONLY — no --fix surface
# (findata is co-owned by generators; proposal §5).
md-lint:        ## Markdown lint via pinned markdownlint-cli2 — doc/ prose base + findata Tier-1 defect rules (qa-gated; needs Node, SKIP without; proposal: markdown_lint_adoption)
> python3 helpers/misc/markdown_lint.py

deptry:         ## Run deptry dependency-health scan (unused/undeclared/transitive deps)
> deptry .

advisory:       ## Run advisory (non-gating) checks in PARALLEL (default 4 jobs; override: make advisory -j N): ty on tests, live invariants, frontend, graph algos, analytics, suggestions, doc/script/note/memory-search freshness checks, lint-audit, secret-scan, cargo-audit, mojo-test, fuzz, license-check (appends outputs/advisory_report.md)
> python3 tests/run_gate_report.py advisory
> @echo "✓ Advisory checks complete (appended to outputs/advisory_report.md; these do NOT block \`make qa\`)"
