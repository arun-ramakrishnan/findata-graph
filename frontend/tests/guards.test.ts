// Behaviour battery for the GENERATED guards (types/guards.ts).
//
// The contract suite (tests/test_integration_ts_contract.py) validates
// payloads through the Python twin (assert_type); this file validates the
// emitted TypeScript itself, so a generator regression — like the union
// join that accepted only if ALL members matched — fails here instead of
// shipping. Run via `make frontend-check` (bun test). Cases mirror the
// corpus's actual declared unions: `string | number` (acquired[].year) and
// the literal unions (mode, status, kind).
import { describe, expect, test } from "bun:test";

import {
    isCompanyNeighbors,
    isDocSearchResponse,
    isGraphRefreshResponse,
    isScriptSearchHit,
} from "../types/guards";

const CN_BASE = {
    entity_type: "company",
    company: "HDFC Bank",
    as_of: null,
    file_path: "findata/hdfc.md",
    sector: "Banking",
    peers: ["ICICI Bank"],
    jv_partners: [{ partner: "A", venture: "B" }],
    group_siblings: [],
    acquired: [],
    subsidiary_of: null,
    suppliers: [],
    customers: [],
};

// Complete-but-minimal fixtures: guards presence-check required keys, so a
// payload exercising one field must still carry every required key.
const DOC_BASE = { query: "graph", stale: false, results: [] };
const REFRESH_BASE = { message: "refreshed" };
const HIT_BASE = {
    path: "helpers/misc/x.py",
    title: "X",
    area: "tooling",
    purpose: "probe",
    snippet: "...",
    score: 0.5,
    similarity: null,
};

describe("union acceptance (accept-any, the `_first` regression)", () => {
    test("string | number accepts a string year", () => {
        expect(isCompanyNeighbors({ ...CN_BASE, acquired: [{ name: "T", year: "2020" }] })).toBeNull();
    });
    test("string | number accepts a number year", () => {
        expect(isCompanyNeighbors({ ...CN_BASE, acquired: [{ name: "T", year: 2020 }] })).toBeNull();
    });
    test("string | number rejects boolean and null years", () => {
        expect(
            isCompanyNeighbors({ ...CN_BASE, acquired: [{ name: "T", year: true }] }),
        ).toContain("year");
        expect(
            isCompanyNeighbors({ ...CN_BASE, acquired: [{ name: "T", year: null }] }),
        ).toContain("year");
    });
    test("literal union accepts each member (mode)", () => {
        for (const mode of ["hybrid", "bm25", "scan"]) {
            expect(isDocSearchResponse({ ...DOC_BASE, mode })).toBeNull();
        }
    });
    test("literal union rejects a non-member (mode)", () => {
        expect(isDocSearchResponse({ ...DOC_BASE, mode: "nope" })).toContain("mode");
    });
    test("literal union accepts each member (status)", () => {
        expect(isGraphRefreshResponse({ ...REFRESH_BASE, status: "ok" })).toBeNull();
        expect(isGraphRefreshResponse({ ...REFRESH_BASE, status: "error" })).toBeNull();
        expect(isGraphRefreshResponse({ ...REFRESH_BASE, status: "wat" })).toContain("status");
    });
    test("five-member literal union accepts each member (kind)", () => {
        for (const kind of ["script", "test", "make", "mojo", "ts"]) {
            expect(isScriptSearchHit({ ...HIT_BASE, kind })).toBeNull();
        }
        expect(isScriptSearchHit({ ...HIT_BASE, kind: "nope" })).toContain("kind");
    });
    test("single literal accepts the member and rejects the rest", () => {
        expect(isCompanyNeighbors({ ...CN_BASE, entity_type: "company" })).toBeNull();
        expect(isCompanyNeighbors({ ...CN_BASE, entity_type: "sector" })).toContain("entity_type");
    });
});

describe("required-key presence", () => {
    test("missing required key is rejected", () => {
        const bad: Record<string, unknown> = { ...CN_BASE };
        delete bad.customers;
        expect(isCompanyNeighbors(bad)).toContain("customers");
    });
    test("optional fields pass when absent (invested_by, semantic_peers)", () => {
        expect(isCompanyNeighbors(CN_BASE)).toBeNull();
    });
    test("nested inline-object required keys are presence-checked", () => {
        expect(
            isCompanyNeighbors({ ...CN_BASE, jv_partners: [{ partner: "A" }] }),
        ).toContain("venture");
    });
});

describe("tolerance contract", () => {
    test("extra unknown keys are allowed", () => {
        expect(isCompanyNeighbors({ ...CN_BASE, extra_key: "ignored" })).toBeNull();
    });
    test("T | null accepts null (as_of)", () => {
        expect(isCompanyNeighbors({ ...CN_BASE, as_of: null })).toBeNull();
        expect(isCompanyNeighbors({ ...CN_BASE, as_of: 5 })).toContain("as_of");
    });
    test("non-object payload is rejected at the top", () => {
        expect(isCompanyNeighbors("nope")).toBeTypeOf("string");
        expect(isCompanyNeighbors(null)).toBeTypeOf("string");
    });
});
