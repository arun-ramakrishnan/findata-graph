# Tab-free Makefile: use '>' as the recipe prefix (GNU Make).
.RECIPEPREFIX := >

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
export PATH := /home/arun/Research/MCP/pdf-ocr-obsidian/.venv/bin:$(PATH)

.PHONY: help qa test live-invariants perf cover fuzz integration snapshot snapshot-check snapshot-restore sync-tags sync-sector-links static-checks install-dev graph-smoke graph-stats graph-algos graph-rebuild update-extensions recompute-graph derive-relations derive-co-mentions derive-themes derive-events derive-insights derive-themes-rebuild derive-cited-in derive-cited-in-rebuild derive-all frontend frontend-check maint maint-full metrics-rebuild relations-enrich lint types types-tests lint-audit deptry advisory secret-scan script-search-rebuild triage-relations live-invariants

help:           ## Show available targets (alphabetical; entries generated from the ## annotations — keep both in sync)
> @echo "FinData targets (alphabetical):"
> @echo "  advisory                 Run advisory (non-gating) checks in PARALLEL (default 4 jobs; override: make advisory -j N): ty on tests, live invariants, frontend, graph algos, analytics, suggestions, integration, lint-audit (appends advisory_report.txt)"
> @echo "  analytics                Read-only analytics over the git-tracked Parquet snapshot (A3; arg = report name)"
> @echo "  cover                    Run all tests with coverage over helpers/ (branch + missing-line report)"
> @echo "  deptry                   Run deptry dependency-health scan (unused/undeclared/transitive deps)"
> @echo "  derive-all               READ-ONLY preview of every derive-* step (dry-runs; nothing written, sidecar suppressed)"
> @echo "  derive-cited-in          Derive cited_in (note -> edition) edges from OKF sources[] frontmatter (okf_activation P)"
> @echo "  derive-cited-in-rebuild  derive-cited-in + graph-rebuild — the paired run cited_in requires (writes entities+edges, then rebuilds the DuckDB cache to match)"
> @echo "  derive-co-mentions       Derive co_mentioned_in edges from newsletter enhancement blocks"
> @echo "  derive-events            Promote relation edges + extract guidance/management events into the events timeline table"
> @echo "  derive-insights          DRY-RUN stale-only preview of quotes/company_metrics + auto '## The Chatter' blocks (writes nothing; apply yourself — see comment above)"
> @echo "  derive-relations         Extract jv_with/acquired/subsidiary_of/same_group/supplier_to/customer_of edges from newsletter prose"
> @echo "  derive-themes            Derive exposed_to (company -> theme) edges from company-note prose"
> @echo "  derive-themes-rebuild    derive-themes + graph-rebuild — the paired run themes require (writes edges, then rebuilds the DuckDB cache to match)"
> @echo "  frontend                 Build the TypeScript frontend bundle into static/findata.bundle.js (needs Node)"
> @echo "  frontend-check           Type-check the TypeScript frontend without emitting (fast, needs Node)"
> @echo "  fuzz                     Run Hypothesis property-based tests (deterministic seed for reproducibility)"
> @echo "  graph-algos              Smoke test the Onager algorithm layer (all 14 metrics, no writes)"
> @echo "  graph-rebuild            Rebuild the disk-based DuckDB cache from SQLite (run after parse_newsletter --apply / derive-relations)"
> @echo "  graph-smoke              Quick smoke test of the graph query layer (sector-of + neighbors)"
> @echo "  graph-stats              Print a one-shot summary of the graph state (entities, edges, sectors, hygiene)"
> @echo "  install-dev              Install dev dependencies (uv sync; prunes undeclared packages)"
> @echo "  integration              Run end-to-end cross-component pipeline tests (parse_newsletter, API bridge, etc.; appends integration_report.txt)"
> @echo "  lint                     Run ruff linter (replaces flake8)"
> @echo "  lint-audit               Run ruff S/UP/C901 audits (security + modernization + complexity) — Bandit/Refurb/Radon equivs"
> @echo "  live-invariants          Run ONLY the live-marked invariant tests (-m live; skip-safe on pristine clone)"
> @echo "  maint                    Routine maintenance: db_maint + snapshot + graph-rebuild (always-safe)"
> @echo "  maint-full               Post-ingest re-derivation: maint + TIER2_STEPS (sync-tags, sector gates, note-search, company-embeddings, doc-search, analytics, insights, events, re-snapshot)"
> @echo "  metrics-rebuild          Refresh company financials + industry edges from yfinance (~1 min, 931 tickers)"
> @echo "  near-duplicates          Report near-duplicate note pairs above cosine 0.9 (rename tripwire; READ-ONLY)"
> @echo "  perf                     Run wall-clock perf benchmarks, print timing table, and append to perf_report.txt"
> @echo "  qa                       Run lint + types + deptry + static + pytest + notes + integrity + snapshot in PARALLEL (default 4 jobs; override: make qa -j N; run-all — failures reported at the end; appends qa_report.txt)"
> @echo "  recompute-graph          Recompute all graph analytics and persist to graph_analytics"
> @echo "  script-search-rebuild    Rebuild the script metadata index (script_search sidecar; query via helpers/misc/script_query.py)"
> @echo "  secret-scan              Incremental git-history secret scan (state under .git/secret-scan/)"
> @echo "  snapshot                 Refresh the versioned DB snapshot"
> @echo "  snapshot-check           Verify the snapshot round-trips against the live DB"
> @echo "  snapshot-restore         Rebuild memory/ DBs from the git-tracked Parquet snapshot (clobbers live DBs)"
> @echo "  static-checks            Fast static checks (syntax, shebangs, YAML, artifacts, merge markers)"
> @echo "  suggest-relations        Print link-prediction relation suggestions (C2; append with --append)"
> @echo "  sync-sector-links        WRITE the auto company index into sector notes (explicit; maint-full only checks staleness)"
> @echo "  sync-tags                Rebuild entity_tags from note YAML (mirrors entity_type/sector/market_cap/subsector)"
> @echo "  test                     pytest unit tests only (no live DB, no slow benchmarks)"
> @echo "  triage-relations         Triage the _pending_relations queue: report + bucketed decisions file (pending_relations_triage)"
> @echo "  types                    Run ty type checker on helpers + app.py (Astral uv+ruff stack)"
> @echo "  types-tests              Run EXPANDED ty checks over tests/ (advisory-grade, warnings non-blocking; the make advisory ty-tests step calls THIS target)"
> @echo "  update-extensions        Update all installed DuckDB extensions to latest (weekly cadence)"

