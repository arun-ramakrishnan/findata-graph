/**
 * TypeScript response shapes for the Flask `/api/*` endpoints consumed by the
 * findata frontend.
 *
 * These are hand-written from the verbatim `jsonify({...})` blocks in
 * `app.py` (there is no OpenAPI/swagger contract to generate from). Each
 * interface mirrors the exact keys the backend returns — keys are NOT
 * invented. When the backend changes a shape, update the matching interface
 * here and `tsc` will flag every frontend call site that breaks: that
 * shape-drift catch is the whole point of this file.
 *
 * Conventions mirrored from the backend:
 *   - Every `/api/*` error body is `{ "error": string }` (see `ErrorResponse`).
 *   - Many scalar fields are `string | null` (SQLite NULLs surfacing as JSON null).
 */

/** Uniform error envelope for every `/api/*` route (4xx/5xx). */
export interface ErrorResponse {
    error: string;
}

// --------------------------------------------------------------------------- //
// GET /api/sectors                                                            //
// --------------------------------------------------------------------------- //
/** One row of `sector_entities` (a sector note + its parsed frontmatter/body). */
export interface SectorEntity {
    name: string;
    file_path: string;
    /** Parsed YAML frontmatter; `{}` if the note failed to parse. */
    frontmatter: Record<string, unknown>;
    /** Markdown body; `"Error reading file"` on read failure. */
    content: string;
}

/** One super-sector with its child sector names. Empty list when none exist. */
export interface SuperSector {
    name: string;
    sectors: string[];
}

export interface SectorsResponse {
    classifications: string[];
    sector_entities: SectorEntity[];
    super_sectors: SuperSector[];
}

// --------------------------------------------------------------------------- //
// GET /api/stats                                                              //
// --------------------------------------------------------------------------- //
export interface StatsResponse {
    /** entity_type → count. */
    entity_counts: Record<string, number>;
    /** sector_classification → count (top 10). */
    top_sectors: Record<string, number>;
    /** market_cap bucket → count. */
    market_cap_counts: Record<string, number>;
    total_entities: number;
}

// --------------------------------------------------------------------------- //
// GET /api/entities (list) — note: distinct shape from the detail endpoint.   //
// --------------------------------------------------------------------------- //
export interface EntityListItem {
    name: string;
    entity_type: string;
    sector_classification: string | null;
    market_cap: string | null;
    /** Sorted in the list endpoint (unsorted in the detail endpoint). */
    enhanced_tags: string[];
    file_path: string | null;
}

export interface EntitiesResponse {
    entities: EntityListItem[];
    total_count: number;
    limit: number;
    offset: number;
}

// --------------------------------------------------------------------------- //
// GET /api/entity/<path> (detail)                                             //
// --------------------------------------------------------------------------- //
/**
 * The detail endpoint extends the list item but conditionally adds note
 * content. `enhanced_tags` here is unsorted and merged with the YAML `tags`
 * list; `frontmatter`/`content`/`raw_content` are present only when the note
 * file was readable.
 */
export interface EntityDetail extends EntityListItem {
    frontmatter?: Record<string, unknown>;
    content?: string;
    raw_content?: string;
}

// --------------------------------------------------------------------------- //
// GET /api/search (FTS5 over note bodies + newsletters)                       //
// --------------------------------------------------------------------------- //
export interface SearchResult {
    doc_type: string;
    file_path: string;
    title: string | null;
    sector: string | null;
    /** FTS5 snippet with literal `<mark>...</mark>` around matches. */
    snippet: string;
    /**
     * Cosine similarity vs the query embedding, when `hybrid=true` was
     * requested and the index carries embeddings; otherwise null.
     */
    similarity: number | null;
}

export interface SearchResponse {
    results: SearchResult[];
    total_count: number;
    limit: number;
    offset: number;
}

// --------------------------------------------------------------------------- //
// POST /api/graph/refresh                                                     //
// --------------------------------------------------------------------------- //
export interface GraphRefreshResponse {
    status: "ok" | "error";
    message: string;
}

