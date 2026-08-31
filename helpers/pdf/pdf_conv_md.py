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
    --engine ENGINE          auto (default) | local | paddle. auto runs the
                             LOCAL no-OCR engine first (no API key needed;
                             born-digital PDFs only) and falls back to the
                             Paddle API when the local engine refuses a PDF
                             (no usable text layer). local/paddle force one
                             engine. Trial: doc/local/local_pdf_engine_trial.md
    --model PP-StructureV3   Paddle model name (default: PP-StructureV3)
    --token TOKEN            Paddle API token (required for the Paddle engine
                             unless PADDLE_API_KEY is set in memory/.env or
                             the environment)
    --timeout SECONDS        max wall-clock time to wait for a Paddle job
                             (default: 600)
    --no-images              skip downloading embedded images (leaves the
                             absolute <img src=...> URLs in the markdown)
    --no-verify              skip the post-conversion self-check (coverage
                             vs the PDF text layer, number audit, wikilink
                             integrity; writes <stem>.verify.json and prints
                             a verdict — WARN passes, FAIL exits 1)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

# slugify is shared with capture_newsletter_images.py (see helpers/pdf/common.py).
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from helpers.pdf.common import slugify  # noqa: E402
from helpers.pdf.pdf_local import (  # noqa: E402
    ENGINE_LABEL as LOCAL_ENGINE_LABEL,
    LocalRefusalError,
    convert as convert_local,
)

# LiteParse + Pix2Text engines (Slice 2, liteparse_pdf_engine proposal)
# liteparse_engine mirrors pdf_local.convert shape and provides the bbox
# sidecar for no-OCR plus the OCR fallback with image sidecar.
try:  # noqa: E402
    from helpers.pdf.liteparse_engine import (  # noqa: E402
        ENGINE_LABEL_NOCR as _LITE_NOCR_LABEL,
        ENGINE_LABEL_OCR as _LITE_OCR_LABEL,
        convert as _lite_convert,
    )
except ImportError:  # pragma: no cover - liteparse not installed
    _lite_convert = None
    _LITE_NOCR_LABEL = "liteparse-noocr"
    _LITE_OCR_LABEL = "liteparse-ocr"
# Fallback for tests that import liteparse_markdown directly
try:  # noqa: E402
    from helpers.pdf.liteparse_markdown import convert_liteparse_ocr as _lite_ocr_convert  # noqa: E402
except ImportError:  # pragma: no cover
    _lite_ocr_convert = None
# pix2text excluded from pipelines (2026-09-02): pulls large nvidia/CUDA deps
# Keep module file for reference but never import it here to avoid deptry/ty
# `unresolved-import` noise and accidental installs. The auto chain and the
# --engine pix2text path below both short-circuit with a clear error.
_pix2text_convert = None  # type: ignore[assignment]
from helpers.pdf.verify_extraction import (  # noqa: E402
    verify as verify_extraction,
)
from helpers.core.env import load_memory_env
from helpers.core.frontmatter import (  # noqa: E402
    iso_now_utc,
    moddate_to_iso_date,
    render_frontmatter,
)

import requests

# Known series -> publisher (newsletter_notes_adoption.md S2, accepted Q1:
# omit-when-unknown — extend this map when a new series lands).
_PUBLISHER_BY_SERIES = {
    "the_chatter": "zerodha",
    "points_and_figures": "zerodha",
    "the_plotlines": "zerodha",
}

JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
DEFAULT_MODEL = "PP-StructureV3"
POLL_INTERVAL = 5

# An <img> wrapped in the centered <div> the API emits around each image.
# Group 1 captures the relative imgs/... src.
IMG_DIV_RE = re.compile(r'<div style="text-align: center;"><img src="(imgs/[^"]+)"[^>]*/></div>')
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


