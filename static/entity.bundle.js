"use strict";
(() => {
  var __defProp = Object.defineProperty;
  var __export = (target, all) => {
    for (var name in all)
      __defProp(target, name, { get: all[name], enumerable: true });
  };

  // src/core/dom.ts
  function getEl(id) {
    const node = document.getElementById(id);
    if (!node) {
      throw new Error(`expected element #${id} not found in DOM`);
    }
    return node;
  }
  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  // src/core/api.ts
  var ApiError = class extends Error {
    constructor(status, message) {
      super(message);
      this.status = status;
    }
  };
  function extractErrorMessage(body, fallback) {
    if (body && typeof body === "object" && "error" in body) {
      const err = body.error;
      if (typeof err === "string" && err) return err;
    }
    return fallback;
  }
  async function fetchJson(url, init) {
    const response = await fetch(url);
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new ApiError(
        response.status,
        extractErrorMessage(body, response.statusText || `HTTP ${response.status}`)
      );
    }
    return await response.json();
  }

  // src/core/reader.ts
  var SERIES_LABELS = {
    The_Chatter: "The Chatter",
    Points_And_Figures: "Points & Figures",
    The_PlotLines: "The Plotlines"
  };
  var WIKILINK_RE = /\[\[([^\[\]|]+?)(?:#[^\[\]|]*)?(?:\|([^\[\]]+?))?\]\]/g;
  var CHIP_KEYS = ["ticker", "sector", "industry", "market_cap", "created", "last_modified"];
  function seriesLabel(filePath) {
    return filePath ? SERIES_LABELS[filePath.split("/")[1]] : void 0;
  }
  function readerTitle(entity) {
    return fmString(entity.frontmatter, "title") ?? entity.name.replace(/_/g, " ");
  }
  function editionBits(entity) {
    const fm = entity.frontmatter;
    const bits = [];
    const publisher = fmPublisher(fm);
    if (publisher) bits.push(escapeHtml(publisher));
    const generated = fmGeneratedAt(fm);
    if (generated) bits.push(`generated ${escapeHtml(generated)}`);
    const stale = fmScalar(fm, "stale_after");
    if (stale) bits.push(`fresh through ${escapeHtml(stale)}`);
    return bits;
  }
  function chipSpans(entity) {
    const fm = entity.frontmatter;
    const chips = [
      `<span class="fm-chip fm-type">${escapeHtml(entity.entity_type.replace(/_/g, " "))}</span>`
    ];
    for (const key of CHIP_KEYS) {
      const value = fmScalar(fm, key);
      if (value) {
        chips.push(
          `<span class="fm-chip"><b>${escapeHtml(key.replace(/_/g, " "))}</b>${escapeHtml(value)}</span>`
        );
      }
    }
    return chips.join("");
  }
  function buildWikilinkIndex(entities2) {
    const index = /* @__PURE__ */ new Map();
    for (const entity of entities2) {
      if (!entity.file_path) continue;
      const stem = (entity.file_path.split("/").pop() || "").replace(/\.md$/i, "");
      if (stem && !index.has(stem)) index.set(stem, entity.file_path);
      if (entity.name && !index.has(entity.name)) index.set(entity.name, entity.file_path);
    }
    return index;
  }
  function linkifyWikilinks(root, index, hrefFor) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        let parent = node.parentElement;
        while (parent && parent !== root) {
          const tag = parent.nodeName;
          if (tag === "CODE" || tag === "PRE" || tag === "A" || tag === "SCRIPT") {
            return NodeFilter.FILTER_REJECT;
          }
          parent = parent.parentElement;
        }
        return node.nodeValue && node.nodeValue.includes("[[") ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
      }
    });
    const targets = [];
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      targets.push(n);
    }
    for (const textNode of targets) {
      const text = textNode.nodeValue || "";
      WIKILINK_RE.lastIndex = 0;
      if (!WIKILINK_RE.test(text)) continue;
      const fragment = document.createDocumentFragment();
      let cursor = 0;
      let match;
      WIKILINK_RE.lastIndex = 0;
      while ((match = WIKILINK_RE.exec(text)) !== null) {
        if (match.index > cursor) {
          fragment.appendChild(document.createTextNode(text.slice(cursor, match.index)));
        }
        const target = (match[1] || "").trim();
        const label = (match[2] || "").trim() || target;
        const href = index.get(target);
        if (href) {
          const resolved = hrefFor(href);
          const anchor = document.createElement("a");
          anchor.className = "wikilink";
          anchor.href = resolved.href;
          if (resolved.datasetHref !== void 0) {
            anchor.dataset.href = resolved.datasetHref;
          }
          anchor.title = href;
          anchor.textContent = label;
          fragment.appendChild(anchor);
        } else {
          const miss = document.createElement("span");
          miss.className = "wikilink wikilink-miss";
          miss.title = "unresolved note";
          miss.textContent = label;
          fragment.appendChild(miss);
        }
        cursor = match.index + match[0].length;
      }
      if (cursor < text.length) {
        fragment.appendChild(document.createTextNode(text.slice(cursor)));
      }
      textNode.replaceWith(fragment);
    }
  }
  function fmString(fm, key) {
    const v = fm[key];
    return typeof v === "string" && v.trim() ? v : null;
  }
  function fmScalar(fm, key) {
    const v = fmString(fm, key);
    if (v === null) return null;
    return /^\d{4}-\d{2}-\d{2}T/.test(v) ? v.slice(0, 10) : v;
  }
  function fmGeneratedAt(fm) {
    const g = fm.generated;
    if (g && typeof g === "object" && "at" in g) {
      const at = g.at;
      if (typeof at === "string" && at) return at.slice(0, 10);
    }
    return null;
  }
  function fmPublisher(fm) {
    const tags = Array.isArray(fm.tags) ? fm.tags.filter((t) => typeof t === "string") : [];
    for (const tag of tags) {
      if (tag.startsWith("publisher/")) {
        return tag.slice("publisher/".length).replace(/\b\w/g, (c) => c.toUpperCase());
      }
    }
    return null;
  }

  // node_modules/sugar-high/lib/shared.js
  var TokenTypes = (
    /** @type {const} */
    "identifier keyword string class property entity jsxliterals sign comment break space".split(" ")
  );
  var [
    T_IDENTIFIER,
    T_KEYWORD,
    T_STRING,
    T_CLASS,
    T_PROPERTY,
    T_ENTITY,
    T_JSX_LITERALS,
    T_SIGN,
    T_COMMENT,
    T_BREAK,
    T_SPACE
  ] = TokenTypes.map((_, index) => index);
  var SugarHigh = (
    /** @type {const} */
    {
      TokenTypes,
      TokenMap: new Map(TokenTypes.map((type, index) => [type, index]))
    }
  );
  function assemble(value, tokens) {
    const lines = [];
    let lineIndex = 0;
    const lineTokens = [];
    let lastWasBreak = false;
    function flushLine(tokens2) {
      lines.push({
        index: lineIndex++,
        value: tokens2.map(([, tokenValue]) => tokenValue).join(""),
        tokens: tokens2.map(([type, tokenValue]) => ({
          type: TokenTypes[type],
          value: tokenValue
        })),
        annotations: []
      });
    }
    for (let index = 0; index < tokens.length; index++) {
      const token = tokens[index];
      const [type, value2] = token;
      if (type !== T_BREAK) {
        if (value2.includes("\n")) {
          const values = value2.split("\n");
          for (let part = 0; part < values.length; part++) {
            lineTokens.push([type, values[part]]);
            if (part < values.length - 1) {
              flushLine(lineTokens);
              lineTokens.length = 0;
            }
          }
        } else {
          lineTokens.push(token);
        }
        lastWasBreak = false;
      } else {
        if (lastWasBreak) flushLine([]);
        else {
          flushLine(lineTokens);
          lineTokens.length = 0;
        }
        if (index === tokens.length - 1) flushLine([]);
        lastWasBreak = true;
      }
    }
    if (lineTokens.length) flushLine(lineTokens);
    return { value, lines };
  }
  function createLine(parsedLine, markLine) {
    const line = {
      index: parsedLine.index,
      value: parsedLine.value,
      tokens: parsedLine.tokens,
      annotations: parsedLine.annotations,
      className: `sh__line${parsedLine.annotations.map((annotation) => ` sh__line--${annotation}`).join("")}`,
      style: {},
      properties: {}
    };
    markLine?.(line);
    return line;
  }
  function createToken({ type, value }, cx, mark) {
    const extraClassName = cx?.[type];
    const token = {
      type,
      value,
      className: `sh__token--${type}${extraClassName ? ` ${extraClassName}` : ""}`,
      style: { color: `var(--sh-${type})` },
      properties: {}
    };
    mark?.(token);
    return token;
  }
  function render(parsed, options) {
    const cx = options?.cx;
    const mark = options?.mark;
    const markLine = options?.markLine;
    if (!cx && !mark && !markLine) {
      return parsed.lines.map((line) => {
        const className = `sh__line${line.annotations.map((annotation) => ` sh__line--${annotation}`).join("")}`;
        const children = line.tokens.map(({ type, value }) => `<span class="sh__token--${type}" style="color:var(--sh-${type})">${encode(value)}</span>`).join("");
        return `<span class="${encode(className)}">${children}</span>`;
      }).join("\n");
    }
    return parsed.lines.map((parsedLine) => {
      const line = createLine(parsedLine, markLine);
      const children = parsedLine.tokens.map((parsedToken) => {
        const token = createToken(parsedToken, cx, mark);
        return `<span ${attributes({
          ...token.properties,
          className: token.className,
          style: token.style
        })}>${encode(token.value)}</span>`;
      }).join("");
      return `<span ${attributes({
        ...line.properties,
        className: line.className,
        style: line.style
      })}>${children}</span>`;
    }).join("\n");
  }
  var entities = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" };
  var encode = (value) => value.replace(/[&<>"']/g, (character) => entities[character]);
  function attributes(values) {
    const style = Object.entries(values.style || {}).map(([key, value]) => `${key.replace(/[A-Z]/g, (match) => `-${match.toLowerCase()}`)}:${value}`).join(";");
    const properties = Object.entries(values).filter(([key, value]) => /^[\w:-]+$/.test(key) && key !== "className" && key !== "style" && value !== false && value != null).map(([key, value]) => value === true ? key : `${key}="${encode(String(value))}"`).join(" ");
    return `class="${encode(values.className || "")}"${style ? ` style="${encode(style)}"` : ""}${properties ? ` ${properties}` : ""}`;
  }

  // node_modules/sugar-high/lib/core.js
  var signs = new Set("+-*/%=!&|^~?:.,;()[]{}<>#@\\".split(""));
  var noComment = () => 0;
  var isWord = (value) => value === "_" || value === "$" || /[\p{L}\p{N}]/u.test(value);
  function isQuotedKey(code, index) {
    while (index < code.length && /\s/.test(code[index])) index++;
    return code[index] === ":";
  }
  function tokenize(code, options) {
    if (typeof options?.tokenize === "function") return options.tokenize(code, options);
    const keywords27 = options?.keywords || /* @__PURE__ */ new Set();
    const typeKeywords12 = options?.typeKeywords || /* @__PURE__ */ new Set();
    const onCommentStart14 = options?.onCommentStart || noComment;
    const onCommentEnd14 = options?.onCommentEnd || noComment;
    const normalize = options?.caseInsensitive ? (value) => value.toLowerCase() : (value) => value;
    const tokens = [];
    let lastSignificant = "";
    function append(type, value) {
      if (!value) return;
      tokens.push([type, value]);
      if (type !== T_SPACE && type !== T_BREAK) lastSignificant = value;
    }
    for (let i = 0; i < code.length; ) {
      const curr = code[i];
      const next = code[i + 1];
      const commentType = onCommentStart14(curr, next, i, code);
      if (commentType) {
        const start = i++;
        while (i < code.length) {
          if (onCommentEnd14(code[i - 1], code[i], i, code, start) == commentType) {
            i++;
            break;
          }
          i++;
        }
        append(T_COMMENT, code.slice(start, i));
        continue;
      }
      const literalLength = options?.onLiteral?.(curr, i, code);
      if (literalLength) {
        append(T_STRING, code.slice(i, i + literalLength));
        i += literalLength;
        continue;
      }
      if (typeof options?.onQuote === "function" && curr === "'") {
        const length = options.onQuote(curr, i, code);
        if (typeof length === "number" && length >= 1) {
          append(T_IDENTIFIER, code.slice(i, i + length));
          i += length;
          continue;
        }
      }
      if (curr === '"' || curr === "'" || options?.templateStrings && curr === "`") {
        const quote = curr;
        const start = i++;
        while (i < code.length) {
          if (code[i] === quote && code[i - 1] !== "\\") {
            i++;
            break;
          }
          i++;
        }
        const value = code.slice(start, i);
        append(options?.quotedKeys && isQuotedKey(code, i) ? T_PROPERTY : T_STRING, value);
        continue;
      }
      if (curr === "\n") {
        append(T_BREAK, curr);
        i++;
        continue;
      }
      if (/[^\S\r\n]/.test(curr)) {
        const start = i++;
        while (i < code.length && /[^\S\r\n]/.test(code[i])) i++;
        append(T_SPACE, code.slice(start, i));
        continue;
      }
      if (isWord(curr)) {
        const start = i++;
        while (i < code.length && isWord(code[i])) i++;
        if (/^\d/.test(curr) && code[i] === "." && /\d/.test(code[i + 1] || "")) {
          i++;
          while (i < code.length && isWord(code[i])) i++;
        }
        const value = code.slice(start, i);
        const normalized = normalize(value);
        const type = typeKeywords12.has(normalized) ? T_CLASS : keywords27.has(normalized) ? T_KEYWORD : lastSignificant === "." ? T_PROPERTY : /^\d/.test(value) || value === "null" || /^\p{Lu}/u.test(value) ? T_CLASS : T_IDENTIFIER;
        append(type, value);
        continue;
      }
      if (signs.has(curr)) {
        append(T_SIGN, curr);
        i++;
        continue;
      }
      append(T_STRING, curr);
      i++;
    }
    return tokens;
  }
  function parse(code, options) {
    const parsed = assemble(code, tokenize(code, options));
    if (options?.annotateLine) {
      for (const line of parsed.lines) options.annotateLine(line);
    }
    return parsed;
  }

  // node_modules/sugar-high/lib/lang/c.js
  var c_exports = {};
  __export(c_exports, {
    keywords: () => keywords,
    onCommentEnd: () => onCommentEnd,
    onCommentStart: () => onCommentStart,
    typeKeywords: () => typeKeywords
  });

  // node_modules/sugar-high/lib/presets/clike-base.js
  var onCommentStart = (currentChar, nextChar) => {
    const pair = currentChar + nextChar;
    if (pair === "//") return 1;
    if (pair === "/*") return 2;
    return 0;
  };
  var onCommentEnd = (prevChar, currChar) => {
    if (currChar === "\n") return 1;
    return prevChar + currChar === "*/" ? 2 : 0;
  };

  // node_modules/sugar-high/lib/lang/c.js
  var typeKeywords = /* @__PURE__ */ new Set([
    "void",
    "char",
    "short",
    "int",
    "long",
    "float",
    "double",
    "signed",
    "unsigned",
    "_Bool",
    "_Complex",
    "_Imaginary"
  ]);
  var keywords = /* @__PURE__ */ new Set([
    "auto",
    "break",
    "case",
    "const",
    "continue",
    "default",
    "do",
    "else",
    "enum",
    "extern",
    "for",
    "goto",
    "if",
    "inline",
    "register",
    "restrict",
    "return",
    "sizeof",
    "static",
    "struct",
    "switch",
    "typedef",
    "union",
    "volatile",
    "while",
    "_Alignas",
    "_Alignof",
    "_Atomic",
    "_Generic",
    "_Noreturn",
    "_Static_assert",
    "_Thread_local"
  ]);

  // node_modules/sugar-high/lib/lang/cpp.js
  var cpp_exports = {};
  __export(cpp_exports, {
    keywords: () => keywords2,
    onCommentEnd: () => onCommentEnd,
    onCommentStart: () => onCommentStart,
    typeKeywords: () => typeKeywords2
  });
  var keywords2 = /* @__PURE__ */ new Set([
    "alignas",
    "alignof",
    "and",
    "and_eq",
    "asm",
    "auto",
    "bitand",
    "bitor",
    "break",
    "case",
    "catch",
    "class",
    "compl",
    "concept",
    "const",
    "consteval",
    "constexpr",
    "constinit",
    "const_cast",
    "continue",
    "co_await",
    "co_return",
    "co_yield",
    "decltype",
    "default",
    "delete",
    "do",
    "dynamic_cast",
    "else",
    "enum",
    "explicit",
    "export",
    "extern",
    "false",
    "for",
    "friend",
    "goto",
    "if",
    "inline",
    "mutable",
    "namespace",
    "new",
    "noexcept",
    "not",
    "not_eq",
    "nullptr",
    "operator",
    "or",
    "or_eq",
    "private",
    "protected",
    "public",
    "register",
    "reinterpret_cast",
    "requires",
    "return",
    "sizeof",
    "static",
    "static_assert",
    "static_cast",
    "struct",
    "switch",
    "template",
    "this",
    "thread_local",
    "throw",
    "true",
    "try",
    "typedef",
    "typeid",
    "typename",
    "union",
    "using",
    "virtual",
    "volatile",
    "while",
    "xor",
    "xor_eq"
  ]);
  var typeKeywords2 = /* @__PURE__ */ new Set([
    "bool",
    "char",
    "char8_t",
    "char16_t",
    "char32_t",
    "double",
    "float",
    "int",
    "long",
    "short",
    "signed",
    "unsigned",
    "void",
    "wchar_t"
  ]);

  // node_modules/sugar-high/lib/lang/csharp.js
  var csharp_exports = {};
  __export(csharp_exports, {
    keywords: () => keywords3,
    onCommentEnd: () => onCommentEnd,
    onCommentStart: () => onCommentStart,
    typeKeywords: () => typeKeywords3
  });
  var keywords3 = /* @__PURE__ */ new Set([
    "abstract",
    "as",
    "async",
    "await",
    "base",
    "break",
    "case",
    "catch",
    "checked",
    "class",
    "const",
    "continue",
    "default",
    "delegate",
    "do",
    "else",
    "enum",
    "event",
    "explicit",
    "extern",
    "false",
    "finally",
    "fixed",
    "for",
    "foreach",
    "from",
    "get",
    "global",
    "goto",
    "if",
    "implicit",
    "in",
    "init",
    "interface",
    "internal",
    "into",
    "is",
    "join",
    "let",
    "lock",
    "namespace",
    "new",
    "null",
    "on",
    "operator",
    "orderby",
    "out",
    "override",
    "params",
    "partial",
    "private",
    "protected",
    "public",
    "readonly",
    "record",
    "ref",
    "remove",
    "required",
    "return",
    "sealed",
    "select",
    "set",
    "sizeof",
    "stackalloc",
    "static",
    "struct",
    "switch",
    "this",
    "throw",
    "true",
    "try",
    "typeof",
    "unchecked",
    "unsafe",
    "using",
    "value",
    "virtual",
    "volatile",
    "when",
    "where",
    "while",
    "with",
    "yield"
  ]);
  var typeKeywords3 = /* @__PURE__ */ new Set([
    "bool",
    "byte",
    "char",
    "decimal",
    "double",
    "dynamic",
    "float",
    "int",
    "long",
    "nint",
    "nuint",
    "object",
    "sbyte",
    "short",
    "string",
    "uint",
    "ulong",
    "ushort",
    "void"
  ]);

  // node_modules/sugar-high/lib/lang/css.js
  var css_exports = {};
  __export(css_exports, {
    keywords: () => keywords4,
    onCommentEnd: () => onCommentEnd2,
    onCommentStart: () => onCommentStart2,
    onLiteral: () => onLiteral,
    tokenize: () => tokenize2
  });
  var keywords4 = /* @__PURE__ */ new Set([
    // css keywords like @media, @import, @keyframes, etc.
    "@media",
    "@import",
    "@keyframes",
    "@font-face",
    "@supports",
    "@page",
    "@counter-style",
    "@font-feature-values",
    "@viewport",
    "@counter-style",
    "@font-feature-values",
    "@document"
  ]);
  var onCommentStart2 = (currentChar, nextChar) => {
    return "/*" === currentChar + nextChar ? 1 : 0;
  };
  var onCommentEnd2 = (prevChar, currChar) => {
    return "*/" === prevChar + currChar ? 1 : 0;
  };
  var onLiteral = (curr, index, code) => {
    if (curr !== "#") return 0;
    return code.slice(index).match(/^#(?:[\da-f]{8}|[\da-f]{6}|[\da-f]{4}|[\da-f]{3})(?![\w-])/i)?.[0].length || 0;
  };
  var isIgnored = (type) => type === T_SPACE || type === T_BREAK || type === T_COMMENT;
  var isPropertyPart = ([type, value]) => type === T_IDENTIFIER || type === T_CLASS || type === T_SIGN && value === "-";
  var isNamePart = ([type]) => type === T_IDENTIFIER || type === T_CLASS || type === T_PROPERTY;
  var isNameStart = (token) => isNamePart(token) && !/^\d/.test(token[1]);
  var isHyphen = ([type, value]) => type === T_SIGN && value === "-";
  var mergeDashedNames = (tokens) => {
    for (let index = 0; index < tokens.length; index++) {
      let firstWord = index;
      let end = index;
      if (isHyphen(tokens[end])) {
        while (tokens[end] && isHyphen(tokens[end])) end++;
        if (!tokens[end] || !isNameStart(tokens[end])) continue;
        firstWord = end++;
      } else if (isNameStart(tokens[end])) {
        end++;
      } else {
        continue;
      }
      let dashed = firstWord > index;
      while (tokens[end] && isHyphen(tokens[end])) {
        const hyphenStart = end;
        while (tokens[end] && isHyphen(tokens[end])) end++;
        if (!tokens[end] || !isNamePart(tokens[end])) {
          end = hyphenStart;
          break;
        }
        dashed = true;
        end++;
      }
      if (!dashed) continue;
      const name = tokens.slice(index, end).map(([, value]) => value).join("");
      tokens.splice(index, end - index, [tokens[firstWord][0], name]);
    }
  };
  var opensBlock = (tokens, start) => {
    let parentheses = 0;
    let brackets = 0;
    for (let index = start; index < tokens.length; index++) {
      const [type, value] = tokens[index];
      if (type !== T_SIGN) continue;
      if (value === "(") parentheses++;
      else if (value === ")") parentheses--;
      else if (value === "[") brackets++;
      else if (value === "]") brackets--;
      else if (!parentheses && !brackets && value === "{") return true;
      else if (!parentheses && !brackets && (value === ";" || value === "}")) return false;
    }
    return false;
  };
  var tokenize2 = (code, options) => {
    const tokens = tokenize(code, { ...options, tokenize: void 0 });
    mergeDashedNames(tokens);
    let blockDepth = 0;
    let declarationStart = false;
    for (let index = 0; index < tokens.length; index++) {
      const [type, value] = tokens[index];
      if (type === T_SIGN && value === "{") {
        blockDepth++;
        declarationStart = true;
        continue;
      }
      if (type === T_SIGN && value === "}") {
        blockDepth--;
        declarationStart = false;
        continue;
      }
      if (type === T_SIGN && value === ";") {
        declarationStart = blockDepth > 0;
        continue;
      }
      if (!declarationStart || isIgnored(type)) continue;
      const propertyStart = index;
      let propertyEnd = index;
      while (propertyEnd < tokens.length && isPropertyPart(tokens[propertyEnd])) {
        propertyEnd++;
      }
      let colon = propertyEnd;
      while (colon < tokens.length && isIgnored(tokens[colon][0])) colon++;
      if (propertyEnd > propertyStart && tokens[colon]?.[0] === T_SIGN && tokens[colon][1] === ":" && !opensBlock(tokens, colon + 1)) {
        const property = tokens.slice(propertyStart, propertyEnd).map(([, part]) => part).join("");
        tokens.splice(propertyStart, propertyEnd - propertyStart, [T_PROPERTY, property]);
      }
      declarationStart = false;
    }
    return tokens;
  };

  // node_modules/sugar-high/lib/lang/diff.js
  var diff_exports = {};
  __export(diff_exports, {
    annotateLine: () => annotateLine,
    keywords: () => keywords5
  });
  var keywords5 = /* @__PURE__ */ new Set([]);
  var annotateLine = (line) => {
    let annotation = "";
    if (line.value.startsWith("+") && !line.value.startsWith("+++")) annotation = "diff-add";
    else if (line.value.startsWith("-") && !line.value.startsWith("---")) annotation = "diff-remove";
    else if (line.value.startsWith("@@")) annotation = "diff-hunk";
    else if (/^(diff --git|index |--- |\+\+\+ )/.test(line.value)) {
      annotation = "diff-meta";
      for (const token of line.tokens) {
        if (token.type === "property") token.type = "identifier";
      }
    }
    if (annotation) line.annotations.push(annotation);
  };

  // node_modules/sugar-high/lib/lang/dockerfile.js
  var dockerfile_exports = {};
  __export(dockerfile_exports, {
    caseInsensitive: () => caseInsensitive,
    keywords: () => keywords6,
    onCommentEnd: () => onCommentEnd3,
    onCommentStart: () => onCommentStart3
  });

  // node_modules/sugar-high/lib/presets/hash-comment-base.js
  var onCommentStart3 = (currentChar) => currentChar === "#" ? 1 : 0;
  var onCommentEnd3 = (_prevChar, currChar) => currChar === "\n" ? 1 : 0;

  // node_modules/sugar-high/lib/lang/dockerfile.js
  var caseInsensitive = true;
  var keywords6 = /* @__PURE__ */ new Set(["add", "arg", "cmd", "copy", "entrypoint", "env", "expose", "from", "healthcheck", "label", "maintainer", "onbuild", "run", "shell", "stopsignal", "user", "volume", "workdir"]);

  // node_modules/sugar-high/lib/lang/go.js
  var go_exports = {};
  __export(go_exports, {
    keywords: () => keywords7,
    onCommentEnd: () => onCommentEnd,
    onCommentStart: () => onCommentStart,
    typeKeywords: () => typeKeywords4
  });
  var typeKeywords4 = /* @__PURE__ */ new Set([
    "bool",
    "byte",
    "complex64",
    "complex128",
    "error",
    "float32",
    "float64",
    "int",
    "int8",
    "int16",
    "int32",
    "int64",
    "rune",
    "string",
    "uint",
    "uint8",
    "uint16",
    "uint32",
    "uint64",
    "uintptr"
  ]);
  var keywords7 = /* @__PURE__ */ new Set([
    "break",
    "case",
    "chan",
    "const",
    "continue",
    "default",
    "defer",
    "else",
    "fallthrough",
    "for",
    "func",
    "go",
    "goto",
    "if",
    "import",
    "interface",
    "map",
    "package",
    "range",
    "return",
    "select",
    "struct",
    "switch",
    "type",
    "var"
  ]);

  // node_modules/sugar-high/lib/lang/graphql.js
  var graphql_exports = {};
  __export(graphql_exports, {
    keywords: () => keywords8,
    onCommentEnd: () => onCommentEnd3,
    onCommentStart: () => onCommentStart3,
    typeKeywords: () => typeKeywords5
  });
  var keywords8 = /* @__PURE__ */ new Set(["directive", "enum", "extend", "fragment", "implements", "input", "interface", "mutation", "on", "query", "repeatable", "scalar", "schema", "subscription", "type", "union"]);
  var typeKeywords5 = /* @__PURE__ */ new Set(["Boolean", "Float", "ID", "Int", "String"]);

  // node_modules/sugar-high/lib/lang/hcl.js
  var hcl_exports = {};
  __export(hcl_exports, {
    keywords: () => keywords9,
    onCommentEnd: () => onCommentEnd4,
    onCommentStart: () => onCommentStart4
  });
  var keywords9 = /* @__PURE__ */ new Set(["false", "for", "if", "in", "null", "true"]);
  var onCommentStart4 = (curr, next) => curr === "#" ? 1 : curr + next === "//" ? 1 : curr + next === "/*" ? 2 : 0;
  var onCommentEnd4 = (prev, curr) => curr === "\n" ? 1 : prev + curr === "*/" ? 2 : 0;

  // node_modules/sugar-high/lib/lang/html.js
  var html_exports = {};
  __export(html_exports, {
    jsx: () => jsx,
    keywords: () => keywords10,
    onCommentEnd: () => onCommentEnd5,
    onCommentStart: () => onCommentStart5,
    regex: () => regex,
    templateStrings: () => templateStrings,
    tokenize: () => tokenize4
  });

  // node_modules/sugar-high/lib/presets/javascript-runtime.js
  var JSXBrackets = /* @__PURE__ */ new Set(["<", ">", "{", "}", "[", "]"]);
  var Keywords_Js = /* @__PURE__ */ new Set([
    "for",
    "do",
    "while",
    "if",
    "else",
    "return",
    "function",
    "var",
    "let",
    "const",
    "true",
    "false",
    "undefined",
    "this",
    "new",
    "delete",
    "typeof",
    "in",
    "instanceof",
    "void",
    "break",
    "continue",
    "switch",
    "case",
    "default",
    "throw",
    "try",
    "catch",
    "finally",
    "debugger",
    "with",
    "yield",
    "async",
    "await",
    "class",
    "extends",
    "super",
    "import",
    "export",
    "from",
    "static"
  ]);
  var Keywords_Ts = /* @__PURE__ */ new Set([
    ...Keywords_Js,
    "type",
    "interface",
    "enum",
    "implements",
    "readonly",
    "abstract",
    "declare",
    "namespace",
    "module",
    "private",
    "protected",
    "public",
    "override",
    "keyof",
    "infer",
    "is",
    "asserts",
    "satisfies",
    "as",
    "unknown",
    "never",
    "any",
    "number",
    "string",
    "boolean",
    "bigint",
    "symbol",
    "object"
  ]);
  var Signs = /* @__PURE__ */ new Set([
    "+",
    "-",
    "*",
    "/",
    "%",
    "=",
    "!",
    "&",
    "|",
    "^",
    "~",
    "!",
    "?",
    ":",
    ".",
    ",",
    ";",
    `'`,
    '"',
    ".",
    "(",
    ")",
    "[",
    "]",
    "#",
    "@",
    "\\",
    ...JSXBrackets
  ]);
  var DefaultOptions = {
    keywords: Keywords_Js,
    onCommentStart: isCommentStart_Js,
    onCommentEnd: isCommentEnd_Js,
    jsx: true,
    regex: true,
    templateStrings: true
  };
  function resolveHighlightOptions(options) {
    return {
      ...DefaultOptions,
      ...options
    };
  }
  function isLikelyTypeScript(code) {
    let tsScore = 0;
    if (/\binterface\s+[A-Za-z_$][\w$]*/.test(code)) tsScore += 2;
    if (/\btype\s+[A-Za-z_$][\w$]*\s*=/.test(code)) tsScore += 2;
    if (/\benum\s+[A-Za-z_$][\w$]*/.test(code)) tsScore += 2;
    if (/\b(?:implements|readonly|declare|namespace|satisfies|infer|keyof|asserts)\b/.test(code)) tsScore += 2;
    if (/:\s*[A-Za-z_$][\w$]*(?:<[^>\n]+>)?(?:\[\])?(?=\s*[,)=;{])/m.test(code)) tsScore += 1;
    if (/\b(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*:\s*/.test(code)) tsScore += 1;
    if (/\)\s*:\s*[A-Za-z_$][\w$]*(?:<[^>\n]+>)?(?:\[\])?\s*(?:=>|\{)/.test(code)) tsScore += 1;
    return tsScore >= 2;
  }
  function isTypeParameterListStart(code, startIndex) {
    if (code[startIndex] !== "<") return false;
    let depth = 0;
    let sawIdentifierStart = false;
    for (let i = startIndex; i < code.length; i++) {
      const ch = code[i];
      if (ch === "<") {
        depth++;
        continue;
      }
      if (ch === ">") {
        depth--;
        if (depth === 0) {
          let next = i + 1;
          while (next < code.length && /\s/.test(code[next])) next++;
          if (!(sawIdentifierStart && code[next] === "(")) return false;
          const tail = code.slice(next, next + 320);
          return /\)\s*(?::[\s\S]{0,120}?)?=>/.test(tail);
        }
        continue;
      }
      if (depth === 0) continue;
      if (/[$A-Za-z_]/.test(ch)) {
        sawIdentifierStart = true;
        continue;
      }
      if (/[\s,\.\=\?\:\|\&\[\]]/.test(ch)) continue;
      return false;
    }
    return false;
  }
  function isSpaces(str) {
    return /^[^\S\r\n]+$/g.test(str);
  }
  function isSign(ch) {
    return Signs.has(ch);
  }
  function isWord2(chr) {
    return /^[\w_]+$/.test(chr) || hasUnicode(chr);
  }
  function isCls(str) {
    const chr0 = str[0];
    return isWord2(chr0) && chr0 === chr0.toUpperCase() || str === "null";
  }
  function hasUnicode(s) {
    return /[^\u0000-\u007f]/.test(s);
  }
  function isAlpha(chr) {
    return /^[a-zA-Z]$/.test(chr);
  }
  function isIdentifierChar(chr) {
    return isAlpha(chr) || hasUnicode(chr);
  }
  function isIdentifier(str) {
    return isIdentifierChar(str[0]) && (str.length === 1 || isWord2(str.slice(1)));
  }
  function isStrTemplateChr(chr) {
    return chr === "`";
  }
  function isSingleQuotes(chr) {
    return chr === '"' || chr === "'";
  }
  function isCommentStart_Js(curr, next) {
    const str = curr + next;
    if (str === "/*") return 2;
    return str === "//" ? 1 : 0;
  }
  function isCommentEnd_Js(prev, curr) {
    return prev + curr === "*/" ? 2 : curr === "\n" ? 1 : 0;
  }
  function isRegexStart(str) {
    return str[0] === "/" && !isCommentStart_Js(str[0], str[1]);
  }
  function isPropertyKey(code, quoteEnd) {
    let i = quoteEnd + 1;
    while (i < code.length && /\s/.test(code[i])) i++;
    return code[i] === ":";
  }
  function tokenize3(code, options) {
    const mergedOptions = resolveHighlightOptions(options);
    const hasCustomKeywords = mergedOptions.keywords !== DefaultOptions.keywords;
    const isTs = typeof mergedOptions.typescript === "boolean" ? mergedOptions.typescript : isLikelyTypeScript(code);
    const resolvedKeywords = hasCustomKeywords ? mergedOptions.keywords : isTs ? Keywords_Ts : Keywords_Js;
    const {
      onCommentStart: onCommentStart14,
      onCommentEnd: onCommentEnd14
    } = mergedOptions;
    const resolvedTypeKeywords = mergedOptions.typeKeywords instanceof Set ? mergedOptions.typeKeywords : null;
    const supportsJsx = mergedOptions.jsx !== false;
    const supportsRegex = mergedOptions.regex !== false;
    const supportsTemplateStrings = mergedOptions.templateStrings !== false;
    const normalizeKeyword = mergedOptions.caseInsensitive ? (token) => token.toLowerCase() : (token) => token;
    const isTemplateQuote = (chr) => supportsTemplateStrings && isStrTemplateChr(chr);
    let current = "";
    let type = -1;
    let last = [-1, ""];
    let beforeLast = [-2, ""];
    const tokens = [];
    let __jsxEnter = false;
    let __jsxTag = 0;
    let __jsxExpr = false;
    let __jsxTagExpr = 0;
    let __jsxStack = 0;
    const __jsxChild = () => __jsxEnter && !__jsxExpr && !__jsxTag;
    const inJsxTag = () => __jsxTag && !__jsxChild();
    const inJsxLiterals = () => !__jsxTag && __jsxChild() && !__jsxExpr && __jsxStack > 0;
    let __strQuote = null;
    let __strTokenStart = 0;
    let __regexQuoteStart = false;
    let __strTemplateExprStack = 0;
    let __strTemplateQuoteStack = 0;
    const inStringQuotes = () => __strQuote !== null;
    const inRegexQuotes = () => __regexQuoteStart;
    const inStrTemplateLiterals = () => __strTemplateQuoteStack > __strTemplateExprStack;
    const inStrTemplateExpr = () => __strTemplateQuoteStack > 0 && __strTemplateQuoteStack === __strTemplateExprStack;
    const inStringContent = () => inStringQuotes() || inStrTemplateLiterals();
    function classify(token) {
      const isLineBreak = token === "\n";
      if (inJsxTag()) {
        if (inStringQuotes()) {
          return T_STRING;
        }
        const [, lastToken] = last;
        if (isIdentifier(token)) {
          if (lastToken === "<" || lastToken === "</")
            return T_ENTITY;
          if (!__jsxTagExpr && /^\s+$/.test(tokens[tokens.length - 1]?.[1] || ""))
            return T_PROPERTY;
        }
      }
      const isJsxLiterals = inJsxLiterals();
      if (isJsxLiterals) return T_JSX_LITERALS;
      if (inStringQuotes() || inStrTemplateLiterals()) {
        return T_STRING;
      } else if (resolvedTypeKeywords && resolvedTypeKeywords.has(normalizeKeyword(token))) {
        return last[1] === "." ? T_IDENTIFIER : T_CLASS;
      } else if (resolvedKeywords.has(normalizeKeyword(token))) {
        return last[1] === "." ? T_IDENTIFIER : T_KEYWORD;
      } else if (isLineBreak) {
        return T_BREAK;
      } else if (isSpaces(token)) {
        return T_SPACE;
      } else if (token.split("").every(isSign)) {
        return T_SIGN;
      } else if (isCls(token)) {
        return inJsxTag() && !__jsxTagExpr ? T_IDENTIFIER : T_CLASS;
      } else {
        if (isIdentifier(token)) {
          const isLastPropDot = last[1] === "." && isIdentifier(beforeLast[1]);
          if (!inStringContent() && !isLastPropDot) return T_IDENTIFIER;
          if (isLastPropDot) return T_PROPERTY;
        }
        return T_STRING;
      }
    }
    const append = (type_, token_) => {
      if (token_) {
        current = token_;
      }
      if (current) {
        type = typeof type_ === "number" ? type_ : classify(current);
        const pair = [type, current];
        if (type !== T_SPACE && type !== T_BREAK) {
          beforeLast = last;
          last = pair;
        }
        if (inJsxTag() && type === T_SIGN) {
          if (current === "{") __jsxTagExpr++;
          if (current === "}") __jsxTagExpr--;
        }
        tokens.push(pair);
      }
      current = "";
    };
    for (let i = 0; i < code.length; i++) {
      const curr = code[i];
      const prev = code[i - 1];
      const next = code[i + 1];
      const p_c = prev + curr;
      const c_n = curr + next;
      if (typeof mergedOptions.onQuote === "function" && curr === "'" && !inStringQuotes() && !inJsxLiterals() && !inStrTemplateLiterals()) {
        const rawLen = mergedOptions.onQuote(curr, i, code);
        if (typeof rawLen === "number" && rawLen >= 1 && !Number.isNaN(rawLen)) {
          const len = Math.min(rawLen, code.length - i);
          const end = i + len;
          append();
          current = code.slice(i, end);
          append(T_IDENTIFIER);
          i = end - 1;
          continue;
        }
      }
      if (isSingleQuotes(curr) && !inJsxLiterals() && !inStrTemplateLiterals()) {
        append();
        let isStringClose = false;
        if (prev !== `\\`) {
          if (__strQuote && curr === __strQuote) {
            __strQuote = null;
            isStringClose = true;
          } else if (!__strQuote) {
            __strQuote = curr;
            __strTokenStart = tokens.length;
          }
        }
        append(T_STRING, curr);
        if (mergedOptions.quotedKeys && isStringClose && isPropertyKey(code, i)) {
          for (let tokenIndex = __strTokenStart; tokenIndex < tokens.length; tokenIndex++) {
            tokens[tokenIndex][0] = T_PROPERTY;
          }
        }
        continue;
      }
      if (!inStrTemplateLiterals()) {
        if (prev !== "\\n" && isTemplateQuote(curr)) {
          append();
          append(T_STRING, curr);
          __strTemplateQuoteStack++;
          continue;
        }
      }
      if (inStrTemplateLiterals()) {
        if (prev !== "\\n" && isTemplateQuote(curr)) {
          if (__strTemplateQuoteStack > 0) {
            append();
            __strTemplateQuoteStack--;
            append(T_STRING, curr);
            continue;
          }
        }
        if (c_n === "${") {
          __strTemplateExprStack++;
          append(T_STRING);
          append(T_SIGN, c_n);
          i++;
          continue;
        }
      }
      if (inStrTemplateExpr() && curr === "}") {
        append();
        __strTemplateExprStack--;
        append(T_SIGN, curr);
        continue;
      }
      if (__jsxChild()) {
        if (curr === "{") {
          append();
          append(T_SIGN, curr);
          __jsxExpr = true;
          continue;
        }
      }
      if (__jsxEnter) {
        if (!__jsxTag && curr === "<") {
          append();
          if (next === "/") {
            __jsxTag = 2;
            current = c_n;
            i++;
          } else {
            __jsxTag = 1;
            current = curr;
          }
          append(T_SIGN);
          continue;
        }
        if (__jsxTag) {
          if (curr === ">" && !"/=".includes(prev)) {
            append();
            if (__jsxTag === 1) {
              __jsxTag = 0;
              __jsxStack++;
            } else {
              __jsxTag = 0;
              __jsxEnter = false;
            }
            append(T_SIGN, curr);
            continue;
          }
          if (c_n === "/>" || c_n === "</") {
            if (current !== "<" && current !== "/") {
              append();
            }
            if (c_n === "/>") {
              __jsxTag = 0;
            } else {
              __jsxStack--;
            }
            if (!__jsxStack)
              __jsxEnter = false;
            current = c_n;
            i++;
            append(T_SIGN);
            continue;
          }
          if (curr === "<") {
            append();
            current = curr;
            append(T_SIGN);
            continue;
          }
          if (curr === "-" && current && !inStringContent() && !inJsxLiterals()) {
            let end = i + 1;
            while (end < code.length && /[$\w-]/.test(code[end])) end++;
            append(T_PROPERTY, current + code.slice(i, end));
            i = end - 1;
            continue;
          }
          if (next === "=" && !inStringContent()) {
            if (!isSpaces(curr)) {
              if (isSpaces(current)) {
                append();
              }
              const prop = current + curr;
              if (isIdentifier(prop)) {
                append(T_PROPERTY, prop);
                continue;
              }
            }
          }
        }
      }
      if (supportsJsx && !__jsxTag && (curr === "<" && isIdentifierChar(next) || c_n === "</")) {
        let prevNonSpace = i - 1;
        while (prevNonSpace >= 0 && /\s/.test(code[prevNonSpace])) prevNonSpace--;
        const prevChar = prevNonSpace >= 0 ? code[prevNonSpace] : "";
        const [lastType, lastTok] = last;
        let typeArgFromPending = false;
        let jsxFromPending = false;
        if (current && !isSpaces(current)) {
          const w = current;
          if (isCls(w) || w === "true" || w === "false") {
            typeArgFromPending = true;
          } else if (resolvedKeywords.has(w) && isIdentifier(w)) {
            jsxFromPending = true;
          } else if (isIdentifier(w)) {
            typeArgFromPending = true;
          }
        }
        const isTsTypeArgStart = curr === "<" && /[$\w\]\)]/.test(prevChar) && (typeArgFromPending || !jsxFromPending && (lastType === T_IDENTIFIER || lastType === T_CLASS || lastType === T_SIGN && (lastTok === ")" || lastTok === "]")));
        const isTsGenericStart = curr === "<" && isTypeParameterListStart(code, i);
        if (!isTsTypeArgStart && !isTsGenericStart) {
          __jsxTag = next === "/" ? 2 : 1;
        }
        if (curr === "<" && (next === "/" || isAlpha(next))) {
          if (!isTsTypeArgStart && !isTsGenericStart && !inStringContent() && !inJsxLiterals() && !inRegexQuotes()) {
            __jsxEnter = true;
          }
        }
      }
      const isQuotationChar = isSingleQuotes(curr) || isTemplateQuote(curr);
      const isStringTemplateLiterals = inStrTemplateLiterals();
      const isRegexChar = supportsRegex && !__jsxEnter && isRegexStart(c_n);
      const isJsxLiterals = inJsxLiterals();
      if (isQuotationChar || isStringTemplateLiterals || isSingleQuotes(__strQuote)) {
        current += curr;
      } else if (isRegexChar) {
        append();
        const [lastType, lastToken] = last;
        if (isRegexChar && lastType !== -1 && !(lastType === T_SIGN && ")" !== lastToken || lastType === T_COMMENT)) {
          current = curr;
          append();
          continue;
        }
        __regexQuoteStart = true;
        const start = i++;
        const isEof = () => i >= code.length;
        const isEol = () => isEof() || code[i] === "\n";
        let foundClose = false;
        let inCharClass = false;
        for (; !isEol(); i++) {
          const ch = code[i];
          const escaped = code[i - 1] === "\\";
          if (!escaped && ch === "[") inCharClass = true;
          if (!escaped && ch === "]") inCharClass = false;
          if (ch === "/" && !inCharClass && !escaped) {
            foundClose = true;
            while (start !== i && /^[a-z]$/.test(code[i + 1]) && !isEol()) {
              i++;
            }
            break;
          }
        }
        __regexQuoteStart = false;
        if (start !== i && foundClose) {
          current = code.slice(start, i + 1);
          append(T_STRING);
        } else {
          current = curr;
          append();
          i = start;
        }
      } else if (onCommentStart14(curr, next, i, code)) {
        append();
        const start = i;
        const startCommentType = onCommentStart14(curr, next, i, code);
        if (startCommentType) {
          for (; i < code.length; i++) {
            const endCommentType = onCommentEnd14(code[i - 1], code[i], i, code);
            if (endCommentType == startCommentType) break;
          }
        }
        current = code.slice(start, i + 1);
        append(T_COMMENT);
      } else if (curr === " " || curr === "\n") {
        if (curr === " " && (isSpaces(current) || !current || isJsxLiterals)) {
          let end = i + 1;
          while (code[end] === " ") end++;
          current += code.slice(i, end);
          i = end - 1;
          if (code[end] === "<") {
            append();
          }
        } else {
          append();
          current = curr;
          append();
        }
      } else {
        if (__jsxExpr && curr === "}") {
          append();
          current = curr;
          append();
          __jsxExpr = false;
        } else if (
          // it's jsx literals and is not a jsx bracket
          isJsxLiterals && !JSXBrackets.has(curr) || // it's template literal content (including quotes)
          inStrTemplateLiterals() || // same type char as previous one in current token
          (isWord2(curr) === isWord2(current[current.length - 1]) || __jsxChild()) && !Signs.has(curr)
        ) {
          current += curr;
        } else {
          if (p_c === "</") {
            current = p_c;
          }
          append();
          if (p_c !== "</") {
            current = curr;
          }
          if (c_n === "</" || c_n === "/>") {
            current = c_n;
            append();
            i++;
          } else if (JSXBrackets.has(curr)) append();
        }
      }
    }
    append();
    return tokens;
  }

  // node_modules/sugar-high/lib/lang/html.js
  var keywords10 = /* @__PURE__ */ new Set([]);
  var jsx = true;
  var regex = false;
  var templateStrings = false;
  var tokenize4 = tokenize3;
  var onCommentStart5 = (_currentChar, _nextChar, index, code) => code.startsWith("<!--", index) ? 2 : 0;
  var onCommentEnd5 = (_prevChar, _currChar, index, code) => code.slice(index - 2, index + 1) === "-->" ? 2 : 0;

  // node_modules/sugar-high/lib/lang/java.js
  var java_exports = {};
  __export(java_exports, {
    keywords: () => keywords11,
    onCommentEnd: () => onCommentEnd,
    onCommentStart: () => onCommentStart,
    typeKeywords: () => typeKeywords6
  });
  var typeKeywords6 = /* @__PURE__ */ new Set([
    "boolean",
    "byte",
    "char",
    "double",
    "float",
    "int",
    "long",
    "short",
    "void"
  ]);
  var keywords11 = /* @__PURE__ */ new Set([
    "abstract",
    "assert",
    "break",
    "case",
    "catch",
    "class",
    "const",
    "continue",
    "default",
    "do",
    "else",
    "enum",
    "extends",
    "final",
    "finally",
    "for",
    "goto",
    "if",
    "implements",
    "import",
    "instanceof",
    "interface",
    "native",
    "new",
    "package",
    "private",
    "protected",
    "public",
    "return",
    "static",
    "strictfp",
    "super",
    "switch",
    "synchronized",
    "this",
    "throw",
    "throws",
    "transient",
    "try",
    "volatile",
    "while"
  ]);

  // node_modules/sugar-high/lib/lang/javascript.js
  var javascript_exports = {};
  __export(javascript_exports, {
    tokenize: () => tokenize5
  });
  var tokenize5 = tokenize3;

  // node_modules/sugar-high/lib/lang/json.js
  var json_exports = {};
  __export(json_exports, {
    keywords: () => keywords12,
    onCommentEnd: () => onCommentEnd,
    onCommentStart: () => onCommentStart,
    quotedKeys: () => quotedKeys
  });
  var keywords12 = /* @__PURE__ */ new Set(["true", "false", "null"]);
  var quotedKeys = true;

  // node_modules/sugar-high/lib/lang/kotlin.js
  var kotlin_exports = {};
  __export(kotlin_exports, {
    keywords: () => keywords13,
    onCommentEnd: () => onCommentEnd,
    onCommentStart: () => onCommentStart,
    typeKeywords: () => typeKeywords7
  });
  var keywords13 = /* @__PURE__ */ new Set(["as", "break", "by", "catch", "class", "companion", "const", "constructor", "continue", "data", "do", "else", "enum", "false", "finally", "for", "fun", "get", "if", "import", "in", "infix", "init", "interface", "internal", "is", "lateinit", "noinline", "null", "object", "open", "operator", "out", "override", "package", "private", "protected", "public", "reified", "return", "sealed", "set", "suspend", "tailrec", "this", "throw", "true", "try", "typealias", "val", "var", "vararg", "when", "where", "while"]);
  var typeKeywords7 = /* @__PURE__ */ new Set(["Any", "Boolean", "Byte", "Char", "Double", "Float", "Int", "Long", "Nothing", "Short", "String", "Unit"]);

  // node_modules/sugar-high/lib/lang/lua.js
  var lua_exports = {};
  __export(lua_exports, {
    keywords: () => keywords14,
    onCommentEnd: () => onCommentEnd6,
    onCommentStart: () => onCommentStart6,
    onLiteral: () => onLiteral2
  });
  var keywords14 = /* @__PURE__ */ new Set([
    "and",
    "break",
    "do",
    "else",
    "elseif",
    "end",
    "false",
    "for",
    "function",
    "goto",
    "if",
    "in",
    "local",
    "nil",
    "not",
    "or",
    "repeat",
    "return",
    "then",
    "true",
    "until",
    "while"
  ]);
  function onCommentStart6(curr, next, index, code) {
    if (curr === "#" && index === 0 && next === "!") return 1;
    if (curr + next !== "--") return 0;
    return /^--\[=*\[/.test(code.slice(index)) ? 2 : 1;
  }
  function onCommentEnd6(_prev, curr, index, code, start) {
    if (curr === "\n") return 1;
    if (curr !== "]") return 0;
    const opener = code.slice(start).match(/^--\[(=*)\[/);
    if (!opener) return 0;
    const closing = `]${opener[1]}]`;
    if (code.slice(index - closing.length + 1, index + 1) === closing) return 2;
    return 0;
  }
  function onLiteral2(curr, index, code) {
    if (curr !== "[") return 0;
    const opener = code.slice(index).match(/^\[(=*)\[/);
    if (!opener) return 0;
    const closing = `]${opener[1]}]`;
    const end = code.indexOf(closing, index + opener[0].length);
    return end === -1 ? code.length - index : end + closing.length - index;
  }

  // node_modules/sugar-high/lib/lang/markdown.js
  var markdown_exports = {};
  __export(markdown_exports, {
    annotateLine: () => annotateLine2,
    keywords: () => keywords15,
    onCommentEnd: () => onCommentEnd7,
    onCommentStart: () => onCommentStart7,
    tokenize: () => tokenize6
  });

  // node_modules/sugar-high/lib/presets/plain-base.js
  var onCommentStart7 = () => 0;
  var onCommentEnd7 = () => 0;

  // node_modules/sugar-high/lib/lang/markdown.js
  var keywords15 = /* @__PURE__ */ new Set([]);
  var tokenize6 = (code) => {
    const tokens = [];
    let fence = "";
    let fenceQuoted = false;
    const append = (type, value) => {
      if (!value) return;
      const previous = tokens[tokens.length - 1];
      if (previous?.[0] === type) previous[1] += value;
      else tokens.push([type, value]);
    };
    for (const part of code.match(/[^\n]*(?:\n|$)/g) || []) {
      if (!part) continue;
      const newline = part.endsWith("\n") ? "\n" : "";
      const line = newline ? part.slice(0, -1) : part;
      const ranges = [];
      const container = line.match(/^(?: {0,3}>[ \t]?)*/)?.[0] || "";
      if (!fence || fenceQuoted) {
        for (const match of container.matchAll(/>/g)) {
          ranges.push([match.index, match.index + 1]);
        }
      }
      let start = container.length;
      let spaces = 0;
      while (spaces < 3 && line[start] === " ") {
        start++;
        spaces++;
      }
      const fenceMarker = line.slice(start).match(/^(`{3,}|~{3,})/)?.[1];
      if (fence) {
        if (fenceMarker?.[0] === fence[0] && fenceMarker.length >= fence.length && /^\s*$/.test(line.slice(start + fenceMarker.length))) {
          ranges.push([start, start + fenceMarker.length]);
          fence = "";
          fenceQuoted = false;
        }
      } else if (fenceMarker) {
        ranges.push([start, start + fenceMarker.length]);
        fence = fenceMarker;
        fenceQuoted = container.includes(">");
      } else {
        const prefix = line.slice(start).match(
          /^(#{1,6}(?=\s)|(?:[-+*]|\d+[.)])(?=\s)|(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})$)/
        )?.[1];
        if (prefix) ranges.push([start, start + prefix.length]);
        for (const match of line.matchAll(/(`+)(.*?)\1|(\*{1,3}|_{1,3}|~{2})(?=\S)(.*?\S)\3/g)) {
          const marker = match[1] || match[3];
          if (line[match.index - 1] === "\\" || marker[0] === "_" && /\w/.test(line[match.index - 1] || "")) continue;
          ranges.push(
            [match.index, match.index + marker.length],
            [match.index + match[0].length - marker.length, match.index + match[0].length]
          );
        }
      }
      let offset = 0;
      for (const [rangeStart, rangeEnd] of ranges.sort((a, b) => a[0] - b[0])) {
        if (rangeStart < offset) continue;
        append(T_IDENTIFIER, line.slice(offset, rangeStart));
        append(T_SIGN, line.slice(rangeStart, rangeEnd));
        offset = rangeEnd;
      }
      append(T_IDENTIFIER, line.slice(offset) + newline);
    }
    return tokens;
  };
  var annotateLine2 = (line) => {
    let annotation = "";
    if (/^#{1,6}\s/.test(line.value)) annotation = "markdown-heading";
    else if (/^\s*>/.test(line.value)) annotation = "markdown-quote";
    else if (/^\s*(?:[-*+] |\d+[.)] )/.test(line.value)) annotation = "markdown-list";
    else if (/^\s*```/.test(line.value)) annotation = "markdown-fence";
    if (annotation) line.annotations.push(annotation);
  };

  // node_modules/sugar-high/lib/lang/php.js
  var php_exports = {};
  __export(php_exports, {
    keywords: () => keywords16,
    onCommentEnd: () => onCommentEnd8,
    onCommentStart: () => onCommentStart8,
    typeKeywords: () => typeKeywords8
  });
  var keywords16 = /* @__PURE__ */ new Set(["abstract", "and", "array", "as", "break", "callable", "case", "catch", "class", "clone", "const", "continue", "declare", "default", "do", "echo", "else", "elseif", "empty", "enddeclare", "endfor", "endforeach", "endif", "endswitch", "endwhile", "enum", "eval", "exit", "extends", "false", "final", "finally", "fn", "for", "foreach", "from", "function", "global", "goto", "if", "implements", "include", "include_once", "instanceof", "insteadof", "interface", "isset", "list", "match", "namespace", "new", "null", "or", "print", "private", "protected", "public", "readonly", "require", "require_once", "return", "static", "switch", "throw", "trait", "true", "try", "unset", "use", "var", "while", "xor", "yield"]);
  var typeKeywords8 = /* @__PURE__ */ new Set(["bool", "float", "int", "iterable", "mixed", "never", "object", "string", "void"]);
  var onCommentStart8 = (curr, next) => curr === "#" ? 1 : curr + next === "//" ? 1 : curr + next === "/*" ? 2 : 0;
  var onCommentEnd8 = (prev, curr) => curr === "\n" ? 1 : prev + curr === "*/" ? 2 : 0;

  // node_modules/sugar-high/lib/lang/plaintext.js
  var plaintext_exports = {};
  __export(plaintext_exports, {
    tokenize: () => tokenize7
  });
  var tokenize7 = (code) => [[T_IDENTIFIER, code]];

  // node_modules/sugar-high/lib/lang/powershell.js
  var powershell_exports = {};
  __export(powershell_exports, {
    caseInsensitive: () => caseInsensitive2,
    keywords: () => keywords17,
    onCommentEnd: () => onCommentEnd9,
    onCommentStart: () => onCommentStart9
  });
  var caseInsensitive2 = true;
  var keywords17 = /* @__PURE__ */ new Set(["begin", "break", "catch", "class", "continue", "data", "define", "do", "dynamicparam", "else", "elseif", "end", "enum", "exit", "filter", "finally", "for", "foreach", "from", "function", "if", "in", "param", "process", "return", "switch", "throw", "trap", "try", "until", "using", "while"]);
  var onCommentStart9 = (curr, next) => curr === "#" ? 1 : curr + next === "<#" ? 2 : 0;
  var onCommentEnd9 = (prev, curr) => curr === "\n" ? 1 : prev + curr === "#>" ? 2 : 0;

  // node_modules/sugar-high/lib/lang/python.js
  var python_exports = {};
  __export(python_exports, {
    keywords: () => keywords18,
    onCommentEnd: () => onCommentEnd10,
    onCommentStart: () => onCommentStart10
  });
  var keywords18 = /* @__PURE__ */ new Set([
    "and",
    "as",
    "assert",
    "async",
    "await",
    "break",
    "class",
    "continue",
    "def",
    "del",
    "elif",
    "else",
    "except",
    "finally",
    "for",
    "from",
    "global",
    "if",
    "import",
    "in",
    "is",
    "lambda",
    "nonlocal",
    "not",
    "or",
    "pass",
    "raise",
    "return",
    "try",
    "while",
    "with",
    "yield"
  ]);
  var onCommentStart10 = (currentChar, _nextChar) => {
    return currentChar === "#" ? 1 : 0;
  };
  var onCommentEnd10 = (_prevChar, currChar) => {
    return currChar === "\n" ? 1 : 0;
  };

  // node_modules/sugar-high/lib/lang/ruby.js
  var ruby_exports = {};
  __export(ruby_exports, {
    keywords: () => keywords19,
    onCommentEnd: () => onCommentEnd11,
    onCommentStart: () => onCommentStart11
  });
  var keywords19 = /* @__PURE__ */ new Set([
    "BEGIN",
    "END",
    "__ENCODING__",
    "__FILE__",
    "__LINE__",
    "alias",
    "and",
    "begin",
    "break",
    "case",
    "class",
    "def",
    "defined",
    "do",
    "else",
    "elsif",
    "end",
    "ensure",
    "false",
    "for",
    "if",
    "in",
    "module",
    "next",
    "nil",
    "not",
    "or",
    "redo",
    "rescue",
    "retry",
    "return",
    "self",
    "super",
    "then",
    "true",
    "undef",
    "unless",
    "until",
    "when",
    "while",
    "yield"
  ]);
  function isBlockCommentStart(index, code) {
    if (index > 0 && code[index - 1] !== "\n") return false;
    return /^=begin(?:\s|$)/.test(code.slice(index));
  }
  function onCommentStart11(curr, _next, index, code) {
    if (curr === "#") return 1;
    if (curr === "=" && isBlockCommentStart(index, code)) return 2;
    return 0;
  }
  function onCommentEnd11(_prev, curr, index, code) {
    if (curr !== "\n") return 0;
    const lineStart = code.lastIndexOf("\n", index - 1) + 1;
    const line = code.slice(lineStart, index);
    if (/^=end(?:\s|$)/.test(line)) return 2;
    return 1;
  }

  // node_modules/sugar-high/lib/lang/rust.js
  var rust_exports = {};
  __export(rust_exports, {
    keywords: () => keywords20,
    onQuote: () => onQuote
  });
  var keywords20 = /* @__PURE__ */ new Set([
    "as",
    "break",
    "const",
    "continue",
    "crate",
    "else",
    "enum",
    "extern",
    "false",
    "fn",
    "for",
    "if",
    "impl",
    "in",
    "let",
    "loop",
    "match",
    "mod",
    "move",
    "mut",
    "pub",
    "ref",
    "return",
    "self",
    "Self",
    "static",
    "struct",
    "super",
    "trait",
    "true",
    "type",
    "unsafe",
    "use",
    "where",
    "while",
    "async",
    "await",
    "dyn",
    "abstract",
    "become",
    "box",
    "do",
    "final",
    "macro",
    "override",
    "priv",
    "typeof",
    "unsized",
    "virtual",
    "yield",
    "try"
  ]);
  function onQuote(curr, i, code) {
    if (curr !== "'" || i + 1 >= code.length) return 1;
    const n1 = code[i + 1];
    if (n1 === "\\") {
      let j = i + 2;
      while (j < code.length) {
        if (code[j] === "\\") {
          j += 2;
          continue;
        }
        if (code[j] === "'") return j - i + 1;
        j++;
      }
      return code.length - i;
    }
    if (n1 === "_") {
      return 2;
    }
    if (/[a-zA-Z]/.test(n1)) {
      let j = i + 2;
      while (j < code.length && /[a-zA-Z0-9_]/.test(code[j])) j++;
      if (j < code.length && code[j] === "'") {
        return j - i + 1;
      }
      return j - i;
    }
    return 1;
  }

  // node_modules/sugar-high/lib/lang/shell.js
  var shell_exports = {};
  __export(shell_exports, {
    keywords: () => keywords21,
    onCommentEnd: () => onCommentEnd3,
    onCommentStart: () => onCommentStart3
  });
  var keywords21 = /* @__PURE__ */ new Set([
    "case",
    "coproc",
    "do",
    "done",
    "elif",
    "else",
    "esac",
    "export",
    "fi",
    "for",
    "function",
    "if",
    "in",
    "local",
    "readonly",
    "return",
    "select",
    "then",
    "time",
    "until",
    "while"
  ]);

  // node_modules/sugar-high/lib/lang/sql.js
  var sql_exports = {};
  __export(sql_exports, {
    caseInsensitive: () => caseInsensitive3,
    keywords: () => keywords22,
    onCommentEnd: () => onCommentEnd12,
    onCommentStart: () => onCommentStart12,
    typeKeywords: () => typeKeywords9
  });
  var keywords22 = /* @__PURE__ */ new Set([
    "add",
    "all",
    "alter",
    "and",
    "as",
    "asc",
    "between",
    "by",
    "case",
    "check",
    "column",
    "constraint",
    "create",
    "cross",
    "database",
    "default",
    "delete",
    "desc",
    "distinct",
    "drop",
    "else",
    "end",
    "exists",
    "foreign",
    "from",
    "full",
    "group",
    "having",
    "in",
    "index",
    "inner",
    "insert",
    "into",
    "is",
    "join",
    "key",
    "left",
    "like",
    "limit",
    "not",
    "null",
    "offset",
    "on",
    "or",
    "order",
    "outer",
    "primary",
    "references",
    "right",
    "select",
    "set",
    "table",
    "then",
    "union",
    "unique",
    "update",
    "values",
    "view",
    "when",
    "where",
    "with"
  ]);
  var typeKeywords9 = /* @__PURE__ */ new Set([
    "bigint",
    "binary",
    "bit",
    "blob",
    "boolean",
    "char",
    "date",
    "datetime",
    "decimal",
    "double",
    "float",
    "int",
    "integer",
    "interval",
    "json",
    "numeric",
    "real",
    "smallint",
    "text",
    "time",
    "timestamp",
    "uuid",
    "varchar"
  ]);
  var caseInsensitive3 = true;
  var onCommentStart12 = (currentChar, nextChar) => {
    const pair = currentChar + nextChar;
    if (pair === "--") return 1;
    if (pair === "/*") return 2;
    return 0;
  };
  var onCommentEnd12 = (prevChar, currChar) => {
    if (currChar === "\n") return 1;
    return prevChar + currChar === "*/" ? 2 : 0;
  };

  // node_modules/sugar-high/lib/lang/swift.js
  var swift_exports = {};
  __export(swift_exports, {
    keywords: () => keywords23,
    onCommentEnd: () => onCommentEnd,
    onCommentStart: () => onCommentStart,
    typeKeywords: () => typeKeywords10
  });
  var keywords23 = /* @__PURE__ */ new Set(["as", "associatedtype", "break", "case", "catch", "class", "continue", "convenience", "default", "defer", "deinit", "didSet", "do", "dynamic", "else", "enum", "extension", "fallthrough", "false", "fileprivate", "final", "for", "func", "get", "guard", "if", "import", "in", "indirect", "infix", "init", "inout", "internal", "is", "lazy", "let", "mutating", "nil", "nonmutating", "open", "operator", "override", "precedencegroup", "private", "protocol", "public", "repeat", "required", "rethrows", "return", "self", "set", "some", "static", "struct", "subscript", "super", "switch", "throw", "throws", "true", "try", "typealias", "unowned", "var", "weak", "where", "while", "willSet"]);
  var typeKeywords10 = /* @__PURE__ */ new Set(["Any", "Bool", "Character", "Double", "Float", "Int", "Never", "String", "UInt", "Void"]);

  // node_modules/sugar-high/lib/lang/toml.js
  var toml_exports = {};
  __export(toml_exports, {
    keywords: () => keywords24,
    onCommentEnd: () => onCommentEnd3,
    onCommentStart: () => onCommentStart3,
    quotedKeys: () => quotedKeys2
  });
  var keywords24 = /* @__PURE__ */ new Set(["false", "true"]);
  var quotedKeys2 = true;

  // node_modules/sugar-high/lib/lang/typescript.js
  var typescript_exports = {};
  __export(typescript_exports, {
    tokenize: () => tokenize8
  });
  var tokenize8 = (code, options) => tokenize3(code, {
    ...options,
    typescript: true
  });

  // node_modules/sugar-high/lib/lang/yaml.js
  var yaml_exports = {};
  __export(yaml_exports, {
    keywords: () => keywords25,
    onCommentEnd: () => onCommentEnd3,
    onCommentStart: () => onCommentStart3,
    quotedKeys: () => quotedKeys3
  });
  var keywords25 = /* @__PURE__ */ new Set([
    "false",
    "False",
    "FALSE",
    "no",
    "No",
    "NO",
    "null",
    "Null",
    "NULL",
    "off",
    "Off",
    "OFF",
    "on",
    "On",
    "ON",
    "true",
    "True",
    "TRUE",
    "yes",
    "Yes",
    "YES"
  ]);
  var quotedKeys3 = true;

  // node_modules/sugar-high/lib/lang/zig.js
  var zig_exports = {};
  __export(zig_exports, {
    keywords: () => keywords26,
    onCommentEnd: () => onCommentEnd13,
    onCommentStart: () => onCommentStart13,
    onLiteral: () => onLiteral3,
    typeKeywords: () => typeKeywords11
  });
  var keywords26 = /* @__PURE__ */ new Set([
    "addrspace",
    "align",
    "allowzero",
    "and",
    "anyframe",
    "anytype",
    "asm",
    "async",
    "await",
    "break",
    "callconv",
    "catch",
    "comptime",
    "const",
    "continue",
    "defer",
    "else",
    "enum",
    "errdefer",
    "error",
    "export",
    "extern",
    "false",
    "fn",
    "for",
    "if",
    "inline",
    "linksection",
    "noalias",
    "noinline",
    "nosuspend",
    "null",
    "opaque",
    "or",
    "orelse",
    "packed",
    "pub",
    "resume",
    "return",
    "struct",
    "suspend",
    "switch",
    "test",
    "threadlocal",
    "true",
    "try",
    "undefined",
    "union",
    "unreachable",
    "usingnamespace",
    "var",
    "volatile",
    "while"
  ]);
  var typeKeywords11 = /* @__PURE__ */ new Set([
    "anyerror",
    "anyopaque",
    "bool",
    "c_char",
    "c_int",
    "c_long",
    "c_longdouble",
    "c_longlong",
    "c_short",
    "c_uint",
    "c_ulong",
    "c_ulonglong",
    "c_ushort",
    "comptime_float",
    "comptime_int",
    "f16",
    "f32",
    "f64",
    "f80",
    "f128",
    "i8",
    "i16",
    "i32",
    "i64",
    "i128",
    "isize",
    "noreturn",
    "type",
    "u8",
    "u16",
    "u32",
    "u64",
    "u128",
    "usize",
    "void"
  ]);
  var onCommentStart13 = (curr, next) => curr + next === "//" ? 1 : 0;
  var onCommentEnd13 = (_prev, curr) => curr === "\n" ? 1 : 0;
  function onLiteral3(curr, index, code) {
    if (curr !== "\\" || code[index + 1] !== "\\") return 0;
    const lineStart = code.lastIndexOf("\n", index - 1) + 1;
    if (code.slice(lineStart, index).trim()) return 0;
    const lineEnd = code.indexOf("\n", index + 2);
    return (lineEnd === -1 ? code.length : lineEnd) - index;
  }

  // node_modules/sugar-high/lib/presets/configs.js
  function nonJavaScript(config) {
    return {
      ...config,
      jsx: false,
      regex: false,
      templateStrings: false
    };
  }
  function createConfigs() {
    return {
      javascript: javascript_exports,
      typescript: typescript_exports,
      css: nonJavaScript(css_exports),
      python: nonJavaScript(python_exports),
      c: nonJavaScript(c_exports),
      go: nonJavaScript(go_exports),
      java: nonJavaScript(java_exports),
      rust: nonJavaScript(rust_exports),
      json: nonJavaScript(json_exports),
      diff: nonJavaScript(diff_exports),
      shell: nonJavaScript(shell_exports),
      cpp: nonJavaScript(cpp_exports),
      csharp: nonJavaScript(csharp_exports),
      sql: nonJavaScript(sql_exports),
      html: html_exports,
      yaml: nonJavaScript(yaml_exports),
      markdown: nonJavaScript(markdown_exports),
      plaintext: nonJavaScript(plaintext_exports),
      ruby: nonJavaScript(ruby_exports),
      kotlin: nonJavaScript(kotlin_exports),
      swift: nonJavaScript(swift_exports),
      php: nonJavaScript(php_exports),
      toml: nonJavaScript(toml_exports),
      powershell: nonJavaScript(powershell_exports),
      dockerfile: nonJavaScript(dockerfile_exports),
      graphql: nonJavaScript(graphql_exports),
      hcl: nonJavaScript(hcl_exports),
      zig: nonJavaScript(zig_exports),
      lua: nonJavaScript(lua_exports)
    };
  }
  var configs = /* @__PURE__ */ createConfigs();
  function configFor(name) {
    return configs[name || "javascript"];
  }

  // node_modules/sugar-high/lib/index.js
  function highlight(code, options) {
    const { lang: lang2, cx, mark, markLine } = options || {};
    const parsed = parse(code, configFor(lang2));
    return render(parsed, { cx, mark, markLine });
  }

  // node_modules/sugar-high/lib/lang.js
  var languages = [
    { id: "javascript", extension: "js", aliases: ["js", "jsx", "node"], config: javascript_exports },
    { id: "typescript", extension: "ts", aliases: ["ts", "tsx"], config: typescript_exports },
    { id: "css", extension: "css", aliases: ["scss"], config: nonJavaScript(css_exports) },
    { id: "python", extension: "py", aliases: ["py", "python3"], config: nonJavaScript(python_exports) },
    { id: "c", extension: "c", aliases: [], config: nonJavaScript(c_exports) },
    { id: "go", extension: "go", aliases: ["golang"], config: nonJavaScript(go_exports) },
    { id: "java", extension: "java", aliases: [], config: nonJavaScript(java_exports) },
    { id: "rust", extension: "rs", aliases: ["rs"], config: nonJavaScript(rust_exports) },
    { id: "json", extension: "json", aliases: ["jsonc"], config: nonJavaScript(json_exports) },
    { id: "diff", extension: "diff", aliases: ["patch"], config: nonJavaScript(diff_exports) },
    { id: "shell", extension: "sh", aliases: ["sh", "bash", "zsh"], config: nonJavaScript(shell_exports) },
    { id: "cpp", extension: "cpp", aliases: ["c++", "cc", "cxx"], config: nonJavaScript(cpp_exports) },
    { id: "csharp", extension: "cs", aliases: ["c#", "cs", "dotnet"], config: nonJavaScript(csharp_exports) },
    { id: "sql", extension: "sql", aliases: [], config: nonJavaScript(sql_exports) },
    { id: "html", extension: "html", aliases: ["htm", "xml"], config: html_exports },
    { id: "yaml", extension: "yaml", aliases: ["yml"], config: nonJavaScript(yaml_exports) },
    { id: "markdown", extension: "md", aliases: ["md", "mdx"], config: nonJavaScript(markdown_exports) },
    { id: "plaintext", extension: "txt", aliases: ["text", "plain"], config: nonJavaScript(plaintext_exports) },
    { id: "ruby", extension: "rb", aliases: [], config: nonJavaScript(ruby_exports) },
    { id: "kotlin", extension: "kt", aliases: ["kts"], config: nonJavaScript(kotlin_exports) },
    { id: "swift", extension: "swift", aliases: [], config: nonJavaScript(swift_exports) },
    { id: "php", extension: "php", aliases: [], config: nonJavaScript(php_exports) },
    { id: "toml", extension: "toml", aliases: [], config: nonJavaScript(toml_exports) },
    { id: "powershell", extension: "ps1", aliases: ["pwsh"], config: nonJavaScript(powershell_exports) },
    { id: "dockerfile", extension: "dockerfile", aliases: ["docker"], config: nonJavaScript(dockerfile_exports) },
    { id: "graphql", extension: "graphql", aliases: ["gql"], config: nonJavaScript(graphql_exports) },
    { id: "hcl", extension: "hcl", aliases: ["terraform", "tf"], config: nonJavaScript(hcl_exports) },
    { id: "zig", extension: "zig", aliases: [], config: nonJavaScript(zig_exports) },
    { id: "lua", extension: "lua", aliases: [], config: nonJavaScript(lua_exports) }
  ];
  function normalizeLanguageName(value) {
    return value.trim().toLowerCase().replace(/^\./, "");
  }
  var languageLookup = /* @__PURE__ */ new Map();
  for (const language of languages) {
    const names = /* @__PURE__ */ new Set([language.id, language.extension, ...language.aliases]);
    for (const name of names) {
      const normalized = normalizeLanguageName(name);
      const existing = languageLookup.get(normalized);
      if (existing && existing !== language) {
        throw new Error(
          `Language name "${normalized}" is shared by "${existing.id}" and "${language.id}"`
        );
      }
      languageLookup.set(normalized, language);
    }
  }
  function findLanguage(name) {
    if (typeof name !== "string") return void 0;
    return languageLookup.get(normalizeLanguageName(name));
  }
  function lang(name) {
    return findLanguage(name)?.id;
  }

  // src/core/toast.ts
  function showToast(message, kind) {
    const toast = document.createElement("div");
    toast.className = `toast ${kind}`;
    toast.innerHTML = `<span>${escapeHtml(message)}</span>`;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3e3);
  }

  // src/core/markdown.ts
  function processRichContent(content) {
    let processedHtml = DOMPurify.sanitize(marked.parse(content));
    const headings = [];
    processedHtml = processedHtml.replace(
      /<h([1-6])[^>]*>(.*?)<\/h[1-6]>/gi,
      (match, level, text) => {
        const id = text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
        headings.push({
          level: parseInt(level, 10),
          text: text.replace(/<[^>]*>/g, ""),
          id
        });
        return `<h${level} id="${id}">${text}</h${level}>`;
      }
    );
    processedHtml = processedHtml.replace(
      /<img([^>]+)src="([^"]+)"([^>]*)>/gi,
      (_match, before, src, after) => {
        const imgId = `img-${Math.random().toString(36).substring(2, 11)}`;
        return `<img${before}src="${src}"${after} class="rich-image" data-img-id="${imgId}" data-lightbox="${escapeAttr(src)}" loading="lazy">`;
      }
    );
    processedHtml = processedHtml.replace(
      /<pre><code class="language-(\w+)">([\s\S]*?)<\/code><\/pre>/gi,
      (_match, lang2, code) => {
        const codeId = `code-${Math.random().toString(36).substring(2, 11)}`;
        const highlightedCode = highlightCode(code, lang2);
        return `
            <div class="code-block">
                <div class="code-header">
                    <span class="code-language">${lang2}</span>
                    <button class="code-copy" data-copy="${codeId}" title="Copy code">
                        <i class="fas fa-copy"></i>
                    </button>
                </div>
                <pre><code id="${codeId}" class="language-${lang2}">${highlightedCode}</code></pre>
            </div>
        `;
      }
    );
    processedHtml = processedHtml.replace(
      /<code>([\s\S]*?)<\/code>/gi,
      '<code class="inline-code">$1</code>'
    );
    processedHtml = processedHtml.replace(
      /<table([^>]*)>([\s\S]*?)<\/table>/gi,
      (_match, attributes2, tableContent) => {
        return `
            <div class="table-wrapper">
                <table${attributes2}>${tableContent}</table>
            </div>
        `;
      }
    );
    processedHtml = processedHtml.replace(
      /<blockquote>([\s\S]*?)<\/blockquote>/gi,
      '<blockquote class="rich-blockquote">$1</blockquote>'
    );
    processedHtml = processExternalContent(processedHtml);
    return {
      html: processedHtml,
      headings
    };
  }
  function highlightCode(code, language) {
    try {
      return highlight(code, { lang: lang(language) });
    } catch (e) {
      console.warn("Syntax highlighting failed:", e);
    }
    return escapeHtml(code);
  }
  function copyCode(codeId) {
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
  function openLightbox(imageSrc) {
    const lightbox = getEl("image-lightbox");
    const lightboxImage = getEl("lightbox-image");
    const caption = document.querySelector(".lightbox-caption");
    lightboxImage.src = imageSrc;
    caption.textContent = imageSrc.split("/").pop() || "Image";
    lightbox.style.display = "flex";
    document.body.style.overflow = "hidden";
  }
  function closeLightbox() {
    const lightbox = getEl("image-lightbox");
    lightbox.style.display = "none";
    document.body.style.overflow = "";
  }
  function processExternalContent(html) {
    html = html.replace(
      /https?:\/\/(?:www\.)?youtube\.com\/watch\?v=([a-zA-Z0-9_-]+)/gi,
      '<div class="video-embed"><iframe src="https://www.youtube.com/embed/$1" frameborder="0" allowfullscreen></iframe></div>'
    );
    html = html.replace(/<a href="([^"]+)"([^>]*)>/gi, (match, href, rest) => {
      const isExternal = href.startsWith("http") && !href.includes(window.location.hostname);
      const externalClass = isExternal ? "external-link" : "";
      const externalIcon = isExternal ? '<i class="fas fa-external-link-alt"></i>' : "";
      return `<a href="${href}"${rest} class="${externalClass}">${externalIcon}`;
    });
    return html;
  }
  function escapeAttr(text) {
    return text.replace(/&/g, "&amp;").replace(/"/g, "&quot;");
  }
  var lightboxOpener = openLightbox;
  function setLightboxOpener(opener) {
    lightboxOpener = opener;
  }
  function wireRichInteractions(root) {
    root.addEventListener("click", (e) => {
      const target = e.target;
      const img = target.closest("img.rich-image[data-lightbox]");
      if (img) {
        lightboxOpener(img.dataset.lightbox || img.src);
        return;
      }
      const copy = target.closest(".code-copy[data-copy]");
      if (copy && copy.dataset.copy) {
        copyCode(copy.dataset.copy);
      }
    });
  }

  // src/entity.ts
  var EntityPage = class {
    constructor() {
      this.entity = null;
      /** stem/name → repo-relative file_path, the wikilink resolver. */
      this.wikilinks = null;
      /** Lightbox navigation state (image order = document order). */
      this.images = [];
      this.currentImageIndex = 0;
      const raw = window.location.pathname.split("/").slice(2).join("/");
      let decoded = raw;
      try {
        decoded = decodeURIComponent(raw);
      } catch {
      }
      this.entityPath = decoded;
      this.bindEvents();
      setLightboxOpener((src) => this.openLightbox(src));
      void this.loadEntity();
    }
    bindEvents() {
      getEl("toggle-rail").addEventListener("click", () => {
        const rail = getEl("entity-rail");
        const hidden = rail.classList.toggle("collapsed");
        getEl("toggle-rail").classList.toggle("active", !hidden);
      });
      getEl("export-content").addEventListener("click", () => this.exportContent());
      getEl("print-content").addEventListener("click", () => {
        window.print();
        showToast("Print dialog opened", "success");
      });
      getEl("toggle-fullscreen").addEventListener("click", () => this.toggleFullscreen());
      document.addEventListener("fullscreenchange", () => this.updateFullscreenButton());
      getEl("close-lightbox").addEventListener("click", () => closeLightbox());
      getEl("image-lightbox").addEventListener("click", (e) => {
        if (e.target.id === "image-lightbox") closeLightbox();
      });
      getEl("lightbox-prev").addEventListener("click", () => this.navigateLightbox(-1));
      getEl("lightbox-next").addEventListener("click", () => this.navigateLightbox(1));
      document.addEventListener("keydown", (e) => {
        const lightbox = getEl("image-lightbox");
        if (lightbox.style.display !== "flex") return;
        if (e.key === "Escape") {
          closeLightbox();
        } else if (e.key === "ArrowLeft") {
          e.preventDefault();
          this.navigateLightbox(-1);
        } else if (e.key === "ArrowRight") {
          e.preventDefault();
          this.navigateLightbox(1);
        }
      });
    }
    // --- load + render ----------------------------------------------------- //
    async loadEntity() {
      try {
        const entity = await fetchJson(
          `/api/entity/${encodeURIComponent(this.entityPath)}`
        );
        this.entity = entity;
        this.displayEntity();
      } catch (error) {
        console.error("Error loading entity:", error);
        this.showError(error instanceof Error ? error.message : "unknown error");
      }
    }
    displayEntity() {
      const entity = this.entity;
      if (!entity) return;
      const fm = entity.frontmatter;
      const isEdition = entity.entity_type === "edition" || fmString(fm, "type") === "newsletter";
      document.title = `${entity.name} \u2014 FinData Knowledge Graph`;
      getEl("page-title").textContent = entity.name;
      getEl("breadcrumb-current").textContent = `${entity.entity_type.replace(/_/g, " ")}: ${entity.name}`;
      this.renderHeader(entity, isEdition);
      this.renderFacts(entity);
      const contentEl = getEl("entity-content");
      if (entity.content) {
        const { html, headings } = processRichContent(entity.content);
        contentEl.innerHTML = html;
        wireRichInteractions(contentEl);
        this.renderToc(headings);
        this.collectImages();
      } else {
        contentEl.innerHTML = '<div class="no-content"><i class="fas fa-file-alt"></i><p>No content available for this entity.</p></div>';
      }
      getEl("loading-state").style.display = "none";
      getEl("main-content").style.display = "grid";
      void this.ensureWikilinkIndex().then((index) => {
        if (index)
          linkifyWikilinks(contentEl, index, (href) => ({
            href: `/entity/${encodeURIComponent(href)}`
          }));
      });
      void this.loadEvents(entity.name);
      void this.loadSemanticPeers(entity.name);
      if (entity.file_path) void this.loadSimilarNotes(entity.file_path);
    }
    /** Title block: chips (companies/sectors) or masthead (editions). */
    renderHeader(entity, isEdition) {
      const mount = getEl("entity-metadata");
      if (isEdition) {
        const series = seriesLabel(entity.file_path);
        const title = readerTitle(entity);
        const bits = editionBits(entity);
        mount.innerHTML = `
                <header class="edition-masthead">
                    <div class="masthead-pub">${escapeHtml(series || "Newsletter")}</div>
                    <h1 class="masthead-title">${escapeHtml(title)}</h1>
                    ${bits.length ? `<div class="masthead-meta">${bits.join(' <span class="dot">\xB7</span> ')}</div>` : ""}
                </header>
            `;
        return;
      }
      const tags = entity.enhanced_tags.length ? `<div class="entity-tags">${entity.enhanced_tags.map((t) => `<span class="entity-tag">${escapeHtml(t)}</span>`).join("")}</div>` : "";
      mount.innerHTML = `
            <header class="entity-head">
                <h1>${escapeHtml(readerTitle(entity))}</h1>
                <div class="fm-chips">${chipSpans(entity)}</div>
                ${tags}
            </header>
        `;
    }
    /** The mono facts block at the top of the rail. */
    renderFacts(entity) {
      const fm = entity.frontmatter;
      const facts = [];
      if (entity.sector_classification) facts.push(["sector", entity.sector_classification]);
      if (entity.market_cap) facts.push(["market cap", entity.market_cap]);
      const normalized = fmString(fm, "normalized_name");
      if (normalized) facts.push(["normalized", normalized]);
      const permalink = fmString(fm, "permalink");
      if (permalink) facts.push(["permalink", permalink]);
      if (entity.file_path) facts.push(["file", entity.file_path]);
      if (!facts.length) {
        getEl("rail-facts").style.display = "none";
        return;
      }
      getEl("facts-grid").innerHTML = facts.map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join("");
    }
    /** TOC into the rail; hidden when the note has fewer than two headings. */
    renderToc(headings) {
      if (headings.length < 2) return;
      getEl("toc-content").innerHTML = headings.map(
        (h) => `<li class="toc-${h.level}"><a href="#${encodeURIComponent(h.id)}">${escapeHtml(h.text)}</a></li>`
      ).join("");
      getEl("toc-block").style.display = "block";
    }
    // --- rail intel ---------------------------------------------------------- //
    /** Vertical events timeline (dated oldest→newest, undated last). */
    async loadEvents(name) {
      try {
        const data = await fetchJson(`/api/events/${encodeURIComponent(name)}`);
        if (!data.events.length) return;
        getEl("events-tl").innerHTML = data.events.map((ev) => {
          const date = this.eventDateLabel(ev.event_date, ev.date_precision);
          const body = [
            ev.counterparty ? escapeHtml(ev.counterparty) : "",
            ev.magnitude ? `<span class="ev-mag">${escapeHtml(ev.magnitude)}</span>` : ""
          ].filter(Boolean).join(" \xB7 ");
          const quote = ev.source_quote ? ` title="${escapeHtml(ev.source_quote).replace(/"/g, "&quot;")}"` : "";
          return `
                    <li class="ev-item"${quote}>
                        <span class="ev-date">${escapeHtml(date)}</span>
                        <span class="ev-type">${escapeHtml(ev.event_type)}</span>
                        <span class="ev-body">${body}</span>
                    </li>
                `;
        }).join("");
        getEl("rail-events").style.display = "block";
      } catch {
      }
    }
    /** Semantic peers as chips (company embeddings only — quiet otherwise). */
    async loadSemanticPeers(name) {
      try {
        const data = await fetchJson(
          `/api/graph/semantic/${encodeURIComponent(name)}?k=8`
        );
        if (!data.neighbors.length) return;
        getEl("peers-chips").innerHTML = data.neighbors.map((n) => {
          const pct = Math.round(n.similarity * 100);
          const href = this.wikilinks?.get(n.name);
          const inner = `${escapeHtml(n.name.replace(/_/g, " "))} <b>${pct}%</b>`;
          return href ? `<a class="peer-chip" href="/entity/${encodeURIComponent(href)}" title="${escapeHtml(n.sector || "")}">${inner}</a>` : `<span class="peer-chip" title="${escapeHtml(n.sector || "")}">${inner}</span>`;
        }).join("");
        getEl("rail-peers").style.display = "block";
      } catch {
      }
    }
    /** Embedding-similar notes as clickable rows. */
    async loadSimilarNotes(filePath) {
      try {
        const data = await fetchJson(
          `/api/graph/similar/${encodeURIComponent(filePath)}?k=6`
        );
        if (!data.neighbors.length) return;
        getEl("similar-list").innerHTML = data.neighbors.map((n) => {
          const pct = Math.round(n.similarity * 100);
          return `
                    <a class="related-row" href="/entity/${encodeURIComponent(n.file_path)}"
                       title="${escapeHtml(n.file_path)}">
                        <span class="related-title">${escapeHtml(n.title.replace(/_/g, " "))}</span>
                        <span class="related-sim"><span class="related-bar"><span
                            class="bar-fill" style="width:${pct}%"></span></span>${pct}%</span>
                    </a>
                `;
        }).join("");
        getEl("rail-similar").style.display = "block";
      } catch {
      }
    }
    // --- wikilinks ------------------------------------------------------------- //
    async ensureWikilinkIndex() {
      if (this.wikilinks) return this.wikilinks;
      try {
        const data = await fetchJson("/api/entities?limit=5000");
        const index = buildWikilinkIndex(data.entities);
        this.wikilinks = index;
        return index;
      } catch {
        return null;
      }
    }
    // --- page chrome -------------------------------------------------------------- //
    collectImages() {
      this.images = Array.from(
        document.querySelectorAll("#entity-content .rich-image")
      ).map((img) => ({ src: img.src, alt: img.alt || "Image" }));
    }
    /** Entry point for the lightbox (delegated via wireRichInteractions). */
    openLightbox(src) {
      const lightbox = getEl("image-lightbox");
      const image = getEl("lightbox-image");
      this.currentImageIndex = Math.max(
        0,
        this.images.findIndex((i) => i.src === src)
      );
      image.src = src;
      document.querySelector(".lightbox-caption").textContent = this.images[this.currentImageIndex]?.alt || "Image";
      lightbox.style.display = "flex";
      getEl("lightbox-prev").style.display = this.images.length > 1 ? "block" : "none";
      getEl("lightbox-next").style.display = this.images.length > 1 ? "block" : "none";
      document.body.style.overflow = "hidden";
    }
    navigateLightbox(direction) {
      if (!this.images.length) return;
      this.currentImageIndex = (this.currentImageIndex + direction + this.images.length) % this.images.length;
      const current = this.images[this.currentImageIndex];
      getEl("lightbox-image").src = current.src;
      document.querySelector(".lightbox-caption").textContent = current.alt;
    }
    toggleFullscreen() {
      if (!document.fullscreenElement) {
        void document.documentElement.requestFullscreen();
      } else {
        void document.exitFullscreen();
      }
    }
    updateFullscreenButton() {
      const btn = getEl("toggle-fullscreen");
      const icon = btn.querySelector("i");
      const text = btn.querySelector("span");
      if (!icon || !text) return;
      if (document.fullscreenElement) {
        icon.className = "fas fa-compress";
        text.textContent = "Exit Fullscreen";
      } else {
        icon.className = "fas fa-expand";
        text.textContent = "Fullscreen";
      }
    }
    exportContent() {
      const entity = this.entity;
      if (!entity) return;
      const facts = getEl("entity-metadata").textContent || "";
      const markdown = `# ${entity.name}

${facts.trim()}

---

${entity.content || ""}`;
      const blob = new Blob([markdown], { type: "text/markdown" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${entity.name.replace(/[^a-z0-9]/gi, "_")}.md`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      showToast("Content exported successfully!", "success");
    }
    showError(message) {
      getEl("loading-state").style.display = "none";
      getEl("main-content").style.display = "none";
      const errorState = getEl("error-state");
      errorState.style.display = "flex";
      const paragraph = errorState.querySelector("p");
      if (paragraph) paragraph.textContent = message;
    }
    eventDateLabel(date, precision) {
      if (!date) return "\u2014";
      if (precision === "year") return date.slice(0, 4);
      if (precision === "month") return date.slice(0, 7);
      return date;
    }
  };
  new EntityPage();
})();
//# sourceMappingURL=entity.bundle.js.map
