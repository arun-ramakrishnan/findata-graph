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
    | CompanyNeighbors
    | SectorNeighbors
    | SuperSectorNeighbors
    | SubSectorNeighbors
    | ThemeNeighbors;

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
