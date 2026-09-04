// Statistics view: /api/stats cards + breakdowns, and the /api/graph/stats
// block (edge types, structure metrics, hygiene, staleness).

import type { GraphStatsResponse, StatsResponse } from "../../types/api";
import { getEl, escapeHtml } from "../core/dom";
import { fetchJson } from "../core/api";
import { loadActive } from "../core/loadActive";

export class StatsView {
    /** Whether this view is the visible one (deferred-render check). */
    private readonly isActive: () => boolean;

    constructor(isActive: () => boolean) {
        this.isActive = isActive;
    }

    async load(): Promise<void> {
        await loadActive({
            fetch: () => fetchJson<StatsResponse>("/api/stats"),
            display: (data) => this.displayStats(data),
            isActive: this.isActive,
            onError: (error) => console.error("Error loading stats:", error),
        });
        // Graph statistics block (edge types, structure, hygiene, staleness).
        // Fetched independently so a failure here doesn't hide /api/stats.
        await loadActive({
            fetch: () => fetchJson<GraphStatsResponse>("/api/graph/stats"),
            display: (data) => this.displayGraphStats(data),
            isActive: this.isActive,
            onError: (error) => {
                console.error("Error loading graph stats:", error);
                this.displayGraphStatsError();
            },
        });
    }

    displayStats(data: StatsResponse): void {
        const container = getEl("stats-container");
        container.innerHTML = "";

        // Total entities.
        const totalCard = this.createStatCard(
            "Total Entities",
            data.total_entities,
            "fas fa-database",
            "primary",
        );
        container.appendChild(totalCard);

        // Entity types.
        const typesCard = this.createStatCard(
            "Entity Types",
            Object.keys(data.entity_counts).length,
            "fas fa-tags",
            "secondary",
        );
        container.appendChild(typesCard);

        // Top sectors.
        const sectorsCard = this.createStatCard(
            "Sectors",
            Object.keys(data.top_sectors).length,
            "fas fa-industry",
            "success",
        );
        container.appendChild(sectorsCard);

        // Market cap distribution.
        const marketCapCard = this.createStatCard(
            "Market Cap Categories",
            Object.keys(data.market_cap_counts).length,
            "fas fa-chart-line",
            "warning",
        );
        container.appendChild(marketCapCard);

        // Detailed breakdowns.
        const breakdownSection = document.createElement("div");
        breakdownSection.className = "stats-breakdown";

        breakdownSection.appendChild(
            this.createBreakdownCard("Entity Types", data.entity_counts, "entity_type"),
        );
        breakdownSection.appendChild(
            this.createBreakdownCard("Top Sectors", data.top_sectors, "sector"),
        );
        breakdownSection.appendChild(
            this.createBreakdownCard(
                "Market Cap Distribution",
                data.market_cap_counts,
                "market_cap",
            ),
        );

        container.appendChild(breakdownSection);
    }

    /** Full graph-stats block for the Statistics view (from /api/graph/stats). */
    displayGraphStats(data: GraphStatsResponse): void {
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

        // Structure metrics (density, clustering, etc.) — nullable.
        if (data.structure) {
            const structure = data.structure;
            const items: [string, number | null][] = [
                ["Density", structure.density],
                ["Diameter", structure.diameter],
                ["Radius", structure.radius],
                ["Avg path length", structure.avg_path_length],
                ["Transitivity", structure.transitivity],
                ["Triangles", structure.triangles],
                ["Avg clustering", structure.avg_clustering],
                ["Assortativity", structure.assortativity],
            ];
            const metrics = document.createElement("div");
            metrics.className = "breakdown-card stats-graph-structure";
            metrics.innerHTML =
                `<h4>Structure</h4><div class="breakdown-items">` +
                items
                    .map(
                        ([label, v]) => `
                    <div class="breakdown-item">
                        <span class="breakdown-label">${escapeHtml(label)}</span>
                        <span class="breakdown-value">${v === null ? "—" : typeof v === "number" ? v.toFixed(4) : escapeHtml(String(v))}</span>
                    </div>`,
                    )
                    .join("") +
                `</div>`;
            section.appendChild(metrics);
        } else {
            const note = document.createElement("div");
            note.className = "hint";
            note.textContent =
                "Structure metrics unavailable (graph analysis layer not connected).";
            section.appendChild(note);
        }

        // Edge Types breakdown — type/count/percent, sorted by count desc.
        const byType = data.edges.by_type || {};
        const sorted: Record<string, number> = {};
        Object.keys(byType)
            .sort((a, b) => byType[b] - byType[a])
            .forEach((k) => {
                sorted[k] = byType[k];
            });
        section.appendChild(this.createBreakdownCard("Edge Types", sorted, "edge_type"));

        container.appendChild(section);
    }

    /** Inner stat cards for the graph block (edges + entities + sectors). */
    _graphStatsCards(data: GraphStatsResponse): string {
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
                <div class="stat-content"><h3>${data.sectors?.top?.[0]?.sector ?? "—"}</h3><p>Top Sector</p></div>
            </div>
            <div class="stat-card stat-secondary">
                <div class="stat-content"><h3 style="color:${staleColor}">${staleLabel}</h3><p>Data Staleness</p></div>
            </div>`;
    }

    /** Degraded fallback when /api/graph/stats is unreachable. */
    displayGraphStatsError(): void {
        const container = getEl("stats-container");
        if (!container) return;
        const note = document.createElement("div");
        note.className = "hint";
        note.textContent = "Graph statistics could not be loaded.";
        container.appendChild(note);
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
                    <span class="breakdown-label">${escapeHtml(this.formatLabel(key, type))}</span>
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
        if (type === "edge_type") {
            // snake_case edge types → readable "Co Mentioned In".
            return key.replace(/_/g, " ").replace(/\b\w/g, (l) => l.toUpperCase());
        }
        return key;
    }
}
