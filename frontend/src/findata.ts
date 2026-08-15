// FinData Knowledge Graph Viewer — TypeScript source.
//
// Built by `make frontend` (esbuild) into static/findata.bundle.js, which the
// Flask app serves via templates/findata.html. DO NOT edit the bundle; edit
// this file and rebuild.
//
// Runtime contract preserved from the original vanilla-JS file:
//   - The script tag has no `defer`/`type=module`; it runs immediately at the
//     end of <body>, so the DOM is already parsed when the constructor runs.
//   - The bundle is an IIFE (not an ES module) and re-attaches the instance
//     to `window.viewer` so the inline onclick handlers in dynamically-built
//     HTML (e.g. `onclick="viewer.goToPage(2)"`) resolve at runtime.

import type {
    CompanyNeighbors,
    EntitiesResponse,
    EntityListItem,
    GraphRefreshResponse,
    NeighborsBundle,
    SectorNeighbors,
    SectorsResponse,
    SearchResponse,
    ShortestPathResponse,
    StatsResponse,
} from "../types/api";

/** Union of the entity-type strings the view can be centred on. */
type ViewName = "companies" | "sectors" | "stats" | "graph";

/** Union of the graph relationship filters from the #graph-filter dropdown. */
type GraphFilter = "all" | "peers" | "jv" | "acquired" | "subsidiary" | "supply";

/** A generated heading captured during markdown processing (for the TOC). */
interface TocHeading {
    level: number;
    text: string;
    id: string;
}

/** Result of processRichContent — HTML ready to inject + the TOC headings. */
interface ProcessedContent {
    html: string;
    headings: TocHeading[];
}

/**
 * cytoscape element (node or edge) as built by the ego-network builders.
 * Matches the CyElementInput vendor shape so the consumer is typed. `group` is
 * optional because edges don't carry one (only nodes are grouped for styling).
 */
interface GraphElement {
    data: {
        id: string;
        label: string;
        group?: string;
        centrality?: number;
        source?: string;
        target?: string;
        type?: string;
        props?: Record<string, unknown>;
    };
}

/** Lazy graph-tab state, initialized on first visit to the Graph view. */
interface GraphState {
    cy: CyInstance | null;
    central: string | null;
    elements: GraphElement[] | null;
    entitiesLoaded: boolean;
    entityType?: "sector" | "company";
}

// `viewer` is referenced as a bare global by inline onclick handlers in the
// HTML strings this file builds. Declare it on window so those references are
// navigable + typo-checked, and so the bottom assignment type-checks.
// `hljs`/`Prism` are CDN-loaded library globals (see types/vendors.d.ts for
// their declared shapes); they're optional (feature-detected at the call site).
declare global {
    interface Window {
        viewer: FinDataViewer;
        hljs?: {
            highlight(code: string, opts: { language: string }): HljsResult;
        };
        Prism?: {
            highlightAll(): void;
        };
    }
}

/**
 * `document.getElementById` narrowed to non-null. The original vanilla-JS file
 * assumed every queried element exists; this preserves that assumption. The
 * script tag runs at end of <body> (no defer/module), so the static HTML in
 * templates/findata.html is always parsed before the constructor runs.
 *
 * Use `getEl<T>()` with a specific element subtype when the caller needs typed
 * access (e.g. `getEl<HTMLInputElement>("search-input")` for `.value`).
 */
function getEl<T extends HTMLElement = HTMLElement>(id: string): T {
    const node = document.getElementById(id);
    if (!node) {
        throw new Error(`expected element #${id} not found in DOM`);
    }
    return node as T;
}

class FinDataViewer {
    // --- view + pagination state ------------------------------------------ //
    currentView: ViewName = "companies";
    currentLayout: "grid" | "list" = "grid";
    currentPage: number = 0;
    pageSize: number = 20;
    totalCount: number = 0;

    /**
     * 'entities' (default) queries /api/entities by name+tag; 'content'
     * queries the FTS5 /api/search endpoint over note bodies + newsletters.
     */
    searchMode: "entities" | "content" = "entities";
    filters: { search: string; sector: string; type: string; marketcap: string } = {
        search: "",
        sector: "",
        type: "",
        marketcap: "",
    };

    // Debounce timer handle for the search input (number in the browser).
    private searchTimeout: ReturnType<typeof setTimeout> | undefined;

    // --- graph-tab state (lazy-initialized in loadGraphView) -------------- //
    graph: GraphState | null = null;

    constructor() {
        this.init();
    }

    init(): void {
        this.bindEvents();
        this.loadInitialData();
    }

    bindEvents(): void {
        // Navigation - only prevent default for navigation links, not all links.
        document.querySelectorAll<HTMLAnchorElement>(".nav-link").forEach((link) => {
            link.addEventListener("click", (e) => {
                e.preventDefault();
                this.switchView(link.dataset.view as ViewName);
            });
        });

        // Search.
        const searchInput = getEl("search-input") as HTMLInputElement;
        const clearSearch = getEl("clear-search") as HTMLElement;

        searchInput.addEventListener("input", (e) => {
            const target = e.target as HTMLInputElement;
            this.filters.search = target.value;
            clearSearch.style.display = target.value ? "block" : "none";
            this.debounceSearch();
        });

        clearSearch.addEventListener("click", () => {
            searchInput.value = "";
            this.filters.search = "";
            clearSearch.style.display = "none";
            this.searchEntities();
        });

        // Search-mode toggle: Entities (name+tag via /api/entities) vs Content
        // (full-text via the FTS5 /api/search over note bodies + newsletters).
        // Switching mode re-runs the current query so the user immediately sees
        // the difference. Content mode only applies to the companies view
        // (the only view with a results container + pagination); other views
        // ignore the toggle.
        getEl("mode-entities").addEventListener("click", () => {
            this.setSearchMode("entities");
        });
        getEl("mode-content").addEventListener("click", () => {
            this.setSearchMode("content");
        });

        // Filters.
        const sectorFilter = getEl("sector-filter") as HTMLSelectElement;
        sectorFilter.addEventListener("change", (e) => {
            this.filters.sector = (e.target as HTMLSelectElement).value;
            // When filtering by sector in companies view, ensure we only show companies.
            if (this.currentView === "companies") {
                this.filters.type = "company";
            }
            this.searchEntities();
        });

        const typeFilter = getEl("type-filter") as HTMLSelectElement;
        typeFilter.addEventListener("change", (e) => {
            this.filters.type = (e.target as HTMLSelectElement).value;
            this.searchEntities();
        });

        const marketcapFilter = getEl("marketcap-filter") as HTMLSelectElement;
        marketcapFilter.addEventListener("change", (e) => {
            this.filters.marketcap = (e.target as HTMLSelectElement).value;
            this.searchEntities();
        });

        // View controls.
        getEl("grid-view").addEventListener("click", () => {
            this.setLayout("grid");
        });
        getEl("list-view").addEventListener("click", () => {
            this.setLayout("list");
        });

        // Lightbox.
        getEl("close-lightbox").addEventListener("click", () => {
            this.closeLightbox();
        });
        getEl("image-lightbox").addEventListener("click", (e) => {
            if ((e.target as HTMLElement).id === "image-lightbox") {
                this.closeLightbox();
            }
        });
    }

