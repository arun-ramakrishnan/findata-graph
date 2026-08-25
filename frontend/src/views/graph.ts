// Graph view — "The Lens" (S3 redesign; S4 adds Rank + Time).
//
// Modes rail (Ego / All / Path / Rank / Time) + the As-Of Chronoscope
// (temporal scrubber; ego + path queries re-run with as_of) + interactive
// edge-type legend + hover tooltips + zoom-fade labels + louvain community
// shading (All mode) + progressive expansion (tap a node's detail panel to
// merge its neighbours into the canvas; sectors render every member — the
// old 60-cap synthetic node is gone).
//
// S4: Rank mode renders metric league tables (every centrality the
// /api/graph/metrics allowlist serves, plus link-prediction partners and
// VoteRank seeds) with louvain groups + read-only link suggestions beside
// them; Time mode renders deal-activity-by-year bars, cross-sector bridges,
// the co-mention leaderboard and the on-demand near-duplicate tripwire.
// The Inspector (right rail) gains an events timeline via /api/events.
//
// fcose (registered at module load since S2) is the default layout for both
// ego networks and the whole-graph cloud; it tiles disconnected components
// natively, so the hand-rolled "components" preset stays available but is
// no longer the cloud default. Edge/node colours read the --edge-* /
// interaction tokens from tokens.css at module load (single source of
// truth: legend chips, filters and canvas cannot drift).
//
// The window.viewer inline-onclick contract does NOT extend into this view:
// every control below is wired with addEventListener.

import cytoscape from "cytoscape";
import fcose from "cytoscape-fcose";
import type {
    BridgesResponse,
    CompanyNeighbors,
    CoMentionsResponse,
    EdgesByYearResponse,
    EntitiesResponse,
    EventItem,
    EventsResponse,
    GraphCloudResponse,
    GraphRefreshResponse,
    LinkPredictionResponse,
    MetricGroupsResponse,
    MetricRankedResponse,
    MetricSeedsResponse,
    NearDuplicatesResponse,
    NeighborsBundle,
    RelationshipTypeSummary,
    SectorNeighbors,
    ShortestPathResponse,
    SuggestionsResponse,
    SuggestionRow,
    YearEdgeCount,
} from "../../types/api";
import { getEl, escapeHtml } from "../core/dom";
import { fetchJson, postJson } from "../core/api";

// Register fcose once per bundle; idempotent inside cytoscape.
cytoscape.use(fcose);

/** Union of the graph relationship filters from the #graph-filter dropdown. */
type GraphFilter = "all" | "peers" | "jv" | "acquired" | "subsidiary" | "supply";

/** Lens modes (S3: ego / all / path; S4: rank / time). */
type LensMode = "ego" | "all" | "path" | "rank" | "time";

/**
 * cytoscape element (node or edge) as built by the ego-network builders.
 * `group` is optional because edges don't carry one (only nodes are grouped
 * for styling).
 */
interface GraphElement {
    data: {
        id: string;
        label: string;
        group?: string;
        centrality?: number;
        deg?: number;
        source?: string;
        target?: string;
        type?: string;
        /** Set to "1" on every cloud-mode element (drives cheap cloud styling). */
        cloud?: string;
        /** Connected-component root id (cloud mode). */
        component?: string;
        /** "1" on high-degree cloud nodes — zoom-fade labels keep these visible. */
        hub?: string;
        props?: Record<string, unknown>;
    };
}

/** Whole-graph cloud data, kept client-side for filter re-application. */
interface CloudCache {
    data: GraphCloudResponse;
    degree: Record<string, number>;
    communities: Map<string, number> | null;
}

/** Lazy graph-tab state, initialized on first visit to the Graph view. */
interface GraphState {
    cy: CyInstance | null;
    central: string | null;
    elements: GraphElement[] | null;
    entitiesLoaded: boolean;
    entityType?: "sector" | "company";
    mode: LensMode;
    cloud: CloudCache | null;
    cloudMode: boolean;
    /** Edge types the user toggled off via the legend chips (cloud mode). */
    hiddenEdgeTypes: Set<string>;
    /** Current zoom-fade label bucket (-1 = not yet applied). */
    labelBucket: number;
    /** True once the user picks a layout explicitly (cloud defaults to the
     *  fast component preset until then — fcose on ~700 nodes is slow). */
    layoutTouched: boolean;
    /** S4 Rank caches: `${metric}:${top}` → payload; louvain groups; seeds. */
    rankData: Map<string, MetricRankedResponse | LinkPredictionResponse>;
    rankGroups: MetricGroupsResponse | null;
    rankSeeds: string[] | null;
    /** S4 link suggestions per method (read-only projection). */
    suggestions: Map<string, SuggestionRow[]>;
    /** S4 Time caches (near-duplicates only after its explicit run). */
    timeByYear: EdgesByYearResponse | null;
    timeBridges: BridgesResponse | null;
    timeCoMentions: CoMentionsResponse | null;
    nearDup: NearDuplicatesResponse | null;
    /** Token guarding async inspector-event renders against stale panels. */
    detailSeq: number;
}

// --------------------------------------------------------------------------- //
// Palette: read the design tokens so canvas + legend + chips share colours.  //
// --------------------------------------------------------------------------- //

/** edge_type → tokens.css custom property (§3.1 of the proposal). */
const _EDGE_TOKENS: Record<string, string> = {
    co_mentioned_in: "--edge-co-mention",
    part_of: "--edge-part-of",
    has_company: "--edge-has-company",
    exposed_to: "--edge-exposed-to",
    belongs_to: "--edge-belongs-to",
    subsidiary_of: "--edge-subsidiary",
    jv_with: "--edge-jv",
    acquired: "--edge-acquired",
    competes_with: "--edge-competes",
    supplier_to: "--edge-supply",
    supplies_to: "--edge-supply",
    customer_of: "--edge-supply",
    same_group: "--edge-same-group",
    cited_in: "--edge-cited-in",
    semantic_peer: "--edge-semantic-peer",
    invested_in: "--edge-invested-in",
};

const EDGE_COLORS: Record<string, string> = {};
(() => {
    const cs = getComputedStyle(document.documentElement);
    for (const [t, token] of Object.entries(_EDGE_TOKENS)) {
        const v = cs.getPropertyValue(token).trim();
        if (v) EDGE_COLORS[t] = v;
    }
})();

const edgeColor = (t: string | undefined): string =>
    (t && EDGE_COLORS[t]) || "#5C6E7E";

/** Louvain community hues (nodes get data(color) when shading is on). */
const _COMMUNITY_PALETTE = [
    "#E0A93E", "#2DD4BF", "#C39BFF", "#F5B14C", "#7CA8C9",
    "#F28B82", "#9BE08A", "#E79BE0", "#8AD7C6", "#D8C9A3",
];

/** Cloud node considered a hub for zoom-fade labels + sizing. */
const _HUB_DEGREE = 6;
/** Guard rails for progressive expansion / sector member renders. */
const _EXPAND_NODE_CAP = 150;
const _SECTOR_RENDER_CAP = 200;
/** Zoom thresholds for the label-fade buckets (cloud mode). */
const _ZOOM_LBL_OFF = 0.35;
const _ZOOM_LBL_HUBS = 0.8;

/** One-line blurbs for the Rank-mode metric dropdown (subtitle under the
 *  table header — says WHAT the number means, not just its name). */
const METRIC_BLURBS: Record<string, string> = {
    degree_centrality: "most-connected entities by raw edge count",
    pagerank: "influence propagated through the whole graph",
    betweenness_centrality: "brokers sitting on the most shortest paths",
    closeness_centrality: "entities closest to everyone else",
    eigenvector_centrality: "connected to the well-connected",
    harmonic_centrality: "closeness that tolerates unreachable pockets",
    katz_centrality: "influence damped by path length",
    laplacian_centrality: "structural importance via Laplacian energy",
    local_reaching_centrality: "reach over each neighbour's own ties",
    local_clustering_coefficient: "how densely each entity's neighbours interconnect",
    link_prediction: "predicted partners per entity (persisted scoring run)",
    voterank: "VoteRank seed set — the nodes worth starting a story from",
};

/** Node groups that can never carry company events (skip the timeline fetch). */
const _NON_EVENT_GROUPS = new Set([
    "sector", "sector-focal", "member", "super_sector", "sub_sector", "edition", "theme",
]);

export class GraphView {
    // --- graph-tab state (lazy-initialized in loadGraphView) -------------- //
    graph: GraphState | null = null;

    constructor() {
        // Nothing to bind statically — every control is wired lazily on first
        // Graph-view visit (same as the original single file did).
    }

