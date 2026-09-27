# findata-graph Desktop (Tauri)

Local-first desktop viewer for the findata knowledge graph: D3 force
graph, FTS5 note search, markdown note reading — all direct to
`memory/research.db` through Rust. **No Flask sidecar, no web server.**

## One-time setup

```bash
# system deps (Linux; Tauri links webkit2gtk + GTK)
sudo apt install -y libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev \
  libayatana-appindicator3-dev pkg-config build-essential

# frontend deps
cd desktop && make install
```

Toolchain: Rust stable + Node 20+ (rustup + nvm already present on this
machine). `cargo install tauri-cli --version "^2.0.0"` is required for
`make tauri` / `make bundle` only — `make dev` works without it.

## Run

```bash
cd desktop
make dev        # vite on :5173 + debug binary (Ctrl-C stops both)
```

Repo-root discovery: the binary walks up from its cwd looking for
`memory/research.db` (`FINDATA_GRAPH_ROOT` overrides). Run it from anywhere
inside the repo.

## Verify without a window

```bash
make test       # 20 live-data tests: graph, ego, cloud, FTS search, notes,
                # docs, metrics, rank, as_of, hyperedges, hybrid
make typecheck  # vue-tsc checkJs over the JS frontend (S8, zero-baseline)
```

## Ship (S6)

```bash
make bundle     # deb + AppImage under src-tauri/target/release/bundle/
```

Bundling is active for the host platform (Linux: `deb`, `appimage`;
`tauri.conf.json` `bundle.targets`). Path quirk, learned the hard way:
the CLI runs `beforeDevCommand`/`beforeBuildCommand` from `desktop/`
but resolves `frontendDist` from `src-tauri/` — so the conf uses
`src-vue …` for commands and `../src-vue/dist` for the dist. Icons:
512px source + 32/128 PNGs ship Linux bundles; no `.ico`/`.icns`
(needed only for Windows/macOS — generate via `cargo tauri icon`
when those targets ship).

## Test fixtures (freshness)

`src-vue/test/fixtures.js` is generated from the live `memory/research.db`
— regenerate after every `make db-sync` / graph rebuild:

```bash
make fixtures   # ../.venv/bin/python3 src-vue/test/make_fixtures.py
```

The Rust tests (`make test`) always run against the live DB, not the
fixtures; the fixtures only drive the headless harness (`make smoke`
via `test.html`). Stale fixtures → smoke screenshots drift from the live
app; re-run `make fixtures` and re-shoot.

## What the app does

| Surface | Behaviour |
|---|---|
| **Ego mode** | Centre on any entity — its 1-hop network (peers, JVs, group, subsidiaries, suppliers, customers, sector) rendered as a force graph. Click a node to inspect, double-click to re-centre, drag to nudge. |
| **Cloud mode** | Whole corpus (26k entities / 57k edges) in one canvas. Edge-type chips in the sidebar hide/show relationships; labels appear for hubs and beyond ~1.1× zoom. |
| **Search** | `/` focuses the bar. Entity suggestions jump the graph; Enter runs full-text search over the `note_search` FTS index (sectioned, deduped to notes, `<mark>` snippets). |
| **Note panel** | Click a search hit or “Open note” → markdown rendered in the right rail. Internal `findata/` / `doc/` links navigate inside the app; external links open in the system browser (`tauri-plugin-opener`, `opener:allow-open-url` only). |
| **Docs browser** | Flat `doc/**` listing (244 files incl. archived `.txt`) with FTS filter over titles/sections/content; click opens the doc in the note panel via the guarded reader. Backed by the `doc_search` sqlite sidecar (`memory/doc_search.db`, rebuilt by `make search-fresh`) — missing sidecar is a hard error, never an empty list. |
| **Metrics tab** | Right-rail Note/Metrics tabs. Metrics shows latest-first company fundamentals (label/value/period) + graph rank scalars (pagerank, centralities) + community labels for the selected entity. Payload metrics (`link_prediction`, `voterank`) excluded — no scalar reading. |
| **Rank tint** | Cloud legend gains a tint select (pagerank + 3 centralities): nodes repaint in a log-scaled sky ramp over the full metric column; label/community metrics and unknown names hard-error instead of zero-tinting. |
| **Chronoscope** | Header date input (`YYYY`, `YYYY-MM`, or `YYYY-MM-DD`, Enter to apply, ✕ to clear) re-invokes the current view with `as_of`: edges with `valid_from` after the cutoff drop (NULL validity = always-valid, mirroring Flask). Statuses echo the normalised date; cloud cache is per-`as_of`. |
| **Hyperedges** | Entity detail gains a hyperedges section (sector/industry/edition/event chips with member counts); click halos the hyperedge's members in the graph in one colour per edge (toggle off by re-clicking). Read-only over `hyper_edges`/`hyper_incidences`, `as_of`-aware at the edge level; members capped at 50. |
| **Hybrid search** | Results header toggles BM25 / hybrid: hybrid fuses BM25 ranks with vector-cosine ranks at RRF K=60 (cosine leg = similarity to the top hit's best section — no query model needed). Pure-Rust cosine over the stored 384-d section BLOBs. |
| **Similar notes** | Open notes gain a similar-notes list (max section-pair cosine, self excluded, scores shown). |
| **Legend** | Ego mode: relationship rollup of the current centre. Cloud mode: global edge counts, click to filter. |

## Architecture

```text
desktop/
├── src-tauri/
│   ├── core/            findata-core — SQLite data layer (rusqlite, read-only)
│   │   └── tests/       live-data tests against research.db (cargo test)
│   ├── src/main.rs      Tauri v2 commands — thin wrappers, errors → toasts
│   ├── capabilities/    core:default + opener:allow-open-url
│   └── tauri.conf.json  devUrl :5173, bundle deb+appimage (Linux)
└── src-vue/             Vue 3 + Vite + Tailwind v4
    └── src/
        ├── components/  GraphView (canvas + d3-force), SearchBar, NotePanel
        └── lib/         api.js (invoke wrappers), store.js, colors.js
```

- **Data path:** one `rusqlite` read-only connection per command, opened and
  dropped — never holds a lock across IPC calls. `graph.duckdb` is not
  touched (it is a read-derived cache; the SQLite `entities` + `graph_edges`
  tables are the source of truth).
- **Graph renderer:** canvas 2D + `d3-force`, custom pan/zoom/drag (screen-
  constant line widths, batched per-colour edge strokes, degree-gated labels).
  Ego layouts cool in ~2s; the cloud sim takes a few seconds and *looks* like
  it is assembling — expected.

## Deliberately out of scope (S7+)

Hybrid query embedding (bundled GGUF — proposal S7 option i),
Windows/macOS installers, Rank/Time *derivation* (display is in;
derivation stays in Python), any write path into the vault. See
`doc/improvements/archive/ui/findata_graph_desktop.md` (completed.md #308).
