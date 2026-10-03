"""Pre-annotation + retrieval escalation for the relations triage queue.

Proposal: doc/improvements/archive/graph/triage_preannotation_escalation.md
(executed 2026-10-04, completed.md #342; S1 pre-annotator, S2 retrieval
escalation, S3 human gate unchanged).

Reads the ``_pending_relations.txt`` queue (JSONL rows as written by
``extract_relations.write_sidecar``: edge_type/source/target_mention/
quote/edition/direction), asks a chat judge the two pinned eval-v3
questions (Q1 world-fact / Q2 house-rubric admit) with the row's own
quote as context, and writes advisory annotations to
``findata/Misc/_pending_annotations.jsonl``.

Rows the judge is unsure about AND flags as knowledge-gap (or that carry
a recent edition date) route through a retrieval substep: web search
(DDG html, Bing fallback) -> snippet evidence -> Q1 re-judge. A verdict
flip requires an explicit supporting quote; the evidence URL is recorded.

This module never writes to the graph. ``--apply-decisions`` on
``triage_pending_relations.py`` stays the only write gate; annotations
are report fields for the human reviewer.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.env import load_memory_env, require_env  # noqa: E402
from helpers.graph.triage_pending_relations import SIDECAR as QUEUE  # noqa: E402

ANNOTATIONS = _REPO_ROOT / "findata" / "Misc" / "_pending_annotations.jsonl"
BASE_URL = "https://api.z.ai/api/coding/paas/v4/chat/completions"
DEFAULT_MODEL = "glm-5.3"

# Pinned two-question prompt (eval-v3 judge, build_eval3.py HDR/SCHEMA,
# 2026-10-03 run: Q2 artifact rejection 37/37, overall 54/56). Prompt text
# is the regression-key surface; any change re-runs the eval-v3 key.
BATCH_HDR = """For each numbered item decide TWO independent questions about a claimed business relation. Each item may carry a "context": the corpus sentence the candidate was extracted from. Use it for what it is - our vault's text, which may be garbled, may be a bare co-mention with no assertion, or may assert the claim. The context is evidence, not truth.

Q1 "factually_accurate": Did the claimed relation actually occur in the real world - these two parties, this type of event, in this direction? If the underlying relationship is real but the type, counterparty, or direction is wrong AS STATED, answer false. Judge against the world, not the context.

Q2 "rubric_admit": Should this edge be admitted to a curated company knowledge graph? Admit ONLY if ALL hold:
(a) the counterparty is a specific named company or institution - not a sector, category, customer class, government body, project, or geography;
(b) the relation is cleanly stated - if a context is present and it is garbled, a fragment, or merely a co-mention with no asserted relation, reject;
(c) the edge type matches the event (acquisition, joint venture, supply, competition);
(d) the claim is factually accurate.

Items:
"""

BATCH_SCHEMA = """
Return ONLY a JSON array, no prose, no markdown fence. One object per item, keys exactly:
{"id":int,"factually_accurate":bool,"p_fact":number,"rubric_admit":bool,"p_rubric":number}"""

EVIDENCE_BATCH_PROMPT = """You previously judged numbered claims about business relations as factually uncertain. Below each claim are web-search results retrieved now. Decide Q1 again for each: did the claimed relation actually occur in the real world - these two parties, this type of event, in this direction? Wrong type, counterparty, or direction AS STATED means false.

Rules: judge against the world using the retrieved results. To answer true you must be able to point at a supporting snippet or a sentence from a fetched page text. If the results are irrelevant or unhelpful, keep your prior judgment (answer false with low confidence).

Items:
{items}

