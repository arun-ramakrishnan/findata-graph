#!/usr/bin/env python3
"""Typed-judgment client for System One models (noul / choice / score).

One narrow question, a typed answer with a distribution — no prose, no
markdown, no parsing. Two carriers, one interface:

  * ``inception/mercury-decide:free`` — OpenRouter **decisions** API
    (``POST /api/alpha/decisions``), one ``state`` + many typed
    ``questions`` per call. This is the Jev pattern the assessment
    evaluated: noul = P(yes) in [0,1], choice = argmax + full
    distribution, score = ordinal. Chat-completions models are NOT in
    this catalog (``/api/v1/models`` 404s ``mercury-decide``) — the id
    only exists on the decisions lane, so the transport is not swappable.
  * ``inception/mercury-2`` / ``mercury-2.5`` — plain OpenRouter chat
    completions, for the cases where the state is too big for one
    decision call or you want prose reasoning. Priced per token, not
    free.

Design rules this module exists to enforce:

* **Typed questions, typed answers.** You declare a question as noul
  (yes/no + P), choice (enum + distribution), or score (ordered rubric).
  You get the distribution back, not a vibe. No JSON-array scraping, no
  "answer only 1" coercion, no prompt drift.
* **Cost-capped and telemetry-first.** Every call returns timings
  (wall + per-question), token/context accounting where the carrier
  reports it, attempt count, and a cache key so a re-run is free. Set
  ``OPENROUTER_MAX_CENTS`` to hard-cap a run's spend (default 0 =
  uncapped); the client refuses to start a call it cannot afford.
* **Cache-first.** Identical (carrier, model, state, questions) is a
  disk cache hit: content-hash key, no network, no cost. ``--no-cache``
  for a forced re-ask.
* **A/B is a first-class call.** :func:`ab` asks the same state across
  N carriers and returns a per-carrier :class:`Verdict` plus an
  agreement map — the second source for a human A/B sitting.
* **Advisory only.** Nothing here writes domain data. It answers
  questions; a human or an existing gate decides.

Transport is injectable (tests pass a fake, no network). Key resolution:
``OPENROUTER_API_KEY`` env (see ``helpers.core.env.load_memory_env``) or
the ``openrouter`` entry of ``~/.local/share/opencode/auth.json``.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import pathlib
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# Repo root: helpers/core/typed_judgment.py -> parents[2]. Must be on
# sys.path BEFORE the helpers.* imports below so the script works as a
# subprocess the same way it works under pytest. (House bootstrap.)
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.env import load_memory_env  # noqa: E402

__all__ = [
    "Carrier",
    "Question",
    "Verdict",
    "RunStats",
    "CallResult",
    "CARRIERS",
    "noul",
    "choice",
    "score",
    "resolve_key",
    "cache_path",
    "ask",
    "ab",
    "AGREE_TOL",
    "brief",
    "cache_age_days",
    "describe_answer",
    "gap_of",
    "rank_contentious",
]

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
CACHE_DIR = pathlib.Path(
    os.environ.get("TYPED_JUDGMENT_CACHE", pathlib.Path.home() / ".cache" / "typed_judgment")
)
MODEL_CONTEXT = {"inception/mercury-decide:free": 32_000, "inception/mercury-2": 128_000}
# noul P(yes) gap below which two carriers count as agreeing. One constant so
# the card renderer and the contentious ranker can never disagree with each other.
AGREE_TOL = 0.2
# The chat lane has no typed schema, so it is asked for the same shape and
# normalized back into {question: {noul|choice|score}}. Without this adapter a
# mixed A/B silently drops the chat carrier's answers.
CHAT_SCHEMA = (
    '{"<question>": {"noul": <0.0-1.0 P(yes)>}}'
    ' | {"<question>": {"choice": "<option>"}}'
    ' | {"<question>": {"score": "<label>"}}'
)

# S6 (system_one_typed_judgment_framework.md): a surface may SHOW carrier
# verdicts to the operator only when it has a labeled eval key — no key,
# no show. The registry maps surface id -> frozen key artifact path. A
# surface that adopts a typed question registers here in the same change
# that ships the question; the key is re-run whenever the question text
# changes (S7 pinning).
SURFACE_EVAL_KEYS: dict[str, str] = {
    "relations_triage_queue": "doc/local/evaluations/jev_pilot/dataset/eval3/items.json",
}


def surface_key_registered(surface: str) -> bool:
    return surface in SURFACE_EVAL_KEYS


def require_surface_key(surface: str) -> str:
    """S6 policy gate: raise when a surface shows carrier verdicts without
    a registered eval key. Advisory rendering stays readable (the raise
    happens before any card is built), but the policy is executable, not
    prose — a new adopting surface fails loudly until it files its key."""
    key = SURFACE_EVAL_KEYS.get(surface)
    if key is None:
        raise KeyError(
            f"surface {surface!r} shows carrier verdicts but has no labeled "
            "eval key (S6: no key, no show) — file one and register it in "
            "SURFACE_EVAL_KEYS first"
        )
    return key


@dataclass(frozen=True)
class Carrier:
    """A System One model endpoint. ``kind`` picks the transport."""

    id: str
    kind: Literal["decisions", "chat"] = "decisions"
    context: int | None = None

    def context_tokens(self) -> int:
        return self.context or MODEL_CONTEXT.get(self.id, 32_000)


CARRIERS: dict[str, Carrier] = {
    "mercury-decide": Carrier("inception/mercury-decide:free", "decisions"),
    "mercury-2": Carrier("inception/mercury-2", "chat", 128_000),
    "mercury-2.5": Carrier("inception/mercury-2.5", "chat", 260_000),
}

Question = dict[str, Any]  # {"type": "noul"|"choice"|"score", ...}
Transport = Callable[[str, dict, float], tuple[int, dict]]


@dataclass
class RunStats:
    """Per-run spend/latency ledger. Attach to any caller for reporting."""

    calls: int = 0
    cache_hits: int = 0
    retries: int = 0
    errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cents: float = 0.0
    max_cents: float = 0.0

    def call(self, *, prompt: int = 0, completion: int = 0, cents: float = 0.0) -> None:
        self.calls += 1
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.cents += cents

    def as_dict(self) -> dict:
        d = asdict(self)
        d["budget_exceeded"] = self.max_cents > 0 and self.cents >= self.max_cents
        return d


@dataclass
class CallResult:
    carrier: str
    model: str
    answers: dict = field(default_factory=dict)
    wall_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    context_used_pct: float = 0.0
    cents: float = 0.0
    attempts: int = 1
    cached: bool = False
    error: str | None = None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        return d


@dataclass
class Verdict:
    """One carrier's answer to one state, with provenance."""

    carrier: str
    ok: bool
    answers: dict = field(default_factory=dict)
    wall_s: float = 0.0
    context_used_pct: float = 0.0
    cents: float = 0.0
    cached: bool = False
    error: str | None = None


