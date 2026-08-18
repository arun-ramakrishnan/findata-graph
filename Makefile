# Tab-free Makefile: use '>' as the recipe prefix (GNU Make).
.RECIPEPREFIX := >

# Prefer the project venv if present (so `python3` / `pytest` resolve to
# .venv/bin without needing `source .venv/bin/activate` in the parent shell).
# PREPEND .venv/bin so it takes precedence over the system python3 (which
# lacks duckdb/pytest/etc.). Harmless if .venv doesn't exist — the entry
# is just a no-op directory on PATH and lookup falls through to the system.
export PATH := /home/arun/Research/MCP/pdf-ocr-obsidian/.venv/bin:$(PATH)

.PHONY: help qa test live-invariants perf cover fuzz integration snapshot snapshot-check snapshot-restore sync-tags sync-sector-links static-checks install-dev graph-smoke graph-stats graph-algos graph-rebuild update-extensions recompute-graph derive-relations derive-co-mentions derive-themes derive-events derive-insights derive-themes-rebuild frontend frontend-check maint maint-full metrics-rebuild lint types lint-audit deptry advisory secret-scan analytics suggest-relations live-invariants

help:           ## Show available targets
> @echo "FinData QA / maintenance targets:"
> @echo "  qa              run lint + static checks + pytest + notes + integrity + snapshot (the full gate)"
> @echo "  advisory        run NON-gating advisory checks (lint-audit, frontend-check, graph algos/smoke, integration)"
> @echo "  static-checks   fast static checks (syntax, shebangs, YAML, artifacts, merge markers)"
> @echo "  sync-tags       rebuild entity_tags table from note YAML (run after editing notes)"
> @echo "  sync-sector-links regenerate the auto company index in each sector note"
> @echo "  test            pytest unit tests (deselects 'live'; see also: perf, integration)"
> @echo "  live-invariants pytest -m live only (~60s; skip-safe without the DBs)"
> @echo "  perf            run wall-clock perf benchmarks, print timing table, append to perf_report.txt"
> @echo "  cover           run tests with coverage over helpers/ (branch + missing-line report)"
> @echo "  fuzz            run Hypothesis property-based tests with a fixed seed (deterministic)"
> @echo "  integration     run end-to-end cross-component pipeline tests (mocked, fast)"
> @echo "  snapshot        refresh snapshots: git-tracked Parquet (snapshots/) + local gzip (db-backup/)"
> @echo "  snapshot-check  verify BOTH gzip + Parquet snapshots round-trip"
> @echo "  snapshot-restore  rebuild memory/ DBs from snapshots/parquet/ (needs --force semantics)"
> @echo "  maint           routine maintenance: db_maint + snapshot + graph-rebuild"
> @echo "  metrics-rebuild  refresh company financials + industry edges from yfinance"
> @echo "  maint-full      maint + sync-tags + recompute-graph + re-snapshot (post-ingest cleanup)"
> @echo "  install-dev     install dev dependencies"
> @echo "  graph-smoke     quick smoke test of the graph query layer"
> @echo "  graph-stats     print a one-shot summary of the graph state"
> @echo "  graph-algos     smoke test of the algorithm dispatcher (pagerank + link-predict)"
> @echo "  graph-rebuild   rebuild the disk-based DuckDB cache (run after parse_newsletter/derive-relations)"
> @echo "  update-extensions  update all installed DuckDB extensions to latest"
> @echo "  recompute-graph write all algorithm metrics to graph_analytics"
> @echo "  secret-scan     incremental git-history secret scan (helpers/misc/git_secret_scan.py)"
> @echo "  derive-themes-rebuild  derive theme entities+edges AND rebuild the DuckDB cache (the paired run themes require)"
> @echo "  frontend        build the TypeScript frontend bundle into static/findata.bundle.js (needs Node)"
> @echo "  frontend-check  type-check the TypeScript frontend without emitting (needs Node)"
> @echo "  types           run ty type checker over helpers/ + app.py (Astral uv+ruff stack)"
> @echo "  lint-audit      ruff S/UP/C901 audits (security, pyupgrade, complexity)"
> @echo "  deptry           dependency-health scan (unused/undeclared/transitive deps)"

static-checks:  ## Fast static checks (syntax, shebangs, YAML, artifacts, merge markers)
> python3 helpers/validators/static_checks.py