    async loadGraphView(): Promise<void> {
        if (!this.graph) {
            this.graph = {
                cy: null,
                central: null,
                elements: null,
                entitiesLoaded: false,
                mode: "ego",
                cloud: null,
                cloudMode: false,
                hiddenEdgeTypes: new Set(),
                labelBucket: -1,
                layoutTouched: false,
                rankData: new Map(),
                rankGroups: null,
                rankSeeds: null,
                suggestions: new Map(),
                timeByYear: null,
                timeBridges: null,
                timeCoMentions: null,
                nearDup: null,
                detailSeq: 0,
            };
        }
        // Build the cytoscape instance if it doesn't exist yet.
        if (!this.graph.cy) {
            const canvas = getEl("graph-canvas");
            this.graph.cy = cytoscape({
                container: canvas,
                elements: [],
                style: this._cytoscapeStyle(),
                layout: { name: "concentric" },
                wheelSensitivity: 0.2,
                minZoom: 0.2,
                maxZoom: 3,
            });
            // Click handler: re-centre on the clicked node (ego-network mode
            // only — in cloud mode tapping a node shows its detail panel and
            // highlights the connected set it belongs to).
            this.graph.cy.on("tap", "node", async (evt) => {
                this._hideTip();
                const name = evt.target.data().id;
                if (!this.graph) return;
                if (this.graph.mode === "all") {
                    this._highlightCloudSet(evt.target.data());
                    this._renderGraphDetail(evt.target.data());
                    return;
                }
                if (name && name !== this.graph.central) {
                    (getEl("graph-search") as HTMLInputElement).value = name;
                    this._setMode("ego");
                    await this.loadEgoNetwork(name);
                }
            });
            // Selection handler: populate side panel (+ highlight cloud set).
            this.graph.cy.on("select", "node", (evt) => {
                if (this.graph && this.graph.mode === "all") {
                    this._highlightCloudSet(evt.target.data());
                }
                this._renderGraphDetail(evt.target.data());
            });
            // Tapping empty canvas clears the set highlight in cloud mode.
            this.graph.cy.on("tap", (evt: CyEvent) => {
                this._hideTip();
                if (!this.graph || this.graph.mode !== "all") return;
                const isNode = (evt.target as CySingular & { isNode?: () => boolean }).isNode?.();
                if (!isNode) this._clearCloudHighlight();
            });

            // --- toolbar ------------------------------------------------- //
            getEl("graph-search-btn").addEventListener("click", async () => {
                const name = (getEl("graph-search") as HTMLInputElement).value.trim();
                if (name) { this._setMode("ego"); await this.loadEgoNetwork(name); }
            });
            (getEl("graph-search") as HTMLInputElement).addEventListener("keydown", async (e) => {
                if (e.key === "Enter") {
                    const name = (e.target as HTMLInputElement).value.trim();
                    if (name) { this._setMode("ego"); await this.loadEgoNetwork(name); }
                }
            });
            (getEl("graph-layout") as HTMLSelectElement).addEventListener("change", (e) => {
                if (!this.graph || !this.graph.cy) return;
                this.graph.layoutTouched = true;
                const inCloud = this.graph.mode === "all";
                this._runGraphLayout((e.target as HTMLSelectElement).value, inCloud);
                if (inCloud) this.graph.cy.fit(undefined, 30);
            });
            getEl("graph-filter").addEventListener("change", () => {
                // Re-render the same central entity with the new filter.
                this._setMode("ego");
                if (this.graph!.central) this.loadEgoNetwork(this.graph!.central);
            });
            getEl("graph-refresh-db").addEventListener("click", async () => {
                const btn = getEl("graph-refresh-db") as HTMLButtonElement;
                btn.disabled = true;
                try {
                    const data = await postJson<GraphRefreshResponse>("/api/graph/refresh");
                    this._setGraphStatus(data.status === "ok"
                        ? "DB refreshed — re-run the view to see new data"
                        : "refresh failed");
                    this.graph!.elements = null;
                    this.graph!.central = null;
                    this.graph!.cloud = null;
                    // S4 caches are as stale as the cloud after a refresh.
                    this.graph!.rankData.clear();
                    this.graph!.rankGroups = null;
                    this.graph!.rankSeeds = null;
                    this.graph!.suggestions.clear();
                    this.graph!.timeByYear = null;
                    this.graph!.timeBridges = null;
                    this.graph!.timeCoMentions = null;
                    this.graph!.nearDup = null;
                } catch (e) {
                    this._setGraphStatus("refresh failed: " + (e as Error).message);
                } finally {
                    btn.disabled = false;
                }
            });
            getEl("shortest-btn").addEventListener("click", () => this.loadShortestPath());
            getEl("shortest-clear").addEventListener("click", () => this.clearShortestPath());

            this._initLensRail();
            this._initTooltip();
            this._initGraphZoom();
            this._setMode("ego");
        }
        // Populate the typeahead (one-time; small query).
        if (!this.graph.entitiesLoaded) {
            await this.loadGraphEntityList();
            this.graph.entitiesLoaded = true;
        }
        // Resize after the section becomes visible (cytoscape needs the
        // container to have non-zero dimensions before its first layout).
        setTimeout(() => this.graph && this.graph.cy && this.graph.cy.resize(), 50);
    }

    // --- Lens rail: modes + chronoscope + cloud filters ------------------- //

    /** Wire the mode buttons, the As-Of chronoscope, and the cloud toggles. */
    private _initLensRail(): void {
        document.querySelectorAll<HTMLButtonElement>(".lens-mode").forEach((btn) => {
            btn.addEventListener("click", () => {
                const mode = btn.dataset.lensMode as LensMode;
                if (this.graph && this.graph.mode !== mode) this._setMode(mode);
            });
        });

        const slider = getEl("chronoscope") as HTMLInputElement;
        const year = getEl("chronoscope-year");
        const reset = getEl("chronoscope-reset");
        const describe = (): string =>
            slider.value === slider.max ? "now" : `as of ${slider.value}`;
        const rerun = (): void => {
            if (!this.graph) return;
            if (this.graph.mode === "ego" && this.graph.central) {
                this.loadEgoNetwork(this.graph.central);
            } else if (this.graph.mode === "path") {
                const a = (getEl("shortest-a") as HTMLInputElement).value.trim();
                const b = (getEl("shortest-b") as HTMLInputElement).value.trim();
                if (a && b) this.loadShortestPath();
            }
        };
        slider.addEventListener("input", () => {
            year.textContent = describe();
            year.classList.toggle("armed", slider.value !== slider.max);
            rerun();
        });
        reset.addEventListener("click", () => {
            slider.value = slider.max;
            year.textContent = "now";
            year.classList.remove("armed");
            rerun();
        });

        getEl("cloud-min-degree").addEventListener("change", () => this._applyCloudFilter());
        getEl("cloud-everything").addEventListener("change", () => this._applyCloudFilter());
        getEl("cloud-community").addEventListener("change", (e) => {
            this._applyCommunityShading((e.target as HTMLInputElement).checked);
        });

        // S4: Rank + Time controls.
        getEl("rank-metric").addEventListener("change", () => void this._loadRankTable());
        getEl("rank-top").addEventListener("change", () => void this._loadRankTable());
        getEl("suggest-method").addEventListener("change", () => void this._loadSuggestions());
        getEl("neardup-run").addEventListener("click", () => void this._loadNearDuplicates());
    }

    /** Current temporal filter ("" = now / no filter). */
    private _asOf(): string {
        const slider = document.getElementById("chronoscope") as HTMLInputElement | null;
        if (!slider || slider.value === slider.max) return "";
        return slider.value;
    }

    /** Switch lens mode; toggles panels and (re)loads data as needed. */
    private _setMode(mode: LensMode): void {
        if (!this.graph) return;
        this.graph.mode = mode;
        this.graph.cloudMode = mode === "all";
        const view = getEl("graph-view");
        view.dataset.lensMode = mode;

        document.querySelectorAll<HTMLButtonElement>(".lens-mode").forEach((btn) => {
            btn.classList.toggle("active", btn.dataset.lensMode === mode);
            btn.setAttribute("aria-pressed", btn.dataset.lensMode === mode ? "true" : "false");
        });

        // Mode-scoped panels.
        getEl("lens-cloud-controls").style.display = mode === "all" ? "block" : "none";
        getEl("graph-cloud-panel").style.display = mode === "all" ? "block" : "none";
        getEl("graph-shortest").style.display = mode === "path" ? "block" : "none";
        // The ego edge-filter select is meaningless in the other modes.
        (getEl("graph-filter").closest(".filters") as HTMLElement | null)
            ?.style.setProperty("display", mode === "ego" ? "" : "none");

        // S4 table modes: Rank/Time replace the canvas row with data panels;
        // the Chronoscope has no as-of semantics for whole-graph aggregates.
        const tableMode = mode === "rank" || mode === "time";
        getEl("lens-rank").style.display = mode === "rank" ? "block" : "none";
        getEl("lens-time").style.display = mode === "time" ? "block" : "none";
        getEl("lens-asof-block").style.display = tableMode ? "none" : "block";
        (document.querySelector("#graph-view .graph-layout-row") as HTMLElement | null)
            ?.style.setProperty("display", tableMode ? "none" : "");

        // Empty-state prompt only makes sense in ego mode.
        const empty = getEl("graph-empty");
        empty.style.display = mode === "ego" && !this.graph.central ? "flex" : "none";

        if (mode === "all") {
            if (this.graph.cloud) {
                this._applyCloudFilter();
                this._renderCloudLegend(this.graph.cloud.data);
                this._renderRelationshipCloud(this.graph.cloud.data.relationship_types);
            } else {
                this.loadGraphCloud();
            }
        }
        if (mode === "path") {
            this._setGraphStatus("Path — enter two entities below");
        }
        if (mode === "rank") void this._loadRankView();
        if (mode === "time") void this._loadTimeView();
        // Returning from a table mode: cytoscape needs a resize kick after
        // its container sat display:none (cheap no-op otherwise).
        if (!tableMode) setTimeout(() => this.graph?.cy?.resize(), 0);
    }

    async loadGraphEntityList(): Promise<void> {
        // Fill the <datalist> for the typeahead. Includes BOTH companies and
        // sectors so the user can centre the graph on either. Sectors are
        // tagged in the option label so they're visually distinguishable.
        try {
            const dl = getEl("graph-entities-list");
            const parts: string[] = [];
            const dc = await fetchJson<EntitiesResponse>("/api/entities?type=company&limit=3000");
            (dc.entities || []).forEach((e) => {
                parts.push(`<option value="${e.name}">${e.name}</option>`);
            });
            const ds = await fetchJson<EntitiesResponse>("/api/entities?type=sector&limit=500");
            (ds.entities || []).forEach((e) => {
                parts.push(`<option value="${e.name}">${e.name} (sector)</option>`);
            });
            dl.innerHTML = parts.join("");
        } catch (e) {
            // Non-fatal: typeahead is a convenience, not essential.
            console.warn("graph typeahead load failed", e);
        }
    }

    // --- All mode: the whole graph, filtered ------------------------------ //

    /** Fetch + cache the whole graph, then render through the active filters. */
    async loadGraphCloud(): Promise<void> {
        if (!this.graph || !this.graph.cy) return;
        this._setGraphStatus("Loading full graph...");
        let data: GraphCloudResponse;
        try {
            data = await fetchJson<GraphCloudResponse>("/api/graph/cloud");
        } catch (e) {
            this._setGraphStatus(`Error: ${(e as Error).message}`);
            return;
        }

        const degree: Record<string, number> = {};
        data.edges.forEach((e) => {
            degree[e.source] = (degree[e.source] || 0) + 1;
            degree[e.target] = (degree[e.target] || 0) + 1;
        });

        this.graph.cloud = { data, degree, communities: null };
        this.graph.central = null;
        this.graph.entityType = undefined;
        this._clearCloudHighlight();

        // Any failure past the fetch lands in the status line instead of a
        // swallowed unhandled rejection (the whole render is one try block).
        this._setGraphStatus("Building the cloud...");
        try {
            this._applyCloudFilter();
            this._renderCloudLegend(data);
            this._renderRelationshipCloud(data.relationship_types);
        } catch (e) {
            this._setGraphStatus(`Cloud render failed: ${(e as Error).message}`);
        }
    }

