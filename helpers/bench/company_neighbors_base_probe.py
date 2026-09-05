#!/usr/bin/env python3
"""S7 companies-base experiment — the semantic-neighbors regression (2026-09-06).

Post-granite-swap neighbors same-sector dominance is 3/10 vs bge's 5/10
(embed_eval, top-5 cosine, >=3/5 same sector). Mechanism hypothesis: the
production base f"{name}. {sector}. {content[:5000]}" was designed around
bge's 512-token window — truncation at ~2,000 chars acted as an accidental
feature selector weighting the sector token and opening descriptors;
granite's 2k-token window reads the full diversified business description,
so similarity follows business-adjacency over the sector label (miss
pattern: NVIDIA -> AMD/Palantir/Adobe).

Legs (identical neighbor semantics to helpers/graph/query.py
semantic_neighbors: cosine top-5 over the full embedded company pool,
self excluded, sector from entities):

- live-full          vectors straight from company_embeddings — pre-S7
                     landing these were the [:5000] full-note bases (read
                     3/10, the harness validation anchor); post-landing
                     the table holds the OVERVIEW bases (read 6/10), so
                     this leg tracks whatever production serves.
- bge-full-cache     the RETIRED [:5000] production bases read from the
                     bge rollback cohort (embeddings.py rides the shared
                     cache). Extended the 5/10 baseline to the 30-seed
                     readout at zero embed cost; SKIPPED if cohort
                     coverage is short.
- granite-trunc2000  f"{name}. {sector}. {content[:2000]}" — the
                     bge-window equivalent under granite.
- granite-structured f"{name}. {sector}. {lead}" — lead = body up to the
                     first markdown heading / blank-line paragraph,
                     capped 1,500 chars (opening-concentration hypothesis).

Readout on BOTH seed sets: the 10 recorded (comparability with 3/10 and
5/10) and an extended 30 (10 recorded + 20 deterministic stride picks
among note-bearing companies — the n=10 delta is 2 hits, too thin to
tune against). Adopt a base only if it beats live-full on the 30-seed
readout; on landing, vss (12/12) must re-verify — company_embeddings
feeds both consumers.

Usage:
    python3 helpers/bench/company_neighbors_base_probe.py            # all legs
    python3 helpers/bench/company_neighbors_base_probe.py live-full  # one leg
    python3 helpers/bench/company_neighbors_base_probe.py --json out.json
"""

import json
import re
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DB_PATH = REPO / "memory" / "research.db"
QUESTIONS_PATH = REPO / "helpers" / "misc" / "embed_eval_questions.json"
N_EXTRA_SEEDS = 20
BGE_MODEL = "bge-small-en-v1.5"  # rollback cohort label (post-swap prod is granite)

_H_RE = re.compile(r"^#{1,6}\s", re.M)
_H2_RE = re.compile(r"^##\s", re.M)


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _frontmatter_strip(content: str) -> str:
    """Mirror _get_company_text's strip (helpers/graph/embeddings.py)."""
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return parts[2]
    return content


def _lead(content: str) -> str:
    """Body of the first ## section (the Company Overview prose) — the
    honest opening-concentration base. Company notes open with an H1 title
    + ticker/sector/cap metadata line, so cut-at-first-heading yields an
    EMPTY lead and the base degenerates to 'Name. Sector.' (sector-token
    string-matching: the void 28/30 of the first structured run). Falls
    back to the truncated-window shape when no ## section exists."""
    m = _H2_RE.search(content)
    if m:
        rest = content[m.end() :]
        nxt = _H_RE.search(rest)
        body = rest[: nxt.start()] if nxt else rest
        return body.strip()[:1500]
    return content[:1500].strip()


def _base(leg: str, name: str, sector: str, content: str) -> str:
    head = f"{name}. {sector or ''}. "
    if leg == "live-full" or leg == "bge-full-cache":
        return head + content[:5000]
    if leg == "granite-trunc2000":
        return head + content[:2000]
    if leg == "granite-structured":
        return head + _lead(content)
    raise ValueError(f"unknown leg {leg!r}")


