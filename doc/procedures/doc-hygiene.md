# Procedure: Doc hygiene — sweep & report (advisory)

**Date:** 2026-09-22
**Scope:** standing **sweep & report** checks for the documentation
breakage classes that `make qa` does **not** cover: dangling paths after
renames, archive-index gaps, stale completed.md headers, entry-format
drift, structural tears in live triage files, and legacy archive files
missing frontmatter. This procedure **detects and prints findings
only** — it does not edit, repoint, or delete anything. Complements
(does not replace) the archival checklist in
`doc/improvements/proposals/README.md` and the Proposal-lifecycle static
check.

Run this after any bulk rename/migration (e.g. `.txt` → `.md`), after a
batch of proposal archival, or at arc end when a sweep turns up rot.

## Breakage classes

| Class | Symptom | Primary check |
|---|---|---|
| Extension / rename rot | Markdown link **or prose path** points at a path that no longer exists (often after a format migration — e.g. `.txt` → `.md`) | Link + path existence (below) |
| Archive index gap | Archived proposal has frontmatter `completed_md` but no `archive/README.md` topic line | Index coverage |
| Index number mismatch | `archive/README.md` cites a different `completed.md` number than the file's frontmatter, cites no number at all, or frontmatter has no `## N` H2 to point at | Frontmatter vs index |
| Header drift | `completed.md` preamble `Generated` / `Total completed` no longer matches the entry census | Header census |
| Entry format drift | Numbered `## N` H2 is bare (no `. title` suffix), or two entries share the same number | Header census |
| Structural tear | A list bullet or record line spliced into the middle of a paragraph (live files: `pending.md`, READMEs) | Manual read of the head + a split-sentence heuristic |
| Stale path | **Live** doc points at `improvements/proposals/<file>` that now lives under `archive/`; **or** an archive file's still-load-bearing pointer (supersession, cross-ref) still aims at `proposals/` instead of the archived sibling | Path inventory (live + archive) |
| Missing frontmatter | Legacy archive `.md` has no YAML `---` block (no `status` / `completed_md` for the lifecycle checker to see) | Frontmatter presence census |
| Orphaned local ref | A `doc/local/` path is referenced from a git-tracked doc but the file no longer exists (common when local notes are deleted without cleaning up back-references) | Path existence (below) |

Historical narrative inside `completed.md` and `archive/**` that *records*
an old path is fine — only **live** operators (`pending.md`, `proposals/
README.md`, `archive/README.md`, design docs, `AGENTS.md`, procedure docs)
and **load-bearing archive cross-refs** (supersession notes, "see also"
pointers a reader would follow) must resolve.

## Commands

### 1. Link & path existence (live surfaces)

