// Markdown → rich HTML pipeline (Docs reader + any note-body rendering).
//
// Extracted from FinDataViewer during the S2 split. One behavioral upgrade,
// the XSS gap fix called out in the redesign proposal §2.2: marked output is
// now sanitized with DOMPurify (already vendored at
// static/vendor/purify.min.js, previously only loaded by entity_detail)
// BEFORE our own attribute augmentation runs — so untrusted markdown can no
// longer inject inline handlers or javascript: URLs, while the lightbox /
// copy-code handlers we add ourselves (after sanitization) still work.
//
// The hljs/Prism/marked globals are declared in types/vendors.d.ts.

import { getEl, escapeHtml } from "./dom";
import { showToast } from "./toast";

/** A generated heading captured during markdown processing (for the TOC). */
export interface TocHeading {
    level: number;
    text: string;
    id: string;
}

/** Result of processRichContent — HTML ready to inject + the TOC headings. */
export interface ProcessedContent {
    html: string;
    headings: TocHeading[];
}

export function processRichContent(content: string): ProcessedContent {
    // Markdown → HTML, sanitized before anything else touches it.
    let processedHtml = DOMPurify.sanitize(marked.parse(content));

    // Extract headings for TOC.
    const headings: TocHeading[] = [];
    processedHtml = processedHtml.replace(/<h([1-6])[^>]*>(.*?)<\/h[1-6]>/gi, (match, level: string, text: string) => {
        const id = text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
        headings.push({
            level: parseInt(level, 10),
            text: text.replace(/<[^>]*>/g, ""),
            id: id,
        });
        return `<h${level} id="${id}">${text}</h${level}>`;
    });

    // Process images for lightbox.
    processedHtml = processedHtml.replace(/<img([^>]+)src="([^"]+)"([^>]*)>/gi, (_match, before: string, src: string, after: string) => {
        const imgId = `img-${Math.random().toString(36).substring(2, 11)}`;
        return `<img${before}src="${src}"${after} class="rich-image" data-img-id="${imgId}" data-lightbox="${escapeAttr(src)}" loading="lazy">`;
    });

    // Process code blocks with syntax highlighting.
    processedHtml = processedHtml.replace(/<pre><code class="language-(\w+)">([\s\S]*?)<\/code><\/pre>/gi, (_match, lang: string, code: string) => {
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

    // Process inline code.
    processedHtml = processedHtml.replace(/<code>([\s\S]*?)<\/code>/gi, '<code class="inline-code">$1</code>');

    // Process tables with responsive design.
    processedHtml = processedHtml.replace(/<table([^>]*)>([\s\S]*?)<\/table>/gi, (_match, attributes: string, tableContent: string) => {
        return `
            <div class="table-wrapper">
                <table${attributes}>${tableContent}</table>
            </div>
        `;
    });

    // Process blockquotes.
    processedHtml = processedHtml.replace(/<blockquote>([\s\S]*?)<\/blockquote>/gi, '<blockquote class="rich-blockquote">$1</blockquote>');

    // Add responsive embeds for external content.
    processedHtml = processExternalContent(processedHtml);

    return {
        html: processedHtml,
        headings: headings,
    };
}

/**
 * TOC renderer for generated-heading lists. NOTE: currently unreferenced by
 * any view (the docs reader uses renderDocsToc instead) — preserved from the
 * original file to keep the module surface identical during the split.
 */
export function generateTableOfContents(headings: TocHeading[]): string {
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
                    ${escapeHtml(heading.text)}
                </a>
            </li>
        `;

        currentLevel = heading.level;
    });

    // Close any remaining nested lists.
    toc += "</ul>".repeat(currentLevel);
    toc += "</ul>";

    return toc;
}

export function highlightCode(code: string, language: string): string {
    try {
        if (window.hljs) {
            return window.hljs.highlight(code, { language }).value;
        }
    } catch (e) {
        console.warn("Syntax highlighting failed:", e);
    }
    return escapeHtml(code);
}

/** Clipboard copy for a rendered code block (inline-onclick entry point). */
export function copyCode(codeId: string): void {
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

export function openLightbox(imageSrc: string): void {
    const lightbox = getEl("image-lightbox");
    const lightboxImage = getEl("lightbox-image") as HTMLImageElement;
    const caption = document.querySelector(".lightbox-caption") as HTMLElement;

    lightboxImage.src = imageSrc;
    caption.textContent = imageSrc.split("/").pop() || "Image";
    lightbox.style.display = "flex";

    // Prevent body scroll.
    document.body.style.overflow = "hidden";
}

export function closeLightbox(): void {
    const lightbox = getEl("image-lightbox");
    lightbox.style.display = "none";
    document.body.style.overflow = "";
}

function processExternalContent(html: string): string {
    // Process YouTube embeds.
    html = html.replace(/https?:\/\/(?:www\.)?youtube\.com\/watch\?v=([a-zA-Z0-9_-]+)/gi,
        '<div class="video-embed"><iframe src="https://www.youtube.com/embed/$1" frameborder="0" allowfullscreen></iframe></div>');

    // Process general links.
    html = html.replace(/<a href="([^"]+)"([^>]*)>/gi, (match, href: string, rest: string) => {
        const isExternal = href.startsWith("http") && !href.includes(window.location.hostname);
        const externalClass = isExternal ? "external-link" : "";
        const externalIcon = isExternal ? '<i class="fas fa-external-link-alt"></i>' : "";
        return `<a href="${href}"${rest} class="${externalClass}">${externalIcon}`;
    });

    return html;
}

/**
 * Post-render wiring for rich content (Prism highlighting, smooth-scroll TOC
 * links, broken-image placeholders). NOTE: currently unreferenced by any view
 * in the original file either — preserved verbatim through the split.
 */
export function initializeInteractiveElements(): void {
    // Initialize syntax highlighting if available.
    if (window.Prism) {
        window.Prism.highlightAll();
    }

    // Add smooth scrolling for TOC links (respect reduced-motion preference).
    const prefersReducedMotion = window.matchMedia
        && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document.querySelectorAll<HTMLAnchorElement>(".toc-link").forEach((link) => {
        link.addEventListener("click", (e) => {
            e.preventDefault();
            const targetId = (link.getAttribute("href") || "").substring(1);
            const targetElement = getEl(targetId);
            if (targetElement) {
                targetElement.scrollIntoView({
                    behavior: prefersReducedMotion ? "auto" : "smooth",
                    block: "start",
                });
            }
        });
    });

    // Add image loading error handling.
    document.querySelectorAll<HTMLImageElement>(".rich-image").forEach((img) => {
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

/** Attribute-safe escaping (escapeHtml leaves quotes untouched). */
function escapeAttr(text: string): string {
    return text.replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}

/**
 * Lightbox opener override. Pages with a richer lightbox (the entity pages'
 * prev/next navigation) replace the default core openLightbox via
 * setLightboxOpener; wireRichInteractions always routes through the
 * current opener.
 */
let lightboxOpener: (src: string) => void = openLightbox;

export function setLightboxOpener(opener: (src: string) => void): void {
    lightboxOpener = opener;
}

/**
 * Delegated interactions for rendered rich content: lightbox images
 * (data-lightbox) and code-copy buttons (data-copy). Replaces the old
 * inline `onclick="viewer.*"` handlers so any page — the SPA or the
 * standalone entity pages — can host rich content without a window.viewer.
 * Call once per container whose innerHTML was (re)built.
 */
export function wireRichInteractions(root: HTMLElement): void {
    root.addEventListener("click", (e) => {
        const target = e.target as HTMLElement;
        const img = target.closest<HTMLImageElement>("img.rich-image[data-lightbox]");
        if (img) {
            lightboxOpener(img.dataset.lightbox || img.src);
            return;
        }
        const copy = target.closest<HTMLElement>(".code-copy[data-copy]");
        if (copy && copy.dataset.copy) {
            copyCode(copy.dataset.copy);
        }
    });
}

/**
 * Restore FTS <mark> highlights into an escaped snippet. The FTS snippet
 * comes back with literal <mark>...</mark> tags wrapping matches;
 * escapeHtml() would escape those into visible text. Instead, escape
 * everything, then restore the markers by re-splitting on the (now-escaped)
 * marker text.
 */
export function highlightSnippet(snippet: string): string {
    if (!snippet) return "";
    // Temporarily mark match boundaries, escape, then convert markers
    // back to real <mark> tags.
    const OPEN = "\u0001"; // unlikely control chars as sentinels
    const CLOSE = "\u0002";
    const markedUp = String(snippet)
        .replace(/<mark>/g, OPEN)
        .replace(/<\/mark>/g, CLOSE);
    return escapeHtml(markedUp)
        .replace(/\u0001/g, "<mark>")
        .replace(/\u0002/g, "</mark>");
}
