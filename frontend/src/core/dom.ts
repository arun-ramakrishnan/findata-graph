// DOM helpers shared by every view.
//
// Extracted verbatim from the original single-file findata.ts during the S2
// split (behavior-preserving): same assumptions (elements queried here are
// guaranteed to exist in templates/findata.html because the bundle script
// runs at end of <body> with no defer/module).

/**
 * `document.getElementById` narrowed to non-null. The original vanilla-JS file
 * assumed every queried element exists; this preserves that assumption.
 *
 * Use `getEl<T>()` with a specific element subtype when the caller needs typed
 * access (e.g. `getEl<HTMLInputElement>("search-input")` for `.value`).
 */
export function getEl<T extends HTMLElement = HTMLElement>(id: string): T {
    const node = document.getElementById(id);
    if (!node) {
        throw new Error(`expected element #${id} not found in DOM`);
    }
    return node as T;
}

export function escapeHtml(text: string): string {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

/** Attribute-safe escaping for a double-quoted attribute value.
 *
 * `escapeHtml` goes through `textContent -> innerHTML`, which escapes
 * `& < >` but deliberately leaves `"` and `'` alone — correct for element
 * text, WRONG inside `attr="..."`, where an unescaped quote ends the
 * attribute early and the rest of the payload becomes live markup. Use
 * this wherever an interpolated value lands in a quoted attribute.
 *
 * Single source of truth: `core/markdown.ts` had a private copy that was
 * never exported, which is how the quote-safe call sites drifted apart.
 */
export function escapeAttr(text: string): string {
    return text
        .replace(/&/g, "&amp;")
        .replace(/"/g, "&quot;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
}

export function truncateText(text: string, maxLength: number): string {
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength) + "...";
}

/** Human-readable byte size (e.g. "9.3 KB"). */
export function formatBytes(bytes: number): string {
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
