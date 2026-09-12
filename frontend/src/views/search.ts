// Unified search view (unified_search S2): one input, three corpora.
//
// Fan-out to /api/docs/search + /api/scripts/search + /api/search in
// parallel; each corpus keeps its own scorer and filters, rendered as
// grouped per-corpus sections — no cross-index score normalization (the
// server-side RRF merge is a recorded non-goal: BM25 scores across three
// differently-built indexes are not comparable). Every leg is settled
// independently, so one failing corpus renders an inline error line and
// never blanks the page. Script snippets render as fenced code through
// the sugar-high pipeline (language inferred from the path; mojo has no
// grammar so it borrows python and says so — "mojo≈python").

import type { DocSearchResponse, ScriptSearchResponse, SearchResponse } from "../../types/api";
import { ApiError, fetchJson } from "../core/api";
import { getEl, escapeHtml } from "../core/dom";
import { highlightCode, highlightSnippet } from "../core/markdown";

/** Scripts kind filter state (null = all kinds). */
type ScriptKind = "script" | "test" | "make" | "mojo" | "ts";

const SCRIPT_KINDS: ScriptKind[] = ["script", "test", "make", "mojo", "ts"];

/** Per-corpus result cap — grouped overview, not a full results page. */
const PER_CORPUS_LIMIT = 8;

/** Debounce window for the shared input (mirrors the Reading Room). */
const DEBOUNCE_MS = 300;

/** doc_type (note_search) → short chip label. */
const DOCTYPE_LABELS: Record<string, string> = {
    company: "company",
    sector: "sector",
    super_sector: "super sector",
    chatter: "chatter",
    points_and_figures: "P&F",
    plotlines: "plotlines",
};

/** Fence language for a script-search hit path. */
function fenceLangForPath(path: string): { lang: string; label: string | null } {
    if (path.endsWith(".mojo")) return { lang: "python", label: "mojo≈python" };
    if (path.endsWith(".py")) return { lang: "python", label: null };
    if (path.endsWith(".ts")) return { lang: "typescript", label: null };
    if (path.endsWith(".js")) return { lang: "javascript", label: null };
    if (path.endsWith(".sql")) return { lang: "sql", label: null };
    if (path.endsWith(".md")) return { lang: "markdown", label: null };
    if (path.endsWith(".yaml") || path.endsWith(".yml")) return { lang: "yaml", label: null };
    if (path.endsWith(".sh")) return { lang: "shell", label: null };
    // make targets ("qa", "integration") and unknown extensions.
    return { lang: "plaintext", label: null };
}

/** Strip FTS <mark> wrappers before token-coloring a snippet. */
function stripMarks(snippet: string): string {
    return snippet.replace(/<\/?mark>/g, "");
}

export class SearchView {
    /** Scripts kind filter (null = all). */
    private kindFilter: ScriptKind | null = null;

    /** Debounce timer handle for the shared input. */
    private searchTimeout: ReturnType<typeof setTimeout> | undefined;

    /** Whether this view is the visible one (stale-render check). */
    private readonly isActive: () => boolean;

    constructor(isActive: () => boolean) {
        this.isActive = isActive;
    }