    /**
     * Rebuild the cloud canvas from the cached response honouring the rail
     * toggles (min-degree ≥ 2 default, "everything" opt-out) and the legend's
     * edge-type chips. Client-side only — no refetch.
     *
     * Edge-filter semantics: while ANY relationship type is toggled off, the
     * canvas shows the subgraph INDUCED by the visible edges (their endpoint
     * nodes — the min-degree rail toggle is suspended for that view), and it
     * re-lays-out + fits so the subgraph is actually legible. With every
     * type visible, the min-degree behaviour is unchanged.
     */
    private _applyCloudFilter(): void {
        const cy = this.graph && this.graph.cy;
        const cache = this.graph && this.graph.cloud;
        if (!cy || !cache) return;
        const everything = (getEl("cloud-everything") as HTMLInputElement).checked;
        const minDegree = everything ? 1
            : (getEl("cloud-min-degree") as HTMLInputElement).checked ? 2 : 1;

        const hidden = this.graph!.hiddenEdgeTypes;
        const edgeFilterActive =
            cache.data.relationship_types.some((rt) => hidden.has(rt.edge_type));

        let kept: Set<string>;
        let visibleEdges: GraphCloudResponse["edges"];
        if (edgeFilterActive) {
            visibleEdges = cache.data.edges.filter((e) => !hidden.has(e.edge_type));
            kept = new Set();
            visibleEdges.forEach((e) => {
                kept.add(e.source);
                kept.add(e.target);
            });
        } else {
            visibleEdges = cache.data.edges;
            kept = new Set(
                cache.data.nodes
                    .filter((n) => (cache.degree[n.id] || 0) >= minDegree)
                    .map((n) => n.id),
            );
        }

        // Connected-component roots (union-find over the VISIBLE edges) power
        // the tap-to-highlight connected set and the component-packing
        // layout. Computed per rebuild so an edge-filtered subgraph gets its
        // own (correct) components.
        const parent = new Map<string, string>();
        const find = (x: string): string => {
            let root = x;
            while (parent.get(root) !== root) root = parent.get(root) || root;
            let cur = x;
            while (cur !== root) {
                const next = parent.get(cur) || root;
                parent.set(cur, root);
                cur = next;
            }
            return root;
        };
        kept.forEach((id) => parent.set(id, id));
        visibleEdges.forEach((e) => {
            if (!kept.has(e.source) || !kept.has(e.target)) return;
            const ra = find(e.source);
            const rb = find(e.target);
            if (ra !== rb) parent.set(ra, rb);
        });

        const elements: GraphElement[] = cache.data.nodes
            .filter((n) => kept.has(n.id))
            .map((n) => ({
                data: {
                    id: n.id,
                    label: n.label,
                    group: n.entity_type,
                    cloud: "1",
                    deg: cache.degree[n.id] || 0,
                    centrality: cache.degree[n.id] || 0,
                    hub: (cache.degree[n.id] || 0) >= _HUB_DEGREE ? "1" : "",
                    component: find(n.id),
                },
            }));
        visibleEdges.forEach((e) => {
            if (!kept.has(e.source) || !kept.has(e.target)) return;
            elements.push({
                data: {
                    id: `${e.source}__${e.target}__${e.edge_type}`,
                    source: e.source,
                    target: e.target,
                    type: e.edge_type,
                    label: "",       // 4k+ labels are the #1 render cost
                    cloud: "1",
                },
            });
        });

        cy.elements().remove();
        cy.add(elements as unknown as CyElementInput[]);
        this.graph!.elements = elements;

        if (elements.length === 0) {
            this._setGraphStatus(
                "Edge filter — no relationships selected. Click a relationship "
                + "chip (or “all”) to show its subgraph.");
            return;
        }

        // Hub classes drive the zoom-fade label buckets.
        cy.batch(() => {
            cy.nodes().forEach((n) => {
                if (n.data().hub === "1") n.addClass("hub");
            });
        });

        // Cloud default layout is the fast component-packing preset; fcose on
        // the ~700-node filtered cloud takes seconds on the main thread. An
        // explicit layout pick (layoutTouched) is always honored.
        const selected = (getEl("graph-layout") as HTMLSelectElement).value;
        const cloudLayout = (selected === "fcose" && !this.graph!.layoutTouched)
            ? "components"
            : selected;
        this._runGraphLayout(cloudLayout, true);
        cy.fit(undefined, 30);
        this._applyLabelBucket(this._labelBucketFor(cy.zoom()));

        const nodeCount = elements.filter((e) => !e.data.source).length;
        const edgeCount = elements.length - nodeCount;
        if (edgeFilterActive) {
            const shown = cache.data.relationship_types.length - hidden.size;
            this._setGraphStatus(
                `Edge filter — ${nodeCount} entities · ${edgeCount} edges `
                + `(${shown} of ${cache.data.relationship_types.length} relationship types)`);
        } else {
            const filterNote = everything ? "everything" : "min degree ≥ 2";
            this._setGraphStatus(
                `Full graph — ${nodeCount} entities · ${edgeCount} edges (${filterNote})`);
        }
    }

    /**
     * Highlight the connected set (component) a tapped node belongs to: every
     * element sharing its component root gets the `in-set` style, everything
     * else fades to background.
     */
    _highlightCloudSet(nodeData: CyNodeData): void {
        const cy = this.graph && this.graph.cy;
        if (!cy || !this.graph || this.graph.mode !== "all") return;
        const comp = nodeData.component as string | undefined;
        cy.elements().removeClass("in-set faded");
        if (!comp) return;
        cy.elements().forEach((el) => {
            if (el.data().component === comp) {
                el.removeClass("faded").addClass("in-set");
            } else {
                el.addClass("faded").removeClass("in-set");
            }
        });
    }

    /** Remove the cloud set-highlight (restores full opacity). */
    _clearCloudHighlight(): void {
        if (this.graph && this.graph.cy) {
            this.graph.cy.elements().removeClass("in-set faded");
        }
    }

    /**
     * Legend with INTERACTIVE edge chips: clicking a chip hides/shows that
     * relationship type client-side (no refetch). "All/none" restore.
     */
    _renderCloudLegend(data: GraphCloudResponse): void {
        const legend = getEl("graph-cloud-legend");
        const nodeTypes = [...new Set(data.nodes.map((n) => n.entity_type))].sort();
        const nodeHtml = nodeTypes.map((t) => `
            <span class="cloud-legend-chip">
                <span class="cloud-swatch cloud-node-${CSS.escape(t)}">${escapeHtml(t)}</span>
            </span>`).join("");
        const chips = data.relationship_types.map((t) => {
            const off = this.graph?.hiddenEdgeTypes.has(t.edge_type) ? " off" : "";
            return `<button type="button" class="edge-chip${off}"
                            data-edge-type="${escapeHtml(t.edge_type)}"
                            title="${escapeHtml(t.semantics)} — ${t.count} edge${t.count !== 1 ? "s" : ""}. Click to hide/show.">
                        <span class="dot" style="background:${edgeColor(t.edge_type)}"></span>
                        ${escapeHtml(t.edge_type)}
                        <span class="chip-count">${t.count}</span>
                    </button>`;
        }).join("");
        legend.innerHTML = `
            <div class="cloud-legend-group"><strong>Entities</strong>
                <div class="cloud-legend-chips">${nodeHtml}</div></div>
            <div class="cloud-legend-group"><strong>Relationships — click to filter</strong>
                <div class="cloud-legend-chips">${chips}
                    <button type="button" class="edge-chip edge-chip-all" data-edge-type="__all">all</button>
                    <button type="button" class="edge-chip edge-chip-all" data-edge-type="__none">none</button>
                </div></div>`;
        legend.querySelectorAll<HTMLButtonElement>(".edge-chip").forEach((chip) => {
            chip.addEventListener("click", () => {
                const t = chip.dataset.edgeType;
                if (!t) return;
                if (t === "__all" || t === "__none") this._setAllEdgeTypes(t === "__all");
                else this._toggleEdgeType(t);
            });
        });
    }

    /** Show/hide one relationship type: rebuild the induced subgraph. */
    private _toggleEdgeType(t: string): void {
        if (!this.graph || !this.graph.cloud || !t) return;
        const nowHidden = !this.graph.hiddenEdgeTypes.has(t);
        if (nowHidden) this.graph.hiddenEdgeTypes.add(t);
        else this.graph.hiddenEdgeTypes.delete(t);
        // Refresh the chip state in BOTH the legend and the relationship cloud.
        document
            .querySelectorAll<HTMLButtonElement>(
                `.edge-chip[data-edge-type="${t}"], .rel-cloud-chip[data-edge-type="${t}"]`)
            .forEach((chip) => chip.classList.toggle("off", nowHidden));
        this._applyCloudFilter();
    }

    /** Show or hide every relationship type at once (all / none chips). */
    private _setAllEdgeTypes(show: boolean): void {
        const cache = this.graph && this.graph.cloud;
        if (!cache) return;
        cache.data.relationship_types.forEach((rt) => {
            if (show) this.graph!.hiddenEdgeTypes.delete(rt.edge_type);
            else this.graph!.hiddenEdgeTypes.add(rt.edge_type);
        });
        document
            .querySelectorAll<HTMLButtonElement>(".edge-chip[data-edge-type], .rel-cloud-chip[data-edge-type]")
            .forEach((chip) => {
                const t = chip.dataset.edgeType;
                if (!t || t.startsWith("__")) return;
                chip.classList.toggle("off", this.graph!.hiddenEdgeTypes.has(t));
            });
        this._applyCloudFilter();
    }

    /** Relationship cloud card: one size-proportional chip per edge type. */
    _renderRelationshipCloud(types: RelationshipTypeSummary[]): void {
        const card = getEl("graph-relationship-cloud");
        if (!types.length) {
            card.innerHTML = '<p class="hint">No relationships in the graph.</p>';
            return;
        }
        const max = Math.max(...types.map((t) => t.count), 1);
        const chips = types.map((t) => {
            const ratio = t.count / max;
            const size = 0.85 + ratio * 1.35;
            const off = this.graph?.hiddenEdgeTypes.has(t.edge_type) ? " off" : "";
            return `<button type="button" class="rel-cloud-chip${off}"
                            data-edge-type="${escapeHtml(t.edge_type)}"
                            title="${escapeHtml(`${t.semantics} — ${t.count} edge${t.count !== 1 ? "s" : ""}`)}"
                            style="font-size:${size.toFixed(2)}rem; color:${edgeColor(t.edge_type)};">
                    ${escapeHtml(t.edge_type)}
                    <span class="rel-cloud-count">${t.count} ${t.symmetric ? "↔" : "→"}</span>
                </button>`;
        }).join("");
        card.innerHTML = `<h4 class="rel-cloud-title"><i class="fas fa-cloud"></i> Relationship Cloud</h4>
                          <div class="rel-cloud-chips">${chips}</div>`;
        card.querySelectorAll<HTMLButtonElement>(".rel-cloud-chip").forEach((chip) => {
            chip.addEventListener("click", () => {
                const et = chip.dataset.edgeType;
                if (et) this._toggleEdgeType(et);
            });
        });
    }

    /** Louvain community shading (All mode): fetch once, colour nodes. */
    private async _applyCommunityShading(on: boolean): Promise<void> {
        const cy = this.graph && this.graph.cy;
        const cache = this.graph && this.graph.cloud;
        if (!cy || !cache) return;
        if (!on) {
            cy.batch(() => {
                cy.nodes().forEach((n) => {
                    n.removeClass("shaded").removeData("color").removeData("community");
                });
            });
            return;
        }
        if (!cache.communities) {
            try {
                const m = await fetchJson<MetricGroupsResponse>(
                    "/api/graph/metrics/louvain_community");
                const map = new Map<string, number>();
                m.groups.forEach((g) => g.members.forEach((name) => map.set(name, g.label)));
                cache.communities = map;
            } catch (e) {
                this._setGraphStatus(
                    `communities unavailable: ${(e as Error).message} (run make recompute-graph)`);
                (getEl("cloud-community") as HTMLInputElement).checked = false;
                return;
            }
        }
        const map = cache.communities;
        cy.batch(() => {
            cy.nodes().forEach((n) => {
                const c = map.get(String(n.data().id));
                if (c !== undefined) {
                    n.data("color", _COMMUNITY_PALETTE[c % _COMMUNITY_PALETTE.length]);
                    n.data("community", c);
                    n.addClass("shaded");
                }
            });
        });
        const n = map.size;
        this._setGraphStatus(`Louvain shading on — ${n} entities in communities`);
    }

