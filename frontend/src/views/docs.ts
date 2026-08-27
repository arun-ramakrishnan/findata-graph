// The Reading Room (S5) — unified doc/ + vault browser.
//
// Two collections in one sidebar: the doc/ design corpus (filesystem API)
// and every note-bearing entity in the findata/ vault (/api/entities). The
// reader renders vault notes in the paper register: frontmatter chips for
// company/sector notes, a double-rule masthead for newsletter editions,
// [[wikilink]] in-place navigation against a client-side stem→file_path
// index, and a related rail (embedding-similar notes +, for editions, the
// companies the edition is about).
//
// Wikilink resolution follows the obsidian-wikilink-resolves-to-stem rule:
// [[X]] targets the note FILENAME stem (with [[stem|title]] label form and
// entity names accepted as aliases) — the index is built from
// /api/entities, the same source the app trusts.

import type {
    DocContentResponse,
    DocSearchResponse,
    DocsResponse,
    EntitiesResponse,
    EntityDetailResponse,
    NoteFrontmatter,
    SearchResponse,
    SimilarNeighbor,
    SimilarNotesResponse,
    EditionCompaniesResponse,
    VaultEntity,
} from "../../types/api";
import { getEl, escapeHtml, formatBytes } from "../core/dom";
import { fetchJson } from "../core/api";
import {
    closeLightbox,
    highlightSnippet,
    processRichContent,
    wireRichInteractions,
} from "../core/markdown";

/** Which sidebar collection is active. */
type Collection = "docs" | "vault";

/** One rendered sidebar group (header label null = flat list). */
interface ListGroup {
    label: string | null;
    rows: { path: string; title: string; sub: string | null; chip: string | null; snippet: string; sim: number | null }[];
}

/** Newsletter series directory → masthead publication label. */
const SERIES_LABELS: Record<string, string> = {
    The_Chatter: "The Chatter",
    Points_And_Figures: "Points & Figures",
    The_PlotLines: "The Plotlines",
};

/** doc_type (note_search) → short sidebar chip label. */
const DOCTYPE_LABELS: Record<string, string> = {
    company: "company",
    sector: "sector",
    super_sector: "super sector",
    chatter: "chatter",
    points_and_figures: "P&F",
    plotlines: "plotlines",
};