    debounceSearch(): void {
        clearTimeout(this.searchTimeout);
        this.searchTimeout = setTimeout(() => {
            this.searchEntities();
        }, 300);
    }

    async loadInitialData(): Promise<void> {
        await this.loadSectors();
        await this.loadStats();
        await this.loadEntities();
    }

    async loadSectors(): Promise<void> {
        try {
            const response = await fetch("/api/sectors");
            const data: SectorsResponse = await response.json();

            const sectorFilter = getEl("sector-filter") as HTMLSelectElement;
            data.classifications.forEach((sector) => {
                const option = document.createElement("option");
                option.value = sector;
                option.textContent = sector;
                sectorFilter.appendChild(option);
            });

            if (this.currentView === "sectors") {
                this.displaySectors(data);
            }
        } catch (error) {
            console.error("Error loading sectors:", error);
        }
    }

    async loadStats(): Promise<void> {
        try {
            const response = await fetch("/api/stats");
            const data: StatsResponse = await response.json();

            if (this.currentView === "stats") {
                this.displayStats(data);
            }
        } catch (error) {
            console.error("Error loading stats:", error);
        }
    }

    async loadEntities(resetPage = true): Promise<void> {
        if (resetPage) {
            this.currentPage = 0;
        }

        this.showLoading(true);

        try {
            // Build params, ensuring type filter for companies view.
            const params = new URLSearchParams({
                limit: String(this.pageSize),
                offset: String(this.currentPage * this.pageSize),
                ...this.filters,
            });

            // Ensure we only show companies in companies view.
            if (this.currentView === "companies" && !params.has("type")) {
                params.set("type", "company");
            }

            const response = await fetch(`/api/entities?${params}`);
            const data: EntitiesResponse = await response.json();

            this.totalCount = data.total_count;

            if (this.currentView === "companies") {
                this.displayEntities(data.entities);
                this.updatePagination();
                this.updateCount();
            }
        } catch (error) {
            console.error("Error loading entities:", error);
            this.showError("Failed to load entities");
        } finally {
            this.showLoading(false);
        }
    }

    searchEntities(): void {
        // Content mode is a distinct code path: it queries the FTS5
        // /api/search endpoint (note bodies + newsletters) and renders a
        // polymorphic result list with snippets — structurally different from
        // the entity-card grid. Only relevant in the companies view (the only
        // view with a results container + pagination).
        if (this.searchMode === "content" && this.currentView === "companies") {
            this.performContentSearch();
        } else {
            this.loadEntities();
        }
    }

    setSearchMode(mode: "entities" | "content"): void {
        if (mode === this.searchMode) return;
        this.searchMode = mode;
        // Toggle button styling.
        getEl("mode-entities").classList.toggle("active", mode === "entities");
        getEl("mode-content").classList.toggle("active", mode === "content");
        // In content mode the sector/type/marketcap entity-filters don't apply
        // (FTS has its own type filter on doc_type); dim them to signal that.
        const filtersApply = mode === "entities";
        document.querySelectorAll<HTMLSelectElement>(".filters select").forEach((sel) => {
            sel.disabled = !filtersApply;
        });
        // Re-run the current query in the new mode so the switch is immediate.
        this.currentPage = 0;
        this.searchEntities();
    }

    async performContentSearch(): Promise<void> {
        const q = (this.filters.search || "").trim();
        this.showLoading(true);
        try {
            const params = new URLSearchParams({
                q,
                limit: String(this.pageSize),
                offset: String(this.currentPage * this.pageSize),
            });
            const response = await fetch(`/api/search?${params}`);
            if (response.status === 503) {
                // FTS index not built — surface a clear message, don't 500 the UI.
                this.displayContentResults([], 0,
                    "Search index not built. Run: python3 helpers/maintenance/rebuild_note_search.py");
                return;
            }
            if (!response.ok) {
                const err = await response.json().catch((): { error?: string } => ({}));
                this.displayContentResults([], 0,
                    err.error || `Search failed (HTTP ${response.status})`);
                return;
            }
            const data: SearchResponse = await response.json();
            this.totalCount = data.total_count;
            this.displayContentResults(data.results, data.total_count);
            this.updatePagination();
            this.updateCount();
        } catch (error) {
            console.error("Error in content search:", error);
            this.showError("Content search failed");
        } finally {
            this.showLoading(false);
        }
    }

    displayContentResults(results: SearchResponse["results"], totalCount: number, errorMessage: string | null = null): void {
        const container = getEl("companies-container");
        if (errorMessage) {
            container.innerHTML = `<div class="no-results">${this.escapeHtml(errorMessage)}</div>`;
            return;
        }
        if (!results || results.length === 0) {
            container.innerHTML = '<div class="no-results">No content matches. Try another term, or switch to Entities mode.</div>';
            return;
        }

        // One card per FTS hit. Renders title + a doc_type badge + sector +
        // the highlighted snippet (FTS snippet already carries <mark> tags;
        // escapeHtml would strip them, so escape the raw text first then
        // reintroduce the <mark> tags safely).
        container.innerHTML = "";
        results.forEach((hit) => {
            const card = document.createElement("div");
            card.className = "entity-card content-search-card";
            const docLabel: Record<string, string> = {
                company: "Company", sector: "Sector", super_sector: "Super-Sector",
                chatter: "Newsletter (Chatter)", points_and_figures: "Newsletter (P&F)",
                plotlines: "Newsletter (Plotlines)",
            };
            const safeSnippet = this.highlightSnippet(hit.snippet);
            const viewLink = hit.file_path
                ? `<a href="/entity/${encodeURIComponent(hit.file_path)}" class="btn-primary" target="_blank" rel="noopener noreferrer"><i class="fas fa-external-link-alt"></i> View note</a>`
                : "";
            card.innerHTML = `
                <div class="card-header">
                    <h3 class="card-title">${this.escapeHtml(hit.title || "(untitled)")}</h3>
                    <div class="card-type"><span class="doc-badge">${this.escapeHtml(docLabel[hit.doc_type] || hit.doc_type)}</span></div>
                </div>
                <div class="card-body">
                    ${hit.sector ? `<div class="card-sector"><i class="fas fa-industry"></i><span>${this.escapeHtml(hit.sector)}</span></div>` : ""}
                    <div class="content-snippet">${safeSnippet}</div>
                </div>
                ${viewLink ? `<div class="card-footer">${viewLink}</div>` : ""}
            `;
            container.appendChild(card);
        });
    }

