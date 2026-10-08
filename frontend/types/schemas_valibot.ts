// GENERATED FILE — do not edit by hand.
// Source: frontend/types/api.ts  (via tests/api_contract.py)
// Regenerate: python3 helpers/misc/gen_api_guards.py
// Verify:     python3 helpers/misc/gen_api_guards.py --check
//
// One valibot schema per api.ts interface, dependency order. `guards.ts`
// wraps these into the is<Interface> functions fetchJson call sites pass;
// bundles tree-shake to exactly the schemas their views consume.
// Unknown type constructs raise in the generator — never emitted as a
// loose schema.
import * as v from "valibot";

export const ErrorResponseSchema = v.object({ error: v.string() });

export const SectorEntitySchema = v.object({
    name: v.string(),
    file_path: v.string(),
    frontmatter: v.record(v.string(), v.unknown()),
    content: v.string(),
});

export const SuperSectorSchema = v.object({ name: v.string(), sectors: v.array(v.string()) });

export const SectorsResponseSchema = v.object({
    classifications: v.array(v.string()),
    sector_entities: v.array(SectorEntitySchema),
    super_sectors: v.array(SuperSectorSchema),
});

export const StatsResponseSchema = v.object({
    entity_counts: v.record(v.string(), v.number()),
    top_sectors: v.record(v.string(), v.number()),
    market_cap_counts: v.record(v.string(), v.number()),
    total_entities: v.number(),
});

export const EntityListItemSchema = v.object({
    name: v.string(),
    entity_type: v.string(),
    sector_classification: v.nullable(v.string()),
    market_cap: v.nullable(v.string()),
    enhanced_tags: v.array(v.string()),
    file_path: v.nullable(v.string()),
});

export const EntityDetailSchema = v.object({
    frontmatter: v.optional(v.record(v.string(), v.unknown())),
    content: v.optional(v.string()),
    raw_content: v.optional(v.string()),
    name: v.string(),
    entity_type: v.string(),
    sector_classification: v.nullable(v.string()),
    market_cap: v.nullable(v.string()),
    enhanced_tags: v.array(v.string()),
    file_path: v.nullable(v.string()),
});

export const SearchResultSchema = v.object({
    doc_type: v.string(),
    file_path: v.string(),
    title: v.nullable(v.string()),
    sector: v.nullable(v.string()),
    section_title: v.nullable(v.string()),
    snippet: v.string(),
    similarity: v.nullable(v.number()),
});

export const SearchResponseSchema = v.object({
    results: v.array(SearchResultSchema),
    total_count: v.number(),
    limit: v.number(),
    offset: v.number(),
});

export const GraphRefreshResponseSchema = v.object({
    status: v.picklist(["ok", "error"]),
    message: v.string(),
});

export const CompanyNeighborsSchema = v.object({
    entity_type: v.literal("company"),
    company: v.string(),
    as_of: v.nullable(v.string()),
    file_path: v.nullable(v.string()),
    sector: v.nullable(v.string()),
    peers: v.array(v.string()),
    jv_partners: v.array(v.object({ partner: v.string(), venture: v.string() })),
    group_siblings: v.array(v.string()),
    acquired: v.array(v.object({ name: v.string(), year: v.union([v.string(), v.number()]) })),
    subsidiary_of: v.nullable(v.string()),
    suppliers: v.array(v.string()),
    customers: v.array(v.string()),
    semantic_peers: v.optional(v.array(v.string())),
    invested_by: v.optional(
        v.array(
            v.object({
                institution: v.string(),
                pctHeld: v.optional(v.number()),
                shares: v.optional(v.number()),
            }),
        ),
    ),
});

export const SectorNeighborsSchema = v.object({
    entity_type: v.literal("sector"),
    sector: v.string(),
    file_path: v.nullable(v.string()),
    members: v.array(v.string()),
    member_count: v.number(),
    market_cap_counts: v.record(v.string(), v.number()),
});

export const SuperSectorNeighborsSchema = v.object({
    entity_type: v.literal("super_sector"),
    super_sector: v.string(),
    file_path: v.nullable(v.string()),
    sectors: v.array(v.string()),
    sector_count: v.number(),
});

export const SubSectorNeighborsSchema = v.object({
    entity_type: v.literal("sub_sector"),
    sub_sector: v.string(),
    parent_sector: v.nullable(v.string()),
});

export const ThemeNeighborsSchema = v.object({
    entity_type: v.literal("theme"),
    theme: v.string(),
    file_path: v.nullable(v.string()),
    members: v.array(v.string()),
    member_count: v.number(),
});

export const ShortestHopSchema = v.object({ name: v.string(), hop: v.number() });