def noul(instructions: str, criteria: dict[str, str]) -> Question:
    return {"type": "noul", "instructions": instructions, "criteria": criteria}


def choice(instructions: str, criteria: dict[str, str]) -> Question:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions: str, criteria: list[str]) -> Question:
    return {"type": "score", "instructions": instructions, "criteria": criteria}


def resolve_key() -> str:
    load_memory_env()
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key:
        return key
    p = pathlib.Path.home() / ".local/share/opencode/auth.json"
    if p.is_file():
        try:
            return json.loads(p.read_text())["openrouter"]["key"]
        except KeyError, json.JSONDecodeError:
            return ""
    return ""


def cache_path(
    carrier: Carrier, state: str, questions: dict, use_cache: bool = True
) -> pathlib.Path | None:
    if not use_cache:
        return None
    key = hashlib.sha256(
        json.dumps([carrier.id, state, questions], sort_keys=True).encode()
    ).hexdigest()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{carrier.id.replace('/', '_')}-{key[:24]}.json"


def cache_age_days(
    carrier: str, state: str, questions: dict, use_cache: bool = True
) -> float | None:
    """Age in days of the cached verdict for this exact call — None when
    uncached, disabled, or timestamp-less (pre-S2 blobs carry no ts).
    Lets the drift lane tell a stale read from provider drift instead
    of conflating the two."""
    tpath = cache_path(CARRIERS.get(carrier, Carrier(carrier)), state, questions, use_cache)
    if not tpath or not tpath.is_file():
        return None
    try:
        ts = json.loads(tpath.read_text()).get("ts", "")
        then = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
    except ValueError, TypeError:
        return None
    return (datetime.datetime.now() - then).total_seconds() / 86400


