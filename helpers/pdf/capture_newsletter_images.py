#!/usr/bin/env python3
"""Capture remote OCR-crop images embedded in a newsletter markdown into a local
`images/` directory, following the project's established convention:

    {newsletter_slug}_p{page}_img{N}.jpeg

What it does
------------
1. Parse every `<img src='URL'>` in document order.
2. Derive a stable filename per image:
     - slug  = newsletter filename stem, spaces -> underscores
     - page  = 1-based "OCR crop group": a new page begins whenever the crop
               counter in the URL returns to 1 (contiguous 1..K). NOTE: these
               are OCR crop-group pages and may differ from physical PDF pages.
     - imgN  = global 1-based image counter (the canonical link key).
3. Download (concurrent, retry) into `<newsletter_dir>/images/`.
4. Verify each file is a non-empty JPEG; re-fetch any failure.
5. Write a manifest JSON next to the images dir for auditability.
6. Skip files already present and valid (idempotent / resumable).

Usage
-----
    python3 helpers/pdf/capture_newsletter_images.py <path/to/newsletter.md> [--workers N] [--rewrite]

`--rewrite` additionally rewrites the newsletter .md IN PLACE, replacing each
remote `<div ...><img src='https://...ufileos...'></div>` block with a local
Obsidian embed `![[images/<slug>_p{page}_img{N}.jpeg]]` at the same position.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

# slugify is shared with pdf_conv_md.py (see helpers/pdf/common.py).
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from helpers.pdf.common import slugify  # noqa: E402

UA = "Mozilla/5.0 (compatible; findata-image-capture/1.0)"
# Match a full remote image block. tolerate single or double quotes and spacing.
IMG_BLOCK_RE = re.compile(
    r"<div[^>]*>\s*<img[^>]*?src=['\"]([^'\"]+)['\"][^>]*?>\s*</div>",
    re.IGNORECASE | re.DOTALL,
)
CROP_RE = re.compile(r"crop_(\d+)_(\d+)")


def parse_images(md_path: Path):
    """Return list of dicts: {idx(0-based), line, url, crop, ts} in document order."""
    text = md_path.read_text(encoding="utf-8", errors="replace")
    out = []
    for m in IMG_BLOCK_RE.finditer(text):
        url = m.group(1)
        cm = CROP_RE.search(url)
        crop = int(cm.group(1)) if cm else 0
        ts = int(cm.group(2)) if cm else 0
        line = text.count("\n", 0, m.start()) + 1
        out.append({"idx": len(out), "line": line, "url": url, "crop": crop, "ts": ts})
    return out, text


def assign_pages(images):
    """Page by crop-reset: new page whenever crop==1 (incl. consecutive ones)."""
    page = 0
    for im in images:
        if im["crop"] == 1 or page == 0:
            page += 1
        im["page"] = page
    return images


def is_valid_jpeg(p: Path) -> bool:
    if not p.exists() or p.stat().st_size < 16:
        return False
    with p.open("rb") as fh:
        # Read 4 bytes so BOTH magic checks work: JPEG SOI (FFD8FF, needs 2+)
        # and PNG signature (\x89PNG, needs 4). A 3-byte read left the PNG
        # branch dead — head[:4] on a 3-byte buffer can't equal a 4-byte sig.
        head = fh.read(4)
    # JPEG SOI marker FFD8FF, or PNG (some crops are png), accept either magic.
    return head[:2] == b"\xff\xd8" or head[:4] == b"\x89PNG"


def fetch(url: str, dest: Path, retries: int = 3):
    # SEC-6 (private security review, 2026-08): URLs come
    # from regex-extracted OCR markdown, so the scheme MUST be allowlisted
    # before urlopen — otherwise a poisoned OCR file could point us at
    # file:// or internal hosts. https only (the newsletter CDN serves https).
    if not url.startswith("https://"):
        return f"skipped non-https scheme: {urlsplit(url).scheme or '(relative)'}"
    last = None
    for _ in range(retries):
        try:
            req = Request(url, headers={"User-Agent": UA})  # noqa: S310  # https-only scheme enforced above
            with urlopen(req, timeout=30) as r:  # noqa: S310  # https-only scheme enforced above
                data = r.read()
            if data and (data[:2] == b"\xff\xd8" or data[:4] == b"\x89PNG"):
                dest.write_bytes(data)
                return True
            last = f"bad magic ({len(data)} bytes)"
        except (HTTPError, URLError, TimeoutError, OSError) as e:
            last = str(e)
    return last or "unknown"


def main():  # noqa: C901
    ap = argparse.ArgumentParser()
    ap.add_argument("md_path")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--rewrite", action="store_true")
    args = ap.parse_args()

    md_path = Path(args.md_path)
    if not md_path.exists():
        sys.exit(f"not found: {md_path}")

    slug = slugify(md_path.stem)
    images_dir = md_path.parent / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    images, text = parse_images(md_path)
    if not images:
        print(f"[{md_path.name}] no remote <img> blocks found; nothing to do.")
        return
    assign_pages(images)

    for im in images:
        im["file"] = f"{slug}_p{im['page']}_img{im['idx'] + 1}.jpeg"
        im["path"] = images_dir / im["file"]

    todo, skipped = [], 0
    for im in images:
        if is_valid_jpeg(im["path"]):
            im["ok"] = True
            im["bytes"] = im["path"].stat().st_size
            skipped += 1
        else:
            todo.append(im)

    print(
        f"[{md_path.name}] slug={slug} images={len(images)} "
        f"already_ok={skipped} to_fetch={len(todo)} pages={images[-1]['page']}"
    )

    failures = []
    if todo:

        def work(im):
            res = fetch(im["url"], im["path"])
            ok = res is True
            im["ok"] = ok
            im["bytes"] = im["path"].stat().st_size if ok else 0
            if not ok:
                im["error"] = res
            return im["idx"], ok, res

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(work, im) for im in todo]
            done = 0
            for fu in as_completed(futs):
                idx, ok, res = fu.result()
                done += 1
                im = images[idx]
                tag = "OK " if ok else "FAIL"
                print(
                    f"  [{done}/{len(todo)}] {tag} img{idx + 1} "
                    f"p{im['page']} {im['file']}" + ("" if ok else f"  ({res})")
                )
                if not ok:
                    failures.append(im)

    ok_total = sum(1 for im in images if im.get("ok"))
    print(f"[{md_path.name}] downloaded ok={ok_total}/{len(images)} failures={len(failures)}")

    manifest = {
        "newsletter": md_path.name,
        "slug": slug,
        "images_dir": str(images_dir.relative_to(md_path.parent.parent))
        if images_dir.parent.parent.exists()
        else str(images_dir),
        "count": len(images),
        "ok": ok_total,
        "failures": len(failures),
        "entries": [
            {
                "imgN": im["idx"] + 1,
                "page": im["page"],
                "line": im["line"],
                "file": im["file"],
                "url": im["url"],
                "bytes": im.get("bytes", 0),
                "ok": im.get("ok", False),
                "error": im.get("error"),
            }
            for im in images
        ],
    }
    man_path = md_path.parent / f"{slug}_image_manifest.json"
    man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[{md_path.name}] manifest -> {man_path}")

    if args.rewrite and not failures:

        def rewrite(t):
            out, pos, i = [], 0, 0
            for m in IMG_BLOCK_RE.finditer(t):
                out.append(t[pos : m.start()])
                out.append(f"![[images/{images[i]['file']}]]")
                pos = m.end()
                i += 1
            out.append(t[pos:])
            return "".join(out)

        new_text = rewrite(text)
        md_path.write_text(new_text, encoding="utf-8")
        remaining = new_text.count("ufileos")
        print(f"[{md_path.name}] rewrote in place; remaining remote URLs = {remaining}")

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
