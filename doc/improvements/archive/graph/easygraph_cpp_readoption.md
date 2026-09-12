---
title: "EasyGraph C++ re-adoption — parked pending upstream weighted-BC/weight/multigraph fixes"
status: executed
filed: "2026-09-12"
executed: "2026-09-12"
completed_md: "231"
area: "helpers/graph/"
---

# EasyGraph C++ re-adoption — parked pending upstream fixes

**Date:** 2026-09-12 · **Status:** DEFERRED — archived 2026-09-12
(completed.md #231; revival triggers §3) · **Area:** helpers/graph/ (a
third-engine bridge lane, if ever)

## 1. Verdict summary

The operator's source build unlocked EasyGraph's C++ backend (pybind11
branch, Python 3.11 AND 3.14 — build recipe + run log:
`doc/local/easy_graph_run.txt`; condensed: graph_layer.md re-test
section). The speed is real (cpp pagerank 0.105s on the full
1648n/19261e graph, ~2x Onager's loop; louvain 0.014s at Q=0.48 — the
best modularity in the entire engine eval) and the licence is friendlier
than igraph's (BSD-3-Clause vs GPL-2.0-or-later).

**It still cannot take over the igraph lane list.** Full-scale probing
(both interpreters) shows the lane matrix below; retiring igraph would
lose Leiden, weighted betweenness, label-filtered weighted paths, and
flow/cut — the entire reason a second engine exists.

## 2. Evidence — lane coverage vs the igraph handover list

| igraph lane (bridge ROUTING) | EasyGraph C++ | Why |
|---|---|---|
| leiden_community | **absent** | no leiden export, python side, or docs |
| weighted_betweenness | **broken** | D1: 378/1648 nodes overflow to ~1e20+, reproduced EXACTLY on 3.11 and 3.14 (same node set, same sane top5); 199/200 sampled values disagree with pure-python BC |
| weighted_closeness | weight **ignored** | `cpp_closeness_centrality` weighted == unweighted output on a weighted toy (probed 2026-09-12) |
| weighted_eigenvector | weight **ignored** | same probe, `w == u`; also sits in eigenvector.cpp — the ODR-violation source file (D3) |
| weighted_pagerank | ✓ works | 0.105s full graph, top5 identical to Onager (india 0.0286) |
| weighted_shortest_path | partial | `cpp_dijkstra_multisource` gives distances; per-pair vpath + per-etype subgraphs = DIY |
| maxflow_mincut | **absent** | no flow/cut/s-t-connectivity exports (only biconnected + components) |
| louvain_compare | half | louvain via raw unwired cpp export (0.014s, Q=0.4817); Leiden absent so no compare-vs-Leiden |

Structural blockers beyond the matrix:

- **Parallel-edge collapse**: GraphC silently merges 2709 multi-edges
  (19261 rows → 16552 edges, last weight wins). The bridge's per-etype
  semantics (label-filtered subgraphs, etype attribution) are impossible
  on the collapsed graph, and weight-sensitive lanes run on different
  weights than igraph/Onager.
- Results are index-aligned arrays needing `G.node_index` remap;
  `list(G.nodes)` order is unrelated.
- `import easygraph` hard-requires torch (cpu wheel suffices).
- ODR violation in cpp sources (CSRMatrix, eigenvector.cpp vs
  katz_centrality.cpp) — UB landmine.

## 3. Revival triggers (ALL of R1-R2, plus any of R3-R4)

- **R1** — upstream fixes cpp weighted betweenness: acceptance = zero
  overflow nodes + ≥99% value parity vs pure-python BC on the live
  graph.
- **R2** — weight honored in cpp closeness + eigenvector: acceptance =
  weighted-vs-unweighted outputs differ on a weighted toy, and
  eigenvector converges/normalizes sensibly on the live graph.
- **R3** — `louvain_communities` becomes @hybrid-wired (or the raw
  export is accepted as a stable API), with sane behavior on small
  graphs.
- **R4** — Leiden lands upstream, OR the revival scope is explicitly
  reduced to "louvain + pagerank lane only".
- Plus the standing condition: multigraph semantics documented or
  preserved (no silent collapse).

## 4. If revived — slices

