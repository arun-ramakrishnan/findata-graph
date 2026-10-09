#!/usr/bin/env python3
"""Parse `frontend/types/api.ts` and assert live responses against it.

The single source of truth for the TS<->Flask contract. Used by:

- `tests/test_integration_ts_contract.py` — the contract suite (presence +
  declared types per endpoint).
- `helpers/misc/gen_api_guards.py` — emits `frontend/types/guards.ts` from the
  same parsed model, so the runtime guards and the test-time assertions can
  never disagree about what the contract is.

No third-party deps: the parser is a brace/line scanner sized to the
constructs api.ts actually uses (measured inventory, 2026-10-06):

    string | number | boolean | T | null | REF[] | string[] | Record<K, V>
    [A, B] tuples | inline object literals (single- and multi-line)
    string-literal unions ("ok" | "error") | interface refs (incl. `extends`)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

PROJECT_ROOT = Path(__file__).resolve().parents[1]
API_TYPES = PROJECT_ROOT / "frontend" / "types" / "api.ts"

_SKIP_TYPES = {"unknown", "any", "never", "void"}


# --------------------------------------------------------------------------- #
# api.ts parsing
# --------------------------------------------------------------------------- #


def _strip_comments(text: str) -> str:
    """Blank out // line comments and /* */ blocks, preserving offsets.

    Needed because a doc comment can contain an interface-shaped line
    (`*: {by, at}) - a loose ...`), which the field scanner would otherwise
    read as a field.
    """
    out = list(text)
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        else:
            i += 1
    return "".join(out)


def _match_brace(text: str, start: int) -> int:
    """Index just past the `}` matching the `{` AT `start`.

    `start` must be the index of the opening brace itself; the caller has
    not consumed it yet.
    """
    assert text[start] == "{", f"_match_brace expects '{{' at {start}, got {text[start]!r}"
    depth = 1
    i = start + 1
    while i < len(text) and depth > 0:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return i


def _split_top_level(text: str, sep: str) -> list[str]:
    """Split on `sep`, ignoring separators nested in <>, [], {} or quotes."""
    parts: list[str] = []
    depth = 0
    quote = ""
    cur: list[str] = []
    for ch in text:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            cur.append(ch)
            continue
        if ch in "<[{(":
            depth += 1
        elif ch in ">]})":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


@dataclass(frozen=True)
class Field:
    """One declared field: name, optionality, and its raw declared type."""

    name: str
    optional: bool
    type_text: str

    @property
    def subfields(self) -> dict[str, Field]:
        """Fields of an inline object literal type (`{ a: string; b: number }`).

        Empty for every non-object type. Single-line literals are parsed the
        same way as multi-line ones so both forms share one code path.
        """
        t = self.type_text.strip()
        if not (t.startswith("{") and t.endswith("}")):
            return {}
        sub: dict[str, Field] = {}
        for part in _split_top_level(t[1:-1], ";"):
            if ":" not in part:
                continue
            fname, _, ftype = part.partition(":")
            fname = fname.strip()
            optional = fname.endswith("?")
            fname = fname.rstrip("?").strip()
            if re.fullmatch(r"\w+", fname):
                sub[fname] = Field(fname, optional, ftype.strip())
        return sub


def _scan_fields(body: str) -> dict[str, Field]:
    """Depth-aware scan of an interface/inline-object body.

    Only fields declared at brace depth 0 belong to this level: the flat
    regex this replaces also picked up fields nested inside inline object
    types (`GraphStatsResponse.entities: { ... }`), which is why that test
    asserted key sets by hand. Type text is captured too, so assertions can
    check kinds, not just presence.
    """
    fields: dict[str, Field] = {}
    i = 0
    n = len(body)
    while i < n:
        m = re.compile(r"[ \t]*([A-Za-z_]\w*)(\?)?[ \t]*:[ \t]*").match(body, i)
        if not m:
            i += 1
            continue
        fname = m.group(1)
        optional = bool(m.group(2))
        j = m.end()
        if body.startswith("{", j):
            end = _match_brace(body, j)
            type_text = body[j:end]
            # An inline object can be array-suffixed: `{ a: string }[]`.
            if body.startswith("[]", end):
                type_text += "[]"
                end += 2
            i = end
        else:
            stop = j
            depth = 0
            while stop < n:
                ch = body[stop]
                if ch in "<[(":
                    depth += 1
                elif ch in ">])":
                    depth -= 1
                elif ch == ";" and depth <= 0:
                    break
                stop += 1
            type_text = body[j:stop]
            i = stop
        fields.setdefault(fname, Field(fname, optional, type_text.strip()))
        if i < n and body[i] == ";":
            i += 1
    return fields


def parse_interfaces(path: Path | None = None) -> tuple[dict[str, dict[str, Field]], list[str]]:
    """Parse api.ts into {InterfaceName: {field: Field}}.

    Handles `extends` (inlines parent fields) and comments. Each field keeps
    its declared type text, and only depth-0 fields are collected.
    """
    text = _strip_comments((path or API_TYPES).read_text(encoding="utf-8"))
    ifaces: dict[str, dict[str, Field]] = {}
    order: list[str] = []

    # Find each interface block; group 3 is the opening brace itself.
    for m in re.finditer(r"export interface (\w+)(?: extends ([^{]+?))?\s*(\{)", text):
        name = m.group(1)
        base = m.group(2).strip() if m.group(2) else None
        end = _match_brace(text, m.start(3))
        fields = _scan_fields(text[m.end() : end - 1])
        if base:
            for bn in (b.strip() for b in base.split(",")):
                if bn in ifaces:
                    # inline parent fields (parent optionality trumps if child
                    # redeclares as required for simplicity)
                    for f, fld in ifaces[bn].items():
                        fields.setdefault(f, fld)
        ifaces[name] = fields
        order.append(name)
    return ifaces, order


INTERFACES, INTERFACE_ORDER = parse_interfaces()


def required_keys(iface: str) -> list[str]:
    return [f for f, fld in INTERFACES.get(iface, {}).items() if not fld.optional]


def all_keys(iface: str) -> list[str]:
    return list(INTERFACES.get(iface, {}).keys())


# --------------------------------------------------------------------------- #
# Assertions — kinds, not just presence
# --------------------------------------------------------------------------- #


def _kind(value: object) -> str:
    """JSON kind name for error messages. bool is checked before int."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _fail(value: object, expected: str, path: str) -> NoReturn:
    got = _kind(value)
    if isinstance(value, str) and len(value) <= 40:
        got = f"string {value!r}"
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        got = f"number {value}"
    raise AssertionError(f"{path}: expected {expected}, got {got}")