def _post(url: str, headers: dict, body: dict, timeout: float) -> tuple[int, dict]:
    import requests

    r = requests.post(url, headers=headers, json=body, timeout=timeout)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"error": {"message": r.text[:200]}}


def normalize_answers(kind: str, resp: dict, questions: dict) -> dict:
    """Map a raw carrier response to ``{question_name: answer_dict}``.

    Both lanes land on the same shape so :func:`ab`, :func:`brief` and
    :func:`rank_contentious` never have to know which carrier answered.
    """
    if kind == "decisions":
        return dict(resp.get("answers") or {})
    try:
        content = resp["choices"][0]["message"]["content"]
    except KeyError, IndexError, TypeError:
        return {}
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        parsed = json.loads(content[start : end + 1])
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {q: v for q, v in parsed.items() if q in questions and isinstance(v, dict)}


def ask(  # noqa: C901  (dispatch ladder: 3 question types + cache/carrier/ledger wiring)
    state: str,
    questions: dict[str, Question],
    carrier: str = "mercury-decide",
    *,
    key: str | None = None,
    transport: Transport | None = None,
    use_cache: bool = True,
    timeout: float = 60.0,
    max_attempts: int = 3,
    max_cents: float = 0.0,
    stats: RunStats | None = None,
) -> CallResult:
    """Ask one carrier ``questions`` about ``state``. Returns a
    :class:`CallResult` (never raises on carrier error — inspect ``ok``
    via :func:`ab`, or ``answers == {}`` here)."""
    c = CARRIERS.get(carrier, Carrier(carrier))
    key = key or resolve_key()
    tpath = cache_path(c, state, questions, use_cache)
    if tpath and tpath.is_file():
        blob = json.loads(tpath.read_text())
        if stats:
            stats.cache_hits += 1
        return CallResult(
            carrier=carrier,
            model=c.id,
            answers=blob["answers"],
            wall_s=blob["wall_s"],
            prompt_tokens=blob["prompt_tokens"],
            completion_tokens=blob["completion_tokens"],
            context_used_pct=blob["context_used_pct"],
            cents=0.0,
            attempts=1,
            cached=True,
        )
    if max_cents > 0 and stats and stats.cents >= max_cents:
        return CallResult(carrier=carrier, model=c.id, attempts=0)

    url = DECISIONS_URL if c.kind == "decisions" else CHAT_URL
    if transport is None:
        if not key:
            return CallResult(carrier=carrier, model=c.id, attempts=0)
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        transport = lambda u, b, t: _post(u, headers, b, t)  # noqa: E731

    if c.kind == "decisions":
        body = {"model": c.id, "state": state, "questions": questions}
    else:
        payload = (
            state
            + "\n\nAnswer every question with one number or label each, as JSON only:\n"
            + CHAT_SCHEMA
            + "\n\nquestions:\n"
            + json.dumps(questions, indent=1)
        )
        body = {
            "model": c.id,
            "messages": [{"role": "user", "content": payload}],
            "temperature": 0,
        }

    t0, attempts, last = time.monotonic(), 0, None
    for attempts in range(1, max_attempts + 1):
        status, resp = transport(url, body, timeout)
        if status == 200:
            answers = normalize_answers(c.kind, resp, questions)
            pt = int(resp.get("usage", {}).get("prompt_tokens", 0) or 0)
            ct = int(resp.get("usage", {}).get("completion_tokens", 0) or 0)
            wall = time.monotonic() - t0
            ctx_pct = round(100.0 * len(state) / (4 * c.context_tokens()), 2)
            res = CallResult(
                carrier=carrier,
                model=c.id,
                answers=answers or {},
                wall_s=round(wall, 3),
                prompt_tokens=pt,
                completion_tokens=ct,
                context_used_pct=ctx_pct,
                attempts=attempts,
            )
            if stats:
                stats.call(prompt=pt, completion=ct)
            if tpath and res.answers:
                # never persist an empty verdict: a parse failure or rate
                # limit cached as {} reads as a permanent answer later
                tpath.write_text(
                    json.dumps(
                        {
                            "answers": res.answers,
                            "wall_s": res.wall_s,
                            "prompt_tokens": pt,
                            "completion_tokens": ct,
                            "context_used_pct": ctx_pct,
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "carrier": carrier,
                            "model": c.id,
                        }
                    )
                )
            return res
        last = str(resp.get("error", {}).get("message", str(resp)))[:200]
        if attempts < max_attempts:
            if stats:
                stats.retries += 1
            time.sleep(min(2**attempts, 8))
    if stats:
        stats.errors += 1
    return CallResult(
        carrier=carrier, model=c.id, attempts=attempts, answers={}, wall_s=0.0, error=last
    )