- S1: `helpers/graph/easygraph_bridge.py` mirroring igraph_bridge's
  contract (lazy import, dry-run default, `--apply` via
  write_analytics, node_index remap, ROUTING rows for adopted lanes).
- S2: parity tests vs the toy baseline JSON + BLOB-free hermetic fixtures.
- S3: adoption gate (same shape as hybrid_graph S7: real --apply
  read-back on a /tmp copy, docs routing notes, licence note — BSD-3).

## 5. Risks

- torch runtime dependency (1.3 GB class) for `import easygraph`.
- API instability: unwired exports, empty-module namespace shadowing
  (see recipe GOTCHA), silent edge collapse.
- ODR/UB in cpp sources may bite any toolchain update.

## 6. Non-goals

Not replacing Onager prod lanes. Not displacing igraph lanes EasyGraph
cannot serve. No venv/pyproject dependency before revival + gate.

## Appendix — full re-test run log (verbatim, folded from
`doc/local/easy_graph_run.txt` on archival 2026-09-12)

### Goal

Goal: execute the "recursive C++ build re-test" that the 2026-09-12 wheel eval
verdict in doc/local/evaluations/graph_layer.md made EasyGraph's only path to
viability. Operator supplied a build recipe; agent built, benchmarked at FULL
scale (1648n/19261e, live research.db projection), found two upstream defects,
and recorded the outcome. Decision after re-test: NO Easy-Graph STANDS.

### 1. OPERATOR NOTES (verbatim, 2026-09-12)

Message 1 (the task + recipe):
  "In our EasyGraph eval documented in doc/local/evaluations/graph_layer.md, we
  skipped it due to C++ backend being absent from the whl. There is a build
  recipe that seems to work :
  git clone <https://github.com/easy-graph/Easy-Graph>
  cd Easy-Graph
  git checkout pybind11
  pip install pybind11
  python3 setup.py build_ext
  python3 setup.py install"

