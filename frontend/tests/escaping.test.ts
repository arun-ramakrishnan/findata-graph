// Escaping guards for the template-literal rendering paths (range review
// H2 / H3 / M5, 2026-10-14).
//
// WHY THIS FILE EXISTS. Before it, `frontend/tests/` held only
// `guards.test.ts` (generated-type behaviour), so five views and two render
// pipelines had ZERO guard coverage over how values reach the DOM — which is
// how entity names reached `dl.innerHTML` inside a `<option value="...">`
// unescaped, and `file_path` reached an unencoded `href`.
//
// Two halves:
//   1. UNIT — the escapers themselves behave as the attribute contract claims.
//   2. SOURCE — a static sweep over src/**/*.ts for the two shapes that keep
//      recurring: `escapeHtml(` inside a double-quoted attribute (escapeHtml
//      goes textContent -> innerHTML and leaves `"` intact, so it is wrong in
//      an attribute), and a raw `${...file_path}` inside an href.
//
// The source half is the point: it fails on the NEXT bad interpolation, not
// just the 29 sites this review happened to find.
import { describe, expect, test } from "bun:test";

import { escapeAttr, escapeHtml } from "../src/core/dom";

/** Every .ts under src/, discovered rather than listed.
 *
 *  A hardcoded list is how this class of bug survives a review: a NEW view
 *  that renders into innerHTML is never swept, because nobody remembered to
 *  add it. Globbing means the guard covers files that did not exist when it
 *  was written.
 */
async function srcFiles(): Promise<string[]> {
    const glob = new Bun.Glob("src/**/*.ts");
    const out: string[] = [];
    for await (const rel of glob.scan({ cwd: new URL("..", import.meta.url).pathname })) {
        if (!rel.includes("/types/")) out.push(rel);
    }
    return out.sort();
}

async function readSrc(rel: string): Promise<string> {
    return await Bun.file(new URL("../" + rel, import.meta.url)).text();
}

describe("escapeAttr — the attribute contract", () => {
    test("escapes the double quote that terminates an attribute", () => {
        // The whole reason this helper exists. `escapeHtml` is implemented as
        // `div.textContent = s; return div.innerHTML`, and the innerHTML
        // serialiser escapes `& < >` in TEXT but deliberately does NOT escape
        // `"`. So `attr="${escapeHtml(x)}"` terminates the attribute early on
        // any x containing a quote. escapeAttr closes that.
        //
        // escapeHtml itself is NOT called here: it needs a DOM, and bun's
        // test runner has none. Its behaviour is pinned by the source sweep
        // below, which fails if anyone reintroduces it into an attribute.
        expect(escapeAttr('Acme "Big" Ltd')).toBe("Acme &quot;Big&quot; Ltd");
    });

    test("escapeHtml is still available for element text", async () => {
        // Guard the import so a dead import is loud rather than silent.
        const dom = await import("../src/core/dom");
        expect(typeof dom.escapeHtml).toBe("function");
    });

    test("escapes & and angle brackets too", () => {
        expect(escapeAttr('a&b<c>d"e')).toBe("a&amp;b&lt;c&gt;d&quot;e");
    });

    test("ampersand is escaped first so entities are not double-escaped", () => {
        expect(escapeAttr('&quot;')).toBe("&amp;quot;");
    });

    test("leaves ordinary text untouched", () => {
        expect(escapeAttr("HDFC Bank Ltd")).toBe("HDFC Bank Ltd");
    });
});

describe("source sweep — no escapeHtml inside a quoted attribute", () => {
    test("all src files", async () => {
        const offenders: string[] = [];
        for (const file of await srcFiles()) {
            const src = await readSrc(file);
            src.split("\n").forEach((line, i) => {
                let from = 0;
                for (;;) {
                    const at = line.indexOf("${escapeHtml(", from);
                    if (at < 0) break;
                    // Inside a double-quoted attribute iff an odd number of
                    // '"' precedes the interpolation on this line.
                    const quotesBefore = (line.slice(0, at).match(/"/g) || []).length;
                    if (quotesBefore % 2 === 1) {
                        offenders.push(`${file}:${i + 1}: ${line.trim()}`);
                    }
                    from = at + 1;
                }
            });
        }
        expect(offenders).toEqual([]);
    });
});

describe("source sweep — the sanitiser runs LAST (M6)", () => {
    test("processExternalContent is called before DOMPurify.sanitize", async () => {
        // M6: processExternalContent used to run after sanitize(), so the
        // <iframe> it synthesises bypassed DOMPurify entirely. This pins the
        // ORDERING, which no behavioural test can catch here because bun has
        // no DOM to run marked/DOMPurify in.
        const src = await readSrc("src/core/markdown.ts");
        const ext = src.indexOf("processExternalContent(marked.parse(");
        const san = src.indexOf("DOMPurify.sanitize(");
        expect(ext).toBeGreaterThan(-1);
        expect(san).toBeGreaterThan(-1);
        expect(ext).toBeLessThan(san);

        // and exactly one call site: a second call after sanitize would
        // silently re-open the hole.
        const calls = src.split("processExternalContent(processedHtml)").length - 1;
        expect(calls).toBe(0);
    });

    test("the YouTube embed regex is anchored to a standalone <p>", async () => {
        // A bare URL pattern also fired inside href="...", injecting markup
        // into an attribute value.
        const src = await readSrc("src/core/markdown.ts");
        // find the regex literal that matches a youtube watch URL
        const line = src
            .split("\n")
            .find(
                (l) =>
                    l.includes("youtube") &&
                    l.trim().startsWith("/") &&
                    !l.trim().startsWith("//"),
            );
        expect(line).toBeDefined();
        // it must open with the <p> anchor, not a bare `https?`
        expect(line!.trim().startsWith("/<p>")).toBe(true);
    });
});

describe("source sweep — entity file_path hrefs are percent-encoded", () => {
    test("all src files", async () => {
        const offenders: string[] = [];
        for (const file of await srcFiles()) {
            const src = await readSrc(file);
            src.split("\n").forEach((line, i) => {
                // href="/entity/${x}" where x is not encodeURI(...)'d.
                const m = line.match(/href="\/entity\/\$\{([^}]+)\}"/);
                if (m && !/encodeURI(Component)?\(/.test(m[1])) {
                    offenders.push(`${file}:${i + 1}: ${m[1]}`);
                }
            });
        }
        expect(offenders).toEqual([]);
    });
});
