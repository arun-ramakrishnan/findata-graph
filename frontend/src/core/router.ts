// Hash-free view router: owns the current-view state and the show/hide +
// nav-highlight mechanics that previously lived in FinDataViewer.switchView.
//
// Behavior-preserving: switching a view hides every `.view-section`, shows
// `#<view>-view`, toggles `.active` on the matching `.nav-link`, then invokes
// the view's loader (which decides whether to render immediately or defer —
// same as the original switch statement did).

export type ViewName = "companies" | "sectors" | "stats" | "graph" | "docs";

/** Per-view data loader invoked on activation (may be async). */
export type ViewLoader = () => void | Promise<void>;

export class Router {
    /** The currently visible view. */
    currentView: ViewName = "companies";

    private readonly loaders: Record<ViewName, ViewLoader>;

    constructor(loaders: Record<ViewName, ViewLoader>) {
        this.loaders = loaders;
    }

    /** Wire the static .nav-link anchors in templates/findata.html. */
    bindNav(): void {
        document.querySelectorAll<HTMLAnchorElement>(".nav-link").forEach((link) => {
            link.addEventListener("click", (e) => {
                e.preventDefault();
                this.switchView(link.dataset.view as ViewName);
            });
        });
    }

    /** True when `view` is the visible view (async loads check before rendering). */
    isActive(view: ViewName): boolean {
        return this.currentView === view;
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
        const section = document.getElementById(`${view}-view`);
        if (!section) {
            throw new Error(`expected element #${view}-view not found in DOM`);
        }
        section.style.display = "block";

        this.currentView = view;

        // Load data for the view.
        void this.loaders[view]();
    }
}
