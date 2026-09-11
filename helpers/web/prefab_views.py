#!/usr/bin/env python3
"""Prefab page views (/v2/*) — the /v2/stats pilot from
doc/improvements/archive/ui/prefab_ui_flask_views.md (S1).

Render parity with the TypeScript Stats tab (frontend/src/views/stats.ts)
over the same APIs (/api/stats + /api/graph/stats), plus the edges-by-year
BarChart named in the proposal's slice table.

Data-binding split, driven by measured prefab-ui 0.20.2 renderer limits:

- Reactive (browser Fetch on mount): every scalar the APIs expose —
  headline metrics, graph-block metrics, the staleness ternary, the
  structure-metric values — via ``Rx`` dot-paths.
- Baked (shaped server-side at page render): dict-valued breakdowns.
  The renderer's ForEach rejects non-arrays (``!Array.isArray`` → null)
  and the ``length`` pipe returns 0 for objects, so the
  ``Record<string, number>`` fields (entity_counts, top_sectors,
  market_cap_counts, edges.by_type) must become row lists / count
  scalars here. State is recomputed on every GET, matching the TS
  view's fetch-on-mount freshness.

The page route reaches the API payloads through
``current_app.view_functions`` (no app.py import — this module is
imported BY app.py via :func:`register`).
"""

from __future__ import annotations

from typing import Any, cast

from flask import Flask, Response, current_app, request

from prefab_ui.actions import Fetch, SetState
from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Badge,
    Button,
    Card,
    CardTitle,
    Column,
    Combobox,
    ComboboxOption,
    DataTable,
    DataTableColumn,
    ForEach,
    Form,
    Heading,
    If,
    Input,
    Link,
    Markdown,
    Metric,
    Row,
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
    Tab,
    Tabs,
    Text,
)
from prefab_ui.components.charts import BarChart, ChartSeries
from prefab_ui.rx import Rx

# --- CSP integration (S0 measured finding): app.py's security headers set
# script-src 'self' app-wide, which blocks Prefab's self-contained inline
# renderer. Minimal relaxation for /v2/* only. Flask runs after_request
# hooks in REVERSE registration order, so register() inserts this hook at
# the list head — it executes last, after the strict default has run.
V2_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'self'"
)


def _v2_csp_override(response: Response) -> Response:
    if request.path.startswith("/v2/"):
        response.headers["Content-Security-Policy"] = V2_CSP
    return response


# Structure-metric rows in the TS view's order (nullable → "—").
_STRUCTURE_FIELDS: list[tuple[str, str]] = [
    ("Density", "density"),
    ("Diameter", "diameter"),
    ("Radius", "radius"),
    ("Avg Path Length", "avg_path_length"),
    ("Transitivity", "transitivity"),
    ("Triangles", "triangles"),
    ("Avg Clustering", "avg_clustering"),
    ("Assortativity", "assortativity"),
]


def _shape_counts(counts: dict[str, int]) -> list[dict[str, Any]]:
    """Sort a Record<string, number> desc and add percentage-of-total."""
    total = sum(counts.values()) or 1
    return [
        {"name": name, "n": n, "pct": f"{100 * n / total:.1f}%"}
        for name, n in sorted(counts.items(), key=lambda kv: -kv[1])
    ]


def _api_payload(endpoint: str) -> dict[str, Any]:
    """Call an app.py API view function in-process and return its JSON."""
    response = cast(
        Response,
        current_app.view_functions[endpoint](),
    )
    payload: dict[str, Any] = response.get_json()
    return payload


def _page_state() -> dict[str, Any]:
    """Bake the shaped (non-reactive) half of the page state.

    Everything a dot-path or array can express client-side is left to the
    on-mount Fetches; only the dict-shaped fields are materialized here.
    """
    stats = _api_payload("api_stats")
    gstats = _api_payload("api_graph_stats")
    timeline_rows = _api_payload("api_graph_edges_by_year")["timeline"]

    by_year: dict[str, int] = {}
    for row in timeline_rows:
        by_year[row["year"]] = by_year.get(row["year"], 0) + row["count"]

    return {
        # Reactive Fetch targets, pre-initialized so first-paint Rx paths
        # resolve to empty instead of throwing on undefined.
        "stats": {},
        "gstats": {},
        # Count scalars the renderer cannot derive from dicts.
        "n_entity_types": len(stats["entity_counts"]),
        "n_sectors": len(stats["top_sectors"]),
        "n_cap_buckets": len(stats["market_cap_counts"]),
        "n_edge_types": len(gstats["edges"]["by_type"]),
        # Breakdown tables (Record<string, number> → sorted rows + pct).
        "entity_rows": _shape_counts(stats["entity_counts"]),
        "sector_rows": _shape_counts(stats["top_sectors"]),
        "cap_rows": _shape_counts(stats["market_cap_counts"]),
        "edge_rows": _shape_counts(gstats["edges"]["by_type"]),
        # Edges-by-year aggregated across edge types for the timeline chart.
        "timeline": [{"year": y, "edges": n} for y, n in sorted(by_year.items())],
    }


