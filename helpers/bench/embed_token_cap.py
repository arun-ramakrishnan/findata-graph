#!/usr/bin/env python3
"""Token-cap bite measurement: how much corpus text survives the 512-token
embedding window on each surface.

Born of the 2026-09-06 capability-gap measurement (doc/local/
embed_model_eval.txt, "Token-cap bite measurement"): frontmatter is
already stripped from all text bases (YAML is NOT the issue), yet 79% of
notes truncate at the cap — median note is 1,210 tokens, token-mass
retained 39% (notes) / 53% (companies) / 70% (docs, section-chunked).
The search eval's 1.00 ceiling measures note HEADS; deep content is
invisible to the vectors. Reopen trigger for the granite long-context
probe or bge note-sectioning — see the record for the ranked options.

Usage:
    python3 helpers/bench/embed_token_cap.py [gguf-path] [cap]
Defaults: the live bge-small model, cap 512 (its trained rope maximum;
llama.cpp has no encoder rope-scaling, so chunking is the only bge-side
path past it).
"""

import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DEFAULT_MODEL = REPO / "models/bge-small-en-v1.5-q8_0.gguf"


def load_bases() -> dict[str, list[str]]:
    import sqlite3

    out = {}
    c = sqlite3.connect(f"file:{REPO}/memory/research.db?mode=ro", uri=True)
    out["notes"] = [
        f"{t}\n{s}\n{(ct or '')[:8000]}"
        for t, s, ct in c.execute("SELECT title, sector, content FROM note_search")
    ]
    c.close()
    d = sqlite3.connect(f"file:{REPO}/memory/doc_search.db?mode=ro", uri=True)
    out["docs"] = [
        f"{t}\n{sec}\n{(ct or '')[:4000]}"
        for t, sec, ct in d.execute("SELECT c0, c1, c4 FROM doc_search_content")
    ]
    d.close()
    return out


def main() -> None:
    from llama_cpp import Llama

    model = sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT_MODEL)
    cap = int(sys.argv[2]) if len(sys.argv) > 2 else 512
    m = Llama(model, embedding=True, n_ctx=cap, verbose=False, n_threads=1)

    def tok_len(text: str) -> int:
        return len(m.tokenize(text.encode("utf-8", "ignore"), add_bos=True))

    for name, texts in load_bases().items():
        lens = sorted(tok_len(t) for t in texts)
        n = len(lens)
        fully = sum(1 for L in lens if L <= cap)
        inside = sum(min(L, cap) for L in lens)
        print(
            f"{name:10s} n={n} median={statistics.median(lens):.0f} "
            f"p90={lens[int(n * 0.9)]} tokens | fully-inside-{cap}: "
            f"{fully} ({100 * fully / n:.0f}%) | token-mass retained: "
            f"{100 * inside / sum(lens):.0f}%"
        )
        over = [L for L in lens if L > cap]
        if over:
            om = statistics.median(over)
            print(
                f"           truncated subset: {len(over)} texts, median {om:.0f} "
                f"tokens (median retained ~{100 * cap / om:.0f}%)"
            )


if __name__ == "__main__":
    main()