static-checks:  ## Fast static checks (syntax, shebangs, YAML, artifacts, merge markers)
> python3 helpers/validators/static_checks.py

qa:             ## Run lint + types + deptry + static + pytest + notes + integrity + snapshot in PARALLEL (default 4 jobs; override: make qa -j N; run-all — failures reported at the end; appends qa_report.txt)
> python3 tests/run_gate_report.py qa
> @echo "✓ QA passed (lint + types + deptry + static + pytest + notes + integrity + snapshot; appended to qa_report.txt)"

test:           ## pytest unit tests only (no live DB, no slow benchmarks)
> pytest -m "not live"

live-invariants: ## Run ONLY the live-marked invariant tests (-m live; skip-safe on pristine clone)
> pytest -m live 
> @echo "✓ live invariant tests passed"

perf:           ## Run wall-clock perf benchmarks, print timing table, and append to perf_report.txt
> python3 tests/run_perf_benchmarks.py
> @echo "✓ performance benchmarks passed (see table above; appended to perf_report.txt)"

cover:          ## Run all tests with coverage over helpers/ (branch + missing-line report)
> pytest --cov=helpers --cov-branch --cov-report=term-missing --cov-report=html
> @echo "✓ coverage report written to htmlcov/index.html"

fuzz:           ## Run Hypothesis property-based tests (deterministic seed for reproducibility)
> pytest tests/test_fuzz_*.py -v

integration:    ## Run end-to-end cross-component pipeline tests (parse_newsletter, API bridge, etc.; appends integration_report.txt)
> python3 tests/run_gate_report.py integration
> @echo "✓ Integration tests passed (appended to integration_report.txt)"

snapshot:       ## Refresh the versioned DB snapshot
> python3 helpers/maintenance/snapshot_db.py
> @echo "✓ Snapshots refreshed (snapshots/parquet/ [git] + db-backup/*.gz [local])"

snapshot-check: ## Verify the snapshot round-trips against the live DB
> python3 helpers/maintenance/snapshot_db.py --check

snapshot-restore: ## Rebuild memory/ DBs from the git-tracked Parquet snapshot (clobbers live DBs)
> python3 helpers/maintenance/snapshot_db.py --restore --force
> @echo "✓ Live DBs rebuilt from snapshots/parquet/"

maint:          ## Routine maintenance: db_maint + snapshot + graph-rebuild (always-safe)
> python3 helpers/maintenance/maint.py
> @echo "✓ Routine maintenance complete"

maint-full:     ## Post-ingest re-derivation: maint + TIER2_STEPS (authoritative list: helpers/maintenance/maint.py)
> python3 helpers/maintenance/maint.py --full
> @echo "✓ Full maintenance complete"

metrics-rebuild: ## Refresh company financials + notes from yfinance (~1 min, 931 tickers)
> python3 helpers/maintenance/enrich_from_yfinance.py
> @echo "✓ yfinance enrichment complete (competes_with moved to relations-enrich; run 'make graph-rebuild' to refresh DuckDB edges)"

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
> python3 helpers/core/sync_tags.py
> @echo "✓ entity_tags synced from notes"

sync-sector-links: ## WRITE the auto company index into sector notes (explicit; maint-full only checks staleness)
> python3 helpers/maintenance/sync_sector_wikilinks.py
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

suggest-relations: ## Print link-prediction relation suggestions (C2; append with --append)
> python3 helpers/graph/suggest_relations.py

triage-relations: ## Triage the _pending_relations queue: report + bucketed decisions file (pending_relations_triage)
> python3 helpers/graph/triage_pending_relations.py
> @echo "✓ triage report + decisions file written (annotate decisions, then --apply-decisions --write)"

