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
MOJO_TARGETS := $(addprefix $(MOJO_BIN)/,$(foreach s,$(MOJO_SOURCES),$(basename $(notdir $(s)))))
MOJO_TESTS := $(wildcard $(MOJO_DIR)/tests/*.mojo)
# Every src/<pkg> dir is an import root, so tests can `import <module>`
# from any package (and packages can import each other's modules).
MOJO_IMPORT_FLAGS := $(foreach d,$(MOJO_PACKAGES),-I $(d)) -I $(MOJO_VENDOR_SRC)

.PHONY: mojo-build mojo-test mojo-bench

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

# Harness: Mojo/bench/run_bench.py runs every bench leg and appends the
# table to Mojo/bench/bench_report.txt. Legs: cosine-knn (the 4-way
# py_math/py_json/sqlite-vec/mojo_simd comparison via bench_cosine_knn.py),
# analyzer (multi-tier compute table), pool-4x (bench_pool x4 workers,
# synthetic matrix), regex-bridge (mojo_regex_probe), yaml-corpus
# (vendored mojo-yaml frontmatter sweep). This target builds ALL binaries
# first (every one is a leg input). Single leg:
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
