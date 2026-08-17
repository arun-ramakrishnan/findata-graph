"use strict";
(() => {
  // src/findata.ts
  function getEl(id) {
    const node = document.getElementById(id);
    if (!node) {
      throw new Error(`expected element #${id} not found in DOM`);
    }
    return node;
  }
  var FinDataViewer = class {
    constructor() {
      // --- view + pagination state ------------------------------------------ //
      this.currentView = "companies";
      this.currentLayout = "grid";
      this.currentPage = 0;
      this.pageSize = 20;
      this.totalCount = 0;
      /**
       * 'entities' (default) queries /api/entities by name+tag; 'content'
       * queries the FTS5 /api/search endpoint over note bodies + newsletters.
       */
      this.searchMode = "entities";
      this.filters = {
        search: "",
        sector: "",
        type: "",
        marketcap: ""
      };
      // --- graph-tab state (lazy-initialized in loadGraphView) -------------- //
      this.graph = null;
      // --- docs-tab state --------------------------------------------------- //
      this.docs = { activePath: null };
      // --- Cloud mode: whole-graph force cloud ------------------------------- //
      // "Cloud" renders EVERY entity + EVERY typed edge at once (GET
      // /api/graph/cloud), coloured by relationship type, with a legend +
      // relationship cloud card. Complements the ego-network view: search / as-of
      // / edge-filter exit back to ego mode (see _exitCloudMode).
      /** Edge-type → colour palette shared by the legend + cytoscape styles. */
      this.EDGE_COLORS = {
        co_mentioned_in: "#7fd1ae",
        part_of: "#4a6fa5",
        has_company: "#3d5a80",
        exposed_to: "#e9c46a",
        belongs_to: "#8d99ae",
        subsidiary_of: "#43aa8b",
        jv_with: "#2a9d8f",
        acquired: "#9d4edd",
        competes_with: "#f4a261",
        supplier_to: "#577590",
        customer_of: "#e5989b",
        same_group: "#b5838d"
      };
      this.init();
    }
    init() {
      this.bindEvents();
      this.loadInitialData();
    }
    bindEvents() {
      document.querySelectorAll(".nav-link").forEach((link) => {
        link.addEventListener("click", (e) => {
          e.preventDefault();
          this.switchView(link.dataset.view);
        });
      });
      const searchInput = getEl("search-input");
      const clearSearch = getEl("clear-search");
      searchInput.addEventListener("input", (e) => {
        const target = e.target;
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
      getEl("mode-entities").addEventListener("click", () => {
        this.setSearchMode("entities");
      });
      getEl("mode-content").addEventListener("click", () => {
        this.setSearchMode("content");
      });
      const sectorFilter = getEl("sector-filter");
      sectorFilter.addEventListener("change", (e) => {
        this.filters.sector = e.target.value;
        if (this.currentView === "companies") {
          this.filters.type = "company";
        }
        this.searchEntities();
      });
      const typeFilter = getEl("type-filter");
      typeFilter.addEventListener("change", (e) => {
        this.filters.type = e.target.value;
        this.searchEntities();
      });
      const marketcapFilter = getEl("marketcap-filter");
      marketcapFilter.addEventListener("change", (e) => {
        this.filters.marketcap = e.target.value;
        this.searchEntities();
      });
      getEl("grid-view").addEventListener("click", () => {
        this.setLayout("grid");
      });
      getEl("list-view").addEventListener("click", () => {
        this.setLayout("list");
      });
      getEl("close-lightbox").addEventListener("click", () => {
        this.closeLightbox();
      });
      getEl("image-lightbox").addEventListener("click", (e) => {
        if (e.target.id === "image-lightbox") {
          this.closeLightbox();
        }
      });
      const docsSearch = getEl("docs-search");
      const docsClear = getEl("docs-search-clear");
      docsSearch.addEventListener("input", (e) => {
        const target = e.target;
        docsClear.style.display = target.value ? "block" : "none";
        this.debounceDocsSearch();
      });
      docsClear.addEventListener("click", () => {
        docsSearch.value = "";
        docsClear.style.display = "none";
        this.loadDocsCatalog();
      });
      getEl("docs-reset").addEventListener("click", () => {
        docsSearch.value = "";
        docsClear.style.display = "none";
        this.loadDocsCatalog();
      });
    }
    debounceSearch() {
      clearTimeout(this.searchTimeout);
      this.searchTimeout = setTimeout(() => {
        this.searchEntities();
      }, 300);
    }
    debounceDocsSearch() {
      clearTimeout(this.docsSearchTimeout);
      this.docsSearchTimeout = setTimeout(() => {
        this.runDocsSearch();
      }, 300);
    }
    async loadInitialData() {
      await this.loadSectors();
      await this.loadStats();
      await this.loadEntities();
    }
    async loadSectors() {
      try {
        const response = await fetch("/api/sectors");
        const data = await response.json();
        const sectorFilter = getEl("sector-filter");
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
    async loadStats() {
      try {
        const response = await fetch("/api/stats");
        const data = await response.json();
        if (this.currentView === "stats") {
          this.displayStats(data);
        }
      } catch (error) {
        console.error("Error loading stats:", error);
      }
      try {
        const response = await fetch("/api/graph/stats");
        const data = await response.json();
        if (this.currentView === "stats") {
          this.displayGraphStats(data);
        }
      } catch (error) {
        console.error("Error loading graph stats:", error);
        this.displayGraphStatsError();
      }
    }
    async loadEntities(resetPage = true) {
      if (resetPage) {
        this.currentPage = 0;
      }
      this.showLoading(true);
      try {
        const params = new URLSearchParams({
          limit: String(this.pageSize),
          offset: String(this.currentPage * this.pageSize),
          ...this.filters
        });
        if (this.currentView === "companies" && !params.has("type")) {
          params.set("type", "company");
        }
        const response = await fetch(`/api/entities?${params}`);
        const data = await response.json();
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
    searchEntities() {
      if (this.searchMode === "content" && this.currentView === "companies") {
        this.performContentSearch();
      } else {
        this.loadEntities();
      }
    }
    setSearchMode(mode) {
      if (mode === this.searchMode) return;
      this.searchMode = mode;
      getEl("mode-entities").classList.toggle("active", mode === "entities");
      getEl("mode-content").classList.toggle("active", mode === "content");
      const filtersApply = mode === "entities";
      document.querySelectorAll(".filters select").forEach((sel) => {
        sel.disabled = !filtersApply;
      });
      this.currentPage = 0;
      this.searchEntities();
    }
    async performContentSearch() {
      const q = (this.filters.search || "").trim();
      this.showLoading(true);
      try {
        const params = new URLSearchParams({
          q,
          limit: String(this.pageSize),
          offset: String(this.currentPage * this.pageSize)
        });
        const response = await fetch(`/api/search?${params}`);
        if (response.status === 503) {
          this.displayContentResults(
            [],
            0,
            "Search index not built. Run: python3 helpers/maintenance/rebuild_note_search.py"
          );
          return;
        }
        if (!response.ok) {
          const err = await response.json().catch(() => ({}));
          this.displayContentResults(
            [],
            0,
            err.error || `Search failed (HTTP ${response.status})`
          );
          return;
        }
        const data = await response.json();
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
    displayContentResults(results, totalCount, errorMessage = null) {
      const container = getEl("companies-container");
      if (errorMessage) {
        container.innerHTML = `<div class="no-results">${this.escapeHtml(errorMessage)}</div>`;
        return;
      }
      if (!results || results.length === 0) {
        container.innerHTML = '<div class="no-results">No content matches. Try another term, or switch to Entities mode.</div>';
        return;
      }
      container.innerHTML = "";
      results.forEach((hit) => {
        const card = document.createElement("div");
        card.className = "entity-card content-search-card";
        const docLabel = {
          company: "Company",
          sector: "Sector",
          super_sector: "Super-Sector",
          chatter: "Newsletter (Chatter)",
          points_and_figures: "Newsletter (P&F)",
          plotlines: "Newsletter (Plotlines)"
        };
        const safeSnippet = this.highlightSnippet(hit.snippet);
        const viewLink = hit.file_path ? `<a href="/entity/${encodeURIComponent(hit.file_path)}" class="btn-primary" target="_blank" rel="noopener noreferrer"><i class="fas fa-external-link-alt"></i> View note</a>` : "";
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
    highlightSnippet(snippet) {
      if (!snippet) return "";
      const OPEN = "";
      const CLOSE = "";
      const marked2 = String(snippet).replace(/<mark>/g, OPEN).replace(/<\/mark>/g, CLOSE);
      const escaped = this.escapeHtml(marked2);
      return escaped.replace(/\u0001/g, "<mark>").replace(/\u0002/g, "</mark>");
    }
    displayEntities(entities) {
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
    createEntityCard(entity) {
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
                    <span>${this.escapeHtml(entity.entity_type || "company")}</span>
                </div>
            </div>
            <div class="card-body">
                <div class="card-sector">
                    <i class="fas fa-industry"></i>
                    <span>${this.escapeHtml(entity.sector_classification || "Unknown")}</span>
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
    displaySectors(data) {
      const container = getEl("sectors-container");
      container.innerHTML = "";
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
          getEl("sector-filter").value = sector;
          this.switchView("companies");
        });
        grid.appendChild(tag);
      });
      classificationsDiv.appendChild(grid);
      container.appendChild(classificationsDiv);
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
    createSectorCard(sector) {
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
    displayStats(data) {
      const container = getEl("stats-container");
      container.innerHTML = "";
      const totalCard = this.createStatCard("Total Entities", data.total_entities, "fas fa-database", "primary");
      container.appendChild(totalCard);
      const typesCard = this.createStatCard("Entity Types", Object.keys(data.entity_counts).length, "fas fa-tags", "secondary");
      container.appendChild(typesCard);
      const sectorsCard = this.createStatCard("Sectors", Object.keys(data.top_sectors).length, "fas fa-industry", "success");
      container.appendChild(sectorsCard);
      const marketCapCard = this.createStatCard("Market Cap Categories", Object.keys(data.market_cap_counts).length, "fas fa-chart-line", "warning");
      container.appendChild(marketCapCard);
      const breakdownSection = document.createElement("div");
      breakdownSection.className = "stats-breakdown";
      breakdownSection.appendChild(this.createBreakdownCard("Entity Types", data.entity_counts, "entity_type"));
      breakdownSection.appendChild(this.createBreakdownCard("Top Sectors", data.top_sectors, "sector"));
      breakdownSection.appendChild(this.createBreakdownCard("Market Cap Distribution", data.market_cap_counts, "market_cap"));
      container.appendChild(breakdownSection);
    }
    /** Full graph-stats block for the Statistics view (from /api/graph/stats). */
    displayGraphStats(data) {
      const container = getEl("stats-container");
      if (!container) return;
      const section = document.createElement("div");
      section.className = "stats-graph-block";
      section.innerHTML = `
            <div class="stats-graph-header">
                <h3><i class="fas fa-project-diagram"></i> Graph Statistics</h3>
                ${data.structure ? '<span class="stats-graph-meta">via Onager graph metrics</span>' : ""}
            </div>
            <div class="stats-graph-cards">${this._graphStatsCards(data)}</div>
        `;
      if (data.structure) {
        const structure = data.structure;
        const items = [
          ["Density", structure.density],
          ["Diameter", structure.diameter],
          ["Radius", structure.radius],
          ["Avg path length", structure.avg_path_length],
          ["Transitivity", structure.transitivity],
          ["Triangles", structure.triangles],
          ["Avg clustering", structure.avg_clustering],
          ["Assortativity", structure.assortativity]
        ];
        const metrics = document.createElement("div");
        metrics.className = "breakdown-card stats-graph-structure";
        metrics.innerHTML = `<h4>Structure</h4><div class="breakdown-items">` + items.map(([label, v]) => `
                    <div class="breakdown-item">
                        <span class="breakdown-label">${this.escapeHtml(label)}</span>
                        <span class="breakdown-value">${v === null ? "\u2014" : typeof v === "number" ? v.toFixed(4) : this.escapeHtml(String(v))}</span>
                    </div>`).join("") + `</div>`;
        section.appendChild(metrics);
      } else {
        const note = document.createElement("div");
        note.className = "hint";
        note.textContent = "Structure metrics unavailable (graph analysis layer not connected).";
        section.appendChild(note);
      }
      const byType = data.edges.by_type || {};
      const sorted = {};
      Object.keys(byType).sort((a, b) => byType[b] - byType[a]).forEach((k) => {
        sorted[k] = byType[k];
      });
      section.appendChild(this.createBreakdownCard("Edge Types", sorted, "edge_type"));
      container.appendChild(section);
    }
    /** Inner stat cards for the graph block (edges + entities + sectors). */
    _graphStatsCards(data) {
      const hy = data.hygiene || {};
      const stale = data.staleness?.stale;
      const staleColor = stale ? "#e63946" : "#2a9d8f";
      const staleLabel = stale ? "Stale" : "Fresh";
      return `
            <div class="stat-card stat-primary">
                <div class="stat-content"><h3>${data.edges.total.toLocaleString()}</h3><p>Total Edges</p></div>
            </div>
            <div class="stat-card stat-secondary">
                <div class="stat-content"><h3>${Object.keys(data.edges.by_type || {}).length}</h3><p>Edge Types</p></div>
            </div>
            <div class="stat-card stat-secondary">
                <div class="stat-content"><h3>${data.entities.total.toLocaleString()}</h3><p>Graph Entities</p></div>
            </div>
            <div class="stat-card stat-secondary">
                <div class="stat-content"><h3>${data.sectors?.count ?? 0}</h3><p>Company Sectors</p></div>
            </div>
            <div class="stat-card stat-secondary">
                <div class="stat-content"><h3>${data.sectors?.top?.[0]?.sector ?? "\u2014"}</h3><p>Top Sector</p></div>
            </div>
            <div class="stat-card stat-secondary">
                <div class="stat-content"><h3 style="color:${staleColor}">${staleLabel}</h3><p>Data Staleness</p></div>
            </div>`;
    }
    /** Degraded fallback when /api/graph/stats is unreachable. */
    displayGraphStatsError() {
      const container = getEl("stats-container");
      if (!container) return;
      const note = document.createElement("div");
      note.className = "hint";
      note.textContent = "Graph statistics could not be loaded.";
      container.appendChild(note);
    }
    createStatCard(title, value, icon, theme) {
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
    createBreakdownCard(title, data, type) {
      const card = document.createElement("div");
      card.className = "breakdown-card";
      const total = Object.values(data).reduce((a, b) => a + b, 0);
      let itemsHtml = "";
      Object.entries(data).forEach(([key, value]) => {
        const percentage = total > 0 ? (value / total * 100).toFixed(1) : "0.0";
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
    formatLabel(key, type) {
      if (type === "market_cap") {
        return key.replace("_", " ").replace(/\b\w/g, (l) => l.toUpperCase());
      }
      if (type === "edge_type") {
        return key.replace(/_/g, " ").replace(/\b\w/g, (l) => l.toUpperCase());
      }
      return key;
    }
    processRichContent(content) {
      let processedHtml = marked.parse(content);
      const headings = [];
      processedHtml = processedHtml.replace(/<h([1-6])[^>]*>(.*?)<\/h[1-6]>/gi, (match, level, text) => {
        const id = text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
        headings.push({
          level: parseInt(level, 10),
          text: text.replace(/<[^>]*>/g, ""),
          id
        });
        return `<h${level} id="${id}">${text}</h${level}>`;
      });
      processedHtml = processedHtml.replace(/<img([^>]+)src="([^"]+)"([^>]*)>/gi, (_match, before, src, after) => {
        const imgId = `img-${Math.random().toString(36).substring(2, 11)}`;
        return `<img${before}src="${src}"${after} class="rich-image" data-img-id="${imgId}" onclick="viewer.openLightbox('${src}')" loading="lazy">`;
      });
      processedHtml = processedHtml.replace(/<pre><code class="language-(\w+)">([\s\S]*?)<\/code><\/pre>/gi, (_match, lang, code) => {
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
      processedHtml = processedHtml.replace(/<code>([\s\S]*?)<\/code>/gi, '<code class="inline-code">$1</code>');
      processedHtml = processedHtml.replace(/<table([^>]*)>([\s\S]*?)<\/table>/gi, (_match, attributes, tableContent) => {
        return `
                <div class="table-wrapper">
                    <table${attributes}>${tableContent}</table>
                </div>
            `;
      });
      processedHtml = processedHtml.replace(/<blockquote>([\s\S]*?)<\/blockquote>/gi, '<blockquote class="rich-blockquote">$1</blockquote>');
      processedHtml = this.processExternalContent(processedHtml);
      return {
        html: processedHtml,
        headings
      };
    }
    generateTableOfContents(headings) {
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
      toc += "</ul>".repeat(currentLevel);
      toc += "</ul>";
      return toc;
    }
    highlightCode(code, language) {
      try {
        if (window.hljs) {
          return window.hljs.highlight(code, { language }).value;
        }
      } catch (e) {
        console.warn("Syntax highlighting failed:", e);
      }
      return this.escapeHtml(code);
    }
    copyCode(codeId) {
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
    openLightbox(imageSrc) {
      const lightbox = getEl("image-lightbox");
      const lightboxImage = getEl("lightbox-image");
      const caption = document.querySelector(".lightbox-caption");
      lightboxImage.src = imageSrc;
      caption.textContent = imageSrc.split("/").pop() || "Image";
      lightbox.style.display = "flex";
      document.body.style.overflow = "hidden";
    }
    closeLightbox() {
      const lightbox = getEl("image-lightbox");
      lightbox.style.display = "none";
      document.body.style.overflow = "";
    }
    processExternalContent(html) {
      html = html.replace(
        /https?:\/\/(?:www\.)?youtube\.com\/watch\?v=([a-zA-Z0-9_-]+)/gi,
        '<div class="video-embed"><iframe src="https://www.youtube.com/embed/$1" frameborder="0" allowfullscreen></iframe></div>'
      );
      html = html.replace(/<a href="([^"]+)"([^>]*)>/gi, (match, href, rest) => {
        const isExternal = href.startsWith("http") && !href.includes(window.location.hostname);
        const externalClass = isExternal ? "external-link" : "";
        const externalIcon = isExternal ? '<i class="fas fa-external-link-alt"></i>' : "";
        return `<a href="${href}"${rest} class="${externalClass}">${externalIcon}`;
      });
      return html;
    }
    initializeInteractiveElements() {
      if (window.Prism) {
        window.Prism.highlightAll();
      }
      document.querySelectorAll(".toc-link").forEach((link) => {
        link.addEventListener("click", (e) => {
          e.preventDefault();
          const targetId = (link.getAttribute("href") || "").substring(1);
          const targetElement = getEl(targetId);
          if (targetElement) {
            targetElement.scrollIntoView({ behavior: "smooth", block: "start" });
          }
        });
      });
      document.querySelectorAll(".rich-image").forEach((img) => {
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
    switchView(view) {
      document.querySelectorAll(".nav-link").forEach((link) => {
        link.classList.remove("active");
      });
      const activeLink = document.querySelector(`[data-view="${view}"]`);
      if (activeLink) activeLink.classList.add("active");
      document.querySelectorAll(".view-section").forEach((section) => {
        section.style.display = "none";
      });
      getEl(`${view}-view`).style.display = "block";
      this.currentView = view;
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
        case "docs":
          this.loadDocsCatalog();
          break;
      }
    }
    // ---------------------------------------------------------------- //
    // Docs view (doc/ corpus browser — catalog, search, reader)         //
    // ---------------------------------------------------------------- //
    /** Load the full catalog (GET /api/docs) into the sidebar. */
    async loadDocsCatalog() {
      this.docs.activePath = null;
      this.hideDocsContentPane();
      try {
        const response = await fetch("/api/docs");
        const data = await response.json();
        this.renderDocsList(data.docs.map((d) => ({
          path: d.path,
          name: d.name,
          section: d.section,
          title: d.title,
          snippet: ""
        })));
        getEl("docs-count").textContent = `${data.docs.length} documents`;
      } catch (error) {
        console.error("Error loading docs:", error);
        getEl("docs-list").innerHTML = '<div class="no-results">Could not load the document catalog.</div>';
      }
    }
    /** Run a corpus search (GET /api/docs/search) and render the hits. */
    async runDocsSearch() {
      const query = getEl("docs-search").value.trim();
      this.docs.activePath = null;
      this.hideDocsContentPane();
      if (!query) {
        this.loadDocsCatalog();
        return;
      }
      try {
        const url = `/api/docs/search?q=${encodeURIComponent(query)}`;
        const response = await fetch(url);
        const data = await response.json();
        this.renderDocsList(data.results);
        const total = data.results.length;
        getEl("docs-count").textContent = total === 0 ? "No matches" : `${total} match${total === 1 ? "" : "es"}`;
      } catch (error) {
        console.error("Error searching docs:", error);
        getEl("docs-list").innerHTML = '<div class="no-results">Search failed. Try again.</div>';
      }
    }
    /**
     * Render the sidebar: one clickable row per doc. Rows built from search
     * hits carry a <mark>-highlighted snippet; catalog rows are plain.
     */
    renderDocsList(items) {
      const list = getEl("docs-list");
      list.innerHTML = "";
      if (items.length === 0) {
        list.innerHTML = '<div class="no-results">No documents match.</div>';
        return;
      }
      items.forEach((item) => {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "docs-row";
        row.dataset.path = item.path;
        const safeSection = item.section ? `<span class="docs-row-section">${this.escapeHtml(item.section)}</span>` : "";
        const safeSnippet = item.snippet ? `<div class="docs-row-snippet">${this.highlightSnippet(item.snippet)}</div>` : "";
        row.innerHTML = `
                <span class="docs-row-title">${this.escapeHtml(item.title)}</span>
                ${safeSection}
                ${safeSnippet}
            `;
        row.addEventListener("click", () => this.openDoc(item.path));
        list.appendChild(row);
      });
    }
    /** Fetch + render one doc's raw markdown/text (GET /api/docs/content). */
    async openDoc(path) {
      this.docs.activePath = path;
      document.querySelectorAll(".docs-row").forEach((row) => {
        row.classList.toggle("active", row.dataset.path === path);
      });
      try {
        const url = `/api/docs/content?path=${encodeURIComponent(path)}`;
        const response = await fetch(url);
        const data = await response.json();
        const { html, headings } = this.processRichContent(data.content);
        getEl("docs-content-empty").style.display = "none";
        const pane = getEl("docs-content-pane");
        pane.style.display = "block";
        pane.innerHTML = `
                <header class="docs-article-header">
                    <h3>${this.escapeHtml(data.title)}</h3>
                    <div class="docs-article-meta">
                        <span>${this.escapeHtml(data.path)}</span>
                        <span>${this.escapeHtml(data.section || "top-level")}</span>
                        <span>${this.formatBytes(data.size_bytes)}</span>
                    </div>
                </header>
                ${headings.length > 1 ? this.renderDocsToc(headings) : ""}
                <div class="docs-article-body">${html}</div>
            `;
      } catch (error) {
        console.error("Error opening doc:", error);
        getEl("docs-content-pane").style.display = "none";
        getEl("docs-content-empty").style.display = "block";
        getEl("docs-content-empty").innerHTML = '<p class="error">Could not load this document.</p>';
      }
    }
    /** Simple TOC linking the <h1..h6 id> headings marked.js produces. */
    renderDocsToc(headings) {
      const items = headings.map((h) => `<li class="toc-${h.level}"><a href="#${encodeURIComponent(h.id)}">${this.escapeHtml(h.text)}</a></li>`).join("");
      return `<nav class="docs-toc"><ul>${items}</ul></nav>`;
    }
    /** Reset the reader pane to its empty state. */
    hideDocsContentPane() {
      getEl("docs-content-pane").style.display = "none";
      getEl("docs-content-pane").innerHTML = "";
      const empty = getEl("docs-content-empty");
      empty.style.display = "block";
      empty.innerHTML = `
            <i class="fas fa-book-open"></i>
            <p>Select a document to read it here.</p>
            <p class="hint">Browse the catalog or search the corpus.</p>
        `;
    }
    // ---------------------------------------------------------------- //
    // Graph view (cytoscape.js — lazy-init on first visit)              //
    // ---------------------------------------------------------------- //
    // All graph-tab state lives under this.graph. cytoscape.js is loaded
    // via a <script> tag in findata.html but only the first click on the
    // Graph tab pays the parse cost; subsequent visits reuse the instance.
    async loadGraphView() {
      if (!this.graph) {
        this.graph = {
          cy: null,
          // cytoscape instance, built on first load
          central: null,
          // currently-centred entity name
          elements: null,
          // last-rendered {nodes, edges} for filter swaps
          entitiesLoaded: false,
          cloudMode: false
        };
      }
      if (!this.graph.cy) {
        if (typeof cytoscape === "undefined") {
          getEl("graph-empty").innerHTML = '<p class="error">Could not load the cytoscape.js graph library. Check your network connection and reload.</p>';
          return;
        }
        const canvas = getEl("graph-canvas");
        this.graph.cy = cytoscape({
          container: canvas,
          elements: [],
          style: this._cytoscapeStyle(),
          layout: { name: "concentric", concentric: (n) => n.data().centrality || 0 },
          wheelSensitivity: 0.2,
          minZoom: 0.2,
          maxZoom: 3
        });
        this.graph.cy.on("tap", "node", async (evt) => {
          const name = evt.target.data().id;
          if (this.graph.cloudMode) {
            this._highlightCloudSet(evt.target.data());
            this._renderGraphDetail(evt.target.data());
            return;
          }
          if (name && name !== this.graph.central) {
            getEl("graph-search").value = name;
            await this.loadEgoNetwork(name);
          }
        });
        this.graph.cy.on("select", "node", (evt) => {
          if (this.graph.cloudMode) this._highlightCloudSet(evt.target.data());
          this._renderGraphDetail(evt.target.data());
        });
        this.graph.cy.on("tap", (evt) => {
          if (!this.graph.cloudMode) return;
          const isNode = evt.target.isNode?.();
          if (!isNode) this._clearCloudHighlight();
        });
        getEl("graph-search-btn").addEventListener("click", async () => {
          const name = getEl("graph-search").value.trim();
          if (name) {
            this._exitCloudMode();
            await this.loadEgoNetwork(name);
          }
        });
        getEl("graph-search").addEventListener("keydown", async (e) => {
          if (e.key === "Enter") {
            const name = e.target.value.trim();
            if (name) {
              this._exitCloudMode();
              await this.loadEgoNetwork(name);
            }
          }
        });
        getEl("graph-layout").addEventListener("change", (e) => {
          const inCloud = !!this.graph && this.graph.cloudMode;
          this._runGraphLayout(e.target.value, inCloud);
          if (inCloud) this.graph.cy.fit(void 0, 30);
        });
        getEl("graph-filter").addEventListener("change", () => {
          this._exitCloudMode();
          if (this.graph.central) this.loadEgoNetwork(this.graph.central);
        });
        getEl("graph-as-of").addEventListener("change", () => {
          this._exitCloudMode();
          if (this.graph.central) this.loadEgoNetwork(this.graph.central);
        });
        getEl("graph-refresh-db").addEventListener("click", async () => {
          const btn = getEl("graph-refresh-db");
          btn.disabled = true;
          try {
            const r = await fetch("/api/graph/refresh", { method: "POST" });
            const data = await r.json();
            this._setGraphStatus(data.status === "ok" ? "DB refreshed \u2014 re-search to see new data" : "refresh failed");
            this.graph.elements = null;
            this.graph.central = null;
          } catch (e) {
            this._setGraphStatus("refresh failed: " + e.message);
          } finally {
            btn.disabled = false;
          }
        });
        getEl("shortest-btn").addEventListener("click", () => this.loadShortestPath());
        getEl("shortest-clear").addEventListener("click", () => this.clearShortestPath());
        getEl("graph-cloud-toggle").addEventListener("click", () => this.toggleGraphCloud());
        getEl("graph-cloud-type").addEventListener("change", () => {
          if (this.graph && this.graph.cloudMode) this.loadGraphCloud();
        });
        this._initGraphZoom();
      }
      if (!this.graph.entitiesLoaded) {
        await this.loadGraphEntityList();
        this.graph.entitiesLoaded = true;
      }
      setTimeout(() => this.graph && this.graph.cy && this.graph.cy.resize(), 50);
    }
    async loadGraphEntityList() {
      try {
        const dl = getEl("graph-entities-list");
        const parts = [];
        const rc = await fetch("/api/entities?type=company&limit=3000");
        const dc = await rc.json();
        (dc.entities || []).forEach((e) => {
          parts.push(`<option value="${e.name}">${e.name}</option>`);
        });
        const rs = await fetch("/api/entities?type=sector&limit=500");
        const ds = await rs.json();
        (ds.entities || []).forEach((e) => {
          parts.push(`<option value="${e.name}">${e.name} (sector)</option>`);
        });
        dl.innerHTML = parts.join("");
      } catch (e) {
        console.warn("graph typeahead load failed", e);
      }
    }
    /** Toggle the whole-graph cloud on/off. */
    async toggleGraphCloud() {
      if (!this.graph) return;
      this.graph.cloudMode = !this.graph.cloudMode;
      if (this.graph.cloudMode) {
        await this.loadGraphCloud();
      } else {
        this._exitCloudMode();
      }
    }
    /** Leave cloud mode: hide the cloud panel + restore the ego canvas. */
    _exitCloudMode() {
      if (!this.graph) return;
      this.graph.cloudMode = false;
      this._clearCloudHighlight();
      getEl("graph-cloud-panel").style.display = "none";
      getEl("graph-cloud-toggle").classList.remove("active");
      if (!this.graph.central) {
        getEl("graph-empty").style.display = "flex";
      }
    }
    /** Fetch + render the whole graph (optionally one relationship type). */
    async loadGraphCloud() {
      if (!this.graph || !this.graph.cy) return;
      this._setGraphStatus("Loading full graph...");
      getEl("graph-cloud-toggle").classList.add("active");
      getEl("graph-cloud-panel").style.display = "block";
      const filter = getEl("graph-cloud-type").value;
      const url = `/api/graph/cloud` + (filter ? `?edge_type=${encodeURIComponent(filter)}` : "");
      let data;
      try {
        const r = await fetch(url);
        if (!r.ok) {
          const err = await r.json().catch(() => ({ error: r.statusText }));
          throw new Error(err.error || `HTTP ${r.status}`);
        }
        data = await r.json();
      } catch (e) {
        this._setGraphStatus(`Error: ${e.message}`);
        return;
      }
      this.graph.cloudMode = true;
      this.graph.central = null;
      this.graph.entityType = void 0;
      this._clearCloudHighlight();
      const degree = {};
      data.edges.forEach((e) => {
        degree[e.source] = (degree[e.source] || 0) + 1;
        degree[e.target] = (degree[e.target] || 0) + 1;
      });
      const root = /* @__PURE__ */ new Map();
      const find = (x) => {
        if (root.get(x) !== x) root.set(x, find(root.get(x)));
        return root.get(x);
      };
      const union = (a, b) => {
        const ra = find(a);
        const rb = find(b);
        if (ra !== rb) root.set(ra, rb);
      };
      data.nodes.forEach((n) => root.set(n.id, n.id));
      data.edges.forEach((e) => union(e.source, e.target));
      const elements = data.nodes.map((n) => ({
        data: {
          id: n.id,
          label: n.label,
          group: n.entity_type,
          cloud: "1",
          centrality: degree[n.id] || 0,
          component: find(n.id)
        }
      }));
      data.edges.forEach((e) => {
        elements.push({
          data: {
            id: `${e.source}__${e.target}__${e.edge_type}`,
            source: e.source,
            target: e.target,
            type: e.edge_type,
            label: "",
            // no per-edge text — 4110 labels is the #1 render cost
            cloud: "1",
            component: find(e.source)
          }
        });
      });
      this.graph.cy.elements().remove();
      this.graph.cy.add(elements);
      this.graph.elements = elements;
      const componentCount = new Set(elements.filter((e) => e.data.id && !e.data.source).map((e) => e.data.component)).size;
      this._runGraphLayout(componentCount > 1 ? "components" : "cose", true);
      this.graph.cy.fit(void 0, 30);
      getEl("graph-empty").style.display = "none";
      this._renderCloudLegend(data);
      this._renderRelationshipCloud(data.relationship_types);
      const typeLabel = filter || "all relationships";
      this._setGraphStatus(
        `Full graph \u2014 ${data.total_nodes} entities \xB7 ${data.total_edges} ${typeLabel}` + (data.total_edges !== 1 ? "s" : "")
      );
    }
    /**
     * Highlight the connected set (component) a tapped node belongs to: every
     * element sharing its component root gets the `in-set` style (bright
     * edges), everything else fades to background.
     */
    _highlightCloudSet(nodeData) {
      const cy = this.graph && this.graph.cy;
      if (!cy || !this.graph || !this.graph.cloudMode) return;
      const comp = nodeData.component;
      cy.elements().removeClass("in-set faded");
      if (!comp) return;
      cy.elements().forEach((el) => {
        if (el.data().component === comp) {
          el.removeClass("faded").addClass("in-set");
        } else {
          el.addClass("faded").removeClass("in-set");
        }
      });
    }
    /** Remove the cloud set-highlight (restores full opacity to all elements). */
    _clearCloudHighlight() {
      if (this.graph && this.graph.cy) {
        this.graph.cy.elements().removeClass("in-set faded");
      }
    }
    /** Legend: entity-type swatches + edge-type colour chips. */
    _renderCloudLegend(data) {
      const legend = getEl("graph-cloud-legend");
      const nodeTypes = [...new Set(data.nodes.map((n) => n.entity_type))].sort();
      const edgeTypes = [...new Set(data.edges.map((e) => e.edge_type))].sort();
      const nodeHtml = nodeTypes.map((t) => `
            <span class="cloud-legend-chip">
                <span class="cloud-swatch cloud-node-${CSS.escape(t)}">${this.escapeHtml(t)}</span>
            </span>`).join("");
      const edgeHtml = edgeTypes.map((t) => `
            <span class="cloud-legend-chip">
                <span class="cloud-swatch" style="background:${this.EDGE_COLORS[t] || "#5a6577"}">${this.escapeHtml(t)}</span>
            </span>`).join("");
      legend.innerHTML = `
            <div class="cloud-legend-group"><strong>Entities</strong><div class="cloud-legend-chips">${nodeHtml}</div></div>
            <div class="cloud-legend-group"><strong>Relationships</strong><div class="cloud-legend-chips">${edgeHtml}</div></div>`;
    }
    /** Relationship cloud card: one size-proportional chip per edge type. */
    _renderRelationshipCloud(types) {
      const card = getEl("graph-relationship-cloud");
      const select = getEl("graph-cloud-type");
      const current = select.value;
      const currentSemantics = types.find((t) => t.edge_type === current);
      const opts = [`<option value="">All relationships</option>`];
      types.forEach((t) => {
        const arrow = t.symmetric ? "\u2194" : "\u2192";
        opts.push(`<option value="${this.escapeHtml(t.edge_type)}">${this.escapeHtml(t.edge_type)} (${t.count}) ${arrow}</option>`);
      });
      select.innerHTML = opts.join("");
      if (current && currentSemantics) select.value = current;
      else select.value = "";
      if (!types.length) {
        card.innerHTML = '<p class="hint">No relationships in the graph.</p>';
        return;
      }
      const max = Math.max(...types.map((t) => t.count), 1);
      const chips = types.map((t) => {
        const ratio = t.count / max;
        const size = 0.85 + ratio * 1.35;
        const color = this.EDGE_COLORS[t.edge_type] || "#5a6577";
        const arrow = t.symmetric ? "\u2194" : "\u2192";
        return `<button type="button" class="rel-cloud-chip"
                        title="${this.escapeHtml(`${t.semantics} \u2014 ${t.count} edge${t.count !== 1 ? "s" : ""}`)}"
                        data-edge-type="${this.escapeHtml(t.edge_type)}"
                        style="font-size:${size.toFixed(2)}rem; color:${color};">
                    ${this.escapeHtml(t.edge_type)}
                    <span class="rel-cloud-count">${t.count} ${arrow}</span>
                </button>`;
      }).join("");
      card.innerHTML = `<h4 class="rel-cloud-title"><i class="fas fa-cloud"></i> Relationship Cloud</h4>
                          <div class="rel-cloud-chips">${chips}</div>`;
      card.querySelectorAll(".rel-cloud-chip").forEach((chip) => {
        chip.addEventListener("click", () => {
          const et = chip.dataset.edgeType;
          if (!et) return;
          const select2 = getEl("graph-cloud-type");
          select2.value = et;
          this.loadGraphCloud();
        });
      });
    }
    async loadEgoNetwork(name) {
      this._setGraphStatus(`Loading ${name}...`);
      const asOf = getEl("graph-as-of").value;
      const params = new URLSearchParams();
      if (asOf) params.set("as_of", asOf);
      const qs = params.toString();
      let data;
      try {
        const url = `/api/graph/neighbors/${encodeURIComponent(name)}` + (qs ? `?${qs}` : "");
        const r = await fetch(url);
        if (!r.ok) {
          const err = await r.json().catch(() => ({ error: r.statusText }));
          throw new Error(err.error || `HTTP ${r.status}`);
        }
        data = await r.json();
      } catch (e) {
        this._setGraphStatus(`Error: ${e.message}`);
        return;
      }
      if (!this.graph || !this.graph.cy) return;
      const isSector = data.entity_type === "sector";
      const filter = isSector ? "all" : getEl("graph-filter").value;
      const elements = isSector ? this._buildSectorEgoElements(data) : this._buildEgoElements(data, filter);
      this.graph.cy.elements().remove();
      this.graph.cy.add(elements);
      this.graph.central = isSector ? data.sector : data.company;
      this.graph.elements = elements;
      this.graph.entityType = isSector ? "sector" : "company";
      this._runGraphLayout(getEl("graph-layout").value);
      this.graph.cy.getElementById(this.graph.central).addClass("focal").select();
      const focalData = this.graph.cy.getElementById(this.graph.central).data();
      focalData.__bundle = data;
      this._renderGraphDetail(focalData);
      const asOfSuffix = asOf ? ` \xB7 as of ${asOf}` : "";
      if (isSector) {
        const sectorBundle = data;
        this._setGraphStatus(
          `${sectorBundle.sector} \u2014 ${sectorBundle.member_count} member` + (sectorBundle.member_count !== 1 ? "s" : "") + asOfSuffix
        );
      } else {
        const companyBundle = data;
        const counts = {
          peers: companyBundle.peers.length,
          jv: companyBundle.jv_partners.length,
          siblings: companyBundle.group_siblings.length,
          acquired: companyBundle.acquired.length,
          suppliers: companyBundle.suppliers.length,
          customers: companyBundle.customers.length
        };
        const total = Object.values(counts).reduce((a, b) => a + b, 0);
        this._setGraphStatus(
          `${companyBundle.company} \u2014 ${total} relationship${total !== 1 ? "s" : ""}` + (companyBundle.sector ? ` \xB7 ${companyBundle.sector}` : "") + asOfSuffix
        );
      }
      getEl("graph-empty").style.display = "none";
    }
    _buildSectorEgoElements(data) {
      const focal = data.sector;
      const MAX = 60;
      const all = data.members || [];
      const truncated = all.length > MAX;
      const shown = truncated ? all.slice(0, MAX) : all;
      const nodes = [
        { data: { id: focal, label: focal, group: "sector-focal", centrality: 10 } }
      ];
      const edges = [];
      shown.forEach((m) => {
        nodes.push({
          data: { id: m, label: m, group: "member", centrality: 5 }
        });
        edges.push({
          data: {
            id: `${focal}__${m}`,
            source: focal,
            target: m,
            type: "has_company",
            label: "has"
          }
        });
      });
      if (truncated) {
        const moreId = `__more_${focal}`;
        nodes.push({
          data: {
            id: moreId,
            label: `+${all.length - MAX} more (open sector page)`,
            group: "more",
            centrality: 4
          }
        });
        edges.push({
          data: {
            id: `${focal}__${moreId}`,
            source: focal,
            target: moreId,
            type: "has_company",
            label: "more"
          }
        });
      }
      return [...nodes, ...edges];
    }
    _buildEgoElements(data, filter) {
      const nodes = [];
      const edges = [];
      const focal = data.company;
      const addNode = (name, group) => {
        if (!name || name === focal) return;
        nodes.push({
          data: {
            id: name,
            label: name,
            group,
            centrality: 0
            // assigned by layout below
          }
        });
      };
      const addEdge = (src, dst, type, label, props = {}) => {
        edges.push({
          data: { id: `${src}__${dst}__${type}`, source: src, target: dst, type, label, props }
        });
      };
      nodes.push({ data: { id: focal, label: focal, group: "focal", centrality: 10 } });
      if (filter === "all" || filter === "peers") {
        data.peers.forEach((p) => {
          addNode(p, "peer");
          addEdge(focal, p, "competes_with", "peer");
        });
      }
      if (filter === "all" || filter === "jv") {
        data.jv_partners.forEach((j) => {
          addNode(j.partner, "jv");
          addEdge(focal, j.partner, "jv_with", "JV" + (j.venture ? `: ${j.venture}` : ""));
        });
      }
      if (filter === "all") {
        data.group_siblings.forEach((s) => {
          addNode(s, "sibling");
          addEdge(focal, s, "same_group", "same group");
        });
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
        data.suppliers.forEach((s) => {
          addNode(s, "supplier");
          addEdge(s, focal, "supplies_to", "supplies");
        });
        data.customers.forEach((c) => {
          addNode(c, "customer");
          addEdge(focal, c, "supplies_to", "customer");
        });
      }
      if (filter === "all" && data.sector) {
        const sectorId = `sector:${data.sector}`;
        nodes.push({ data: { id: sectorId, label: data.sector, group: "sector", centrality: 8 } });
        edges.push({ data: { id: `${focal}__${sectorId}`, source: focal, target: sectorId, type: "part_of", label: "part of" } });
      }
      nodes.forEach((n) => {
        if (n.data.group === "peer") n.data.centrality = 6;
        else if (n.data.group === "parent") n.data.centrality = 7;
        else if (n.data.group === "sector") n.data.centrality = 8;
      });
      return [...nodes, ...edges];
    }
    /** Wire the zoom slider / buttons / fit to the cytoscape instance. */
    _initGraphZoom() {
      if (!this.graph || !this.graph.cy) return;
      const slider = getEl("graph-zoom");
      const label = getEl("graph-zoom-label");
      const cy = this.graph.cy;
      const applyZoom = () => {
        const z = parseFloat(slider.value) || 1;
        cy.zoom(z);
        label.textContent = `${Math.round(z * 100)}%`;
      };
      slider.addEventListener("input", applyZoom);
      getEl("graph-zoom-in").addEventListener("click", () => {
        const next = Math.min(cy.maxZoom(), cy.zoom() * 1.25);
        cy.zoom(next);
      });
      getEl("graph-zoom-out").addEventListener("click", () => {
        const next = Math.max(cy.minZoom(), cy.zoom() / 1.25);
        cy.zoom(next);
      });
      getEl("graph-zoom-fit").addEventListener("click", () => cy.fit(void 0, 30));
      cy.on("zoom", () => {
        const z = cy.zoom();
        slider.value = String(z);
        label.textContent = `${Math.round(z * 100)}%`;
      });
      cy.on("layoutstop", () => {
        const z = cy.zoom();
        slider.value = String(z);
        label.textContent = `${Math.round(z * 100)}%`;
      });
    }
    _runGraphLayout(name, cloud = false) {
      if (!this.graph || !this.graph.cy || this.graph.cy.elements().length === 0) return;
      if (name === "components") {
        const els = this.graph.elements || [];
        if (els.some((e) => e.data.component)) {
          const positions = this._cloudComponentPositions(els);
          if (Object.keys(positions).length) {
            this.graph.cy.layout({
              name: "preset",
              positions,
              animate: !cloud,
              animationDuration: 300
            }).run();
            return;
          }
        }
        name = "concentric";
      }
      const opts = { name, animate: !cloud, animationDuration: 300 };
      if (name === "cose") {
        opts.nodeRepulsion = () => 8e3;
        opts.idealEdgeLength = () => 100;
        opts.nodeOverlap = 20;
        if (cloud) {
          opts.randomize = true;
          opts.numIter = 300;
          opts.initialTemp = 200;
          opts.coolingFactor = 0.8;
          opts.minTemp = 1;
          opts.gravity = 2;
        }
      } else if (name === "concentric") {
        opts.concentric = (n) => n.data().centrality || 0;
        opts.levelWidth = () => 1;
        opts.minNodeSpacing = 30;
      } else if (name === "breadthfirst") {
        opts.directed = true;
        opts.spacingFactor = 1.2;
        opts.roots = this.graph.central ? `#${CSS.escape(this.graph.central)}` : void 0;
      }
      this.graph.cy.layout(opts).run();
    }
    /**
     * Positions for the component-separating cloud layout. Each connected
     * component (all nodes sharing a union-find root id) is packed into its
     * own grid cell: the component's highest-degree hub sits at the cell
     * centre and the remaining members orbit on a circle around it. Grid
     * cells are sized by the largest component so sets never overlap.
     */
    _cloudComponentPositions(els) {
      const nodes = els.filter((e) => e.data.id && !e.data.source);
      if (!nodes.length) return {};
      const compMap = /* @__PURE__ */ new Map();
      nodes.forEach((n) => {
        const c = n.data.component || n.data.id;
        if (!compMap.has(c)) compMap.set(c, []);
        compMap.get(c).push(n.data.id);
      });
      const comps = [...compMap.values()].sort((a, b) => b.length - a.length);
      const degree = {};
      els.forEach((e) => {
        if (e.data.source && e.data.target) {
          degree[e.data.source] = (degree[e.data.source] || 0) + 1;
          degree[e.data.target] = (degree[e.data.target] || 0) + 1;
        }
      });
      const nodeSpacing = 40;
      const cellPad = 90;
      const cellRadius = (n) => Math.max(30, Math.sqrt(n) * nodeSpacing / 2);
      const maxR = Math.max(...comps.map((c) => cellRadius(c.length)));
      const cell = maxR * 2 + cellPad;
      const cols = Math.max(1, Math.ceil(Math.sqrt(comps.length)));
      const positions = {};
      comps.forEach((comp, i) => {
        const cx = i % cols * cell + cell / 2;
        const cy = Math.floor(i / cols) * cell + cell / 2;
        const r = cellRadius(comp.length);
        const sorted = [...comp].sort((a, b) => (degree[b] || 0) - (degree[a] || 0));
        const hub = sorted[0];
        positions[hub] = { x: cx, y: cy };
        sorted.slice(1).forEach((id, j) => {
          const ang = j / (sorted.length - 1) * Math.PI * 2;
          positions[id] = {
            x: cx + Math.cos(ang) * r,
            y: cy + Math.sin(ang) * r
          };
        });
      });
      return positions;
    }
    _renderGraphDetail(nodeData) {
      const panel = getEl("graph-detail");
      if (!nodeData) {
        panel.innerHTML = '<div class="graph-detail-empty"><i class="fas fa-hand-pointer"></i><p>Click a node to centre the graph on it.</p></div>';
        return;
      }
      const name = nodeData.id;
      const group = nodeData.group || "company";
      const bundle = nodeData.__bundle;
      let html = `<div class="graph-detail-header">
            <span class="graph-badge graph-badge-${group}">${group}</span>
            <h3>${this.escapeHtml(name)}</h3>
        </div>`;
      if (bundle && bundle.entity_type === "sector") {
        const sectorBundle = bundle;
        html += `<ul class="graph-detail-list">`;
        html += `<li><strong>Members:</strong> ${sectorBundle.member_count}</li>`;
        const mc = sectorBundle.market_cap_counts || {};
        Object.keys(mc).forEach((k) => {
          html += `<li><strong>${this.escapeHtml(k)}:</strong> ${mc[k]}</li>`;
        });
        html += `</ul>`;
        if (sectorBundle.file_path) {
          html += `<a class="btn-primary" href="/entity/${sectorBundle.file_path}">View sector note \u2192</a>`;
        }
      } else if (bundle) {
        const companyBundle = bundle;
        html += `<ul class="graph-detail-list">`;
        if (companyBundle.sector) html += `<li><strong>Sector:</strong> ${this.escapeHtml(companyBundle.sector)}</li>`;
        if (companyBundle.subsidiary_of) html += `<li><strong>Parent:</strong> ${this.escapeHtml(companyBundle.subsidiary_of)}</li>`;
        html += `<li><strong>Peers:</strong> ${companyBundle.peers.length || "\u2014"}</li>`;
        html += `<li><strong>JV partners:</strong> ${companyBundle.jv_partners.length || "\u2014"}</li>`;
        html += `<li><strong>Group siblings:</strong> ${companyBundle.group_siblings.length || "\u2014"}</li>`;
        html += `<li><strong>Acquired:</strong> ${companyBundle.acquired.length || "\u2014"}</li>`;
        html += `<li><strong>Suppliers:</strong> ${companyBundle.suppliers.length || "\u2014"}</li>`;
        html += `<li><strong>Customers:</strong> ${companyBundle.customers.length || "\u2014"}</li>`;
        html += `</ul>`;
        if (companyBundle.file_path) {
          html += `<a class="btn-primary" href="/entity/${companyBundle.file_path}">View full note \u2192</a>`;
        }
      } else if (group === "more") {
        html += `<p class="hint">This sector has more members than the graph renders (cap: 60). Use the sector page for the full list.</p>`;
        if (this.graph && this.graph.central) {
          html += `<a class="btn-primary" href="/api/graph/sector/${encodeURIComponent(this.graph.central)}">View all members (JSON)</a>`;
        }
      } else {
        html += `<p class="hint">Click this node (or it's already selected) to re-centre on <em>${this.escapeHtml(name)}</em>.</p>`;
        html += `<button class="btn-primary" onclick="getEl('graph-search').value='${name.replace(/'/g, "\\'")}'; document.getElementById('graph-search-btn').click();">Centre on ${this.escapeHtml(name)}</button>`;
      }
      panel.innerHTML = html;
    }
    _setGraphStatus(text) {
      const el = getEl("graph-status");
      if (el) el.textContent = text;
    }
    _cytoscapeStyle() {
      return cytoscape.stylesheet().selector("node").style({
        "label": "data(label)",
        "text-valign": "bottom",
        "text-halign": "center",
        "text-outline-width": 2,
        "text-outline-color": "#1f2933",
        "color": "#f0f4f8",
        "font-size": 11,
        "width": 28,
        "height": 28,
        "background-color": "#5a6577"
      }).selector('node[group="focal"]').style({
        "background-color": "#e63946",
        "width": 44,
        "height": 44,
        "font-size": 14,
        "font-weight": "bold"
      }).selector('node[group="peer"]').style({ "background-color": "#f4a261" }).selector('node[group="jv"]').style({ "background-color": "#2a9d8f" }).selector('node[group="sibling"]').style({ "background-color": "#8d99ae" }).selector('node[group="acquired"]').style({ "background-color": "#9d4edd" }).selector('node[group="parent"]').style({ "background-color": "#43aa8b" }).selector('node[group="supplier"]').style({ "background-color": "#577590" }).selector('node[group="customer"]').style({ "background-color": "#577590" }).selector('node[group="company"]').style({ "background-color": "#5a6577" }).selector('node[group="theme"]').style({
        "background-color": "#e9c46a",
        "shape": "hexagon",
        "width": 24,
        "height": 24
      }).selector('node[group="super_sector"]').style({
        "background-color": "#8d99ae",
        "shape": "rectangle",
        "width": 54,
        "height": 32
      }).selector('node[group="sub_sector"]').style({
        "background-color": "#7d8597",
        "shape": "round-rectangle",
        "width": 44,
        "height": 28
      }).selector('node[group="sector"]').style({
        "background-color": "#4a6fa5",
        "shape": "rectangle",
        "width": 50,
        "height": 30
      }).selector('node[group="sector-focal"]').style({
        "background-color": "#3d5a80",
        "shape": "rectangle",
        "width": 56,
        "height": 36,
        "font-size": 14,
        "font-weight": "bold"
      }).selector('node[group="member"]').style({ "background-color": "#7d8597" }).selector('node[group="more"]').style({
        "background-color": "#6c757d",
        "shape": "diamond",
        "width": 24,
        "height": 24,
        "font-size": 9
      }).selector("node.focal").style({ "border-width": 3, "border-color": "#ffd166" }).selector("edge").style({
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
        "text-background-opacity": 0.7
      }).selector('edge[type="competes_with"]').style({ "line-color": "#f4a261", "target-arrow-color": "#f4a261" }).selector('edge[type="jv_with"]').style({ "line-color": "#2a9d8f", "target-arrow-color": "#2a9d8f" }).selector('edge[type="acquired"]').style({ "line-color": "#9d4edd", "target-arrow-color": "#9d4edd" }).selector('edge[type="subsidiary_of"]').style({ "line-color": "#43aa8b", "target-arrow-color": "#43aa8b" }).selector('edge[type="supplies_to"]').style({ "line-color": "#577590", "target-arrow-color": "#577590" }).selector('edge[type="part_of"]').style({ "line-color": "#4a6fa5", "target-arrow-color": "#4a6fa5" }).selector('edge[type="has_company"]').style({ "line-color": "#3d5a80", "target-arrow-color": "#3d5a80" }).selector('edge[type="co_mentioned_in"]').style({ "line-color": "#7fd1ae", "target-arrow-color": "#7fd1ae" }).selector('edge[type="exposed_to"]').style({ "line-color": "#e9c46a", "target-arrow-color": "#e9c46a" }).selector('edge[type="belongs_to"]').style({ "line-color": "#8d99ae", "target-arrow-color": "#8d99ae" }).selector('edge[type="supplier_to"]').style({ "line-color": "#577590", "target-arrow-color": "#577590" }).selector('edge[type="customer_of"]').style({ "line-color": "#e5989b", "target-arrow-color": "#e5989b" }).selector('edge[type="same_group"]').style({ "line-color": "#b5838d", "target-arrow-color": "#b5838d" }).selector('edge[type="co_mentioned_in"], edge[type="jv_with"], edge[type="competes_with"], edge[type="same_group"]').style({ "target-arrow-shape": "none" }).selector("edge.highlighted").style({
        "width": 4,
        "line-color": "#ffd166",
        "target-arrow-color": "#ffd166",
        "z-index": 10
      }).selector("node.highlighted").style({
        "border-width": 3,
        "border-color": "#ffd166",
        "z-index": 10
      }).selector('edge[cloud="1"]').style({
        "curve-style": "straight",
        "width": 1,
        "label": "",
        "text-opacity": 0,
        "font-size": 0
      }).selector('node[cloud="1"]').style({
        "font-size": 9,
        "text-outline-width": 1,
        "min-zoomed-font-size": 6
      }).selector("edge.in-set").style({
        "width": 4,
        "line-color": "#ffd166",
        "target-arrow-color": "#ffd166",
        "z-index": 12,
        "overlay-opacity": 0
      }).selector("node.in-set").style({
        "border-width": 3,
        "border-color": "#ffd166",
        "z-index": 12
      }).selector(".faded").style({ "opacity": 0.25 });
    }
    async loadShortestPath() {
      const a = getEl("shortest-a").value.trim();
      const b = getEl("shortest-b").value.trim();
      const result = getEl("shortest-result");
      if (!a || !b) {
        result.innerHTML = '<p class="hint">Enter both entities.</p>';
        return;
      }
      const asOf = getEl("graph-as-of").value;
      const params = new URLSearchParams({ a, b });
      if (asOf) params.set("as_of", asOf);
      result.innerHTML = '<p><i class="fas fa-spinner fa-spin"></i> Finding path...</p>';
      try {
        const r = await fetch(`/api/graph/shortest?${params}`);
        if (!r.ok) {
          const err = await r.json().catch(() => ({ error: r.statusText }));
          throw new Error(err.error || `HTTP ${r.status}`);
        }
        const data = await r.json();
        this._renderShortestPath(data);
      } catch (e) {
        result.innerHTML = `<p class="error">${this.escapeHtml(e.message)}</p>`;
      }
    }
    _renderShortestPath(data) {
      const result = getEl("shortest-result");
      if (data.path === null) {
        result.innerHTML = `<p class="hint">No path found between <em>${this.escapeHtml(data.source)}</em> and <em>${this.escapeHtml(data.target)}</em> within the hop limit.</p>`;
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
      if (this.graph && this.graph.cy) {
        const cy = this.graph.cy;
        cy.elements().removeClass("highlighted faded");
        const pathNodes = chain.filter((n) => cy.getElementById(n).length > 0);
        if (pathNodes.length === chain.length) {
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
    clearShortestPath() {
      getEl("shortest-result").innerHTML = "";
      getEl("shortest-a").value = "";
      getEl("shortest-b").value = "";
      if (this.graph && this.graph.cy) {
        this.graph.cy.elements().removeClass("highlighted faded");
      }
    }
    setLayout(layout) {
      this.currentLayout = layout;
      document.querySelectorAll(".view-btn").forEach((btn) => {
        btn.classList.remove("active");
      });
      const activeBtn = document.querySelector(`[data-layout="${layout}"]`);
      if (activeBtn) activeBtn.classList.add("active");
      const container = getEl("companies-container");
      container.className = `entities-container ${layout}-layout`;
    }
    updatePagination() {
      const container = getEl("companies-pagination");
      const totalPages = Math.ceil(this.totalCount / this.pageSize);
      if (totalPages <= 1) {
        container.innerHTML = "";
        return;
      }
      let paginationHtml = "";
      if (this.currentPage > 0) {
        paginationHtml += `<button class="page-btn" onclick="viewer.goToPage(${this.currentPage - 1})">Previous</button>`;
      }
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
      if (this.currentPage < totalPages - 1) {
        paginationHtml += `<button class="page-btn" onclick="viewer.goToPage(${this.currentPage + 1})">Next</button>`;
      }
      container.innerHTML = paginationHtml;
    }
    goToPage(page) {
      this.currentPage = page;
      if (this.searchMode === "content" && this.currentView === "companies") {
        this.performContentSearch();
      } else {
        this.loadEntities(false);
      }
    }
    updateCount() {
      const countLabel = getEl("companies-count");
      const start = this.currentPage * this.pageSize + 1;
      const end = Math.min(start + this.pageSize - 1, this.totalCount);
      countLabel.textContent = `${start}-${end} of ${this.totalCount}`;
    }
    showLoading(show) {
      const loading = getEl("loading");
      loading.style.display = show ? "block" : "none";
    }
    showError(message) {
      const toast = document.createElement("div");
      toast.className = "toast error";
      toast.innerHTML = `
            <i class="fas fa-exclamation-circle"></i>
            <span>${this.escapeHtml(message)}</span>
        `;
      document.body.appendChild(toast);
      setTimeout(() => {
        toast.remove();
      }, 3e3);
    }
    // showToast is invoked by copyCode but wasn't in the original; declared so
    // the call type-checks. Kept as a no-op-friendly fallback that reuses
    // showError's toast plumbing (matches the prior runtime behavior).
    showToast(message, _kind) {
      const toast = document.createElement("div");
      toast.className = `toast ${_kind}`;
      toast.innerHTML = `<span>${this.escapeHtml(message)}</span>`;
      document.body.appendChild(toast);
      setTimeout(() => toast.remove(), 3e3);
    }
    escapeHtml(text) {
      const div = document.createElement("div");
      div.textContent = text;
      return div.innerHTML;
    }
    truncateText(text, maxLength) {
      if (text.length <= maxLength) return text;
      return text.substring(0, maxLength) + "...";
    }
    /** Human-readable byte size (e.g. "9.3 KB"). */
    formatBytes(bytes) {
      if (bytes < 1024) return `${bytes} B`;
      const units = ["KB", "MB", "GB"];
      let value = bytes;
      let unit = -1;
      do {
        value /= 1024;
        unit += 1;
      } while (value >= 1024 && unit < units.length - 1);
      return `${value.toFixed(1)} ${units[unit]}`;
    }
  };
  var viewer = new FinDataViewer();
  window.viewer = viewer;
})();
//# sourceMappingURL=findata.bundle.js.map