```bash
# Broken markdown links in the archive topic index
.venv/bin/python3 - <<'PY'
import re, pathlib
root = pathlib.Path("doc/improvements/archive")
text = (root / "README.md").read_text()
for _t, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", text):
    if not (root / target).exists():
        print(f"BROKEN {target}")
PY

# Live docs pointing at .txt paths that are now .md (post-migration)
rg -n 'archive/[A-Za-z0-9_./-]+\.txt' doc --glob '*.md' \
  | while IFS= read -r line; do
      p=$(printf '%s' "$line" | rg -o 'archive/[A-Za-z0-9_./-]+\.txt' | head -1)
      [ -f "doc/improvements/$p" ] || echo "MISSING $line"
    done

# Live proposals/ paths that no longer exist (exclude completed.md history)
rg -o 'doc/improvements/proposals/[A-Za-z0-9_./-]+\.md' \
  doc/procedures doc/design doc/improvements/pending.md \
  doc/improvements/proposals README.md AGENTS.md 2>/dev/null \
  | sort -u | while IFS= read -r p; do
      [ -f "$p" ] || echo "MISSING $p"
    done

# Archive load-bearing pointers that still aim at proposals/ (A4 class)
rg -n '`(?:\.\./)+proposals/[^`]+`' doc/improvements/archive --glob '*.md' \
  | while IFS= read -r line; do
      f=${line%%:*}
      ref=$(printf '%s' "$line" | rg -o '(?:\.\./)+proposals/[^`]+' | head -1)
      # resolve relative to the file's directory
      dir=$(dirname "$f")
      [ -e "$dir/$ref" ] || echo "STALE_ARCHIVE_REF $line"
    done
```

### 1b. Prose `.txt` paths in live surfaces (post-migration)

```bash
rg -n 'archive/[A-Za-z0-9_./-]+\.txt' \
  doc/improvements/pending.md doc/improvements/proposals \
  doc/procedures doc/design README.md AGENTS.md 2>/dev/null \
  | while IFS= read -r line; do
      p=$(printf '%s' "$line" | rg -o 'archive/[A-Za-z0-9_./-]+\.txt' | head -1)
      # live target is .md after migration; missing .md = finding
      [ -f "doc/improvements/${p%.txt}.md" ] || echo "MISSING_TXT $line"
      # .txt still named in a *live* operator when only .md exists = rot
      [ -f "doc/improvements/$p" ] || echo "TXT_PROSE $line"
    done
```

### 2. Archive index coverage + number agreement

```bash
.venv/bin/python3 - <<'PY'
import re, pathlib
root = pathlib.Path("doc/improvements/archive")
readme = (root / "README.md").read_text()
for p in sorted(root.rglob("*.md")):
    if p.name == "README.md":
        continue
    rel = str(p.relative_to(root))
    fm = re.search(r"^completed_md:\s*['\"]?(\S+?)['\"]?\s*$",
                   p.read_text(), re.M)
    cm = fm.group(1) if fm else None
    if f"]({rel})" not in readme and f"]({rel[:-3]}.txt)" not in readme:
        # .txt link is itself a class-1 defect if the file is .md
        print(f"UNLINKED\t{rel}\tfm={cm or '-'}")
    if cm:
        lines = [ln for ln in readme.splitlines()
                 if p.name in ln and "](" in ln]
        if lines:
            refs = re.findall(r"completed\.md\s+#(\d+[a-z]?)", lines[0])
            # bare trailing #N after a prior completed.md on the same line
            if "completed.md" in lines[0]:
                refs += re.findall(r"#(\d+[a-z]?)",
                                   lines[0].split("completed.md", 1)[1])
            refs = list(dict.fromkeys(refs))
            if refs and cm not in refs:
                print(f"WRONG_NUM\t{rel}\tfm={cm}\treadme={refs}")
            elif not refs:
                print(f"NO_NUM\t{rel}\tfm={cm}")
            # frontmatter number with no ## N H2 (sub-bullet only)
            comp = pathlib.Path("doc/improvements/completed.md").read_text()
            if cm and not re.search(rf"^## {re.escape(cm)}\b", comp, re.M):
                print(f"NO_H2\t{rel}\tfm={cm}")
    if not p.read_text().startswith("---"):
        print(f"NO_FM\t{rel}")
PY
```

### 3. completed.md header census + entry format

```bash
# Numbered H2 entries (dotted `N. title` OR bare `N`)
rg -c '^## [0-9]' doc/improvements/completed.md
# Preamble claims (report if they diverge from the census)
sed -n '1,6p' doc/improvements/completed.md
# Duplicate entry numbers (archival checklist step 2)
rg '^## [0-9]+[a-z]?\.?' doc/improvements/completed.md \
  | rg -o '## [0-9]+[a-z]?' | sort | uniq -d
# Bare H2s — numbered heading with no `. title` suffix
# (number then space/EOL; `## 72. title` does not match)
rg -n '^## [0-9]+[a-z]?(\s|$)' doc/improvements/completed.md \
  || true
```

Header check: `Total completed` must equal the numbered `## N` entry
census (letter-bundles under topic headings do not count). A mismatch is
a finding — this procedure does not rewrite the header.

Entry format: prefer `## N. title`. Bare `## N` and duplicate numbers
are findings; this procedure does not retitle or renumber.

### 4. Structural tear heuristic (pending.md & README heads)

```bash
.venv/bin/python3 - <<'PY'
from pathlib import Path
for path in [Path("doc/improvements/pending.md"),
             Path("doc/improvements/proposals/README.md")]:
    lines = path.read_text().splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("- ") or i == 0:
            continue
        prev = lines[i - 1].strip()
        if prev and not prev.startswith(("-", "#", ">", "|", " ")):
            # previous non-empty line is prose mid-sentence → splice
            if not prev.endswith((".", ":", "!", "?", "`")):
                print(f"INTERLEAVE {path}:{i+1}: {prev[-60:]!r} | {line[:60]!r}")
PY
```

### 5. Frontmatter lifecycle (already gated — run for completeness)

```bash
.venv/bin/python3 helpers/validators/static_checks.py
```

Proposal lifecycle must stay green: every YAML-frontmatter archive file
is `status: executed` + `executed` + `completed_md`; live
`proposals/*.md` (README excluded) stay `status: proposed` with null
executed/completed_md.

### 6. Orphaned local doc references

```bash
# doc/local/ paths in live docs that no longer exist
# (completed.md historical references excepted)
rg -o 'doc/local/[A-Za-z0-9_./-]+\.(?:md|txt)' \
  doc --glob '*.md' \
  | rg -o 'doc/local/[A-Za-z0-9_./-]+\.(?:md|txt)' \
  | sort -u | while IFS= read -r p; do
    echo "$p" | rg -q 'completed\.md' && continue
    [ -f "$p" ] || echo "ORPHAN_LOCAL_REF $p"
  done
```

## Remediation Map (advisory only)

**Not part of this procedure.** Findings above are report-only; apply
the matching row only after the operator explicitly approves a fix.
Nothing here is executed by the sweep.

| Finding class | Typical operator fix (advisory) |
|---|---|
| Extension / rename rot | Repoint to the live path (`.md`, new topic dir). Never leave a `.txt` path in a live operator when only `.md` exists. |
| Archive index gap | Add the topic-section line under `archive/README.md` with the frontmatter `completed.md` number and a working relative link (same patch as the archival when possible). |
| Number mismatch / NO_NUM / NO_H2 | Frontmatter is authoritative when it points at a real entry; fix the README. If the number has no real `## N` H2 (sub-bullet only), either mint the H2 or retarget frontmatter — prefer a real H2 so `rg '^## N\.'` finds it. |
| Header drift | Rewrite `Total completed` to the census; leave historical `Generated` unless rewriting the preamble. |
| Entry format drift (bare H2) | Retitle to `## N. short — subject` without changing the number. |
| Entry format drift (duplicate N) | Do not silently renumber history; prefer minting a unique H2 for the second occurrence or documenting the intentional dual (letter-suffix `N a` / `N b` only with operator approval). |
| Structural tear | Restore the paragraph; move the spliced bullet into the list below (or into the compressed record line it belongs to). Do not reword unrelated content in the same edit. |
| Stale path (live) | Repoint to `archive/<topic>/…`. Leave `completed.md` prose that quotes the old path as history unless it is the *only* pointer a reader would follow. |
| Stale path (archive cross-ref) | Repoint the supersession/see-also to the archived sibling (often same directory: `hgx_first_scaling.md`, not `../../proposals/…`). |
| Missing frontmatter | Optionally backfill `--- title/status/executed/completed_md/area ---` to match the completed.md entry so the lifecycle checker sees the file; not qa-blocking today. |
| Orphaned local ref | Either restore the local note or remove the dangling reference from the live doc. **Rule: when deleting a `doc/local/` note, always sweep for back-references first.** |
| After an approved fix | `make search-fresh APPLY=1`, then plain `make search-fresh` (rc=0). Doc-only edits do not need full `make qa` unless code touched — but md-lint + static checks are cheap and worth a local run. |

## Out of scope

- Content accuracy of archived proposals (dated records).
- Letter-bundle entries (`P1`, `Q1`, …) under topic headings in
  `completed.md` — pre-numbering scheme, left as history.
- Minting new `completed.md` numbers or renumbering history (operator).
- `doc/local/**` (gitignored private notes; index only via doc_search).

## Related

- `doc/improvements/proposals/README.md` — archival checklist (steps 1–6)
- `doc/procedures/search.md` — `search-fresh` / index `--check` contract
- `helpers/validators/static_checks.py` — Proposal lifecycle, frontmatter