    /** Wire the static controls (once at boot). */
    bindEvents(): void {
        const input = getEl("unified-search-input") as HTMLInputElement;
        const clear = getEl("unified-search-clear");
        input.addEventListener("input", (e) => {
            const target = e.target as HTMLInputElement;
            clear.style.display = target.value ? "block" : "none";
            this.debounceSearch();
        });
        // Enter commits immediately (the debounce serves typing, not intent).
        input.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                clearTimeout(this.searchTimeout);
                void this.runSearch(input.value.trim());
            }
        });
        clear.addEventListener("click", () => {
            input.value = "";
            clear.style.display = "none";
            this.renderIdle();
        });

        // Kind chips (scripts section filter) via event delegation.
        getEl("unified-kind-chips").addEventListener("click", (e) => {
            const chip = (e.target as HTMLElement).closest<HTMLElement>("[data-kind]");
            if (!chip) return;
            const kind = chip.dataset.kind as ScriptKind | "all";
            this.kindFilter = kind === "all" ? null : kind;
            this.renderKindChips();
            const q = input.value.trim();
            if (q) void this.runSearch(q);
        });
        this.renderKindChips();
    }

    /** Router loader: focus + idle hint on first activation (no auto-query). */
    activate(): void {
        const results = getEl("unified-results");
        if (!results.childElementCount) this.renderIdle();
        const input = document.getElementById("unified-search-input") as HTMLInputElement | null;
        if (input && this.isActive()) input.focus();
    }

    private debounceSearch(): void {
        clearTimeout(this.searchTimeout);
        this.searchTimeout = setTimeout(() => {
            const input = getEl("unified-search-input") as HTMLInputElement;
            void this.runSearch(input.value.trim());
        }, DEBOUNCE_MS);
    }

    /** Fan out to the three corpora; render grouped sections. */
    private async runSearch(q: string): Promise<void> {
        const results = getEl("unified-results");
        if (!q) {
            this.renderIdle();
            this.setCount("");
            return;
        }
        results.innerHTML = `<div class="docs-list"><em>searching…</em></div>`;
        if (!this.isActive()) return; // stale render guard

        const enc = encodeURIComponent(q);
        const kindQ = this.kindFilter ? `&kind=${this.kindFilter}` : "";
        const [docs, scripts, notes] = await Promise.allSettled([
            fetchJson<DocSearchResponse>(`/api/docs/search?q=${enc}&limit=${PER_CORPUS_LIMIT}`),
            fetchJson<ScriptSearchResponse>(
                `/api/scripts/search?q=${enc}&limit=${PER_CORPUS_LIMIT}${kindQ}`,
            ),
            fetchJson<SearchResponse>(`/api/search?q=${enc}&limit=${PER_CORPUS_LIMIT}`),
        ]);

        if (!this.isActive()) return; // user switched away mid-flight

        const total =
            (docs.status === "fulfilled" ? docs.value.results.length : 0) +
            (scripts.status === "fulfilled" ? scripts.value.results.length : 0) +
            (notes.status === "fulfilled" ? notes.value.results.length : 0);
        this.setCount(q ? `${total} hits across 3 corpora` : "");

        results.innerHTML =
            this.renderDocs(docs) + this.renderScripts(scripts) + this.renderNotes(notes);
    }

    // --- per-corpus renderers --------------------------------------------- //

    private renderDocs(leg: PromiseSettledResult<DocSearchResponse>): string {
        if (leg.status === "rejected") {
            return this.sectionError("Docs", leg.reason);
        }
        const data = leg.value;
        const stale = data.stale
            ? `<div class="count-label">index stale — rebuild via ` +
              `helpers/maintenance/rebuild_doc_search.py</div>`
            : "";
        if (!data.results.length) return this.sectionEmpty("Docs") + stale;
        const rows = data.results
            .map((hit) => {
                const where = hit.section_title
                    ? `${escapeHtml(hit.title)} · ${escapeHtml(hit.section_title)}`
                    : escapeHtml(hit.title);
                return `<li>
                    <div class="count-label">${escapeHtml(hit.path)}</div>
                    <div>${where}</div>
                    <div class="docs-row-snippet">${highlightSnippet(hit.snippet)}</div>
                </li>`;
            })
            .join("");
        return this.section("Docs", `mode: ${data.mode}`, stale, rows);
    }

    private renderScripts(leg: PromiseSettledResult<ScriptSearchResponse>): string {
        if (leg.status === "rejected") {
            const err = leg.reason instanceof ApiError ? leg.reason : null;
            const hint =
                err && err.status === 503
                    ? ` — rebuild via .venv/bin/python3 ` +
                      `helpers/maintenance/rebuild_script_search.py`
                    : "";
            return this.sectionError("Scripts", leg.reason, hint);
        }
        const data = leg.value;
        const stale = data.stale
            ? `<div class="count-label">index stale — rebuild via ` +
              `helpers/maintenance/rebuild_script_search.py</div>`
            : "";
        if (!data.results.length) return this.sectionEmpty("Scripts") + stale;
        const rows = data.results
            .map((hit) => {
                const { lang, label } = fenceLangForPath(hit.path);
                const approx = label ? ` <span class="fm-chip">${escapeHtml(label)}</span>` : "";
                const code = highlightCode(stripMarks(hit.snippet), lang);
                return `<li>
                    <div>
                        <span class="fm-chip fm-type">${escapeHtml(hit.kind)}</span>
                        ${escapeHtml(hit.path)}${approx}
                    </div>
                    ${hit.purpose ? `<div>${escapeHtml(hit.purpose)}</div>` : ""}
                    <pre class="code-block"><code>${code}</code></pre>
                </li>`;
            })
            .join("");
        return this.section("Scripts", `mode: ${data.mode}`, stale, rows);
    }

    private renderNotes(leg: PromiseSettledResult<SearchResponse>): string {
        if (leg.status === "rejected") {
            const err = leg.reason instanceof ApiError ? leg.reason : null;
            const hint =
                err && err.status === 503
                    ? ` — rebuild via .venv/bin/python3 ` +
                      `helpers/maintenance/rebuild_note_search.py`
                    : "";
            return this.sectionError("Notes", leg.reason, hint);
        }
        const data = leg.value;
        if (!data.results.length) return this.sectionEmpty("Notes");
        const more = data.total_count > data.results.length ? ` · ${data.total_count} total` : "";
        const rows = data.results
            .map((hit) => {
                const chip = hit.doc_type
                    ? `<span class="fm-chip fm-type">` +
                      `${escapeHtml(DOCTYPE_LABELS[hit.doc_type] ?? hit.doc_type)}</span> `
                    : "";
                const where = hit.section_title ? ` · ${escapeHtml(hit.section_title)}` : "";
                return `<li>
                    <div>${chip}${escapeHtml(hit.title ?? hit.file_path)}${where}</div>
                    <div class="count-label">${escapeHtml(hit.file_path)}</div>
                    <div class="docs-row-snippet">${highlightSnippet(hit.snippet)}</div>
                </li>`;
            })
            .join("");
        return this.section(`Notes${more}`, "", "", rows);
    }

    // --- shared section chrome -------------------------------------------- //

    private section(title: string, meta: string, pre: string, rows: string): string {
        const metaLine = meta ? ` <span class="count-label">${escapeHtml(meta)}</span>` : "";
        return `<div class="docs-list">
            <h3>${escapeHtml(title)}${metaLine}</h3>${pre}
            <ul class="unified-group">${rows}</ul>
        </div>`;
    }

    private sectionEmpty(corpora: string): string {
        return (
            `<div class="docs-list"><h3>${escapeHtml(corpora)}</h3>` +
            `<div class="count-label">no hits in ${escapeHtml(corpora.toLowerCase())}</div></div>`
        );
    }

    private sectionError(corpora: string, reason: unknown, hint = ""): string {
        const msg = reason instanceof Error ? reason.message : String(reason);
        return (
            `<div class="docs-list"><h3>${escapeHtml(corpora)}</h3>` +
            `<div class="count-label">leg failed: ${escapeHtml(msg)}${escapeHtml(hint)}</div></div>`
        );
    }

    private renderKindChips(): void {
        const chips = [
            `<button type="button" class="fm-chip${this.kindFilter === null ? " fm-type" : ""}" data-kind="all">all</button>`,
            ...SCRIPT_KINDS.map(
                (k) =>
                    `<button type="button" class="fm-chip${this.kindFilter === k ? " fm-type" : ""}" data-kind="${k}">${k}</button>`,
            ),
        ];
        getEl("unified-kind-chips").innerHTML = chips.join("");
    }

    private renderIdle(): void {
        getEl("unified-results").innerHTML =
            `<div class="docs-list"><em>one query, three indexes — doc/ sections, ` +
            `scripts/tests/make targets, vault notes. Start typing.</em></div>`;
    }

    private setCount(text: string): void {
        getEl("unified-count").textContent = text;
    }
}
