// Graph renderer — the sigma.js v3 (WebGL) engine behind the Lens view.
//
// S1 of graph_rendering_overhaul.md: the Gate 1a/1b head-to-head picked
// sigma v3 + server-side positions (composition 1+3) — 59.9 FPS vs 1.2
// for cytoscape on the same GPU at 2x scale, 18 MB heap vs 417 MB — and
// the parity prototype priced the port at "not a rewrite". This module
// owns everything renderer-shaped so graph.ts keeps only the domain
// logic (modes, filters, panels, neighbor bundles):
//
//   - the graphology multigraph + Sigma instance + node/edge reducers
//     (the declarative counterpart of the old cytoscape stylesheet +
//     class toggles — visual state lives in fields, reducers read it)
//   - every layout engine behind the #graph-layout dropdown: FA2
//     replaces fcose/cose (same physics family, ~13x faster here);
//     cached/components/concentric/circle/grid/breadthfirst are
//     deterministic position assignments
//   - camera math in sigma's normalized space (fit/capped-fit/
//     spotlight) and the ratio↔% mapping the zoom slider reads
//   - interaction events surfaced as callbacks; async handlers are
//     promise-wrapped so failures reach the status line instead of
//     dying as unhandled rejections (Gate 1b cost item 4)
//
// Known visual deltas vs the cytoscape stylesheet, recorded in the
// proposal: shape variety is gone (circles for everything — rectangles
// for sectors etc. read through colour + label instead), and node font
// size is global (per-node 9px cloud / 12px focal becomes one 10px with
// sigma's size-threshold label gating).

import Graph from "graphology";
import Sigma from "sigma";
import type { EdgeDisplayData, NodeDisplayData } from "sigma/types";
import {
    drawDiscNodeHover,
    drawDiscNodeLabel,
    EdgeArrowProgram,
    EdgeLineProgram,
    NodeCircleProgram,
} from "sigma/rendering";
import { createNodeBorderProgram } from "@sigma/node-border";
import forceAtlas2 from "graphology-layout-forceatlas2";

// --------------------------------------------------------------------------- //
// Shared element + payload types (graph.ts builds these, the renderer draws)  //
// --------------------------------------------------------------------------- //

/**
 * Graph element (node or edge) as built by the ego-network/cloud/path
 * builders. Identical shape to the old cytoscape element inputs, so the
 * builders in graph.ts are untouched by the renderer swap.
 */