// --------------------------------------------------------------------------- //
// GET /api/graph/neighbors/<name> — a tagged union on `entity_type`.          //
// --------------------------------------------------------------------------- //
// The ONLY key common to every branch is `entity_type`; the focal-name key
// (`company` / `sector` / `super_sector` / `sub_sector` / `theme`) and the
// members/children payload differ per branch. Discriminate on `entity_type`.

export interface CompanyNeighbors {
    entity_type: "company";
    company: string;
    as_of: string | null;
    file_path: string | null;
    sector: string | null;
    peers: string[];
    jv_partners: { partner: string; venture: string }[];
    group_siblings: string[];
    acquired: { name: string; year: string | number }[];
    subsidiary_of: string | null;
    suppliers: string[];
    customers: string[];
    /** E3: embedding cosine neighbours (weight 0.5, symmetric). Present when
     *  the neighbors bundle includes semantic_peer edges; absent on older
     *  endpoints. */
    semantic_peers?: string[];
    /** E5: institution → company holder edges (directed). Map institution →
     *  properties {pctHeld, shares, reported}. */
    invested_by?: { institution: string; pctHeld?: number; shares?: number }[];
}

export interface SectorNeighbors {
    entity_type: "sector";
    sector: string;
    file_path: string | null;
    members: string[];
    member_count: number;
    market_cap_counts: Record<string, number>;
}

export interface SuperSectorNeighbors {
    entity_type: "super_sector";
    super_sector: string;
    file_path: string | null;
    sectors: string[];
    sector_count: number;
}

export interface SubSectorNeighbors {
    entity_type: "sub_sector";
    sub_sector: string;
    parent_sector: string | null;
}

export interface ThemeNeighbors {
    entity_type: "theme";
    theme: string;
    file_path: string | null;
    members: string[];
    member_count: number;
}

export type NeighborsBundle =
    CompanyNeighbors | SectorNeighbors | SuperSectorNeighbors | SubSectorNeighbors | ThemeNeighbors;

/**
 * The graph view (loadEgoNetwork) only ever centres on a company or a sector
 * (the typeahead in `loadGraphEntityList` loads companies + sectors only, and
 * `_renderGraphDetail` only handles company/sector bundles). So the consumer
 * narrows to this two-arm union.
 */
export type GraphEgoBundle = CompanyNeighbors | SectorNeighbors;

// --------------------------------------------------------------------------- //
// GET /api/graph/shortest                                                     //
// --------------------------------------------------------------------------- //
/** One node on a shortest path. `hop` is the 1-based step from `source`. */
export interface ShortestHop {
    name: string;
    hop: number;
}

export interface ShortestPathResponse {
    source: string;
    target: string;
    /** `null` when no path was found within the hop limit. */
    path: ShortestHop[] | null;
    /** `null` iff `path` is `null`. */
    hops: number | null;
    as_of: string | null;
}

// --------------------------------------------------------------------------- //
// GET /api/events/<name> (D7 — temporal spine)                                //
// --------------------------------------------------------------------------- //
export interface EventItem {
    event_type: string;
    event_date: string | null;
    period: string | null;
    date_precision: string | null;
    magnitude: string | null;
    counterparty: string | null;
    source_quote: string | null;
    as_of_edition: string | null;
}

export interface EventsResponse {
    entity: string;
    entity_type: string;
    file_path: string | null;
    event_count: number;
    events: EventItem[];
}

// --------------------------------------------------------------------------- //
// GET /api/docs, /api/docs/content, /api/docs/search (doc/ corpus browser)    //
// --------------------------------------------------------------------------- //
/** One entry in the doc/ catalog. `section` is the subdir relative to doc/
 * ("" for top-level, e.g. "improvements", "improvements/archive"). */
export interface DocItem {
    path: string;
    name: string;
    section: string;
    title: string;
    size_bytes: number;
    mtime: number;
}

export interface DocsResponse {
    docs: DocItem[];
}

/** Raw markdown/plain-text body of one doc, served for client-side rendering. */
export interface DocContentResponse {
    path: string;
    name: string;
    section: string;
    title: string;
    content: string;
    size_bytes: number;
    mtime: number;
}