    // The FTS snippet comes back with literal <mark>...</mark> tags wrapping
    // matches. escapeHtml() would escape those into visible text. Instead,
    // escape everything, then restore the markers by re-splitting on the
    // (now-escaped) marker text.
    highlightSnippet(snippet: string): string {
        if (!snippet) return "";
        // Temporarily mark match boundaries, escape, then convert markers
        // back to real <mark> tags.
        const OPEN = "\u0001"; // unlikely control chars as sentinels
        const CLOSE = "\u0002";
        const marked = String(snippet)
            .replace(/<mark>/g, OPEN)
            .replace(/<\/mark>/g, CLOSE);
        const escaped = this.escapeHtml(marked);
        return escaped
            .replace(/\u0001/g, "<mark>")
            .replace(/\u0002/g, "</mark>");
    }

    displayEntities(entities: EntityListItem[]): void {
        const container = getEl("companies-container");
        container.innerHTML = "";

        if (entities.length === 0) {
            container.innerHTML = '<div class="no-results">No companies found</div>';
            return;
        }

        entities.forEach((entity) => {
            const card = this.createEntityCard(entity);
            container.appendChild(card);
        });
    }

    createEntityCard(entity: EntityListItem): HTMLElement {
        const card = document.createElement("div");
        card.className = "entity-card";

        const tags = entity.enhanced_tags || [];
        const marketCapTag = tags.find((tag) => tag.startsWith("market_cap/"));
        const geographyTag = tags.find((tag) => tag.startsWith("geography/"));

        card.innerHTML = `
            <div class="card-header">
                <h3 class="card-title">${this.escapeHtml(entity.name)}</h3>
                <div class="card-type">
                    <i class="fas fa-building"></i>
                    <span>${entity.entity_type || "company"}</span>
                </div>
            </div>
            <div class="card-body">
                <div class="card-sector">
                    <i class="fas fa-industry"></i>
                    <span>${entity.sector_classification || "Unknown"}</span>
                </div>
                ${marketCapTag ? `<div class="card-market-cap">
                    <i class="fas fa-chart-line"></i>
                    <span>${this.escapeHtml(marketCapTag.replace("market_cap/", "").replace("_", " "))}</span>
                </div>` : ""}
                ${geographyTag ? `<div class="card-geography">
                    <i class="fas fa-globe"></i>
                    <span>${this.escapeHtml(geographyTag.replace("geography/", ""))}</span>
                </div>` : ""}
            </div>
            <div class="card-footer">
                <a href="/entity/${entity.file_path}" class="btn-primary" target="_blank" rel="noopener noreferrer">
                    <i class="fas fa-external-link-alt"></i> View Details
                </a>
            </div>
        `;

        return card;
    }

    displaySectors(data: SectorsResponse): void {
        const container = getEl("sectors-container");
        container.innerHTML = "";

        // Display sector classifications.
        const classificationsDiv = document.createElement("div");
        classificationsDiv.className = "sector-classifications";
        classificationsDiv.innerHTML = "<h3>Sector Classifications</h3>";

        const grid = document.createElement("div");
        grid.className = "sector-tags";

        data.classifications.forEach((sector) => {
            const tag = document.createElement("span");
            tag.className = "sector-tag";
            tag.textContent = sector;
            tag.addEventListener("click", () => {
                this.filters.sector = sector;
                (getEl("sector-filter") as HTMLSelectElement).value = sector;
                this.switchView("companies");
            });
            grid.appendChild(tag);
        });

        classificationsDiv.appendChild(grid);
        container.appendChild(classificationsDiv);

        // Display sector entities.
        if (data.sector_entities.length > 0) {
            const entitiesDiv = document.createElement("div");
            entitiesDiv.className = "sector-entities";
            entitiesDiv.innerHTML = "<h3>Sector Analysis</h3>";

            data.sector_entities.forEach((sector) => {
                const card = this.createSectorCard(sector);
                entitiesDiv.appendChild(card);
            });

            container.appendChild(entitiesDiv);
        }

        getEl("sectors-count").textContent = `${data.classifications.length} classifications`;
    }

    createSectorCard(sector: SectorsResponse["sector_entities"][number]): HTMLElement {
        const card = document.createElement("div");
        card.className = "sector-card";

        card.innerHTML = `
            <div class="card-header">
                <h3 class="card-title">${this.escapeHtml(sector.name)}</h3>
                <div class="card-type">
                    <i class="fas fa-industry"></i>
                    <span>Sector</span>
                </div>
            </div>
            <div class="card-body">
                <div class="card-content">
                    ${this.truncateText(sector.content, 150)}
                </div>
            </div>
            <div class="card-footer">
                <a href="/entity/${encodeURIComponent(sector.file_path)}" class="btn-primary" target="_blank" rel="noopener noreferrer">
                    <i class="fas fa-external-link-alt"></i> Read Analysis
                </a>
            </div>
        `;

        return card;
    }

    displayStats(data: StatsResponse): void {
        const container = getEl("stats-container");
        container.innerHTML = "";

        // Total entities.
        const totalCard = this.createStatCard("Total Entities", data.total_entities, "fas fa-database", "primary");
        container.appendChild(totalCard);

        // Entity types.
        const typesCard = this.createStatCard("Entity Types", Object.keys(data.entity_counts).length, "fas fa-tags", "secondary");
        container.appendChild(typesCard);

        // Top sectors.
        const sectorsCard = this.createStatCard("Sectors", Object.keys(data.top_sectors).length, "fas fa-industry", "success");
        container.appendChild(sectorsCard);

        // Market cap distribution.
        const marketCapCard = this.createStatCard("Market Cap Categories", Object.keys(data.market_cap_counts).length, "fas fa-chart-line", "warning");
        container.appendChild(marketCapCard);

        // Detailed breakdowns.
        const breakdownSection = document.createElement("div");
        breakdownSection.className = "stats-breakdown";

        breakdownSection.appendChild(this.createBreakdownCard("Entity Types", data.entity_counts, "entity_type"));
        breakdownSection.appendChild(this.createBreakdownCard("Top Sectors", data.top_sectors, "sector"));
        breakdownSection.appendChild(this.createBreakdownCard("Market Cap Distribution", data.market_cap_counts, "market_cap"));

        container.appendChild(breakdownSection);
    }

    createStatCard(title: string, value: number, icon: string, theme: string): HTMLElement {
        const card = document.createElement("div");
        card.className = `stat-card stat-${theme}`;
        card.innerHTML = `
            <div class="stat-icon">
                <i class="${icon}"></i>
            </div>
            <div class="stat-content">
                <h3>${value.toLocaleString()}</h3>
                <p>${title}</p>
            </div>
        `;
        return card;
    }

    createBreakdownCard(title: string, data: Record<string, number>, type: string): HTMLElement {
        const card = document.createElement("div");
        card.className = "breakdown-card";

        const total = Object.values(data).reduce((a, b) => a + b, 0);
        let itemsHtml = "";
        Object.entries(data).forEach(([key, value]) => {
            const percentage = total > 0 ? ((value / total) * 100).toFixed(1) : "0.0";
            itemsHtml += `
                <div class="breakdown-item">
                    <span class="breakdown-label">${this.escapeHtml(this.formatLabel(key, type))}</span>
                    <span class="breakdown-value">${value}</span>
                    <span class="breakdown-percentage">${percentage}%</span>
                </div>
            `;
        });

        card.innerHTML = `
            <h4>${title}</h4>
            <div class="breakdown-items">
                ${itemsHtml}
            </div>
        `;

        return card;
    }