def _breakdown_card(title: str, rows: list[dict[str, Any]]) -> None:
    """Append a breakdown card (baked rows) to the enclosing container."""
    with Card():
        CardTitle(title)
        DataTable(
            columns=[
                DataTableColumn(key="name", header="Name"),
                DataTableColumn(key="n", header="Count", align="right"),
                DataTableColumn(key="pct", header="Share", align="right"),
            ],
            rows=rows,
            paginated=True,
            page_size=10,
        )


def stats_page() -> PrefabApp:
    state = _page_state()
    col = Column(gap=4)
    with col:
        Heading("Findata Statistics")
        Text("Prefab pilot (/v2/stats) — same APIs as the /findata Stats tab")

        Heading("Database")
        with Row(gap=2):
            Metric(label="Total Entities", value=Rx("stats.total_entities"))
            Metric(label="Entity Types", value="{{ n_entity_types }}")
            Metric(label="Sectors", value="{{ n_sectors }}")
            Metric(label="Market Cap Categories", value="{{ n_cap_buckets }}")
        with Row(gap=2):
            _breakdown_card("Entity Types", state["entity_rows"])
            _breakdown_card("Top Sectors", state["sector_rows"])
            _breakdown_card("Market Cap Distribution", state["cap_rows"])

        Heading("Graph")
        with Row(gap=2):
            Metric(label="Total Edges", value=Rx("gstats.edges.total"))
            Metric(label="Edge Types", value="{{ n_edge_types }}")
            Metric(label="Graph Entities", value=Rx("gstats.entities.total"))
            Metric(label="Company Sectors", value=Rx("gstats.sectors.count"))
            Metric(label="Top Sector", value=Rx("gstats.sectors.top.0.sector"))
            Metric(
                label="Data Staleness",
                value=Rx("gstats.staleness.stale").then("Stale", "Fresh"),
            )
        with Row(gap=2):
            _breakdown_card("Edge Types", state["edge_rows"])
            with Card():
                CardTitle("Structure Metrics")
                for label, key in _STRUCTURE_FIELDS:
                    with Row(gap=2):
                        Text(label)
                        # Nullable → "—" only for null/undefined, matching the
                        # TS view. The default PIPE is unusable here: its bare
                        # (unquoted) string args break the renderer's tokenizer,
                        # leaving the raw {{ }} template on screen; expression
                        # tokens ARE quoted, so the ternary form parses.
                        value = Rx(f"gstats.structure.{key}")
                        Text(f"{value.__ne__(None).then(value, '—')}")

        Heading("Edge Additions by Year")
        BarChart(
            data=state["timeline"],
            # Explicit color: the default fill is var(--color-edges), which no
            # host stylesheet defines in bundled mode → invisible bars.
            series=[ChartSeries(data_key="edges", label="edges", color="#2563eb")],
            x_axis="year",
            height=220,
        )

    return PrefabApp(
        title="Findata Stats",
        view=col,
        mode="light",
        state=state,
        on_mount=[
            Fetch(url="/api/stats", on_success=SetState("stats", Rx("$result"))),
            Fetch(
                url="/api/graph/stats",
                on_success=SetState("gstats", Rx("$result")),
            ),
        ],
    )


def stats_page_view() -> Response:
    return Response(
        stats_page().html(renderer_mode="bundled"),
        mimetype="text/html",
    )


def register(app: Flask) -> None:
    """Wire /v2/* page routes + the CSP relaxation into the Flask app."""
    app.after_request_funcs.setdefault(None, []).insert(0, _v2_csp_override)
    app.add_url_rule("/v2/stats", view_func=stats_page_view, endpoint="v2_stats")
    app.add_url_rule("/v2/companies", view_func=companies_page_view, endpoint="v2_companies")
    app.add_url_rule("/v2/sectors", view_func=sectors_page_view, endpoint="v2_sectors")
    app.add_url_rule("/v2/docs", view_func=docs_page_view, endpoint="v2_docs")
    app.add_url_rule("/v2/search", view_func=note_search_page_view, endpoint="v2_search")
    app.add_url_rule("/v2/entity", view_func=entity_page_view, endpoint="v2_entity")