    // --- Rank mode (S4): metric league tables + side intelligence -------- //

    /** Load every Rank panel (table, louvain groups, suggestions). */
    private async _loadRankView(): Promise<void> {
        if (!this.graph) return;
        void this._loadRankTable();
        void this._loadRankGroups();
        void this._loadSuggestions();
    }

    /** League table for the selected metric (scalar / link-prediction / seeds). */
    private async _loadRankTable(): Promise<void> {
        if (!this.graph) return;
        const wrap = getEl("rank-table");
        const metric = (getEl("rank-metric") as HTMLSelectElement).value;
        const top = parseInt((getEl("rank-top") as HTMLSelectElement).value, 10) || 25;
        const blurb = METRIC_BLURBS[metric] || metric;
        const loading = `<p class="hint"><i class="fas fa-spinner fa-spin"></i> computing ${escapeHtml(metric)}…</p>`;
        const fail = (e: unknown): string =>
            `<p class="hint">unavailable — ${escapeHtml((e as Error).message)}`
            + ` (is <span class="mono">make recompute-graph</span> fresh?)</p>`;

        if (metric === "voterank") {
            let seeds = this.graph.rankSeeds;
            if (!seeds) {
                wrap.innerHTML = loading;
                try {
                    const data = await fetchJson<MetricSeedsResponse>("/api/graph/metrics/voterank");
                    seeds = data.seeds;
                    this.graph.rankSeeds = seeds;
                } catch (e) {
                    wrap.innerHTML = fail(e);
                    return;
                }
            }
            const rows = seeds.slice(0, top).map((name, i) => `
                <tr>
                    <td class="idx num">${i + 1}</td>
                    <td><button type="button" class="rank-entity" data-centre="${escapeHtml(name)}"
                                title="Centre the Lens on ${escapeHtml(name)}">${escapeHtml(name)}</button></td>
                    <td class="num muted">seed ${i + 1} of ${seeds.length}</td>
                </tr>`).join("");
            wrap.innerHTML = `<p class="panel-note mono">${escapeHtml(blurb)}</p>
                <table class="rank-table">
                    <thead><tr><th class="num">#</th><th>entity</th><th class="num">note</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>
                <p class="panel-note mono">${Math.min(seeds.length, top)} of ${seeds.length} seeds</p>`;
            this._wireCentre(wrap);
            this._setGraphStatus(`Rank — voterank · ${Math.min(seeds.length, top)} of ${seeds.length} seeds`);
            return;
        }

        const key = `${metric}:${top}`;
        if (metric === "link_prediction") {
            let data = this.graph.rankData.get(key) as LinkPredictionResponse | undefined;
            if (!data) {
                wrap.innerHTML = loading;
                try {
                    // The payload branch serves every entity; `top` slices here.
                    data = await fetchJson<LinkPredictionResponse>(
                        `/api/graph/metrics/${metric}?top=${top}`);
                    this.graph.rankData.set(key, data);
                } catch (e) {
                    wrap.innerHTML = fail(e);
                    return;
                }
            }
            const rows = data.entities.slice(0, top).map((ent, i) => {
                const best = ent.candidates[0];
                return `
                <tr>
                    <td class="idx num">${i + 1}</td>
                    <td><button type="button" class="rank-entity" data-centre="${escapeHtml(ent.entity)}"
                                title="Centre the Lens on ${escapeHtml(ent.entity)}">${escapeHtml(ent.entity)}</button></td>
                    <td>${best ? `<button type="button" class="rank-entity muted-entity" data-centre="${escapeHtml(best.name)}"
                                title="Centre the Lens on ${escapeHtml(best.name)}">↔ ${escapeHtml(best.name)}</button>` : '<span class="muted">—</span>'}</td>
                    <td class="num">${this._fmtScore(ent.best_score)}</td>
                </tr>`;
            }).join("");
            wrap.innerHTML = `<p class="panel-note mono">${escapeHtml(blurb)}</p>
                <table class="rank-table">
                    <thead><tr><th class="num">#</th><th>entity</th><th>predicted partner</th><th class="num">best</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>
                <p class="panel-note mono">${Math.min(data.entities.length, top)} of ${data.total} scored entities</p>`;
            this._wireCentre(wrap);
            this._setGraphStatus(`Rank — link prediction · ${Math.min(data.entities.length, top)} of ${data.total}`);
            return;
        }

        // Scalar centralities.
        let data = this.graph.rankData.get(key) as MetricRankedResponse | undefined;
        if (!data) {
            wrap.innerHTML = loading;
            try {
                data = await fetchJson<MetricRankedResponse>(
                    `/api/graph/metrics/${metric}?top=${top}`);
                this.graph.rankData.set(key, data);
            } catch (e) {
                wrap.innerHTML = fail(e);
                return;
            }
        }
        if (!data.ranked.length) {
            wrap.innerHTML = `<p class="hint">No rows for ${escapeHtml(metric)} — run `
                + `<span class="mono">make recompute-graph</span>.</p>`;
            return;
        }
        const max = Math.max(...data.ranked.map((r) => r.value), 0);
        const rows = data.ranked.map((r, i) => `
            <tr>
                <td class="idx num">${i + 1}</td>
                <td><button type="button" class="rank-entity" data-centre="${escapeHtml(r.entity)}"
                            title="Centre the Lens on ${escapeHtml(r.entity)}">${escapeHtml(r.entity)}</button></td>
                <td class="num"><span class="score-bar" style="width:${max > 0 ? Math.max(2, Math.round((r.value / max) * 46)) : 0}px"></span>
                    ${this._fmtScore(r.value)}</td>
            </tr>`).join("");
        wrap.innerHTML = `<p class="panel-note mono">${escapeHtml(blurb)}</p>
            <table class="rank-table">
                <thead><tr><th class="num">#</th><th>entity</th><th class="num">score</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>
            <p class="panel-note mono">${data.ranked.length} of ${data.total} entities · ${escapeHtml(metric)}</p>`;
        this._wireCentre(wrap);
        this._setGraphStatus(`Rank — ${metric} · ${data.ranked.length} of ${data.total}`);
    }

    /** Louvain groups side panel (top groups by size, clickable members). */
    private async _loadRankGroups(): Promise<void> {
        if (!this.graph) return;
        const mount = getEl("rank-groups");
        if (!this.graph.rankGroups) {
            mount.innerHTML = `<p class="hint"><i class="fas fa-spinner fa-spin"></i> loading…</p>`;
            try {
                this.graph.rankGroups = await fetchJson<MetricGroupsResponse>(
                    "/api/graph/metrics/louvain_community");
            } catch (e) {
                mount.innerHTML = `<p class="hint">unavailable — ${escapeHtml((e as Error).message)}</p>`;
                return;
            }
        }
        const groups = [...this.graph.rankGroups.groups]
            .sort((a, b) => b.size - a.size).slice(0, 8);
        if (!groups.length) {
            mount.innerHTML = `<p class="hint">No communities — run <span class="mono">make recompute-graph</span>.</p>`;
            return;
        }
        mount.innerHTML = groups.map((g) => {
            const color = _COMMUNITY_PALETTE[g.label % _COMMUNITY_PALETTE.length];
            const members = g.members.slice(0, 6).map((m) =>
                `<button type="button" class="chip-entity" data-centre="${escapeHtml(m)}"
                         title="Centre the Lens on ${escapeHtml(m)}">${escapeHtml(m)}</button>`).join("");
            const more = g.members.length > 6
                ? `<span class="chip-more">+${g.members.length - 6}</span>` : "";
            return `
            <div class="rank-group">
                <div class="rank-group-head">
                    <span class="swatch" style="background:${color}"></span>
                    <span class="mono">group ${g.label}</span>
                    <span class="cnt mono">${g.size}</span>
                </div>
                <div class="rank-group-members">${members}${more}</div>
            </div>`;
        }).join("") + (this.graph.rankGroups.modularity !== undefined
            ? `<p class="panel-note mono">modularity ${this.graph.rankGroups.modularity.toFixed(3)}</p>`
            : "");
        this._wireCentre(mount);
    }

    /** Read-only link-prediction suggestions (side panel, per method). */
    private async _loadSuggestions(): Promise<void> {
        if (!this.graph) return;
        const mount = getEl("rank-suggestions");
        const method = (getEl("suggest-method") as HTMLSelectElement).value;
        let rows = this.graph.suggestions.get(method);
        if (!rows) {
            mount.innerHTML = `<p class="hint"><i class="fas fa-spinner fa-spin"></i> predicting…</p>`;
            // pref-attach scores aren't normalized — the 0.3 floor would
            // drop everything, so it runs unfiltered.
            const minScore = method === "pref-attach" ? 0 : 0.3;
            try {
                const data = await fetchJson<SuggestionsResponse>(
                    `/api/graph/suggestions?method=${method}&top=15&min_score=${minScore}`);
                rows = data.suggestions;
                this.graph.suggestions.set(method, rows);
            } catch (e) {
                mount.innerHTML = `<p class="hint">unavailable — ${escapeHtml((e as Error).message)}</p>`;
                return;
            }
        }
        if (!rows.length) {
            mount.innerHTML = `<p class="hint">No pairs above the threshold.</p>`;
            return;
        }
        const max = Math.max(...rows.map((r) => r.score), 0.0001);
        mount.innerHTML = rows.map((r, i) => `
            <button type="button" class="suggest-row" data-centre="${escapeHtml(r.source)}"
                    title="${escapeHtml(r.source)} ↔ ${escapeHtml(r.target)}${r.edition ? ` · ${escapeHtml(r.edition)}` : ""} — centre on ${escapeHtml(r.source)}">
                <span class="idx mono">${i + 1}</span>
                <span class="suggest-pair">${escapeHtml(r.source)} <span class="arrow">↔</span> ${escapeHtml(r.target)}</span>
                <span class="suggest-bar"><span style="width:${((r.score / max) * 100).toFixed(1)}%"></span></span>
                <span class="val mono">${this._fmtScore(r.score)}</span>
            </button>`).join("");
        this._wireCentre(mount);
    }

    // --- Time mode (S4): temporal formation -------------------------------- //

    /** Load every Time panel except near-duplicates (explicit, on-demand). */
    private async _loadTimeView(): Promise<void> {
        if (!this.graph) return;
        this._setGraphStatus("Time — loading...");
        await Promise.allSettled([
            this._loadByYear(),
            this._loadBridges(),
            this._loadCoMentions(),
        ]);
    }

