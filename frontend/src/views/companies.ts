// Companies view: entity-card grid/list, entity vs full-text search modes,
// filters, layout toggle, pagination.
//
// Extracted from FinDataViewer during the S2 split. State that was previously
// scattered across the single class (page/layout/search-mode/filters) now
// lives here; behavior is unchanged, including the inline-onclick pagination
// contract (`viewer.goToPage(n)` — re-exposed on the shell).

import type {
    EntitiesResponse,
    EntityListItem,
    SearchResponse,
} from "../../types/api";
import { getEl, escapeHtml } from "../core/dom";
import { fetchJson } from "../core/api";
import { showLoading, showError } from "../core/toast";
import { highlightSnippet } from "../core/markdown";

export class CompaniesView {
    // --- view + pagination state ---------------------------------------- //
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

    /** Whether this view is the visible one (deferred-render check). */
    private readonly isActive: () => boolean;

    constructor(isActive: () => boolean) {
        this.isActive = isActive;
    }

    /** Wire the static controls in templates/findata.html (called once at boot). */
    bindEvents(): void {
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
            if (this.isActive()) {
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
    }

    debounceSearch(): void {
        clearTimeout(this.searchTimeout);
        this.searchTimeout = setTimeout(() => {
            this.searchEntities();
        }, 300);
    }

    async loadEntities(resetPage = true): Promise<void> {
        if (resetPage) {
            this.currentPage = 0;
        }

        showLoading(true);

        try {
            // Build params, ensuring type filter for companies view.
            const params = new URLSearchParams({
                limit: String(this.pageSize),
                offset: String(this.currentPage * this.pageSize),
                ...this.filters,
            });

            // Ensure we only show companies in companies view.
            if (this.isActive() && !params.has("type")) {
                params.set("type", "company");
            }

            const data = await fetchJson<EntitiesResponse>(`/api/entities?${params}`);

            this.totalCount = data.total_count;

            if (this.isActive()) {
                this.displayEntities(data.entities);
                this.updatePagination();
                this.updateCount();
            }
        } catch (error) {
            console.error("Error loading entities:", error);
            showError("Failed to load entities");
        } finally {
            showLoading(false);
        }
    }

    searchEntities(): void {
        // Content mode is a distinct code path: it queries the FTS5
        // /api/search endpoint (note bodies + newsletters) and renders a
        // polymorphic result list with snippets — structurally different from
        // the entity-card grid. Only relevant in the companies view (the only
        // view with a results container + pagination).
        if (this.searchMode === "content" && this.isActive()) {
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

    /** Sector-tag handoff from the sectors view (sets state + dropdown). */
    setSectorFilter(sector: string): void {
        this.filters.sector = sector;
        (getEl("sector-filter") as HTMLSelectElement).value = sector;
    }

    async performContentSearch(): Promise<void> {
        const q = (this.filters.search || "").trim();
        showLoading(true);
        try {
            const params = new URLSearchParams({
                q,
                limit: String(this.pageSize),
                offset: String(this.currentPage * this.pageSize),
            });
            // Raw fetch: this endpoint has bespoke status handling (503 =
            // index not built is a first-class UI message, not an error).
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
            showError("Content search failed");
        } finally {
            showLoading(false);
        }
    }

    displayContentResults(results: SearchResponse["results"], totalCount: number, errorMessage: string | null = null): void {
        const container = getEl("companies-container");
        if (errorMessage) {
            container.innerHTML = `<div class="no-results">${escapeHtml(errorMessage)}</div>`;
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
            const safeSnippet = highlightSnippet(hit.snippet);
            const viewLink = hit.file_path
                ? `<a href="/entity/${encodeURIComponent(hit.file_path)}" class="btn-primary" target="_blank" rel="noopener noreferrer"><i class="fas fa-external-link-alt"></i> View note</a>`
                : "";
            card.innerHTML = `
                <div class="card-header">
                    <h3 class="card-title">${escapeHtml(hit.title || "(untitled)")}</h3>
                    <div class="card-type"><span class="doc-badge">${escapeHtml(docLabel[hit.doc_type] || hit.doc_type)}</span></div>
                </div>
                <div class="card-body">
                    ${hit.sector ? `<div class="card-sector"><i class="fas fa-industry"></i><span>${escapeHtml(hit.sector)}</span></div>` : ""}
                    <div class="content-snippet">${safeSnippet}</div>
                </div>
                ${viewLink ? `<div class="card-footer">${viewLink}</div>` : ""}
            `;
            container.appendChild(card);
        });
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
                <h3 class="card-title">${escapeHtml(entity.name)}</h3>
                <div class="card-type">
                    <i class="fas fa-building"></i>
                    <span>${escapeHtml(entity.entity_type || "company")}</span>
                </div>
            </div>
            <div class="card-body">
                <div class="card-sector">
                    <i class="fas fa-industry"></i>
                    <span>${escapeHtml(entity.sector_classification || "Unknown")}</span>
                </div>
                ${marketCapTag ? `<div class="card-market-cap">
                    <i class="fas fa-chart-line"></i>
                    <span>${escapeHtml(marketCapTag.replace("market_cap/", "").replace("_", " "))}</span>
                </div>` : ""}
                ${geographyTag ? `<div class="card-geography">
                    <i class="fas fa-globe"></i>
                    <span>${escapeHtml(geographyTag.replace("geography/", ""))}</span>
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

    /** Pagination entry point — invoked by inline onclick in page buttons. */
    goToPage(page: number): void {
        this.currentPage = page;
        // Content mode paginates its own FTS results, not the entity list.
        if (this.searchMode === "content" && this.isActive()) {
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
}
