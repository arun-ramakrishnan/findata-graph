"""Tests for `--resolve-orphans` and `--keep` in `harvest_conversations`.

Both exist because the corpus writer is add/merge-only: `SessionWriter.flush`
iterates the sessions a source *reported* and never enumerates what vanished,
so a session deleted in the harness leaves its parquet behind claiming
`opencode:live`. Measured 2026-10-17: 29 such pure orphans / 16,949 rows, all
still searchable in convo_search.

The snapshot-backed remainder (123 sessions) is DELIBERATE retention — the
snapshot lanes exist so history outlives the harness — and every test here
pins that it is left alone.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from helpers.maintenance import harvest_conversations as hc


def _write(
    root: Path, harness: str, sid: str, *, source: str, rows: int = 3, newest_days_ago: int = 1
) -> Path:
    conv = root / harness / "conversations"
    conv.mkdir(parents=True, exist_ok=True)
    newest = datetime.now(UTC) - timedelta(days=newest_days_ago)
    data = {
        "part_id": [f"p{i}" for i in range(rows)],
        "message_id": [f"m{i}" for i in range(rows)],
        "session_id": [sid] * rows,
        "ts": [newest - timedelta(minutes=i) for i in range(rows)],
        "role": ["user"] * rows,
        "agent": [""] * rows,
        "model": ["m"] * rows,
        "part_type": ["text"] * rows,
        "text": ["t"] * rows,
        "meta": ["{}"] * rows,
        "source": [source] * rows,
    }
    f = conv / f"{hc._safe(sid)}.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [{k: v[i] for k, v in data.items()} for i in range(rows)], schema=hc._SCHEMA
        ),
        f,
    )
    return f


def _append(root: Path, harness: str, sid: str, *, source: str, rows: int = 3) -> Path:
    """Add rows to an existing session file with a DIFFERENT source.

    Models a partial relabel: `--resolve-orphans` wrote `orphan:<harness>` for
    the rows it saw, and a later harvest appended `<harness>:live` rows to the
    same parquet. The file then carries both labels at once.
    """
    f = root / harness / "conversations" / f"{hc._safe(sid)}.parquet"
    base = pq.read_table(f).to_pylist()
    newest = max(r["ts"] for r in base)
    fresh = [
        {
            **base[0],
            "part_id": f"new{i}",
            "message_id": f"nm{i}",
            "ts": newest + timedelta(minutes=i + 1),
            "source": source,
        }
        for i in range(rows)
    ]
    pq.write_table(pa.Table.from_pylist(base + fresh, schema=hc._SCHEMA), f)
    return f


def _live_db(tmp_path: Path, ids: list[str]) -> Path:
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE session (id TEXT PRIMARY KEY)")
    con.executemany("INSERT INTO session VALUES (?)", [(i,) for i in ids])
    con.commit()
    con.close()
    return db


def _sources(f: Path) -> set[str]:
    return set(pq.read_table(f, columns=["source"]).column("source").to_pylist())


class TestResolveOrphans:
    def _harness(self, tmp_path: Path, live: list[str], monkeypatch) -> Path:
        root = tmp_path / "corpus"
        _live_db(tmp_path, live)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setattr(hc, "_oc_live_ids", lambda: set(live))
        monkeypatch.setitem(hc._LIVE_SESSION_IDS, "opencode", set(live))
        return root

    def test_relabels_a_session_the_harness_dropped(self, tmp_path: Path, monkeypatch):
        root = self._harness(tmp_path, ["ses_alive"], monkeypatch)
        f = _write(root, "opencode", "ses_gone", source="opencode:live")
        hc._resolve_orphans(root, dry_run=False)
        assert _sources(f) == {"orphan:opencode"}
        # rows are RETAINED — relabelling is not deleting
        assert pq.read_table(f).num_rows == 3

    def test_leaves_live_sessions_alone(self, tmp_path: Path, monkeypatch):
        root = self._harness(tmp_path, ["ses_alive"], monkeypatch)
        f = _write(root, "opencode", "ses_alive", source="opencode:live")
        hc._resolve_orphans(root, dry_run=False)
        assert _sources(f) == {"opencode:live"}

    def test_leaves_snapshot_backed_sessions_alone(self, tmp_path: Path, monkeypatch):
        """Snapshot retention is the feature, not the bug."""
        root = self._harness(tmp_path, [], monkeypatch)
        f = _write(root, "opencode", "ses_snap", source="opencode-snap:2026-07-26_19-00-02")
        hc._resolve_orphans(root, dry_run=False)
        assert _sources(f) == {"opencode-snap:2026-07-26_19-00-02"}

    def test_skips_harnesses_with_no_live_manifest(self, tmp_path: Path, monkeypatch):
        """dsh/prime/zcode cannot be enumerated — guessing would relabel 500 live files."""
        root = tmp_path / "corpus"
        f = _write(root, "zcode", "z_gone", source="zcode:live")
        monkeypatch.setitem(hc._LIVE_SESSION_IDS, "zcode", None)
        hc._resolve_orphans(root, dry_run=False)
        assert _sources(f) == {"zcode:live"}

    def test_dry_run_writes_nothing(self, tmp_path: Path, monkeypatch):
        root = self._harness(tmp_path, [], monkeypatch)
        f = _write(root, "opencode", "ses_gone", source="opencode:live")
        hc._resolve_orphans(root, dry_run=True)
        assert _sources(f) == {"opencode:live"}

    def test_is_idempotent(self, tmp_path: Path, monkeypatch):
        root = self._harness(tmp_path, [], monkeypatch)
        f = _write(root, "opencode", "ses_gone", source="opencode:live")
        hc._resolve_orphans(root, dry_run=False)
        assert hc._resolve_orphans(root, dry_run=False) == (0, 0), "second pass is a no-op"
        assert _sources(f) == {"orphan:opencode"}

    def test_missing_root_is_not_fatal(self, tmp_path: Path, capsys):
        assert hc._resolve_orphans(tmp_path / "nope", dry_run=True) == (0, 0)
        assert "no corpus root" in capsys.readouterr().err


class TestPurgeOlderThan:
    def test_purges_by_row_timestamp_not_mtime(self, tmp_path: Path):
        """mtime tracks the last APPLY, not the conversation — purging on it
        would keep dead sessions forever and delete live ones on restore."""
        root = tmp_path / "corpus"
        old = _write(root, "opencode", "ses_old", source="opencode:live", newest_days_ago=200)
        fresh = _write(root, "opencode", "ses_new", source="opencode:live", newest_days_ago=2)
        import os
        import time

        now = time.time()
        os.utime(old, (now, now))  # both files "fresh" on disk
        os.utime(fresh, (now, now))
        files, rows = hc._purge_older_than(root, "90d", dry_run=False)
        assert files == 1 and rows == 3
        assert not old.exists() and fresh.exists()

    def test_honours_the_range(self, tmp_path: Path):
        root = tmp_path / "corpus"
        a = _write(root, "opencode", "ses_100d", source="opencode:live", newest_days_ago=100)
        b = _write(root, "opencode", "ses_40d", source="opencode:live", newest_days_ago=40)
        hc._purge_older_than(root, "90d", dry_run=False)
        assert not a.exists() and b.exists()

    def test_dry_run_writes_nothing(self, tmp_path: Path, capsys):
        root = tmp_path / "corpus"
        a = _write(root, "opencode", "ses_old", source="opencode:live", newest_days_ago=400)
        hc._purge_older_than(root, "90d", dry_run=True)
        assert a.exists()
        assert "would purge 1" in capsys.readouterr().err

    def test_snapshot_retention_is_also_bounded_by_keep(self, tmp_path: Path):
        """--keep is about AGE, not provenance: an old snapshot is the first
        thing to go when someone asks to keep 30d."""
        root = tmp_path / "corpus"
        f = _write(
            root, "opencode", "ses_snap", source="opencode-snap:2026-01-01", newest_days_ago=300
        )
        hc._purge_older_than(root, "30d", dry_run=False)
        assert not f.exists()

    def test_empty_corpus(self, tmp_path: Path):
        root = tmp_path / "corpus"
        root.mkdir()
        assert hc._purge_older_than(root, "90d", dry_run=False) == (0, 0)


class TestDeletedSessionsAreReported:
    """`stale_deleted` was declared on the `--check` path since the v2 cutover
    and assigned NOWHERE — and 29 sessions / 16,949 rows sat behind it until
    someone counted by hand. These pin that it is populated, and that it gates
    only the unresolved subset."""

    def _root(self, tmp_path: Path, live: list[str], monkeypatch) -> Path:
        root = tmp_path / "corpus"
        _live_db(tmp_path, live)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setattr(hc, "_oc_live_ids", lambda: set(live))
        monkeypatch.setitem(hc._LIVE_SESSION_IDS, "opencode", set(live))
        return root

    def test_finds_a_deleted_session(self, tmp_path: Path, monkeypatch):
        root = self._root(tmp_path, [], monkeypatch)
        _write(root, "opencode", "ses_gone", source="opencode:live")
        deleted, unresolved = hc._deleted_sessions(root)
        assert deleted == ["ses_gone"]
        assert unresolved == ["ses_gone"], "still labelled opencode:live -> must gate"

    def test_live_session_is_not_reported(self, tmp_path: Path, monkeypatch):
        root = self._root(tmp_path, ["ses_here"], monkeypatch)
        _write(root, "opencode", "ses_here", source="opencode:live")
        assert hc._deleted_sessions(root) == ([], [])

    def test_relabelled_orphan_is_reported_but_does_not_gate(self, tmp_path: Path, monkeypatch):
        root = self._root(tmp_path, [], monkeypatch)
        _write(root, "opencode", "ses_gone", source="orphan:opencode")
        deleted, unresolved = hc._deleted_sessions(root)
        assert deleted == ["ses_gone"], "still worth reporting"
        assert unresolved == [], "--resolve-orphans must CLOSE the gate once"

    def test_PARTIAL_relabel_does_not_pass_as_resolved(self, tmp_path: Path, monkeypatch):
        """3 orphan rows + 39 opencode:live rows in ONE file is not resolved.

        `--resolve-orphans` relabels the rows present when it runs; rows
        harvested afterwards re-assert `<harness>:live`. The first version of
        this gate checked for the orphan label's PRESENCE, so a partial relabel
        passed — 3 real sessions, 2,926 rows.
        """
        root = self._root(tmp_path, [], monkeypatch)
        _write(root, "opencode", "ses_gone", source="orphan:opencode")
        _append(root, "opencode", "ses_gone", source="opencode:live")
        deleted, unresolved = hc._deleted_sessions(root)
        assert deleted == ["ses_gone"]
        assert unresolved == ["ses_gone"], "a mixed-provenance file must still gate"

    def test_snapshot_backed_is_not_drift(self, tmp_path: Path, monkeypatch):
        """Pure-snapshot provenance: reported as deleted, never gates."""
        root = self._root(tmp_path, [], monkeypatch)
        _write(root, "opencode", "ses_snap", source="opencode-snap:2026-07-26_19-00-02")
        deleted, unresolved = hc._deleted_sessions(root)
        assert deleted == ["ses_snap"], "the harness dropped it, so say so"
        assert unresolved == [], "but the provenance is truthful, so do not gate"

    def test_snapshot_does_not_excuse_a_live_label(self, tmp_path: Path, monkeypatch):
        """Retention and provenance are separate. A snapshot keeps the file; it
        does not make `opencode:live` true.

        Skipping snapshot-backed sessions wholesale hid 21,127 rows across 14
        sessions on 2026-10-17 — more than the 29 pure orphans it was written
        to protect. The snapshot rows stay; the live-claimed rows do not.
        """
        root = self._root(tmp_path, [], monkeypatch)
        _write(
            root,
            "opencode",
            "ses_mixed",
            source="opencode-snap:2026-07-26_19-00-02",
            rows=10,
        )
        _append(root, "opencode", "ses_mixed", source="opencode:live", rows=6)
        deleted, unresolved = hc._deleted_sessions(root)
        assert deleted == ["ses_mixed"]
        assert unresolved == ["ses_mixed"], "a snapshot backs retention, not the provenance claim"

    def test_relabel_keeps_snapshot_rows_and_rewrites_live_rows(self, tmp_path: Path, monkeypatch):
        root = self._root(tmp_path, [], monkeypatch)
        _write(root, "opencode", "ses_mixed", source="opencode-snap:S1", rows=10)
        _append(root, "opencode", "ses_mixed", source="opencode:live", rows=6)
        hc._resolve_orphans(root, dry_run=False)
        import pyarrow.parquet as pq

        srcs = set(
            pq.read_table(root / "opencode" / "conversations" / "ses_mixed.parquet")
            .column("source")
            .to_pylist()
        )
        assert srcs == {"opencode-snap:S1", "orphan:opencode"}, (
            "the snapshot label must survive the relabel verbatim"
        )
        assert hc._deleted_sessions(root) == (["ses_mixed"], []), "gate must close"

    def test_harness_without_a_manifest_is_skipped(self, tmp_path: Path, monkeypatch):
        root = tmp_path / "corpus"
        _write(root, "prime-rlm", "p_gone", source="prime:live")
        monkeypatch.setattr(hc, "_live_session_ids", lambda h: None)
        assert hc._deleted_sessions(root) == ([], [])

    def test_missing_root(self, tmp_path: Path):
        assert hc._deleted_sessions(tmp_path / "nope") == ([], [])

    def test_check_path_populates_the_field(self, tmp_path: Path, monkeypatch):
        root = self._root(tmp_path, [], monkeypatch)
        _write(root, "opencode", "ses_gone", source="opencode:live")
        monkeypatch.setattr(hc, "_drift", lambda store, cr: [])
        monkeypatch.setattr(hc, "_coverage", lambda store: [])
        out = hc.harvest(check=True, corpus_root=root, db=tmp_path / "meta.db")
        assert out["stale_deleted"] == ["ses_gone"]
        assert out["stale_deleted_unresolved"] == ["ses_gone"]
        assert out["index_stale"] is False, "source drift is empty; the orphan gate is separate"

    def test_resolved_orphans_keep_check_green(self, tmp_path: Path, monkeypatch):
        root = self._root(tmp_path, [], monkeypatch)
        _write(root, "opencode", "ses_gone", source="orphan:opencode")
        monkeypatch.setattr(hc, "_drift", lambda store, cr: [])
        monkeypatch.setattr(hc, "_coverage", lambda store: [])
        out = hc.harvest(check=True, corpus_root=root, db=tmp_path / "meta.db")
        assert out["stale_deleted"] and not out["stale_deleted_unresolved"]


class TestIndexProvenanceIsRefreshed:
    """The corpus is the truth; `convo_search.provenance` is a COPY.

    A relabel rewrites `source` in place, so the rebuild's file-diff sees
    nothing and the index keeps serving the stale label — 912 rows did, and
    `convo-fresh APPLY=1` did not fix them.
    """

    def test_resolve_orphans_refreshes_index_provenance(self, tmp_path, monkeypatch):
        root = tmp_path / "corpus"
        _write(root, "opencode", "ses_gone", source="opencode:live")
        seen: list[list] = []
        monkeypatch.setattr(
            hc,
            "_refresh_index_provenance",
            lambda files, dry_run=False: seen.append(list(files)),
        )
        hc.main(["--resolve-orphans", "--corpus-root", str(root)])
        assert seen and [p.name for p in seen[0]] == ["ses_gone.parquet"], (
            "the files just relabelled must be handed to the index refresh"
        )

    def test_dry_run_does_not_refresh(self, tmp_path, monkeypatch):
        root = tmp_path / "corpus"
        _write(root, "opencode", "ses_gone", source="opencode:live")
        seen: list[tuple] = []
        monkeypatch.setattr(
            hc,
            "_refresh_index_provenance",
            lambda files, dry_run=False: seen.append((list(files), dry_run)),
        )
        hc.main(["--resolve-orphans", "--dry-run", "--corpus-root", str(root)])
        files, dry = seen[0]
        assert [p.name for p in files] == ["ses_gone.parquet"], "still reports the scope"
        assert dry is True, "and must reach the refresh helper flagged dry-run"

    def test_refresh_helper_is_a_noop_without_an_index(self, tmp_path, capsys):
        hc.INDEX_DB = tmp_path / "absent.duckdb"
        hc._refresh_index_provenance([tmp_path / "x.parquet"])
        assert "no index" in capsys.readouterr().err

    def test_refresh_helper_skips_dry_run(self, capsys):
        hc._refresh_index_provenance([hc.Path("/x.parquet")], dry_run=True)
        assert capsys.readouterr().err == "", "dry-run must be completely silent"


class TestProvenanceDriftIsChecked:
    """`provenance` is a COPY; an in-place rewrite moves no file and no row, so
    nothing else in `--check` can see the index disagree with the corpus."""

    def test_clean_corpus_reports_no_drift(self, tmp_path):
        assert hc._provenance_drift(tmp_path, tmp_path / "absent.duckdb") == []

    def test_a_single_disagreeing_row_is_reported(self, tmp_path, monkeypatch):
        import duckdb

        root = tmp_path / "corpus"
        _write(root, "opencode", "ses_x", source="orphan:opencode", rows=4)
        idx = tmp_path / "idx.duckdb"
        con = duckdb.connect(str(idx))
        con.execute(
            "CREATE TABLE convo_search (file_path VARCHAR, row_no INTEGER, provenance VARCHAR)"
        )
        rel = str((root / "opencode" / "conversations" / "ses_x.parquet").resolve())
        con.executemany(
            "INSERT INTO convo_search VALUES (?, ?, ?)",
            [(rel, i, "orphan:opencode") for i in range(4)],
        )
        con.execute("UPDATE convo_search SET provenance='opencode:live' WHERE row_no=2")
        con.close()
        drift = hc._provenance_drift(root, idx)
        assert len(drift) == 1, f"expected exactly the one bad row, got {drift}"
        assert "1 row(s) opencode:live -> orphan:opencode" in drift[0]
        assert "ses_x.parquet" in drift[0]

    def test_unindexed_rows_are_not_drift(self, tmp_path):
        import duckdb

        root = tmp_path / "corpus"
        _write(root, "opencode", "ses_x", source="opencode:live", rows=3)
        idx = tmp_path / "idx.duckdb"
        con = duckdb.connect(str(idx))
        con.execute(
            "CREATE TABLE convo_search (file_path VARCHAR, row_no INTEGER, provenance VARCHAR)"
        )
        con.close()
        assert hc._provenance_drift(root, idx) == [], (
            "a file the index has not read yet is stale_new's business, not drift"
        )

    def test_check_path_surfaces_it(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hc, "_drift", lambda store, cr: [])
        monkeypatch.setattr(hc, "_coverage", lambda store: [])
        monkeypatch.setattr(hc, "_deleted_sessions", lambda cr: ([], []))
        monkeypatch.setattr(
            hc, "_provenance_drift", lambda cr, **kw: ["f.parquet: 3 row(s) a -> b"]
        )
        out = hc.harvest(check=True, corpus_root=tmp_path, db=tmp_path / "m.db")
        assert out["stale_provenance"] == ["f.parquet: 3 row(s) a -> b"]


class TestCliRejectsBadRanges:
    @pytest.mark.parametrize("flag", ["--keep", "--since"])
    def test_bad_range_exits_2(self, flag, tmp_path, capsys):
        if flag == "--since":
            pytest.skip("--since belongs to bili_sessions, covered there")
        rc = hc.main([flag, "6m", "--corpus-root", str(tmp_path)])
        assert rc == 2
        assert "ambiguous" in capsys.readouterr().err

    def test_bad_range_is_rejected_before_anything_is_touched(self, tmp_path: Path):
        root = tmp_path / "corpus"
        f = _write(root, "opencode", "ses_old", source="opencode:live", newest_days_ago=900)
        assert hc.main(["--keep", "6m", "--corpus-root", str(root)]) == 2
        assert f.exists(), "a rejected range must not purge"