# --------------------------------------------------------------------------- #
# S2 — /v2/companies + /v2/sectors (table views)                               #
# --------------------------------------------------------------------------- #
# Interactive-filtering pattern (verified against the 0.20.2 renderer source):
# on_change actions run sequentially and action props (Fetch.url included) are
# re-interpolated against CURRENT state + `$event` (the changed value) at fire
# time. A named control's auto state-write is suppressed when on_change is
# provided (onValueChange is occupied by the action runner), so each filter
# SetStates its own value first — subsequent Fetches then see it.

# Every filter Fetch re-issues the full query: empty params mean "no filter"
# to /api/entities. limit=300 ≈ the TS view's grid at rest (1165 companies
# would need server-side pagination, which the pilot defers — see log).
_COMPANIES_QUERY = (
    "/api/entities?search={{ q }}&type={{ f_type }}&sector={{ f_sector }}"
    "&marketcap={{ f_cap }}&limit=300"
)
_COMPANIES_ON_SUCCESS = [
    SetState("companies", Rx("$result.entities")),
    SetState("total", Rx("$result.total_count")),
]
_CAP_OPTIONS = ["large_cap", "mid_cap", "small_cap", "micro_cap"]


def _companies_state() -> dict[str, Any]:
    stats = _api_payload("api_stats")
    sectors = _api_payload("api_sectors")
    return {
        "companies": [],
        "total": 0,
        "q": "",
        "f_type": "company",
        "f_sector": "",
        "f_cap": "",
        # Filter options are baked (Combobox children are static components);
        # they change on entity-stub/derivation runs, i.e. page reloads.
        "type_options": sorted(stats["entity_counts"]),
        "sector_options": sectors["classifications"],
    }


def _companies_filter(on_change_value: str) -> list[Any]:
    return [
        SetState(on_change_value, "{{ $event }}"),
        Fetch(url=_COMPANIES_QUERY, on_success=list(_COMPANIES_ON_SUCCESS)),
    ]


def companies_page() -> PrefabApp:
    state = _companies_state()
    col = Column(gap=4)
    with col:
        Heading("Companies")
        Text("Prefab pilot (/v2/companies) — server-side filters over /api/entities")
        with Row(gap=2):
            Metric(label="Matches", value=Rx("total"))
            Input(
                name="q",
                placeholder="Search name or sector tag…",
                on_change=_companies_filter("q"),
            )
        with Row(gap=2):
            with Combobox(name="f_type", value="company", on_change=_companies_filter("f_type")):
                for entity_type in state["type_options"]:
                    ComboboxOption(entity_type, value=entity_type)
            with Combobox(name="f_sector", on_change=_companies_filter("f_sector")):
                ComboboxOption("All sectors", value="")
                for sector in state["sector_options"]:
                    ComboboxOption(sector, value=sector)
            with Combobox(name="f_cap", on_change=_companies_filter("f_cap")):
                ComboboxOption("All market caps", value="")
                for cap in _CAP_OPTIONS:
                    ComboboxOption(cap, value=cap)
        with Table():
            with TableHeader():
                with TableRow():
                    TableHead("Name")
                    TableHead("Type")
                    TableHead("Sector")
                    TableHead("Market Cap")
                    TableHead("Note")
            with TableBody():
                with ForEach("companies") as e:
                    with TableRow():
                        TableCell(f"{e.name}")
                        TableCell(f"{e.entity_type}")
                        TableCell(f"{e.sector_classification}")
                        # Full-template form so null renders empty (a mixed
                        # string interpolation would print "null"); If keeps
                        # the badge pill off uncapped rows entirely.
                        with TableCell():
                            with If(f"{e.market_cap} != null"):
                                Badge(f"{e.market_cap}", variant="outline")
                        with TableCell():
                            Link(
                                "view",
                                href="/entity/{{ $item.file_path }}",
                                target="_blank",
                            )
    return PrefabApp(
        title="Findata Companies",
        view=col,
        mode="light",
        state=state,
        on_mount=[Fetch(url=_COMPANIES_QUERY, on_success=list(_COMPANIES_ON_SUCCESS))],
    )


def companies_page_view() -> Response:
    return Response(
        companies_page().html(renderer_mode="bundled"),
        mimetype="text/html",
    )