graph-algos:    ## Smoke test the Onager algorithm layer (all 14 metrics, no writes)
> python3 helpers/graph/algorithms.py --all --no-apply
> @echo "✓ Onager graph-algorithm layer smoke test passed (all metrics, nothing written)"

graph-rebuild:  ## Rebuild the disk-based DuckDB cache from SQLite (run after parse_newsletter --apply / derive-relations)
> python3 helpers/graph/query.py rebuild
> @echo "✓ DuckDB graph cache rebuilt (memory/graph.duckdb)"

near-duplicates: ## Report near-duplicate note pairs above cosine 0.9 (rename tripwire; READ-ONLY — triage by hand, remediation is user-held)
> python3 helpers/graph/query.py near-duplicates --min-sim 0.9
> @echo "✓ Near-duplicate report complete (nothing written)"

update-extensions: ## Update all installed DuckDB extensions to latest (weekly cadence)
> python3 helpers/graph/query.py update-extensions
> @echo "✓ DuckDB extensions checked/updated"

secret-scan: ## Incremental git-history secret scan (state under .git/secret-scan/)
> python3 helpers/misc/git_secret_scan.py
> @echo "✓ Secret scan complete (incremental; state: .git/secret-scan/state.json)"

script-search-rebuild: ## Rebuild the script metadata index (script_search sidecar; query via helpers/misc/script_query.py)
> python3 helpers/maintenance/rebuild_script_search.py
> @echo "✓ script_search index rebuilt (memory/script_search.db; gate: make perf)"

recompute-graph: ## Recompute all graph analytics and persist to graph_analytics
> python3 helpers/graph/algorithms.py --all --apply
> @echo "✓ graph_analytics refreshed (degree, pagerank, betweenness, louvain, ..., link_prediction)"

derive-co-mentions: ## Derive co_mentioned_in edges from newsletter enhancement blocks
> python3 helpers/graph/derive_co_mentions.py --newsletter The_Chatter --apply
> @echo "✓ co_mentioned_in edges refreshed"

derive-relations: ## Extract jv_with/acquired/subsidiary_of/same_group/supplier_to/customer_of edges from newsletter prose
> python3 helpers/graph/extract_relations.py findata/The_Chatter findata/Points_And_Figures findata/The_PlotLines --apply
> @echo "✓ structured relation edges refreshed (unresolved -> findata/_pending_relations.txt)"

derive-themes: ## Derive exposed_to (company -> theme) edges from company-note prose
> python3 helpers/graph/derive_themes.py --apply
> @echo "✓ theme entities + exposed_to edges refreshed"

derive-events: ## Promote relation edges + extract guidance/management events into the events timeline table
> python3 helpers/graph/derive_events.py --apply
> @echo "✓ events table refreshed (acquisition/jv/guidance/management_change)"

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
> @echo "✓ derive-all preview complete — nothing written"

frontend: ## Build the TypeScript frontend bundle into static/findata.bundle.js (needs Node)
> cd frontend && npm ci && npm run build
> @echo "✓ frontend bundle rebuilt (static/findata.bundle.js)"

frontend-check: ## Type-check the TypeScript frontend without emitting (fast, needs Node)
> cd frontend && npx tsc --noEmit
> @echo "✓ frontend type-check passed (strict)"

lint:           ## Run ruff linter (replaces flake8)
> ruff check .

types:          ## Run ty type checker on helpers + app.py (Astral uv+ruff stack)
> ty check helpers app.py

# The EXPANDED ty surface (tests/): single source of truth for the exact
# extra-search-path flag soup + ty.tests.toml config — run it standalone
# after a major feature instead of reconstructing the command
# (tests/run_gate_report.py's advisory ty-tests step calls THIS target).
# Distinct from `make types` (qa-gated, production code only): tests use
# sys.path bootstraps + mock proxies ty can't reason about statically, so
# known test idioms are downgraded to warnings (non-blocking, --exit-zero-
# on-warning); real type errors in test code still exit non-zero here.
types-tests:    ## Run EXPANDED ty checks over tests/ (advisory-grade, warnings non-blocking; the make advisory ty-tests step calls THIS target)
> ty check tests --extra-search-path helpers --extra-search-path helpers/core --extra-search-path helpers/maintenance --extra-search-path helpers/misc --config-file ty.tests.toml --exit-zero-on-warning
> @echo "✓ ty expanded test checks passed (warnings non-blocking; config: ty.tests.toml)"

lint-audit:     ## Run ruff S/UP/C901 audits (security + modernization + complexity) — Bandit/Refurb/Radon equivs
> ruff check --select S,UP,C901 .

deptry:         ## Run deptry dependency-health scan (unused/undeclared/transitive deps)
> deptry .

advisory:       ## Run advisory (non-gating) checks in PARALLEL (default 4 jobs; override: make advisory -j N): ty on tests, live invariants, frontend, graph algos, analytics, suggestions, integration, lint-audit (appends advisory_report.txt)
> python3 tests/run_gate_report.py advisory
> @echo "✓ Advisory checks complete (appended to advisory_report.txt; these do NOT block \`make qa\`)"