def _companies() -> list[tuple[str, str, str]]:
    """(name, sector, frontmatter-stripped note body) for every embedded
    company, mirroring populate_local's entity_type='company' selection."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT name, file_path, sector_classification FROM entities "
        "WHERE entity_type = 'company' ORDER BY name"
    ).fetchall()
    conn.close()

    out = []
    for name, file_path, sector in rows:
        content = ""
        if file_path:
            full = REPO / file_path
            if full.exists():
                try:
                    content = _frontmatter_strip(full.read_text(encoding="utf-8", errors="replace"))
                except Exception:  # noqa: S110  # mirror the prod best-effort read
                    pass
        out.append((name, sector or "", content))
    return out


def _seeds(names_with_notes: list[str]) -> tuple[list[dict], list[dict]]:
    recorded = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))["neighbors"]
    pool = [n for n in names_with_notes if n not in {q["company"] for q in recorded}]
    extras = [
        {"company": pool[i * len(pool) // N_EXTRA_SEEDS], "sector": None}
        for i in range(N_EXTRA_SEEDS)
    ]
    return recorded, extras


def _live_vectors() -> dict[str, list[float]]:
    from helpers.core.local_embedder import MODEL_ID

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT company_name, embedding FROM company_embeddings WHERE model = ?",
        (MODEL_ID,),
    ).fetchall()
    conn.close()
    return {n: json.loads(e) for n, e in rows}


def _cache_vectors(bases: dict[str, str]) -> dict[str, list[float]] | None:
    """Old-production-base vectors from the bge rollback cohort, or None if
    coverage is short (a shrunken neighbor pool would skew the readout)."""
    from helpers.core.embed_cache import _hash
    from helpers.core.vec_search import _attach_vec_db

    conn = sqlite3.connect(f"file:{REPO / 'memory/embed_store.db'}?mode=ro", uri=True)
    _attach_vec_db(conn)
    cached = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT text_hash, embedding FROM vecdb.embed_cache WHERE model = ?",
            (BGE_MODEL,),
        )
    }
    conn.close()

    out, missing = {}, []
    for name, base in bases.items():
        j = cached.get(_hash(base))
        if j is None:
            missing.append(name)
        else:
            out[name] = json.loads(j)
    if missing:
        _eprint(
            f"[companies] bge-full-cache: cohort covers {len(out)}/{len(bases)} "
            f"({len(missing)} missing, e.g. {missing[:3]}) — leg SKIPPED"
        )
        return None
    return out


def _fresh_vectors(leg: str, bases: dict[str, str]) -> dict[str, list[float]]:
    """Serial per-text granite embed of the leg's bases; checkpoint keyed by
    base sha256 in a MODEL_ID-suffixed scratch (cross-model scratch reuse
    is garbage — the 2026-09-05 swap lesson)."""
    from helpers.core.embed_cache import _hash
    from helpers.core import local_embedder
    from helpers.core.local_embedder import MODEL_ID

    scratch = Path(f"/tmp/company_base_probe_{leg}_{MODEL_ID}.json")  # noqa: S108  # throwaway probe scratch, single-user box
    done: dict[str, list[float]] = {}
    if scratch.exists():
        done = json.loads(scratch.read_text(encoding="utf-8"))

    out, embeds = {}, {}
    t0 = time.perf_counter()
    for i, (name, base) in enumerate(bases.items(), 1):
        h = _hash(base)
        v = done.get(h)
        if v is None:
            v = local_embedder.embed_document(base)
            embeds[h] = v
            done[h] = v
            if len(embeds) % 64 == 0:
                rate = len(embeds) / (time.perf_counter() - t0)
                _eprint(f"[companies] {leg} {i}/{len(bases)} ({rate:.1f}/s)")
        out[name] = v
    if embeds:
        scratch.write_text(json.dumps(done), encoding="utf-8")
    _eprint(f"[companies] {leg}: {len(out)} vectors ({len(embeds)} fresh embeds)")
    return out


def _score(
    vecs: dict[str, list[float]],
    sectors: dict[str, str],
    seeds: list[tuple[str, str]],
) -> tuple[int, list[str]]:
    import math

    norm = {n: math.sqrt(sum(x * x for x in v)) for n, v in vecs.items()}
    hits, misses = 0, []
    for seed, ssector in seeds:
        sv = vecs.get(seed)
        if sv is None:
            misses.append(f"{seed}: NO VECTOR")
            continue
        sims = []
        for n, v in vecs.items():
            if n == seed:
                continue
            dot = sum(a * b for a, b in zip(sv, v))
            sim = dot / (norm[seed] * norm[n])
            if sim > 0:  # mirror semantic_neighbors' positive-sim filter
                sims.append((sim, n))
        sims.sort(reverse=True)
        top5 = [n for _s, n in sims[:5]]
        same = sum(1 for n in top5 if sectors.get(n) == ssector)
        if same >= 3:
            hits += 1
        else:
            misses.append(
                f"{seed} ({ssector}): {same}/5 — "
                + ", ".join(f"{n}({sectors.get(n, '?')})" for n in top5)
            )
    return hits, misses


def run() -> int:  # noqa: C901  # bench script: one straight-line report
    from helpers.core.local_embedder import MODEL_ID

    all_legs = ["live-full", "bge-full-cache", "granite-trunc2000", "granite-structured"]
    argv = list(sys.argv[1:])
    if "--json" in argv:  # drop the flag AND its value before leg collection
        i = argv.index("--json")
        argv = argv[:i] + argv[i + 2 :]
    legs = argv or all_legs
    bad = [a for a in legs if a not in all_legs]
    if bad:
        raise SystemExit(f"unknown leg(s) {bad} — known: {all_legs}")

    companies = _companies()
    sectors = {n: s for n, s, _c in companies}
    names_with_notes = [n for n, _s, c in companies if c]
    recorded, extras = _seeds(names_with_notes)
    for e in extras:
        e["sector"] = sectors.get(e["company"], "")
    seeds10 = [(q["company"], q["sector"]) for q in recorded]
    seeds30 = seeds10 + [(e["company"], e["sector"]) for e in extras]
    _eprint(
        f"[companies] {len(companies)} embedded companies "
        f"({len(names_with_notes)} with notes); seeds 10 recorded + "
        f"{len(extras)} deterministic = {len(seeds30)}"
    )

    results: dict[str, dict[str, int | list[str]]] = {}
    score30s: dict[str, int] = {}
    for leg in legs:
        if leg == "live-full":
            vecs = _live_vectors()
        else:
            bases = {n: _base(leg, n, s, c) for n, s, c in companies}
            # Degenerate-base guard: a construction that yields the bare
            # "Name. Sector." head (empty body) measures sector-token
            # string-matching, not semantics — the void 28/30 lesson.
            degenerate = sum(
                1 for n, b in bases.items() if len(b) <= len(f"{n}. {sectors.get(n, '')}. ") + 1
            )
            if degenerate > len(bases) // 20:
                raise SystemExit(
                    f"{leg}: {degenerate}/{len(bases)} bases have no body — "
                    "construction is degenerate, refusing to measure"
                )
            vecs = _cache_vectors(bases) if leg == "bge-full-cache" else _fresh_vectors(leg, bases)
            if vecs is None:
                continue
        # live-full anchors the harness against the recorded eval number
        # (3/10 on the [:5000] base at S7 time; 6/10 after the overview
        # base landed — a different read means the harness drifted).
        h10, m10 = _score(vecs, sectors, seeds10)
        h30, m30 = _score(vecs, sectors, seeds30)
        results[leg] = {"score10": h10, "score30": h30, "misses30": m30}
        score30s[leg] = h30
        _eprint(f"[companies] {leg}: 10-seed {h10}/10 | 30-seed {h30}/30")
        if leg == "live-full" and h10 not in (3, 6):
            _eprint(
                f"[companies] WARNING: live-full read {h10}/10 — expected "
                "3 (retired [:5000] base) or 6 (overview base); harness "
                "faithfulness broken, treat all legs as void"
            )
        for m in m30:
            _eprint(f"    MISS {m}")

    prod = "live-full" in results
    print(f"\ncompanies base experiment (neighbors >=3/5 same-sector, prod={MODEL_ID})")
    print(f"{'leg':20s} {'10-seed':8s} {'30-seed':8s}")
    for leg, r in results.items():
        print(f"{leg:20s} {r['score10']}/10     {r['score30']}/30")
    if prod:
        base30 = score30s["live-full"]
        verdict = {
            leg: "ADOPT-candidate" if s30 > base30 else "no-gain"
            for leg, s30 in score30s.items()
            if leg != "live-full"
        }
        print(f"\nvs live-full 30-seed {base30}/30: {verdict}")
    if "--json" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--json") + 1])
        out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        _eprint(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
