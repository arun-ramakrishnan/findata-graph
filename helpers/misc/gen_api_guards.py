#!/usr/bin/env python3
"""Emit the valibot runtime-validation stack for the /api/* payloads.

S3 of ts_contract_hardening (valibot arm, accepted 2026-10-06): `fetchJson`
ended every call with `response.json() as T`, an assertion the compiler keeps
and the browser throws away. The chosen fix validates every success payload
against its declared interface with valibot at the call site
(`fetchJson<T>(url, isT)`), so each bundle tree-shakes to exactly the guards
its views consume and a drifted payload fails loudly (ShapeError) instead of
rendering garbage.

Two files are generated from `frontend/types/api.ts` through the same parser
the contract suite uses (`tests/api_contract.py`), one source, three
consumers (browser checks, Python assertions, this emitter):

  frontend/types/schemas_valibot.ts  one valibot schema per interface
  frontend/types/guards.ts           `is<Interface>` wrappers (safeParse →
                                     null | "path: message") with the same
                                     isX surface the battery tests

    python3 helpers/misc/gen_api_guards.py            # write both files
    python3 helpers/misc/gen_api_guards.py --check    # verify both are current

`--check` runs in `make frontend-check` beside `bun test` (the behavioural
battery, frontend/tests/guards.test.ts), so editing api.ts without
regenerating — or a generator change that alters semantics — fails loudly.

Any declared type-text the translator cannot map raises: unknown constructs
never silently pass as loose schemas. `export type` aliases resolve via
TYPE_ALIASES below.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.api_contract import (  # noqa: E402
    INTERFACES,
    Field,
    _split_top_level,
    _split_union,
)

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
SCHEMAS_PATH = FRONTEND_DIR / "types" / "schemas_valibot.ts"
GUARDS_PATH = FRONTEND_DIR / "types" / "guards.ts"

# `export type` aliases in api.ts the interface parser doesn't collect.
# NoteFrontmatter is near-vacuous (Record<string, unknown>); the two bundle
# aliases are real discriminated unions of their member-interface schemas.
TYPE_ALIASES = {
    "NoteFrontmatter": "Record<string, unknown>",
    "NeighborsBundle": "CompanyNeighbors | SectorNeighbors",
    "GraphEgoBundle": "CompanyNeighbors | SectorNeighbors",
}

LIT = re.compile(r'^"[^"]*"$')
TUPLE = re.compile(r"^\[(.*)\]$")

SCHEMAS_HEADER = """// GENERATED FILE — do not edit by hand.
// Source: frontend/types/api.ts  (via tests/api_contract.py)
// Regenerate: python3 helpers/misc/gen_api_guards.py
// Verify:     python3 helpers/misc/gen_api_guards.py --check
//
// One valibot schema per api.ts interface, dependency order. `guards.ts`
// wraps these into the is<Interface> functions fetchJson call sites pass;
// bundles tree-shake to exactly the schemas their views consume.
// Unknown type constructs raise in the generator — never emitted as a
// loose schema.
import * as v from "valibot";
"""

GUARDS_HEADER = """// GENERATED FILE — do not edit by hand.
// Source: frontend/types/api.ts  (via tests/api_contract.py)
// Regenerate: python3 helpers/misc/gen_api_guards.py
// Verify:     python3 helpers/misc/gen_api_guards.py --check
//
// Runtime shape guards for the /api/* payloads. Each `is<Interface>(value)`
// runs the valibot schema for its api.ts interface and returns null on
// match, or a path ("entities.3.weight") naming the first mismatch —
// required keys are presence-checked, optional fields pass when absent.
// fetchJson turns a mismatch into a ShapeError so a drifted response fails
// loudly instead of rendering garbage. Behaviour is regression-tested in
// frontend/tests/guards.test.ts (bun test, via `make frontend-check`).
import * as v from "valibot";

import * as S from "./schemas_valibot";

