# Evidence — P2.2 rebuild-cost probe (2026-10-01)

Raw artefacts behind the P2.2 hold and the `vault_scaling.md` §3.1
re-anchor. **Superseded for reproduction by `tests/bench_rebuild_scale.py`** —
these are the first-pass files, kept because §3.1's numbers were derived from
them and because the failure modes they hit are the ones the shipped harness
was written to avoid.

| File | What it is |
|---|---|
| `scale_probe.py.txt` | the first-pass harness: clones `memory/research.db` and inflates it k-fold by **row duplication with remapped keys** |
| `probe.log` | run 1 — k=1 succeeded (R=115,030 → 2.62 s); k=3 died on a column-count bug in the `graph_edges` insert |
| `probe2.log` | run 2 — k=3 died with `datatype mismatch`: the blanket `INSERT OR IGNORE … CAST(col AS TEXT)\|\|' [k]'` suffixing hit integer FK columns in `entity_tags` / `hyper_*` |
| `probe3.log` | run 3 — k=1 and k=3 landed; k=10/30/100 **abandoned**: the SQLite *clone* was the bottleneck (307 MB → 1.68 GB at 10×; 100× would be ~17 GB), not the build |
| `rebuild_t1_t2.log` | first run of the shipped harness: T1 = 3.94 s, T2 = 28.08 s; then the `R=0` `CHECK constraint failed: source != target` traceback that exposed the `company_n` floor |
| `rebuild_r0.log` | the reconciled run: R=0 → 1.47 s, R=1M → 3.99 s, fit `T(R) ≈ 1.47 s + 2.52 µs·R`, plus the fixed-cost breakdown |

## Why these are evidence and not the harness

`scale_probe.py.txt` (renamed from `.py`: it is a frozen
artefact, and as a `.py` file the `SQLite helper usage` static check flags its
raw `sqlite3.connect` calls as if it were current code — a false signal for a
file that must never be edited) measures the right *function* (`query.connect(rebuild=True)`
→ `_build_graph`) but the wrong *shape* of experiment: it starts from a
307 MB copy of production `research.db` and multiplies it, so cost per scale
point is dominated by SQLite index maintenance on a real-sized source
(~1.68 GB at 10×). Its 10×/30×/100× points were never obtained.

`tests/bench_rebuild_scale.py` generates the source instead — schema clone
with no rows, then bulk-insert a collision-free synthetic graph at the
requested R — which is what makes T1 and T2 reachable. It also fixed three
bugs this evidence records: the FTS5 shadow-table ordering in the schema
clone, the `UNIQUE(source, target, edge_type)` birthday collision (solved by
enumerating ordered pairs rather than hashing endpoints), and the
`company_n == 1` self-loop at R=0.

## Reading the two intercepts

They differ, and the difference is informative rather than a contradiction:

| Source | Intercept | Slope | 5 s crossed at |
|---|---|---|---|
| real-data clone (`scale_probe.py`, k=1/3 + R=0) | 2.28 s | 3.0 µs·R | R ≈ 907K |
| synthetic generate (`bench_rebuild_scale.py`) | 1.47 s | 2.52 µs·R | R ≈ 1.40M |

The slopes agree within ~20%; the intercepts do not, because the clone carries
production's full 307 MB source (all tables, all indexes, the FTS5 side
tables) while the synthetic source is lean. Use the **synthetic** numbers for
tier-ladder and budget decisions — they are reproducible from a clean checkout
— and the **clone** number for what production actually pays today. The
`make perf` leg is an independent third witness: `graph_rebuild` measured
2.57 s against the clone probe's 2.62 s at the same scale.
