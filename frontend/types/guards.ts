// GENERATED FILE — do not edit by hand.
// Source: frontend/types/api.ts  (via tests/api_contract.py)
// Regenerate: python3 helpers/misc/gen_api_guards.py
// Verify:     python3 helpers/misc/gen_api_guards.py --check
//
// Runtime shape guards for the /api/* payloads. Each `is<Interface>(value)`
// runs the valibot schema for its api.ts interface and returns null on
// match, or a path ("entities.3.weight") naming the first mismatch —
// required keys are presence-checked, optional fields pass when absent.
// fetchJson turns a mismatch into a ShapeError so a drifted response fails
// loudly instead of rendering garbage. Behaviour is regression-tested in
// frontend/tests/guards.test.ts (bun test, via `make frontend-check`).
import * as v from "valibot";

import * as S from "./schemas_valibot";

export type Guard = (value: unknown) => string | null;

export const isErrorResponse: Guard = (value) => {
    const result = v.safeParse(S.ErrorResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isSectorEntity: Guard = (value) => {
    const result = v.safeParse(S.SectorEntitySchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isSuperSector: Guard = (value) => {
    const result = v.safeParse(S.SuperSectorSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isSectorsResponse: Guard = (value) => {
    const result = v.safeParse(S.SectorsResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isStatsResponse: Guard = (value) => {
    const result = v.safeParse(S.StatsResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isEntityListItem: Guard = (value) => {
    const result = v.safeParse(S.EntityListItemSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isEntityDetail: Guard = (value) => {
    const result = v.safeParse(S.EntityDetailSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isSearchResult: Guard = (value) => {
    const result = v.safeParse(S.SearchResultSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isSearchResponse: Guard = (value) => {
    const result = v.safeParse(S.SearchResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isGraphRefreshResponse: Guard = (value) => {
    const result = v.safeParse(S.GraphRefreshResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isCompanyNeighbors: Guard = (value) => {
    const result = v.safeParse(S.CompanyNeighborsSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isSectorNeighbors: Guard = (value) => {
    const result = v.safeParse(S.SectorNeighborsSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isSuperSectorNeighbors: Guard = (value) => {
    const result = v.safeParse(S.SuperSectorNeighborsSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isSubSectorNeighbors: Guard = (value) => {
    const result = v.safeParse(S.SubSectorNeighborsSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isThemeNeighbors: Guard = (value) => {
    const result = v.safeParse(S.ThemeNeighborsSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isShortestHop: Guard = (value) => {
    const result = v.safeParse(S.ShortestHopSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isShortestPathResponse: Guard = (value) => {
    const result = v.safeParse(S.ShortestPathResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isEventItem: Guard = (value) => {
    const result = v.safeParse(S.EventItemSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isEventsResponse: Guard = (value) => {
    const result = v.safeParse(S.EventsResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isDocItem: Guard = (value) => {
    const result = v.safeParse(S.DocItemSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isDocsResponse: Guard = (value) => {
    const result = v.safeParse(S.DocsResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isDocContentResponse: Guard = (value) => {
    const result = v.safeParse(S.DocContentResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isDocSearchHit: Guard = (value) => {
    const result = v.safeParse(S.DocSearchHitSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isDocSearchResponse: Guard = (value) => {
    const result = v.safeParse(S.DocSearchResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isScriptSearchHit: Guard = (value) => {
    const result = v.safeParse(S.ScriptSearchHitSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isScriptSearchResponse: Guard = (value) => {
    const result = v.safeParse(S.ScriptSearchResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isGraphCloudNode: Guard = (value) => {
    const result = v.safeParse(S.GraphCloudNodeSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isGraphCloudEdge: Guard = (value) => {
    const result = v.safeParse(S.GraphCloudEdgeSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isRelationshipTypeSummary: Guard = (value) => {
    const result = v.safeParse(S.RelationshipTypeSummarySchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isGraphCloudResponse: Guard = (value) => {
    const result = v.safeParse(S.GraphCloudResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isGraphPositionsResponse: Guard = (value) => {
    const result = v.safeParse(S.GraphPositionsResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isGraphStructure: Guard = (value) => {
    const result = v.safeParse(S.GraphStructureSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isGraphStatsResponse: Guard = (value) => {
    const result = v.safeParse(S.GraphStatsResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isMetricGroup: Guard = (value) => {
    const result = v.safeParse(S.MetricGroupSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isMetricGroupsResponse: Guard = (value) => {
    const result = v.safeParse(S.MetricGroupsResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isMetricRankedRow: Guard = (value) => {
    const result = v.safeParse(S.MetricRankedRowSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isMetricRankedResponse: Guard = (value) => {
    const result = v.safeParse(S.MetricRankedResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isMetricSeedsResponse: Guard = (value) => {
    const result = v.safeParse(S.MetricSeedsResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isLinkPredictionCandidate: Guard = (value) => {
    const result = v.safeParse(S.LinkPredictionCandidateSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isLinkPredictionEntity: Guard = (value) => {
    const result = v.safeParse(S.LinkPredictionEntitySchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isLinkPredictionResponse: Guard = (value) => {
    const result = v.safeParse(S.LinkPredictionResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isSuggestionRow: Guard = (value) => {
    const result = v.safeParse(S.SuggestionRowSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isSuggestionsResponse: Guard = (value) => {
    const result = v.safeParse(S.SuggestionsResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isNearDuplicatePair: Guard = (value) => {
    const result = v.safeParse(S.NearDuplicatePairSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isNearDuplicatesResponse: Guard = (value) => {
    const result = v.safeParse(S.NearDuplicatesResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isCoMentionRow: Guard = (value) => {
    const result = v.safeParse(S.CoMentionRowSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isCoMentionsResponse: Guard = (value) => {
    const result = v.safeParse(S.CoMentionsResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isSectorBridge: Guard = (value) => {
    const result = v.safeParse(S.SectorBridgeSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isBridgesResponse: Guard = (value) => {
    const result = v.safeParse(S.BridgesResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isYearEdgeCount: Guard = (value) => {
    const result = v.safeParse(S.YearEdgeCountSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isEdgesByYearResponse: Guard = (value) => {
    const result = v.safeParse(S.EdgesByYearResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isVaultEntity: Guard = (value) => {
    const result = v.safeParse(S.VaultEntitySchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isEntityDetailResponse: Guard = (value) => {
    const result = v.safeParse(S.EntityDetailResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isSimilarNeighbor: Guard = (value) => {
    const result = v.safeParse(S.SimilarNeighborSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isSimilarNotesResponse: Guard = (value) => {
    const result = v.safeParse(S.SimilarNotesResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isEditionCompaniesResponse: Guard = (value) => {
    const result = v.safeParse(S.EditionCompaniesResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isSemanticNeighbor: Guard = (value) => {
    const result = v.safeParse(S.SemanticNeighborSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isSemanticResponse: Guard = (value) => {
    const result = v.safeParse(S.SemanticResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};

export const isEntitiesResponse: Guard = (value) => {
    const result = v.safeParse(S.EntitiesResponseSchema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${where}: ${issue.message}`;
};
