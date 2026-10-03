"""S8: db_maint's agent_id guard + advisory provenance report.

Hermetic: tmp SQLite DB, no shared memory/research.db involved.
"""

import sqlite3

import pytest

from helpers.maintenance.db_maint import (
    install_agent_id_guard,
    provenance_agent_report,
)


def _mk_db(tmp_path):
    path = tmp_path / "r.db"
    con = sqlite3.connect(
        str(path)
    )  # raw client: FK OFF, like the raw-sqlite3.connect hole S8 covers
    con.executescript(
        """
        CREATE TABLE provenance_agents(agent_id TEXT PRIMARY KEY);
        INSERT INTO provenance_agents VALUES ('manual');
        CREATE TABLE graph_edges(id INTEGER PRIMARY KEY, agent_id TEXT);
        INSERT INTO graph_edges(agent_id) VALUES ('manual');   -- legal: pass through
        INSERT INTO graph_edges(agent_id) VALUES (NULL);       -- unknown is legal too
        INSERT INTO graph_edges(agent_id) VALUES ('');         -- the historical hole
        INSERT INTO graph_edges(agent_id) VALUES ('ghost');    -- unregistered id
        """
    )
    return con


class TestAgentIdGuardTriggers:
    def test_registered_and_null_pass_empty_and_unregistered_abort(self, tmp_path):
        con = _mk_db(tmp_path)
        installed = install_agent_id_guard(con)
        assert set(installed) == {"graph_edges:INSERT", "graph_edges:UPDATE"}

        con.execute("INSERT INTO graph_edges(agent_id) VALUES ('manual')")  # 3 ok
        con.execute("INSERT INTO graph_edges(agent_id) VALUES (NULL)")
        with pytest.raises(sqlite3.IntegrityError, match="registered non-empty"):
            con.execute("INSERT INTO graph_edges(agent_id) VALUES ('')")
        with pytest.raises(sqlite3.IntegrityError, match="registered non-empty"):
            con.execute("INSERT INTO graph_edges(agent_id) VALUES ('ghost')")
        con.execute("UPDATE graph_edges SET agent_id=NULL WHERE id=1")  # ok
        with pytest.raises(sqlite3.IntegrityError, match="registered non-empty"):
            con.execute("UPDATE graph_edges SET agent_id='' WHERE id=2")
        # 4 pre-guard rows (incl. the '' and 'ghost' holes — the guard is
        # forward-only; existing drift is named by the probe, not rewritten)
        # + 2 legal post-guard rows.
        assert con.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0] == 6
        con.close()

    def test_absent_provenance_registry_returns_empty(self, tmp_path):
        con = sqlite3.connect(str(tmp_path / "r.db"))
        con.execute("CREATE TABLE graph_edges(id INTEGER PRIMARY KEY, agent_id TEXT)")
        assert install_agent_id_guard(con) == []
        con.close()

    def test_absent_fact_tables_skipped(self, tmp_path):
        con = sqlite3.connect(str(tmp_path / "r.db"))
        con.execute("CREATE TABLE provenance_agents(agent_id TEXT PRIMARY KEY)")
        # no graph_edges/events/quotes/company_metrics/hyper_edges -> all skipped
        assert install_agent_id_guard(con) == []
        con.close()


class TestProvenanceAgentReport:
    def test_names_dangling_state(self, tmp_path):
        con = _mk_db(tmp_path)
        report = provenance_agent_report(con)
        assert report["registered"] == 1
        assert report["empty_agent_id"] == {"graph_edges": 1}
        assert report["unregistered_agent_id"] == {"graph_edges": ["ghost"]}
        con.close()

    def test_clean_state_reports_zeroes(self, tmp_path):
        con = _mk_db(tmp_path)
        con.execute("DELETE FROM graph_edges WHERE agent_id='' OR agent_id='ghost'")
        report = provenance_agent_report(con)
        assert report["empty_agent_id"] == {} and report["unregistered_agent_id"] == {}
        con.close()
