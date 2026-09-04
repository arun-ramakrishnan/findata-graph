// Sectors view: classification tag cloud + sector analysis cards, plus the
// #sector-filter dropdown population (a companies-view input, but the data
// arrives from /api/sectors — same place it always did).

import type { SectorsResponse } from "../../types/api";
import { getEl, escapeHtml, truncateText } from "../core/dom";
import { fetchJson } from "../core/api";
import { loadActive } from "../core/loadActive";

export class SectorsView {
    /** Whether this view is the visible one (deferred-render check). */
    private readonly isActive: () => boolean;

    /** Handoff when a classification tag is clicked: filter + jump to companies. */
    private readonly onSectorPicked: (sector: string) => void;

    constructor(isActive: () => boolean, onSectorPicked: (sector: string) => void) {
        this.isActive = isActive;
        this.onSectorPicked = onSectorPicked;
    }

    async load(): Promise<void> {
        await loadActive({
            fetch: () => fetchJson<SectorsResponse>("/api/sectors"),
            // Unguarded: the sector-filter dropdown lives in the companies
            // view but is populated from here — must run even when this
            // view is not visible.
            onFetched: (data) => {
                const sectorFilter = getEl("sector-filter") as HTMLSelectElement;
                data.classifications.forEach((sector) => {
                    const option = document.createElement("option");
                    option.value = sector;
                    option.textContent = sector;
                    sectorFilter.appendChild(option);
                });
            },
            display: (data) => this.displaySectors(data),
            isActive: this.isActive,
            onError: (error) => console.error("Error loading sectors:", error),
        });
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
                this.onSectorPicked(sector);
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
                <h3 class="card-title">${escapeHtml(sector.name)}</h3>
                <div class="card-type">
                    <i class="fas fa-industry"></i>
                    <span>Sector</span>
                </div>
            </div>
            <div class="card-body">
                <div class="card-content">
                    ${truncateText(sector.content, 150)}
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
}
