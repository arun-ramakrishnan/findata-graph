---
title: "Scale corpus + embeddings to 100M elements — lazy Corpus, aligned f32 matrix, flat KNN"
status: executed
filed: "2026-09-02"
executed: "2026-09-02"
completed_md: "195"
area: "helpers/core (corpus.py lazy/shard, new embedding-matrix store), note_search fallback leg"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Scale corpus + embeddings to 100M elements — lazy Corpus, aligned f32 matrix, flat KNN

<!--
House proposal skeleton (matches the executed corpus's shape — see
doc/improvements/archive/ for real examples). Rules:

- File the proposal BEFORE implementing multi-slice work (house rule
  2026-08-21). One proposal per arc; slices inside it.
- Every number is MEASURED on this box — a proposal with unmeasured
  claims gets challenged; keep the raw log in the Appendix.
- Tables for comparisons, prose for causality.
- On EXECUTED: git mv to ../archive/<topic>/, completed.md entry (unique
  number), pending.md sweep, archive/README topic line, README pointer
  reset, `make search-fresh APPLY=1` — the full checklist lives in the
  proposals README.
- If a proposals frontmatter contract lands, this template gains it —
  until then the bold-line header below is the canonical status field.
-->

**Date:** 2026-09-02 · **Status:** EXECUTED (same day; code rode the #194-titled commit) ·
**Area:** `helpers/core/corpus.py` (lazy + shard close-out) · `helpers/core/embed_matrix.py` (new) · `helpers/core/vec_search.py` (flat-KNN leg) · follows `doc/improvements/archive/tooling/shared_corpus_incremental_derive.md` (S1, #194)

## 1. Motivation

S1 (#194) made the corpus pipeline incremental and one-walk at `1243` notes.
Operator direction 2026-09-02: **timings are not the goal — scalability is**.
The target is `100M+` embedding elements, i.e. **~260,000 notes at bge-small
384-d** (381 MB fp32 matrix — the exact shape benched 2026-09-02) — a `210×`
breadth jump from `1243`. Two independent arcs already proved the endpoints:

- the KNN compute leg at 100M elements is solved and linear (flat scan,
  `top1_ok`-gated — Appendix);
- the corpus side has the incremental primitives (`Corpus.load` DB cache,
  content-hash verdict, `load_shard` — in code, needs shakedown).

What breaks at `210×` breadth is **memory, not time**: `Corpus` returns
`list[Note]` with full text always (`body` is `10 KB/note` vs frontmatter
`730 B/note`); at 260K notes that is ~2.8 GB resident where ~190 MB of
frontmatter would do. And the embedding substrate (`embed_store.db`: JSON
column + vec0 blobs) has no mmap-able **aligned** float32 matrix, which is
the contract the 100M bench proved required (`vmovaps` 32-byte alignment —
misaligned inputs segfault 8/8).

Trigger: user request 2026-09-02 ("timings are not important … looking at
scalability to 100M+ … what can be unlocked") after the MAX-arc scale bench
(1M→500M linear) and the S1 close-out.

## 2. Evidence (measured 2026-09-02, this box `i5-6500 4c no-HT`; code-state audit same day)

| Configuration | Result | Verdict |
|---|---|---|
| flat MAX matvec over aligned f32 (bench `Mojo/bench/max_real_matmul.py`) | `1M 0.435 / 7.6M 1.109 / 100M 19.733 / 250M 40.694 / 500M 79.278 ms` — linear `~0.16 ns/element`, `top1_ok=True` every run | exact KNN at 100M+ solved; no ANN needed at this scale |
| same, input forced `16 mod 32` | `8/8` segfault; forced 64B-aligned `8/8` pass | alignment is a hard contract for any matrix the KNN leg touches |
| `Corpus.load` DB cache (S1b, in code) | `0.37s` cold / `0.15s` cached @ `1243` | incremental primitive exists |
| content-hash verdict (S1b.2, in code; S1 appendix) | touch same content `0.19s` vs `0.70s` mtime-false-full; content change `0.20s` | hash gate implemented; shakedown = this proposal's A1 |
| `Corpus.load_shard("Companies")` (S1b.3, in code) | API present; `maint` wiring absent | adopt for Companies-only derives (A2) |
| `list[Note]` resident (S1) | `29 MB` @ `1243` (`fm 730 B` + `body 10 KB` per note) | **projection** 260K notes ≈ `2.8 GB` full vs `≈190 MB` frontmatter-only |
| `embed_store.db` today | `50 MB`: `embed_cache 5355` rows (`sha256(text)+model` key), `note_search_vec` vec0 `1241` rows | embedding incrementality EXISTS; missing piece is the aligned matrix view |
| bge embed throughput (S1 §6) | `~1243 docs ≈ 1.2–1.56s` (`~1K docs/s`) | **projection** 260K one-time backfill ≈ `4–5 min`, then hash-gated O(changed) |

Ruled out / moot, do not re-audit: **S2 Mojo corpus_sweep** (its entire
justification was timing — dropped by operator direction); GPU legs (no
discrete accelerator; HD530 only wins ≥50 MB *resident* repeated scans at
`21 GB/s` vs `25 GB/s` DRAM — not worth the plumbing); DuckDB corpus store
(S1 measured: `47 MB`, `CREATE 8.62s`, SELECT slower than sqlite); wiring
`verify_notes`/`frontmatter_schema` to Corpus (timing-only, demoted);
`ThreadPool` corpus load (S1 measured slower at 1.2k small files).

## 3. Design

**Chosen:** make every stage O(changed) and memory-bounded, with one new
artifact — the aligned f32 embedding matrix — as the KNN substrate. The vec0
index stays primary at current scale; the flat leg is the scale/fallback
path (exact, no approximation drift).

Slices (each independently landable, order matters):

1. **S2a — corpus memory ceiling (S1b.4 promoted, S1b.2/S1b.3 closed)**:
   `Corpus.iter_notes(fields="frontmatter")` lazy `Iterator[Note]` — stream
   from the DB cache without materializing `list[Note]` bodies; eager
   `load()` stays default so no caller changes. `load_shard` adopted by
   Companies-only derives (`derive_themes`). A1 shakedown = hash-verdict
   acceptance (touch/content-change) recorded here, not re-implemented.
   *Progress: A1 shakedown DONE 2026-09-02 (×3 stable); `iter_notes` DONE
   (parity 1243/1243, tracemalloc `59.1 MB → ~0 MB` peak); root-scoped
   eviction fix DONE (the A1 shakedown exposed the old table-global
   eviction deleting every other root's cache rows on a shard/synthetic
   load — fatal for `load_shard` adoption). Shard adoption still open.*
   *Shard adoption DONE same day: `derive_themes --corpus` now
   `load_shard("Companies")` — `359`-edge parity, `29` tests (see Appendix).*
2. **S2b — aligned embedding matrix store** (`helpers/core/embed_matrix.py`):
   `memory/embed_matrix.f32` (row-major `N×D` fp32, 64B-aligned rows) +
   sidecar meta (ids, `D`, model tag, per-row `text_hash` from
   `embed_cache` keys). Refresh is hash-gated: unchanged rows are copied,
   changed notes re-embed via the existing cache (never re-embedding
   unchanged text). Model mismatch ⇒ full rebuild (same semantics as
   `embed_cache` key).
   *Progress: DONE 2026-09-02 — built from live `note_search` (`1241×384`,
   rows already unit-norm), base `%64 == 0` asserted, refresh no-op
   `rewritten=0`, changed-rows-only rewrite byte-verified in
   `tests/test_embed_matrix.py` (7 passed). Deviation: the per-row hash is
   `blake2b(embedding, 8)` — embedding-stable, not the text hash (text
   hashes already gate `embed_cache`; this hash gates the matrix write).*
3. **S2c — flat exact KNN leg**: `vec_search.knn_flat(query, k)` over the
   matrix — aligned query buffer, MAX-engine matvec (the benched pattern)
   with numpy fallback; top-k ids + scores. Parity-gated vs numpy brute
   force (100% top-k overlap) and cross-checked vs vec0 on the live corpus.
   *Progress: DONE 2026-09-02, landed as `EmbedMatrix.top_k` (numpy, the
   default — µs at 1.2k rows, keeps Tier-1 helpers MAX-free) + `FlatKNN`
   (MAX opt-in) in `embed_matrix.py`, not `vec_search.knn_flat` — one
   module owns the alignment contract. Parity: numpy `20/20` exact top-10
   vs brute force, self-retrieval ok; MAX `5/5`; vec0 `50/50` top-10
   shared. **Found + fixed**: passing MAX the raw mmap view SEGV'd
   intermittently (DLPack import may copy into a 16-mod-32 mimalloc
   buffer — the bench's alignment mechanism); `FlatKNN` now materializes
   a resident 64B-aligned copy at init (which IS the serving working set
   at 100M scale). ×5 stable after the fix.*
4. **S2d — optional adoption**: the `note_search` hybrid fallback (the
   app.py cosine path when vec0 is unavailable) may route through S2c;
   adopt only if the parity gate is green — otherwise keep S2c a bench/
   scale leg.
   *Progress: DONE 2026-09-02 — parity was green (A4: numpy 20/20, vec0
   50/50), so `app._flat_knn_map` now serves as the fallback-of-the-fallback
   with the SAME global-rank `{file_path: sim}` contract as vec0 (accepted
   semantics 2026-08-17); a page-subset staleness gate returns None → the
   page-local Python cosine still runs when note_search moved ahead of the
   matrix; never raises (hybrid degrades, never 500). Refresh hook:
   `rebuild_note_search` refreshes the matrix (hash-gated) in BOTH the full
   and incremental branches via `_refresh_embed_matrix` — best-effort,
   same derived-state class as the vec0 mirror. Real-corpus check: full
   `1241`-entry map, self-retrieval top-1 `1.000000`, stale page → None.
   Deferred sub-item: at 260K breadth the full-read refresh (~seconds of
   JSON parse) wants a row-append delta path — revisit at the scale
   trigger.*

Alternatives considered: **extend vec0 partitions** to 260K rows (untested
at that breadth on this build, opaque memory, no alignment control — the
flat matrix is measurable and exact); **numpy-only KNN** (works, but
surrenders the `19.7 ms @ 100M` leg to a `~10×` slower dot path; keep as
fallback, not primary); **shard the matrix files** (deferred — linear to
500M proven in one buffer; row-shard only past the RAM ceiling, ~1.9 GB
matrix).

## 4. Acceptance criteria & shakedown

1. **A1 hash shakedown**: `touch` a note (content unchanged) → next
   `Corpus.load` reuses without re-parse (second-load stays cache-speed,
   not false-full); edit one note → exactly that row re-parsed. `×3` stable.
2. **A2 lazy**: iterate `fields="frontmatter"` — frontmatter dicts equal to
   eager `load()` for all `1243` (assert), peak RSS below eager load
   (`/usr/bin/time -v`), `by_path` unaffected (eager default unchanged).
3. **A3 matrix**: build from live embeddings (`1241×384`), row base
   `% 64 == 0` asserted; refresh after a one-note touch rewrites only that
   row (hash gate); model-tag mismatch forces rebuild.
4. **A4 KNN parity**: `knn_flat` top-k(10) vs numpy brute force = `100%`
   overlap on ≥20 real queries; vec0 overlap recorded (not gated);
   `×3` stable.
5. Gates: targeted pytest green (`tests/test_corpus_advisory.py`,
   `tests/test_derive_themes.py` + touched), ruff clean on touched files.
   Full `make qa` once at arc end per house rule.

| Projected outcome | Today `1243` | At 100M elements (260K notes) |
|---|---|---|
| corpus resident (frontmatter consumers) | `29 MB` eager | `≈190 MB` lazy (vs `≈2.8 GB` eager) — projection |
| corpus refresh after 1-note edit | `0.20s` | O(1 file) — hash-gated, same mechanism |
| embedding refresh | full re-embed avoided by `embed_cache` | same, plus matrix row-level rewrite |
| KNN over full corpus | vec0 `11.8ms` / flat `0.44ms` @ 1M-equiv | flat `≈20ms @ 100M` (measured), exact |

## 5. Risks

- **Alignment drift** — any path handing the matrix to MAX without the
  64B contract segfaults (measured 8/8). Mitigation: the alignment assert
  lives inside `embed_matrix` access, not at call sites.
- **vec0 ↔ flat drift** — two KNN paths disagreeing silently. Mitigation:
  A4 parity gate on the live corpus; vec0 stays primary, flat is
  fallback/scale, one canonical embedding source (`embed_cache`).
- **Lazy iteration changes ordering/consumption semantics** — eager
  `load()` default unchanged; `iter_notes` is additive; advisory test
  extended rather than repointed.
- **Model swap invalidates the matrix** — model tag in meta; mismatch ⇒
  rebuild (mirrors `embed_cache` key semantics; a swap re-embeds once).
- **Scope creep toward timing work** — operator direction is scalability;
  timing-only items (walker wiring) are explicitly non-goals.

## 6. Non-goals

- `S2` Mojo `corpus_sweep` promotion — moot (timing-only justification).
- Replacing vec0 as the primary KNN at current scale; no ANN.
- Wiring `verify_notes`/`frontmatter_schema` to `Corpus` (timing-only).
- S1b.5 generation counter, S1b.6 advisory→gating flip (triggers unmet).
- GPU paths (no discrete accelerator on this box).
- Matrix row-sharding (deferred until the single-buffer RAM ceiling,
  ~1.9 GB matrix, is actually hit; linear-to-500M measured).

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-02 | `Mojo/bench/max_real_matmul.py --elements …` (9 shapes, 30+ runs) | `0.435 / 1.109 / 2.324 / 2.225 / 19.733 / 40.694 / 79.278 ms` best @ 1M/7.6M/10M/12.8M/100M/250M/500M; linear `~0.16 ns/element`; `top1_ok=True` all | full run log in `doc/local/mojo/mojo_pilot.md` § MAX evaluation; 250M+ DRAM-bound `~25 GB/s` |
| 2026-09-02 | aligned vs `16 mod 32` inputs (1000×768, 8 runs each) | `8/8` pass vs `8/8` segfault | SEGV root cause: JIT `vmovaps` 32B requirement, zero-copy host input |
| 2026-09-02 | code-state audit | S1b.2 hash verdict + `load_shard` present in `helpers/core/corpus.py`; all five `derive_*` carry `--stale-only`; `verify_notes`/`frontmatter_schema` still solo walkers; no `maint` shard wiring | this proposal consumes, not re-implements |
| 2026-09-02 | `sqlite3 memory/embed_store.db` | `embed_cache 5355` rows, `note_search_vec` vec0 `1241` rowids, `50 MB` file | embedding substrate already content-hash incremental |
| 2026-09-02 (S1 #194 appendix) | `Corpus` hash-verdict runs | cold `0.82s` / cached `0.17s` / touch-same `0.19s` vs `0.70s` mtime-false-full / content-change `0.20s` | S1b.2 measurements, cited not re-measured |
| 2026-09-02 | A1 shakedown (synthetic 60-note corpus, isolated cache DB, ×3) | cached < cold every run; touch-same-content reuses with mtime fixup (`0` stale rows); edit re-parses exactly `1` note; frontmatter parity | mechanism confirmed; N=60 makes wall thresholds meaningless — the discriminating number is S1's `0.19s vs 0.70s @ 1243` |
| 2026-09-02 | A1 bug found: eviction was table-global | loading a foreign root computed `to_del = all keys not in this walk` — would delete the other root's rows | fixed root-scoped in `load()`; proven on a DB copy: `1243` real rows intact, `+60` synthetic added |
| 2026-09-02 | A2 `iter_notes` verification (real corpus) | parity `1243/1243 equal=True` (`0.035s` stream); tracemalloc peak eager `59.1 MB` vs lazy consume `~0 MB`; no-DB fallback + `fields` validation ok; `23` targeted tests + ruff green | lazy path is O(1) memory as projected |
| 2026-09-02 | S2a shard adoption — `derive_themes --corpus` → `load_shard("Companies")` | `themes=12 companies_scanned=1078 derived_edges=359` — identical to the full-corpus baseline; wall `0.59s`; `29` tests incl. integration derive chain | Companies-only derive loads `1078/1243` notes (~8 MB vs ~29 MB resident) |
| 2026-09-02 | A3 matrix build (live source) | `from_note_search` `1241×384` (row norms `0.99999994–1.0` — cosine == dot); build `0.005s`; `base % 64 == 0`; refresh no-op `rewritten=0 rebuild=False` | `dims=384` needs no stride padding (`1536B ≡ 0 mod 64`); padded-stride path covered by tests at `dims=100` |
| 2026-09-02 | A4 flat KNN parity (×5 runs after fix) | numpy `20/20` exact top-10 vs brute force (`0.17 ms/query` avg), self-retrieval top-1 ok; MAX `FlatKNN` `5/5` exact per run; vec0 cross-check `50/50` top-10 shared over 5 queries | both KNN paths agree exactly — no vec0↔flat drift on the live corpus |
| 2026-09-02 | A4 bug found: MAX on raw mmap view | first acceptance run SEGV'd (exit `139`, no output — buffered); `FlatKNN` had passed MAX a `np.memmap` view — the DLPack import can copy into a 16-mod-32 mimalloc buffer → `vmovaps` SEGV (bench mechanism) | fixed with a resident 64B-aligned copy in `FlatKNN.__init__`; `×5` clean runs after |
| 2026-09-02 | final batch | `45` targeted tests pass (`embed_matrix 7`, `corpus_advisory 2`, themes/cited_in/integration `36`); ruff green on all touched files | S2a+S2b+S2c complete; S2d (note_search adoption) optional/next |
| 2026-09-02 | S2d fallback + refresh hook | `_flat_knn_map` real-corpus: full `1241`-entry map, self top-1 `1.000000`, stale page → `None`; `_refresh_embed_matrix` on in-memory conn: build `6` → no-op `0` → one-row change `1`; `45` tests (`flat_knn_fallback 4` + embed_matrix/vec_search/api_search) pass, ruff green | hybrid degrade order is now vec0 → flat matrix → Python cosine |
| 2026-09-02 | arc-end gates (`make qa` ×3 → `perf` ×2 → `advisory` + `lint-audit`) | **qa 7/9**: lint/md-lint/types/deptry/static_checks/verify_notes/integrity OK; remaining pytest fails are the 2 environmental `compute_root` tests (hard-assert the live checkout dir name `pdf-ocr-obsidian` — unpassable in a worktree named `graph_algos`), and `snapshot_check` = generation staleness vs the hardlinked live DB (snapshot `58188` — the published export — vs source `65041` from operator live sessions; this arc never bumped graph generations; fix = operator's snapshot-refresh publish flow). **perf 21/22 solo** (all timing budgets pass; first run's 5 OVER_BUDGET legs were contention — perf ran concurrently with `advisory`'s 80 s live-invariants on this 4C box; solo rerun clean; `snapshot_check` same staleness). **advisory 10/10 PASS**, **lint-audit PASS** | gate fixes during qa: numpy declared (first production import — was transitive), `FlatKNN` moved to `Mojo/bench/flat_knn.py` (keeps dev-group `max` out of Tier-1 helpers), `from_note_search` routed through `db.connect` (B2), deptry config: `Mojo/bench` excluded + `regex` DEP002 ignore (bench is its only importer), 2 pre-existing unformatted `.mojo` bench files formatted, bench `assert` → `RuntimeError` (lint-audit S101) |

## Appendix — how to run `Mojo/bench/flat_knn.py` (explicit record, 2026-09-02)

`Mojo/bench/flat_knn.py` is a LIBRARY MODULE, not a script — no CLI, no
`make` target, no pytest leg (the MAX JIT compile costs ~2.8 s per session;
parity is already gated in the Appendix above). You import it.

**Purpose.** The MAX-backed scale leg of the KNN stack (S2c). Default path
is `EmbedMatrix.top_k` in `helpers/core/embed_matrix.py` — pure numpy,
microseconds at `1241×384`, and keeps Tier-1 helpers free of the `max`
package (a dev-group dependency — the reason `FlatKNN` lives under
`Mojo/bench/`, which deptry excludes). `FlatKNN` compiles the same
`ops.matmul` graph the 2026-09-02 bench validated (`19.7 ms` exact scan @
100M elements, linear to 500M) and exists for large matrices / many-query
sessions where numpy's dot path is ~10× slower.

**Prerequisites.**

1. `memory/embed_matrix.f32` + `memory/embed_matrix.json` must exist.
   Built/refreshed automatically by `rebuild_note_search` (full AND
   incremental branches, hash-gated row rewrites — `stats["matrix_rows"]`),
   or manually:
   `EmbedMatrixStore().build(*from_note_search())`.
2. `Mojo/bench` is not a Python package — the snippet's two `sys.path`
   inserts are required (repo root for `helpers`, `Mojo/bench` for
   `flat_knn`).
3. Matrix must have `stride == dims` (contiguous rows — true for 384-d,
   `1536B ≡ 0 mod 64`, no padding); `FlatKNN` raises otherwise.
4. Never hand MAX a raw `np.memmap` view or an unaligned buffer — it
   segfaults (`vmovaps` 32B contract; DLPack-import copies land at
   `16 mod 32` under mimalalloc — observed and fixed this arc). `FlatKNN`
   makes the resident 64B-aligned copy itself; just don't bypass it.

**Verified run** (2026-09-02, live corpus — copy-paste):

```bash
.venv/bin/python3 - <<'EOF'
import sys, time
sys.path.insert(0, "."); sys.path.insert(0, "Mojo/bench")
from helpers.core.embed_matrix import EmbedMatrixStore, from_note_search
from flat_knn import FlatKNN

em = EmbedMatrixStore().load()
print(f"matrix: {em.matrix.shape[0]}x{em.matrix.shape[1]}, aligned={em.aligned}")
t0 = time.perf_counter(); fk = FlatKNN(em); print(f"FlatKNN compile: {time.perf_counter()-t0:.1f}s")

ids, emb = from_note_search()  # live embeddings; query with note 0's own vector
t0 = time.perf_counter()
hits = fk.top_k(emb[0], 5)
dt = (time.perf_counter() - t0) * 1000
for fp, score in hits:
    print(f"  {score:.6f}  {fp}")
print(f"query: {dt:.2f} ms (self top-1: {hits[0][0] == ids[0]})")
EOF
```

Observed output (this box, 2026-09-02):

```text
matrix: 1241x384, aligned=True
FlatKNN compile: 2.8s
  1.000000  findata/Companies/Agriculture/Avanti_Feeds.md
  0.796175  findata/Companies/FMCG/Sharat_Industries.md
  0.770700  findata/Companies/Infrastructure/Kings_Infra.md
  0.764479  findata/Companies/FMCG/Apex_Frozen_Foods_Ltd.md
  0.752287  findata/Sectors/Agriculture.md
query: 1.22 ms (self top-1: True)
```

**When to use which.** One-off queries / small corpus → `em.top_k(q, k)`
(numpy, no compile, µs at 1.2k rows). Large matrix (millions of rows) or
many queries in one process → `FlatKNN` (compile ~2.8 s once, then each
query is a flat MAX matvec; scores are cosine — rows are unit-norm, the
query is normalized inside). Production hybrid search never calls either
directly: it goes vec0 → `_flat_knn_map` (numpy `top_k`) → Python cosine
(S2d).