/** A search hit. `snippet` carries literal `<mark>...</mark>` around matches
 * (mirrors the FTS5 /api/search convention — reuse highlightSnippet()).
 * `section_title`/`anchor` are set on the index path (one row per `##`
 * section, anchor = 1-based header line); the scan fallback carries
 * `section_title: ""` and `anchor: null`. */
export interface DocSearchHit {
    path: string;
    name: string;
    section: string;
    title: string;
    section_title: string;
    anchor: number | null;
    snippet: string;
    score: number;
    similarity?: number | null;
}

export interface DocSearchResponse {
    query: string;
    mode: "hybrid" | "bm25" | "scan";
    stale: boolean;
    results: DocSearchHit[];
}

// --------------------------------------------------------------------------- //
// GET /api/graph/cloud (whole-graph force cloud)                              //
// --------------------------------------------------------------------------- //
/** One entity rendered in the graph cloud. `entity_type` colours the node
 * (company vs sector vs theme etc.). */
export interface GraphCloudNode {
    id: string;
    label: string;
    entity_type: string;
}

/** One typed edge in the graph cloud. */
export interface GraphCloudEdge {
    source: string;
    target: string;
    edge_type: string;
}

/** Relationship-type summary for the cloud card: count + direction flag +
 * human-readable semantics (mirrors the graph_design.txt edge-type table). */
export interface RelationshipTypeSummary {
    edge_type: string;
    count: number;
    symmetric: boolean;
    semantics: string;
}

export interface GraphCloudResponse {
    nodes: GraphCloudNode[];
    edges: GraphCloudEdge[];
    relationship_types: RelationshipTypeSummary[];
    total_nodes: number;
    total_edges: number;
}

// --------------------------------------------------------------------------- //
// GET /api/graph/stats (graph stats block for the Statistics view)            //
// --------------------------------------------------------------------------- //
/** Whole-graph structural metrics via Onager (null when unavailable). */
export interface GraphStructure {
    density: number | null;
    diameter: number | null;
    radius: number | null;
    avg_path_length: number | null;
    transitivity: number | null;
    triangles: number | null;
    avg_clustering: number | null;
    assortativity: number | null;
}

export interface GraphStatsResponse {
    /** null when the Onager/DuckDB layer is unavailable (degradable). */
    structure: GraphStructure | null;
    entities: {
        total: number;
        by_type: Record<string, number>;
    };
    edges: {
        total: number;
        by_type: Record<string, number>;
    };
    sectors: {
        count: number;
        top: { sector: string; n: number }[];
        size_distribution: { min: number; max: number; mean: number };
    };
    hygiene: {
        orphan_companies: number;
        no_ticker: number;
        self_loops: number;
        orphan_edges: number;
        conflicting_market_cap: number;
    };
    staleness: {
        stale: boolean;
        most_recent_entity_update: string | null;
        most_recent_analytics_compute: string | null;
    };
}

/** GET /api/graph/metrics/<label-metric> — louvain / wcc groups (S3 shading). */
export interface MetricGroup {
    label: number;
    size: number;
    members: string[];
}

export interface MetricGroupsResponse {
    metric: string;
    total: number;
    groups: MetricGroup[];
    modularity?: number;
}

// --------------------------------------------------------------------------- //
// GET /api/graph/metrics/<metric> — S4 Rank mode (scalar + payload shapes).   //
// --------------------------------------------------------------------------- //
/**
 * Scalar-metric ranking (pagerank, degree/betweenness/closeness/eigenvector/
 * harmonic/katz/laplacian/local_reaching centrality, local clustering
 * coefficient). `top=` limits the rows server-side.
 */
export interface MetricRankedRow {
    entity: string;
    value: number;
}

export interface MetricRankedResponse {
    metric: string;
    total: number;
    ranked: MetricRankedRow[];
}

/** voterank — one shared ordered seed list (every row carries the same list). */
export interface MetricSeedsResponse {
    metric: string;
    total: number;
    seeds: string[];
}

/** link_prediction — per-entity candidate lists, best score first. */
export interface LinkPredictionCandidate {
    name: string;
    score: number;
}

export interface LinkPredictionEntity {
    entity: string;
    method: string;
    edge_types: string[];
    best_score: number;
    candidates: LinkPredictionCandidate[];
}

