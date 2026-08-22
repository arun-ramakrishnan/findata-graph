"use strict";
(() => {
  // src/core/dom.ts
  function getEl(id) {
    const node = document.getElementById(id);
    if (!node) {
      throw new Error(`expected element #${id} not found in DOM`);
    }
    return node;
  }
  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  // src/core/api.ts
  var ApiError = class extends Error {
    constructor(status, message) {
      super(message);
      this.status = status;
    }
  };
  function extractErrorMessage(body, fallback) {
    if (body && typeof body === "object" && "error" in body) {
      const err = body.error;
      if (typeof err === "string" && err) return err;
    }
    return fallback;
  }
  async function fetchJson(url, init) {
    const response = await fetch(url);
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new ApiError(response.status, extractErrorMessage(body, response.statusText || `HTTP ${response.status}`));
    }
    return await response.json();
  }

  // src/core/toast.ts
  function showToast(message, kind) {
    const toast = document.createElement("div");
    toast.className = `toast ${kind}`;
    toast.innerHTML = `<span>${escapeHtml(message)}</span>`;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3e3);
  }

  // src/core/markdown.ts
  function processRichContent(content) {
    let processedHtml = DOMPurify.sanitize(marked.parse(content));
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
      return `<img${before}src="${src}"${after} class="rich-image" data-img-id="${imgId}" data-lightbox="${escapeAttr(src)}" loading="lazy">`;
    });
    processedHtml = processedHtml.replace(/<pre><code class="language-(\w+)">([\s\S]*?)<\/code><\/pre>/gi, (_match, lang, code) => {
      const codeId = `code-${Math.random().toString(36).substring(2, 11)}`;
      const highlightedCode = highlightCode(code, lang);
      return `
            <div class="code-block">
                <div class="code-header">
                    <span class="code-language">${lang}</span>
                    <button class="code-copy" data-copy="${codeId}" title="Copy code">
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
    processedHtml = processExternalContent(processedHtml);
    return {
      html: processedHtml,
      headings
    };
  }
  function highlightCode(code, language) {
    try {
      if (window.hljs) {
        return window.hljs.highlight(code, { language }).value;
      }
    } catch (e) {
      console.warn("Syntax highlighting failed:", e);
    }
    return escapeHtml(code);
  }
  function copyCode(codeId) {
    const codeElement = getEl(codeId);
    if (codeElement) {
      const text = codeElement.textContent;
      navigator.clipboard.writeText(text || "").then(() => {
        showToast("Code copied to clipboard!", "success");
      }).catch((err) => {
        console.error("Failed to copy code:", err);
        showToast("Failed to copy code", "error");
      });
    }
  }
  function openLightbox(imageSrc) {
    const lightbox = getEl("image-lightbox");
    const lightboxImage = getEl("lightbox-image");
    const caption = document.querySelector(".lightbox-caption");
    lightboxImage.src = imageSrc;
    caption.textContent = imageSrc.split("/").pop() || "Image";
    lightbox.style.display = "flex";
    document.body.style.overflow = "hidden";
  }
  function closeLightbox() {
    const lightbox = getEl("image-lightbox");
    lightbox.style.display = "none";
    document.body.style.overflow = "";
  }
  function processExternalContent(html) {
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
  function escapeAttr(text) {
    return text.replace(/&/g, "&amp;").replace(/"/g, "&quot;");
  }
  var lightboxOpener = openLightbox;
  function setLightboxOpener(opener) {
    lightboxOpener = opener;
  }
  function wireRichInteractions(root) {
    root.addEventListener("click", (e) => {
      const target = e.target;
      const img = target.closest("img.rich-image[data-lightbox]");
      if (img) {
        lightboxOpener(img.dataset.lightbox || img.src);
        return;
      }
      const copy = target.closest(".code-copy[data-copy]");
      if (copy && copy.dataset.copy) {
        copyCode(copy.dataset.copy);
      }
    });
  }

  // src/entity.ts
  var SERIES_LABELS = {
    The_Chatter: "The Chatter",
    Points_And_Figures: "Points & Figures",
    The_PlotLines: "The Plotlines"
  };
  var WIKILINK_RE = /\[\[([^\[\]|]+?)(?:#[^\[\]|]*)?(?:\|([^\[\]]+?))?\]\]/g;
  var CHIP_KEYS = ["ticker", "sector", "industry", "market_cap", "created", "last_modified"];
  var EntityPage = class {
    constructor() {
      this.entity = null;
      /** stem/name → repo-relative file_path, the wikilink resolver. */
      this.wikilinks = null;
      /** Lightbox navigation state (image order = document order). */
      this.images = [];
      this.currentImageIndex = 0;
      const raw = window.location.pathname.split("/").slice(2).join("/");
      let decoded = raw;
      try {
        decoded = decodeURIComponent(raw);
      } catch {
      }
      this.entityPath = decoded;
      this.bindEvents();
      setLightboxOpener((src) => this.openLightbox(src));
      void this.loadEntity();
    }
    bindEvents() {
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
      getEl("close-lightbox").addEventListener("click", () => closeLightbox());
      getEl("image-lightbox").addEventListener("click", (e) => {
        if (e.target.id === "image-lightbox") closeLightbox();
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
    async loadEntity() {
      try {
        const entity = await fetchJson(
          `/api/entity/${encodeURIComponent(this.entityPath)}`
        );
        this.entity = entity;
        this.displayEntity();
      } catch (error) {
        console.error("Error loading entity:", error);
        this.showError(error instanceof Error ? error.message : "unknown error");
      }
    }
    displayEntity() {
      const entity = this.entity;
      if (!entity) return;
      const fm = entity.frontmatter;
      const isEdition = entity.entity_type === "edition" || this.fmString(fm, "type") === "newsletter";
      document.title = `${entity.name} \u2014 FinData Knowledge Graph`;
      getEl("page-title").textContent = entity.name;
      getEl("breadcrumb-current").textContent = `${entity.entity_type.replace(/_/g, " ")}: ${entity.name}`;
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
        contentEl.innerHTML = '<div class="no-content"><i class="fas fa-file-alt"></i><p>No content available for this entity.</p></div>';
      }
      getEl("loading-state").style.display = "none";
      getEl("main-content").style.display = "grid";
      void this.ensureWikilinkIndex().then((index) => {
        if (index) this.linkifyWikilinks(contentEl);
      });
      void this.loadEvents(entity.name);
      void this.loadSemanticPeers(entity.name);
      if (entity.file_path) void this.loadSimilarNotes(entity.file_path);
    }
    /** Title block: chips (companies/sectors) or masthead (editions). */
    renderHeader(entity, isEdition) {
      const fm = entity.frontmatter;
      const mount = getEl("entity-metadata");
      if (isEdition) {
        const series = SERIES_LABELS[(entity.file_path || "").split("/")[1]];
        const title = this.fmString(fm, "title") ?? entity.name.replace(/_/g, " ");
        const bits = [];
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
                    ${bits.length ? `<div class="masthead-meta">${bits.join(' <span class="dot">\xB7</span> ')}</div>` : ""}
                </header>
            `;
        return;
      }
      const chips = [
        `<span class="fm-chip fm-type">${escapeHtml(entity.entity_type.replace(/_/g, " "))}</span>`
      ];
      for (const key of CHIP_KEYS) {
        const value = this.fmScalar(fm, key);
        if (value) {
          chips.push(
            `<span class="fm-chip"><b>${escapeHtml(key.replace(/_/g, " "))}</b>${escapeHtml(value)}</span>`
          );
        }
      }
      const tags = entity.enhanced_tags.length ? `<div class="entity-tags">${entity.enhanced_tags.map((t) => `<span class="entity-tag">${escapeHtml(t)}</span>`).join("")}</div>` : "";
      mount.innerHTML = `
            <header class="entity-head">
                <h1>${escapeHtml(this.fmString(fm, "title") ?? entity.name.replace(/_/g, " "))}</h1>
                <div class="fm-chips">${chips.join("")}</div>
                ${tags}
            </header>
        `;
    }
    /** The mono facts block at the top of the rail. */
    renderFacts(entity) {
      const fm = entity.frontmatter;
      const facts = [];
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
      getEl("facts-grid").innerHTML = facts.map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join("");
    }
    /** TOC into the rail; hidden when the note has fewer than two headings. */
    renderToc(headings) {
      if (headings.length < 2) return;
      getEl("toc-content").innerHTML = headings.map((h) => `<li class="toc-${h.level}"><a href="#${encodeURIComponent(h.id)}">${escapeHtml(h.text)}</a></li>`).join("");
      getEl("toc-block").style.display = "block";
    }
    // --- rail intel ---------------------------------------------------------- //
    /** Vertical events timeline (dated oldest→newest, undated last). */
    async loadEvents(name) {
      try {
        const data = await fetchJson(
          `/api/events/${encodeURIComponent(name)}`
        );
        if (!data.events.length) return;
        getEl("events-tl").innerHTML = data.events.map((ev) => {
          const date = this.eventDateLabel(ev.event_date, ev.date_precision);
          const body = [
            ev.counterparty ? escapeHtml(ev.counterparty) : "",
            ev.magnitude ? `<span class="ev-mag">${escapeHtml(ev.magnitude)}</span>` : ""
          ].filter(Boolean).join(" \xB7 ");
          const quote = ev.source_quote ? ` title="${escapeHtml(ev.source_quote).replace(/"/g, "&quot;")}"` : "";
          return `
                    <li class="ev-item"${quote}>
                        <span class="ev-date">${escapeHtml(date)}</span>
                        <span class="ev-type">${escapeHtml(ev.event_type)}</span>
                        <span class="ev-body">${body}</span>
                    </li>
                `;
        }).join("");
        getEl("rail-events").style.display = "block";
      } catch {
      }
    }
    /** Semantic peers as chips (company embeddings only — quiet otherwise). */
    async loadSemanticPeers(name) {
      try {
        const data = await fetchJson(
          `/api/graph/semantic/${encodeURIComponent(name)}?k=8`
        );
        if (!data.neighbors.length) return;
        getEl("peers-chips").innerHTML = data.neighbors.map((n) => {
          const pct = Math.round(n.similarity * 100);
          const href = this.wikilinks?.get(n.name);
          const inner = `${escapeHtml(n.name.replace(/_/g, " "))} <b>${pct}%</b>`;
          return href ? `<a class="peer-chip" href="/entity/${encodeURIComponent(href)}" title="${escapeHtml(n.sector || "")}">${inner}</a>` : `<span class="peer-chip" title="${escapeHtml(n.sector || "")}">${inner}</span>`;
        }).join("");
        getEl("rail-peers").style.display = "block";
      } catch {
      }
    }
    /** Embedding-similar notes as clickable rows. */
    async loadSimilarNotes(filePath) {
      try {
        const data = await fetchJson(
          `/api/graph/similar/${encodeURIComponent(filePath)}?k=6`
        );
        if (!data.neighbors.length) return;
        getEl("similar-list").innerHTML = data.neighbors.map((n) => {
          const pct = Math.round(n.similarity * 100);
          return `
                    <a class="related-row" href="/entity/${encodeURIComponent(n.file_path)}"
                       title="${escapeHtml(n.file_path)}">
                        <span class="related-title">${escapeHtml(n.title.replace(/_/g, " "))}</span>
                        <span class="related-sim"><span class="related-bar"><span
                            class="bar-fill" style="width:${pct}%"></span></span>${pct}%</span>
                    </a>
                `;
        }).join("");
        getEl("rail-similar").style.display = "block";
      } catch {
      }
    }
    // --- wikilinks ------------------------------------------------------------- //
    async ensureWikilinkIndex() {
      if (this.wikilinks) return this.wikilinks;
      try {
        const data = await fetchJson("/api/entities?limit=5000");
        const index = /* @__PURE__ */ new Map();
        for (const entity of data.entities) {
          if (!entity.file_path) continue;
          const stem = (entity.file_path.split("/").pop() || "").replace(/\.md$/i, "");
          if (stem && !index.has(stem)) index.set(stem, entity.file_path);
          if (entity.name && !index.has(entity.name)) index.set(entity.name, entity.file_path);
        }
        this.wikilinks = index;
        return index;
      } catch {
        return null;
      }
    }
    /** Same DOM-level rewrite as the Reading Room (code/pre/a untouched). */
    linkifyWikilinks(root) {
      const index = this.wikilinks;
      if (!index) return;
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
        acceptNode(node) {
          let parent = node.parentElement;
          while (parent && parent !== root) {
            const tag = parent.nodeName;
            if (tag === "CODE" || tag === "PRE" || tag === "A" || tag === "SCRIPT") {
              return NodeFilter.FILTER_REJECT;
            }
            parent = parent.parentElement;
          }
          return node.nodeValue && node.nodeValue.includes("[[") ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
        }
      });
      const targets = [];
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        targets.push(n);
      }
      for (const textNode of targets) {
        const text = textNode.nodeValue || "";
        WIKILINK_RE.lastIndex = 0;
        if (!WIKILINK_RE.test(text)) continue;
        const fragment = document.createDocumentFragment();
        let cursor = 0;
        let match;
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
    collectImages() {
      this.images = Array.from(document.querySelectorAll("#entity-content .rich-image")).map((img) => ({ src: img.src, alt: img.alt || "Image" }));
    }
    /** Entry point for the lightbox (delegated via wireRichInteractions). */
    openLightbox(src) {
      const lightbox = getEl("image-lightbox");
      const image = getEl("lightbox-image");
      this.currentImageIndex = Math.max(0, this.images.findIndex((i) => i.src === src));
      image.src = src;
      document.querySelector(".lightbox-caption").textContent = this.images[this.currentImageIndex]?.alt || "Image";
      lightbox.style.display = "flex";
      getEl("lightbox-prev").style.display = this.images.length > 1 ? "block" : "none";
      getEl("lightbox-next").style.display = this.images.length > 1 ? "block" : "none";
      document.body.style.overflow = "hidden";
    }
    navigateLightbox(direction) {
      if (!this.images.length) return;
      this.currentImageIndex = (this.currentImageIndex + direction + this.images.length) % this.images.length;
      const current = this.images[this.currentImageIndex];
      getEl("lightbox-image").src = current.src;
      document.querySelector(".lightbox-caption").textContent = current.alt;
    }
    toggleFullscreen() {
      if (!document.fullscreenElement) {
        void document.documentElement.requestFullscreen();
      } else {
        void document.exitFullscreen();
      }
    }
    updateFullscreenButton() {
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
    exportContent() {
      const entity = this.entity;
      if (!entity) return;
      const facts = getEl("entity-metadata").textContent || "";
      const markdown = `# ${entity.name}

${facts.trim()}

---

${entity.content || ""}`;
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
    showError(message) {
      getEl("loading-state").style.display = "none";
      getEl("main-content").style.display = "none";
      const errorState = getEl("error-state");
      errorState.style.display = "flex";
      const paragraph = errorState.querySelector("p");
      if (paragraph) paragraph.textContent = message;
    }
    // --- frontmatter helpers --------------------------------------------------------- //
    fmString(fm, key) {
      const v = fm[key];
      return typeof v === "string" && v.trim() ? v : null;
    }
    fmScalar(fm, key) {
      const v = this.fmString(fm, key);
      if (v === null) return null;
      return /^\d{4}-\d{2}-\d{2}T/.test(v) ? v.slice(0, 10) : v;
    }
    fmGeneratedAt(fm) {
      const g = fm.generated;
      if (g && typeof g === "object" && "at" in g) {
        const at = g.at;
        if (typeof at === "string" && at) return at.slice(0, 10);
      }
      return null;
    }
    fmPublisher(fm) {
      const tags = Array.isArray(fm.tags) ? fm.tags.filter((t) => typeof t === "string") : [];
      for (const tag of tags) {
        if (tag.startsWith("publisher/")) {
          return tag.slice("publisher/".length).replace(/\b\w/g, (c) => c.toUpperCase());
        }
      }
      return null;
    }
    eventDateLabel(date, precision) {
      if (!date) return "\u2014";
      if (precision === "year") return date.slice(0, 4);
      if (precision === "month") return date.slice(0, 7);
      return date;
    }
  };
  new EntityPage();
})();
//# sourceMappingURL=entity.bundle.js.map