def ab(
    state: str,
    questions: dict[str, Question],
    carriers: list[str],
    *,
    key: str | None = None,
    keys: dict[str, str] | None = None,
    transport: Transport | None = None,
    use_cache: bool = True,
    max_cents: float = 0.0,
    max_attempts: int = 3,
    timeout: float = 60.0,
) -> tuple[dict[str, Verdict], dict[str, dict]]:
    """Ask ``carriers`` the same questions; return (verdicts, agreement).

    ``agreement[q]`` is ``{"agree": bool, "values": {carrier: value}}``
    where value is noul P(yes) / choice argmax / score label. Disagreement
    is the signal a human A/B sitting wants. ``keys`` maps carrier -> API
    key for mixed-registry A/Bs whose carriers live on different
    endpoints (e.g. glm-5.3 on the Z.AI plan beside a decisions-lane
    carrier on OpenRouter); ``key`` remains the single-key form.
    """
    stats = RunStats(max_cents=max_cents)
    verdicts: dict[str, Verdict] = {}
    for name in carriers:
        r = ask(
            state,
            questions,
            name,
            key=(keys or {}).get(name) or key,
            transport=transport,
            use_cache=use_cache,
            stats=stats,
            max_cents=max_cents,
            max_attempts=max_attempts,
            timeout=timeout,
        )
        verdicts[name] = Verdict(
            carrier=name,
            ok=bool(r.answers),
            answers=r.answers,
            wall_s=r.wall_s,
            context_used_pct=r.context_used_pct,
            cents=r.cents,
            cached=r.cached,
            error=None if r.answers else f"http/attempts={r.attempts}",
        )
    agreement: dict[str, dict] = {}
    multi = len(verdicts) > 1
    for q in questions:
        values = {}
        for name, v in verdicts.items():
            a = v.answers.get(q) or {}
            if "noul" in a:
                values[name] = round(float(a["noul"]), 4)
            elif "choice" in a:
                values[name] = a["choice"]
            elif "score" in a:
                values[name] = a["score"]
        # single survivor of a multi-carrier ask is UNKNOWN, never
        # consensus: a dead second source must not read as agreement
        single = multi and len(values) <= 1
        if len({v for v in values.values() if not isinstance(v, float)}) > 1:
            agree = False
        elif len(values) > 1 and all(isinstance(v, float) for v in values.values()):
            agree = max(values.values()) - min(values.values()) <= AGREE_TOL
        else:
            agree = len(set(values.values())) <= 1
        entry: dict = {"agree": agree and not single, "values": values}
        if single:
            entry["single_source"] = True
        agreement[q] = entry
    return verdicts, agreement