export interface LinkPredictionResponse {
    metric: string;
    total: number;
    entities: LinkPredictionEntity[];
}

// --------------------------------------------------------------------------- //
// GET /api/graph/suggestions (S4 Rank mode; read-only, sidecar untouched).    //
// --------------------------------------------------------------------------- //
export interface SuggestionRow {
    source: string;
    target: string;
    score: number;
    /** Edition the prediction is scoped to, when the method reports one. */
    edition: string | null;
}

export interface SuggestionsResponse {
    method: string;
    top: number;
    suggestions: SuggestionRow[];
}

// --------------------------------------------------------------------------- //
// GET /api/graph/near-duplicates (S4 Time mode; on-demand only, ~1s).        //
// --------------------------------------------------------------------------- //
export interface NearDuplicatePair {
    path_a: string;
    path_b: string;
    title_a: string;
    title_b: string;
    similarity: number;
}

export interface NearDuplicatesResponse {
    doc_type: string;
    min_sim: number;
    pairs: NearDuplicatePair[];
}

// --------------------------------------------------------------------------- //
// GET /api/graph/co-mentions · /bridges · /edges-by-year (S4 Time mode).     //
// --------------------------------------------------------------------------- //
export interface CoMentionRow {
    entity: string;
    co_mentions: number;
}

export interface CoMentionsResponse {
    ranked: CoMentionRow[];
}

export interface SectorBridge {
    edge_type: string;
    sector_a: string;
    sector_b: string;
    count: number;
}

export interface BridgesResponse {
    bridges: SectorBridge[];
}

export interface YearEdgeCount {
    year: string;
    edge_type: string;
    count: number;
}

export interface EdgesByYearResponse {
    timeline: YearEdgeCount[];
}

// --------------------------------------------------------------------------- //
// S5 — Reading Room: /api/entities, /api/search, /api/entity, similar rails   //
// --------------------------------------------------------------------------- //

/** One vault entity row (GET /api/entities). `file_path` is repo-relative
 * (`findata/...`) and null for note-less entities (sub_sectors, themes). */
export interface VaultEntity {
    name: string;
    entity_type: string;
    sector_classification: string | null;
    market_cap: string | null;
    enhanced_tags: string[];
    file_path: string | null;
}

export interface EntitiesResponse {
    entities: VaultEntity[];
    total_count: number;
    limit: number;
    offset: number;
}

/** Parsed YAML frontmatter (GET /api/entity/<path>). Values are scalars,
 * string lists (tags) or one nested block (generated: {by, at}) — a loose
 * record by design (a type alias, not an interface, so the TS-contract
 * parser doesn't treat it as an endpoint shape). */
export type NoteFrontmatter = Record<string, unknown>;

/** Full vault-note payload (GET /api/entity/<path>): `content` has the
 * frontmatter block stripped; `raw_content` is the untouched file. */
export interface EntityDetailResponse {
    name: string;
    entity_type: string;
    sector_classification: string | null;
    market_cap: string | null;
    enhanced_tags: string[];
    file_path: string | null;
    frontmatter: NoteFrontmatter;
    content: string;
    raw_content: string;
}

/** Shared shape of /api/graph/similar neighbors and edition companies. */
export interface SimilarNeighbor {
    file_path: string;
    title: string;
    similarity: number;
}

export interface SimilarNotesResponse {
    note: string;
    k: number;
    doc_type: string | null;
    neighbors: SimilarNeighbor[];
}

export interface EditionCompaniesResponse {
    edition: string;
    k: number;
    companies: SimilarNeighbor[];
}

// --------------------------------------------------------------------------- //
// S6 — entity pages: /api/graph/semantic (VSS peers)                          //
// --------------------------------------------------------------------------- //

/** One embedding-space neighbour (GET /api/graph/semantic/<name>). */
export interface SemanticNeighbor {
    name: string;
    sector: string | null;
    similarity: number;
}

export interface SemanticResponse {
    company: string;
    k: number;
    metric: string;
    cross_sector: boolean;
    neighbors: SemanticNeighbor[];
}