    /** Deal-activity-by-year stacked bars (M&A + JV edges per year). */
    private async _loadByYear(): Promise<void> {
        if (!this.graph) return;
        const mount = getEl("time-byyear");
        if (!this.graph.timeByYear) {
            mount.innerHTML = `<p class="hint"><i class="fas fa-spinner fa-spin"></i> loading…</p>`;
            try {
                this.graph.timeByYear = await fetchJson<EdgesByYearResponse>(
                    "/api/graph/edges-by-year");
            } catch (e) {
                mount.innerHTML = `<p class="hint">unavailable — ${escapeHtml((e as Error).message)}</p>`;
                return;
            }
        }
        const rows = this.graph.timeByYear.timeline;
        if (!rows.length) {
            mount.innerHTML = `<p class="hint">No dated M&A / JV edges in the graph.</p>`;
            getEl("time-legend").innerHTML = "";
            return;
        }
        const types = [...new Set(rows.map((r) => r.edge_type))].sort();
        getEl("time-legend").innerHTML = types.map((t) =>
            `<span class="legend-key"><span class="dot" style="background:${edgeColor(t)}"></span>${escapeHtml(t)}</span>`).join("");

        const byYear = new Map<string, { total: number; parts: YearEdgeCount[] }>();
        rows.forEach((r) => {
            const y = byYear.get(r.year) || { total: 0, parts: [] };
            y.total += r.count;
            y.parts.push(r);
            byYear.set(r.year, y);
        });
        const years = [...byYear.keys()].sort();
        const max = Math.max(...years.map((y) => byYear.get(y)!.total), 1);
        mount.innerHTML = years.map((y) => {
            const { total, parts } = byYear.get(y)!;
            const segs = parts.map((p) =>
                `<span class="bar-seg" style="width:${((p.count / total) * 100).toFixed(2)}%;background:${edgeColor(p.edge_type)}"
                       title="${escapeHtml(p.edge_type)}: ${p.count}"></span>`).join("");
            return `
            <div class="year-row">
                <span class="yr mono">${escapeHtml(y)}</span>
                <span class="bar-track" style="width:${((total / max) * 100).toFixed(1)}%">${segs}</span>
                <span class="cnt mono">${total}</span>
            </div>`;
        }).join("");
        const grand = rows.reduce((a, r) => a + r.count, 0);
        this._setGraphStatus(`Time — ${grand} dated deals across ${years.length} years`);
    }

    /** Cross-sector bridges table (M&A + JV between sector pairs). */
    private async _loadBridges(): Promise<void> {
        if (!this.graph) return;
        const mount = getEl("time-bridges");
        if (!this.graph.timeBridges) {
            mount.innerHTML = `<p class="hint"><i class="fas fa-spinner fa-spin"></i> loading…</p>`;
            try {
                this.graph.timeBridges = await fetchJson<BridgesResponse>("/api/graph/bridges");
            } catch (e) {
                mount.innerHTML = `<p class="hint">unavailable — ${escapeHtml((e as Error).message)}</p>`;
                return;
            }
        }
        const rows = [...this.graph.timeBridges.bridges]
            .sort((a, b) => b.count - a.count).slice(0, 12);
        if (!rows.length) {
            mount.innerHTML = `<p class="hint">No cross-sector M&A / JV edges yet.</p>`;
            return;
        }
        mount.innerHTML = rows.map((b) => `
            <div class="bridge-row">
                <span class="edge-dot" style="background:${edgeColor(b.edge_type)}"
                      title="${escapeHtml(b.edge_type)}"></span>
                <span class="bridge-pair">${escapeHtml(b.sector_a)} <span class="arrow">↔</span> ${escapeHtml(b.sector_b)}</span>
                <span class="cnt mono">${b.count}</span>
            </div>`).join("");
    }

    /** Co-mention leaderboard (most-connected entities in prose). */
    private async _loadCoMentions(): Promise<void> {
        if (!this.graph) return;
        const mount = getEl("time-comentions");
        if (!this.graph.timeCoMentions) {
            mount.innerHTML = `<p class="hint"><i class="fas fa-spinner fa-spin"></i> loading…</p>`;
            try {
                this.graph.timeCoMentions = await fetchJson<CoMentionsResponse>(
                    "/api/graph/co-mentions?top=15");
            } catch (e) {
                mount.innerHTML = `<p class="hint">unavailable — ${escapeHtml((e as Error).message)}</p>`;
                return;
            }
        }
        const rows = this.graph.timeCoMentions.ranked;
        if (!rows.length) {
            mount.innerHTML = `<p class="hint">No co-mentions derived yet.</p>`;
            return;
        }
        const max = Math.max(...rows.map((r) => r.co_mentions), 1);
        mount.innerHTML = rows.map((r) => `
            <div class="cm-row">
                <button type="button" class="rank-entity" data-centre="${escapeHtml(r.entity)}"
                        title="Centre the Lens on ${escapeHtml(r.entity)}">${escapeHtml(r.entity)}</button>
                <span class="cm-bar"><span style="width:${((r.co_mentions / max) * 100).toFixed(1)}%"></span></span>
                <span class="cnt mono">${r.co_mentions}</span>
            </div>`).join("");
        this._wireCentre(mount);
    }

    /** Near-duplicate triage — explicit run only (the ~1s pairwise scan). */
    private async _loadNearDuplicates(): Promise<void> {
        if (!this.graph) return;
        const btn = getEl("neardup-run") as HTMLButtonElement;
        const mount = getEl("time-neardup");
        if (this.graph.nearDup) {
            this._renderNearDuplicates(this.graph.nearDup);
            return;
        }
        btn.disabled = true;
        mount.innerHTML = `<p class="hint"><i class="fas fa-spinner fa-spin"></i> comparing note embeddings (~1s)…</p>`;
        try {
            this.graph.nearDup = await fetchJson<NearDuplicatesResponse>(
                "/api/graph/near-duplicates?min_sim=0.9&limit=50");
        } catch (e) {
            mount.innerHTML = `<p class="hint">unavailable — ${escapeHtml((e as Error).message)}</p>`;
            return;
        } finally {
            btn.disabled = false;
        }
        this._renderNearDuplicates(this.graph.nearDup);
    }

    private _renderNearDuplicates(data: NearDuplicatesResponse): void {
        const mount = getEl("time-neardup");
        if (!data.pairs.length) {
            mount.innerHTML = `<p class="hint">Clean — no pairs at cosine ≥ ${data.min_sim.toFixed(2)}.</p>`;
            return;
        }
        mount.innerHTML = data.pairs.map((p) => `
            <div class="neardup-row">
                <span class="sim mono">${p.similarity.toFixed(3)}</span>
                <a href="/entity/${encodeURI(p.path_a)}" target="_blank" rel="noopener">${escapeHtml(p.title_a || p.path_a)}</a>
                <span class="arrow">↔</span>
                <a href="/entity/${encodeURI(p.path_b)}" target="_blank" rel="noopener">${escapeHtml(p.title_b || p.path_b)}</a>
            </div>`).join("");
    }

    // --- Shared S4 helpers --------------------------------------------------- //

    /** Jump the Lens to an ego view of `name` (Rank/Time click-outs). */
    private _centreOn(name: string): void {
        (getEl("graph-search") as HTMLInputElement).value = name;
        this._setMode("ego");
        void this.loadEgoNetwork(name);
    }

    /** Wire every [data-centre] button inside `root` to the ego jump. */
    private _wireCentre(root: HTMLElement): void {
        root.querySelectorAll<HTMLButtonElement>("[data-centre]").forEach((btn) => {
            btn.addEventListener("click", () => {
                const n = btn.dataset.centre;
                if (n) this._centreOn(n);
            });
        });
    }

    /** Compact score formatting for the mono data voice. */
    private _fmtScore(v: number): string {
        const a = Math.abs(v);
        if (a === 0) return "0";
        if (a >= 100) return v.toFixed(0);
        if (a >= 1) return v.toFixed(2);
        if (a >= 0.001) return v.toFixed(4);
        return v.toExponential(1);
    }

    // --- Ego mode ---------------------------------------------------------- //

    async loadEgoNetwork(name: string): Promise<void> {
        this._setGraphStatus(`Loading ${name}...`);
        const asOf = this._asOf();
        const params = new URLSearchParams();
        if (asOf) params.set("as_of", asOf);
        const qs = params.toString();
        let data: NeighborsBundle;
        try {
            const url = `/api/graph/neighbors/${encodeURIComponent(name)}`
                + (qs ? `?${qs}` : "");
            data = await fetchJson<NeighborsBundle>(url);
        } catch (e) {
            this._setGraphStatus(`Error: ${(e as Error).message}`);
            return;
        }
        if (!this.graph || !this.graph.cy) return;

        const isSector = data.entity_type === "sector";
        const filter: GraphFilter = isSector
            ? "all"
            : (getEl("graph-filter") as HTMLSelectElement).value as GraphFilter;
        const elements: GraphElement[] = isSector
            ? this._buildSectorEgoElements(data as SectorNeighbors)
            : this._bundleElements(data as CompanyNeighbors, filter, "focal");

        this.graph.cy.elements().remove();
        this.graph.cy.add(elements as unknown as CyElementInput[]);
        this.graph.central = isSector ? (data as SectorNeighbors).sector : (data as CompanyNeighbors).company;
        this.graph.elements = elements;
        this.graph.entityType = isSector ? "sector" : "company";

        this._runGraphLayout((getEl("graph-layout") as HTMLSelectElement).value);
        this.graph.cy.fit(undefined, 40);
        this._applyLabelBucket(-1); // ego labels are never zoom-gated

        // Highlight + select the focal node; show it in the side panel.
        this.graph.cy.getElementById(this.graph.central).addClass("focal").select();
        const focalData = this.graph.cy.getElementById(this.graph.central).data();
        (focalData as CyNodeData & { __bundle?: NeighborsBundle }).__bundle = data;
        this._renderGraphDetail(focalData);

        const asOfSuffix = asOf ? ` · as of ${asOf}` : "";
        if (isSector) {
            const sectorBundle = data as SectorNeighbors;
            const capped = sectorBundle.member_count > _SECTOR_RENDER_CAP;
            this._setGraphStatus(
                `${sectorBundle.sector} — ${sectorBundle.member_count} member`
                + (sectorBundle.member_count !== 1 ? "s" : "")
                + (capped ? ` (rendering first ${_SECTOR_RENDER_CAP})` : "")
                + asOfSuffix);
        } else {
            const companyBundle = data as CompanyNeighbors;
            const counts = {
                peers: companyBundle.peers.length,
                jv: companyBundle.jv_partners.length,
                siblings: companyBundle.group_siblings.length,
                acquired: companyBundle.acquired.length,
                suppliers: companyBundle.suppliers.length,
                customers: companyBundle.customers.length,
            };
            const total = Object.values(counts).reduce((a, b) => a + b, 0);
            this._setGraphStatus(
                `${companyBundle.company} — ${total} relationship${total !== 1 ? "s" : ""}`
                + (companyBundle.sector ? ` · ${companyBundle.sector}` : "") + asOfSuffix);
        }
        getEl("graph-empty").style.display = "none";
    }