export type Guard = (value: unknown) => string | null;
"""


def schema_for(t: str) -> str:
    """Translate declared type text to a valibot schema expression."""
    t = t.strip()
    atom = _schema_atom(t)
    if atom is not None:
        return atom
    members = _split_union(t)
    if members is not None:
        return _schema_union(members, t)
    tup = _schema_tuple(t)
    if tup is not None:
        return tup
    if t.startswith("{") and t.endswith("}"):
        return _schema_inline_object(t)
    if t in TYPE_ALIASES:
        return schema_for(TYPE_ALIASES[t])
    if t in INTERFACES:
        return f"{t}Schema"
    raise ValueError(f"UNTRANSLATED type text: {t!r}")


def _schema_atom(t: str) -> str | None:
    """Primitive, array, Record or single-literal form; None when `t` is none."""
    if t in ("string", "number", "boolean", "unknown"):
        return f"v.{t}()"
    if t.endswith("[]"):
        return f"v.array({schema_for(t[:-2])})"
    if t.startswith("Record<") and t.endswith(">"):
        key, _, val = t[len("Record<") : -1].partition(",")
        if key.strip() != "string":
            raise ValueError(f"non-string Record key: {t}")
        return f"v.record(v.string(), {schema_for(val.strip())})"
    if LIT.match(t):
        return f"v.literal({t})"
    return None


def _schema_union(members: list[str], t: str) -> str:
    if all(m == "null" for m in members):
        raise ValueError(f"pure-null union: {t}")
    if "null" in members:
        return _schema_nullable_union(members)
    if all(LIT.match(m) for m in members):
        return f"v.picklist([{', '.join(members)}])"
    return f"v.union([{', '.join(schema_for(m) for m in members)}])"


def _schema_nullable_union(members: list[str]) -> str:
    rest = [m for m in members if m != "null"]
    if len(rest) == 1:
        return f"v.nullable({schema_for(rest[0])})"
    return f"v.union([{', '.join(schema_for(r) for r in rest)}, v.null()])"


def _schema_tuple(t: str) -> str | None:
    m = TUPLE.match(t)
    if m and not t.startswith('"'):
        return f"v.tuple([{', '.join(schema_for(x) for x in m.group(1).split(','))}])"
    return None


def _schema_inline_object(t: str) -> str:
    entries = []
    for name, fld in inline_fields(t).items():
        s = schema_for(fld.type_text)
        if fld.optional:
            s = f"v.optional({s})"
        entries.append(f"{name}: {s}")
    return f"v.object({{{', '.join(entries)}}})"


def inline_fields(t: str) -> dict[str, Field]:
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


def dep_order() -> list[str]:
    """Interfaces ordered so every referenced interface is emitted first."""
    order: list[str] = []
    done: set[str] = set()

    def refs_of(name: str) -> set[str]:
        out = set()
        for fld in INTERFACES[name].values():
            for m in re.finditer(r"\b([A-Z]\w+)\b", fld.type_text):
                if m.group(1) in INTERFACES and m.group(1) != name:
                    out.add(m.group(1))
        return out

    pending = list(INTERFACES)
    while pending:
        progressed = False
        for name in list(pending):
            if refs_of(name) <= done:
                order.append(name)
                done.add(name)
                pending.remove(name)
                progressed = True
        if not progressed:
            raise RuntimeError(f"unresolvable interface references: {pending}")
    return order


def emit_schemas() -> str:
    out: list[str] = [SCHEMAS_HEADER, ""]
    for name in dep_order():
        entries = []
        for f, fld in INTERFACES[name].items():
            s = schema_for(fld.type_text)
            if fld.optional:
                s = f"v.optional({s})"
            entries.append(f"{f}: {s}")
        out.append(f"export const {name}Schema = v.object({{{', '.join(entries)}}});")
        out.append("")
    return "\n".join(out)


def emit_guards() -> str:
    out: list[str] = [GUARDS_HEADER, ""]
    for name in dep_order():
        out.append(
            f"""export const is{name}: Guard = (value) => {{
    const result = v.safeParse(S.{name}Schema, value);
    if (result.success) return null;
    const issue = result.issues[0];
    const where = v.getDotPath(issue) ?? "(root)";
    return `${{where}}: ${{issue.message}}`;
}};
"""
        )
    return "\n".join(out)


PRETTIER_CONFIG = FRONTEND_DIR / ".prettierrc"


def _prettier(path: Path) -> None:
    """Format with frontend/.prettierrc so `prettier --check src types` stays green.

    The config is passed explicitly: `--check` formats a temp file outside the
    frontend tree, where prettier would otherwise apply its own defaults
    (2-space) and the comparison would never match the committed output.
    """
    try:
        subprocess.run(  # noqa: S603
            [  # noqa: S607
                "bun",
                "x",
                "prettier",
                "--config",
                str(PRETTIER_CONFIG),
                "--write",
                str(path),
            ],
            check=True,
            capture_output=True,
            cwd=str(FRONTEND_DIR),
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"warn: prettier did not run ({exc}); generated file may need formatting")


def _formatted(text: str) -> str:
    """The emitted text as prettier will leave it.

    `--check` compares against this, not the raw emitter output: prettier
    reflows AND adds semicolons (default `semi: true`), so a byte or
    whitespace comparison against unformatted output always says "stale".
    """
    with tempfile.NamedTemporaryFile(
        "w", suffix=".ts", dir=Path(tempfile.gettempdir()), delete=False, encoding="utf-8"
    ) as fh:
        fh.write(text)
        tmp = Path(fh.name)
    try:
        _prettier(tmp)
        return tmp.read_text(encoding="utf-8")
    finally:
        tmp.unlink(missing_ok=True)


def _normalized(text: str) -> str:
    """Compare ignoring prettier's line wrapping and whitespace."""
    return " ".join(text.split())


OUTPUTS = ((SCHEMAS_PATH, emit_schemas), (GUARDS_PATH, emit_guards))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="verify both outputs are current")
    args = ap.parse_args()
    if args.check:
        ok = True
        for path, emit in OUTPUTS:
            if not path.exists():
                print(f"FAIL: {path} does not exist — run gen_api_guards.py")
                ok = False
                continue
            if _normalized(path.read_text(encoding="utf-8")) != _normalized(_formatted(emit())):
                print(f"FAIL: {path.name} is stale — run: python3 helpers/misc/gen_api_guards.py")
                ok = False
        if ok:
            print(f"OK: schemas_valibot.ts + guards.ts are current ({len(INTERFACES)} guards)")
            return 0
        return 1
    for path, emit in OUTPUTS:
        path.write_text(emit(), encoding="utf-8")
        _prettier(path)
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