def sectors_page() -> PrefabApp:
    col = Column(gap=4)
    with col:
        Heading("Sectors")
        Text("Prefab pilot (/v2/sectors) — over /api/sectors")
        Heading("Classifications")
        with Row(gap=1):
            with ForEach("sectors.classifications") as c:
                Badge(f"{c}", variant="secondary")
        Heading("Sector Analysis")
        with ForEach("sectors.sector_entities") as s:
            with Card():
                CardTitle(f"{s.name}")
                Text(f"{s.content.truncate(150)}")
                Link("Read analysis", href="/entity/{{ $item.file_path }}")
    return PrefabApp(
        title="Findata Sectors",
        view=col,
        mode="light",
        state={"sectors": {"classifications": [], "sector_entities": []}},
        on_mount=[
            Fetch(url="/api/sectors", on_success=SetState("sectors", Rx("$result"))),
        ],
    )


def sectors_page_view() -> Response:
    return Response(
        sectors_page().html(renderer_mode="bundled"),
        mimetype="text/html",
    )


# --------------------------------------------------------------------------- #
# S3 — /v2/docs, /v2/search, /v2/entity (prose + search views)                 #
# --------------------------------------------------------------------------- #
# Search commit model: Form onSubmit harvests named inputs into state FIRST
# (renderer-side FormData walk), then runs the actions — so a Fetch url with
# {{ q }} sees the committed value. This is the Enter-commit the proposal
# asked for (the docs' per-keystroke example is too chatty for hybrid
# cosine queries). Note-search filters compose like the S2 filters: the
# doc-type Combobox refetches immediately with $event, reading q from state.

_DOC_TYPES = [
    "company",
    "sector",
    "super_sector",
    "chatter",
    "points_and_figures",
    "plotlines",
]

# Click-to-reader chain shared by the catalog and search tabs: set the path,
# fetch its content off the updated state, flip the Tabs state to the reader.
_OPEN_DOC = lambda key: [  # noqa: E731  (factory, not a global side effect)
    SetState("doc_path", f"{{{{ {key}.path }}}}"),
    Fetch(
        url="/api/docs/content?path={{ doc_path }}",
        on_success=SetState("doc", Rx("$result")),
    ),
    SetState("docs_tab", "reader"),
]


def docs_page() -> PrefabApp:
    col = Column(gap=4)
    with col:
        Heading("Docs & Research")
        Text("Prefab pilot (/v2/docs) — doc/ catalog, hybrid search, Markdown reader")
        with Tabs(name="docs_tab", value="catalog"):
            with Tab("Catalog", value="catalog"):
                with Form(
                    on_submit=[
                        Fetch(
                            url="/api/docs?q={{ catalog_q }}",
                            on_success=SetState("docs", Rx("$result.docs")),
                        ),
                    ]
                ):
                    with Row(gap=2):
                        Input(name="catalog_q", placeholder="Filter paths…")
                        Button("Filter")
                with Table():
                    with TableHeader():
                        with TableRow():
                            TableHead("Title")
                            TableHead("Section")
                            TableHead("Size")
                            TableHead("")
                    with TableBody():
                        with ForEach("docs") as d:
                            with TableRow():
                                TableCell(f"{d.title}")
                                TableCell(f"{d.section}")
                                TableCell(f"{d.size_bytes}")
                                with TableCell():
                                    Button("open", on_click=list(_OPEN_DOC("$item")))
            with Tab("Search", value="search"):
                with Form(
                    on_submit=[
                        Fetch(
                            url="/api/docs/search?q={{ doc_q }}&limit=25",
                            on_success=[
                                SetState("doc_hits", Rx("$result.results")),
                                SetState("doc_mode", Rx("$result.mode")),
                            ],
                        ),
                    ]
                ):
                    with Row(gap=2):
                        Input(name="doc_q", placeholder="Search the doc/ corpus…")
                        Button("Search")
                # Hit count via the length PIPE ({{ x | length }}): a
                # method-call form like {{ doc_hits.length() }} is not part
                # of the expression grammar — the tokenizer drops the whole
                # segment silently (S1's default-pipe trap, second sighting).
                Text(f"mode: {{{{ doc_mode }}}} — {Rx('doc_hits').length()} hits")
                with ForEach("doc_hits") as h:
                    with Card():
                        with Row(gap=2):
                            CardTitle(f"{h.title}")
                            with If(f"{h.section_title} != null"):
                                Badge(f"{h.section_title}", variant="outline")
                        Text(f"{h.snippet}")
                        Button("open", on_click=list(_OPEN_DOC("$item")))
            with Tab("Reader", value="reader"):
                with Card():
                    CardTitle("{{ doc.title }}")
                    Text("{{ doc.path }}")
                    Markdown("{{ doc.content }}")
    return PrefabApp(
        title="Findata Docs",
        view=col,
        mode="light",
        state={
            "docs": [],
            "doc_hits": [],
            "doc_mode": "—",
            "doc": {
                "title": "",
                "path": "",
                "content": "Open a doc from the Catalog or Search tab.",
            },
            "doc_path": "",
            "docs_tab": "catalog",
            "catalog_q": "",
            "doc_q": "",
        },
        on_mount=[
            Fetch(url="/api/docs", on_success=SetState("docs", Rx("$result.docs"))),
        ],
    )


