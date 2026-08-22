// FinData Knowledge Graph Viewer — shell module.
//
// Built by `make frontend` (esbuild) into static/findata.bundle.js, which the
// Flask app serves via templates/findata.html. DO NOT edit the bundle; edit
// the files under src/ and rebuild.
//
// Since the S2 split this file is only the coordinator: it owns nothing but
// wiring. The views live in src/views/*, shared plumbing in src/core/*.
//
// Runtime contract preserved from the original vanilla-JS file:
//   - The script tag has no `defer`/`type=module`; it runs immediately at the
//     end of <body>, so the DOM is already parsed when the constructor runs.
//   - The bundle is an IIFE (not an ES module) and re-attaches the instance
//     to `window.viewer` so the inline onclick handlers in dynamically-built
//     HTML resolve at runtime. Since S6 the only inline-onclick consumer is:
//       viewer.goToPage(n)      — pagination buttons (views/companies.ts)
//     Markdown images and code-copy buttons now carry data-* attributes
//     wired by core/markdown.wireRichInteractions() at each render site,
//     which is what lets the standalone entity pages share the pipeline.

import { Router } from "./core/router";
import type { ViewName } from "./core/router";
import { copyCode, openLightbox } from "./core/markdown";
import { CompaniesView } from "./views/companies";
import { SectorsView } from "./views/sectors";
import { StatsView } from "./views/stats";
import { DocsView } from "./views/docs";
import { GraphView } from "./views/graph";

// `viewer` is referenced as a bare global by inline onclick handlers in the
// HTML strings the views build. Declare it on window so those references are
// navigable + typo-checked, and so the bottom assignment type-checks.
// `hljs`/`Prism` are vendored library globals (see types/vendors.d.ts for
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

class FinDataViewer {
    readonly router: Router;
    readonly companies: CompaniesView;
    readonly sectors: SectorsView;
    readonly stats: StatsView;
    readonly docs: DocsView;
    readonly graph: GraphView;

    constructor() {
        this.companies = new CompaniesView(() => this.router.isActive("companies"));
        this.sectors = new SectorsView(
            () => this.router.isActive("sectors"),
            // Classification-tag click: filter companies + jump over.
            (sector) => {
                this.companies.setSectorFilter(sector);
                this.router.switchView("companies");
            },
        );
        this.stats = new StatsView(() => this.router.isActive("stats"));
        this.docs = new DocsView(() => this.router.isActive("docs"));
        this.graph = new GraphView();

        this.router = new Router({
            companies: () => this.companies.loadEntities(),
            sectors: () => this.sectors.load(),
            stats: () => this.stats.load(),
            graph: () => this.graph.loadGraphView(),
            docs: () => this.docs.loadCatalog(),
        } satisfies Record<ViewName, () => unknown>);

        this.init();
    }

    init(): void {
        this.bindEvents();
        this.loadInitialData();
    }

    bindEvents(): void {
        // Navigation links.
        this.router.bindNav();

        // Per-view static controls.
        this.companies.bindEvents();
        this.docs.bindEvents();
    }

    async loadInitialData(): Promise<void> {
        await this.sectors.load();
        await this.stats.load();
        await this.companies.loadEntities();
    }

    // --- window.viewer inline-onclick surface ----------------------------- //
    // Keep these three exactly as named; generated HTML depends on them.

    /** Pagination buttons (views/companies.ts updatePagination). */
    goToPage(page: number): void {
        this.companies.goToPage(page);
    }

    /** Markdown image lightbox (core/markdown.ts processRichContent). */
    openLightbox(imageSrc: string): void {
        openLightbox(imageSrc);
    }

    /** Code-block copy buttons (core/markdown.ts processRichContent). */
    copyCode(codeId: string): void {
        copyCode(codeId);
    }
}

// Initialize the viewer + re-expose on window so the inline onclick handlers
// in the HTML strings above resolve at runtime (load-bearing: see file header).
const viewer = new FinDataViewer();
window.viewer = viewer;