    /**
     * Sector ego elements: the focal sector + one edge per member company.
     * S3: every member renders (guard cap only) — the old 60-member synthetic
     * "+N more" node is gone.
     */
    _buildSectorEgoElements(data: SectorNeighbors): GraphElement[] {
        const focal = data.sector;
        const all = (data.members || []).slice(0, _SECTOR_RENDER_CAP);
        const nodes: GraphElement[] = [
            { data: { id: focal, label: focal, group: "sector-focal", centrality: 10 } },
        ];
        const edges: GraphElement[] = [];
        all.forEach((m) => {
            nodes.push({ data: { id: m, label: m, group: "member", centrality: 5 } });
            edges.push({
                data: {
                    id: `${focal}__${m}`, source: focal, target: m,
                    type: "has_company", label: "has",
                },
            });
        });
        return [...nodes, ...edges];
    }

    /**
     * Company ego elements from the neighbors bundle. `focalGroup` is "focal"
     * for the canvas centre or "outer" when the same builder merges a second
     * ring in during progressive expansion (the focal node already exists).
     */
    private _bundleElements(
        data: CompanyNeighbors,
        filter: GraphFilter,
        focalGroup: "focal" | "outer",
    ): GraphElement[] {
        const nodes: GraphElement[] = [];
        const edges: GraphElement[] = [];
        const focal = data.company;
        const addNode = (name: string | null | undefined, group: string): void => {
            if (!name || name === focal) return;
            nodes.push({ data: { id: name, label: name, group, centrality: 0 } });
        };
        const addEdge = (
            src: string, dst: string, type: string, label: string,
            props: Record<string, unknown> = {},
        ): void => {
            edges.push({
                data: { id: `${src}__${dst}__${type}`, source: src, target: dst, type, label, props },
            });
        };

        if (focalGroup === "focal") {
            nodes.push({ data: { id: focal, label: focal, group: "focal", centrality: 10 } });
        }

        if (filter === "all" || filter === "peers") {
            data.peers.forEach((p) => { addNode(p, "peer"); addEdge(focal, p, "competes_with", "peer"); });
        }
        if (filter === "all" || filter === "jv") {
            data.jv_partners.forEach((j) => {
                addNode(j.partner, "jv");
                addEdge(focal, j.partner, "jv_with", "JV" + (j.venture ? `: ${j.venture}` : ""));
            });
        }
        if (filter === "all") {
            data.group_siblings.forEach((s) => { addNode(s, "sibling"); addEdge(focal, s, "same_group", "same group"); });
        }
        if (filter === "all" || filter === "acquired") {
            data.acquired.forEach((a) => {
                addNode(a.name, "acquired");
                addEdge(focal, a.name, "acquired", "acquired" + (a.year ? ` ${a.year}` : ""));
            });
        }
        if (filter === "all" || filter === "subsidiary") {
            if (data.subsidiary_of) {
                addNode(data.subsidiary_of, "parent");
                addEdge(data.subsidiary_of, focal, "subsidiary_of", "parent of");
            }
        }
        if (filter === "all" || filter === "supply") {
            data.suppliers.forEach((s) => { addNode(s, "supplier"); addEdge(s, focal, "supplier_to", "supplies"); });
            data.customers.forEach((c) => { addNode(c, "customer"); addEdge(focal, c, "customer_of", "customer"); });
        }
        if (filter === "all" && data.sector) {
            const sectorId = `sector:${data.sector}`;
            nodes.push({ data: { id: sectorId, label: data.sector, group: "sector", centrality: 8 } });
            edges.push({
                data: { id: `${focal}__${sectorId}`, source: focal, target: sectorId, type: "part_of", label: "part of" },
            });
        }

        nodes.forEach((n) => {
            if (n.data.group === "peer") n.data.centrality = 6;
            else if (n.data.group === "parent") n.data.centrality = 7;
            else if (n.data.group === "sector") n.data.centrality = 8;
        });
        return [...nodes, ...edges];
    }

    /**
     * Progressive expansion: fetch the bundle for `name` and merge its
     * not-yet-present nodes/edges into the canvas as a dashed "outer" ring,
     * keeping existing positions (fcose without randomize).
     */
    private async _expandNode(name: string): Promise<void> {
        const cy = this.graph && this.graph.cy;
        if (!cy || !this.graph) return;
        if (cy.nodes().length >= _EXPAND_NODE_CAP) {
            this._setGraphStatus(
                `expansion cap (${_EXPAND_NODE_CAP} nodes) — centre on ${name} to continue there`);
            return;
        }
        this._setGraphStatus(`Adding neighbours of ${name}...`);
        let data: NeighborsBundle;
        try {
            data = await fetchJson<NeighborsBundle>(
                `/api/graph/neighbors/${encodeURIComponent(name)}`);
        } catch (e) {
            this._setGraphStatus(`Error: ${(e as Error).message}`);
            return;
        }
        const els = data.entity_type === "sector"
            ? this._buildSectorEgoElements(data as SectorNeighbors)
            : this._bundleElements(data as CompanyNeighbors, "all", "outer");

        const fresh: GraphElement[] = [];
        els.filter((el) => !el.data.source).forEach((el) => {
            if (cy.getElementById(el.data.id).length === 0
                    && !fresh.some((x) => x.data.id === el.data.id)) {
                el.data.group = "outer";
                fresh.push(el);
            }
        });
        const ids = new Set(fresh.map((el) => el.data.id));
        const freshEdges = els.filter((el) => el.data.source && el.data.target
            && (ids.has(el.data.source) || cy.getElementById(el.data.source).length > 0)
            && (ids.has(el.data.target) || cy.getElementById(el.data.target).length > 0));

        cy.add([...fresh, ...freshEdges] as unknown as CyElementInput[]);
        this._runGraphLayout((getEl("graph-layout") as HTMLSelectElement).value, false, false);
        cy.fit(undefined, 40); // include the merged outer ring in the view
        this._setGraphStatus(
            `+${fresh.length} nodes from ${name} · ${cy.nodes().length} on canvas`);
    }

    // --- Zoom, tooltips, zoom-fade labels ---------------------------------- //

    /** Wire the zoom slider / buttons / fit + the zoom-fade label buckets. */
    _initGraphZoom(): void {
        if (!this.graph || !this.graph.cy) return;
        const slider = getEl("graph-zoom") as HTMLInputElement;
        const label = getEl("graph-zoom-label");
        const cy = this.graph.cy;

        const applyZoom = (): void => {
            const z = parseFloat(slider.value) || 1;
            cy.zoom(z);
            label.textContent = `${Math.round(z * 100)}%`;
        };
        slider.addEventListener("input", applyZoom);
        getEl("graph-zoom-in").addEventListener("click", () => {
            cy.zoom(Math.min(cy.maxZoom(), cy.zoom() * 1.25));
        });
        getEl("graph-zoom-out").addEventListener("click", () => {
            cy.zoom(Math.max(cy.minZoom(), cy.zoom() / 1.25));
        });
        getEl("graph-zoom-fit").addEventListener("click", () => cy.fit(undefined, 30));

        const sync = (): void => {
            const z = cy.zoom();
            slider.value = String(z);
            label.textContent = `${Math.round(z * 100)}%`;
        };
        // cytoscape → slider + label buckets (wheel / pinch / buttons).
        cy.on("zoom", () => {
            sync();
            if (this.graph && this.graph.mode === "all") {
                this._applyLabelBucket(this._labelBucketFor(cy.zoom()));
            }
        });
        // After any layout, keep the slider truthful (layout may re-zoom).
        cy.on("layoutstop", () => {
            sync();
            if (this.graph && this.graph.mode === "all") {
                this._applyLabelBucket(this._labelBucketFor(cy.zoom()));
            }
        });
    }

    private _labelBucketFor(z: number): number {
        if (z < _ZOOM_LBL_OFF) return 0;
        if (z < _ZOOM_LBL_HUBS) return 1;
        return 2;
    }

    /**
     * Apply a zoom-fade label bucket to the CLOUD nodes only (ego labels are
     * always on — small graphs): 0 = no labels, 1 = hubs only, 2 = all.
     * Bucket -1 clears the gate. Only touches elements on bucket CHANGE.
     */
    private _applyLabelBucket(bucket: number): void {
        const cy = this.graph && this.graph.cy;
        if (!cy || !this.graph) return;
        if (bucket === this.graph.labelBucket) return;
        this.graph.labelBucket = bucket;
        cy.batch(() => {
            cy.nodes().forEach((n) => {
                if (n.data().cloud !== "1") return;
                const hub = n.data().hub === "1";
                const show = bucket === 2 || (bucket === 1 && hub);
                n.toggleClass("lbl-hide", !show);
            });
        });
    }

    /** Hover tooltips for nodes + edges (#graph-tip, viewport-positioned). */
    private _initTooltip(): void {
        const cy = this.graph && this.graph.cy;
        if (!cy) return;
        const tip = getEl("graph-tip");
        const canvas = getEl("graph-canvas");
        const place = (e: CyEvent): void => {
            let x = e.renderedPosition?.x;
            let y = e.renderedPosition?.y;
            if (x === undefined || y === undefined) {
                const rect = canvas.getBoundingClientRect();
                x = (e.originalEvent?.clientX ?? rect.left) - rect.left;
                y = (e.originalEvent?.clientY ?? rect.top) - rect.top;
            }
            const maxX = canvas.clientWidth - 290;
            const maxY = canvas.clientHeight - 90;
            tip.style.left = `${Math.max(4, Math.min(x + 14, maxX))}px`;
            tip.style.top = `${Math.max(4, Math.min(y + 14, maxY))}px`;
        };
        cy.on("mouseover", "node", (e) => {
            const d = e.target.data();
            const rows: string[] = [];
            if (d.cloud === "1") {
                rows.push(`<div class="tip-meta">degree ${String(d.deg ?? "?")} · ${escapeHtml(String(d.group || "entity"))}</div>`);
            }
            if (d.community !== undefined) {
                rows.push(`<div class="tip-meta">community ${String(d.community)}</div>`);
            }
            tip.innerHTML =
                `<div class="tip-type">${escapeHtml(String(d.group || "node"))}</div>` +
                `<div class="tip-name">${escapeHtml(String(d.label || d.id))}</div>` +
                rows.join("");
            place(e);
            tip.style.display = "block";
        });
        cy.on("mouseover", "edge", (e) => {
            const d = e.target.data();
            if (d.cloud === "1" && !d.type) return; // hidden cloud edges carry no info
            const props = (d.props || {}) as Record<string, unknown>;
            const extra = Object.keys(props)
                .map((k) => `${k}: ${String(props[k])}`).join(" · ");
            tip.innerHTML =
                `<div class="tip-type">${escapeHtml(String(d.type || "edge"))}</div>` +
                `<div class="tip-name">${escapeHtml(String(d.source))} → ${escapeHtml(String(d.target))}</div>` +
                (extra ? `<div class="tip-meta">${escapeHtml(extra)}</div>` : "");
            place(e);
            tip.style.display = "block";
        });
        cy.on("mouseout", "node", () => this._hideTip());
        cy.on("mouseout", "edge", () => this._hideTip());
        cy.on("zoom", () => this._hideTip());
        cy.on("pan", () => this._hideTip());
    }