def _split_union(t: str) -> list[str] | None:
    """Split a top-level `A | B` union, or None when `t` is not one.

    Separators inside <>, [] or {} belong to Record/tuple/inline-object
    syntax, so `Record<string, "a" | "b">` stays a single member.
    """
    depth = 0
    quote = ""
    for ch in t:
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "<[{(":
            depth += 1
        elif ch in ">]})":
            depth -= 1
        elif ch == "|" and depth == 0:
            return [m.strip() for m in _split_top_level(t, "|")]
    return None


def assert_type(value: object, type_text: str, path: str, seen: frozenset[str]) -> None:
    """Assert `value` matches the declared TS `type_text` at `path`.

    Recurses through interface references, arrays, inline object literals,
    Record values and tuples; `seen` guards against a self-referential
    interface. Unknown/any accept anything, by design.
    """
    t = " ".join(type_text.split()).strip().rstrip(";").strip()
    if not t or t in _SKIP_TYPES:
        return

    union = _split_union(t)
    if union is not None:
        _assert_union(value, union, path, seen)
        return
    _assert_singular(value, t, path, seen)


def _assert_union(value: object, union: list[str], path: str, seen: frozenset[str]) -> None:
    """Accept on the first matching member; otherwise raise the last failure."""
    if any(m in _SKIP_TYPES for m in union):
        return
    if "null" in union:
        if value is None:
            return
        union = [m for m in union if m != "null"]
    elif value is None:
        _fail(value, "|".join(union), path)
    _accept_any_member(value, union, path, seen)


def _accept_any_member(value: object, union: list[str], path: str, seen: frozenset[str]) -> None:
    # Keep the LAST member failure: it names the exact field and kind
    # ("entities[1].name: expected string"), which is the actionable
    # message — a generic "expected A | B" hides the culprit.
    last: AssertionError | None = None
    for m in union:
        try:
            assert_type(value, m, path, seen)
        except AssertionError as exc:
            last = exc
            continue
        return
    if last is not None:
        raise last
    _fail(value, "|".join(union), path)


def _assert_singular(value: object, t: str, path: str, seen: frozenset[str]) -> None:
    """Dispatch one non-union type text to its kind-specific asserter."""
    array_inner = t[:-2].strip() if t.endswith("[]") else _generic_inner(t, "Array")
    if array_inner is not None:
        _assert_array(value, t, path, seen, array_inner)
        return
    record_inner = _generic_inner(t, "Record")
    if record_inner is not None:
        _assert_record(value, t, record_inner, path, seen)
        return
    if t.startswith("{") and t.endswith("}"):
        _assert_inline_object(value, t, path, seen)
        return
    if t.startswith("[") and t.endswith("]"):
        _assert_tuple(value, t, path, seen)
        return
    _assert_named(value, t, path, seen)


def _generic_inner(t: str, name: str) -> str | None:
    """Inner text of `name<...>` when `t` is exactly that generic form."""
    if t.startswith(f"{name}<") and t.endswith(">"):
        return t[len(name) + 1 : -1]
    return None


def _assert_array(value: object, t: str, path: str, seen: frozenset[str], inner: str) -> None:
    if not isinstance(value, list):
        _fail(value, t, path)
    for idx, item in enumerate(value):
        assert_type(item, inner, f"{path}[{idx}]", seen)


def _assert_record(value: object, t: str, inner_text: str, path: str, seen: frozenset[str]) -> None:
    if not isinstance(value, dict):
        _fail(value, t, path)
    inner = _split_top_level(inner_text, ",")[-1].strip()
    for k, v in value.items():
        assert_type(v, inner, f"{path}.{k}", seen)


