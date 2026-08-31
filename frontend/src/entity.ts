// Entity detail page (S6) — standalone /entity/<path> pages, migrated from
// static/entity_detail.js into the TypeScript build (second esbuild entry →
// static/entity.bundle.js). One pipeline with the SPA: core/api typed
// fetches, core/markdown rendering (DOMPurify + data-attr interactions),
// and the S5 Reading Room reader idioms — paper register, frontmatter
// chips, edition mastheads, [[wikilink]] navigation — plus the entity-page
// rail: mono facts, vertical events timeline, semantic peers, similar notes.
//
// Navigation model: this is a separate page per note, so wikilinks and rail
// rows navigate the browser to /entity/<encoded file_path> (no SPA router
// here).

import type {
    EntitiesResponse,
    EntityDetailResponse,
    EventsResponse,
    NoteFrontmatter,
    SemanticResponse,
    SimilarNotesResponse,
} from "../types/api";
import { getEl, escapeHtml } from "./core/dom";
import { fetchJson } from "./core/api";
import {
    processRichContent,
    wireRichInteractions,
    closeLightbox,
    setLightboxOpener,
} from "./core/markdown";
import { showToast } from "./core/toast";

/** Newsletter series directory → masthead publication label (S5 parity). */
const SERIES_LABELS: Record<string, string> = {
    The_Chatter: "The Chatter",
    Points_And_Figures: "Points & Figures",
    The_PlotLines: "The Plotlines",
};

