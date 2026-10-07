# findata-graph desktop (Tauri)
#
#   make install   one-time: npm deps
#   make dev       run in dev mode (vite + debug binary)
#   make test      data-layer tests against the live research.db (read-only)
#   make build     production frontend + release binary

.PHONY: install dev tauri test build clean fixtures smoke contract check

install:
	npm --prefix src-vue install

# Regenerate test/fixtures.js from the live research.db
fixtures:
	../.venv/bin/python3 src-vue/test/make_fixtures.py

# Debug run without the tauri-cli: starts vite, then the binary (which
# connects to http://localhost:5173 via devUrl), and stops vite on exit.
dev:
	@npm --prefix src-vue run dev & VPID=$$!; \
	trap "kill $$VPID 2>/dev/null" EXIT INT TERM; \
	sleep 1; \
	cd src-tauri && cargo run

# Headless screenshot smoke test against the running vite server.
smoke:
	@for s in search note cloud docs metrics asof hyper opener hybrid; do \
		UD=$$(mktemp -d /tmp/ui.XXXX); \
		google-chrome --headless=new --no-sandbox --disable-gpu --hide-scrollbars \
			--user-data-dir="$$UD" --window-size=1600,950 --virtual-time-budget=9000 \
			--screenshot=/tmp/ui-$$s.png "http://localhost:5173/test.html?scenario=$$s"; \
		rm -rf "$$UD"; \
	done; \
	echo "screenshots: /tmp/ui-search.png /tmp/ui-note.png /tmp/ui-cloud.png /tmp/ui-docs.png /tmp/ui-metrics.png /tmp/ui-asof.png /tmp/ui-hyper.png /tmp/ui-opener.png /tmp/ui-hybrid.png"

# Alternative if tauri-cli is installed (cargo install tauri-cli --version "^2.0.0")
tauri:
	cd src-tauri && cargo tauri dev

# S6: installer bundle for the host platform (deb/appimage on Linux).
# Requires tauri-cli + system webkit (see README one-time setup).
bundle:
	cd src-tauri && cargo tauri build --bundles deb,appimage

# S8: vue-tsc checkJs over the JS frontend (zero-baseline; keeps the
# Svelte question closed — safety without a language migration).
typecheck:
	npm --prefix src-vue run typecheck

# ipc_guards S3: the consumer-side contract gate — every fixtures.js
# payload asserted against its lib/schemas.js schema (the valibot mirror
# of the Rust return types). A Rust struct change (or make_fixtures.py
# mirror update) that outpaces a schema fails here naming cmd + field.
contract:
	npm --prefix src-vue run test:contract

# Full consumer-side check lane: types + IPC contract.
check: typecheck contract

test:
	cd src-tauri && cargo test -p findata-core

build:
	npm --prefix src-vue run build
	cd src-tauri && cargo build --release

clean:
	rm -rf src-vue/dist src-tauri/target