def docs_page_view() -> Response:
    return Response(docs_page().html(renderer_mode="bundled"), mimetype="text/html")


def note_search_page() -> PrefabApp:
    col = Column(gap=4)
    with col:
        Heading("Note Search")
        Text("Prefab pilot (/v2/search) — FTS5 over all findata notes (/api/search)")
        with Form(
            on_submit=[
                Fetch(
                    url="/api/search?q={{ note_q }}&type={{ note_type }}&limit=25",
                    on_success=[
                        SetState("hits", Rx("$result.results")),
                        SetState("hits_total", Rx("$result.total_count")),
                    ],
                    on_error=SetState("search_err", Rx("$error")),
                ),
            ]
        ):
            with Row(gap=2):
                Input(name="note_q", placeholder='FTS5 query, e.g. "shrimp feed"')
                with Combobox(name="note_type"):
                    ComboboxOption("All types", value="")
                    for doc_type in _DOC_TYPES:
                        ComboboxOption(doc_type, value=doc_type)
                Button("Search")
        Text("{{ hits_total }} hits — {{ search_err }}")
        with ForEach("hits") as hit:
            with Card():
                with Row(gap=2):
                    CardTitle(f"{hit.title}")
                    Badge(f"{hit.doc_type}", variant="secondary")
                Markdown("{{ $item.snippet }}")
                with If(f"{hit.file_path} != null"):
                    Link(
                        "open note",
                        href="/entity/{{ $item.file_path }}",
                        target="_blank",
                    )
    return PrefabApp(
        title="Findata Note Search",
        view=col,
        mode="light",
        state={
            "hits": [],
            "hits_total": 0,
            "search_err": "",
            "note_q": "",
            "note_type": "",
        },
    )


def note_search_page_view() -> Response:
    return Response(
        note_search_page().html(renderer_mode="bundled"),
        mimetype="text/html",
    )


def entity_page() -> PrefabApp:
    col = Column(gap=4)
    with col:
        Heading("Entity Detail")
        Text("Prefab pilot (/v2/entity) — /api/entity + /api/events timeline")
        with Form(
            on_submit=[
                Fetch(
                    url="/api/entity/{{ entity_q }}",
                    on_success=SetState("entity", Rx("$result")),
                    on_error=SetState("entity_err", Rx("$error")),
                ),
                Fetch(
                    url="/api/events/{{ entity_q }}",
                    on_success=[
                        SetState("events", Rx("$result.events")),
                        SetState("event_count", Rx("$result.event_count")),
                    ],
                    on_error=[
                        SetState("events", []),
                        SetState("event_count", 0),
                    ],
                ),
            ]
        ):
            with Row(gap=2):
                Input(name="entity_q", placeholder="Entity name, e.g. HDFC Bank")
                Button("Load")
        with If("entity.name != null"):
            with Card():
                with Row(gap=2):
                    CardTitle("{{ entity.name }}")
                    Badge("{{ entity.entity_type }}", variant="secondary")
                    with If("entity.sector_classification != null"):
                        Badge("{{ entity.sector_classification }}", variant="outline")
                    with If("entity.market_cap != null"):
                        Badge("{{ entity.market_cap }}", variant="outline")
                Markdown("{{ entity.content }}")
        Heading("Events — {{ event_count }}")
        with Table():
            with TableHeader():
                with TableRow():
                    TableHead("Date")
                    TableHead("Type")
                    TableHead("Counterparty")
                    TableHead("Magnitude")
                    TableHead("Source quote")
            with TableBody():
                with ForEach("events") as ev:
                    with TableRow():
                        TableCell(f"{ev.event_date}")
                        TableCell(f"{ev.event_type}")
                        TableCell(f"{ev.counterparty}")
                        TableCell(f"{ev.magnitude}")
                        TableCell(f"{ev.source_quote}")
    return PrefabApp(
        title="Findata Entity",
        view=col,
        mode="light",
        state={
            "entity": {},
            "entity_err": "",
            "events": [],
            "event_count": 0,
            "entity_q": "",
        },
    )


def entity_page_view() -> Response:
    return Response(
        entity_page().html(renderer_mode="bundled"),
        mimetype="text/html",
    )