Return ONLY a JSON array, no prose, no markdown fence. One object per item, keys exactly:
{{"id":int,"factually_accurate":bool,"p_fact":number,"support_quote":"" or a short verbatim quote from that item's results,"evidence_url":"" or the URL of the supporting result}}"""

_EVIDENCE_ITEM = """{i}. edge_type: {edge_type}
   source: {source}
   counterparty: {target_mention}
   vault context: {context}
   prior judgment: factually_accurate={prior} (p_fact={p_prior:.2f})
   retrieved results:
{evidence}"""

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_FRESH_DAYS = 90


# --------------------------------------------------------------------------- #
# queue parsing (pure)                                                        #
# --------------------------------------------------------------------------- #
def parse_rows(lines: list[str]) -> list[dict]:
    """Dedupe typed candidate rows; skip suggested/blank/unparseable lines.

    Mirrors the build_triage dedupe key (edge_type, source, target_mention)
    but keeps the full untruncated quote for context attachment.
    """
    rows, seen = [], set()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("edge_type") in ("", "suggested"):
            continue
        key = f"{d.get('edge_type')}\x1f{d.get('source')}\x1f{d.get('target_mention')}"
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "id": f"{d.get('edge_type')}:{d.get('source')}:{d.get('target_mention')}",
                "edge_type": d.get("edge_type", ""),
                "source": d.get("source", ""),
                "target_mention": d.get("target_mention", ""),
                "quote": d.get("quote") or "",
                "edition": d.get("edition", ""),
            }
        )
    return rows


def edition_is_recent(edition: str, today: time.struct_time | None = None) -> bool:
    """True if the edition string carries a date within _FRESH_DAYS of today."""
    m = _DATE_RE.search(edition or "")
    if not m:
        return False
    import datetime

    try:
        d = datetime.date.fromisoformat(m.group(0))
    except ValueError:
        return False
    now = datetime.date.today() if today is None else datetime.date(*today[:3])
    return (now - d).days <= _FRESH_DAYS


def load_annotations(path: Path = ANNOTATIONS) -> dict[tuple[str, str, str], dict]:
    """Load annotations keyed by (edge_type, source, target_mention) — the
    row triple, so callers can match regardless of their own id scheme
    (triage report rows use a sha256 prefix id). Missing file = {}."""
    out: dict[tuple[str, str, str], dict] = {}
    if not Path(path).exists():
        return out
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            a = json.loads(line)
        except ValueError:
            continue
        eid = str(a.get("id", ""))
        parts = eid.split(":", 2)
        if len(parts) == 3:
            out[(parts[0], parts[1], parts[2])] = a
    return out


def fresh_flag(row: dict) -> bool:
    """True when the row carries a recent (fresh) edition date. Knowledge
    gaps need no separate flag: they manifest as low p_fact, which is the
    escalation trigger."""
    return edition_is_recent(row.get("edition", ""))