Message 2 (mid-run correction — this recipe was for the IGRAAPH venv, not this;
ignore it, proceed with message 1's clone recipe):
  "the recipe was : python3.14 -m venv /tmp/venv_igraph &&
  /tmp/venv_igraph/bin/pip install igraph pytest pyyaml hypothesis
  python-dotenv numpy ok my bad. this was igraph venv not this. ignore my
  recpie"

### 2. ENVIRONMENT
Box: linux x64, 4 cores. gcc/g++ 15.2.0 (Ubuntu). NO system cmake.
python3 (system) = 3.14.0; python3.11 at /home/arun/.local/bin/python3.11;
uv at /home/arun/.local/bin/uv.
Previous eval environment (/tmp/venv_eg311 from the wheel eval, /tmp/
eval_easygraph.md) was GONE — tmp wiped between sessions. Rebuilt from scratch.
Repo surface: main worktree /home/arun/Research/MCP/pdf-ocr-obsidian; the
graph_algos worktree referenced by the wheel eval also still holds
memory/research.db (identical file, same mtime/size).
Data: memory/research.db graph_edges = 19261 rows / 1648 distinct endpoints —
EXACTLY the wheel eval's FULL scale. Projection used:
  SELECT source, target, weight FROM graph_edges
(schema: graph_edges(source TEXT, target TEXT, edge_type TEXT, weight REAL,
 UNIQUE(source,target,edge_type))).

Artifacts (all /tmp = volatile):
  clone    /tmp/Easy-Graph            (branch pybind11 = default branch)
  venv     /tmp/venv_eg311            (uv venv, python 3.11.13)
  script   /tmp/eval_eg_cpp.py        (final pass-2 benchmark + defect probes)

### 3. BUILD LOG — RECIPE AS GIVEN, AND WHERE IT BROKE

3.1 Vanilla recipe attempts
  a) git clone --recursive <https://github.com/easy-graph/Easy-Graph> → OK.
     `git branch --show-current` → pybind11 (already default; `git checkout
     pybind11` in the recipe is a no-op on a fresh clone).
  b) setup.py is CMake-based: CMakeExtension + CMakeBuild (cmake_example
     pattern). NOT plain setuptools. → cmake is a hard requirement. First
     build_ext died: `error: [Errno 2] No such file or directory: 'cmake'`.
     (Same root cause as the wheel eval's 3.14 sdist failure —
     `pybind11_add_module unknown` — cmake machinery missing.)
  c) Installed cmake via pip/uv into the venv (cmake 4.4.3). Second
     build_ext died EARLIER: setup_requires=[Cython] (setup.py line 164) →
     setuptools' _fetch_build_eggs tried `pip wheel --no-deps Cython` and
     failed → DistutilsError. → Cython must be pre-installed.
  d) Pre-installed cython 3.3.0 + numpy 2.2.6 + scipy 1.17.1 + networkx.
     Third build_ext with PATH=/tmp/venv_eg311/bin:$PATH → SUCCESS (~4 min,
     4 cores). Output:
       build/lib.linux-x86_64-cpython-311/cpp_easygraph.cpython-311-
       x86_64-linux-gnu.so
     Compile warnings: ODR VIOLATION — `class CSRMatrix` defined differently
     in cpp_easygraph/functions/centrality/eigenvector.cpp (has
     `bool is_weighted`) vs katz_centrality.cpp (no such field); gcc 15 LTO
     emits -Wodr / -Wlto-type-mismatch ("code may be misoptimized unless
     -fno-strict-aliasing"). Real UB risk upstream; built fine otherwise.

3.2 Install attempts (the messy part)
  e) `python3 setup.py install` (per recipe) FAILED: its easy_install
     fallback tried to source-build deps from sdists and died on
     contourpy 1.4.0 ("Couldn't find a setup script"). CRITICAL SIDE EFFECT:
     before dying it had installed a PURE-PYTHON egg
     Python_EasyGraph-1.6.2-py3.11-linux-x86_64.egg (NO .so) plus
     easy-install.pth and a pile of dep eggs (pandas/scikit-learn/gensim/
     matplotlib/optuna/nose/progressbar... as .egg dirs).
  f) The stale egg SHADOWED everything: `import easygraph` resolved into the
     egg → cpp module absent. Fix: rm the egg + easy-install.pth. Note:
     deleting easy-install.pth also unhooked all the dep eggs easy_install
     had fetched (requests etc. vanished) → reinstalled runtime deps
     properly via uv: requests six tqdm pandas scikit-learn gensim optuna
     fastjsonschema progressbar progressbar33 nose.
  g) `uv pip install -e . --no-deps --no-build-isolation` (editable) → OK.
     NOTE: uv venvs have NO pip binary — `/tmp/venv_eg311/bin/pip` doesn't
     exist; must use `uv pip install --python ...`.
  h) Editable finder maps easygraph → /tmp/Easy-Graph/easygraph, but the .so
     sits in build/lib.../ → final step `setup.py build_ext --inplace`
     drops cpp_easygraph.cpython-311-x86_64-linux-gnu.so NEXT TO the
     sources (/tmp/Easy-Graph/) → import works.

WORKING RECIPE (as landed in doc/local/evaluations/graph_layer.md §re-test):
  git clone --recursive <https://github.com/easy-graph/Easy-Graph> /tmp/Easy-Graph
  cd /tmp/Easy-Graph                      # pybind11 IS the default branch
  uv venv /tmp/venv_eg311 --python 3.11
  uv pip install --python /tmp/venv_eg311/bin/python pybind11 cython cmake \
    "numpy<2.3" scipy networkx torch --extra-index-url \
    <https://download.pytorch.org/whl/cpu>      # torch cpu wheel; import
                                              # hard-requires torch (warnings
                                              # still print — their detector
                                              # is brittle; harmless)
  uv pip install --python ... matplotlib requests six tqdm pandas \
    scikit-learn gensim optuna fastjsonschema progressbar progressbar33 nose
  PATH=/tmp/venv_eg311/bin:$PATH python3.11 setup.py build_ext          # ~4min
  PATH=/tmp/venv_eg311/bin:$PATH uv pip install --python ... -e . \
    --no-deps --no-build-isolation
  PATH=/tmp/venv_eg311/bin:$PATH python3.11 setup.py build_ext --inplace

  Recipe deltas vs upstream README: (1) never `setup.py install` (broken +
  leaves shadowing egg); editable + inplace. (2) pre-install cython. (3)
  cmake on PATH. (4) if hit, delete the stale egg + easy-install.pth.

