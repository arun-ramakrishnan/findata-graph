#!/usr/bin/env python3
"""Convert a PDF to markdown using the Paddle AI Studio document-parsing API.

Submits the source PDF to the PP-StructureV3 job endpoint, polls until the job
finishes, downloads the JSONL result, and writes:

    <output_dir>/<stem>.md            combined markdown in the The_Chatter
                                      style, with images embedded as Obsidian
                                      wikilinks (![[images/<name>]])
    <output_dir>/<stem>.json          raw per-page structured result (the shape
                                      used by the Reports/ eval JSONs)
    <output_dir>/images/              embedded images downloaded as
                                      <stem>_p<page>_img<N>.<ext>, matching the
                                      findata/The_Chatter/images convention

Usage
-----
    python3 helpers/pdf/pdf_conv_md.py <source.pdf> <output_dir> [options]

Options
-------
    --model PP-StructureV3   model name (default: PP-StructureV3)
    --token TOKEN            API token (required unless env PADDLE_API_KEY set)
    --timeout SECONDS        max wall-clock time to wait for the job
                             (default: 600)
    --no-images              skip downloading embedded images (leaves the
                             absolute <img src=...> URLs in the markdown)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
DEFAULT_MODEL = "PP-StructureV3"
POLL_INTERVAL = 5

# An <img> wrapped in the centered <div> the API emits around each image.
# Group 1 captures the relative imgs/... src.
IMG_DIV_RE = re.compile(
    r'<div style="text-align: center;"><img src="(imgs/[^"]+)"[^>]*/></div>'
)
# A bare <img> whose src is a relative imgs/ path (fallback if not div-wrapped).
IMG_TAG_RE = re.compile(r'<img src="(imgs/[^"]+)"[^>]*/?>')
# Leftover empty wrapper after the <img> was replaced.
EMPTY_DIV_RE = re.compile(r'<div style="text-align: center;">\s*</div>')
# A relative imgs/... src inside an <img> tag (for resolve_markdown).
IMGSRC_RE = re.compile(r'src="(imgs/[^"]+)"')

# image/jpeg -> .jpeg (matches the The_Chatter/images convention).
CONTENT_TYPE_EXT = {
    "image/jpeg": ".jpeg",
    "image/jpg": ".jpeg",
    "image/png": ".png",
    "image/webp": ".webp",
}
DEFAULT_EXT = ".jpeg"


def slugify(stem: str) -> str:
    s = re.sub(r"\s+", "_", stem.strip())
    s = re.sub(r"__+", "_", s).strip("_")
    return s


def submit_job(
    pdf_path: Path, token: str, model: str, optional_payload: dict
) -> str:
    """Submit the PDF and return the job id."""
    headers = {"Authorization": f"bearer {token}"}
    data = {"model": model, "optionalPayload": json.dumps(optional_payload)}
    with pdf_path.open("rb") as f:
        resp = requests.post(
            JOB_URL, headers=headers, data=data, files={"file": f}, timeout=60
        )
    if resp.status_code != 200:
        raise RuntimeError(f"submit failed {resp.status_code}: {resp.text}")
    return resp.json()["data"]["jobId"]


def poll_job(job_id: str, token: str, timeout: int) -> str:
    """Poll until the job is done/failed and return the JSONL result URL."""
    headers = {"Authorization": f"bearer {token}"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.get(f"{JOB_URL}/{job_id}", headers=headers, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f"poll failed {resp.status_code}: {resp.text}")
        data = resp.json()["data"]
        state = data["state"]
        if state == "done":
            return data["resultUrl"]["jsonUrl"]
        if state == "failed":
            raise RuntimeError(f"job failed: {data.get('errorMsg')}")
        progress = data.get("extractProgress", {})
        if progress:
            print(
                f"  {state}: {progress.get('extractedPages', 0)}/"
                f"{progress.get('totalPages', '?')} pages"
            )
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"job {job_id} not done within {timeout}s")


def download_jsonl(url: str) -> list[dict]:
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    return [
        json.loads(line)
        for line in resp.text.strip().splitlines()
        if line.strip()
    ]


def parse_pages(lines: list[dict]) -> list[dict]:
    """Extract one page object per JSONL line (the Reports/ eval shape)."""
    pages = []
    for line in lines:
        lpr = line["result"]["layoutParsingResults"][0]
        pages.append(
            {
                "prunedResult": lpr["prunedResult"],
                "markdown": lpr["markdown"],
                "outputImages": lpr["outputImages"],
                "inputImage": lpr["inputImage"],
            }
        )
    return pages


def image_extension(url: str, content_type: str | None) -> str:
    """Pick an extension for a downloaded image.

    Prefers the Content-Type header; falls back to the URL path suffix; defaults
    to .jpeg (the The_Chatter/images convention).
    """
    if content_type:
        ext = CONTENT_TYPE_EXT.get(content_type.split(";")[0].strip().lower())
        if ext:
            return ext
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in CONTENT_TYPE_EXT.values() else DEFAULT_EXT


def plan_images(page_index: int, images: dict, counter: int, stem: str) -> tuple[dict, int]:
    """Build a rel-src -> {filename, url} map for one page's images.

    Returns (plan, new_counter) where counter is a document-wide image counter
    used for the _img<N> suffix. Images keep insertion order of `images`.
    """
    plan = {}
    for rel, url in images.items():
        counter += 1
        ext = image_extension(url, None)
        plan[rel] = {
            "filename": f"{stem}_p{page_index}_img{counter}{ext}",
            "url": url,
        }
    return plan, counter


def to_wikilinks(text: str, plan: dict) -> str:
    """Replace the API's centered <img> divs with Obsidian wikilinks.

    Unplanned imgs/ srcs (not in `plan`) are left untouched so no link is
    dropped silently.
    """

    def _sub(m: re.Match) -> str:
        rel = m.group(1)
        item = plan.get(rel)
        return f"![[images/{item['filename']}]]" if item else m.group(0)

    text = IMG_DIV_RE.sub(_sub, text)
    text = IMG_TAG_RE.sub(_sub, text)
    return EMPTY_DIV_RE.sub("", text)


def resolve_markdown(text: str, images: dict) -> str:
    """Replace relative `imgs/...` srcs with the absolute URL from images map."""
    return IMGSRC_RE.sub(lambda m: f'src="{images.get(m.group(1), m.group(1))}"', text)


def write_outputs(pages: list[dict], out_dir: Path, stem: str, fetch_images: bool) -> None:
    """Write combined .md, raw .json, and (optionally) download images."""
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{stem}.json"
    json_path.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {json_path} ({json_path.stat().st_size} bytes)")

    img_dir = out_dir / "images"
    img_counter = 0
    md_parts = []
    for i, page in enumerate(pages, start=1):
        md = page["markdown"]
        images_map = md.get("images", {})
        plan, img_counter = plan_images(i, images_map, img_counter, stem)

        if fetch_images:
            img_dir.mkdir(parents=True, exist_ok=True)
            for rel, item in plan.items():
                dest = img_dir / item["filename"]
                try:
                    r = requests.get(item["url"], timeout=60)
                    r.raise_for_status()
                    ext = image_extension(item["url"], r.headers.get("Content-Type"))
                    if ext != dest.suffix:
                        dest = dest.with_suffix(ext)
                        item["filename"] = dest.name
                    dest.write_bytes(r.content)
                except requests.RequestException as e:
                    print(f"  warn: page{i} image {rel}: {e}")

        if plan:
            md_parts.append(to_wikilinks(md["text"], plan))
        else:
            md_parts.append(resolve_markdown(md["text"], images_map))

    md_path = out_dir / f"{stem}.md"
    md_path.write_text("\n\n".join(md_parts), encoding="utf-8")
    print(f"wrote {md_path} ({md_path.stat().st_size} bytes)")

    if fetch_images and img_dir.exists():
        n = sum(1 for _ in img_dir.rglob("*") if _.is_file())
        print(f"images downloaded: {n} -> {img_dir}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("source_pdf", help="path to the source PDF file")
    ap.add_argument("output_dir", help="directory to store the results in")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--token", default=os.environ.get("PADDLE_API_KEY"))
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--no-images", action="store_true")
    args = ap.parse_args()

    if not args.token:
        print(
            "error: no API token: set PADDLE_API_KEY or pass --token",
            file=sys.stderr,
        )
        return 1

    pdf_path = Path(args.source_pdf)
    if not pdf_path.is_file():
        print(f"error: not found: {pdf_path}", file=sys.stderr)
        return 1

    optional_payload = {
        "useDocOrientationClassify": False,
        "useDocUnwarping": False,
        "useChartRecognition": False,
    }

    print(f"submitting {pdf_path.name} to model {args.model} ...")
    job_id = submit_job(pdf_path, args.token, args.model, optional_payload)
    print(f"job id: {job_id}")
    jsonl_url = poll_job(job_id, args.token, args.timeout)
    lines = download_jsonl(jsonl_url)
    print(f"result lines: {len(lines)}")

    pages = parse_pages(lines)
    stem = slugify(pdf_path.stem)
    write_outputs(pages, Path(args.output_dir), stem, not args.no_images)
    return 0


if __name__ == "__main__":
    sys.exit(main())