# --------------------------------------------------------------------------- #
# judge + retrieval                                                            #
# --------------------------------------------------------------------------- #
def call_glm(prompt: str, key: str, model: str, retries: int = 3) -> str:
    """One chat completion, bounded retry. Returns raw content.

    No `temperature` field on purpose: the provider endpoint behaves
    erratically when temperature is set (operator, 2026-10-04), and our
    measurements showed temperature 0 did NOT buy determinism anyway."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            body = json.dumps(
                {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 16000,
                }
            ).encode()
            req = urllib.request.Request(
                BASE_URL,
                data=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310  # allowlisted bases; query params quoted
                return json.loads(r.read())["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001 — bounded retry, re-raised after
            last = exc
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"chat call failed after {retries} attempts: {last}")


def _fmt_item(i: int, row: dict) -> str:
    return (
        f"{i}. edge_type: {row['edge_type']}\n"
        f"   source: {row['source']}\n"
        f"   counterparty (target mention): {row['target_mention']}\n"
        f"   context: {row.get('quote') or '(none)'}"
    )


def judge_rows(
    rows: list[dict], key: str, model: str, size: int = 10, on_chunk=None
) -> dict[str, dict]:
    """Q1/Q2 annotations for queue rows, batch framing (validated eval-v3
    instrument shape). Booleans are taken VERBATIM from the reply — the
    validated v3 scorer used them (37/37 artifact rejection, 54/56). The
    p_* fields are the model's CONFIDENCE in its boolean (measured
    2026-10-03: "false" with p_fact 0.98 = confident reject), NOT P(truth);
    deriving booleans from them inverts confident negatives — do not.

    Returns {row id: annotation}. Rows whose id is missing from the reply
    (decode dropout) are absent; callers re-try them via judge_row.
    """
    out: dict[str, dict] = {}
    for start in range(0, len(rows), size):
        chunk = rows[start : start + size]
        prompt = (
            BATCH_HDR
            + "\n".join(_fmt_item(i, r) for i, r in enumerate(chunk, start=1))
            + BATCH_SCHEMA
        )
        content = call_glm(prompt, key, model)
        m = re.search(r"\[.*\]", content, re.S)
        if not m:
            raise ValueError(f"no JSON array in judge reply: {content[:200]!r}")
        replies = {int(r["id"]): r for r in json.loads(m.group(0))}
        for i, row in enumerate(chunk, 1):
            r = replies.get(i)
            if r is None:
                continue
            out[row["id"]] = {
                "id": row["id"],
                "p_fact": float(r.get("p_fact", 0.0)),
                "factually_accurate": bool(r.get("factually_accurate", False)),
                "rubric_admit": bool(r.get("rubric_admit", False)),
                "p_rubric": float(r.get("p_rubric", 0.0)),
                "fresh_flag": fresh_flag(row),
                "context_chars": len(row.get("quote") or ""),
                "escalated": False,
                "evidence_url": None,
                "support_quote": None,
            }
        if on_chunk:
            on_chunk(start + len(chunk), len(rows))
    return out


def judge_row(row: dict, key: str, model: str) -> dict:
    """Single-row annotation (batch of one) — also the decode-dropout retry."""
    a = judge_rows([row], key, model).get(row["id"])
    if a is None:
        raise ValueError(f"judge dropped row id from reply: {row['id'][:80]}")
    return a


_DDG_BASE = "https://html.duckduckgo.com/html/?q="
_BING_HTML_BASE = "https://www.bing.com/search?q="
_BING_NEWS_BASE = "https://www.bing.com/news/search?q="
_GOOGLE_NEWS_BASE = "https://news.google.com/rss/search?q="
_GOOGLE_NEWS_TAIL = "&hl=en-IN&gl=IN&ceid=IN:en"


def _html_lane(query: str, base: str, parse) -> list[dict]:
    """Legacy scraping lane (captcha/homepage-prone) — degraded fallback only."""
    for attempt in range(2):
        try:
            req = urllib.request.Request(  # noqa: S310  # allowlisted bases; query params quoted
                base + urllib.parse.quote_plus(query),
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) triage-preannotate"},
            )
            with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310  # allowlisted bases; query params quoted
                html = r.read().decode("utf-8", "replace")
            snippets = parse(html)
            if snippets:
                return snippets
        except Exception:  # noqa: BLE001 — retry then fall through
            time.sleep(2 * (attempt + 1))
    return []


def _rss_get(url: str) -> str:
    req = urllib.request.Request(  # noqa: S310  # allowlisted bases; query params quoted
        url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) triage-preannotate"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310  # allowlisted bases; query params quoted
        return r.read().decode("utf-8", "replace")


def _unwrap_bing_news_url(link: str) -> str:
    """Bing News wraps targets as apiclick.aspx?...&url=<article url>; the
    value may be percent-encoded or plain."""
    link = link.replace("&amp;", "&")
    m = re.search(r"[?&]url=([^&]+)", link)
    if not m:
        return link
    target = urllib.parse.unquote(m.group(1))
    return target if target.startswith("http") else link


def _parse_bing_news_rss(xml: str) -> list[dict]:
    out = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
        title = re.search(r"<title>(.*?)</title>", item, re.S)
        link = re.search(r"<link>(.*?)</link>", item, re.S)
        desc = re.search(r"<description>(.*?)</description>", item, re.S)
        if not (title and link):
            continue
        headline = _strip_tags(title.group(1)).strip()
        out.append(
            {
                "title": headline,
                "url": _unwrap_bing_news_url(link.group(1)),
                "snippet": (_strip_tags(desc.group(1)).strip() if desc else headline)[:400],
            }
        )
    return out


def _parse_google_news_rss(xml: str) -> list[dict]:
    """Headlines are the evidence (verified 2026-09-29: the TCS-MHP fact sits
    in the titles); links stay news.google.com redirects — do not chain them
    through readers (r.jina.ai 403s google redirect pages)."""
    out = []
    for title, link in re.findall(r"<item><title>(.*?)</title><link>(.*?)</link>", xml, re.S):
        headline = _strip_tags(title).strip()
        out.append({"title": headline, "url": link.strip(), "snippet": headline[:400]})
    return out


def _relevance_filter(snippets: list[dict], query: str, source: str = "") -> list[dict]:
    """Drop noise results; off-topic evidence is worse than none.

    Two gates (field findings 2026-09-29 + 2026-10-04):
    * a result's title+snippet must share at least one >=4-char query token —
      the html lanes answer brand queries with brand homepages and unrelated
      retail pages;
    * a result hosted on the SOURCE entity's own domain must never evidence
      that source's row (a TCS row is not evidenced by tcs.com). Match by
      source word tokens and word initials ("Tata Consultancy Services" ->
      "tcs") against the host.
    """
    tokens = {w for w in re.findall(r"[a-z]{4,}", query.lower())}
    src_tokens = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", source)}
    initials = "".join(w[0] for w in re.findall(r"[A-Za-z]", source)).lower()
    kept = []
    for r in snippets:
        hay = (r.get("title", "") + " " + r.get("snippet", "")).lower()
        if tokens and not any(t in hay for t in tokens):
            continue
        host = (urllib.parse.urlparse(r.get("url", "")).hostname or "").lower()
        if source and (
            any(t in host for t in src_tokens if len(t) >= 3)
            or (len(initials) >= 2 and initials in host)
        ):
            continue
        kept.append(r)
    return kept


def _search_bing_news(query: str) -> list[dict]:
    return _parse_bing_news_rss(
        _rss_get(_BING_NEWS_BASE + urllib.parse.quote_plus(query) + "&format=rss")
    )


def _search_google_news(query: str) -> list[dict]:
    out = _parse_google_news_rss(
        _rss_get(_GOOGLE_NEWS_BASE + urllib.parse.quote_plus(query) + _GOOGLE_NEWS_TAIL)
    )
    return out[:5]  # measured 100 items on a 10-item probe day; cap explicitly


def _term_windows(text: str, terms: tuple[str, ...], width: int = 700, max_windows: int = 3) -> str:
    """Up to `max_windows` ~width-char windows of `text` around term hits."""
    low, hits = text.lower(), []
    for term in terms:
        t = (term or "").lower().strip()
        if len(t) < 3:
            continue
        start = 0
        while len(hits) < max_windows * 2:
            i = low.find(t, start)
            if i < 0:
                break
            hits.append(max(0, i - width // 3))
            start = i + len(t)
    if not hits:
        return text[:1200]
    windows, last = [], -(10**9)
    for h in sorted(hits):
        if h - last < width // 2:
            continue
        windows.append(text[h : h + width])
        last = h
        if len(windows) >= max_windows:
            break
    return " ... ".join(windows)


def _fetch_page_text(url: str, terms: tuple[str, ...] = (), timeout: int = 30) -> tuple[str, str]:
    """Fetch a result page's text; direct first, r.jina.ai reader fallback
    (proposal: search_enablers.md, S3 fetch layer).

    Returns (text, via): text is term-windowed (up to 3 ~700-char windows
    around the row terms) or the page head; failures return ("", "none") so
    escalation degrades to snippet evidence. Never raises.
    """
    transports = (
        (
            "direct",
            url,
            {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            },
        ),
        (
            "reader",
            "https://r.jina.ai/" + url,
            {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) triage-preannotate"},
        ),
    )
    for via, fetch_url, headers in transports:
        try:
            req = urllib.request.Request(fetch_url, headers=headers)  # noqa: S310  # allowlisted bases; query params quoted
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310  # allowlisted bases; query params quoted
                raw = r.read(500_000).decode("utf-8", "replace")
            text = re.sub(r"\s{2,}", " ", _strip_tags(raw)).strip()
            if len(text) < 200:
                continue
            return _term_windows(text, terms), via
        except Exception:  # noqa: S112, BLE001 — best effort; next transport
            continue
    return "", "none"


def web_search(query: str, retries: int = 1) -> list[dict]:
    """Agent-safe lanes first (proposal: search_enablers.md), html last.

    Lanes, first non-empty wins (returns [{title,url,snippet}] <=5):
      1. Bing News RSS   — direct article URLs via the apiclick `url=` param
      2. Google News RSS — headline-as-evidence; links stay google redirects
      3. DDG html        — legacy scraping (captcha-prone, degraded fallback)
      4. Bing html       — legacy scraping fallback

    Raises RuntimeError when every lane fails/empties so callers can mark the
    row needs-retry instead of hanging. `retries` is accepted for caller
    compatibility; RSS lanes are single-attempt by design.
    """
    lanes = (
        ("bing-news", _search_bing_news),
        ("google-news", _search_google_news),
        ("ddg-html", lambda q: _html_lane(q, _DDG_BASE, _parse_ddg)),
        ("bing-html", lambda q: _html_lane(q, _BING_HTML_BASE, _parse_bing)),
    )
    failures = []
    for name, lane in lanes:
        try:
            snippets = lane(query)
        except Exception as err:  # noqa: BLE001 — lane failure degrades, never hangs
            failures.append(f"{name}: {type(err).__name__}")
            continue
        if name.endswith("-html"):
            snippets = _relevance_filter(snippets, query)  # brand-homepage noise gate
        if snippets:
            return snippets[:5]
        failures.append(f"{name}: empty")
    raise RuntimeError(f"web search failed on all lanes ({', '.join(failures)}): {query!r}")


def _strip_tags(s: str) -> str:
    return (
        re.sub(r"<[^>]+>", "", s)
        .replace("&amp;", "&")
        .replace("&#x27;", "'")
        .replace("&quot;", '"')
    )


def _parse_ddg(html: str) -> list[dict]:
    if "challenge" in html.lower() or "captcha" in html.lower():
        return []
    out = []
    for m in re.finditer(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>',
        html,
        re.S,
    ):
        url, title, snip = m.group(1), _strip_tags(m.group(2)), _strip_tags(m.group(3))
        if url.startswith("//duckduckgo.com/l/?uddg="):
            url = urllib.parse.unquote(url.split("uddg=", 1)[1].split("&", 1)[0])
        out.append({"title": title.strip(), "url": url, "snippet": snip.strip()[:400]})
    return out


def _parse_bing(html: str) -> list[dict]:
    out = []
    for m in re.finditer(
        r'<li class="b_algo".*?<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a></h2>.*?<p[^>]*>(.*?)</p>',
        html,
        re.S,
    ):
        out.append(
            {
                "title": _strip_tags(m.group(2)).strip(),
                "url": _unwrap_bing_url(m.group(1)),
                "snippet": _strip_tags(m.group(3)).strip()[:400],
            }
        )
    return out


def _unwrap_bing_url(href: str) -> str:
    """Bing wraps result URLs as /ck/a?!...&u=a1<base64 of the target>."""
    href = href.replace("&amp;", "&")
    m = re.search(r"[?&]u=a1([A-Za-z0-9]+)", href)
    if m:
        import base64

        try:
            pad = "=" * (-len(m.group(1)) % 4)
            return base64.b64decode(m.group(1) + pad).decode("utf-8", "replace")
        except Exception:  # noqa: S110, BLE001 — malformed wrapper, return as-is
            pass
    return href


def evidence_query(row: dict) -> str:
    """Search query for one escalation row: the entity pair and the relation
    type, preferring the PRE-truncation mention when the row carries one.

    Measured 2026-10-04, and the obvious first answer was wrong: adding the
    row `quote` to the query made results WORSE (5 -> 2 on 2 of 3 live
    rows), because the quote is a mid-word-clipped 120-char window carrying
    unrelated entities and URL residue ("rtnersh", "dian Bank Amagi CMR
    Green", "youtube.com/watch?v=LzlH"), and those tokens narrow a news
    query to nothing.

    What actually helps is `truncated_from` (S6): the full captured mention
    keeps the relation words ("Sankyu Corporation involving") without the
    window's debris. Rows with no truncation are unaffected.
    """
    mention = row.get("truncated_from") or row.get("target_mention", "")
    parts = [row.get("source", ""), mention, str(row.get("edge_type", "")).replace("_", " ")]
    return " ".join(p for p in parts if p)


def _search_evidence(row: dict, retries: int = 1) -> list[dict]:
    """One quick search sweep for a row; empty on failure (best effort —
    the sweep runs once per row, never blocking on provider rate limits)."""
    query = evidence_query(row)
    try:
        return _relevance_filter(web_search(query, retries=retries), query, source=row["source"])
    except RuntimeError:
        return []


def escalate_rows(rows: list[dict], annotations: dict[str, dict], key: str, model: str) -> int:  # noqa: C901  (transport fallback + one evidence-batch judge per row)
    """S2 for ALL low-confidence rows in ONE evidence-batch judge call.

    Per-row retrieval (search each row, judge each row separately) costs a
    full-depth model call per row — measured 2026-10-03 at >60 min for ~28
    rows. Here: one search per row (quick, best-effort), then a single
    batched evidence re-judge. Verdict flips still require a supporting
    quote + URL; rows whose search failed are marked needs_retry.

    Mutates the annotations in place; returns the escalated count.
    """
    todo = [
        (r, annotations[r["id"]])
        for r in rows
        if r["id"] in annotations and needs_escalation(annotations[r["id"]])
    ]
    if not todo:
        return 0
    searched: list[tuple[dict, dict, list[dict]]] = []
    for row, a in todo:
        a["escalated"] = True
        results = _search_evidence(row)
        if not results:
            a["needs_retry"] = True
            continue
        searched.append((row, a, results))
    if not searched:
        return len(todo)  # all searches failed; nothing to re-judge
    items, evidence_text = [], {}
    for i, (row, a, results) in enumerate(searched, 1):
        # S3 fetch layer (search_enablers.md): verbatim quotes need page text,
        # not snippet luck. One page per row, top result only, best effort.
        page, via = "", "none"
        if results:
            try:
                page, via = _fetch_page_text(
                    results[0]["url"], (row["source"], row["target_mention"])
                )
            except Exception:  # noqa: BLE001 — fetch is best effort
                page, via = "", "none"
        evidence = "\n\n".join(
            f"[{j + 1}] {r['title']}\n{r['url']}\n{r['snippet']}" for j, r in enumerate(results)
        )
        if page:
            evidence += f"\n\n   fetched page text (via {via}):\n{page}"
        evidence_text[i] = evidence
        items.append(
            _EVIDENCE_ITEM.format(
                i=i,
                edge_type=row["edge_type"],
                source=row["source"],
                target_mention=row["target_mention"],
                context=row.get("quote") or "(none)",
                prior=a["factually_accurate"],
                p_prior=a["p_fact"],
                evidence=evidence,
            )
        )
    prompt = EVIDENCE_BATCH_PROMPT.format(items="\n\n".join(items))
    _m = re.search(r"\[.*\]", call_glm(prompt, key, model), re.S)
    replies = {int(r["id"]): r for r in json.loads(_m.group(0) if _m else "[]")}
    for i, (row, a, results) in enumerate(searched, 1):
        r = replies.get(i)
        if r is None:
            a["needs_retry"] = True
            continue
        quote = (r.get("support_quote") or "").strip()
        url = (r.get("evidence_url") or "").strip()
        a["support_quote"] = quote or None
        a["evidence_url"] = url or None

        # Verbatim-in-evidence check: a quote that appears nowhere in the
        # snippets or fetched page text is not evidence (measured risk once
        # page text lands — the judge can paraphrase or quote a snippet).
        def norm(s):
            return re.sub(r"\W+", " ", s).lower().strip()

        if quote and url and norm(quote) not in norm(evidence_text.get(i, "")):
            a["needs_retry"] = True
            a["quote_unverified"] = True
            continue
        if quote and url:  # apply the evidence verdict only when quoted AND verified
            a["p_fact"] = float(r.get("p_fact", a["p_fact"]))
            a["factually_accurate"] = bool(r.get("factually_accurate", a["factually_accurate"]))
            # Rubric clause (d): admission requires the fact to hold.
            a["rubric_admit"] = a["rubric_admit"] and a["factually_accurate"]
    return len(todo)


def escalate(row: dict, annotation: dict, key: str, model: str) -> dict:
    """Single-row escalation (batch of one) — thin wrapper over escalate_rows."""
    escalate_rows([row], {row["id"]: annotation}, key, model)
    return annotation


def needs_escalation(annotation: dict) -> bool:
    """Low CONFIDENCE in the fact boolean (p_fact < 0.5, either direction) —
    knowledge gap or fresh event. Retrieval resolves either; the direction
    comes from the boolean, so a confident reject never escalates."""
    return annotation["p_fact"] < 0.5


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="triage_preannotate", description=__doc__)
    ap.add_argument("--queue", type=Path, default=QUEUE, help="sidecar queue (JSONL)")
    ap.add_argument("--out", type=Path, default=ANNOTATIONS, help="annotations output (JSONL)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--limit", type=int, default=0, help="annotate only first N rows (0 = all)")
    ap.add_argument("--no-escalate", action="store_true", help="skip the retrieval substep")
    args = ap.parse_args(argv)

    lines = args.queue.read_text(encoding="utf-8").splitlines() if args.queue.exists() else []
    rows = parse_rows(lines)
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        print(f"no candidate rows in {args.queue}")
        return 0

    load_memory_env()
    key = require_env("ZAI_API_KEY", what="Z.AI coding-plan key")
    args.out.parent.mkdir(parents=True, exist_ok=True)

    annotations = judge_rows(
        rows,
        key,
        args.model,
        on_chunk=lambda done, total: print(f"judged {done}/{total}", flush=True),
    )
    missing = [r for r in rows if r["id"] not in annotations]
    for row in missing:  # decode-dropout retry, one row at a time
        try:
            annotations[row["id"]] = judge_row(row, key, args.model)
        except ValueError:
            continue
    print(f"judged {len(annotations)}/{len(rows)} rows", flush=True)

    def _dump() -> int:
        admits = sum(1 for a in annotations.values() if a["rubric_admit"])
        with args.out.open("w", encoding="utf-8") as f:
            for a in annotations.values():
                f.write(json.dumps(a, ensure_ascii=False) + "\n")
        return admits

    _dump()  # base pass survives even if escalation is interrupted
    if not args.no_escalate:
        escalate_rows(rows, annotations, key, args.model)
    admits = _dump()
    n_escalated = sum(1 for a in annotations.values() if a["escalated"])
    n_retry = sum(1 for a in annotations.values() if a.get("needs_retry"))
    for a in annotations.values():
        print(
            f"{'ADMIT' if a['rubric_admit'] else 'keep-out'} "
            f"p_fact={a['p_fact']:.2f} p_rubric={a['p_rubric']:.2f}"
            f"{' ESC' if a['escalated'] else ''}{' RETRY' if a.get('needs_retry') else ''} {a['id'][:70]}",
            flush=True,
        )
    print(
        f"\nannotations: {args.out} rows={len(annotations)} admits={admits} "
        f"escalated={n_escalated} needs_retry={n_retry}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
