#!/usr/bin/env python3
"""Prefab /v2/stats pilot page — route shell, CSP split, and wiring tests.

The page's data-binding design (reactive Fetch vs server-shaped baked state)
is documented in helpers/web/prefab_views.py; these tests pin the server-side
contract. Full render parity was verified in-browser (proposal S1 log).
"""

from __future__ import annotations

import re

import pytest

from tests.test_integration_ts_contract import contract_client as ts_contract_client

pytestmark = [pytest.mark.integration]

# Fixture re-export: ts-contract's seeded client is exactly the page's
# dependency surface (entities/tags/graph_edges/graph_analytics, DuckDB
# layer stubbed) — register it here under its canonical name.
contract_client = ts_contract_client


def _get(client):
    return client.get("/v2/stats")


class TestV2StatsPage:
    def test_returns_200_html(self, contract_client):
        r = contract_client.get("/v2/stats")
        assert r.status_code == 200
        assert r.mimetype == "text/html"

    def test_self_contained_bundled_renderer(self, contract_client):
        """Bundled mode: the renderer ships inline — no external asset tags.

        Head-scoped because the bundle's embedded docstrings contain
        `<script src=...>` example code that would false-positive a
        whole-body probe.
        """
        r = contract_client.get("/v2/stats")
        body = r.data
        assert len(body) > 1_000_000, "bundled renderer should be inline (~6.6 MB)"
        head = body.split(b"</head>", 1)[0]
        assert b"<script" in head, "renderer script must be present"
        assert not re.search(rb"<script[^>]*\ssrc=", head), "external script tag"
        assert not re.search(rb'<link[^>]*href="http', head), "external stylesheet"
        assert b"/static/" not in body, "no repo static assets on the Prefab page"

    def test_fetch_wiring_targets_stats_apis(self, contract_client):
        """The on-mount Fetch actions hit the same APIs as the TS view."""
        body = contract_client.get("/v2/stats").data
        assert b'"/api/stats"' in body
        assert b'"/api/graph/stats"' in body

    def test_baked_state_shapes_dict_breakdowns(self, contract_client):
        """Dict fields ship pre-shaped (renderer can't iterate objects).

        Seed: 3 companies + 2 sectors; part_of 3/5 edges. Rows are sorted
        desc with a percentage-of-total share column.
        """
        body = contract_client.get("/v2/stats").data
        for key in (b'"entity_rows"', b'"sector_rows"', b'"cap_rows"', b'"edge_rows"'):
            assert key in body, f"missing baked state key {key.decode()}"
        assert b'"name":"part_of","n":3,"pct":"60.0%"' in body
        assert b'"timeline":[]' in body  # no valid_from dates in the seed

    def test_dict_count_scalars_baked(self, contract_client):
        """Counts the renderer can't derive from dicts (length(obj) == 0)."""
        body = contract_client.get("/v2/stats").data
        assert b'"n_entity_types":2' in body
        assert b'"n_edge_types":3' in body


class TestV2CspSplit:
    def test_v2_csp_relaxed_for_inline_renderer(self, contract_client):
        r = contract_client.get("/v2/stats")
        csp = r.headers["Content-Security-Policy"]
        assert "script-src 'self' 'unsafe-inline'" in csp

    def test_findata_csp_stays_strict(self, contract_client):
        """The relaxation is scoped to /v2/* — the main app keeps script-src 'self'."""
        r = contract_client.get("/findata")
        csp = r.headers["Content-Security-Policy"]
        assert "script-src 'self'" in csp
        assert "script-src 'self' 'unsafe-inline'" not in csp


class TestV2CompaniesPage:
    def test_returns_200_html(self, contract_client):
        r = contract_client.get("/v2/companies")
        assert r.status_code == 200
        assert r.mimetype == "text/html"

    def test_filter_fetch_reissues_full_query(self, contract_client):
        """Each filter change refetches with ALL filters interpolated.

        on_change actions run sequentially with current state + $event, so
        the URL template reads every filter from state — composing filters
        server-side like the TS view does.
        """
        body = contract_client.get("/v2/companies").data
        assert (
            b"/api/entities?search={{ q }}&amp;type={{ f_type }}"
            b"&amp;sector={{ f_sector }}&amp;marketcap={{ f_cap }}&amp;limit=300"
        ) in body or (
            b"/api/entities?search={{ q }}&type={{ f_type }}"
            b"&sector={{ f_sector }}&marketcap={{ f_cap }}&limit=300"
        ) in body
        assert b"$event" in body

    def test_filter_options_baked(self, contract_client):
        """Combobox children are static — options ship with the page."""
        body = contract_client.get("/v2/companies").data
        assert b'"type_options"' in body
        assert b'"sector_options"' in body
        assert b"Banking" in body  # from the seeded sector_classification

    def test_rows_pre_initialized_empty(self, contract_client):
        body = contract_client.get("/v2/companies").data
        assert b'"companies":[]' in body
        assert b'"total":0' in body


class TestV2SectorsPage:
    def test_returns_200_html(self, contract_client):
        r = contract_client.get("/v2/sectors")
        assert r.status_code == 200
        assert r.mimetype == "text/html"

    def test_fetch_wiring(self, contract_client):
        body = contract_client.get("/v2/sectors").data
        assert b'"/api/sectors"' in body
        assert b'"classifications"' in body
        assert b'"sector_entities"' in body


class TestV2DocsPage:
    def test_returns_200_html(self, contract_client):
        r = contract_client.get("/v2/docs")
        assert r.status_code == 200
        assert r.mimetype == "text/html"

    def test_three_surface_wiring(self, contract_client):
        """Catalog (/api/docs), hybrid search (/api/docs/search), and the
        click-to-reader content fetch are all wired."""
        body = contract_client.get("/v2/docs").data
        assert b'"/api/docs"' in body
        assert b'"/api/docs/search' in body
        assert b"/api/docs/content?path={{ doc_path }}" in body
        assert b'"docs_tab"' in body  # tab switch is state-driven

    def test_hit_count_uses_length_pipe(self, contract_client):
        """The method-call form ({{ x.length() }}) isn't in the expression
        grammar — the tokenizer drops the segment silently."""
        body = contract_client.get("/v2/docs").data
        assert b"doc_hits | length" in body
        assert b"doc_hits.length()" not in body


class TestV2NoteSearchPage:
    def test_returns_200_html(self, contract_client):
        r = contract_client.get("/v2/search")
        assert r.status_code == 200
        assert r.mimetype == "text/html"

    def test_form_commit_wiring(self, contract_client):
        body = contract_client.get("/v2/search").data
        assert b"/api/search?q={{ note_q }}&type={{ note_type }}&limit=25" in body
        assert b'"note_q"' in body

    def test_doc_type_options(self, contract_client):
        body = contract_client.get("/v2/search").data
        assert b"points_and_figures" in body


class TestV2EntityPage:
    def test_returns_200_html(self, contract_client):
        r = contract_client.get("/v2/entity")
        assert r.status_code == 200
        assert r.mimetype == "text/html"

    def test_entity_and_events_chained_fetches(self, contract_client):
        """One submit drives both /api/entity and /api/events off {{ entity_q }}."""
        body = contract_client.get("/v2/entity").data
        assert b"/api/entity/{{ entity_q }}" in body
        assert b"/api/events/{{ entity_q }}" in body
        assert b'"event_count"' in body
