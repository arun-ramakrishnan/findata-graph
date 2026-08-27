# Mojo build — standalone makefile invoked from the main Makefile:
#   make mojo-build   -> compile every Mojo/src/*/*.mojo into Mojo/bin/
#   make mojo-test    -> run every Mojo/tests/*.mojo (TestSuite runners)
# Repo-root cwd assumed (the main Makefile delegates with $(MAKE) -f).
# Tab-free like the main Makefile: use '>' as the recipe prefix.
.RECIPEPREFIX := >

# Layout: sources live in Mojo/src/<pkg>/<name>.mojo (one directory per
# package; Mojo/src/common is reserved for shared code). Binaries are FLAT
# in Mojo/bin/<name> (PATH_add Mojo/bin exposes them directly), so rules
# are generated per source file. Note: basenames must be unique across
# packages — a collision would silently map two sources to one binary.
MOJO := .venv/bin/mojo
MOJO_DIR := Mojo
MOJO_SRC := $(MOJO_DIR)/src
MOJO_BIN := $(MOJO_DIR)/bin
MOJO_PACKAGES := $(wildcard $(MOJO_SRC)/*)
MOJO_SOURCES := $(wildcard $(MOJO_SRC)/*/*.mojo)
MOJO_TARGETS := $(addprefix $(MOJO_BIN)/,$(foreach s,$(MOJO_SOURCES),$(basename $(notdir $(s)))))
MOJO_TESTS := $(wildcard $(MOJO_DIR)/tests/*.mojo)
# Every src/<pkg> dir is an import root, so tests can `import <module>`
# from any package (and packages can import each other's modules).
MOJO_IMPORT_FLAGS := $(foreach d,$(MOJO_PACKAGES),-I $(d))

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

# Harness: the comparison table lives at Mojo/tests/bench_cosine_knn.py
# (py_math / py_json / sqlite-vec / mojo_simd legs + cross-validations);
# it consumes Mojo/bin/bench_cosine, which this target builds if needed.
# The analyzer binary runs after it (its own multi-tier table + numpy
# cross-check). Overrides: `make mojo-bench MOJO_BENCH_SCALE=1,4 MOJO_BENCH_REPS=5`.
MOJO_BENCH_SCALE ?= 1,4,16
MOJO_BENCH_REPS ?= 3
mojo-bench: $(MOJO_BIN)/bench_cosine $(MOJO_BIN)/analyzer
> @.venv/bin/python3 $(MOJO_DIR)/tests/bench_cosine_knn.py --scales $(MOJO_BENCH_SCALE) --reps $(MOJO_BENCH_REPS)
> @echo
> @echo "=== analyzer tiers (Mojo/src/bench/analyzer.mojo, 1M samples) ==="
> @$(MOJO_BIN)/analyzer

$(MOJO_BIN):
> mkdir -p $(MOJO_BIN)

define MOJO_RULE
$$(MOJO_BIN)/$(basename $(notdir $(1))): $(1) | $$(MOJO_BIN)
> $$(MOJO) build $$< -o $$@
endef
$(foreach s,$(MOJO_SOURCES),$(eval $(call MOJO_RULE,$(s))))