# --------------------------------------------------------------------------- #
# CLI — probe a carrier, or A/B two carriers, from files (advisory)          #
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--carrier", default="mercury-decide", help="carrier id or raw model id")
    ap.add_argument(
        "--ab",
        metavar="CARRIER",
        help="comma-separated carriers to ask identically (A/B second source)",
    )
    ap.add_argument("--state-file", required=True, help="file whose text is the state")
    ap.add_argument("--questions-file", required=True, help="JSON: {name: question}")
    ap.add_argument("--out", help="append the result JSON to this JSONL")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument(
        "--max-cents", type=float, default=float(os.environ.get("OPENROUTER_MAX_CENTS", 0))
    )
    ap.add_argument("--timeout", type=float, default=60.0)
    args = ap.parse_args(argv)

    state = pathlib.Path(args.state_file).read_text(encoding="utf-8")
    questions = json.loads(pathlib.Path(args.questions_file).read_text(encoding="utf-8"))
    carriers = args.ab.split(",") if args.ab else [args.carrier]
    key = resolve_key()
    stats = RunStats(max_cents=args.max_cents)

    verdicts, agreement = ab(
        state,
        questions,
        carriers,
        key=key,
        use_cache=not args.no_cache,
        max_cents=args.max_cents,
    )
    for name, v in verdicts.items():
        print(
            f"--- {name} ({CARRIERS.get(name, Carrier(name)).id}) ok={v.ok} "
            f"wall={v.wall_s}s ctx={v.context_used_pct}% cached={v.cached}"
            + (f" err={v.error}" if v.error else "")
        )
        print(json.dumps(v.answers, indent=1))
    print("--- agreement")
    print(json.dumps(agreement, indent=1))
    print("--- stats")
    print(json.dumps(stats.as_dict(), indent=1))
    if args.out:
        line = json.dumps(
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "state_file": args.state_file,
                "carriers": {n: v.answers for n, v in verdicts.items()},
                "agreement": agreement,
                "stats": stats.as_dict(),
            }
        )
        with open(args.out, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        print(f"appended -> {args.out}")
    return 0 if any(v.ok for v in verdicts.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())


# --------------------------------------------------------------------------- #
# Decision briefs — the human-facing surface (plain English, never JSON)      #
# --------------------------------------------------------------------------- #
# A verdict only helps a human if it arrives as a QUESTION they can answer.
# These renderers turn carrier answers into a decision card: the question in
# plain English, each carrier's lean, whether they agree, and the context
# needed to decide. The operator should never have to read a probability
# distribution to know what is being asked.


def describe_answer(ans: dict) -> str:
    """One-line plain-English rendering of a typed answer."""
    if not ans:
        return "no answer"
    if "noul" in ans:
        p = float(ans["noul"])
        return f"{p * 100:.0f}% yes" if p >= 0.5 else f"{(1 - p) * 100:.0f}% no"
    if "choice" in ans:
        probs = ans.get("probabilities") or {}
        conf = f" ({max(probs.values()) * 100:.0f}%)" if probs else ""
        return f"{ans['choice']}{conf}"
    if "score" in ans:
        return str(ans["score"])
    return "no answer"


def gap_of(answers: dict[str, dict]) -> float:
    """Disagreement magnitude in [0,1] across carriers for one question.

    0.0 on a SINGLE carrier means unmeasured, not agreement — the
    unknown handling lives in ab() (single_source flag) and brief()
    (>1 carrier required to agree). Numeric callers must only feed it
    two-carrier maps (the triage surfaces enforce both-required)."""
    ps = [float(a["noul"]) for a in answers.values() if a and "noul" in a]
    if len(ps) > 1:
        return max(ps) - min(ps)
    picks = {a["choice"] for a in answers.values() if a and "choice" in a}
    if len(picks) > 1:
        return 1.0
    scores = {a["score"] for a in answers.values() if a and "score" in a}
    return 0.5 if len(scores) > 1 else 0.0


def brief(
    question: str,
    answers: dict[str, dict],
    *,
    context: str = "",
    contested: bool | None = None,
) -> str:
    """Render one decision card as plain English.

    ``answers`` maps carrier -> raw typed answer. When the carriers
    disagree (``contested`` unset, inferred from the gap) the card says so
    in the first line — the operator reads the disagreement before the
    numbers, because that is the information the human is there for.
    Agreement requires more than one carrier: a lone survivor renders
    contested (unknown, not consensus).
    """
    lines = []
    agree = (
        (len(answers) > 1 and gap_of(answers) <= AGREE_TOL) if contested is None else not contested
    )
    verdict = "the carriers AGREE" if agree else "the carriers DISAGREE — you decide"
    lines.append(f"{verdict}")
    lines.append("")
    lines.append(f"QUESTION: {question}")
    if context:
        lines.append("")
        lines.append(f"CONTEXT: {context}")
    lines.append("")
    lines.append("WHAT THE MODELS SAY (advisory only):")
    for carrier, ans in answers.items():
        lines.append(f"  - {carrier}: {describe_answer(ans)}")
    if not agree:
        lines.append("")
        lines.append(
            "They read the item differently — that disagreement is why this "
            "one is in front of you. The models do not get a vote on the write."
        )
    return "\n".join(lines)


def rank_contentious(
    per_item: dict[str, dict[str, dict]],
    *,
    top: int = 1,
) -> list[tuple[str, float]]:
    """Rank items by cross-carrier disagreement, worst first.

    ``per_item`` is ``{item_id: {carrier: raw answer}}``. Returns the
    ``top`` ids with a gap above AGREE_TOL — the shortlist a human should
    actually look at, instead of the whole queue.
    """
    scored = [(iid, g) for iid, ans in per_item.items() if (g := gap_of(ans)) > AGREE_TOL]
    scored.sort(key=lambda t: (-t[1], t[0]))
    return scored[:top]