### 4. DISPATCH MECHANICS — WHY THE WHEEL EVAL COULDN'T SEE C++ AT ALL
This is the load-bearing discovery of the run (and partially VINDICATES the
wheel eval):

- `@hybrid("cpp_x")` (easygraph/utils/decorators.py) decorates the FREE
    functions (eg.pagerank, eg.betweenness_centrality, ...) — NOT methods.
    Wrapper: `if G.cflag: return cpp_easygraph.cpp_x(*args) else: python`.
- `eg.Graph.__init__` hardcodes `self.cflag = 0` → eg.Graph is PURE PYTHON,
    ALWAYS, even with a perfect .so installed.
- C++ requires BOTH: (1) the graph object is eg.GraphC — declared in
    classes/graph.py as `class GraphC(cpp_easygraph.Graph): cflag = 1`
    (DiGraphC analogously at directed_graph.py:1303) — and (2) the op is
    called as a FREE function: eg.pagerank(GC, weight="weight").
- GraphC instances have NO algorithm methods at all (dir() = construction/
    mutation only: add_edge, nodes, edges, ego_subgraph, to_index_node_graph,
    py, ...). G.pagerank raises AttributeError on both classes.
- MODULE PATH: the extension is TOP-LEVEL `import cpp_easygraph` — NOT
    `easygraph.cpp_easygraph`. The wheel eval's diagnostic probed
    easygraph.cpp_easygraph (graph_layer.md §(1)/(3)); wrong path, but the
    wheel ships no .so under EITHER name, so its conclusion stood.