def _assert_inline_object(value: object, t: str, path: str, seen: frozenset[str]) -> None:
    sub = Field(name="", optional=False, type_text=t).subfields
    if not isinstance(value, dict):
        _fail(value, "object", path)
    _assert_fields(value, sub, path, seen)


def _assert_tuple(value: object, t: str, path: str, seen: frozenset[str]) -> None:
    inner = [m.strip() for m in _split_top_level(t[1:-1], ",")]
    if not isinstance(value, list) or len(value) != len(inner):
        _fail(value, f"tuple[{len(inner)}]", path)
    for idx, (item, spec) in enumerate(zip(value, inner)):
        assert_type(item, spec, f"{path}[{idx}]", seen)


def _assert_named(value: object, t: str, path: str, seen: frozenset[str]) -> None:
    """Literal-union, primitive or named-interface form."""
    if t.startswith('"') and '"' in t[1:]:
        _assert_literal(value, t, path)
    elif t in ("string", "number", "boolean"):
        if not _scalar_ok(value, t):
            _fail(value, t, path)
    else:
        _assert_interface(value, t, path, seen)


def _assert_literal(value: object, t: str, path: str) -> None:
    allowed = {m.strip().strip('"') for m in t.split("|")}
    if not (isinstance(value, str) and value in allowed):
        _fail(value, f"one of {sorted(allowed)}", path)


def _scalar_ok(value: object, t: str) -> bool:
    if t == "string":
        return isinstance(value, str)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, bool)


def _assert_interface(value: object, t: str, path: str, seen: frozenset[str]) -> None:
    fields = INTERFACES.get(t)
    if fields is None:
        return  # not an interface we know (e.g. a type alias) — presence still applies
    if t in seen:
        return  # cycle guard
    if not isinstance(value, dict):
        _fail(value, f"{t} (object)", path)
    _assert_fields(value, fields, path, seen | {t})


def _assert_fields(
    data: dict, fields: dict[str, Field], path: str, seen: frozenset[str] = frozenset()
) -> None:
    for fname, fld in fields.items():
        if fname not in data:
            continue  # presence is assert_keys' job
        _assert_type_path(data[fname], fld.type_text, f"{path}.{fname}" if path else fname, seen)


def _assert_type_path(value: object, type_text: str, path: str, seen: frozenset[str]) -> None:
    assert_type(value, type_text, path, seen)


def assert_types(data: object, iface: str, path: str = "") -> None:
    """Assert every present field of `data` matches `iface`'s declared types."""
    fields = INTERFACES.get(iface)
    if fields is None or not isinstance(data, dict):
        return
    _assert_fields(data, fields, path)


def assert_keys(data: dict, iface: str, required_only: bool = False) -> None:
    """Assert every declared field in the api.ts interface is present."""
    keys = required_keys(iface) if required_only else all_keys(iface)
    missing = [k for k in keys if k not in data]
    assert not missing, (
        f"api.ts interface {iface} declares {missing} key(s) missing from "
        f"response {sorted(data.keys())}"
    )


def assert_contract(data: dict, iface: str, required_only: bool = True) -> None:
    """Presence AND declared types — the full contract for one payload.

    `required_only=True` by default: an optional field (`foo?: T`) is a
    *conditional* promise — api.ts documents "present when …; absent on
    older endpoints" — so requiring it would encode the opposite of the
    contract. Optional fields are still type-checked whenever present. Pass
    `required_only=False` for endpoints whose every declared key is
    unconditional.
    """
    assert_keys(data, iface, required_only)
    assert_types(data, iface)


# --------------------------------------------------------------------------- #
# Route inventory — which routes the TS client calls
# --------------------------------------------------------------------------- #


def app_routes(app_py: Path | None = None) -> list[str]:
    """Flask route templates declared in app.py, longest static prefix first."""
    text = (app_py or (PROJECT_ROOT / "app.py")).read_text(encoding="utf-8")
    routes = re.findall(r'@app\.route\(\s*"(/api/[^"]+)"', text)
    return sorted(set(routes), key=lambda r: (-len(r.split("<")[0]), r))


def consumed_routes(src_root: Path | None = None) -> set[str]:
    """Route templates the TS client actually calls.

    Scans fetchJson/postJson literals under frontend/src, strips template
    interpolation, then matches each against the app.py route templates so
    `/api/graph/metrics/louvain_community` normalises to
    `/api/graph/metrics/<metric>`.
    """
    templates = app_routes()
    routes: set[str] = set()
    for path in (src_root or (PROJECT_ROOT / "frontend" / "src")).rglob("*.ts"):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r'["`]([^"`]*?/api/[^"`?]+)', text):
            literal = m.group(1)
            if not literal.startswith("/api/"):
                continue
            # Keep the trailing slash of `/api/entity/${...}` — the template's
            # static prefix ends with `/` — and only drop interpolation.
            probe = literal.split("${")[0]
            for tmpl in templates:
                if probe.startswith(tmpl.split("<")[0]):
                    routes.add(tmpl)
                    break
    return routes
