#!/usr/bin/env node
/**
 * TS doc-surface extractor for the script_search index (corpus_uniformity
 * S6): emits one JSON document per module — the leading block/line comment
 * as module_doc plus one entry per exported symbol (name, signature, doc).
 *
 * Invoked by helpers/maintenance/rebuild_script_search.py:
 *   node frontend/scripts/extract_ts_docs.mjs <file.ts>
 *
 * NOTE (2026-08-31): the planned typescript-compiler-API extraction is
 * IMPOSSIBLE with the installed toolchain — typescript@7 is the native
 * (Go) compiler and its npm package ships only version metadata, no JS
 * API (no createSourceFile/SyntaxKind). Rather than pin typescript@5
 * just to parse, this is a pragmatic structural scanner: depth-0
 * `export` statements + their immediately preceding JSDoc. It can miss
 * unconventional spellings (computed names, brace-in-string edge cases)
 * — acceptable for a BM25 intent index, NOT a correctness gate. The
 * Python side treats ANY nonzero exit, timeout, or empty stdout as
 * "extraction failed" (silent skip → degraded purpose), never an error.
 */
import { readFileSync } from "node:fs";
import { argv } from "node:process";

const file = argv[2];
if (!file) {
  console.error("usage: extract_ts_docs.mjs <file.ts>");
  process.exit(2);
}

let src;
try {
  src = readFileSync(file, "utf8");
} catch (err) {
  console.error(`cannot read ${file}: ${err.message}`);
  process.exit(1);
}

function cleanBlockComment(block) {
  return block
    .split("\n")
    .map((line) => line.replace(/^\s*\/?\*\*?\/?/, "").replace(/\*\/\s*$/, "")
      .replace(/^\s*\*\s?/, "").trimEnd())
    .join("\n")
    .trim();
}

function moduleDoc() {
  const head = src.slice(0, src.search(/\/\/|\/\*|^\S/m) >= 0 ? src.length : src.length);
  const trimmed = src.trimStart();
  if (trimmed.startsWith("/*")) {
    const end = trimmed.indexOf("*/");
    if (end !== -1) return cleanBlockComment(trimmed.slice(0, end + 2));
  }
  const lines = [];
  for (const line of trimmed.split("\n")) {
    const t = line.trimStart();
    if (t.startsWith("//")) lines.push(t.replace(/^\/\/\s?/, ""));
    else if (t === "" && lines.length === 0) continue;
    else break;
  }
  return lines.join("\n").trim();
}

const EXPORT_RE = /^export\s+(?:default\s+)?(?:async\s+)?(function\*?|class|const|let|var|interface|type|enum)\s+([A-Za-z_$][\w$]*)/;

function braceDelta(line) {
  let delta = 0;
  let i = 0;
  while (i < line.length) {
    const ch = line[i];
    if (ch === '"' || ch === "'" || ch === "`") {
      const quote = ch;
      i += 1;
      while (i < line.length && line[i] !== quote) {
        if (line[i] === "\\") i += 1;
        i += 1;
      }
    } else if (ch === "/" && line[i + 1] === "/") {
      break; // rest of the line is a comment
    } else if (ch === "{") {
      delta += 1;
    } else if (ch === "}") {
      delta -= 1;
    }
    i += 1;
  }
  return delta;
}

function extractExports() {
  const out = [];
  const lines = src.split("\n");
  let depth = 0;
  let pendingDoc = "";
  let i = 0;
  while (i < lines.length) {
    const trimmed = lines[i].trim();
    if (trimmed.startsWith("/**")) {
      let block = [lines[i]];
      if (!trimmed.includes("*/", 3)) {
        while (++i < lines.length && !lines[i].includes("*/")) block.push(lines[i]);
        if (i < lines.length) block.push(lines[i]);
      }
      pendingDoc = cleanBlockComment(block.join("\n"));
      i += 1;
      continue;
    }
    if (trimmed.startsWith("//") || trimmed === "") {
      i += 1;
      continue;
    }
    if (depth === 0) {
      const m = EXPORT_RE.exec(trimmed);
      if (m) {
        // Signature: from `export` to the end of the declaration header —
        // the line where the body `{` opens or the `;` lands (params may
        // wrap over several lines).
        const header = [trimmed.replace(/^export\s+/, "")];
        let j = i;
        let done = /[{;]/.test(trimmed);
        while (!done && ++j < lines.length) {
          const l = lines[j].trim();
          header.push(l);
          done = /[{;]/.test(l);
          if (j > i + 12) break; // runaway guard (~12 continuation lines max)
        }
        const signature = header
          .join(" ")
          .replace(/\s+/g, " ")
          .replace(/[{;]\s*$/, "")
          .trim();
        out.push({ name: m[2], signature: signature.slice(0, 200), doc: pendingDoc });
        pendingDoc = "";
        if (j > i && done) i = j; // resume after the header (body braces tracked below)
      } else if (trimmed.startsWith("export {")) {
        const names = [];
        let j = i;
        for (; j < lines.length && !lines[j].includes("}"); j += 1) {
          for (const part of lines[j].replace(/^[^{]*\{/, "").split(",")) {
            const name = part.replace(/^export \{/, "").replace(/[}].*$/, "")
              .split(/\s+as\s+/)[0].trim().replace(/^["']|["']$/g, "");
            if (/^[A-Za-z_$][\w$]*$/.test(name)) names.push(name);
          }
        }
        for (const name of names) {
          out.push({ name, signature: `export { ${name} }`, doc: pendingDoc });
        }
        pendingDoc = "";
      } else if (trimmed.startsWith("export default ")) {
        out.push({
          name: "default",
          signature: trimmed.replace(/\s+/g, " ").slice(0, 200),
          doc: pendingDoc,
        });
        pendingDoc = "";
      }
    }
    depth += braceDelta(lines[i]);
    i += 1;
  }
  return out;
}

process.stdout.write(
  JSON.stringify({ module_doc: moduleDoc(), exports: extractExports() }),
);