    private _hideTip(): void {
        const tip = document.getElementById("graph-tip");
        if (tip) tip.style.display = "none";
    }

    // --- Layouts ------------------------------------------------------------ //

    _runGraphLayout(name: string, cloud = false, randomize = true): void {
        if (!this.graph || !this.graph.cy || this.graph.cy.elements().length === 0) return;

        // Component-separating preset: each connected set gets its own grid
        // cell (only meaningful when elements carry component ids).
        if (name === "components") {
            const els = this.graph.elements || [];
            if (els.some((e) => e.data.component)) {
                const positions = this._cloudComponentPositions(els);
                if (Object.keys(positions).length) {
                    this.graph.cy.layout({
                        name: "preset",
                        positions,
                        animate: !cloud,
                        animationDuration: 300,
                    }).run();
                    return;
                }
            }
            name = "concentric";
        }

        const opts: Record<string, unknown> = {
            name, animate: !cloud, animationDuration: 400, randomize,
        };
        if (name === "fcose") {
            // fcose tiles disconnected components natively (tile: true). On
            // the ego-sized graphs it is the default; on the full cloud it is
            // an explicit opt-in (seconds of main-thread work), with a
            // bounded iteration budget. Valid qualities: default | proof.
            opts.quality = "default";
            opts.nodeSeparation = cloud ? 60 : 90;
            opts.idealEdgeLength = cloud ? 70 : 110;
            opts.edgeElasticity = 0.45;
            opts.gravity = cloud ? 0.25 : 0.3;
            opts.numIter = cloud ? 600 : 2500;
            opts.tile = true;
            opts.tilingPaddingVertical = 40;
            opts.tilingPaddingHorizontal = 40;
            // Cloud fits itself after layout; ego lets the layout fit (the
            // ego path does not call fit(), so fit:false would strand the
            // network outside the viewport).
            if (cloud) opts.fit = false;
        } else if (name === "cose") {
            opts.nodeRepulsion = () => 8000;
            opts.idealEdgeLength = () => 100;
            opts.nodeOverlap = 20;
            if (cloud) {
                opts.randomize = true;
                opts.numIter = 300;
                opts.initialTemp = 200;
                opts.coolingFactor = 0.8;
                opts.minTemp = 1.0;
                opts.gravity = 2;
            }
        } else if (name === "concentric") {
            opts.concentric = (n: CySingular) => (n.data().centrality as number) || 0;
            opts.levelWidth = () => 1;
            opts.minNodeSpacing = 30;
        } else if (name === "breadthfirst") {
            opts.directed = true;
            opts.spacingFactor = 1.2;
            opts.roots = this.graph.central ? `#${CSS.escape(this.graph.central)}` : undefined;
        }
        this.graph.cy.layout(opts).run();
    }

    /**
     * Positions for the component-separating cloud layout (each connected
     * component grid-packed with its hub at the cell centre). Kept as a
     * selectable alternative to fcose's native tiling.
     */
    _cloudComponentPositions(els: GraphElement[]): Record<string, { x: number; y: number }> {
        const nodes = els.filter((e) => e.data.id && !e.data.source);
        if (!nodes.length) return {};
        const compMap = new Map<string, string[]>();
        nodes.forEach((n) => {
            const c = n.data.component || n.data.id;
            if (!compMap.has(c)) compMap.set(c, []);
            compMap.get(c)!.push(n.data.id);
        });
        const comps = [...compMap.values()].sort((a, b) => b.length - a.length);
        const degree: Record<string, number> = {};
        els.forEach((e) => {
            if (e.data.source && e.data.target) {
                degree[e.data.source] = (degree[e.data.source] || 0) + 1;
                degree[e.data.target] = (degree[e.data.target] || 0) + 1;
            }
        });
        const nodeSpacing = 40;
        const cellPad = 90;
        const cellRadius = (n: number): number => Math.max(30, Math.sqrt(n) * nodeSpacing / 2);
        const maxR = Math.max(...comps.map((c) => cellRadius(c.length)));
        const cell = maxR * 2 + cellPad;
        const cols = Math.max(1, Math.ceil(Math.sqrt(comps.length)));
        const positions: Record<string, { x: number; y: number }> = {};
        comps.forEach((comp, i) => {
            const cx = (i % cols) * cell + cell / 2;
            const cyy = (Math.floor(i / cols)) * cell + cell / 2;
            const r = cellRadius(comp.length);
            const sorted = [...comp].sort((a, b) => (degree[b] || 0) - (degree[a] || 0));
            const hub = sorted[0];
            positions[hub] = { x: cx, y: cyy };
            sorted.slice(1).forEach((id, j) => {
                const ang = (j / (sorted.length - 1)) * Math.PI * 2;
                positions[id] = { x: cx + Math.cos(ang) * r, y: cyy + Math.sin(ang) * r };
            });
        });
        return positions;
    }

    // --- Inspector (detail panel) ------------------------------------------- //

    _renderGraphDetail(nodeData: CyNodeData | null): void {
        const panel = getEl("graph-detail");
        if (!nodeData) {
            panel.innerHTML = '<div class="graph-detail-empty"><i class="fas fa-hand-pointer"></i>' +
                              "<p>Click a node to centre the graph on it.</p></div>";
            return;
        }
        const name = nodeData.id;
        const group = (nodeData.group as string) || "company";
        const bundle = (nodeData as CyNodeData & { __bundle?: NeighborsBundle }).__bundle;

        let html = `<div class="graph-detail-header">
            <span class="graph-badge graph-badge-${CSS.escape(group)}">${escapeHtml(group)}</span>
            <h3>${escapeHtml(name)}</h3>
        </div>`;

        if (bundle && bundle.entity_type === "sector") {
            const sectorBundle = bundle as SectorNeighbors;
            html += `<ul class="graph-detail-list">`;
            html += `<li><strong>Members:</strong> ${sectorBundle.member_count}</li>`;
            const mc = sectorBundle.market_cap_counts || {};
            Object.keys(mc).forEach((k) => {
                html += `<li><strong>${escapeHtml(k)}:</strong> ${mc[k]}</li>`;
            });
            html += `</ul>`;
            if (sectorBundle.file_path) {
                html += `<a class="btn-primary" href="/entity/${sectorBundle.file_path}">View sector note →</a>`;
            }
        } else if (bundle) {
            const companyBundle = bundle as CompanyNeighbors;
            html += `<ul class="graph-detail-list">`;
            if (companyBundle.sector) html += `<li><strong>Sector:</strong> ${escapeHtml(companyBundle.sector)}</li>`;
            if (companyBundle.subsidiary_of) html += `<li><strong>Parent:</strong> ${escapeHtml(companyBundle.subsidiary_of)}</li>`;
            html += `<li><strong>Peers:</strong> ${companyBundle.peers.length || "—"}</li>`;
            html += `<li><strong>JV partners:</strong> ${companyBundle.jv_partners.length || "—"}</li>`;
            html += `<li><strong>Group siblings:</strong> ${companyBundle.group_siblings.length || "—"}</li>`;
            html += `<li><strong>Acquired:</strong> ${companyBundle.acquired.length || "—"}</li>`;
            html += `<li><strong>Suppliers:</strong> ${companyBundle.suppliers.length || "—"}</li>`;
            html += `<li><strong>Customers:</strong> ${companyBundle.customers.length || "—"}</li>`;
            html += `</ul>`;
            if (companyBundle.file_path) {
                html += `<a class="btn-primary" href="/entity/${companyBundle.file_path}">View full note →</a>`;
            }
        } else {
            html += `<p class="hint">Click this node (or it's already selected) to re-centre on <em>${escapeHtml(name)}</em>.</p>`;
        }

        panel.innerHTML = html;

        if (!bundle) {
            const row = document.createElement("div");
            row.className = "graph-detail-actions";
            const centreBtn = document.createElement("button");
            centreBtn.className = "btn-primary";
            centreBtn.textContent = `Centre on ${name}`;
            centreBtn.addEventListener("click", () => {
                (getEl("graph-search") as HTMLInputElement).value = name;
                getEl("graph-search-btn").click();
            });
            const expandBtn = document.createElement("button");
            expandBtn.className = "btn-secondary";
            expandBtn.textContent = `＋ ${name}'s neighbours`;
            expandBtn.title = "Merge this node's neighbours into the canvas (progressive expansion)";
            expandBtn.addEventListener("click", () => this._expandNode(name));
            row.append(centreBtn, expandBtn);
            panel.appendChild(row);
        }

        // S4: the events timeline (companies only — sectors/themes/etc. never
        // carry events rows).
        if (!_NON_EVENT_GROUPS.has(group)) {
            const evMount = document.createElement("div");
            evMount.id = "inspector-events";
            evMount.className = "inspector-events";
            panel.appendChild(evMount);
            void this._loadInspectorEvents(name);
        }
    }

    /**
     * Events timeline for the inspected entity (/api/events — acquisitions,
     * JVs, guidance, management changes, date-ordered). Guarded by a
     * monotonic token so a slow fetch can't paint over a newer selection.
     */
    private async _loadInspectorEvents(name: string): Promise<void> {
        const mount = document.getElementById("inspector-events");
        if (!mount || !this.graph) return;
        const seq = ++this.graph.detailSeq;
        mount.innerHTML = `<h4 class="insp-events-head"><i class="fas fa-timeline"></i> Events</h4>
            <p class="hint"><i class="fas fa-spinner fa-spin"></i></p>`;
        let data: EventsResponse;
        try {
            data = await fetchJson<EventsResponse>(`/api/events/${encodeURIComponent(name)}`);
        } catch {
            // 404 or worse: most entities simply have no events — stay quiet.
            if (this.graph.detailSeq === seq) mount.innerHTML = "";
            return;
        }
        if (!this.graph || this.graph.detailSeq !== seq) return;
        if (!data.events.length) {
            mount.innerHTML = `<h4 class="insp-events-head"><i class="fas fa-timeline"></i> Events</h4>
                <p class="hint">None recorded.</p>`;
            return;
        }
        const items = data.events.map((ev) => {
            const bits = [ev.counterparty, ev.magnitude]
                .filter((x): x is string => Boolean(x))
                .map((x) => escapeHtml(x)).join(" · ");
            return `
            <li class="tl-item"${ev.source_quote ? ` title="${escapeHtml(ev.source_quote)}"` : ""}>
                <span class="tl-date mono">${this._eventDateLabel(ev)}</span>
                <span class="tl-type">${escapeHtml(ev.event_type)}</span>
                <span class="tl-body">${bits || "&nbsp;"}</span>
            </li>`;
        }).join("");
        mount.innerHTML = `<h4 class="insp-events-head"><i class="fas fa-timeline"></i> Events
                <span class="cnt mono">${data.event_count}</span></h4>
            <ol class="tl">${items}</ol>`;
    }