export const ShortestPathResponseSchema = v.object({
    source: v.string(),
    target: v.string(),
    path: v.nullable(v.array(ShortestHopSchema)),
    hops: v.nullable(v.number()),
    as_of: v.nullable(v.string()),
});

export const EventItemSchema = v.object({
    event_type: v.string(),
    event_date: v.nullable(v.string()),
    period: v.nullable(v.string()),
    date_precision: v.nullable(v.string()),
    magnitude: v.nullable(v.string()),
    counterparty: v.nullable(v.string()),
    source_quote: v.nullable(v.string()),
    as_of_edition: v.nullable(v.string()),
});

export const EventsResponseSchema = v.object({
    entity: v.string(),
    entity_type: v.string(),
    file_path: v.nullable(v.string()),
    event_count: v.number(),
    events: v.array(EventItemSchema),
});

export const DocItemSchema = v.object({
    path: v.string(),
    name: v.string(),
    section: v.string(),
    title: v.string(),
    size_bytes: v.number(),
    mtime: v.number(),
});

export const DocsResponseSchema = v.object({ docs: v.array(DocItemSchema) });

export const DocContentResponseSchema = v.object({
    path: v.string(),
    name: v.string(),
    section: v.string(),
    title: v.string(),
    content: v.string(),
    size_bytes: v.number(),
    mtime: v.number(),
});

export const DocSearchHitSchema = v.object({
    path: v.string(),
    name: v.string(),
    section: v.string(),
    title: v.string(),
    section_title: v.string(),
    anchor: v.nullable(v.number()),
    snippet: v.string(),
    score: v.number(),
    similarity: v.optional(v.nullable(v.number())),
});

export const DocSearchResponseSchema = v.object({
    query: v.string(),
    mode: v.picklist(["hybrid", "bm25", "scan"]),
    stale: v.boolean(),
    results: v.array(DocSearchHitSchema),
});

export const ScriptSearchHitSchema = v.object({
    path: v.string(),
    title: v.string(),
    kind: v.picklist(["script", "test", "make", "mojo", "ts"]),
    area: v.nullable(v.string()),
    purpose: v.nullable(v.string()),
    snippet: v.string(),
    score: v.number(),
    similarity: v.nullable(v.number()),
});

export const ScriptSearchResponseSchema = v.object({
    query: v.string(),
    mode: v.picklist(["hybrid", "bm25", "vector"]),
    stale: v.boolean(),
    results: v.array(ScriptSearchHitSchema),
});

export const GraphCloudNodeSchema = v.object({
    id: v.string(),
    label: v.string(),
    entity_type: v.string(),
});

export const GraphCloudEdgeSchema = v.object({
    source: v.string(),
    target: v.string(),
    edge_type: v.string(),
});

export const RelationshipTypeSummarySchema = v.object({
    edge_type: v.string(),
    count: v.number(),
    symmetric: v.boolean(),
    semantics: v.string(),
});

export const GraphCloudResponseSchema = v.object({
    nodes: v.array(GraphCloudNodeSchema),
    edges: v.array(GraphCloudEdgeSchema),
    relationship_types: v.array(RelationshipTypeSummarySchema),
    total_nodes: v.number(),
    total_edges: v.number(),
});

export const GraphPositionsResponseSchema = v.object({
    positions: v.nullable(v.record(v.string(), v.tuple([v.number(), v.number()]))),
    edge_set_hash: v.string(),
    engine: v.string(),
    engine_params: v.record(v.string(), v.unknown()),
    computed_at: v.string(),
    node_count: v.number(),
    edge_count: v.number(),
    recomputed: v.optional(v.boolean()),
});

export const GraphStructureSchema = v.object({
    density: v.nullable(v.number()),
    diameter: v.nullable(v.number()),
    radius: v.nullable(v.number()),
    avg_path_length: v.nullable(v.number()),
    transitivity: v.nullable(v.number()),
    triangles: v.nullable(v.number()),
    avg_clustering: v.nullable(v.number()),
    assortativity: v.nullable(v.number()),
});

export const GraphStatsResponseSchema = v.object({
    structure: v.nullable(GraphStructureSchema),
    structure_exact: v.nullable(v.record(v.string(), v.number())),
    entities: v.object({ total: v.number(), by_type: v.record(v.string(), v.number()) }),
    edges: v.object({ total: v.number(), by_type: v.record(v.string(), v.number()) }),
    sectors: v.object({
        count: v.number(),
        top: v.array(v.object({ sector: v.string(), n: v.number() })),
        size_distribution: v.object({ min: v.number(), max: v.number(), mean: v.number() }),
    }),
    hygiene: v.object({
        orphan_companies: v.number(),
        no_ticker: v.number(),
        self_loops: v.number(),
        orphan_edges: v.number(),
        conflicting_market_cap: v.number(),
    }),
    staleness: v.object({
        stale: v.boolean(),
        most_recent_entity_update: v.nullable(v.string()),
        most_recent_analytics_compute: v.nullable(v.string()),
    }),
});