qa:             ## Run lint + types + deptry + static checks + pytest + notes + integrity + snapshot checks
> ruff check .
> ty check helpers app.py
> deptry .
> python3 helpers/validators/static_checks.py
> pytest -m "not live"
> python3 helpers/validators/verify_notes.py
> python3 helpers/misc/database_integrity_check.py
> python3 helpers/maintenance/snapshot_db.py --check
> @echo "✓ QA passed (lint + types + deptry + static + pytest + notes + integrity + snapshot)"

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

integration:    ## Run end-to-end cross-component pipeline tests (parse_newsletter, API bridge, etc.)
> pytest -m integration -v
> @echo "✓ Integration tests passed"

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

maint-full:     ## Post-ingest cleanup: maint + sync-tags + recompute-graph + re-snapshot
> python3 helpers/maintenance/maint.py --full
> @echo "✓ Full maintenance complete"

metrics-rebuild: ## Refresh company financials + industry edges from yfinance (~1 min, 931 tickers)
> python3 helpers/maintenance/enrich_from_yfinance.py
> @echo "✓ yfinance enrichment complete (run 'make graph-rebuild' to refresh DuckDB edges)"

sync-tags:      ## Rebuild entity_tags from note YAML (mirrors entity_type/sector/market_cap/subsector)
> python3 helpers/core/sync_tags.py
> @echo "✓ entity_tags synced from notes"

sync-sector-links: ## Regenerate the auto company index in each sector note (Bundle H3)
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

graph-algos:    ## Smoke test the Onager algorithm layer (all 14 metrics, no writes)
> python3 helpers/graph/algorithms.py --all --no-apply
> @echo "✓ Onager graph-algorithm layer smoke test passed (all metrics, nothing written)"

graph-rebuild:  ## Rebuild the disk-based DuckDB cache from SQLite (run after parse_newsletter --apply / derive-relations)
> python3 helpers/graph/query.py rebuild
> @echo "✓ DuckDB graph cache rebuilt (memory/graph.duckdb)"

update-extensions: ## Update all installed DuckDB extensions to latest (weekly cadence)
> python3 helpers/graph/query.py update-extensions
> @echo "✓ DuckDB extensions checked/updated"

secret-scan: ## Incremental git-history secret scan (state under .git/secret-scan/)
> python3 helpers/misc/git_secret_scan.py
> @echo "✓ Secret scan complete (incremental; state: .git/secret-scan/state.json)"

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

# Note-rendering path: this target renders `## The Chatter` + `## Key Figures`
# blocks into company notes. maint-full runs derive_insights with --no-notes
# (DB-only) so housekeeping never mutates notes; run THIS target standalone to
# refresh the rendered blocks (and to self-heal any marker-nesting collisions).
derive-insights: ## Extract concall quotes + financial magnitudes into quotes/company_metrics tables and render auto `## The Chatter` blocks into company notes
> python3 helpers/graph/derive_insights.py findata --apply
> @echo "✓ quotes + company_metrics refreshed; auto chatter blocks rendered (hand-written blocks preserved)"

derive-themes-rebuild: ## derive-themes + graph-rebuild — the paired run themes require (writes edges, then rebuilds the DuckDB cache to match)
> python3 helpers/graph/derive_themes.py --apply
> python3 helpers/graph/query.py rebuild
> @echo "✓ themes derived AND DuckDB cache rebuilt (derive-themes + graph-rebuild)"

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

lint-audit:     ## Run ruff S/UP/C901 audits (security + modernization + complexity) — Bandit/Refurb/Radon equivs
> ruff check --select S,UP,C901 .

deptry:         ## Run deptry dependency-health scan (unused/undeclared/transitive deps)
> deptry .

advisory:       ## Run advisory (non-gating) checks: ty on tests, live invariants, frontend, graph algos, analytics, suggestions, integration, lint-audit
> ty check tests --extra-search-path helpers --extra-search-path helpers/core --extra-search-path helpers/maintenance --extra-search-path helpers/misc --config-file ty.tests.toml --exit-zero-on-warning || true
> $(MAKE) -k live-invariants frontend-check graph-algos analytics suggest-relations integration lint-audit
> @echo "✓ Advisory checks complete (these do NOT block \`make qa\`)"
