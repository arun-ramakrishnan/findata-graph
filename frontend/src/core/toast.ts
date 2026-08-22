// Toasts + the global loading indicator.
//
// Extracted verbatim from FinDataViewer.showLoading/showError/showToast
// during the S2 split (behavior-preserving): same DOM classes, same 3-second
// auto-dismiss, same escape-before-inject discipline.

import { getEl, escapeHtml } from "./dom";

export function showLoading(show: boolean): void {
    const loading = getEl("loading");
    loading.style.display = show ? "block" : "none";
}

/** Error toast (auto-dismisses after 3s). */
export function showError(message: string): void {
    const toast = document.createElement("div");
    toast.className = "toast error";
    toast.innerHTML = `
        <i class="fas fa-exclamation-circle"></i>
        <span>${escapeHtml(message)}</span>
    `;

    document.body.appendChild(toast);

    setTimeout(() => {
        toast.remove();
    }, 3000);
}

/**
 * Generic toast. In the original single file this was a private method kept
 * as a no-op-friendly fallback for copyCode's success/error feedback; it
 * reuses showError's toast plumbing (same runtime behavior).
 */
export function showToast(message: string, kind: string): void {
    const toast = document.createElement("div");
    toast.className = `toast ${kind}`;
    toast.innerHTML = `<span>${escapeHtml(message)}</span>`;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
}