export const MetricGroupSchema = v.object({
    label: v.number(),
    size: v.number(),
    members: v.array(v.string()),
});

export const MetricGroupsResponseSchema = v.object({
    metric: v.string(),
    total: v.number(),
    groups: v.array(MetricGroupSchema),
    modularity: v.optional(v.number()),
});

export const MetricRankedRowSchema = v.object({ entity: v.string(), value: v.number() });

export const MetricRankedResponseSchema = v.object({
    metric: v.string(),
    total: v.number(),
    ranked: v.array(MetricRankedRowSchema),
});

export const MetricSeedsResponseSchema = v.object({
    metric: v.string(),
    total: v.number(),
    seeds: v.array(v.string()),
});

export const LinkPredictionCandidateSchema = v.object({ name: v.string(), score: v.number() });

export const LinkPredictionEntitySchema = v.object({
    entity: v.string(),
    method: v.string(),
    edge_types: v.array(v.string()),
    best_score: v.number(),
    candidates: v.array(LinkPredictionCandidateSchema),
});

export const LinkPredictionResponseSchema = v.object({
    metric: v.string(),
    total: v.number(),
    entities: v.array(LinkPredictionEntitySchema),
});

export const SuggestionRowSchema = v.object({
    source: v.string(),
    target: v.string(),
    score: v.number(),
    edition: v.nullable(v.string()),
});

export const SuggestionsResponseSchema = v.object({
    method: v.string(),
    top: v.number(),
    suggestions: v.array(SuggestionRowSchema),
});

export const NearDuplicatePairSchema = v.object({
    path_a: v.string(),
    path_b: v.string(),
    title_a: v.string(),
    title_b: v.string(),
    similarity: v.number(),
});

export const NearDuplicatesResponseSchema = v.object({
    doc_type: v.string(),
    min_sim: v.number(),
    pairs: v.array(NearDuplicatePairSchema),
});

export const CoMentionRowSchema = v.object({ entity: v.string(), co_mentions: v.number() });

export const CoMentionsResponseSchema = v.object({ ranked: v.array(CoMentionRowSchema) });

export const SectorBridgeSchema = v.object({
    edge_type: v.string(),
    sector_a: v.string(),
    sector_b: v.string(),
    count: v.number(),
});

export const BridgesResponseSchema = v.object({ bridges: v.array(SectorBridgeSchema) });

export const YearEdgeCountSchema = v.object({
    year: v.string(),
    edge_type: v.string(),
    count: v.number(),
});

export const EdgesByYearResponseSchema = v.object({ timeline: v.array(YearEdgeCountSchema) });

export const VaultEntitySchema = v.object({
    name: v.string(),
    entity_type: v.string(),
    sector_classification: v.nullable(v.string()),
    market_cap: v.nullable(v.string()),
    enhanced_tags: v.array(v.string()),
    file_path: v.nullable(v.string()),
});

export const EntityDetailResponseSchema = v.object({
    name: v.string(),
    entity_type: v.string(),
    sector_classification: v.nullable(v.string()),
    market_cap: v.nullable(v.string()),
    enhanced_tags: v.array(v.string()),
    file_path: v.nullable(v.string()),
    frontmatter: v.record(v.string(), v.unknown()),
    content: v.string(),
    raw_content: v.string(),
});

export const SimilarNeighborSchema = v.object({
    file_path: v.string(),
    title: v.string(),
    similarity: v.number(),
});

export const SimilarNotesResponseSchema = v.object({
    note: v.string(),
    k: v.number(),
    doc_type: v.nullable(v.string()),
    neighbors: v.array(SimilarNeighborSchema),
});

export const EditionCompaniesResponseSchema = v.object({
    edition: v.string(),
    k: v.number(),
    companies: v.array(SimilarNeighborSchema),
});

export const SemanticNeighborSchema = v.object({
    name: v.string(),
    sector: v.nullable(v.string()),
    similarity: v.number(),
});

export const SemanticResponseSchema = v.object({
    company: v.string(),
    k: v.number(),
    metric: v.string(),
    cross_sector: v.boolean(),
    neighbors: v.array(SemanticNeighborSchema),
});

export const EntitiesResponseSchema = v.object({
    entities: v.array(VaultEntitySchema),
    total_count: v.number(),
    limit: v.number(),
    offset: v.number(),
});
