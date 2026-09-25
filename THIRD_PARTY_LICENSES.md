# Third-party license inventory

This is the initial inventory for the AGPL migration. It records direct
runtime, optional, native-extension, and frontend dependencies. Before a
release, regenerate the exact resolved set and attach the upstream license
and notice files for the selected wheels/bundles; package metadata alone is
not a complete distribution notice.

## First-party boundary

The project license in `LICENSE` applies to first-party source code. The
following remain under their own rights or are not relicensed by the project:

- `findata/` source-derived notes and corpus content;
- `Reports/`, external PDFs, images, and newsletter material;
- `models/` model weights and downloaded model artifacts;
- `memory/` databases, indexes, snapshots, and generated caches;
- third-party source, package code, browser bundles, and native libraries.

## Python runtime and native components

| Component | SPDX/status | Notes |
|---|---|---|
| Flask | BSD-3-Clause | Web framework |
| MarkupSafe | BSD-3-Clause | Flask dependency |
| python-dotenv | BSD-3-Clause | Environment loading |
| Werkzeug | BSD-3-Clause | WSGI/runtime |
| gunicorn | MIT | Production WSGI server |
| yfinance | Apache-2.0 | Market-data client |
| sqlite-spellfix | MIT (verify release) | Native SQLite extension |
| PyYAML | MIT | Frontmatter/validation |
| DuckDB | MIT | Graph/query engine |
| PyArrow | Apache-2.0 | Parquet/Arrow I/O |
| NumPy | BSD-3-Clause | Numeric runtime |
| requests | Apache-2.0 | HTTP/API client |
| pymupdf4llm | MIT wrapper; bundles PyMuPDF terms | Verify wrapper and bundled notices |
| PyMuPDF | AGPL-3.0 | PDF engine; compatibility requires AGPL distribution review |
| liteparse | Verify release license | OCR/PDF fallback |
| sqlite-vec | MIT (verify release) | SQLite vector extension |
| jsonschema | MIT | JSON Schema validation |
| fastjsonschema | MIT | Fast validation path |
| llama-cpp-python | MIT wrapper; bundled/runtime terms require review | Local GGUF embedder |
| gspread | Apache-2.0 | Google Sheets client |
| finnhub-python | MIT (verify release) | Market-data client |
| nse-xbrl | MIT (verify release) | NSE filing client |
| openpyxl | MIT | XLSX parsing |
| regex | MIT | Mojo/Python regex bridge |
| Matplotlib | PSF-based license | Plotting/OCR support |
| HypergraphX | BSD-3-Clause | Hypergraph analytics |
| SciPy | BSD-3-Clause | Sparse/numeric kernels |
| prefab-ui | Apache-2.0 | Flask UI views |
| Onager | Apache-2.0 | DuckDB community graph extension |
| python-igraph | GPL-2.0-or-later | Candidate only; not a current dependency; GLPK/wheel/build review required |

SciPy wheels and other native builds may include additional notices and
components, including OpenBLAS, LAPACK, libgfortran with the GCC Runtime
Library Exception, and libquadmath. Preserve the exact notices shipped by
the selected wheel.

## Optional/dev/tooling dependencies

| Group | Components | License handling |
|---|---|---|
| `dev` | pytest, pytest-cov, pytest-xdist, Hypothesis, Ruff, deptry | Preserve each package's MIT/BSD/Apache notices; tooling is not part of the runtime grant |
| `tui` | Textual, Rich, tree-sitter, tree-sitter-sql | Preserve upstream MIT/BSD notices; runtime extra remains separately governed |
| `mojo` | Mojo and MAX toolchains | Record the exact compiler/runtime license and exceptions per release; do not infer from the Python project license |

## Frontend packages

`frontend/package.json` is private and its direct npm packages retain their
own licenses. The initial set is esbuild (MIT), Prettier (MIT), TypeScript
(Apache-2.0), `@sigma/node-border` (MIT), graphology (MIT),
`graphology-layout-forceatlas2` (MIT), sigma (MIT), and sugar-high (MIT).
The committed `static/` bundles must carry the applicable notices for the
versions used to build them.

## Candidate GPL component gate

python-igraph is not added by this migration. Before adding it, record the
exact Python package, C core, GLPK linkage, wheel contents, modifications,
source URL, and notice obligations. Keep Onager as the default engine; any
igraph lane must be optional and compatibility-reviewed.

## Release rule

A release is incomplete if the root license, project SPDX metadata, this
inventory, the selected dependency license texts, or the network source-offer
runbook is missing. License checks are governance signals, not legal advice;
the operator remains responsible for the final compatibility decision.
