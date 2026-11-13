"""Tests for the semantic_cluster e-means lane (semantic_clusters.py).

Covers the pure-python ARI (no sklearn dep), the pinned-recipe payload
(S3), the lazy-import failure mode (S2), the empty-store skip (W1), the
--apply write path (write_analytics, replace_population), and the app label
allowlist wiring (S4).
"""

from __future__ import annotations

import builtins
import json
import sys
from pathlib import Path

import duckdb
import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from helpers.graph import semantic_clusters as sem  # noqa: E402

GRAPH_ANALYTICS_DDL = (
    "CREATE TABLE graph_analytics(entity_name VARCHAR, metric VARCHAR, "
    '"value" VARCHAR NOT NULL, computed_at TIMESTAMP '
    "DEFAULT(CURRENT_TIMESTAMP) NOT NULL, PRIMARY KEY(entity_name, metric))"
)


def _embed_tags(vec):
    return np.asarray(vec, dtype=np.float32).tobytes()


class TestARI:
    def test_identical_partitions_ari_one(self):
        assert sem.adjusted_rand_score([0, 0, 1, 1], [0, 0, 1, 1]) == 1.0
        # label permutation is the same partition
        assert sem.adjusted_rand_score([0, 0, 1, 1], [1, 1, 0, 0]) == 1.0

    def test_known_small_ari_value(self):
        # formula cross-check: ARI([0,0,1,1], [0,1,0,1]) = -0.5
        assert sem.adjusted_rand_score([0, 0, 1, 1], [0, 1, 0, 1]) == pytest.approx(-0.5)
        # random-leaning small case stays near 0
        a = [0, 0, 0, 1, 1, 1]
        b = [0, 0, 1, 1, 2, 2]
        ari = sem.adjusted_rand_score(a, b)
        assert -0.25 <= ari <= 0.25


class TestPayload:
    def test_pinned_recipe_persisted(self):
        meta = {
            "k": 42,
            "seed": 42,
            "nepochs": 5,
            "batch_law": "throughput",
            "fit_s": 0.345,
            "empty_clusters": 0,
            "model": "embeddinggemma-2-q8_512",
        }
        payload = sem._payload(3, meta)
        assert payload["cluster"] == 3
        assert payload["k"] == 42
        assert payload["seed"] == 42
        assert payload["nepochs"] == 5
        assert payload["batch_law"] == "throughput"


class TestLazyImport:
    def test_missing_faiss_raises_actionable(self, monkeypatch):
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "faiss" or name.startswith("faiss."):
                raise ImportError("No module named 'faiss'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(ImportError) as ei:
            sem._require_faiss()
        assert "faiss-cpu" in str(ei.value)
        assert "uv pip install faiss-cpu" in str(ei.value)


class TestSkipPath:
    def test_empty_store_skips(self, capsys):
        conn = duckdb.connect()
        rc = sem._cli([], _conn=conn)
        conn.close()
        assert rc == 0
        assert "skipping" in capsys.readouterr().out


class TestApplyWritePath:
    def test_apply_writes_payload_with_pins(self, monkeypatch, capsys):
        conn = duckdb.connect()
        conn.execute(GRAPH_ANALYTICS_DDL)
        conn.execute(
            "CREATE TABLE company_embeddings(company_name VARCHAR, embedding BLOB, "
            "model VARCHAR, created_at TIMESTAMP)"
        )
        comps = ["Alpha", "Beta", "Gamma"]
        for i, name in enumerate(comps):
            conn.execute(
                "INSERT INTO company_embeddings(company_name, embedding, model) "
                "VALUES (?, ?, 'embeddinggemma-2-q8_512')",
                (name, _embed_tags([float(i + 1)] + [0.0] * 511)),
            )
        conn.execute("CREATE TABLE entities(name VARCHAR, sector_classification VARCHAR)")
        conn.executemany(
            "INSERT INTO entities(name, sector_classification) VALUES (?, ?)",
            [("Alpha", "Banking"), ("Beta", "Energy"), ("Gamma", "Banking")],
        )
        # hermetic: stub the fit so no dependency on faiss/embeddings here
        monkeypatch.setattr(
            sem,
            "fit_clusters",
            lambda X, **kw: (
                np.array([0, 1, 0]),
                {
                    "k": 42,
                    "seed": 42,
                    "nepochs": 5,
                    "batch_law": "throughput",
                    "fit_s": 0.1,
                    "empty_clusters": 0,
                    "model": "embeddinggemma-2-q8_512",
                },
            ),
        )
        rc = sem._cli(["--apply"], _conn=conn)
        assert rc == 0
        assert "APPLY: wrote 3 rows" in capsys.readouterr().out
        rows = conn.execute(
            "SELECT entity_name, metric, value FROM graph_analytics "
            "WHERE metric='semantic_cluster' ORDER BY entity_name"
        ).fetchall()
        assert len(rows) == 3
        payload = json.loads(rows[0][2])
        assert payload["cluster"] == 0
        assert payload["seed"] == 42
        assert payload["batch_law"] == "throughput"
        conn.close()


class TestAppWiring:
    def test_label_allowlist_and_key(self):
        import app as A  # noqa: E402

        assert "semantic_cluster" in A._LABEL_GRAPH_METRICS
        # the label-key map is inline in the handler; assert source contains
        # the mapping for the metric (wiring regression guard)
        src = Path(REPO_ROOT / "app.py").read_text()
        assert '"semantic_cluster": "cluster"' in src
