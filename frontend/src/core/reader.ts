// Shared paper-register reader core (consolidation: single source of truth).
//
// Canonical home for the reader stack previously duplicated between the
// entity page (entity.ts) and the Reading Room (views/docs.ts): wikilink
// regex + DOM walker, frontmatter helpers, masthead/chips fragments, and
// the entity→file_path index builder. Both views import from here; the
// only behavioral variation (wikilink href emission) is a parameter, not
// a fork: entity pages navigate to /entity/…, the Room navigates in place.

import type { EntityDetailResponse, NoteFrontmatter } from "../../types/api";
import { escapeHtml } from "./dom";

/** Newsletter series directory → masthead publication label (S5 parity). */
export const SERIES_LABELS: Record<string, string> = {
    The_Chatter: "The Chatter",
    Points_And_Figures: "Points & Figures",
    The_PlotLines: "The Plotlines",
};

/** [[target]] / [[target|label]] (heading-anchor suffix tolerated + dropped). */
export const WIKILINK_RE = /\[\[([^\[\]|]+?)(?:#[^\[\]|]*)?(?:\|([^\[\]]+?))?\]\]/g;

/** Frontmatter scalars surfaced as chips / facts. */
export const CHIP_KEYS = ["ticker", "sector", "industry", "market_cap", "created", "last_modified"];

/** Series publication label for a repo-relative file path. */
export function seriesLabel(filePath: string | null | undefined): string | undefined {
    return filePath ? SERIES_LABELS[filePath.split("/")[1]] : undefined;
}

/** Display title: frontmatter title with underscore fallback. */
export function readerTitle(entity: EntityDetailResponse): string {
    return fmString(entity.frontmatter, "title") ?? entity.name.replace(/_/g, " ");
}

/** Edition masthead meta bits (publisher / generated / freshness). */
export function editionBits(entity: EntityDetailResponse): string[] {
    const fm = entity.frontmatter;
    const bits: string[] = [];
    const publisher = fmPublisher(fm);
    if (publisher) bits.push(escapeHtml(publisher));
    const generated = fmGeneratedAt(fm);
    if (generated) bits.push(`generated ${escapeHtml(generated)}`);
    const stale = fmScalar(fm, "stale_after");
    if (stale) bits.push(`fresh through ${escapeHtml(stale)}`);
    return bits;
}

/** Frontmatter chip spans, including the entity-type chip. */
export function chipSpans(entity: EntityDetailResponse): string {
    const fm = entity.frontmatter;
    const chips: string[] = [
        `<span class="fm-chip fm-type">${escapeHtml(entity.entity_type.replace(/_/g, " "))}</span>`,
    ];
    for (const key of CHIP_KEYS) {
        const value = fmScalar(fm, key);
        if (value) {
            chips.push(
                `<span class="fm-chip"><b>${escapeHtml(key.replace(/_/g, " "))}</b>${escapeHtml(value)}</span>`,
            );
        }
    }
    return chips.join("");
}

/** Minimal shape for index building (both entity lists satisfy this). */
export interface IndexableEntity {
    file_path?: string | null;
    name?: string | null;
}

/** stem/name → repo-relative file_path, first-wins (the wikilink resolver). */
export function buildWikilinkIndex(entities: readonly IndexableEntity[]): Map<string, string> {
    const index = new Map<string, string>();
    for (const entity of entities) {
        if (!entity.file_path) continue;
        const stem = (entity.file_path.split("/").pop() || "").replace(/\.md$/i, "");
        if (stem && !index.has(stem)) index.set(stem, entity.file_path);
        if (entity.name && !index.has(entity.name)) index.set(entity.name, entity.file_path);
    }
    return index;
}

/** Resolved href for a wikilink hit (entity page vs Reading Room). */
export interface WikilinkHref {
    href: string;
    datasetHref?: string;
}

/** DOM-level [[wikilink]] rewrite (code/pre/a/script untouched). */
export function linkifyWikilinks(
    root: HTMLElement,
    index: Map<string, string>,
    hrefFor: (target: string) => WikilinkHref,
): void {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
        acceptNode(node: Node): number {
            let parent: Node | null = node.parentElement;
            while (parent && parent !== root) {
                const tag = parent.nodeName;
                if (tag === "CODE" || tag === "PRE" || tag === "A" || tag === "SCRIPT") {
                    return NodeFilter.FILTER_REJECT;
                }
                parent = parent.parentElement;
            }
            return node.nodeValue && node.nodeValue.includes("[[")
                ? NodeFilter.FILTER_ACCEPT
                : NodeFilter.FILTER_SKIP;
        },
    });
    const targets: Text[] = [];
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        targets.push(n as Text);
    }
    for (const textNode of targets) {
        const text = textNode.nodeValue || "";
        WIKILINK_RE.lastIndex = 0;
        if (!WIKILINK_RE.test(text)) continue;
        const fragment = document.createDocumentFragment();
        let cursor = 0;
        let match: RegExpExecArray | null;
        WIKILINK_RE.lastIndex = 0;
        while ((match = WIKILINK_RE.exec(text)) !== null) {
            if (match.index > cursor) {
                fragment.appendChild(document.createTextNode(text.slice(cursor, match.index)));
            }
            const target = (match[1] || "").trim();
            const label = (match[2] || "").trim() || target;
            const href = index.get(target);
            if (href) {
                const resolved = hrefFor(href);
                const anchor = document.createElement("a");
                anchor.className = "wikilink";
                anchor.href = resolved.href;
                if (resolved.datasetHref !== undefined) {
                    anchor.dataset.href = resolved.datasetHref;
                }
                anchor.title = href;
                anchor.textContent = label;
                fragment.appendChild(anchor);
            } else {
                const miss = document.createElement("span");
                miss.className = "wikilink wikilink-miss";
                miss.title = "unresolved note";
                miss.textContent = label;
                fragment.appendChild(miss);
            }
            cursor = match.index + match[0].length;
        }
        if (cursor < text.length) {
            fragment.appendChild(document.createTextNode(text.slice(cursor)));
        }
        textNode.replaceWith(fragment);
    }
}

export function fmString(fm: NoteFrontmatter, key: string): string | null {
    const v = fm[key];
    return typeof v === "string" && v.trim() ? v : null;
}

/** Scalar frontmatter value, dates sliced to YYYY-MM-DD. */
export function fmScalar(fm: NoteFrontmatter, key: string): string | null {
    const v = fmString(fm, key);
    if (v === null) return null;
    return /^\d{4}-\d{2}-\d{2}T/.test(v) ? v.slice(0, 10) : v;
}

/** generated.at from the nested frontmatter block. */
export function fmGeneratedAt(fm: NoteFrontmatter): string | null {
    const g = fm.generated;
    if (g && typeof g === "object" && "at" in g) {
        const at = (g as { at?: unknown }).at;
        if (typeof at === "string" && at) return at.slice(0, 10);
    }
    return null;
}

/** publisher/<name> tag → capitalized label. */
export function fmPublisher(fm: NoteFrontmatter): string | null {
    const tags = Array.isArray(fm.tags)
        ? fm.tags.filter((t): t is string => typeof t === "string")
        : [];
    for (const tag of tags) {
        if (tag.startsWith("publisher/")) {
            return tag.slice("publisher/".length).replace(/\b\w/g, (c) => c.toUpperCase());
        }
    }
    return null;
}