/** [[target]] / [[target|label]] (heading-anchor suffix tolerated + dropped). */
const WIKILINK_RE = /\[\[([^\[\]|]+?)(?:#[^\[\]|]*)?(?:\|([^\[\]]+?))?\]\]/g;

/** Frontmatter scalar keys surfaced as chips on non-edition notes. */
const CHIP_KEYS = ["ticker", "sector", "industry", "market_cap", "created", "last_modified"];

/** localStorage keys for the persisted reader preferences. */
const READSIZE_KEY = "findata.docs.readsize";
const FOCUS_KEY = "findata.docs.focus";

export class DocsView {
    // --- docs-tab state --------------------------------------------------- //
    activePath: string | null = null;

    /** Which collection the sidebar shows. */
    private collection: Collection = "docs";

    /** Vault entities with notes (cached; null until first vault open). */
    private vaultEntities: VaultEntity[] | null = null;

    /** stem/name → repo-relative file_path, the wikilink resolver. */
    private wikilinks: Map<string, string> | null = null;

    /** Debounce timer handle for the docs search input. */
    private docsSearchTimeout: ReturnType<typeof setTimeout> | undefined;

    /** Whether this view is the visible one (deferred-render check). */
    private readonly isActive: () => boolean;

    constructor(isActive: () => boolean) {
        this.isActive = isActive;
    }

    /** Wire the static docs controls + lightbox dismissers (once at boot). */
    bindEvents(): void {
        // Lightbox.
        getEl("close-lightbox").addEventListener("click", () => {
            closeLightbox();
        });
        getEl("image-lightbox").addEventListener("click", (e) => {
            if ((e.target as HTMLElement).id === "image-lightbox") {
                closeLightbox();
            }
        });

        // Debounced search + reset-to-catalog.
        const docsSearch = getEl("docs-search") as HTMLInputElement;
        const docsClear = getEl("docs-search-clear") as HTMLElement;
        docsSearch.addEventListener("input", (e) => {
            const target = e.target as HTMLInputElement;
            docsClear.style.display = target.value ? "block" : "none";
            this.debounceDocsSearch();
        });
        docsClear.addEventListener("click", () => {
            docsSearch.value = "";
            docsClear.style.display = "none";
            this.loadCatalog();
        });
        getEl("docs-reset").addEventListener("click", () => {
            docsSearch.value = "";
            docsClear.style.display = "none";
            this.loadCatalog();
        });

        // Collection tabs (doc/ ↔ vault).
        document.querySelectorAll<HTMLElement>(".collection-tab").forEach((tab) => {
            tab.addEventListener("click", () => {
                const c = tab.dataset.collection as Collection;
                if (c !== this.collection) this.setCollection(c);
            });
        });

        // Hybrid rerank only affects the vault corpus — re-run the search
        // when it flips so the toggle is immediately legible.
        getEl("hybrid-search").addEventListener("change", () => {
            if ((getEl("docs-search") as HTMLInputElement).value.trim()) {
                this.debounceDocsSearch();
            }
        });

        // In-place wikilink navigation (delegated — pane innerHTML rebuilds).
        getEl("docs-content-pane").addEventListener("click", (e) => {
            const anchor = (e.target as HTMLElement).closest<HTMLAnchorElement>("a.wikilink");
            if (!anchor) return;
            e.preventDefault();
            const href = anchor.dataset.href;
            if (href) void this.openNote(href);
        });

        // Reader comfort: text size + focus mode, both persisted. The view
        // section carries the state (data-readsize / .focus-mode) so the CSS
        // stays descendant-scoped to #docs-view.
        const view = getEl("docs-view");
        const applyReadSize = (size: string): void => {
            view.dataset.readsize = size;
            document.querySelectorAll<HTMLButtonElement>(".readsize-btn").forEach((b) => {
                b.classList.toggle("active", b.dataset.readsize === size);
            });
            try { localStorage.setItem(READSIZE_KEY, size); } catch { /* private mode */ }
        };
        document.querySelectorAll<HTMLButtonElement>(".readsize-btn").forEach((btn) => {
            btn.addEventListener("click", () => applyReadSize(btn.dataset.readsize || "m"));
        });
        const focusToggle = getEl("docs-focus-toggle");
        const applyFocus = (on: boolean): void => {
            view.classList.toggle("focus-mode", on);
            focusToggle.classList.toggle("active", on);
            try { localStorage.setItem(FOCUS_KEY, on ? "1" : "0"); } catch { /* private mode */ }
        };
        focusToggle.addEventListener("click", () =>
            applyFocus(!view.classList.contains("focus-mode")));
        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape" && view.classList.contains("focus-mode")
                    && this.isActive()) {
                applyFocus(false);
            }
        });
        try {
            const savedSize = localStorage.getItem(READSIZE_KEY);
            applyReadSize(savedSize === "s" || savedSize === "m" || savedSize === "l"
                ? savedSize : "m");
            if (localStorage.getItem(FOCUS_KEY) === "1") applyFocus(true);
        } catch {
            applyReadSize("m");
        }
    }

    // --- collection switching ---------------------------------------------- //

    private setCollection(c: Collection): void {
        this.collection = c;
        document.querySelectorAll<HTMLElement>(".collection-tab").forEach((tab) => {
            const active = tab.dataset.collection === c;
            tab.classList.toggle("active", active);
            tab.setAttribute("aria-selected", active ? "true" : "false");
        });
        getEl("hybrid-toggle").style.display = c === "vault" ? "flex" : "none";
        const search = getEl("docs-search") as HTMLInputElement;
        search.value = "";
        search.placeholder = c === "vault"
            ? "Search every vault note (FTS)..."
            : "Search design docs, proposals, archives...";
        getEl("docs-search-clear").style.display = "none";
        void this.loadCatalog();
    }

    // --- catalog ----------------------------------------------------------- //

    /** Load the active collection's full catalog into the sidebar. */
    async loadCatalog(): Promise<void> {
        this.activePath = null;
        this.hideContentPane();
        if (this.collection === "vault") {
            try {
                const entities = await this.ensureVault();
                this.renderVaultList(entities);
                getEl("docs-count").textContent =
                    `${entities.length.toLocaleString()} notes`;
            } catch (error) {
                console.error("Error loading vault:", error);
                getEl("docs-list").innerHTML =
                    '<div class="no-results">Could not load the vault index.</div>';
            }
            return;
        }
        try {
            const data = await fetchJson<DocsResponse>("/api/docs");
            this.renderGroups([{
                label: null,
                rows: data.docs.map((d) => ({
                    path: d.path,
                    title: d.title,
                    sub: d.section || null,
                    chip: null,
                    snippet: "",
                    sim: null,
                })),
            }]);
            getEl("docs-count").textContent = `${data.docs.length} documents`;
        } catch (error) {
            console.error("Error loading docs:", error);
            getEl("docs-list").innerHTML =
                '<div class="no-results">Could not load the document catalog.</div>';
        }
    }

    /** Fetch the vault entity list once; also builds the wikilink index. */
    private async ensureVault(): Promise<VaultEntity[]> {
        if (this.vaultEntities) return this.vaultEntities;
        const data = await fetchJson<EntitiesResponse>("/api/entities?limit=5000");
        const withNotes = data.entities.filter((e) => e.file_path);
        const index = new Map<string, string>();
        for (const entity of withNotes) {
            const fp = entity.file_path as string;
            const stem = (fp.split("/").pop() || "").replace(/\.md$/i, "");
            if (stem && !index.has(stem)) index.set(stem, fp);
            if (entity.name && !index.has(entity.name)) index.set(entity.name, fp);
        }
        this.vaultEntities = withNotes;
        this.wikilinks = index;
        return withNotes;
    }

    /** Grouped vault listing: supers → sectors → editions by series → companies by sector. */
    private renderVaultList(entities: VaultEntity[]): void {
        const byType = (t: string) => entities.filter((e) => e.entity_type === t);
        const groups: ListGroup[] = [];

        groups.push({
            label: "Super Sectors",
            rows: byType("super_sector").map(rowForTyped),
        });
        groups.push({
            label: "Sectors",
            rows: byType("sector").map(rowForTyped),
        });

        const editions = byType("edition");
        for (const [dir, label] of Object.entries(SERIES_LABELS)) {
            const rows = editions
                .filter((e) => (e.file_path || "").split("/")[1] === dir)
                .map(rowForTyped)
                .sort((a, b) => a.title.localeCompare(b.title));
            if (rows.length) groups.push({ label: `${label} (${rows.length})`, rows });
        }

        const sectorGroups = new Map<string, VaultEntity[]>();
        for (const company of byType("company")) {
            const key = company.sector_classification || "Unclassified";
            sectorGroups.set(key, [...(sectorGroups.get(key) || []), company]);
        }
        for (const key of [...sectorGroups.keys()].sort((a, b) => a.localeCompare(b))) {
            const rows = (sectorGroups.get(key) || [])
                .map(rowForTyped)
                .sort((a, b) => a.title.localeCompare(b.title));
            groups.push({ label: `${key} (${rows.length})`, rows });
        }

        this.renderGroups(groups.filter((g) => g.rows.length > 0));

        function rowForTyped(e: VaultEntity): ListGroup["rows"][number] {
            const sub = e.entity_type === "edition"
                ? SERIES_LABELS[(e.file_path || "").split("/")[1]] || null
                : e.sector_classification;
            return {
                path: e.file_path as string,
                title: e.name.replace(/_/g, " "),
                sub,
                chip: null,
                snippet: "",
                sim: null,
            };
        }
    }

    // --- search ------------------------------------------------------------ //

    debounceDocsSearch(): void {
        clearTimeout(this.docsSearchTimeout);
        this.docsSearchTimeout = setTimeout(() => {
            void this.runSearch();
        }, 300);
    }

    /** Search the ACTIVE collection's corpus and render the hits. */
    async runSearch(): Promise<void> {
        const query = (getEl("docs-search") as HTMLInputElement).value.trim();
        this.activePath = null;
        this.hideContentPane();
        if (!query) {
            void this.loadCatalog();
            return;
        }
        if (this.collection === "vault") {
            await this.runVaultSearch(query);
            return;
        }
        try {
            const url = `/api/docs/search?q=${encodeURIComponent(query)}`;
            const data = await fetchJson<DocSearchResponse>(url);
            this.renderGroups([{
                label: null,
                rows: data.results.map((r) => ({
                    path: r.path,
                    title: r.title,
                    // Prefer the matched section's own title (deep-link
                    // context) over the bare directory; anchor rides the chip.
                    sub: r.section_title || r.section || null,
                    chip: r.anchor !== null && r.anchor !== undefined ? `L${r.anchor}` : null,
                    snippet: r.snippet,
                    sim: r.similarity ?? null,
                })),
            }]);
            const total = data.results.length;
            const mode = data.mode ? ` · ${data.mode}` : "";
            const stale = data.stale ? " · stale (scan)" : "";
            getEl("docs-count").textContent =
                total === 0
                    ? `No matches${stale}`
                    : `${total} match${total === 1 ? "" : "es"}${mode}${stale}`;
        } catch (error) {
            console.error("Error searching docs:", error);
            getEl("docs-list").innerHTML =
                '<div class="no-results">Search failed. Try again.</div>';
        }
    }

    /** FTS (optionally hybrid) search over every findata/ note body. */
    private async runVaultSearch(query: string): Promise<void> {
        const hybrid = (getEl("hybrid-search") as HTMLInputElement).checked;
        const url = `/api/search?q=${encodeURIComponent(query)}&limit=50${hybrid ? "&hybrid=1" : ""}`;
        try {
            const data = await fetchJson<SearchResponse>(url);
            this.renderGroups([{
                label: null,
                rows: data.results.map((r) => ({
                    path: r.file_path,
                    // note_search titles are stems for companies — prettify.
                    title: (r.title ?? "(untitled)").replace(/_/g, " "),
                    sub: r.sector,
                    chip: DOCTYPE_LABELS[r.doc_type] || r.doc_type,
                    snippet: r.snippet,
                    sim: hybrid ? r.similarity : null,
                })),
            }]);
            getEl("docs-count").textContent =
                `${data.total_count.toLocaleString()} match${data.total_count === 1 ? "" : "es"}`
                + (hybrid ? " · hybrid" : "");
        } catch (error) {
            console.error("Error searching vault:", error);
            getEl("docs-list").innerHTML =
                `<div class="no-results">Search failed: ${escapeHtml(
                    error instanceof Error ? error.message : "unknown error")}</div>`;
        }
    }

    // --- sidebar rendering -------------------------------------------------- //

    /** Render grouped rows; group headers are mono section labels. */
    renderGroups(groups: ListGroup[]): void {
        const list = getEl("docs-list");
        list.innerHTML = "";
        const rowCount = groups.reduce((n, g) => n + g.rows.length, 0);
        if (rowCount === 0) {
            list.innerHTML = '<div class="no-results">No documents match.</div>';
            return;
        }
        for (const group of groups) {
            if (group.label) {
                const head = document.createElement("div");
                head.className = "docs-group-h";
                head.textContent = group.label;
                list.appendChild(head);
            }
            for (const item of group.rows) {
                list.appendChild(this.buildRow(item));
            }
        }
    }

    /** Kept for the S2 public surface (flat lists = one label-less group). */
    renderList(items: { path: string; name: string; section: string; title: string; snippet: string }[]): void {
        this.renderGroups([{
            label: null,
            rows: items.map((i) => ({
                path: i.path, title: i.title, sub: i.section || null,
                chip: null, snippet: i.snippet, sim: null,
            })),
        }]);
    }

    private buildRow(item: ListGroup["rows"][number]): HTMLButtonElement {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "docs-row";
        row.dataset.path = item.path;
        const chip = item.chip
            ? `<span class="doctype-chip">${escapeHtml(item.chip)}</span>`
            : "";
        const sub = item.sub
            ? `<span class="docs-row-section">${escapeHtml(item.sub)}</span>`
            : "";
        const snippet = item.snippet
            ? `<div class="docs-row-snippet">${highlightSnippet(item.snippet)}</div>`
            : "";
        const sim = item.sim != null
            ? `<span class="docs-row-sim">${(item.sim * 100).toFixed(0)}%</span>`
            : "";
        row.innerHTML = `
            <span class="docs-row-title">${chip}${escapeHtml(item.title)}</span>
            ${sub}${sim}${snippet}
        `;
        row.addEventListener("click", () => {
            if (this.collection === "vault") void this.openNote(item.path);
            else void this.openDoc(item.path);
        });
        return row;
    }

    private markActiveRow(path: string): void {
        document.querySelectorAll<HTMLElement>(".docs-row").forEach((row) => {
            row.classList.toggle("active", row.dataset.path === path);
        });
    }

    // --- reader ------------------------------------------------------------- //

    /** Fetch + render one doc/ file (raw markdown/text, desk catalog source). */
    async openDoc(path: string): Promise<void> {
        this.activePath = path;
        this.markActiveRow(path);
        try {
            const url = `/api/docs/content?path=${encodeURIComponent(path)}`;
            const data = await fetchJson<DocContentResponse>(url);
            const { html, headings } = processRichContent(data.content);
            getEl("docs-content-empty").style.display = "none";
            const pane = getEl("docs-content-pane");
            pane.style.display = "block";
            pane.innerHTML = `
                <div class="reader-grid">
                    <div class="reader-main">
                        <header class="docs-article-header">
                            <h3>${escapeHtml(data.title)}</h3>
                            <div class="docs-article-meta">
                                <span>${escapeHtml(data.path)}</span>
                                <span>${escapeHtml(data.section || "top-level")}</span>
                                <span>${formatBytes(data.size_bytes)}</span>
                            </div>
                        </header>
                        <div class="docs-article-body">${html}</div>
                    </div>
                    <aside class="reader-rail">
                        ${headings.length > 1 ? this.renderToc(headings) : ""}
                    </aside>
                </div>
            `;
            wireRichInteractions(pane);
        } catch (error) {
            console.error("Error opening doc:", error);
            this.readerError();
        }
    }

    /** Fetch + render one vault note (paper register, wikilinks, related rail). */
    async openNote(filePath: string): Promise<void> {
        this.activePath = filePath;
        this.markActiveRow(filePath);
        // Reading a vault note implies the vault index; build it lazily so
        // wikilinks resolve even when the note was reached from doc/ search.
        void this.ensureVault().catch(() => undefined);
        try {
            const url = `/api/entity/${encodeURIComponent(filePath)}`;
            const entity = await fetchJson<EntityDetailResponse>(url);
            const fm = entity.frontmatter;
            const isEdition = entity.entity_type === "edition"
                || this.fmString(fm, "type") === "newsletter";
            const { html, headings } = processRichContent(entity.content);
            getEl("docs-content-empty").style.display = "none";
            const pane = getEl("docs-content-pane");
            pane.style.display = "block";
            pane.innerHTML = `
                <div class="reader-grid">
                    <div class="reader-main">
                        ${isEdition ? this.renderMasthead(entity) : this.renderChips(entity)}
                        <div class="docs-article-body">${html}</div>
                    </div>
                    <aside class="reader-rail">
                        ${headings.length > 1 ? this.renderToc(headings) : ""}
                        <div class="reader-related" id="reader-related"></div>
                    </aside>
                </div>
            `;
            wireRichInteractions(pane);
            this.linkifyWikilinks(pane);
            void this.loadRelatedRail(entity, isEdition);
        } catch (error) {
            console.error("Error opening note:", error);
            this.readerError();
        }
    }

    private readerError(): void {
        getEl("docs-content-pane").style.display = "none";
        getEl("docs-content-empty").style.display = "block";
        getEl("docs-content-empty").innerHTML =
            '<p class="error">Could not load this document.</p>';
    }

    /** Edition masthead: publication / issue / provenance between double rules. */
    private renderMasthead(entity: EntityDetailResponse): string {
        const fm = entity.frontmatter;
        const series = SERIES_LABELS[(entity.file_path || "").split("/")[1]];
        const title = this.fmString(fm, "title")
            ?? entity.name.replace(/_/g, " ");
        const bits: string[] = [];
        const publisher = this.fmPublisher(fm);
        if (publisher) bits.push(escapeHtml(publisher));
        const generated = this.fmGeneratedAt(fm);
        if (generated) bits.push(`generated ${escapeHtml(generated)}`);
        const stale = this.fmScalar(fm, "stale_after");
        if (stale) bits.push(`fresh through ${escapeHtml(stale)}`);
        return `
            <header class="edition-masthead">
                <div class="masthead-pub">${escapeHtml(series || "Newsletter")}</div>
                <h3 class="masthead-title">${escapeHtml(title)}</h3>
                ${bits.length ? `<div class="masthead-meta">${bits.join(' <span class="dot">·</span> ')}</div>` : ""}
            </header>
        `;
    }

    /** Non-edition header: title + frontmatter chips in the mono data voice. */
    private renderChips(entity: EntityDetailResponse): string {
        const fm = entity.frontmatter;
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
        const title = this.fmString(fm, "title") ?? entity.name.replace(/_/g, " ");
        return `
            <header class="docs-article-header">
                <h3>${escapeHtml(title)}</h3>
                <div class="fm-chips">${chips.join("")}</div>
                <div class="docs-article-meta">
                    <span>${escapeHtml(entity.file_path || entity.name)}</span>
                </div>
            </header>
        `;
    }

    // --- wikilinks ------------------------------------------------------------ //

    /**
     * Rewrite [[target]] / [[target|label]] text into in-place nav links.
     * DOM-level pass so code blocks, existing links and tags are never
     * touched; unresolved targets render as muted non-links.
     */
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
                    anchor.href = "#";
                    anchor.dataset.href = href;
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

    // --- related rail ---------------------------------------------------------- //

    /** Similar-notes + (editions) featured-companies rail content. */
    private async loadRelatedRail(entity: EntityDetailResponse, isEdition: boolean): Promise<void> {
        const mount = document.getElementById("reader-related");
        if (!mount || !entity.file_path) return;
        const parts: string[] = [];
        try {
            const similar = await fetchJson<SimilarNotesResponse>(
                `/api/graph/similar/${encodeURIComponent(entity.file_path)}?k=6`);
            if (similar.neighbors.length) {
                parts.push('<h4><i class="fas fa-clone"></i> Similar notes</h4>');
                parts.push(...similar.neighbors.map((n) => this.relatedRow(n)));
            }
        } catch {
            // Unembedded note (or DuckDB cold) — the rail just stays sparse.
        }
        if (isEdition) {
            const stem = (entity.file_path.split("/").pop() || "").replace(/\.md$/i, "");
            try {
                const companies = await fetchJson<EditionCompaniesResponse>(
                    `/api/graph/edition_companies?edition=${encodeURIComponent(stem)}&k=8`);
                if (companies.companies.length) {
                    parts.push('<h4><i class="fas fa-building"></i> Companies in this edition</h4>');
                    parts.push(...companies.companies.map((n) => this.relatedRow(n)));
                }
            } catch {
                // Edition not embedded / unresolvable — quiet.
            }
        }
        mount.innerHTML = parts.length
            ? parts.join("")
            : '<p class="hint">No related notes.</p>';
        mount.querySelectorAll<HTMLElement>("[data-note]").forEach((el) => {
            el.addEventListener("click", () => {
                const note = el.dataset.note;
                if (note) void this.openNote(note);
            });
        });
    }

    private relatedRow(n: SimilarNeighbor): string {
        const pct = Math.round(n.similarity * 100);
        return `
            <button type="button" class="related-row" data-note="${escapeHtml(n.file_path)}"
                    title="${escapeHtml(n.file_path)}">
                <span class="related-title">${escapeHtml(n.title)}</span>
                <span class="related-sim"><span class="related-bar"><span
                    class="bar-fill" style="width:${pct}%"></span></span>${pct}%</span>
            </button>
        `;
    }

    // --- shared helpers --------------------------------------------------------- //

    /** Simple TOC linking the <h1..h6 id> headings marked.js produces. */
    renderToc(headings: { level: number; text: string; id: string }[]): string {
        const items = headings
            .map((h) => `<li class="toc-${h.level}"><a href="#${encodeURIComponent(h.id)}">${escapeHtml(h.text)}</a></li>`)
            .join("");
        return `<nav class="docs-toc"><h4>On this page</h4><ul>${items}</ul></nav>`;
    }

    /** Reset the reader pane to its empty state. */
    hideContentPane(): void {
        getEl("docs-content-pane").style.display = "none";
        getEl("docs-content-pane").innerHTML = "";
        const empty = getEl("docs-content-empty");
        empty.style.display = "block";
        empty.innerHTML = `
            <i class="fas fa-book-open"></i>
            <p>Select a document to read it here.</p>
            <p class="hint">Browse the doc/ and vault collections, or search —
               flip on hybrid for semantic rerank.</p>
        `;
    }

    private fmString(fm: NoteFrontmatter, key: string): string | null {
        const v = fm[key];
        return typeof v === "string" && v.trim() ? v : null;
    }

    /** Scalar frontmatter value, dates sliced to YYYY-MM-DD. */
    private fmScalar(fm: NoteFrontmatter, key: string): string | null {
        const v = this.fmString(fm, key);
        if (v === null) return null;
        return /^\d{4}-\d{2}-\d{2}T/.test(v) ? v.slice(0, 10) : v;
    }

    /** generated.at from the nested frontmatter block. */
    private fmGeneratedAt(fm: NoteFrontmatter): string | null {
        const g = fm.generated;
        if (g && typeof g === "object" && "at" in g) {
            const at = (g as { at?: unknown }).at;
            if (typeof at === "string" && at) return at.slice(0, 10);
        }
        return null;
    }

    /** publisher/<name> tag → capitalized label. */
    private fmPublisher(fm: NoteFrontmatter): string | null {
        const tags = Array.isArray(fm.tags)
            ? fm.tags.filter((t): t is string => typeof t === "string")
            : [];
        for (const tag of tags) {
            if (tag.startsWith("publisher/")) {
                return tag.slice("publisher/".length)
                    .replace(/\b\w/g, (c) => c.toUpperCase());
            }
        }
        return null;
    }
}