- RESULT FORMATS: cpp free functions return index-aligned lists /
    numpy.ndarrays, NOT node-keyed dicts. Name attribution MUST go through
    `G.node_index` (dict {node: index}, canonical). `list(G.nodes)` order is
    UNRELATED (verified on a 3-node toy: node_index A→0,B→1,C→2 while
    G.nodes iterates C,B,A). First benchmark pass mis-attributed PR top5
    ('Cement' heading with india's exact 0.0286) before this was caught.
- eg.connected_components(GC) returns {component_id: [node indices]} from
    cpp (or a count depending on path) — not a list of sets.

### 5. BENCHMARK RESULTS — FULL SCALE 1648n/19261e (→ 1648n/16552e in-graph)
Same methodology as graph_layer.md §(3): all 19261 graph_edges rows, weights
0.4–3.0, easygraph silently collapses 2709 parallel edges (16552 edges, last
weight wins) — identical to the wheel run. GraphC build from rows: 0.045–
0.052s. Multiple passes; timings as ranges of observed runs (4-core box,
single-threaded unless noted; OpenMP present in ext but ops ran effectively
serial at this size).

| Op                        | wheel pure-Py      | cpp via GraphC            | Onager ref                |
|---------------------------|--------------------|---------------------------|---------------------------|
| pagerank weighted         | 2.03s              | 0.020–0.030s  CORRECT     | ~0.2–0.3s (incl 30ms remap)|
| pagerank unweighted       | 2.00s              | 0.003–0.038s              | —                          |
| betweenness weighted      | 23.1s              | 0.29–0.38s  VALUES BROKEN | unweighted-only caveat     |
| betweenness unweighted    | —                  | 0.28–0.42s  BROKEN        | —                          |
| louvain (python path)     | 0.31s, 4 comms     | 0.33–0.36s, same 4 comms  | prod-used                  |
|                           | [664,407,318,259]  | Q=0.4017 (cpp_modularity) |                            |
| louvain (cpp export,      | dead code          | 0.015s, 12–13 comms,      | —                          |
| direct call)              |                    | Q=0.482–0.484             |                            |
| connected_components      | —                  | 0.05–0.23s; 1 comp × 1648 | —                          |

Correctness:
- PR(w) top5 IDENTICAL to the wheel eval and Onager: india 0.0286,
    A_Quarter_That_Refuses_To_Behave 0.0124, Blackrock Inc. 0.0040,
    Automotive 0.0040, Technology 0.0035. → cpp pagerank numerically sound.
- Louvain quality: cpp partition Q=0.482 vs python-path partition Q=0.402
    measured with the SAME cpp_modularity function on the same graph → the
    cpp result is genuinely better, and fixes the wheel eval's
    "under-partitions (4 comms, Q=0.21)" flag. (The wheel eval's Q=0.21 for
    python came from a different modularity computation; on cpp_modularity
    the python partition scores 0.4017.)
- WCC = 1 component × 1648 — consistent with the eval's "isolated 1" being
    outside the 1648 endpoints.

### 6. DEFECTS FOUND (upstream, pybind11 branch @ 1.6.2+)
D1. cpp BETWEENNESS NUMERICALLY BROKEN on the live graph (the killer):
    - 378/1648 nodes overflow to ~1e20–1e23 garbage in BOTH weighted and
      unweighted modes, same node set (examples: MRF 5.687e21, REC
      1.435e20, EPL, Bai-Kakaji Polymers, Trent in the raw first pass).
    - Non-overflowed values are ALSO wrong: of the first 200 "sane" cpp
      values only 1/200 agrees with pure-python BC (tol 1e-3 rel).
    - Pure-python BC on the identical graph is entirely sane — including on
      the 378 overflow nodes (MRF 0.0005, REC 0.0005, NHPC 0.0002, SJVN
      0.0001, MOIL 0.0029).
    - Sane-looking cpp unweighted top5 did exist (Titagarh Rail Systems
      931846.14, Schneider Electric Infrastructure 893391.30, Zydus
      Lifesciences 887849.97, Genus Power Infrastructures 772052.58, Adani
      Total Gas 755971.20) — plausible magnitudes, but with 99.5% of sampled
      values disagreeing with python, the whole array is untrustworthy.
    - Toy C8 ring (uniform 0.214 both modes) is FINE → data-dependent;
      plausibly unreachable-pair / normalization handling (DBL_MAX or inf
      accumulating through Brandes). Not chased to root cause — unusable
      regardless.

D2. LOUVAIN NOT HYBRID-WIRED:
    - easygraph/functions/community/louvain.py `louvain_communities` has NO
      @hybrid decorator; grep finds NO python-side call to
      cpp_louvain_communities → the cpp export is dead code from the API.
    - The python path CRASHES on GraphC objects: louvain_partitions →
      _gen_graph → H.add_node(i, nodes=nodes) passes a set into the C++
      add_node → pybind11 RuntimeError "Unable to cast Python instance of
      type <class 'set'> to C++ type '?'".
    - Calling cpp_easygraph.cpp_louvain_communities(G, "weight") DIRECTLY
      works (results above). Signature: (G, weight='weight',
      threshold=2e-05, resolution=1.0). Returns list of node-INDEX groups;
      on the 12-node/16-edge toy it returned [] (empty — another quirk;
      only trusted at full scale).

D3. ODR VIOLATION in cpp sources: CSRMatrix defined with different fields in
    eigenvector.cpp vs katz_centrality.cpp (LTO type-mismatch warnings).
    Works today; UB landmine for -fvisibility or future LTO defaults.

D4. MINOR: import easygraph prints spurious "Please install Pytorch"
    warnings even with torch installed (their try-import detector is
    brittle). Non-blocking. requirements.txt also pins torch<=2.3.0 while we
    run torch 2.14.0+cpu fine for core algorithms.

D5. PACKAGING: setup_requires Cython fetch broken; setup.py install broken
    (easy_install contourpy sdist) with a shadowing-egg side effect; no pip
    in uv venvs interacts badly with every recipe written as `pip ...`.

### 7. VERDICT (unchanged decision, sharper evidence)
The recipe DOES unlock real C++ speed: cpp pagerank ~70–100x faster than the
wheel (and ~10x faster than Onager's full loop incl remap), cpp louvain
15ms with the best modularity seen in the whole eval (Q=0.48, 12–13 balanced
communities — beating both easygraph-python and the wheel eval's numbers).

Adoption still fails:
- Betweenness — the ONE op Onager legitimately cannot do weighted, i.e.
    the main reason to want a second engine — is broken in the only fast
    path (D1). igraph does weighted BC correctly in ms.
- The working API is a maze: GraphC + free functions + cflag + node_index
    remap + dead/unwired exports (D2).
- Gaps unchanged from the wheel eval: MultiGraph broken, link-pred
    None-stubs, VoteRank/harmonic/local-reaching/Leiden absent.
- Build needs local patches (cmake/cython/install) — not a clean adopt.

KEEP: Onager prod, igraph lab/second engine. NO Easy-Graph.
EasyGraph-cpp remains a useful reference datapoint: "fast good-Q Louvain
exists outside igraph" (0.015s / Q=0.48 at our scale).

### 8. WHERE THIS LANDED
* doc/local/evaluations/graph_layer.md — new section "Easy-Graph C++
  source-build re-test (2026-09-12 ...)" (condensed recipe + numbers +
  defects + verdict) and a pointer line under "Final decision" noting the
  re-test executed and the decision stands.
* This file — the full run log incl. operator notes verbatim.
* make search-fresh APPLY=1 run after both doc edits (all three indexes
  fresh; embed cache full-hit).
* Tree left dirty for operator staging (no stg/git lifecycle ops per
  AGENTS.md).
* Volatile: /tmp/Easy-Graph, /tmp/venv_eg311, /tmp/eval_eg_cpp.py — recreate
  from §3 recipe if ever needed again.

### 9. ADDENDUM — 3.14 SOURCE BUILD (2026-09-12, later same day)
Trigger: operator installed cmake system-wide (4.2.3) and asked whether the
build failures were harness/3.11-bound. Re-ran the build on Python 3.14.

Result: **EasyGraph C++ builds and computes on Python 3.14.0.**

  venv:   uv venv /tmp/venv_eg314 --python 3.14
  deps:   uv pip install --python /tmp/venv_eg314/bin/python pybind11 cython             setuptools scipy networkx numpy
          (pybind11 3.1.0, cython 3.3.0, numpy 2.5.3; setuptools is REQUIRED
          on >=3.12 — setup.py does `from distutils import sysconfig` and
          stdlib distutils is gone; setuptools supplies the shim)
  build:  /tmp/venv_eg314/bin/python setup.py build_ext
          /tmp/venv_eg314/bin/python setup.py build_ext --inplace
          (system cmake 4.2.3 — no PATH prefix needed)

  GOTCHA: skipping --inplace leaves the .so only under build/lib.../, and
  `import cpp_easygraph` then resolves to the SOURCE DIRECTORY as an empty
  namespace package (dir() == []). Always finish with --inplace.

  Smoke (toy 4n/5e): cpp_pagerank symmetric 0.3451/0.1549 correct;
  cpp_betweenness_centrality 0.167 uniform; cpp_louvain_communities runs
  (single group on tiny graphs — known small-graph quirk, §6 D2 context).
  Note the cpp signatures are keyword-only for weight:
  cpp_pagerank(G, weight="weight") — positional "weight" is a TypeError.

Recipe deltas vs §3 (the 3.11 recipe): + setuptools; numpy unpinned
(2.5.3); no torch at build time (torch remains required only for
`import easygraph`); no PATH prefix (system cmake). **The 3.11 venv
requirement is RETIRED for builds.** §6 adoption blockers are code-level
(betweenness overflow, louvain wiring, ODR) — unaffected by interpreter
version, so the §7 verdict (NO Easy-Graph stands) is unchanged.

FULL-SCALE REPRODUCTION ON 3.14 (/tmp/eval314.py, same research.db
projection): D1 reproduces EXACTLY — betweenness overflows 378/1648 in
BOTH modes (same node count; sane top5 identical to the 3.11 run:
Titagarh 931846.1, Schneider 893391.3), i.e. deterministic upstream bug,
not UB flakiness. PR(w) 0.105s correct (top5 = india 0.0286 /
A_Quarter 0.0124 / Blackrock 0.004 / Automotive 0.004 / Technology
0.0035 — identical to Onager + wheel + 3.11). GraphC-path louvain via
raw cpp export: 0.014s, 11 comms, Q=0.4817 (3.11: 12-13, Q=0.482-0.484 —
same quality band). Parallel-edge collapse identical (16552 in-graph).