    formatLabel(key: string, type: string): string {
        if (type === "market_cap") {
            return key.replace("_", " ").replace(/\b\w/g, (l) => l.toUpperCase());
        }
        return key;
    }

    processRichContent(content: string): ProcessedContent {
        // Enhanced markdown processing with rich media support.
        let processedHtml = marked.parse(content);

        // Extract headings for TOC.
        const headings: TocHeading[] = [];
        processedHtml = processedHtml.replace(/<h([1-6])[^>]*>(.*?)<\/h[1-6]>/gi, (match, level: string, text: string) => {
            const id = text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
            headings.push({
                level: parseInt(level, 10),
                text: text.replace(/<[^>]*>/g, ""),
                id: id,
            });
            return `<h${level} id="${id}">${text}</h${level}>`;
        });

        // Process images for lightbox.
        processedHtml = processedHtml.replace(/<img([^>]+)src="([^"]+)"([^>]*)>/gi, (_match, before: string, src: string, after: string) => {
            const imgId = `img-${Math.random().toString(36).substring(2, 11)}`;
            return `<img${before}src="${src}"${after} class="rich-image" data-img-id="${imgId}" onclick="viewer.openLightbox('${src}')" loading="lazy">`;
        });

        // Process code blocks with syntax highlighting.
        processedHtml = processedHtml.replace(/<pre><code class="language-(\w+)">([\s\S]*?)<\/code><\/pre>/gi, (_match, lang: string, code: string) => {
            const codeId = `code-${Math.random().toString(36).substring(2, 11)}`;
            const highlightedCode = this.highlightCode(code, lang);
            return `
                <div class="code-block">
                    <div class="code-header">
                        <span class="code-language">${lang}</span>
                        <button class="code-copy" onclick="viewer.copyCode('${codeId}')" title="Copy code">
                            <i class="fas fa-copy"></i>
                        </button>
                    </div>
                    <pre><code id="${codeId}" class="language-${lang}">${highlightedCode}</code></pre>
                </div>
            `;
        });

        // Process inline code.
        processedHtml = processedHtml.replace(/<code>([\s\S]*?)<\/code>/gi, '<code class="inline-code">$1</code>');

        // Process tables with responsive design.
        processedHtml = processedHtml.replace(/<table([^>]*)>([\s\S]*?)<\/table>/gi, (_match, attributes: string, tableContent: string) => {
            return `
                <div class="table-wrapper">
                    <table${attributes}>${tableContent}</table>
                </div>
            `;
        });

        // Process blockquotes.
        processedHtml = processedHtml.replace(/<blockquote>([\s\S]*?)<\/blockquote>/gi, '<blockquote class="rich-blockquote">$1</blockquote>');

        // Add responsive embeds for external content.
        processedHtml = this.processExternalContent(processedHtml);

        return {
            html: processedHtml,
            headings: headings,
        };
    }

    generateTableOfContents(headings: TocHeading[]): string {
        if (headings.length === 0) return '<p class="toc-empty">No headings found</p>';

        let toc = '<ul class="toc-list">';
        let currentLevel = 0;

        headings.forEach((heading) => {
            if (heading.level > currentLevel) {
                toc += '<ul class="toc-nested">';
            } else if (heading.level < currentLevel) {
                toc += "</ul>".repeat(currentLevel - heading.level);
            }

            toc += `
                <li class="toc-item toc-level-${heading.level}">
                    <a href="#${heading.id}" class="toc-link">
                        ${this.escapeHtml(heading.text)}
                    </a>
                </li>
            `;

            currentLevel = heading.level;
        });

        // Close any remaining nested lists.
        toc += "</ul>".repeat(currentLevel);
        toc += "</ul>";

        return toc;
    }

    highlightCode(code: string, language: string): string {
        try {
            if (window.hljs) {
                return window.hljs.highlight(code, { language }).value;
            }
        } catch (e) {
            console.warn("Syntax highlighting failed:", e);
        }
        return this.escapeHtml(code);
    }

    copyCode(codeId: string): void {
        const codeElement = getEl(codeId);
        if (codeElement) {
            const text = codeElement.textContent;
            navigator.clipboard.writeText(text || "").then(() => {
                this.showToast("Code copied to clipboard!", "success");
            }).catch((err) => {
                console.error("Failed to copy code:", err);
                this.showToast("Failed to copy code", "error");
            });
        }
    }

    openLightbox(imageSrc: string): void {
        const lightbox = getEl("image-lightbox");
        const lightboxImage = getEl("lightbox-image") as HTMLImageElement;
        const caption = document.querySelector(".lightbox-caption") as HTMLElement;

        lightboxImage.src = imageSrc;
        caption.textContent = imageSrc.split("/").pop() || "Image";
        lightbox.style.display = "flex";

        // Prevent body scroll.
        document.body.style.overflow = "hidden";
    }

    closeLightbox(): void {
        const lightbox = getEl("image-lightbox");
        lightbox.style.display = "none";
        document.body.style.overflow = "";
    }

    processExternalContent(html: string): string {
        // Process YouTube embeds.
        html = html.replace(/https?:\/\/(?:www\.)?youtube\.com\/watch\?v=([a-zA-Z0-9_-]+)/gi,
            '<div class="video-embed"><iframe src="https://www.youtube.com/embed/$1" frameborder="0" allowfullscreen></iframe></div>');

        // Process general links.
        html = html.replace(/<a href="([^"]+)"([^>]*)>/gi, (match, href: string, rest: string) => {
            const isExternal = href.startsWith("http") && !href.includes(window.location.hostname);
            const externalClass = isExternal ? "external-link" : "";
            const externalIcon = isExternal ? '<i class="fas fa-external-link-alt"></i>' : "";
            return `<a href="${href}"${rest} class="${externalClass}">${externalIcon}`;
        });

        return html;
    }

    initializeInteractiveElements(): void {
        // Initialize syntax highlighting if available.
        if (window.Prism) {
            window.Prism.highlightAll();
        }

        // Add smooth scrolling for TOC links.
        document.querySelectorAll<HTMLAnchorElement>(".toc-link").forEach((link) => {
            link.addEventListener("click", (e) => {
                e.preventDefault();
                const targetId = (link.getAttribute("href") || "").substring(1);
                const targetElement = getEl(targetId);
                if (targetElement) {
                    targetElement.scrollIntoView({ behavior: "smooth", block: "start" });
                }
            });
        });

        // Add image loading error handling.
        document.querySelectorAll<HTMLImageElement>(".rich-image").forEach((img) => {
            img.addEventListener("error", () => {
                img.style.display = "none";
                const placeholder = document.createElement("div");
                placeholder.className = "image-placeholder";
                placeholder.innerHTML = '<i class="fas fa-image"></i><span>Image failed to load</span>';
                if (img.parentNode) {
                    img.parentNode.insertBefore(placeholder, img);
                }
            });
        });
    }

    switchView(view: ViewName): void {
        // Update navigation.
        document.querySelectorAll<HTMLAnchorElement>(".nav-link").forEach((link) => {
            link.classList.remove("active");
        });
        const activeLink = document.querySelector<HTMLAnchorElement>(`[data-view="${view}"]`);
        if (activeLink) activeLink.classList.add("active");

        // Hide all views.
        document.querySelectorAll<HTMLElement>(".view-section").forEach((section) => {
            section.style.display = "none";
        });

        // Show selected view.
        getEl(`${view}-view`).style.display = "block";

        this.currentView = view;

        // Load data for the view.
        switch (view) {
            case "companies":
                this.loadEntities();
                break;
            case "sectors":
                this.loadSectors();
                break;
            case "stats":
                this.loadStats();
                break;
            case "graph":
                this.loadGraphView();
                break;
        }
    }

    // ---------------------------------------------------------------- //
    // Graph view (cytoscape.js — lazy-init on first visit)              //
    // ---------------------------------------------------------------- //
    // All graph-tab state lives under this.graph. cytoscape.js is loaded
    // via a <script> tag in findata.html but only the first click on the
    // Graph tab pays the parse cost; subsequent visits reuse the instance.

    async loadGraphView(): Promise<void> {
        if (!this.graph) {
            this.graph = {
                cy: null, // cytoscape instance, built on first load
                central: null, // currently-centred entity name
                elements: null, // last-rendered {nodes, edges} for filter swaps
                entitiesLoaded: false,
            };
        }
        // Build the cytoscape instance if it doesn't exist yet.
        if (!this.graph.cy) {
            // Defensive: cytoscape library failed to load (CDN blocked, etc.).
            if (typeof cytoscape === "undefined") {
                getEl("graph-empty").innerHTML =
                    '<p class="error">Could not load the cytoscape.js graph library. ' +
                    "Check your network connection and reload.</p>";
                return;
            }
            const canvas = getEl("graph-canvas");
            this.graph.cy = cytoscape({
                container: canvas,
                elements: [],
                style: this._cytoscapeStyle(),
                layout: { name: "concentric", concentric: (n: CySingular) => (n.data().centrality as number) || 0 },
                wheelSensitivity: 0.2,
                minZoom: 0.2,
                maxZoom: 3,
            });
            // Click handler: re-centre on the clicked node.
            this.graph.cy.on("tap", "node", async (evt) => {
                const name = evt.target.data().id;
                if (name && name !== this.graph!.central) {
                    (getEl("graph-search") as HTMLInputElement).value = name;
                    await this.loadEgoNetwork(name);
                }
            });
            // Selection handler: populate side panel.
            this.graph.cy.on("select", "node", (evt) => {
                this._renderGraphDetail(evt.target.data());
            });
            // Wire toolbar events once.
            getEl("graph-search-btn").addEventListener("click", async () => {
                const name = (getEl("graph-search") as HTMLInputElement).value.trim();
                if (name) await this.loadEgoNetwork(name);
            });
            (getEl("graph-search") as HTMLInputElement).addEventListener("keydown", async (e) => {
                if (e.key === "Enter") {
                    const name = (e.target as HTMLInputElement).value.trim();
                    if (name) await this.loadEgoNetwork(name);
                }
            });
            (getEl("graph-layout") as HTMLSelectElement).addEventListener("change", (e) => {
                this._runGraphLayout((e.target as HTMLSelectElement).value);
            });
            getEl("graph-filter").addEventListener("change", () => {
                // Re-render the same central entity with the new filter.
                if (this.graph!.central) this.loadEgoNetwork(this.graph!.central);
            });
            getEl("graph-as-of").addEventListener("change", () => {
                // Re-render with the new temporal filter applied.
                if (this.graph!.central) this.loadEgoNetwork(this.graph!.central);
            });
            getEl("graph-refresh-db").addEventListener("click", async () => {
                const btn = getEl("graph-refresh-db") as HTMLButtonElement;
                btn.disabled = true;
                try {
                    const r = await fetch("/api/graph/refresh", { method: "POST" });
                    const data: GraphRefreshResponse = await r.json();
                    this._setGraphStatus(data.status === "ok"
                        ? "DB refreshed — re-search to see new data"
                        : "refresh failed");
                    // Drop the cached elements so the next load fetches fresh.
                    this.graph!.elements = null;
                    this.graph!.central = null;
                } catch (e) {
                    this._setGraphStatus("refresh failed: " + (e as Error).message);
                } finally {
                    btn.disabled = false;
                }
            });
            getEl("shortest-btn").addEventListener("click", () => this.loadShortestPath());
            getEl("shortest-clear").addEventListener("click", () => this.clearShortestPath());
        }
        // Populate the typeahead (one-time; small query).
        if (!this.graph.entitiesLoaded) {
            await this.loadGraphEntityList();
            this.graph.entitiesLoaded = true;
        }
        // Resize after the section becomes visible (cytoscape needs the
        // container to have non-zero dimensions before its first layout).
        setTimeout(() => this.graph && this.graph.cy && this.graph.cy.resize(), 50);
    }

    async loadGraphEntityList(): Promise<void> {
        // Fill the <datalist> for the typeahead. Includes BOTH companies and
        // sectors so the user can centre the graph on either. Sectors are
        // tagged in the option label so they're visually distinguishable.
        try {
            const dl = getEl("graph-entities-list");
            const parts: string[] = [];
            // Companies first (the common case).
            const rc = await fetch("/api/entities?type=company&limit=3000");
            const dc: EntitiesResponse = await rc.json();
            (dc.entities || []).forEach((e) => {
                parts.push(`<option value="${e.name}">${e.name}</option>`);
            });
            // Then sectors — prepend a marker so the user can tell them apart
            // in the dropdown. The datalist shows the text content; the value
            // stays the bare sector name so /api/graph/neighbors/<name> gets a
            // clean path segment.
            const rs = await fetch("/api/entities?type=sector&limit=500");
            const ds: EntitiesResponse = await rs.json();
            (ds.entities || []).forEach((e) => {
                parts.push(`<option value="${e.name}">${e.name} (sector)</option>`);
            });
            dl.innerHTML = parts.join("");
        } catch (e) {
            // Non-fatal: typeahead is a convenience, not essential.
            console.warn("graph typeahead load failed", e);
        }
    }

    async loadEgoNetwork(name: string): Promise<void> {
        this._setGraphStatus(`Loading ${name}...`);
        // Read optional temporal filter from the #graph-as-of dropdown. Year
        // only is what the data supports today (only `acquired` edges carry
        // valid_from, and the backfill writes YYYY-01-01).
        const asOf = (getEl("graph-as-of") as HTMLSelectElement).value;
        const params = new URLSearchParams();
        if (asOf) params.set("as_of", asOf);
        const qs = params.toString();
        let data: NeighborsBundle;
        try {
            const url = `/api/graph/neighbors/${encodeURIComponent(name)}`
                + (qs ? `?${qs}` : "");
            const r = await fetch(url);
            if (!r.ok) {
                const err = await r.json().catch((): { error?: string } => ({ error: r.statusText }));
                throw new Error(err.error || `HTTP ${r.status}`);
            }
            data = await r.json() as NeighborsBundle;
        } catch (e) {
            this._setGraphStatus(`Error: ${(e as Error).message}`);
            return;
        }
        if (!this.graph || !this.graph.cy) return;

        // Branch on entity_type — sector focal renders differently from
        // company focal.
        const isSector = data.entity_type === "sector";
        const filter: GraphFilter = isSector ? "all" : (getEl("graph-filter") as HTMLSelectElement).value as GraphFilter;
        const elements: GraphElement[] = isSector
            ? this._buildSectorEgoElements(data as SectorNeighbors)
            : this._buildEgoElements(data as CompanyNeighbors, filter);

        this.graph.cy.elements().remove();
        this.graph.cy.add(elements as unknown as CyElementInput[]);
        this.graph.central = isSector ? (data as SectorNeighbors).sector : (data as CompanyNeighbors).company;
        this.graph.elements = elements;
        this.graph.entityType = isSector ? "sector" : "company";

        // Lay out + render.
        this._runGraphLayout((getEl("graph-layout") as HTMLSelectElement).value);

        // Highlight + select the focal node.
        this.graph.cy.getElementById(this.graph.central)
            .addClass("focal")
            .select();

        // Side panel shows the focal entity by default.
        const focalData = this.graph.cy.getElementById(this.graph.central).data();
        (focalData as CyNodeData & { __bundle?: NeighborsBundle }).__bundle = data;
        this._renderGraphDetail(focalData);

        // Status line. Append `· as of YYYY` when the temporal filter is on.
        const asOfSuffix = asOf ? ` · as of ${asOf}` : "";
        if (isSector) {
            const sectorBundle = data as SectorNeighbors;
            this._setGraphStatus(
                `${sectorBundle.sector} — ${sectorBundle.member_count} member` +
                (sectorBundle.member_count !== 1 ? "s" : "") + asOfSuffix);
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
                `${companyBundle.company} — ${total} relationship${total !== 1 ? "s" : ""}`
                + (companyBundle.sector ? ` · ${companyBundle.sector}` : "") + asOfSuffix);
        }
        getEl("graph-empty").style.display = "none";
    }

    _buildSectorEgoElements(data: SectorNeighbors): GraphElement[] {
        // Render a sector as the focal node with one edge per member company.
        // For very large sectors (Automotive has 86 members) we cap the
        // rendered count at 60 and mark the focal node so the UI can warn.
        const focal = data.sector;
        const MAX = 60;
        const all = data.members || [];
        const truncated = all.length > MAX;
        const shown = truncated ? all.slice(0, MAX) : all;
        const nodes: GraphElement[] = [
            { data: { id: focal, label: focal, group: "sector-focal", centrality: 10 } },
        ];
        const edges: GraphElement[] = [];
        shown.forEach((m) => {
            nodes.push({
                data: { id: m, label: m, group: "member", centrality: 5 },
            });
            edges.push({
                data: {
                    id: `${focal}__${m}`, source: focal, target: m,
                    type: "has_company", label: "has",
                },
            });
        });
        if (truncated) {
            // Add a synthetic "+N more" node so the user can see the cap.
            const moreId = `__more_${focal}`;
            nodes.push({
                data: {
                    id: moreId,
                    label: `+${all.length - MAX} more (open sector page)`,
                    group: "more",
                    centrality: 4,
                },
            });
            edges.push({
                data: { id: `${focal}__${moreId}`, source: focal, target: moreId,
                        type: "has_company", label: "more" },
            });
        }
        return [...nodes, ...edges];
    }

    _buildEgoElements(data: CompanyNeighbors, filter: GraphFilter): GraphElement[] {
        // Translate the /api/graph/neighbors bundle into cytoscape's element
        // format: [{data: {id, label, group}}, ...] for nodes and edges.
        const nodes: GraphElement[] = [];
        const edges: GraphElement[] = [];
        const focal = data.company;
        const addNode = (name: string | null | undefined, group: string): void => {
            if (!name || name === focal) return;
            nodes.push({
                data: {
                    id: name,
                    label: name,
                    group,
                    centrality: 0, // assigned by layout below
                },
            });
        };
        const addEdge = (src: string, dst: string, type: string, label: string, props: Record<string, unknown> = {}): void => {
            edges.push({
                data: { id: `${src}__${dst}__${type}`, source: src, target: dst, type, label, props },
            });
        };

        // Focal node first.
        nodes.push({ data: { id: focal, label: focal, group: "focal", centrality: 10 } });

        if (filter === "all" || filter === "peers") {
            data.peers.forEach((p) => { addNode(p, "peer"); addEdge(focal, p, "competes_with", "peer"); });
        }
        if (filter === "all" || filter === "jv") {
            data.jv_partners.forEach((j) => {
                addNode(j.partner, "jv");
                addEdge(focal, j.partner, "jv_with", "JV" + (j.venture ? `: ${j.venture}` : ""));
            });
        }
        if (filter === "all") {
            data.group_siblings.forEach((s) => { addNode(s, "sibling"); addEdge(focal, s, "same_group", "same group"); });
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
            data.suppliers.forEach((s) => { addNode(s, "supplier"); addEdge(s, focal, "supplies_to", "supplies"); });
            data.customers.forEach((c) => { addNode(c, "customer"); addEdge(focal, c, "supplies_to", "customer"); });
        }
        if (filter === "all" && data.sector) {
            // Sector node — kept visually distinct from companies.
            const sectorId = `sector:${data.sector}`;
            nodes.push({ data: { id: sectorId, label: data.sector, group: "sector", centrality: 8 } });
            edges.push({ data: { id: `${focal}__${sectorId}`, source: focal, target: sectorId, type: "part_of", label: "part of" } });
        }

        // Centrality: focal highest; sector next; everything else orbits.
        nodes.forEach((n) => {
            if (n.data.group === "peer") n.data.centrality = 6;
            else if (n.data.group === "parent") n.data.centrality = 7;
            else if (n.data.group === "sector") n.data.centrality = 8;
        });
        return [...nodes, ...edges];
    }

    _runGraphLayout(name: string): void {
        if (!this.graph || !this.graph.cy || this.graph.cy.elements().length === 0) return;
        const opts: Record<string, unknown> = { name, animate: true, animationDuration: 300 };
        if (name === "cose") {
            opts.nodeRepulsion = () => 8000;
            opts.idealEdgeLength = () => 100;
            opts.nodeOverlap = 20;
        } else if (name === "concentric") {
            opts.concentric = (n: CySingular) => (n.data().centrality as number) || 0;
            opts.levelWidth = () => 1;
            opts.minNodeSpacing = 30;
        } else if (name === "breadthfirst") {
            opts.directed = true;
            opts.spacingFactor = 1.2;
            opts.roots = this.graph.central ? `#${CSS.escape(this.graph.central)}` : undefined;
        }
        this.graph.cy.layout(opts).run();
    }

    _renderGraphDetail(nodeData: CyNodeData | null): void {
        const panel = getEl("graph-detail");
        if (!nodeData) {
            panel.innerHTML = '<div class="graph-detail-empty"><i class="fas fa-hand-pointer"></i>' +
                              "<p>Click a node to centre the graph on it.</p></div>";
            return;
        }
        const name = nodeData.id;
        const group = (nodeData.group as string) || "company";
        const bundle = (nodeData as CyNodeData & { __bundle?: NeighborsBundle }).__bundle; // only set on focal node

        let html = `<div class="graph-detail-header">
            <span class="graph-badge graph-badge-${group}">${group}</span>
            <h3>${this.escapeHtml(name)}</h3>
        </div>`;

        if (bundle && bundle.entity_type === "sector") {
            // Sector focal node — show member count + market-cap breakdown.
            const sectorBundle = bundle as SectorNeighbors;
            html += `<ul class="graph-detail-list">`;
            html += `<li><strong>Members:</strong> ${sectorBundle.member_count}</li>`;
            const mc = sectorBundle.market_cap_counts || {};
            Object.keys(mc).forEach((k) => {
                html += `<li><strong>${this.escapeHtml(k)}:</strong> ${mc[k]}</li>`;
            });
            html += `</ul>`;
            if (sectorBundle.file_path) {
                html += `<a class="btn-primary" href="/entity/${sectorBundle.file_path}">View sector note →</a>`;
            }
        } else if (bundle) {
            // Company focal node — show the full bundle.
            const companyBundle = bundle as CompanyNeighbors;
            html += `<ul class="graph-detail-list">`;
            if (companyBundle.sector) html += `<li><strong>Sector:</strong> ${this.escapeHtml(companyBundle.sector)}</li>`;
            if (companyBundle.subsidiary_of) html += `<li><strong>Parent:</strong> ${this.escapeHtml(companyBundle.subsidiary_of)}</li>`;
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
        } else if (group === "more") {
            // Synthetic "+N more" node — link to the sector page.
            html += `<p class="hint">This sector has more members than the graph renders (cap: 60). ` +
                    `Use the sector page for the full list.</p>`;
            if (this.graph && this.graph.central) {
                html += `<a class="btn-primary" href="/api/graph/sector/${encodeURIComponent(this.graph.central)}">View all members (JSON)</a>`;
            }
        } else {
            // Non-focal node — click to re-centre.
            html += `<p class="hint">Click this node (or it's already selected) to re-centre on <em>${this.escapeHtml(name)}</em>.</p>`;
            html += `<button class="btn-primary" onclick="getEl('graph-search').value='${name.replace(/'/g, "\\'")}'; ` +
                    `document.getElementById('graph-search-btn').click();">Centre on ${this.escapeHtml(name)}</button>`;
        }

        panel.innerHTML = html;
    }

    _setGraphStatus(text: string): void {
        const el = getEl("graph-status");
        if (el) el.textContent = text;
    }

    _cytoscapeStyle(): CyStylesheet {
        // Returns cytoscape.js style array. Colours per node group are defined
        // here (and mirrored as CSS classes in findata.css for the legend).
        return cytoscape.stylesheet()
            .selector("node").style({
                "label": "data(label)",
                "text-valign": "bottom",
                "text-halign": "center",
                "text-outline-width": 2,
                "text-outline-color": "#1f2933",
                "color": "#f0f4f8",
                "font-size": 11,
                "width": 28,
                "height": 28,
                "background-color": "#5a6577",
            })
            .selector('node[group="focal"]').style({
                "background-color": "#e63946", "width": 44, "height": 44,
                "font-size": 14, "font-weight": "bold",
            })
            .selector('node[group="peer"]').style({ "background-color": "#f4a261" })
            .selector('node[group="jv"]').style({ "background-color": "#2a9d8f" })
            .selector('node[group="sibling"]').style({ "background-color": "#8d99ae" })
            .selector('node[group="acquired"]').style({ "background-color": "#9d4edd" })
            .selector('node[group="parent"]').style({ "background-color": "#43aa8b" })
            .selector('node[group="supplier"]').style({ "background-color": "#577590" })
            .selector('node[group="customer"]').style({ "background-color": "#577590" })
            .selector('node[group="sector"]').style({
                "background-color": "#4a6fa5", "shape": "rectangle",
                "width": 50, "height": 30,
            })
            .selector('node[group="sector-focal"]').style({
                "background-color": "#3d5a80", "shape": "rectangle",
                "width": 56, "height": 36, "font-size": 14, "font-weight": "bold",
            })
            .selector('node[group="member"]').style({ "background-color": "#7d8597" })
            .selector('node[group="more"]').style({
                "background-color": "#6c757d", "shape": "diamond",
                "width": 24, "height": 24, "font-size": 9,
            })
            .selector("node.focal").style({ "border-width": 3, "border-color": "#ffd166" })
            .selector("edge").style({
                "width": 2,
                "line-color": "#4a5568",
                "target-arrow-color": "#4a5568",
                "target-arrow-shape": "triangle",
                "curve-style": "bezier",
                "label": "data(label)",
                "font-size": 9,
                "color": "#9aa5b1",
                "text-background-color": "#1f2933",
                "text-background-padding": 2,
                "text-background-opacity": 0.7,
            })
            .selector('edge[type="competes_with"]').style({ "line-color": "#f4a261", "target-arrow-color": "#f4a261" })
            .selector('edge[type="jv_with"]').style({ "line-color": "#2a9d8f", "target-arrow-color": "#2a9d8f" })
            .selector('edge[type="acquired"]').style({ "line-color": "#9d4edd", "target-arrow-color": "#9d4edd" })
            .selector('edge[type="subsidiary_of"]').style({ "line-color": "#43aa8b", "target-arrow-color": "#43aa8b" })
            .selector('edge[type="supplies_to"]').style({ "line-color": "#577590", "target-arrow-color": "#577590" })
            .selector('edge[type="part_of"]').style({ "line-color": "#4a6fa5", "target-arrow-color": "#4a6fa5" })
            .selector('edge[type="has_company"]').style({ "line-color": "#3d5a80", "target-arrow-color": "#3d5a80" })
            .selector("edge.highlighted").style({
                "width": 4, "line-color": "#ffd166", "target-arrow-color": "#ffd166",
                "z-index": 10,
            })
            .selector("node.highlighted").style({
                "border-width": 3, "border-color": "#ffd166", "z-index": 10,
            })
            .selector(".faded").style({ "opacity": 0.25 });
    }

    async loadShortestPath(): Promise<void> {
        const a = (getEl("shortest-a") as HTMLInputElement).value.trim();
        const b = (getEl("shortest-b") as HTMLInputElement).value.trim();
        const result = getEl("shortest-result");
        if (!a || !b) {
            result.innerHTML = '<p class="hint">Enter both entities.</p>';
            return;
        }
        // Honour the same #graph-as-of filter as the ego-network view so a
        // user exploring "as of 2022" gets path results consistent with what
        // they see on the canvas.
        const asOf = (getEl("graph-as-of") as HTMLSelectElement).value;
        const params = new URLSearchParams({ a, b });
        if (asOf) params.set("as_of", asOf);
        result.innerHTML = '<p><i class="fas fa-spinner fa-spin"></i> Finding path...</p>';
        try {
            const r = await fetch(`/api/graph/shortest?${params}`);
            if (!r.ok) {
                const err = await r.json().catch((): { error?: string } => ({ error: r.statusText }));
                throw new Error(err.error || `HTTP ${r.status}`);
            }
            const data: ShortestPathResponse = await r.json();
            this._renderShortestPath(data);
        } catch (e) {
            result.innerHTML = `<p class="error">${this.escapeHtml((e as Error).message)}</p>`;
        }
    }

    _renderShortestPath(data: ShortestPathResponse): void {
        const result = getEl("shortest-result");
        if (data.path === null) {
            result.innerHTML = `<p class="hint">No path found between <em>${this.escapeHtml(data.source)}</em> and <em>${this.escapeHtml(data.target)}</em> within the hop limit.</p>`;
            // Clear any previous highlight.
            if (this.graph && this.graph.cy) {
                this.graph.cy.elements().removeClass("highlighted faded");
            }
            return;
        }
        const chain = data.path.map((p) => p.name);
        const hops = data.hops ?? 0;
        result.innerHTML = `
            <p><strong>${hops}</strong> hop${hops !== 1 ? "s" : ""}:</p>
            <ol class="path-chain">${chain.map((n) => `<li>${this.escapeHtml(n)}</li>`).join("")}</ol>`;
        // If the path involves the current graph, highlight it.
        if (this.graph && this.graph.cy) {
            const cy = this.graph.cy;
            cy.elements().removeClass("highlighted faded");
            const pathNodes = chain.filter((n) => cy.getElementById(n).length > 0);
            if (pathNodes.length === chain.length) {
                // Add highlighted class to path nodes & the edges between consecutive ones.
                const pathEles = cy.collection();
                for (let i = 0; i < chain.length - 1; i++) {
                    const e = cy.getElementById(chain[i]).edgesWith(cy.getElementById(chain[i + 1]));
                    pathEles.merge(e);
                    pathEles.merge(cy.getElementById(chain[i]));
                }
                pathEles.merge(cy.getElementById(chain[chain.length - 1]));
                pathEles.addClass("highlighted");
                cy.elements().not(pathEles).addClass("faded");
            }
        }
    }

    clearShortestPath(): void {
        getEl("shortest-result").innerHTML = "";
        (getEl("shortest-a") as HTMLInputElement).value = "";
        (getEl("shortest-b") as HTMLInputElement).value = "";
        if (this.graph && this.graph.cy) {
            this.graph.cy.elements().removeClass("highlighted faded");
        }
    }

    setLayout(layout: "grid" | "list"): void {
        this.currentLayout = layout;

        // Update buttons.
        document.querySelectorAll<HTMLButtonElement>(".view-btn").forEach((btn) => {
            btn.classList.remove("active");
        });
        const activeBtn = document.querySelector<HTMLButtonElement>(`[data-layout="${layout}"]`);
        if (activeBtn) activeBtn.classList.add("active");

        // Update container.
        const container = getEl("companies-container");
        container.className = `entities-container ${layout}-layout`;
    }

    updatePagination(): void {
        const container = getEl("companies-pagination");
        const totalPages = Math.ceil(this.totalCount / this.pageSize);

        if (totalPages <= 1) {
            container.innerHTML = "";
            return;
        }

        let paginationHtml = "";

        // Previous button.
        if (this.currentPage > 0) {
            paginationHtml += `<button class="page-btn" onclick="viewer.goToPage(${this.currentPage - 1})">Previous</button>`;
        }

        // Page numbers.
        const startPage = Math.max(0, this.currentPage - 2);
        const endPage = Math.min(totalPages - 1, this.currentPage + 2);

        if (startPage > 0) {
            paginationHtml += `<button class="page-btn" onclick="viewer.goToPage(0)">1</button>`;
            if (startPage > 1) {
                paginationHtml += '<span class="page-ellipsis">...</span>';
            }
        }

        for (let i = startPage; i <= endPage; i++) {
            const activeClass = i === this.currentPage ? "active" : "";
            paginationHtml += `<button class="page-btn ${activeClass}" onclick="viewer.goToPage(${i})">${i + 1}</button>`;
        }

        if (endPage < totalPages - 1) {
            if (endPage < totalPages - 2) {
                paginationHtml += '<span class="page-ellipsis">...</span>';
            }
            paginationHtml += `<button class="page-btn" onclick="viewer.goToPage(${totalPages - 1})">${totalPages}</button>`;
        }

        // Next button.
        if (this.currentPage < totalPages - 1) {
            paginationHtml += `<button class="page-btn" onclick="viewer.goToPage(${this.currentPage + 1})">Next</button>`;
        }

        container.innerHTML = paginationHtml;
    }

    goToPage(page: number): void {
        this.currentPage = page;
        // Content mode paginates its own FTS results, not the entity list.
        if (this.searchMode === "content" && this.currentView === "companies") {
            this.performContentSearch();
        } else {
            this.loadEntities(false);
        }
    }

    updateCount(): void {
        const countLabel = getEl("companies-count");
        const start = this.currentPage * this.pageSize + 1;
        const end = Math.min(start + this.pageSize - 1, this.totalCount);
        countLabel.textContent = `${start}-${end} of ${this.totalCount}`;
    }

    showLoading(show: boolean): void {
        const loading = getEl("loading");
        loading.style.display = show ? "block" : "none";
    }

    showError(message: string): void {
        // Create error toast.
        const toast = document.createElement("div");
        toast.className = "toast error";
        toast.innerHTML = `
            <i class="fas fa-exclamation-circle"></i>
            <span>${this.escapeHtml(message)}</span>
        `;

        document.body.appendChild(toast);

        setTimeout(() => {
            toast.remove();
        }, 3000);
    }

    // showToast is invoked by copyCode but wasn't in the original; declared so
    // the call type-checks. Kept as a no-op-friendly fallback that reuses
    // showError's toast plumbing (matches the prior runtime behavior).
    private showToast(message: string, _kind: string): void {
        const toast = document.createElement("div");
        toast.className = `toast ${_kind}`;
        toast.innerHTML = `<span>${this.escapeHtml(message)}</span>`;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 3000);
    }

    escapeHtml(text: string): string {
        const div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }

    truncateText(text: string, maxLength: number): string {
        if (text.length <= maxLength) return text;
        return text.substring(0, maxLength) + "...";
    }
}

// Initialize the viewer + re-expose on window so the inline onclick handlers
// in the HTML strings above resolve at runtime (load-bearing: see file header).
const viewer = new FinDataViewer();
window.viewer = viewer;