def submit_job(pdf_path: Path, token: str, model: str, optional_payload: dict) -> str:
    """Submit the PDF and return the job id."""
    headers = {"Authorization": f"bearer {token}"}
    data = {"model": model, "optionalPayload": json.dumps(optional_payload)}
    with pdf_path.open("rb") as f:
        # 300s, not the AI-Studio sample's unbounded POST and not the old
        # 60s cap: the multipart upload to the CN-hosted job API can stall
        # past 60s on a cold/slow route (observed repeatedly 2026-08-25) —
        # the failure was always a client-side write timeout, never a
        # server rejection. requests' timeout applies per-socket-op, so a
        # healthy transfer resets it chunk-by-chunk.
        resp = requests.post(JOB_URL, headers=headers, data=data, files={"file": f}, timeout=300)
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
    return [json.loads(line) for line in resp.text.strip().splitlines() if line.strip()]


def parse_pages(lines: list[dict]) -> list[dict]:
    """Extract one page object per JSONL line (the Reports/ eval shape).

    Robust to unexpected Paddle JSONL shapes (an error object, a missing or
    empty ``result``/``layoutParsingResults``, or a page whose ``markdown`` key
    is absent): such lines are skipped with a warning instead of raising, so a
    single malformed page cannot abort the whole conversion.

    Returns only successfully parsed pages.
    """
    pages = []
    for idx, line in enumerate(lines):
        if not isinstance(line, dict):
            print(f"  warn: parse_pages[{idx}]: not a JSON object, skip")
            continue
        result = line.get("result")
        if not isinstance(result, dict):
            print(f"  warn: parse_pages[{idx}]: missing/non-dict 'result', skip")
            continue
        lpr_list = result.get("layoutParsingResults")
        if not isinstance(lpr_list, list) or not lpr_list:
            print(f"  warn: parse_pages[{idx}]: empty/missing layoutParsingResults, skip")
            continue
        lpr = lpr_list[0]
        if not isinstance(lpr, dict) or "markdown" not in lpr:
            print(f"  warn: parse_pages[{idx}]: lpr[0] missing 'markdown', skip")
            continue
        pages.append(
            {
                "prunedResult": lpr.get("prunedResult"),
                "markdown": lpr["markdown"],
                "outputImages": lpr.get("outputImages", []),
                "inputImage": lpr.get("inputImage"),
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


def _pdf_metadata(pdf_path: Path) -> dict[str, str]:
    """Title/ModDate from pdfinfo; {} when pdfinfo is missing or fails.

    The values feed the OKF ``sources[]`` credibility signals
    (okf_adoption.md §2.2); absence is tolerated — keys are simply omitted.
    """
    import shutil
    import subprocess

    pdfinfo = shutil.which("pdfinfo")
    if pdfinfo is None:
        return {}
    try:
        proc = subprocess.run(  # noqa: S603  # resolved absolute path, no shell
            [pdfinfo, str(pdf_path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except OSError, subprocess.SubprocessError:
        return {}
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        if key.strip() in ("Title", "ModDate") and val.strip():
            out[key.strip()] = val.strip()
    return out


def _first_heading_title(pages: list[dict], stem: str) -> str:
    """First markdown heading across the pages, else the file stem."""
    for page in pages:
        for line in (page.get("markdown") or {}).get("text", "").splitlines():
            if line.startswith("#"):
                return line.lstrip("# ").strip() or stem
    return stem


def build_okf_frontmatter(
    pages: list[dict],
    pdf_path: Path,
    model: str,
    stem: str,
    *,
    now: str | None = None,
    out_dir: Path | str | None = None,
) -> str:
    """Render the OKF v0.2 provenance frontmatter for a converted note.

    - ``type: newsletter`` — self-describing; validated by
      doc/okf/frontmatter.newsletter.v1.json since the source trees came
      under the B1 gate (newsletter_notes_adoption.md S1/S2).
    - ``tags``: namespaced source vocabulary — ``series/<out_dir slug>``
      always, plus ``publisher/<slug>`` when the series is in the known map
      (accepted Q1: omitted when unknown, never guessed).
    - ``generated``: ``pdf_conv_md.py/<model>`` actor + ISO 8601 UTC time.
    - ``sources``: exactly one entry, and ONLY when the source PDF sits
      under ``Reports/`` (accepted decision Q1) — ``resource`` is the
      bundle-relative path with a leading ``/``; ``title`` falls back to
      the stem when pdfinfo has none; ``last_modified`` is the PDF's
      ModDate converted to an ISO UTC date (omitted when unknown).
    """
    repo_root = Path(__file__).resolve().parents[2]
    try:
        rel = pdf_path.resolve().relative_to(repo_root)
    except ValueError:
        rel = None
    fm: dict = {"type": "newsletter"}
    fm["title"] = _first_heading_title(pages, stem)
    tags: list[str] = []
    if out_dir is not None:
        series = re.sub(r"[^a-z0-9]+", "_", Path(out_dir).name.lower()).strip("_")
        if series:
            tags.append(f"series/{series}")
            publisher = _PUBLISHER_BY_SERIES.get(series)
            if publisher:
                tags.append(f"publisher/{publisher}")
    if tags:
        fm["tags"] = tags
    fm["generated"] = {"by": f"pdf_conv_md.py/{model}", "at": now or iso_now_utc()}
    if rel is not None and rel.parts[:1] == ("Reports",):
        meta = _pdf_metadata(pdf_path)
        src = {
            "id": stem,
            "resource": "/" + rel.as_posix(),
            "title": meta.get("Title") or pdf_path.stem,
            "author": "process:pdf_conv_md",
        }
        lm = moddate_to_iso_date(meta.get("ModDate"))
        if lm:
            src["last_modified"] = lm
        fm["sources"] = [src]
    return render_frontmatter(fm)


def write_outputs(
    pages: list[dict], out_dir: Path, stem: str, fetch_images: bool, frontmatter: str | None = None
) -> None:
    """Write combined .md, raw .json, and (optionally) download images.

    ``frontmatter`` (OKF provenance block, §okf_adoption 2.2) is prepended
    to the combined markdown when given.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{stem}.json"
    json_path.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {json_path} ({json_path.stat().st_size} bytes)")

    img_dir = out_dir / "images"
    img_counter = 0
    n_captured = 0  # this run only — images/ is shared across editions, so
    md_parts = []  # a dir-wide count would mostly report older editions
    for i, page in enumerate(pages, start=1):
        md = page["markdown"]
        images_map = md.get("images", {})
        plan, img_counter = plan_images(i, images_map, img_counter, stem)

        if fetch_images:
            img_dir.mkdir(parents=True, exist_ok=True)
            for rel, item in plan.items():
                dest = img_dir / item["filename"]
                local_src = Path(item["url"])
                if local_src.is_file():  # local engine: copy, no network
                    shutil.copy2(local_src, dest)
                    n_captured += 1
                    continue
                try:
                    r = requests.get(item["url"], timeout=60)
                    r.raise_for_status()
                    ext = image_extension(item["url"], r.headers.get("Content-Type"))
                    if ext != dest.suffix:
                        dest = dest.with_suffix(ext)
                        item["filename"] = dest.name
                    dest.write_bytes(r.content)
                    n_captured += 1
                except requests.RequestException as e:
                    print(f"  warn: page{i} image {rel}: {e}")

        if plan:
            md_parts.append(to_wikilinks(md["text"], plan))
        else:
            md_parts.append(resolve_markdown(md["text"], images_map))

    md_path = out_dir / f"{stem}.md"
    body = "\n\n".join(md_parts)
    md_path.write_text((frontmatter + "\n" + body) if frontmatter else body, encoding="utf-8")
    print(f"wrote {md_path} ({md_path.stat().st_size} bytes)")

    if fetch_images:
        print(f"images captured: {n_captured} -> {img_dir}")


def _convert_paddle(pdf_path: Path, args: argparse.Namespace) -> list[dict]:
    """The Paddle API path (submit, poll, download, parse)."""
    load_memory_env()  # memory/.env may supply PADDLE_API_KEY
    args.token = args.token or os.environ.get("PADDLE_API_KEY")
    if not args.token:
        raise SystemExit("error: no API token: set PADDLE_API_KEY or pass --token")
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
    return parse_pages(lines)


def main() -> int:  # noqa: C901  # engine dispatch — linear fallback chain, not refactorable without obscuring
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("source_pdf", help="path to the source PDF file")
    ap.add_argument("output_dir", help="directory to store the results in")
    ap.add_argument(
        "--engine",
        choices=("auto", "local", "paddle", "lite", "lite-ocr", "pix2text"),
        default="auto",
        help=(
            "auto (default): pdf_local (pymupdf4llm) for born-digital, "
            "then lite OCR (Tesseract) for scanned, Paddle API last; "
            "pix2text is currently disabled (excluded from pipelines — nvidia deps); "
            "local/paddle/lite/lite-ocr/pix2text force one (pix2text errors with hint)"
        ),
    )
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--token", default=os.environ.get("PADDLE_API_KEY"))
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--no-verify", action="store_true", help="skip the post-conversion self-check")
    ap.add_argument(
        "--layout",
        action="store_true",
        help="local engine: use pymupdf's ONNX layout model "
        "(default off since 2026-08-26: ~3x faster without "
        "it and better word coverage on the Reports corpus)",
    )
    args = ap.parse_args()

    pdf_path = Path(args.source_pdf)
    if not pdf_path.is_file():
        print(f"error: not found: {pdf_path}", file=sys.stderr)
        return 1

    # Engine chain (2026-09-02, pix2text excluded):
    # auto: pdf_local (born-digital) -> lite OCR (Tesseract, 0.3s) -> Paddle last
    # pix2text (mfd-1.5) disabled — excluded from pipelines to avoid large nvidia/CUDA deps.
    # pdf_local stays primary for non-OCR per review; lite is OCR fallback, not markdown replacement.
    pages: list[dict] | None = None
    engine_label = ""
    tmpdir: tempfile.TemporaryDirectory[str] | None = None

    # Helper to wrap lite/pix2text markdown (plain text) into the pdf_conv_md pages shape
    def _wrap_markdown_as_pages(md_text: str, label: str) -> list[dict]:
        # Minimal pages shape: single page with text, no images (images via pymupdf sidecar if needed)
        # Downstream parse_newsletter handles "## " headings added by lite/pix2text markdown
        return [
            {
                "prunedResult": None,
                "markdown": {"text": md_text, "images": {}},
                "outputImages": [],
                "inputImage": None,
            }
        ]

    def _wrap_markdown_per_page(page_texts: list[str], label: str) -> list[dict]:
        # Per-page wrapper so `verify_extraction` per-page coverage works on
        # multi-page PDFs (e.g. SBI 28p via lite OCR). Each entry mirrors the
        # pdf_local shape with its own markdown text.
        return [
            {
                "prunedResult": None,
                "markdown": {"text": pt, "images": {}},
                "outputImages": [],
                "inputImage": None,
            }
            for pt in page_texts
        ]

    try:
        # 1. pdf_local (pymupdf4llm) for born-digital
        if args.engine in ("auto", "local"):
            tmpdir = tempfile.TemporaryDirectory(prefix="pdf_local_")
            try:
                pages = convert_local(pdf_path, Path(tmpdir.name) / "imgs", layout=args.layout)
                engine_label = LOCAL_ENGINE_LABEL
                print(f"parsed locally with {engine_label}")
            except LocalRefusalError as e:
                if args.engine == "local":
                    print(f"error: local engine refused: {e}", file=sys.stderr)
                    return 1
                print(f"  local engine refused ({e}) — trying lite OCR fallback")
        # 2. lite / lite-ocr for scanned and bbox sidecar
        # `lite` is the fast no-OCR path (bbox sidecar, 0.10s) — refuses scanned.
        # `lite-ocr` / `auto` fallback is Tesseract OCR (0.3s) for scanned.
        if pages is None and args.engine in ("auto", "lite", "lite-ocr"):
            # Prefer the engine that handles images + per-page shape
            if _lite_convert is not None:
                try:
                    import os as _os

                    _os.environ.setdefault("TESSDATA_PREFIX", "/usr/share/tesseract-ocr/5/tessdata")
                    # Determine ocr flag: `lite` -> no-ocr, `lite-ocr` -> ocr, `auto` -> ocr (scanned)
                    want_ocr = args.engine == "lite-ocr" or args.engine == "auto"
                    # For `auto` we try OCR directly (scanned); lite no-ocr would refuse anyway
                    if args.engine == "lite":
                        want_ocr = False
                    # tmp dir for lite image sidecar (reuse pdf_local tmpdir if present)
                    if tmpdir is None:
                        tmpdir = tempfile.TemporaryDirectory(prefix="lite_")
                    lite_img_dir = Path(tmpdir.name) / "imgs"
                    lite_img_dir.mkdir(parents=True, exist_ok=True)
                    pages = _lite_convert(pdf_path, lite_img_dir, ocr=want_ocr)
                    engine_label = _LITE_OCR_LABEL if want_ocr else _LITE_NOCR_LABEL
                    total_chars = sum(len(p["markdown"]["text"]) for p in pages)
                    print(f"parsed via {engine_label} ({total_chars} chars, {len(pages)}p)")
                    if args.engine in ("lite", "lite-ocr"):
                        pass
                except LocalRefusalError as e:
                    print(
                        f"  lite {args.engine} refused ({e}) — trying next fallback",
                        file=sys.stderr,
                    )
                    pages = None
                except Exception as e:
                    print(f"  lite failed ({e}) — trying next fallback", file=sys.stderr)
                    pages = None
            elif _lite_ocr_convert is not None:
                # Fallback to old liteparse_markdown path (no image sidecar)
                try:
                    import os as _os

                    _os.environ.setdefault("TESSDATA_PREFIX", "/usr/share/tesseract-ocr/5/tessdata")
                    md, meta = _lite_ocr_convert(pdf_path)
                    pts = meta.get("page_texts")
                    if isinstance(pts, list) and len(pts) == meta.get("pages", len(pts)):
                        pages = _wrap_markdown_per_page(pts, meta.get("engine", "liteparse-ocr"))
                    else:
                        pages = _wrap_markdown_as_pages(md, meta.get("engine", "liteparse-ocr"))
                    engine_label = meta.get("engine", "liteparse-ocr-eng")
                    print(
                        f"parsed via lite OCR {engine_label} ({meta.get('chars', len(md))} chars, {meta.get('pages', 1)}p)"
                    )
                except Exception as e:
                    print(f"  lite OCR failed ({e}) — trying next fallback", file=sys.stderr)
                    pages = None
            else:
                print("  lite not available (liteparse not installed)", file=sys.stderr)
        # 3. pix2text — DISABLED (excluded from pipelines 2026-09-02, nvidia deps)
        if args.engine == "pix2text":
            print(
                "error: --engine pix2text is currently disabled (excluded from pipelines "
                "to avoid large nvidia/CUDA dependencies). Use --engine auto/lite-ocr "
                "(Tesseract) or --engine paddle instead.",
                file=sys.stderr,
            )
            return 1
        if pages is None:
            engine_label = args.model
            pages = _convert_paddle(pdf_path, args)

        stem = slugify(pdf_path.stem)
        fm = build_okf_frontmatter(
            pages, pdf_path, engine_label, stem, out_dir=Path(args.output_dir)
        )
        write_outputs(pages, Path(args.output_dir), stem, not args.no_images, fm)
    finally:
        if tmpdir is not None:
            tmpdir.cleanup()

    if not args.no_verify:
        from helpers.pdf.verify_extraction import summarize

        manifest = verify_extraction(pdf_path, Path(args.output_dir), stem)
        print(summarize(manifest))
        if manifest["verdict"] == "FAIL":
            print(f"error: verification FAILED — see {stem}.verify.json", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