export interface GraphElement {
    data: {
        id: string;
        label: string;
        group?: string;
        centrality?: number;
        deg?: number;
        /** Cloud node diameter in px (sqrt visible-degree curve). */
        size?: number;
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

/** Node attributes as stored on the graphology graph (domain payload). */
export interface NodePayload {
    /** Node key (duplicated into attrs — tooltips/panels get payloads, not keys). */
    id: string;
    x: number;
    y: number;
    label: string | null;
    size: number;
    color: string;
    group?: string;
    cloud?: boolean;
    deg?: number;
    hub?: boolean;
    component?: string;
    centrality?: number;
    [k: string]: unknown;
}

/** Edge attributes as stored (rel = relationship type; `type` is the
 *  renderer's program key and must not be reused for domain data). */
export interface EdgePayload {
    label: string | null;
    rel?: string;
    cloud?: boolean;
    component?: string;
    props?: Record<string, unknown>;
    [k: string]: unknown;
}

/** What the view layer receives for tooltips / detail panels. */
export interface EdgeHoverInfo extends EdgePayload {
    source: string;
    target: string;
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

export const EDGE_COLORS: Record<string, string> = {};
(() => {
    const cs = getComputedStyle(document.documentElement);
    for (const [t, token] of Object.entries(_EDGE_TOKENS)) {
        const v = cs.getPropertyValue(token).trim();
        if (v) EDGE_COLORS[t] = v;
    }
})();

export const edgeColor = (t: string | undefined): string => (t && EDGE_COLORS[t]) || "#5C6E7E";

/** Louvain community hues (reducer picks these when shading is on). */
export const COMMUNITY_PALETTE = [
    "#E0A93E",
    "#2DD4BF",
    "#C39BFF",
    "#F5B14C",
    "#7CA8C9",
    "#F28B82",
    "#9BE08A",
    "#E79BE0",
    "#8AD7C6",
    "#D8C9A3",
];

/** Symmetric relationship types — plain lines, no arrowheads. */
const _SYMMETRIC_RELS = new Set(["co_mentioned_in", "jv_with", "competes_with", "same_group"]);

/** Group → (fill, radius px). Ported from the cytoscape stylesheet. */
const _NODE_GROUPS: Record<string, { color: string; size: number }> = {
    focal: { color: "#E0A93E", size: 16 },
    peer: { color: "#F5B14C", size: 12 },
    jv: { color: "#C39BFF", size: 12 },
    sibling: { color: "#B5838D", size: 12 },
    acquired: { color: "#F28B82", size: 12 },
    parent: { color: "#43AA8B", size: 12 },
    supplier: { color: "#7CA8C9", size: 12 },
    customer: { color: "#7CA8C9", size: 12 },
    outer: { color: "#66788C", size: 10 },
    company: { color: "#C7D3E0", size: 12 },
    theme: { color: "#C39BFF", size: 12 },
    edition: { color: "#D8C9A3", size: 15 },
    sector: { color: "#2DD4BF", size: 25 },
    "sector-focal": { color: "#1FB9A6", size: 28 },
    super_sector: { color: "#17766C", size: 27 },
    sub_sector: { color: "#3E8F86", size: 22 },
    member: { color: "#7CA8C9", size: 11 },
    "path-end": { color: "#E0A93E", size: 20 },
};
const _NODE_DEFAULT = { color: "#7E8FA3", size: 12 };

const _EDGE_BASE = "rgba(96, 116, 140, 0.75)";
const _CLOUD_EDGE = "rgba(96, 116, 140, 0.5)";

/** Deterministic scatter for cloud nodes missing from the server sidecar
 *  (added to the graph after the layout snapshot). FNV-1a over the id →
 *  angle + radius ring outside the main mass; stable across visits. */
function _scatterPosition(id: string): { x: number; y: number } {
    let h = 2166136261;
    for (let i = 0; i < id.length; i++) {
        h ^= id.charCodeAt(i);
        h = Math.imul(h, 16777619);
    }
    const angle = (((h >>> 0) % 3600) / 3600) * 2 * Math.PI;
    const radius = 1500 + ((h >>> 10) % 900);
    return { x: Math.round(Math.cos(angle) * radius), y: Math.round(Math.sin(angle) * radius) };
}

/** Re-alpha a #hex / rgba() colour (fade machinery for hover/set/path dim). */
function _withAlpha(color: string, alpha: number): string {
    const m = /^#([0-9a-f]{6})([0-9a-f]{2})?$/i.exec(color);
    if (m) {
        const r = parseInt(m[1].slice(0, 2), 16);
        const g = parseInt(m[1].slice(2, 4), 16);
        const b = parseInt(m[1].slice(4, 6), 16);
        return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }
    const m2 = /^rgba?\(([^)]+)\)$/i.exec(color);
    if (m2) {
        const parts = m2[1].split(",").map((s) => parseFloat(s));
        return `rgba(${parts[0]}, ${parts[1]}, ${parts[2]}, ${alpha})`;
    }
    return color;
}

/** Focal/hOVER emphasis ring: a bordered-disc program (labels + hover
 *  discs preserved — the border factory drops them unless re-passed). */
const _BorderProgram = createNodeBorderProgram({
    borders: [{ size: { value: 0.16 }, color: { attribute: "borderColor" } }],
    drawLabel: drawDiscNodeLabel,
    drawHover: drawDiscNodeHover,
});

// --------------------------------------------------------------------------- //
// Renderer                                                                    //
// --------------------------------------------------------------------------- //

export interface RendererCallbacks {
    /** Node tap — cloud: highlight + detail; ego: re-centre. Async-safe. */
    onNodeTap(id: string): void | Promise<void>;
    /** Tap on empty canvas (clears the cloud set highlight). */
    onStageTap(): void;
    onNodeHover(attrs: NodePayload, x: number, y: number): void;
    onNodeLeave(): void;
    onEdgeHover(attrs: EdgeHoverInfo, x: number, y: number): void;
    onEdgeLeave(): void;
    /** Camera moved (wheel/pan/slider/animation): sync slider + label buckets. */
    onCameraChange(): void;
    /** Async handler failures (status line, not an unhandled rejection). */
    onError(err: unknown): void;
}

export interface LayoutOptions {
    cloud?: boolean;
    randomize?: boolean;
    /** Server-side precomputed positions (the "cached" layout source). */
    cachedPositions?: Record<string, { x: number; y: number }> | null;
    /** BFS root for the breadthfirst layout (path/ego chains). */
    root?: string | null;
}

export class GraphRenderer {
    private graph: Graph;
    private sigma: Sigma;
    private container: HTMLElement;
    private callbacks: RendererCallbacks;

    // --- visual state (reducers read this; the cytoscape class system's
    //     declarative counterpart) ---------------------------------------- //
    private hoverNode: string | null = null;
    private hoverSet: { nodes: Set<string>; edges: Set<string> } | null = null;
    private component: string | null = null;
    private focal: string | null = null;
    private labelBucket = -1;
    private communities: Map<string, number> | null = null;
    private pathSet: { nodes: Set<string>; edges: Set<string> } | null = null;

    constructor(container: HTMLElement, callbacks: RendererCallbacks) {
        this.container = container;
        this.callbacks = callbacks;
        // E2E/diagnostic handle (tests read graph + camera state; harmless).
        (window as unknown as { __graphRenderer?: GraphRenderer }).__graphRenderer = this;
        this.graph = new Graph({ multi: true, type: "undirected" });
        this.sigma = new Sigma(this.graph, container, {
            nodeReducer: (node, data) => this._nodeReducer(node, data as NodePayload),
            edgeReducer: (edge, data) => this._edgeReducer(edge, data as EdgePayload),
            nodeProgramClasses: { circle: NodeCircleProgram, border: _BorderProgram },
            edgeProgramClasses: { arrow: EdgeArrowProgram, line: EdgeLineProgram },
            defaultNodeType: "circle",
            defaultEdgeType: "arrow",
            defaultNodeColor: _NODE_DEFAULT.color,
            defaultEdgeColor: _EDGE_BASE,
            // Camera bounds = the old cytoscape zoom range, inverted:
            // sigma ratio shrinks as you zoom in (1/ratio ↔ cytoscape zoom).
            minCameraRatio: 1 / 3,
            maxCameraRatio: 5,
            labelFont: "'IBM Plex Mono', monospace",
            labelSize: 10,
            labelWeight: "500",
            labelColor: { color: "#DCE5EE" },
            edgeLabelFont: "'IBM Plex Mono', monospace",
            edgeLabelSize: 8,
            edgeLabelWeight: "400",
            edgeLabelColor: { color: "#B9C6D4" },
            labelDensity: 1,
            labelGridCellSize: 90,
            labelRenderedSizeThreshold: 6,
            renderLabels: true,
            renderEdgeLabels: true,
            enableEdgeEvents: true, // enterEdge/leaveEdge → edge tooltips
            zIndex: true,
        });

        // Node tap. Sigma fires two clickNode events ahead of a double
        // click — handlers here are idempotent (set-selection, re-centre
        // is debounced in graph.ts), so no gesture discrimination needed.
        this.sigma.on("clickNode", ({ node }) => {
            this.callbacks.onNodeLeave();
            void Promise.resolve(this.callbacks.onNodeTap(node)).catch((e) =>
                this.callbacks.onError(e),
            );
        });
        this.sigma.on("clickStage", () => {
            this.callbacks.onNodeLeave();
            this.callbacks.onStageTap();
        });

        // Hover: neighbourhood dim + tooltip content via the view layer.
        this.sigma.on("enterNode", ({ node, event }) => {
            this.hoverNode = node;
            this._setHover(node);
            this.callbacks.onNodeHover(this.nodeAttrs(node), event.x, event.y);
            this.sigma.refresh();
        });
        this.sigma.on("leaveNode", () => {
            this.hoverNode = null;
            this._setHover(null);
            this.callbacks.onNodeLeave();
            this.sigma.refresh();
        });
        this.sigma.on("enterEdge", ({ edge, event }) => {
            const a = this.graph.getEdgeAttributes(edge) as EdgePayload;
            this.callbacks.onEdgeHover(
                {
                    ...a,
                    source: this.graph.source(edge),
                    target: this.graph.target(edge),
                },
                event.x,
                event.y,
            );
        });
        this.sigma.on("leaveEdge", () => this.callbacks.onEdgeLeave());

        // Camera moves (wheel/pan/animation/slider) drive the zoom slider
        // sync + label bucket recompute in the view layer.
        this.sigma.getCamera().on("updated", () => this.callbacks.onCameraChange());
    }

    // --- element sync ------------------------------------------------------ //

    /** Full rebuild: replace the canvas contents with `elements`. */
    setElements(elements: GraphElement[]): void {
        this.graph.clear();
        const nodes = elements.filter((el) => !el.data.source);
        for (const el of nodes) {
            const group = el.data.group || "";
            const style = _NODE_GROUPS[group] || _NODE_DEFAULT;
            const cloud = el.data.cloud === "1";
            this.graph.addNode(el.data.id, {
                id: el.data.id,
                label: el.data.label || el.data.id,
                group,
                cloud,
                deg: el.data.deg,
                hub: el.data.hub === "1",
                component: el.data.component,
                centrality: el.data.centrality,
                // Cloud sizes arrive as px diameters; sigma wants the radius.
                size: cloud && el.data.size ? el.data.size / 2 : style.size,
                color: style.color,
                x: 0,
                y: 0,
            } as NodePayload);
        }
        this._seedRing();
        this._addEdges(elements);
        this.sigma.refresh();
    }

    /**
     * Progressive expansion: merge only the not-yet-present nodes/edges as
     * an "outer" ring seeded around `anchor`, then a short FA2 relaxation
     * that keeps existing positions (the old fcose-randomize:false role).
     * Returns the number of nodes added.
     */
    mergeElements(elements: GraphElement[], anchor: string | null): number {
        const seed =
            anchor && this.graph.hasNode(anchor)
                ? this.graph.getNodeAttributes(anchor)
                : { x: 0, y: 0 };
        let added = 0;
        const fresh: GraphElement[] = [];
        elements.forEach((el) => {
            if (el.data.source || this.graph.hasNode(el.data.id)) return;
            fresh.push(el);
        });
        fresh.forEach((el, i) => {
            const a = (i / Math.max(1, fresh.length)) * 2 * Math.PI;
            const r = 140 + 8 * Math.sqrt(i);
            const group = el.data.group || "outer";
            const style = _NODE_GROUPS[group] || _NODE_GROUPS.outer;
            this.graph.addNode(el.data.id, {
                id: el.data.id,
                label: el.data.label || el.data.id,
                group,
                cloud: false,
                centrality: el.data.centrality,
                size: style.size,
                color: style.color,
                x: seed.x + Math.cos(a) * r,
                y: seed.y + Math.sin(a) * r,
            } as NodePayload);
            added++;
        });
        this._addEdges(elements);
        if (added) this._fa2(80, 10);
        this.sigma.refresh();
        return added;
    }

    /** Add edges whose endpoints exist; cloud edges inherit the source
     *  node's component so set-highlighting keeps whole components lit. */
    private _addEdges(elements: GraphElement[]): void {
        elements.forEach((el) => {
            const s = el.data.source;
            const t = el.data.target;
            if (!s || !t || !this.graph.hasNode(s) || !this.graph.hasNode(t)) return;
            if (this.graph.hasEdge(el.data.id)) return;
            this.graph.addEdgeWithKey(el.data.id, s, t, {
                rel: el.data.type,
                label: el.data.label || null,
                cloud: el.data.cloud === "1",
                props: el.data.props,
                component:
                    el.data.cloud === "1"
                        ? (this.graph.getNodeAttributes(s).component ?? undefined)
                        : undefined,
            } as EdgePayload);
        });
    }

    // --- visual state setters (each ends in a refresh) --------------------- //

    setFocal(id: string | null): void {
        this.focal = id;
        this.sigma.refresh();
    }

    /** -1 = ungated (ego); 0/1/2 = zoom-fade buckets (cloud). */
    setLabelBucket(bucket: number): void {
        if (bucket === this.labelBucket) return;
        this.labelBucket = bucket;
        this.sigma.setSetting("renderLabels", bucket !== 0);
        this.sigma.refresh();
    }

    setCommunities(map: Map<string, number> | null): void {
        this.communities = map;
        this.sigma.refresh();
    }

    /** Highlight the connected set `component` (null clears). */
    highlightComponent(component: string | null): void {
        this.component = component;
        this.sigma.refresh();
    }

    /** Highlight a shortest-path chain (null clears); edges between
     *  consecutive chain nodes light up, everything else fades. */
    highlightPath(chain: string[] | null): void {
        if (!chain) {
            this.pathSet = null;
            this.sigma.refresh();
            return;
        }
        const nodes = new Set(chain);
        const edges = new Set<string>();
        for (let i = 0; i < chain.length - 1; i++) {
            this.graph.edges(chain[i], chain[i + 1]).forEach((k) => edges.add(k));
        }
        this.pathSet = { nodes, edges };
        this.sigma.refresh();
    }

    // --- camera ------------------------------------------------------------- //

    /** Current zoom in "cytoscape" units (1/ratio) — the slider's currency. */
    zoomValue(): number {
        return 1 / this.sigma.getCamera().ratio;
    }

    setZoomValue(z: number): void {
        const cam = this.sigma.getCamera();
        cam.setState({ ...cam.getState(), ratio: Math.min(5, Math.max(1 / 3, 1 / z)) });
    }

    zoomIn(): void {
        void this.sigma.getCamera().animatedZoom({ factor: 1.25, duration: 180 });
    }

    zoomOut(): void {
        void this.sigma.getCamera().animatedUnzoom({ factor: 1.25, duration: 180 });
    }

    /**
     * Fit the whole graph into the viewport with `padding` px margin, then
     * floor the zoom-in at `maxZoom` (slider units: 1.3 = 130%). Sigma's
     * normalized camera keeps node sizes screen-constant at fit, so the old
     * cytoscape small-graph balloon problem (and its zoom cap machinery)
     * does not arise; the cap remains as a sanity bound.
     */
    fitCapped(padding = 30, maxZoom = 3, focusId?: string | null): void {
        if (!this.graph.order) return;
        const w = this.container.clientWidth || 800;
        const h = this.container.clientHeight || 600;
        const larger = Math.max(w, h);
        const fitRatio = larger / Math.max(1, larger - 2 * padding);
        const ratio = Math.max(fitRatio, 1 / maxZoom);
        const cam = this.sigma.getCamera();
        cam.setState({ ...cam.getState(), x: 0.5, y: 0.5, angle: 0, ratio });
        if (focusId && this.graph.hasNode(focusId)) {
            const pos = this.sigma.getNodeDisplayData(focusId);
            if (pos) cam.setState({ ...cam.getState(), x: pos.x, y: pos.y });
        }
    }

    /**
     * All-mode search spotlight: animate the camera onto `id`'s closed
     * neighbourhood. Returns the neighbourhood node count (status line).
     */
    spotlight(id: string): number {
        if (!this.graph.hasNode(id)) return 0;
        const nodes = new Set<string>([id, ...this.graph.neighbors(id)]);
        let minX = Infinity,
            minY = Infinity,
            maxX = -Infinity,
            maxY = -Infinity;
        nodes.forEach((n) => {
            const pos = this.sigma.getNodeDisplayData(n);
            if (!pos) return;
            minX = Math.min(minX, pos.x);
            minY = Math.min(minY, pos.y);
            maxX = Math.max(maxX, pos.x);
            maxY = Math.max(maxY, pos.y);
        });
        if (Number.isFinite(minX)) {
            const cam = this.sigma.getCamera();
            const span = Math.max(maxX - minX, maxY - minY) || 0.1;
            void cam.animate(
                {
                    ...cam.getState(),
                    x: (minX + maxX) / 2,
                    y: (minY + maxY) / 2,
                    ratio: Math.min(5, span * 1.3),
                },
                { duration: 280 },
            );
        }
        return nodes.size;
    }

    // --- queries ------------------------------------------------------------ //

    nodeCount(): number {
        return this.graph.order;
    }

    hasNode(id: string): boolean {
        return this.graph.hasNode(id);
    }

    nodeAttrs(id: string): NodePayload {
        return this.graph.getNodeAttributes(id) as NodePayload;
    }

    refresh(): void {
        this.sigma.refresh();
    }

    // --- reducers (the declarative stylesheet) ------------------------------ //

    private _nodeReducer(node: string, data: NodePayload): Partial<NodeDisplayData> {
        // borderColor feeds the border node program (not part of sigma's
        // DisplayData — program-specific attribute).
        const display: Partial<NodeDisplayData> & { borderColor?: string } = {
            x: data.x,
            y: data.y,
            label: data.label,
            size: data.size,
            color: data.color,
            type: "circle",
            zIndex: 0,
        };

        // Louvain shading overrides the group colour.
        if (this.communities) {
            const c = this.communities.get(node);
            if (c !== undefined) display.color = COMMUNITY_PALETTE[c % COMMUNITY_PALETTE.length];
        }

        // Zoom-fade label buckets (cloud nodes only; -1 = ungated).
        if (data.cloud && this.labelBucket === 1 && !data.hub) display.label = null;

        const faded =
            (this.hoverSet && !this.hoverSet.nodes.has(node)) ||
            (this.component !== null && data.component !== this.component) ||
            (this.pathSet && !this.pathSet.nodes.has(node));
        if (faded) {
            display.color = _withAlpha(display.color || _NODE_DEFAULT.color, 0.22);
            display.label = null;
        }

        // Emphasis rings, weakest → strongest: set membership, path, focal.
        if (this.component !== null && data.component === this.component) {
            display.type = "border";
            display.borderColor = "#E0A93E";
            display.zIndex = 6;
        }
        if (this.pathSet && this.pathSet.nodes.has(node)) {
            display.type = "border";
            display.borderColor = "#E0A93E";
            display.zIndex = 7;
        }
        if (this.focal === node) {
            display.type = "border";
            display.borderColor = "#F5D08C";
            display.size = Math.max(display.size || 0, 14) * 1.1;
            display.zIndex = 8;
        }

        // Hovered node: sigma's built-in halo disc + forced label.
        if (this.hoverNode === node) {
            display.highlighted = true;
            display.forceLabel = true;
        }
        return display;
    }

    private _edgeReducer(edge: string, data: EdgePayload): Partial<EdgeDisplayData> {
        const base = data.cloud ? _CLOUD_EDGE : edgeColor(data.rel);
        const display: Partial<EdgeDisplayData> = {
            label: data.cloud ? null : data.label || null,
            size: data.cloud ? 0.7 : 1.6,
            color: base,
            type: !data.cloud && data.rel && !_SYMMETRIC_RELS.has(data.rel) ? "arrow" : "line",
            zIndex: 0,
        };

        if (data.rel === "path-hop") {
            display.color = "#E0A93E";
            display.size = 3.5;
            display.zIndex = 10;
        }

        const faded =
            (this.hoverSet && !this.hoverSet.edges.has(edge)) ||
            (this.component !== null && data.component !== this.component) ||
            (this.pathSet && !this.pathSet.edges.has(edge));
        if (faded) display.color = _withAlpha(display.color || base, 0.15);

        if (this.pathSet && this.pathSet.edges.has(edge)) {
            display.color = "#E0A93E";
            display.size = 3;
            display.zIndex = 9;
        }
        if (this.component !== null && data.component === this.component) {
            display.color = _withAlpha(base, 0.9);
            display.size = 1.6;
        }
        return display;
    }

    /** Closed neighbourhood of `id` (nodes + internal edges) for hover dim. */
    private _setHover(id: string | null): void {
        if (!id) {
            this.hoverSet = null;
            return;
        }
        const nodes = new Set<string>([id]);
        this.graph.forEachNeighbor(id, (n) => nodes.add(n));
        const edges = new Set<string>();
        this.graph.forEachEdge((k, _attrs, s, t) => {
            if (nodes.has(s) && nodes.has(t)) edges.add(k);
        });
        this.hoverSet = { nodes, edges };
    }

    // --- layouts ------------------------------------------------------------ //

    /**
     * Run layout `name` over the current graph. "cached" falls through to
     * "components" when the sidecar is cold; anything unknown → FA2.
     */
    runLayout(name: string, opts: LayoutOptions = {}): void {
        if (!this.graph.order) return;
        const randomize = opts.randomize !== false;

        if (name === "cached") {
            const cached = opts.cachedPositions;
            if (cached) {
                this.graph.forEachNode((id) => {
                    const p = cached[id] ?? _scatterPosition(id);
                    this.graph.setNodeAttribute(id, "x", p.x);
                    this.graph.setNodeAttribute(id, "y", p.y);
                });
                this.sigma.refresh();
                return;
            }
            name = "components"; // sidecar cold → fast deterministic fallback
        }

        if (name === "components") {
            const positions = this._componentPositions();
            if (positions) {
                this.graph.forEachNode((id) => {
                    const p = positions[id];
                    if (p) {
                        this.graph.setNodeAttribute(id, "x", p.x);
                        this.graph.setNodeAttribute(id, "y", p.y);
                    }
                });
                this.sigma.refresh();
                return;
            }
            name = "concentric"; // no component attrs → cheap deterministic fallback
        }

        switch (name) {
            case "concentric":
                this._concentricPositions();
                break;
            case "circle":
                this._circlePositions();
                break;
            case "grid":
                this._gridPositions();
                break;
            case "breadthfirst":
                this._breadthFirstPositions(opts.root ?? null);
                break;
            case "cose":
                // The "fast force" option: fewer iterations, tighter pull.
                if (randomize) this._seedRing();
                this._fa2(opts.cloud ? 150 : 250, 6);
                break;
            default:
                // "fcose" — the quality force layout, now FA2 (same physics
                // family; ~13x faster than fcose was at cloud scale).
                if (randomize) this._seedRing();
                this._fa2(opts.cloud ? 600 : 500, 10);
                break;
        }
        this.sigma.refresh();
    }

    /** Deterministic circular seed + FNV jitter (FA2 needs a non-degenerate
     *  start; identical input → identical layout across visits). */
    private _seedRing(): void {
        const n = this.graph.order;
        if (!n) return;
        const radius = Math.max(60, Math.sqrt(n) * 40);
        let i = 0;
        this.graph.forEachNode((id) => {
            const base = (2 * Math.PI * i++) / n;
            let h = 2166136261;
            for (let c = 0; c < id.length; c++) {
                h ^= id.charCodeAt(c);
                h = Math.imul(h, 16777619);
            }
            const jitter = (((h >>> 0) % 1000) / 1000 - 0.5) * 0.04;
            this.graph.setNodeAttribute(id, "x", Math.cos(base + jitter) * radius);
            this.graph.setNodeAttribute(id, "y", Math.sin(base + jitter) * radius);
        });
    }

    private _fa2(iterations: number, scalingRatio: number): void {
        forceAtlas2.assign(this.graph, {
            iterations,
            settings: {
                barnesHutOptimize: this.graph.order > 500,
                barnesHutTheta: 0.5,
                gravity: 1,
                scalingRatio,
                slowDown: 2,
                adjustSizes: true,
                edgeWeightInfluence: 0,
                strongGravityMode: false,
                linLogMode: false,
                outboundAttractionDistribution: false,
            },
        });
    }

    /** Component-separating cloud layout: each connected set grid-packed
     *  with its hub at the cell centre (ported verbatim from graph.ts). */
    private _componentPositions(): Record<string, { x: number; y: number }> | null {
        const compOf = new Map<string, string>();
        let has = false;
        this.graph.forEachNode((id, a) => {
            const c = (a as NodePayload).component;
            compOf.set(id, c || id);
            if (c) has = true;
        });
        if (!has) return null;
        const degree: Record<string, number> = {};
        this.graph.forEachEdge((_k, _a, s, t) => {
            degree[s] = (degree[s] || 0) + 1;
            degree[t] = (degree[t] || 0) + 1;
        });
        const compMap = new Map<string, string[]>();
        this.graph.forEachNode((id) => {
            const c = compOf.get(id) as string;
            if (!compMap.has(c)) compMap.set(c, []);
            (compMap.get(c) as string[]).push(id);
        });
        const comps = [...compMap.values()].sort((a, b) => b.length - a.length);
        const nodeSpacing = 52;
        const maxComp = Math.max(...comps.map((c) => c.length));
        const cellPad = maxComp >= 8 ? 90 : 70;
        const cellRadius = (n: number): number => Math.max(30, (Math.sqrt(n) * nodeSpacing) / 2);
        const maxR = Math.max(...comps.map((c) => cellRadius(c.length)));
        const cell = maxR * 2 + cellPad;
        const cols = Math.max(1, Math.ceil(Math.sqrt(comps.length)));
        const positions: Record<string, { x: number; y: number }> = {};
        comps.forEach((comp, i) => {
            const cx = (i % cols) * cell + cell / 2;
            const cyy = Math.floor(i / cols) * cell + cell / 2;
            const r = cellRadius(comp.length);
            const sorted = [...comp].sort((a, b) => (degree[b] || 0) - (degree[a] || 0));
            const hub = sorted[0];
            if (comp.length === 2) {
                positions[hub] = { x: cx, y: cyy - r };
                positions[sorted[1]] = { x: cx, y: cyy + r };
                return;
            }
            positions[hub] = { x: cx, y: cyy };
            sorted.slice(1).forEach((id, j) => {
                const ang = -Math.PI / 2 + (j / (sorted.length - 1)) * Math.PI * 2;
                positions[id] = { x: cx + Math.cos(ang) * r, y: cyy + Math.sin(ang) * r };
            });
        });
        return positions;
    }

    /** Concentric rings by centrality (one ring per distinct value). */
    private _concentricPositions(): void {
        const nodes = this.graph.nodes();
        const ranked = [...nodes].sort(
            (a, b) =>
                ((this.graph.getNodeAttributes(b) as NodePayload).centrality ?? 0) -
                ((this.graph.getNodeAttributes(a) as NodePayload).centrality ?? 0),
        );
        const levels: { value: number; members: string[] }[] = [];
        ranked.forEach((id) => {
            const v = (this.graph.getNodeAttributes(id) as NodePayload).centrality ?? 0;
            const last = levels[levels.length - 1];
            if (last && last.value === v) last.members.push(id);
            else levels.push({ value: v, members: [id] });
        });
        levels.forEach((level, li) => {
            const r = 70 + li * 90;
            level.members.forEach((id, i) => {
                const a = (2 * Math.PI * i) / level.members.length + li * 0.5;
                this.graph.setNodeAttribute(id, "x", Math.cos(a) * r);
                this.graph.setNodeAttribute(id, "y", Math.sin(a) * r);
            });
        });
    }

    /** Single ring in insertion order. */
    private _circlePositions(): void {
        const nodes = this.graph.nodes();
        const r = 40 + 10 * Math.sqrt(nodes.length);
        nodes.forEach((id, i) => {
            const a = (2 * Math.PI * i) / nodes.length;
            this.graph.setNodeAttribute(id, "x", Math.cos(a) * r);
            this.graph.setNodeAttribute(id, "y", Math.sin(a) * r);
        });
    }

    /** Deterministic grid, id-sorted. */
    private _gridPositions(): void {
        const nodes = this.graph.nodes().sort();
        const cols = Math.max(1, Math.ceil(Math.sqrt(nodes.length)));
        nodes.forEach((id, i) => {
            this.graph.setNodeAttribute(id, "x", (i % cols) * 80);
            this.graph.setNodeAttribute(id, "y", Math.floor(i / cols) * 80);
        });
    }

    /** BFS layering from `root` (path chains lay out as a straight line). */
    private _breadthFirstPositions(root: string | null): void {
        const start = root && this.graph.hasNode(root) ? root : this.graph.nodes()[0];
        const seen = new Set<string>([start]);
        const layers: string[][] = [[start]];
        let frontier = [start];
        while (frontier.length) {
            const next: string[] = [];
            frontier.forEach((n) => {
                this.graph.forEachNeighbor(n, (m) => {
                    if (!seen.has(m)) {
                        seen.add(m);
                        next.push(m);
                    }
                });
            });
            if (next.length) layers.push(next);
            frontier = next;
        }
        // Unreached nodes (shouldn't happen on chains) stack on the last row.
        this.graph.forEachNode((id) => {
            if (!seen.has(id)) {
                seen.add(id);
                (layers[layers.length - 1] as string[]).push(id);
            }
        });
        layers.forEach((layer, li) => {
            layer.forEach((id, i) => {
                this.graph.setNodeAttribute(id, "x", (i - (layer.length - 1) / 2) * 130);
                this.graph.setNodeAttribute(id, "y", li * 140);
            });
        });
    }
}
