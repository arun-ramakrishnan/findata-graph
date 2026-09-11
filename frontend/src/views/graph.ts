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
// Rendering is sigma.js v3 (WebGL) via graphRenderer.ts — the S1 swap from
// graph_rendering_overhaul.md. This file keeps the domain layer only:
// payloads → GraphElement[], modes, filters, panels; the renderer owns
// the canvas, layouts, reducers and camera. The default layout is the
// server-side "cached" sidecar when it has landed (stable fcose-quality
// coordinates, zero client layout cost), FA2 otherwise. Edge/node colours
// read the --edge-* / interaction tokens from tokens.css at module load
// (single source of truth: legend chips, filters and canvas cannot drift).
//
// The window.viewer inline-onclick contract does NOT extend into this view:
// every control below is wired with addEventListener.

import type {
    BridgesResponse,
    CompanyNeighbors,
    CoMentionsResponse,
    EdgesByYearResponse,
    EntitiesResponse,
    EventItem,
    EventsResponse,
    GraphCloudResponse,
    GraphPositionsResponse,
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
import {
    GraphRenderer,
    type EdgeHoverInfo,
    type GraphElement,
    type NodePayload,
    COMMUNITY_PALETTE,
    edgeColor,
} from "./graphRenderer";

/** Union of the graph relationship filters from the #graph-filter dropdown. */
type GraphFilter = "all" | "peers" | "jv" | "acquired" | "subsidiary" | "supply";

/** Lens modes (S3: ego / all / path; S4: rank / time). */
type LensMode = "ego" | "all" | "path" | "rank" | "time";

/** Whole-graph cloud data, kept client-side for filter re-application. */
interface CloudCache {
    data: GraphCloudResponse;
    degree: Record<string, number>;
    communities: Map<string, number> | null;
    /** Server-side precomputed positions (lane 3), null until fetched/failed.
     * Same coordinate space as the full cloud — filtered subsets reuse them. */
    positions: Record<string, { x: number; y: number }> | null;
}

/** Lazy graph-tab state, initialized on first visit to the Graph view. */
interface GraphState {
    renderer: GraphRenderer | null;
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
    /** True while the on-canvas subgraph is small enough that labels are
     *  always on (zoom-fade gating suspended). Ego sets it false. */
    labelAlways: boolean;
    /** True once the user picks a layout explicitly (cloud defaults to the
     *  cached sidecar / component preset until then). */
    layoutTouched: boolean;
    /** Neighbors bundle of the ego focal (drives the Inspector panel; the
     *  old cytoscape node-data attachment hack, minus the node data). */
    focalBundle: NeighborsBundle | null;
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
    /** Timestamp of the last node tap (ego re-centre double-fire guard —
     *  sigma emits two clickNode events ahead of a double click). */
    lastNodeTapAt: number;
}

// --------------------------------------------------------------------------- //
// Palette: edge/community colours live in graphRenderer.ts (read from the   //
// tokens.css --edge-* tokens at module load) — re-exported above.           //
// --------------------------------------------------------------------------- //

/** Cloud node considered a hub for zoom-fade labels + sizing. */
const _HUB_DEGREE = 6;

/** Guard rails for progressive expansion / sector member renders. */
const _EXPAND_NODE_CAP = 150;
const _SECTOR_RENDER_CAP = 200;
/** Camera-ratio thresholds for the label-fade buckets (cloud mode). Sigma
 *  ratio is fit-relative (1 = whole graph fitted): zoomed out past 2.5× no
 *  labels, past 1.4× hubs only, otherwise the density grid governs. The old
 *  cytoscape thresholds were model-px zooms — meaningless in normalized
 *  camera space, so the buckets became fit-relative (recorded in the S1 log).
 *  Sigma's label density grid + size threshold keep the fitted cloud calm. */
const _RATIO_LBL_OFF = 2.5;
const _RATIO_LBL_HUBS = 1.4;
/** Cloud node sizing: sqrt degree curve between these px bounds. */
const _NODE_SIZE_MIN = 9;
const _NODE_SIZE_MAX = 30;
/** Post-fit zoom ceilings — a 4-node ego graph must not balloon to 200%+.
 *  Applied by _fitCapped after every fit (screenshot 2 regression). */
const _EGO_FIT_MAX_ZOOM = 1.3;
const _CLOUD_FIT_MAX_ZOOM = 1.1;
/** Adaptive render policy for filtered subgraphs: below these sizes the
 *  cloud drops its big-graph compromises (zoom-gated labels, silent edges). */
const _LABEL_ALWAYS_NODES = 400;
const _EDGE_LABEL_EDGES = 80;

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
    "sector",
    "sector-focal",
    "member",
    "super_sector",
    "sub_sector",
    "edition",
    "theme",
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
                renderer: null,
                central: null,
                elements: null,
                entitiesLoaded: false,
                mode: "ego",
                cloud: null,
                cloudMode: false,
                hiddenEdgeTypes: new Set(),
                labelBucket: -1,
                labelAlways: false,
                layoutTouched: false,
                focalBundle: null,
                rankData: new Map(),
                rankGroups: null,
                rankSeeds: null,
                suggestions: new Map(),
                timeByYear: null,
                timeBridges: null,
                timeCoMentions: null,
                nearDup: null,
                detailSeq: 0,
                lastNodeTapAt: 0,
            };
        }
        // Build the renderer if it doesn't exist yet.
        if (!this.graph.renderer) {
            const canvas = getEl("graph-canvas");
            this.graph.renderer = new GraphRenderer(canvas, {
                // Node tap: cloud mode highlights the tapped connected set +
                // opens its detail panel; ego mode re-centres on the node.
                // Double-fire guard: sigma emits two clickNode events ahead
                // of a double click — the ego re-centre (a fetch + full
                // re-render) only runs 350 ms after the previous tap.
                onNodeTap: (id) => {
                    const g = this.graph;
                    const attrs = g?.renderer?.nodeAttrs(id);
                    if (!g || !attrs) return;
                    if (g.mode === "all") {
                        this._highlightCloudSet(attrs);
                        this._renderGraphDetail(attrs);
                        return;
                    }
                    const now = performance.now();
                    if (now - g.lastNodeTapAt < 350) return;
                    g.lastNodeTapAt = now;
                    if (id !== g.central) {
                        (getEl("graph-search") as HTMLInputElement).value = id;
                        this._setMode("ego");
                        return this.loadEgoNetwork(id);
                    }
                },
                onStageTap: () => {
                    if (this.graph?.mode === "all") this._clearCloudHighlight();
                },
                onNodeHover: (attrs, x, y) => this._showNodeTip(attrs, x, y),
                onNodeLeave: () => this._hideTip(),
                onEdgeHover: (attrs, x, y) => this._showEdgeTip(attrs, x, y),
                onEdgeLeave: () => this._hideTip(),
                onCameraChange: () => this._onCameraChange(),
                onError: (e) => this._setGraphStatus(`graph error: ${(e as Error).message}`),
            });

            // --- toolbar ------------------------------------------------- //
            const centreFromSearch = async (): Promise<void> => {
                const name = (getEl("graph-search") as HTMLInputElement).value.trim();
                if (!name) return;
                // All mode: spotlight the entity inside the CURRENT filter
                // instead of yanking the view to Ego (the old behaviour read
                // as "the button does nothing"). Ego stays the fallback.
                if (
                    this.graph &&
                    this.graph.mode === "all" &&
                    this.graph.cloud &&
                    this._spotlightInCloud(name)
                )
                    return;
                this._setMode("ego");
                await this.loadEgoNetwork(name);
            };
            getEl("graph-search-btn").addEventListener("click", () => void centreFromSearch());
            (getEl("graph-search") as HTMLInputElement).addEventListener("keydown", (e) => {
                if (e.key === "Enter") void centreFromSearch();
            });
            (getEl("graph-layout") as HTMLSelectElement).addEventListener("change", (e) => {
                if (!this.graph || !this.graph.renderer) return;
                this.graph.layoutTouched = true;
                const inCloud = this.graph.mode === "all";
                this._runGraphLayout((e.target as HTMLSelectElement).value, inCloud);
                if (inCloud) this._fitCapped(30, _CLOUD_FIT_MAX_ZOOM);
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
                    if (data.status !== "ok") {
                        this._setGraphStatus("refresh failed");
                        return;
                    }
                    this._setGraphStatus("DB refreshed — view reloaded");
                    // Re-run the active view with the fresh connection (used
                    // to tell the user to re-run it by hand). Capture the
                    // pre-clear state — the wipes below null it out.
                    const rerunMode = this.graph!.mode;
                    const rerunCentral = this.graph!.central;
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
                    if (rerunMode === "all") {
                        void this.loadGraphCloud();
                    } else if (rerunMode === "ego" && rerunCentral) {
                        void this.loadEgoNetwork(rerunCentral);
                    } else if (rerunMode === "rank") {
                        void this._loadRankView();
                    } else if (rerunMode === "time") {
                        void this._loadTimeView();
                    }
                } catch (e) {
                    this._setGraphStatus("refresh failed: " + (e as Error).message);
                } finally {
                    btn.disabled = false;
                }
            });
            getEl("shortest-btn").addEventListener("click", () => this.loadShortestPath());
            getEl("shortest-clear").addEventListener("click", () => this.clearShortestPath());

            this._initLensRail();
            this._initGraphZoom();
            this._setMode("ego");
        }
        // Populate the typeahead (one-time; small query).
        if (!this.graph.entitiesLoaded) {
            await this.loadGraphEntityList();
            this.graph.entitiesLoaded = true;
        }
        // Kick a refresh after the section becomes visible (the renderer
        // needs the container to have non-zero dimensions before first paint).
        setTimeout(() => this.graph?.renderer?.refresh(), 50);
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
        (getEl("graph-filter").closest(".filters") as HTMLElement | null)?.style.setProperty(
            "display",
            mode === "ego" ? "" : "none",
        );

        // S4 table modes: Rank/Time replace the canvas row with data panels;
        // the Chronoscope has no as-of semantics for whole-graph aggregates.
        const tableMode = mode === "rank" || mode === "time";
        getEl("lens-rank").style.display = mode === "rank" ? "block" : "none";
        getEl("lens-time").style.display = mode === "time" ? "block" : "none";
        getEl("lens-asof-block").style.display = tableMode ? "none" : "block";
        (
            document.querySelector("#graph-view .graph-layout-row") as HTMLElement | null
        )?.style.setProperty("display", tableMode ? "none" : "");

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
        // Returning from a table mode: the renderer needs a refresh kick
        // after its container sat display:none (cheap no-op otherwise).
        if (!tableMode) setTimeout(() => this.graph?.renderer?.refresh(), 0);
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
        if (!this.graph || !this.graph.renderer) return;
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

        this.graph.cloud = { data, degree, communities: null, positions: null };
        this.graph.central = null;
        this.graph.entityType = undefined;
        this._clearCloudHighlight();

        // Lane 3: precomputed positions are a progressive enhancement —
        // fetch in parallel, never block the first (components-preset)
        // paint; when they land on an untouched layout, re-run the cheap
        // preset with stable server coordinates.
        void this._loadCachedPositions();

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

    /** Fetch the server-side layout sidecar into the cloud cache and apply
     * it to the visible cloud when the user hasn't picked a layout yet.
     * Silent on failure by design: positions are an enhancement, the local
     * components/concentric default remains fully functional without them. */
    private async _loadCachedPositions(): Promise<void> {
        try {
            const data = await fetchJson<GraphPositionsResponse>("/api/graph/positions");
            const graph = this.graph;
            if (!data.positions || !graph?.cloud) return;
            const positions: Record<string, { x: number; y: number }> = {};
            for (const [id, xy] of Object.entries(data.positions)) {
                positions[id] = { x: xy[0], y: xy[1] };
            }
            graph.cloud.positions = positions;
            // Apply now only if the cloud is on screen and untouched: an
            // explicit layout pick (layoutTouched) always wins.
            if (
                graph.mode === "all" &&
                graph.renderer &&
                !graph.layoutTouched &&
                (getEl("graph-layout") as HTMLSelectElement).value === "fcose"
            ) {
                this._runGraphLayout("cached", true);
                this._fitCapped(30, _CLOUD_FIT_MAX_ZOOM);
                this._applyLabelBucket(this._labelBucketFor(graph.renderer.zoomValue()));
            }
        } catch {
            // Cold server, ceiling refusal, or transient error — the status
            // line stays with the render message; nothing to surface.
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
        const renderer = this.graph && this.graph.renderer;
        const cache = this.graph && this.graph.cloud;
        if (!renderer || !cache) return;
        const everything = (getEl("cloud-everything") as HTMLInputElement).checked;
        const minDegree = everything
            ? 1
            : (getEl("cloud-min-degree") as HTMLInputElement).checked
              ? 2
              : 1;

        const hidden = this.graph!.hiddenEdgeTypes;
        const edgeFilterActive = cache.data.relationship_types.some((rt) =>
            hidden.has(rt.edge_type),
        );

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

        // Visible-degree drives every visual encoding (size, hub flag): the
        // old code sized nodes by FULL-graph degree, so a 1-edge filtered
        // pair still rendered as a 46px blob (screenshot 1). With no filter
        // active this equals the full degree.
        const visDegree: Record<string, number> = {};
        visibleEdges.forEach((e) => {
            visDegree[e.source] = (visDegree[e.source] || 0) + 1;
            visDegree[e.target] = (visDegree[e.target] || 0) + 1;
        });
        let maxDeg = 1;
        kept.forEach((id) => {
            maxDeg = Math.max(maxDeg, visDegree[id] || 0);
        });
        const sizeFor = (deg: number): number =>
            Math.round(
                _NODE_SIZE_MIN + (_NODE_SIZE_MAX - _NODE_SIZE_MIN) * Math.sqrt(deg / maxDeg),
            );

        const elements: GraphElement[] = cache.data.nodes
            .filter((n) => kept.has(n.id))
            .map((n) => {
                const deg = visDegree[n.id] || 0;
                return {
                    data: {
                        id: n.id,
                        label: n.label,
                        group: n.entity_type,
                        cloud: "1",
                        deg,
                        size: sizeFor(deg),
                        centrality: deg,
                        hub: deg >= _HUB_DEGREE ? "1" : "",
                        component: find(n.id),
                    },
                };
            });
        // Small subgraphs can afford edge labels — in a mixed view the type
        // name on each edge is the only "which relationship is this" cue.
        // Single-type views stay silent (arrows + the chip caption suffice);
        // the cloud payload carries no per-edge properties, so there is
        // nothing richer to show without an API change.
        const labelEdges =
            visibleEdges.length <= _EDGE_LABEL_EDGES &&
            cache.data.relationship_types.length - hidden.size > 1;
        visibleEdges.forEach((e) => {
            if (!kept.has(e.source) || !kept.has(e.target)) return;
            elements.push({
                data: {
                    id: `${e.source}__${e.target}__${e.edge_type}`,
                    source: e.source,
                    target: e.target,
                    type: e.edge_type,
                    label: labelEdges ? e.edge_type : "", // 4k+ labels are the #1 render cost
                    cloud: "1",
                },
            });
        });

        renderer.setElements(elements);
        this.graph!.elements = elements;

        const nodeCount = elements.filter((e) => !e.data.source).length;
        const edgeCount = elements.length - nodeCount;
        if (elements.length === 0) {
            this.graph!.labelAlways = false;
            this._setGraphStatus(
                "Edge filter — no relationships selected. Click a relationship " +
                    "chip (or “all”) to show its subgraph.",
            );
            return;
        }

        // Small subgraph = drop the big-cloud compromises: labels always on
        // (the zoom-fade buckets exist for the ~700-node full cloud only —
        // at a filtered view's fit zoom they hid EVERY label). Applied
        // before the layout so the bucket state + zoom handler agree.
        const small = nodeCount <= _LABEL_ALWAYS_NODES;
        this.graph!.labelAlways = small;
        if (small) this._applyLabelBucket(2);

        // Cloud default layout: the precomputed server sidecar when it has
        // landed (stable fcose-quality coordinates, zero client layout
        // cost), else the fast component-packing preset; an explicit force
        // run on the ~700-node filtered cloud takes seconds on the main
        // thread. An explicit layout pick (layoutTouched) is always honored.
        const selected = (getEl("graph-layout") as HTMLSelectElement).value;
        const cloudLayout =
            selected === "fcose" && !this.graph!.layoutTouched
                ? cache.positions
                    ? "cached"
                    : "components"
                : selected;
        this._runGraphLayout(cloudLayout, true);
        this._fitCapped(30, _CLOUD_FIT_MAX_ZOOM);
        if (!small) this._applyLabelBucket(this._labelBucketFor(renderer.zoomValue()));

        if (edgeFilterActive) {
            const shown = cache.data.relationship_types.length - hidden.size;
            this._setGraphStatus(
                `Edge filter — ${nodeCount} entities · ${edgeCount} edges ` +
                    `(${shown} of ${cache.data.relationship_types.length} relationship types)`,
            );
        } else {
            const filterNote = everything ? "everything" : "min degree ≥ 2";
            this._setGraphStatus(
                `Full graph — ${nodeCount} entities · ${edgeCount} edges (${filterNote})`,
            );
        }
    }

    /**
     * Fit the canvas to its elements, then clamp the zoom (see
     * GraphRenderer.fitCapped — sigma's normalized camera keeps node sizes
     * screen-constant, so the small-graph balloon the cap fixed in
     * cytoscape does not arise; the cap remains a sanity bound).
     */
    _fitCapped(padding: number, maxZoom: number, focusId?: string): void {
        this.graph?.renderer?.fitCapped(padding, maxZoom, focusId);
    }

    /**
     * All-mode search: centre the camera on `name`'s connected set within the
     * CURRENT filter and highlight it. Returns false when the entity is not
     * on the canvas (caller falls back to the Ego jump).
     */
    _spotlightInCloud(name: string): boolean {
        const renderer = this.graph && this.graph.renderer;
        if (!renderer || !this.graph) return false;
        if (!renderer.hasNode(name)) return false;
        this._highlightCloudSet(renderer.nodeAttrs(name));
        const count = renderer.spotlight(name);
        this._setGraphStatus(`Spotlight — ${name} · ${count} entities in its connected set`);
        return true;
    }

    /**
     * Highlight the connected set (component) a tapped node belongs to:
     * reducer-level in-set emphasis for its members, everything else fades
     * toward the background.
     */
    _highlightCloudSet(nodeData: NodePayload | undefined): void {
        if (!this.graph || this.graph.mode !== "all") return;
        this.graph.renderer?.highlightComponent(nodeData?.component ?? null);
    }

    /** Remove the cloud set-highlight (restores full opacity). */
    _clearCloudHighlight(): void {
        this.graph?.renderer?.highlightComponent(null);
    }

    /**
     * Legend with INTERACTIVE edge chips: clicking a chip hides/shows that
     * relationship type client-side (no refetch). "All/none" restore.
     */
    _renderCloudLegend(data: GraphCloudResponse): void {
        const legend = getEl("graph-cloud-legend");
        const nodeTypes = [...new Set(data.nodes.map((n) => n.entity_type))].sort();
        // Swatch square + label BESIDE it — the old markup put the label
        // INSIDE the 12px swatch box, so the type names overlapped into a
        // garbled line (screenshot 1).
        const nodeHtml = nodeTypes
            .map(
                (t) => `
            <span class="cloud-legend-chip">
                <span class="cloud-swatch cloud-node-${CSS.escape(t)}"></span>${escapeHtml(t)}
            </span>`,
            )
            .join("");
        const chips = data.relationship_types
            .map((t) => {
                const off = this.graph?.hiddenEdgeTypes.has(t.edge_type) ? " off" : "";
                return `<button type="button" class="edge-chip${off}"
                            data-edge-type="${escapeHtml(t.edge_type)}"
                            title="${escapeHtml(t.semantics)} — ${t.count} edge${t.count !== 1 ? "s" : ""}. Click to hide/show.">
                        <span class="dot" style="background:${edgeColor(t.edge_type)}"></span>
                        ${escapeHtml(t.edge_type)}
                        <span class="chip-count">${t.count}</span>
                    </button>`;
            })
            .join("");
        legend.innerHTML = `
            <div class="cloud-legend-group"><strong>Entities</strong>
                <div class="cloud-legend-chips">${nodeHtml}</div></div>
            <div class="cloud-legend-group"><strong>Relationships — click to hide · double-click to isolate</strong>
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
            chip.addEventListener("dblclick", () => {
                const t = chip.dataset.edgeType;
                if (t && !t.startsWith("__")) this._soloEdgeType(t);
            });
        });
    }

    /** Reflect hiddenEdgeTypes on every chip (legend + relationship cloud). */
    private _syncChipStates(): void {
        document
            .querySelectorAll<HTMLButtonElement>(
                ".edge-chip[data-edge-type], .rel-cloud-chip[data-edge-type]",
            )
            .forEach((chip) => {
                const t = chip.dataset.edgeType;
                if (!t || t.startsWith("__")) return;
                chip.classList.toggle("off", this.graph!.hiddenEdgeTypes.has(t));
            });
    }

    /** Double-click a chip: show ONLY that relationship type (isolate) —
     *  the one-click answer to "see only acquisitions" that previously
     *  needed the none → chip dance. Restore with the "all" chip. */
    private _soloEdgeType(t: string): void {
        const cache = this.graph && this.graph.cloud;
        if (!cache || !t) return;
        cache.data.relationship_types.forEach((rt) => {
            if (rt.edge_type === t) this.graph!.hiddenEdgeTypes.delete(rt.edge_type);
            else this.graph!.hiddenEdgeTypes.add(rt.edge_type);
        });
        this._syncChipStates();
        this._applyCloudFilter();
    }

    /** Show/hide one relationship type: rebuild the induced subgraph. */
    private _toggleEdgeType(t: string): void {
        if (!this.graph || !this.graph.cloud || !t) return;
        const nowHidden = !this.graph.hiddenEdgeTypes.has(t);
        if (nowHidden) this.graph.hiddenEdgeTypes.add(t);
        else this.graph.hiddenEdgeTypes.delete(t);
        // Refresh the chip state in BOTH the legend and the relationship cloud.
        this._syncChipStates();
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
        this._syncChipStates();
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
        const chips = types
            .map((t) => {
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
            })
            .join("");
        card.innerHTML = `<h4 class="rel-cloud-title"><i class="fas fa-cloud"></i> Relationship Cloud</h4>
                          <div class="rel-cloud-chips">${chips}</div>`;
        card.querySelectorAll<HTMLButtonElement>(".rel-cloud-chip").forEach((chip) => {
            chip.addEventListener("click", () => {
                const et = chip.dataset.edgeType;
                if (et) this._toggleEdgeType(et);
            });
            chip.addEventListener("dblclick", () => {
                const et = chip.dataset.edgeType;
                if (et) this._soloEdgeType(et);
            });
        });
    }

    /** Louvain community shading (All mode): fetch once, colour nodes. */
    private async _applyCommunityShading(on: boolean): Promise<void> {
        const renderer = this.graph && this.graph.renderer;
        const cache = this.graph && this.graph.cloud;
        if (!renderer || !cache) return;
        if (!on) {
            renderer.setCommunities(null);
            return;
        }
        if (!cache.communities) {
            try {
                const m = await fetchJson<MetricGroupsResponse>(
                    "/api/graph/metrics/louvain_community",
                );
                const map = new Map<string, number>();
                m.groups.forEach((g) => g.members.forEach((name) => map.set(name, g.label)));
                cache.communities = map;
            } catch (e) {
                this._setGraphStatus(
                    `communities unavailable: ${(e as Error).message} (run make recompute-graph)`,
                );
                (getEl("cloud-community") as HTMLInputElement).checked = false;
                return;
            }
        }
        renderer.setCommunities(cache.communities);
        this._setGraphStatus(
            `Louvain shading on — ${cache.communities.size} entities in communities`,
        );
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
            `<p class="hint">unavailable — ${escapeHtml((e as Error).message)}` +
            ` (is <span class="mono">make recompute-graph</span> fresh?)</p>`;

        if (metric === "voterank") {
            let seeds = this.graph.rankSeeds;
            if (!seeds) {
                wrap.innerHTML = loading;
                try {
                    const data = await fetchJson<MetricSeedsResponse>(
                        "/api/graph/metrics/voterank",
                    );
                    seeds = data.seeds;
                    this.graph.rankSeeds = seeds;
                } catch (e) {
                    wrap.innerHTML = fail(e);
                    return;
                }
            }
            const rows = seeds
                .slice(0, top)
                .map(
                    (name, i) => `
                <tr>
                    <td class="idx num">${i + 1}</td>
                    <td><button type="button" class="rank-entity" data-centre="${escapeHtml(name)}"
                                title="Centre the Lens on ${escapeHtml(name)}">${escapeHtml(name)}</button></td>
                    <td class="num muted">seed ${i + 1} of ${seeds.length}</td>
                </tr>`,
                )
                .join("");
            wrap.innerHTML = `<p class="panel-note mono">${escapeHtml(blurb)}</p>
                <table class="rank-table">
                    <thead><tr><th class="num">#</th><th>entity</th><th class="num">note</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>
                <p class="panel-note mono">${Math.min(seeds.length, top)} of ${seeds.length} seeds</p>`;
            this._wireCentre(wrap);
            this._setGraphStatus(
                `Rank — voterank · ${Math.min(seeds.length, top)} of ${seeds.length} seeds`,
            );
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
                        `/api/graph/metrics/${metric}?top=${top}`,
                    );
                    this.graph.rankData.set(key, data);
                } catch (e) {
                    wrap.innerHTML = fail(e);
                    return;
                }
            }
            const rows = data.entities
                .slice(0, top)
                .map((ent, i) => {
                    const best = ent.candidates[0];
                    return `
                <tr>
                    <td class="idx num">${i + 1}</td>
                    <td><button type="button" class="rank-entity" data-centre="${escapeHtml(ent.entity)}"
                                title="Centre the Lens on ${escapeHtml(ent.entity)}">${escapeHtml(ent.entity)}</button></td>
                    <td>${
                        best
                            ? `<button type="button" class="rank-entity muted-entity" data-centre="${escapeHtml(best.name)}"
                                title="Centre the Lens on ${escapeHtml(best.name)}">↔ ${escapeHtml(best.name)}</button>`
                            : '<span class="muted">—</span>'
                    }</td>
                    <td class="num">${this._fmtScore(ent.best_score)}</td>
                </tr>`;
                })
                .join("");
            wrap.innerHTML = `<p class="panel-note mono">${escapeHtml(blurb)}</p>
                <table class="rank-table">
                    <thead><tr><th class="num">#</th><th>entity</th><th>predicted partner</th><th class="num">best</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>
                <p class="panel-note mono">${Math.min(data.entities.length, top)} of ${data.total} scored entities</p>`;
            this._wireCentre(wrap);
            this._setGraphStatus(
                `Rank — link prediction · ${Math.min(data.entities.length, top)} of ${data.total}`,
            );
            return;
        }

        // Scalar centralities.
        let data = this.graph.rankData.get(key) as MetricRankedResponse | undefined;
        if (!data) {
            wrap.innerHTML = loading;
            try {
                data = await fetchJson<MetricRankedResponse>(
                    `/api/graph/metrics/${metric}?top=${top}`,
                );
                this.graph.rankData.set(key, data);
            } catch (e) {
                wrap.innerHTML = fail(e);
                return;
            }
        }
        if (!data.ranked.length) {
            wrap.innerHTML =
                `<p class="hint">No rows for ${escapeHtml(metric)} — run ` +
                `<span class="mono">make recompute-graph</span>.</p>`;
            return;
        }
        const max = Math.max(...data.ranked.map((r) => r.value), 0);
        const rows = data.ranked
            .map(
                (r, i) => `
            <tr>
                <td class="idx num">${i + 1}</td>
                <td><button type="button" class="rank-entity" data-centre="${escapeHtml(r.entity)}"
                            title="Centre the Lens on ${escapeHtml(r.entity)}">${escapeHtml(r.entity)}</button></td>
                <td class="num"><span class="score-bar" style="width:${max > 0 ? Math.max(2, Math.round((r.value / max) * 46)) : 0}px"></span>
                    ${this._fmtScore(r.value)}</td>
            </tr>`,
            )
            .join("");
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
                    "/api/graph/metrics/louvain_community",
                );
            } catch (e) {
                mount.innerHTML = `<p class="hint">unavailable — ${escapeHtml((e as Error).message)}</p>`;
                return;
            }
        }
        const groups = [...this.graph.rankGroups.groups]
            .sort((a, b) => b.size - a.size)
            .slice(0, 8);
        if (!groups.length) {
            mount.innerHTML = `<p class="hint">No communities — run <span class="mono">make recompute-graph</span>.</p>`;
            return;
        }
        mount.innerHTML =
            groups
                .map((g) => {
                    const color = COMMUNITY_PALETTE[g.label % COMMUNITY_PALETTE.length];
                    const members = g.members
                        .slice(0, 6)
                        .map(
                            (m) =>
                                `<button type="button" class="chip-entity" data-centre="${escapeHtml(m)}"
                         title="Centre the Lens on ${escapeHtml(m)}">${escapeHtml(m)}</button>`,
                        )
                        .join("");
                    const more =
                        g.members.length > 6
                            ? `<span class="chip-more">+${g.members.length - 6}</span>`
                            : "";
                    return `
            <div class="rank-group">
                <div class="rank-group-head">
                    <span class="swatch" style="background:${color}"></span>
                    <span class="mono">group ${g.label}</span>
                    <span class="cnt mono">${g.size}</span>
                </div>
                <div class="rank-group-members">${members}${more}</div>
            </div>`;
                })
                .join("") +
            (this.graph.rankGroups.modularity !== undefined
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
                    `/api/graph/suggestions?method=${method}&top=15&min_score=${minScore}`,
                );
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
        mount.innerHTML = rows
            .map(
                (r, i) => `
            <button type="button" class="suggest-row" data-centre="${escapeHtml(r.source)}"
                    title="${escapeHtml(r.source)} ↔ ${escapeHtml(r.target)}${r.edition ? ` · ${escapeHtml(r.edition)}` : ""} — centre on ${escapeHtml(r.source)}">
                <span class="idx mono">${i + 1}</span>
                <span class="suggest-pair">${escapeHtml(r.source)} <span class="arrow">↔</span> ${escapeHtml(r.target)}</span>
                <span class="suggest-bar"><span style="width:${((r.score / max) * 100).toFixed(1)}%"></span></span>
                <span class="val mono">${this._fmtScore(r.score)}</span>
            </button>`,
            )
            .join("");
        this._wireCentre(mount);
    }

    // --- Time mode (S4): temporal formation -------------------------------- //

    /** Load every Time panel except near-duplicates (explicit, on-demand). */
    private async _loadTimeView(): Promise<void> {
        if (!this.graph) return;
        this._setGraphStatus("Time — loading...");
        await Promise.allSettled([this._loadByYear(), this._loadBridges(), this._loadCoMentions()]);
    }

    /** Deal-activity-by-year stacked bars (M&A + JV edges per year). */
    private async _loadByYear(): Promise<void> {
        if (!this.graph) return;
        const mount = getEl("time-byyear");
        if (!this.graph.timeByYear) {
            mount.innerHTML = `<p class="hint"><i class="fas fa-spinner fa-spin"></i> loading…</p>`;
            try {
                this.graph.timeByYear = await fetchJson<EdgesByYearResponse>(
                    "/api/graph/edges-by-year",
                );
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
        getEl("time-legend").innerHTML = types
            .map(
                (t) =>
                    `<span class="legend-key"><span class="dot" style="background:${edgeColor(t)}"></span>${escapeHtml(t)}</span>`,
            )
            .join("");

        const byYear = new Map<string, { total: number; parts: YearEdgeCount[] }>();
        rows.forEach((r) => {
            const y = byYear.get(r.year) || { total: 0, parts: [] };
            y.total += r.count;
            y.parts.push(r);
            byYear.set(r.year, y);
        });
        const years = [...byYear.keys()].sort();
        const max = Math.max(...years.map((y) => byYear.get(y)!.total), 1);
        mount.innerHTML = years
            .map((y) => {
                const { total, parts } = byYear.get(y)!;
                const segs = parts
                    .map(
                        (p) =>
                            `<span class="bar-seg" style="width:${((p.count / total) * 100).toFixed(2)}%;background:${edgeColor(p.edge_type)}"
                       title="${escapeHtml(p.edge_type)}: ${p.count}"></span>`,
                    )
                    .join("");
                return `
            <div class="year-row">
                <span class="yr mono">${escapeHtml(y)}</span>
                <span class="bar-track" style="width:${((total / max) * 100).toFixed(1)}%">${segs}</span>
                <span class="cnt mono">${total}</span>
            </div>`;
            })
            .join("");
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
            .sort((a, b) => b.count - a.count)
            .slice(0, 12);
        if (!rows.length) {
            mount.innerHTML = `<p class="hint">No cross-sector M&A / JV edges yet.</p>`;
            return;
        }
        mount.innerHTML = rows
            .map(
                (b) => `
            <div class="bridge-row">
                <span class="edge-dot" style="background:${edgeColor(b.edge_type)}"
                      title="${escapeHtml(b.edge_type)}"></span>
                <span class="bridge-pair">${escapeHtml(b.sector_a)} <span class="arrow">↔</span> ${escapeHtml(b.sector_b)}</span>
                <span class="cnt mono">${b.count}</span>
            </div>`,
            )
            .join("");
    }

    /** Co-mention leaderboard (most-connected entities in prose). */
    private async _loadCoMentions(): Promise<void> {
        if (!this.graph) return;
        const mount = getEl("time-comentions");
        if (!this.graph.timeCoMentions) {
            mount.innerHTML = `<p class="hint"><i class="fas fa-spinner fa-spin"></i> loading…</p>`;
            try {
                this.graph.timeCoMentions = await fetchJson<CoMentionsResponse>(
                    "/api/graph/co-mentions?top=15",
                );
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
        mount.innerHTML = rows
            .map(
                (r) => `
            <div class="cm-row">
                <button type="button" class="rank-entity" data-centre="${escapeHtml(r.entity)}"
                        title="Centre the Lens on ${escapeHtml(r.entity)}">${escapeHtml(r.entity)}</button>
                <span class="cm-bar"><span style="width:${((r.co_mentions / max) * 100).toFixed(1)}%"></span></span>
                <span class="cnt mono">${r.co_mentions}</span>
            </div>`,
            )
            .join("");
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
                "/api/graph/near-duplicates?min_sim=0.9&limit=50",
            );
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
        mount.innerHTML = data.pairs
            .map(
                (p) => `
            <div class="neardup-row">
                <span class="sim mono">${p.similarity.toFixed(3)}</span>
                <a href="/entity/${encodeURI(p.path_a)}" target="_blank" rel="noopener">${escapeHtml(p.title_a || p.path_a)}</a>
                <span class="arrow">↔</span>
                <a href="/entity/${encodeURI(p.path_b)}" target="_blank" rel="noopener">${escapeHtml(p.title_b || p.path_b)}</a>
            </div>`,
            )
            .join("");
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
            const url = `/api/graph/neighbors/${encodeURIComponent(name)}` + (qs ? `?${qs}` : "");
            data = await fetchJson<NeighborsBundle>(url);
        } catch (e) {
            this._setGraphStatus(`Error: ${(e as Error).message}`);
            return;
        }
        if (!this.graph || !this.graph.renderer) return;

        const isSector = data.entity_type === "sector";
        const filter: GraphFilter = isSector
            ? "all"
            : ((getEl("graph-filter") as HTMLSelectElement).value as GraphFilter);
        const elements: GraphElement[] = isSector
            ? this._buildSectorEgoElements(data as SectorNeighbors)
            : this._bundleElements(data as CompanyNeighbors, filter, "focal");

        this.graph.renderer.setElements(elements);
        this.graph.central = isSector
            ? (data as SectorNeighbors).sector
            : (data as CompanyNeighbors).company;
        this.graph.elements = elements;
        this.graph.entityType = isSector ? "sector" : "company";

        this._runGraphLayout(
            (getEl("graph-layout") as HTMLSelectElement).value,
            false,
            true,
            this.graph.central,
        );
        this._fitCapped(40, _EGO_FIT_MAX_ZOOM, this.graph.central || undefined);
        this.graph.labelAlways = false;
        this._applyLabelBucket(-1); // ego labels are never zoom-gated

        // Emphasize the focal node; show it in the side panel.
        this.graph.renderer.setFocal(this.graph.central);
        this.graph.focalBundle = data;
        this._renderGraphDetail(this.graph.renderer.nodeAttrs(this.graph.central), data);

        const asOfSuffix = asOf ? ` · as of ${asOf}` : "";
        if (isSector) {
            const sectorBundle = data as SectorNeighbors;
            const capped = sectorBundle.member_count > _SECTOR_RENDER_CAP;
            this._setGraphStatus(
                `${sectorBundle.sector} — ${sectorBundle.member_count} member` +
                    (sectorBundle.member_count !== 1 ? "s" : "") +
                    (capped ? ` (rendering first ${_SECTOR_RENDER_CAP})` : "") +
                    asOfSuffix,
            );
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
                `${companyBundle.company} — ${total} relationship${total !== 1 ? "s" : ""}` +
                    (companyBundle.sector ? ` · ${companyBundle.sector}` : "") +
                    asOfSuffix,
            );
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
                    id: `${focal}__${m}`,
                    source: focal,
                    target: m,
                    type: "has_company",
                    label: "has",
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
            src: string,
            dst: string,
            type: string,
            label: string,
            props: Record<string, unknown> = {},
        ): void => {
            edges.push({
                data: {
                    id: `${src}__${dst}__${type}`,
                    source: src,
                    target: dst,
                    type,
                    label,
                    props,
                },
            });
        };

        if (focalGroup === "focal") {
            nodes.push({ data: { id: focal, label: focal, group: "focal", centrality: 10 } });
        }

        if (filter === "all" || filter === "peers") {
            data.peers.forEach((p) => {
                addNode(p, "peer");
                addEdge(focal, p, "competes_with", "peer");
            });
        }
        if (filter === "all" || filter === "jv") {
            data.jv_partners.forEach((j) => {
                addNode(j.partner, "jv");
                addEdge(focal, j.partner, "jv_with", "JV" + (j.venture ? `: ${j.venture}` : ""));
            });
        }
        if (filter === "all") {
            data.group_siblings.forEach((s) => {
                addNode(s, "sibling");
                addEdge(focal, s, "same_group", "same group");
            });
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
            data.suppliers.forEach((s) => {
                addNode(s, "supplier");
                addEdge(s, focal, "supplier_to", "supplies");
            });
            data.customers.forEach((c) => {
                addNode(c, "customer");
                addEdge(focal, c, "customer_of", "customer");
            });
        }
        if (filter === "all" && data.sector) {
            const sectorId = `sector:${data.sector}`;
            nodes.push({
                data: { id: sectorId, label: data.sector, group: "sector", centrality: 8 },
            });
            edges.push({
                data: {
                    id: `${focal}__${sectorId}`,
                    source: focal,
                    target: sectorId,
                    type: "part_of",
                    label: "part of",
                },
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
     * not-yet-present nodes/edges into the canvas as an "outer" ring,
     * keeping existing positions (the renderer seeds new nodes around the
     * anchor and relaxes with a short FA2 pass — the old fcose-without-
     * randomize role).
     */
    private async _expandNode(name: string): Promise<void> {
        const renderer = this.graph && this.graph.renderer;
        if (!renderer || !this.graph) return;
        if (renderer.nodeCount() >= _EXPAND_NODE_CAP) {
            this._setGraphStatus(
                `expansion cap (${_EXPAND_NODE_CAP} nodes) — centre on ${name} to continue there`,
            );
            return;
        }
        this._setGraphStatus(`Adding neighbours of ${name}...`);
        let data: NeighborsBundle;
        try {
            data = await fetchJson<NeighborsBundle>(
                `/api/graph/neighbors/${encodeURIComponent(name)}`,
            );
        } catch (e) {
            this._setGraphStatus(`Error: ${(e as Error).message}`);
            return;
        }
        const els =
            data.entity_type === "sector"
                ? this._buildSectorEgoElements(data as SectorNeighbors)
                : this._bundleElements(data as CompanyNeighbors, "all", "outer");
        els.forEach((el) => {
            if (!el.data.source && !renderer.hasNode(el.data.id)) el.data.group = "outer";
        });

        const added = renderer.mergeElements(els, name);
        this._fitCapped(40, _EGO_FIT_MAX_ZOOM); // include the merged outer ring in the view
        this._setGraphStatus(`+${added} nodes from ${name} · ${renderer.nodeCount()} on canvas`);
    }

    // --- Zoom, tooltips, zoom-fade labels ---------------------------------- //

    /** Wire the zoom slider / buttons / fit (buckets sync via camera events). */
    _initGraphZoom(): void {
        if (!this.graph || !this.graph.renderer) return;
        const renderer = this.graph.renderer;
        const slider = getEl("graph-zoom") as HTMLInputElement;
        const label = getEl("graph-zoom-label");

        const applyZoom = (): void => {
            const z = parseFloat(slider.value) || 1;
            renderer.setZoomValue(z);
            label.textContent = `${Math.round(z * 100)}%`;
        };
        slider.addEventListener("input", applyZoom);
        getEl("graph-zoom-in").addEventListener("click", () => renderer.zoomIn());
        getEl("graph-zoom-out").addEventListener("click", () => renderer.zoomOut());
        getEl("graph-zoom-fit").addEventListener("click", () =>
            renderer.fitCapped(30, _CLOUD_FIT_MAX_ZOOM),
        );
        this._syncZoomUi();
    }

    /** Slider + % label ← renderer camera (wheel / pinch / buttons / animation). */
    private _syncZoomUi(): void {
        const renderer = this.graph?.renderer;
        if (!renderer) return;
        const slider = getEl("graph-zoom") as HTMLInputElement;
        const label = getEl("graph-zoom-label");
        const z = renderer.zoomValue();
        slider.value = String(Math.min(3, Math.max(0.2, z)));
        label.textContent = `${Math.round(z * 100)}%`;
    }

    /** Camera moved: tooltip hides, slider re-syncs, buckets recompute. */
    private _onCameraChange(): void {
        this._hideTip();
        this._syncZoomUi();
        if (this.graph && this.graph.mode === "all" && !this.graph.labelAlways) {
            this._applyLabelBucket(this._labelBucketFor(this.graph.renderer?.zoomValue() || 1));
        }
    }

    /** Bucket for a zoom value in slider units (z = 1/camera-ratio). */
    private _labelBucketFor(z: number): number {
        if (z < 1 / _RATIO_LBL_OFF) return 0;
        if (z < 1 / _RATIO_LBL_HUBS) return 1;
        return 2;
    }

    /**
     * Apply a zoom-fade label bucket (cloud nodes only — ego labels are
     * always on; the reducer gates labels by the bucket + hub flag).
     * Bucket -1 clears the gate. Only refreshes on bucket CHANGE.
     */
    private _applyLabelBucket(bucket: number): void {
        if (!this.graph?.renderer) return;
        if (bucket === this.graph.labelBucket) return;
        this.graph.labelBucket = bucket;
        this.graph.renderer.setLabelBucket(bucket);
    }

    /** Tooltip placement + content (viewport-positioned, camera-relative). */
    private _placeTip(x: number, y: number): void {
        const tip = getEl("graph-tip");
        const canvas = getEl("graph-canvas");
        const maxX = canvas.clientWidth - 290;
        const maxY = canvas.clientHeight - 90;
        tip.style.left = `${Math.max(4, Math.min(x + 14, maxX))}px`;
        tip.style.top = `${Math.max(4, Math.min(y + 14, maxY))}px`;
        tip.style.display = "block";
    }

    private _showNodeTip(d: NodePayload, x: number, y: number): void {
        const rows: string[] = [];
        if (d.cloud) {
            rows.push(
                `<div class="tip-meta">degree ${String(d.deg ?? "?")} · ${escapeHtml(String(d.group || "entity"))}</div>`,
            );
        }
        const community = this.graph?.cloud?.communities?.get(d.id);
        if (community !== undefined) {
            rows.push(`<div class="tip-meta">community ${community}</div>`);
        }
        getEl("graph-tip").innerHTML =
            `<div class="tip-type">${escapeHtml(String(d.group || "node"))}</div>` +
            `<div class="tip-name">${escapeHtml(String(d.label || ""))}</div>` +
            rows.join("");
        this._placeTip(x, y);
    }

    private _showEdgeTip(d: EdgeHoverInfo, x: number, y: number): void {
        const props = (d.props || {}) as Record<string, unknown>;
        const extra = Object.keys(props)
            .map((k) => `${k}: ${String(props[k])}`)
            .join(" · ");
        getEl("graph-tip").innerHTML =
            `<div class="tip-type">${escapeHtml(String(d.rel || "edge"))}</div>` +
            `<div class="tip-name">${escapeHtml(d.source)} → ${escapeHtml(d.target)}</div>` +
            (extra ? `<div class="tip-meta">${escapeHtml(extra)}</div>` : "");
        this._placeTip(x, y);
    }

    private _hideTip(): void {
        const tip = document.getElementById("graph-tip");
        if (tip) tip.style.display = "none";
    }

    // --- Layouts ------------------------------------------------------------ //

    /** Run a layout from the #graph-layout dropdown (renderer owns the
     *  engines: FA2 for the force options, preset assignments otherwise;
     *  "cached" falls back to "components" when the sidecar is cold). */
    _runGraphLayout(
        name: string,
        cloud = false,
        randomize = true,
        root: string | null = null,
    ): void {
        if (!this.graph?.renderer) return;
        this.graph.renderer.runLayout(name, {
            cloud,
            randomize,
            cachedPositions: this.graph.cloud?.positions ?? null,
            root,
        });
        // Layouts may re-zoom via the subsequent fit; the camera event
        // handler re-syncs the slider, but a fit-free path (ego) won't fire
        // one — sync explicitly.
        this._syncZoomUi();
    }

    // --- Inspector (detail panel) ------------------------------------------- //

    _renderGraphDetail(nodeData: NodePayload | null, bundle?: NeighborsBundle | null): void {
        const panel = getEl("graph-detail");
        if (!nodeData) {
            panel.innerHTML =
                '<div class="graph-detail-empty"><i class="fas fa-hand-pointer"></i>' +
                "<p>Click a node to centre the graph on it.</p></div>";
            return;
        }
        const name = nodeData.id;
        const group = nodeData.group || "company";
        // The ego focal carries its neighbors bundle (state field); every
        // other node renders the centre/expand action row instead.
        const nb = bundle !== undefined ? bundle : (this.graph?.focalBundle ?? null);
        const isFocal =
            nb !== null &&
            (nb.entity_type === "sector"
                ? (nb as SectorNeighbors).sector === name
                : (nb as CompanyNeighbors).company === name);

        let html = `<div class="graph-detail-header">
            <span class="graph-badge graph-badge-${CSS.escape(group)}">${escapeHtml(group)}</span>
            <h3>${escapeHtml(name)}</h3>
        </div>`;

        if (nb && isFocal && nb.entity_type === "sector") {
            const sectorBundle = nb as SectorNeighbors;
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
        } else if (nb && isFocal) {
            const companyBundle = nb as CompanyNeighbors;
            html += `<ul class="graph-detail-list">`;
            if (companyBundle.sector)
                html += `<li><strong>Sector:</strong> ${escapeHtml(companyBundle.sector)}</li>`;
            if (companyBundle.subsidiary_of)
                html += `<li><strong>Parent:</strong> ${escapeHtml(companyBundle.subsidiary_of)}</li>`;
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

        if (!isFocal) {
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
            expandBtn.title =
                "Merge this node's neighbours into the canvas (progressive expansion)";
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
        const items = data.events
            .map((ev) => {
                const bits = [ev.counterparty, ev.magnitude]
                    .filter((x): x is string => Boolean(x))
                    .map((x) => escapeHtml(x))
                    .join(" · ");
                return `
            <li class="tl-item"${ev.source_quote ? ` title="${escapeHtml(ev.source_quote)}"` : ""}>
                <span class="tl-date mono">${this._eventDateLabel(ev)}</span>
                <span class="tl-type">${escapeHtml(ev.event_type)}</span>
                <span class="tl-body">${bits || "&nbsp;"}</span>
            </li>`;
            })
            .join("");
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
        const renderer = this.graph && this.graph.renderer;
        if (data.path === null) {
            result.innerHTML =
                `<p class="hint">No path found between <em>${escapeHtml(data.source)}</em> and ` +
                `<em>${escapeHtml(data.target)}</em> within the hop limit` +
                (this._asOf() ? ` as of ${this._asOf()}` : "") +
                `.</p>`;
            return;
        }
        const chain = data.path.map((p) => p.name);
        const hops = data.hops ?? 0;
        // Hop ribbon: chips are clickable — jumps to an ego view of that hop.
        const ribbon = chain
            .map(
                (n, i) =>
                    `<button type="button" class="hop-chip" data-hop-name="${escapeHtml(n)}" ` +
                    `title="Centre the Lens on ${escapeHtml(n)}">` +
                    `<span class="hop-idx">${i + 1}</span>${escapeHtml(n)}</button>`,
            )
            .join(`<span class="hop-arrow">→</span>`);
        result.innerHTML =
            `
            <div class="path-ribbon">${ribbon}</div>
            <p class="hint">${hops} hop${hops !== 1 ? "s" : ""}` +
            (this._asOf() ? ` · as of ${this._asOf()}` : " · now") +
            `</p>`;
        result.querySelectorAll<HTMLButtonElement>(".hop-chip").forEach((chip) => {
            chip.addEventListener("click", () => {
                const n = chip.dataset.hopName;
                if (!n) return;
                (getEl("graph-search") as HTMLInputElement).value = n;
                this._setMode("ego");
                this.loadEgoNetwork(n);
            });
        });

        if (!renderer) return;
        // Path mode renders the path as its own subgraph (a clean hop chain);
        // in ego mode we keep the old behaviour of highlighting the path when
        // it is fully present on the canvas.
        if (this.graph && this.graph.mode === "path") {
            const elements: GraphElement[] = chain.map((n, i) => ({
                data: {
                    id: n,
                    label: n,
                    group: i === 0 || i === chain.length - 1 ? "path-end" : "company",
                },
            }));
            for (let i = 0; i < chain.length - 1; i++) {
                elements.push({
                    data: {
                        id: `path__${chain[i]}__${chain[i + 1]}`,
                        source: chain[i],
                        target: chain[i + 1],
                        type: "path-hop",
                        label: String(i + 1),
                    },
                });
            }
            renderer.setElements(elements);
            this.graph.labelAlways = false;
            this._runGraphLayout("breadthfirst", false, true, chain[0]);
            this._fitCapped(60, _EGO_FIT_MAX_ZOOM);
            this._applyLabelBucket(-1);
            getEl("graph-empty").style.display = "none";
        } else {
            const pathNodes = chain.filter((n) => renderer.hasNode(n));
            if (pathNodes.length === chain.length) {
                renderer.highlightPath(chain);
            } else {
                renderer.highlightPath(null);
            }
        }
    }

    clearShortestPath(): void {
        getEl("shortest-result").innerHTML = "";
        (getEl("shortest-a") as HTMLInputElement).value = "";
        (getEl("shortest-b") as HTMLInputElement).value = "";
        this.graph?.renderer?.highlightPath(null);
    }
}