/** [[target]] / [[target|label]] (heading-anchor suffix tolerated + dropped). */
const WIKILINK_RE = /\[\[([^\[\]|]+?)(?:#[^\[\]|]*)?(?:\|([^\[\]]+?))?\]\]/g;

/** Frontmatter scalars surfaced as chips / facts. */
const CHIP_KEYS = ["ticker", "sector", "industry", "market_cap", "created", "last_modified"];

class EntityPage {
    private readonly entityPath: string;
    private entity: EntityDetailResponse | null = null;

    /** stem/name → repo-relative file_path, the wikilink resolver. */
    private wikilinks: Map<string, string> | null = null;

    /** Lightbox navigation state (image order = document order). */
    private images: { src: string; alt: string }[] = [];
    private currentImageIndex = 0;

    constructor() {
        // /entity/<path...> — decode once; re-encode per fetch (handles both
        // the %2F form the SPA emits and raw-slash URLs).
        const raw = window.location.pathname.split("/").slice(2).join("/");
        let decoded = raw;
        try {
            decoded = decodeURIComponent(raw);
        } catch {
            // Malformed percent sequence — fall back to the raw path.
        }
        this.entityPath = decoded;

        this.bindEvents();
        // Rich-content images route into this page's navigable lightbox.
        setLightboxOpener((src) => this.openLightbox(src));
        void this.loadEntity();
    }

    private bindEvents(): void {
        getEl("toggle-rail").addEventListener("click", () => {
            const rail = getEl("entity-rail");
            const hidden = rail.classList.toggle("collapsed");
            getEl("toggle-rail").classList.toggle("active", !hidden);
        });
        getEl("export-content").addEventListener("click", () => this.exportContent());
        getEl("print-content").addEventListener("click", () => {
            window.print();
            showToast("Print dialog opened", "success");
        });
        getEl("toggle-fullscreen").addEventListener("click", () => this.toggleFullscreen());
        document.addEventListener("fullscreenchange", () => this.updateFullscreenButton());

        // Lightbox: dismiss + prev/next + keyboard.
        getEl("close-lightbox").addEventListener("click", () => closeLightbox());
        getEl("image-lightbox").addEventListener("click", (e) => {
            if ((e.target as HTMLElement).id === "image-lightbox") closeLightbox();
        });
        getEl("lightbox-prev").addEventListener("click", () => this.navigateLightbox(-1));
        getEl("lightbox-next").addEventListener("click", () => this.navigateLightbox(1));
        document.addEventListener("keydown", (e) => {
            const lightbox = getEl("image-lightbox");
            if (lightbox.style.display !== "flex") return;
            if (e.key === "Escape") {
                closeLightbox();
            } else if (e.key === "ArrowLeft") {
                e.preventDefault();
                this.navigateLightbox(-1);
            } else if (e.key === "ArrowRight") {
                e.preventDefault();
                this.navigateLightbox(1);
            }
        });
    }

    // --- load + render ----------------------------------------------------- //

    private async loadEntity(): Promise<void> {
        try {
            const entity = await fetchJson<EntityDetailResponse>(
                `/api/entity/${encodeURIComponent(this.entityPath)}`,
            );
            this.entity = entity;
            this.displayEntity();
        } catch (error) {
            console.error("Error loading entity:", error);
            this.showError(error instanceof Error ? error.message : "unknown error");
        }
    }

    private displayEntity(): void {
        const entity = this.entity;
        if (!entity) return;
        const fm = entity.frontmatter;
        const isEdition =
            entity.entity_type === "edition" || this.fmString(fm, "type") === "newsletter";

        document.title = `${entity.name} — FinData Knowledge Graph`;
        getEl("page-title").textContent = entity.name;
        getEl("breadcrumb-current").textContent =
            `${entity.entity_type.replace(/_/g, " ")}: ${entity.name}`;

        this.renderHeader(entity, isEdition);
        this.renderFacts(entity);

        const contentEl = getEl("entity-content");
        if (entity.content) {
            const { html, headings } = processRichContent(entity.content);
            contentEl.innerHTML = html;
            wireRichInteractions(contentEl);
            this.renderToc(headings);
            this.collectImages();
        } else {
            contentEl.innerHTML =
                '<div class="no-content"><i class="fas fa-file-alt"></i><p>No content available for this entity.</p></div>';
        }

        getEl("loading-state").style.display = "none";
        getEl("main-content").style.display = "grid";

        // Async rail intel + wikilink resolution (quiet on failure).
        void this.ensureWikilinkIndex().then((index) => {
            if (index) this.linkifyWikilinks(contentEl);
        });
        void this.loadEvents(entity.name);
        void this.loadSemanticPeers(entity.name);
        if (entity.file_path) void this.loadSimilarNotes(entity.file_path);
    }

    /** Title block: chips (companies/sectors) or masthead (editions). */
    private renderHeader(entity: EntityDetailResponse, isEdition: boolean): void {
        const fm = entity.frontmatter;
        const mount = getEl("entity-metadata");
        if (isEdition) {
            const series = SERIES_LABELS[(entity.file_path || "").split("/")[1]];
            const title = this.fmString(fm, "title") ?? entity.name.replace(/_/g, " ");
            const bits: string[] = [];
            const publisher = this.fmPublisher(fm);
            if (publisher) bits.push(escapeHtml(publisher));
            const generated = this.fmGeneratedAt(fm);
            if (generated) bits.push(`generated ${escapeHtml(generated)}`);
            const stale = this.fmString(fm, "stale_after");
            if (stale) bits.push(`fresh through ${escapeHtml(stale.slice(0, 10))}`);
            mount.innerHTML = `
                <header class="edition-masthead">
                    <div class="masthead-pub">${escapeHtml(series || "Newsletter")}</div>
                    <h1 class="masthead-title">${escapeHtml(title)}</h1>
                    ${bits.length ? `<div class="masthead-meta">${bits.join(' <span class="dot">·</span> ')}</div>` : ""}
                </header>
            `;
            return;
        }
        const chips: string[] = [
            `<span class="fm-chip fm-type">${escapeHtml(entity.entity_type.replace(/_/g, " "))}</span>`,
        ];
        for (const key of CHIP_KEYS) {
            const value = this.fmScalar(fm, key);
            if (value) {
                chips.push(
                    `<span class="fm-chip"><b>${escapeHtml(key.replace(/_/g, " "))}</b>${escapeHtml(value)}</span>`,
                );
            }
        }
        const tags = entity.enhanced_tags.length
            ? `<div class="entity-tags">${entity.enhanced_tags
                  .map((t) => `<span class="entity-tag">${escapeHtml(t)}</span>`)
                  .join("")}</div>`
            : "";
        mount.innerHTML = `
            <header class="entity-head">
                <h1>${escapeHtml(this.fmString(fm, "title") ?? entity.name.replace(/_/g, " "))}</h1>
                <div class="fm-chips">${chips.join("")}</div>
                ${tags}
            </header>
        `;
    }

    /** The mono facts block at the top of the rail. */
    private renderFacts(entity: EntityDetailResponse): void {
        const fm = entity.frontmatter;
        const facts: [string, string][] = [];
        if (entity.sector_classification) facts.push(["sector", entity.sector_classification]);
        if (entity.market_cap) facts.push(["market cap", entity.market_cap]);
        const normalized = this.fmString(fm, "normalized_name");
        if (normalized) facts.push(["normalized", normalized]);
        const permalink = this.fmString(fm, "permalink");
        if (permalink) facts.push(["permalink", permalink]);
        if (entity.file_path) facts.push(["file", entity.file_path]);
        if (!facts.length) {
            getEl("rail-facts").style.display = "none";
            return;
        }
        getEl("facts-grid").innerHTML = facts
            .map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`)
            .join("");
    }

    /** TOC into the rail; hidden when the note has fewer than two headings. */
    private renderToc(headings: { level: number; text: string; id: string }[]): void {
        if (headings.length < 2) return;
        getEl("toc-content").innerHTML = headings
            .map(
                (h) =>
                    `<li class="toc-${h.level}"><a href="#${encodeURIComponent(h.id)}">${escapeHtml(h.text)}</a></li>`,
            )
            .join("");
        getEl("toc-block").style.display = "block";
    }

    // --- rail intel ---------------------------------------------------------- //

    /** Vertical events timeline (dated oldest→newest, undated last). */
    private async loadEvents(name: string): Promise<void> {
        try {
            const data = await fetchJson<EventsResponse>(`/api/events/${encodeURIComponent(name)}`);
            if (!data.events.length) return;
            getEl("events-tl").innerHTML = data.events
                .map((ev) => {
                    const date = this.eventDateLabel(ev.event_date, ev.date_precision);
                    const body = [
                        ev.counterparty ? escapeHtml(ev.counterparty) : "",
                        ev.magnitude
                            ? `<span class="ev-mag">${escapeHtml(ev.magnitude)}</span>`
                            : "",
                    ]
                        .filter(Boolean)
                        .join(" · ");
                    const quote = ev.source_quote
                        ? ` title="${escapeHtml(ev.source_quote).replace(/"/g, "&quot;")}"`
                        : "";
                    return `
                    <li class="ev-item"${quote}>
                        <span class="ev-date">${escapeHtml(date)}</span>
                        <span class="ev-type">${escapeHtml(ev.event_type)}</span>
                        <span class="ev-body">${body}</span>
                    </li>
                `;
                })
                .join("");
            getEl("rail-events").style.display = "block";
        } catch {
            // Most entities have no events — quiet.
        }
    }

    /** Semantic peers as chips (company embeddings only — quiet otherwise). */
    private async loadSemanticPeers(name: string): Promise<void> {
        try {
            const data = await fetchJson<SemanticResponse>(
                `/api/graph/semantic/${encodeURIComponent(name)}?k=8`,
            );
            if (!data.neighbors.length) return;
            getEl("peers-chips").innerHTML = data.neighbors
                .map((n) => {
                    const pct = Math.round(n.similarity * 100);
                    const href = this.wikilinks?.get(n.name);
                    const inner = `${escapeHtml(n.name.replace(/_/g, " "))} <b>${pct}%</b>`;
                    return href
                        ? `<a class="peer-chip" href="/entity/${encodeURIComponent(href)}" title="${escapeHtml(n.sector || "")}">${inner}</a>`
                        : `<span class="peer-chip" title="${escapeHtml(n.sector || "")}">${inner}</span>`;
                })
                .join("");
            getEl("rail-peers").style.display = "block";
        } catch {
            // Not embedded / not a company — quiet.
        }
    }

    /** Embedding-similar notes as clickable rows. */
    private async loadSimilarNotes(filePath: string): Promise<void> {
        try {
            const data = await fetchJson<SimilarNotesResponse>(
                `/api/graph/similar/${encodeURIComponent(filePath)}?k=6`,
            );
            if (!data.neighbors.length) return;
            getEl("similar-list").innerHTML = data.neighbors
                .map((n) => {
                    const pct = Math.round(n.similarity * 100);
                    return `
                    <a class="related-row" href="/entity/${encodeURIComponent(n.file_path)}"
                       title="${escapeHtml(n.file_path)}">
                        <span class="related-title">${escapeHtml(n.title.replace(/_/g, " "))}</span>
                        <span class="related-sim"><span class="related-bar"><span
                            class="bar-fill" style="width:${pct}%"></span></span>${pct}%</span>
                    </a>
                `;
                })
                .join("");
            getEl("rail-similar").style.display = "block";
        } catch {
            // Unembedded note — quiet.
        }
    }

    // --- wikilinks ------------------------------------------------------------- //

    private async ensureWikilinkIndex(): Promise<Map<string, string> | null> {
        if (this.wikilinks) return this.wikilinks;
        try {
            const data = await fetchJson<EntitiesResponse>("/api/entities?limit=5000");
            const index = new Map<string, string>();
            for (const entity of data.entities) {
                if (!entity.file_path) continue;
                const stem = (entity.file_path.split("/").pop() || "").replace(/\.md$/i, "");
                if (stem && !index.has(stem)) index.set(stem, entity.file_path);
                if (entity.name && !index.has(entity.name))
                    index.set(entity.name, entity.file_path);
            }
            this.wikilinks = index;
            return index;
        } catch {
            return null;
        }
    }

    /** Same DOM-level rewrite as the Reading Room (code/pre/a untouched). */
    private linkifyWikilinks(root: HTMLElement): void {
        const index = this.wikilinks;
        if (!index) return;
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
                    const anchor = document.createElement("a");
                    anchor.className = "wikilink";
                    anchor.href = `/entity/${encodeURIComponent(href)}`;
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

    // --- page chrome -------------------------------------------------------------- //

    private collectImages(): void {
        this.images = Array.from(
            document.querySelectorAll<HTMLImageElement>("#entity-content .rich-image"),
        ).map((img) => ({ src: img.src, alt: img.alt || "Image" }));
    }

    /** Entry point for the lightbox (delegated via wireRichInteractions). */
    openLightbox(src: string): void {
        const lightbox = getEl("image-lightbox");
        const image = getEl("lightbox-image") as HTMLImageElement;
        this.currentImageIndex = Math.max(
            0,
            this.images.findIndex((i) => i.src === src),
        );
        image.src = src;
        (document.querySelector(".lightbox-caption") as HTMLElement).textContent =
            this.images[this.currentImageIndex]?.alt || "Image";
        lightbox.style.display = "flex";
        getEl("lightbox-prev").style.display = this.images.length > 1 ? "block" : "none";
        getEl("lightbox-next").style.display = this.images.length > 1 ? "block" : "none";
        document.body.style.overflow = "hidden";
    }

    private navigateLightbox(direction: number): void {
        if (!this.images.length) return;
        this.currentImageIndex =
            (this.currentImageIndex + direction + this.images.length) % this.images.length;
        const current = this.images[this.currentImageIndex];
        (getEl("lightbox-image") as HTMLImageElement).src = current.src;
        (document.querySelector(".lightbox-caption") as HTMLElement).textContent = current.alt;
    }

    private toggleFullscreen(): void {
        if (!document.fullscreenElement) {
            void document.documentElement.requestFullscreen();
        } else {
            void document.exitFullscreen();
        }
    }

    private updateFullscreenButton(): void {
        const btn = getEl("toggle-fullscreen");
        const icon = btn.querySelector("i");
        const text = btn.querySelector("span");
        if (!icon || !text) return;
        if (document.fullscreenElement) {
            icon.className = "fas fa-compress";
            text.textContent = "Exit Fullscreen";
        } else {
            icon.className = "fas fa-expand";
            text.textContent = "Fullscreen";
        }
    }

    private exportContent(): void {
        const entity = this.entity;
        if (!entity) return;
        const facts = getEl("entity-metadata").textContent || "";
        const markdown = `# ${entity.name}\n\n${facts.trim()}\n\n---\n\n${entity.content || ""}`;
        const blob = new Blob([markdown], { type: "text/markdown" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = `${entity.name.replace(/[^a-z0-9]/gi, "_")}.md`;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
        showToast("Content exported successfully!", "success");
    }

    private showError(message: string): void {
        getEl("loading-state").style.display = "none";
        getEl("main-content").style.display = "none";
        const errorState = getEl("error-state");
        errorState.style.display = "flex";
        const paragraph = errorState.querySelector("p");
        if (paragraph) paragraph.textContent = message;
    }

    // --- frontmatter helpers --------------------------------------------------------- //

    private fmString(fm: NoteFrontmatter, key: string): string | null {
        const v = fm[key];
        return typeof v === "string" && v.trim() ? v : null;
    }

    private fmScalar(fm: NoteFrontmatter, key: string): string | null {
        const v = this.fmString(fm, key);
        if (v === null) return null;
        return /^\d{4}-\d{2}-\d{2}T/.test(v) ? v.slice(0, 10) : v;
    }

    private fmGeneratedAt(fm: NoteFrontmatter): string | null {
        const g = fm.generated;
        if (g && typeof g === "object" && "at" in g) {
            const at = (g as { at?: unknown }).at;
            if (typeof at === "string" && at) return at.slice(0, 10);
        }
        return null;
    }

    private fmPublisher(fm: NoteFrontmatter): string | null {
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

    private eventDateLabel(date: string | null, precision: string | null): string {
        if (!date) return "—";
        if (precision === "year") return date.slice(0, 4);
        if (precision === "month") return date.slice(0, 7);
        return date;
    }
}

// Boot immediately (script runs at end of <body>).
new EntityPage();