    /** Date label cut to the stored precision ("2022-07-14" → 2022 / 2022-07 / full). */
    private _eventDateLabel(ev: EventItem): string {
        if (!ev.event_date) return "—";
        const d = String(ev.event_date);
        if (ev.date_precision === "year") return d.slice(0, 4);
        if (ev.date_precision === "month") return d.slice(0, 7);
        return d.slice(0, 10);
    }

    _setGraphStatus(text: string): void {
        const el = document.getElementById("graph-status");
        if (el) el.textContent = text;
    }

    // --- Stylesheet ---------------------------------------------------------- //

    _cytoscapeStyle(): CyStylesheet {
        // Colours come from the tokens.css --edge-* palette (single source of
        // truth with the legend chips) plus the Desk interaction tokens.
        const ss = cytoscape.stylesheet()
            .selector("node").style({
                "label": "data(label)",
                "text-valign": "bottom",
                "text-halign": "center",
                "text-outline-width": 2,
                "text-outline-color": "#0B0F14",
                "color": "#DCE5EE",
                "font-family": "'IBM Plex Mono', monospace",
                "font-size": 11,
                "width": 26,
                "height": 26,
                "background-color": "#7E8FA3",
            })
            .selector('node[group="focal"]').style({
                "background-color": "#E0A93E", "width": 46, "height": 46,
                "font-size": 14, "font-weight": "bold",
                "color": "#0B0F14", "text-outline-color": "#E0A93E",
            })
            .selector('node[group="peer"]').style({ "background-color": "#F5B14C" })
            .selector('node[group="jv"]').style({ "background-color": "#C39BFF" })
            .selector('node[group="sibling"]').style({ "background-color": "#B5838D" })
            .selector('node[group="acquired"]').style({ "background-color": "#F28B82" })
            .selector('node[group="parent"]').style({ "background-color": "#43AA8B" })
            .selector('node[group="supplier"]').style({ "background-color": "#7CA8C9" })
            .selector('node[group="customer"]').style({ "background-color": "#7CA8C9" })
            .selector('node[group="outer"]').style({
                "background-color": "#66788C",
                "border-width": 1, "border-style": "dashed", "border-color": "#8CA0B4",
                "width": 20, "height": 20,
            })
            // Entity-type groups (cloud + sector ego).
            .selector('node[group="company"]').style({ "background-color": "#DCE5EE" })
            .selector('node[group="theme"]').style({
                "background-color": "#C39BFF", "shape": "hexagon",
                "width": 24, "height": 24,
            })
            .selector('node[group="edition"]').style({
                "background-color": "#D8C9A3", "shape": "rectangle",
                "width": 30, "height": 30,
            })
            .selector('node[group="super_sector"]').style({
                "background-color": "#17766C", "shape": "rectangle",
                "width": 54, "height": 32,
            })
            .selector('node[group="sub_sector"]').style({
                "background-color": "#3E8F86", "shape": "round-rectangle",
                "width": 44, "height": 28,
            })
            .selector('node[group="sector"]').style({
                "background-color": "#2DD4BF", "shape": "rectangle",
                "width": 50, "height": 30,
            })
            .selector('node[group="sector-focal"]').style({
                "background-color": "#1FB9A6", "shape": "rectangle",
                "width": 56, "height": 36, "font-size": 14, "font-weight": "bold",
            })
            .selector('node[group="member"]').style({
                "background-color": "#7CA8C9", "width": 22, "height": 22,
            })
            // Path subgraph (Path mode).
            .selector('node[group="path-end"]').style({
                "background-color": "#E0A93E", "width": 40, "height": 40,
                "font-weight": "bold",
            })
            .selector("node.focal").style({
                "border-width": 3, "border-color": "#F5D08C",
            })
            // Louvain shading overrides the type colour with data(color).
            .selector("node.shaded").style({ "background-color": "data(color)" })
            // Zoom-fade labels (cloud).
            .selector("node.lbl-hide").style({ "text-opacity": 0 })
            .selector("edge").style({
                "width": 2,
                "line-color": "#3B4A5C",
                "target-arrow-color": "#3B4A5C",
                "target-arrow-shape": "triangle",
                "curve-style": "bezier",
                "label": "data(label)",
                "font-family": "'IBM Plex Mono', monospace",
                "font-size": 9,
                "color": "#9FB0BF",
                "text-background-color": "#0B0F14",
                "text-background-padding": 2,
                "text-background-opacity": 0.75,
            })
            .selector('edge[type="path-hop"]').style({
                "width": 3.5, "line-color": "#E0A93E", "target-arrow-color": "#E0A93E",
                "curve-style": "bezier", "z-index": 10,
            });

        // Edge-type colours from the token palette.
        Object.keys(_EDGE_TOKENS).forEach((t) => {
            ss.selector(`edge[type="${t}"]`).style({
                "line-color": edgeColor(t),
                "target-arrow-color": edgeColor(t),
            });
        });

        ss.selector('edge[type="co_mentioned_in"], edge[type="jv_with"], edge[type="competes_with"], edge[type="same_group"]')
            .style({ "target-arrow-shape": "none" })
            .selector("edge.highlighted").style({
                "width": 4, "line-color": "#E0A93E", "target-arrow-color": "#E0A93E",
                "z-index": 10,
            })
            .selector("node.highlighted").style({
                "border-width": 3, "border-color": "#E0A93E", "z-index": 10,
            })
            // Cloud styling: straight thin edges, no text (the #1 render
            // cost at 4k+ edges); degree-scaled nodes; zoom-gated labels.
            .selector('edge[cloud="1"]').style({
                "curve-style": "straight",
                "width": 1,
                "label": "",
                "text-opacity": 0,
                "font-size": 0,
            })
            .selector('node[cloud="1"]').style({
                "font-size": 9,
                "text-outline-width": 1,
                "min-zoomed-font-size": 7,
                "width": "mapData(deg, 1, 40, 10, 46)",
                "height": "mapData(deg, 1, 40, 10, 46)",
            })
            .selector("edge.in-set").style({
                "width": 4,
                "line-color": "#E0A93E",
                "target-arrow-color": "#E0A93E",
                "z-index": 12,
                "overlay-opacity": 0,
            })
            .selector("node.in-set").style({
                "border-width": 3,
                "border-color": "#E0A93E",
                "z-index": 12,
            })
            .selector(".faded").style({ "opacity": 0.25 });
        return ss;
    }

    // --- Path mode ------------------------------------------------------------ //

    async loadShortestPath(): Promise<void> {
        const a = (getEl("shortest-a") as HTMLInputElement).value.trim();
        const b = (getEl("shortest-b") as HTMLInputElement).value.trim();
        const result = getEl("shortest-result");
        if (!a || !b) {
            result.innerHTML = '<p class="hint">Enter both entities.</p>';
            return;
        }
        // The Chronoscope drives path queries too, so a user exploring
        // "as of 2022" gets results consistent with the ego canvas.
        const asOf = this._asOf();
        const params = new URLSearchParams({ a, b });
        if (asOf) params.set("as_of", asOf);
        result.innerHTML = '<p><i class="fas fa-spinner fa-spin"></i> Finding path...</p>';
        try {
            const data = await fetchJson<ShortestPathResponse>(`/api/graph/shortest?${params}`);
            this._renderShortestPath(data);
        } catch (e) {
            result.innerHTML = `<p class="error">${escapeHtml((e as Error).message)}</p>`;
        }
    }

    _renderShortestPath(data: ShortestPathResponse): void {
        const result = getEl("shortest-result");
        const cy = this.graph && this.graph.cy;
        if (data.path === null) {
            result.innerHTML =
                `<p class="hint">No path found between <em>${escapeHtml(data.source)}</em> and `
                + `<em>${escapeHtml(data.target)}</em> within the hop limit`
                + (this._asOf() ? ` as of ${this._asOf()}` : "") + `.</p>`;
            return;
        }
        const chain = data.path.map((p) => p.name);
        const hops = data.hops ?? 0;
        // Hop ribbon: chips are clickable — jumps to an ego view of that hop.
        const ribbon = chain.map((n, i) =>
            `<button type="button" class="hop-chip" data-hop-name="${escapeHtml(n)}" `
            + `title="Centre the Lens on ${escapeHtml(n)}">`
            + `<span class="hop-idx">${i + 1}</span>${escapeHtml(n)}</button>`)
            .join(`<span class="hop-arrow">→</span>`);
        result.innerHTML = `
            <div class="path-ribbon">${ribbon}</div>
            <p class="hint">${hops} hop${hops !== 1 ? "s" : ""}`
            + (this._asOf() ? ` · as of ${this._asOf()}` : " · now") + `</p>`;
        result.querySelectorAll<HTMLButtonElement>(".hop-chip").forEach((chip) => {
            chip.addEventListener("click", () => {
                const n = chip.dataset.hopName;
                if (!n) return;
                (getEl("graph-search") as HTMLInputElement).value = n;
                this._setMode("ego");
                this.loadEgoNetwork(n);
            });
        });

        if (!cy) return;
        // Path mode renders the path as its own subgraph (a clean hop chain);
        // in ego mode we keep the old behaviour of highlighting the path when
        // it is fully present on the canvas.
        if (this.graph && this.graph.mode === "path") {
            const elements: GraphElement[] = chain.map((n, i) => ({
                data: {
                    id: n, label: n,
                    group: (i === 0 || i === chain.length - 1) ? "path-end" : "company",
                },
            }));
            for (let i = 0; i < chain.length - 1; i++) {
                elements.push({
                    data: {
                        id: `path__${chain[i]}__${chain[i + 1]}`,
                        source: chain[i], target: chain[i + 1],
                        type: "path-hop", label: String(i + 1),
                    },
                });
            }
            cy.elements().remove();
            cy.add(elements as unknown as CyElementInput[]);
            cy.layout({
                name: "breadthfirst", directed: true, spacingFactor: 1.4,
                roots: `#${CSS.escape(chain[0])}`, animate: true, animationDuration: 400,
            }).run();
            cy.fit(undefined, 60);
            this._applyLabelBucket(-1);
            getEl("graph-empty").style.display = "none";
        } else {
            cy.elements().removeClass("highlighted faded");
            const pathNodes = chain.filter((n) => cy.getElementById(n).length > 0);
            if (pathNodes.length === chain.length) {
                const pathEles = cy.collection();
                for (let i = 0; i < chain.length - 1; i++) {
                    const e = cy.getElementById(chain[i]).edgesWith(cy.getElementById(chain[i + 1]));
                    pathEles.merge(e);
                    pathEles.merge(cy.getElementById(chain[i]));
                }
                pathEles.merge(cy.getElementById(chain[chain.length - 1]));
                pathEles.addClass("highlighted");
                cy.elements().not(pathEles).addClass("faded");
            }
        }
    }

    clearShortestPath(): void {
        getEl("shortest-result").innerHTML = "";
        (getEl("shortest-a") as HTMLInputElement).value = "";
        (getEl("shortest-b") as HTMLInputElement).value = "";
        if (this.graph && this.graph.cy) {
            this.graph.cy.elements().removeClass("highlighted faded");
        }
    }
}
