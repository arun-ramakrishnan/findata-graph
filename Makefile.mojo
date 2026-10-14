# Mojo build — standalone makefile invoked from the main Makefile:
#   make mojo-build   -> compile every Mojo/src/*/*.mojo into Mojo/bin/
#   make mojo-test    -> run every Mojo/tests/*.mojo (TestSuite runners)
# Repo-root cwd assumed (the main Makefile delegates with $(MAKE) -f).
# Tab-free like the main Makefile: use '>' as the recipe prefix.
.RECIPEPREFIX := >

# Layout: sources live in Mojo/src/<pkg>/<name>.mojo (one directory per
# package; Mojo/src/common holds shared code + runnable probe programs,
# e.g. the concurrency probes). Binaries are FLAT
# in Mojo/bin/<name> (PATH_add Mojo/bin exposes them directly), so rules
# are generated per source file. Note: basenames must be unique across
# packages — a collision would silently map two sources to one binary.
MOJO := .venv/bin/mojo
MOJO_DIR := Mojo
MOJO_SRC := $(MOJO_DIR)/src
MOJO_BIN := $(MOJO_DIR)/bin
MOJO_PACKAGES := $(wildcard $(MOJO_SRC)/*)
MOJO_SOURCES := $(wildcard $(MOJO_SRC)/*/*.mojo)
# Vendored third-party libs live outside src/ (no main(), must not hit
# the flat-binary build rules); their import roots join the flags only.
MOJO_VENDOR_SRC := $(MOJO_DIR)/vendor/mojo-yaml/src
MOJO_VENDOR_L0 := $(MOJO_DIR)/vendor/mojo-intel-gpu/src
MOJO_TARGETS := $(addprefix $(MOJO_BIN)/,$(foreach s,$(MOJO_SOURCES),$(basename $(notdir $(s)))))
MOJO_TESTS := $(wildcard $(MOJO_DIR)/tests/*.mojo)
# Every src/<pkg> dir is an import root, so tests can `import <module>`
# from any package (and packages can import each other's modules).
MOJO_IMPORT_FLAGS := $(foreach d,$(MOJO_PACKAGES),-I $(d)) -I $(MOJO_VENDOR_SRC) -I $(MOJO_VENDOR_L0)

.PHONY: mojo-build mojo-test mojo-bench mojo-format mojo-parity

# One-shot formatter (the fix command for the tests/test_lint_gates.py
# mojo-format gate, which checks a formatted COPY against the tree and never
# mutates sources). No -I flags needed: the formatter is purely lexical.
mojo-format:
> $(MOJO) format $(MOJO_SRC)/*/*.mojo $(MOJO_TESTS)

mojo-build: $(MOJO_TARGETS)
> @echo "✓ Mojo binaries up to date in $(MOJO_BIN)/"

# Mojo 1.0: `mojo test` CLI is gone — test files carry their own
# TestSuite.discover_tests runner and are executed with `mojo run`.
mojo-test:
> @for t in $(MOJO_TESTS); do \
>   echo "  mojo run $$t"; \
>   $(MOJO) run $(MOJO_IMPORT_FLAGS) "$$t" || exit 1; \
> done
> @echo "✓ Mojo tests passed ($(words $(MOJO_TESTS)) files)"

# Golden-parity differential: the Mojo port vs the Python original.
# This is the ONLY mechanism that detects drift between
# integrity_check.mojo and database_integrity_check.py. It used to be
# reachable only through the `mojo-bench` PERF harness, where it ran
# whenever an operator happened to run a benchmark and never as a gate —
# which is how the Mojo copy drifted 26 days out of date behind a
# Python checker that moved 8 times. Now a qa leg (2026-10-14).
#
# PATH must carry .venv/bin: the Mojo/Python bridge resolves libpython
# from the python3 on PATH, and without the venv it aborts with
# `symbol not found: Py_Initialize` (the trap _venv_env() in
# bench/run_bench.py documents).
#
# Output is filtered and each line capped at 300 chars: the mismatch
# values for `fuzzy_duplicates.pairs`, `normalization.duplicates` and
# `duplicate_tickers.groups` run to tens of KB and would otherwise land
# verbatim in the gate report. The exit code is the BINARY's, not grep's.
# (`duplicate_tickers.groups` and `normalization.errors` disagree because
# Python suppresses duplicate-ticker groups the Mojo copy still counts.)
MOJO_PARITY_LOG = $${TMPDIR:-/mnt/data/tmp}/mojo_parity.log
.PHONY: mojo-parity
mojo-parity: $(MOJO_BIN)/integrity_check
> @PATH="$(CURDIR)/.venv/bin:$$PATH" MOJO_INTEGRITY_PARITY=1 $(MOJO_BIN)/integrity_check > $(MOJO_PARITY_LOG) 2>&1; rc=$$?; \
>   grep -E '^(cold spawn|python original|parity:|Entities:|GOLDEN PARITY|EXIT )|mojo=|MISSING in mojo|EXTRA in mojo' $(MOJO_PARITY_LOG) | cut -c1-300 || true; \
>   if [ $$rc -ne 0 ]; then echo "  full log: $(MOJO_PARITY_LOG)"; fi; \
>   exit $$rc

# Harness: Mojo/bench/run_bench.py runs every bench leg and appends the
# table to Mojo/bench/bench_report.txt. Legs: cosine-knn (the 4-way
# py_math/py_json/sqlite-vec/mojo_simd comparison via bench_cosine_knn.py),
# analyzer (multi-tier compute table), pool-4x (bench_pool x4 workers,
# synthetic matrix), regex-bridge (mojo_regex_probe), yaml-corpus
# (vendored mojo-yaml frontmatter sweep), regex-corpus (whole-corpus
# findall battery), db-access (sqlite+duckdb drivers through the
# bridge), db-integrity (the integrity_check port, golden parity),
# graph-algos (make graph-algos surface via the original modules —
# Onager + the full FTS5 surface, parity-gated). This target builds ALL
# binaries first (every one is a leg input). Single leg:
#   make mojo-bench MOJO_BENCH_ARGS='--leg pool-4x'
# Knobs: MOJO_BENCH_SCALE=1,4 MOJO_BENCH_REPS=5 (cosine-knn leg only).
MOJO_BENCH_SCALE ?= 1,4
MOJO_BENCH_REPS ?= 3
MOJO_BENCH_ARGS ?=
mojo-bench: $(MOJO_TARGETS)
> @.venv/bin/python3 $(MOJO_DIR)/bench/run_bench.py --scales $(MOJO_BENCH_SCALE) --reps $(MOJO_BENCH_REPS) $(MOJO_BENCH_ARGS)

$(MOJO_BIN):
> mkdir -p $(MOJO_BIN)

define MOJO_RULE
$$(MOJO_BIN)/$(basename $(notdir $(1))): $(1) | $$(MOJO_BIN)
> $$(MOJO) build $$< -o $$@ $$(MOJO_IMPORT_FLAGS)
endef
$(foreach s,$(MOJO_SOURCES),$(eval $(call MOJO_RULE,$(s))))
