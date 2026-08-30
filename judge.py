# -*- coding: utf-8 -*-
"""
LLM judge for the Nir'ut audit — replaces the regex "recommended" detector.

WHY THIS EXISTS
---------------
`scoring.analyze_response()` decided `recommended` with two text rules: the
business name appearing in the first 150 characters, or a recommendation
marker word within +/-40 characters of the name. Both fail structurally on
how Hebrew LLM answers are actually shaped:

- Real answers open with a hedging preamble ("קשה לקבוע 'הכי טובה' באופן
  אובייקטיבי כי זה תלוי בטעם אישי…"), which always consumes the first 150
  characters. That rule effectively never fires in production — only in unit
  tests, whose fixtures have no preamble.
- Real answers are markdown category lists. The verb that governs the list
  ("מסעדות שנחשבות לטובות ומומלצות:") sits ABOVE the list, while the +/-40
  character window around the business name contains the category header
  ("מטבח ים תיכוני —"). Real recommendations are missed; conversely a name
  buried 14th in a list can sit near a stray "מומלץ" and false-positive.

The live OCD audit reported recommendation_rate 8% (1/12) while its own
excerpts showed OCD presented positively several times. That 8% was an
artifact of the detector, not a measurement of the business.

DESIGN
------
One cheap model call per engine response, returning structured JSON. This
module owns the ONLY network call; `scoring.py` stays network-free (that
separation is deliberate and stated in its docstring — keep it).

The regex path in `scoring.analyze_response()` is NOT deleted. It remains the
fallback whenever a judge call fails, is disabled, or returns something
unparseable — see `app.py`'s `_do()`. A judge outage degrades the audit to
exactly today's behaviour rather than breaking it.

COST
----
Judged with claude-haiku-4-5 ($1.00 / 1M input, $5.00 / 1M output — verified
against Anthropic's pricing 2026-08-30; still the cheapest Haiku-class model).
One call per run, roughly 600 input / 60 output tokens:

    12-run audit = 12 calls ~= 7,200 in + 720 out ~= $0.011 per audit

That is about a cent — an order of magnitude below the $0.06-0.15 estimated
in the spec, and negligible against the engine calls already being billed
(and far below grounded search, see `engines.py`). The existing
`MAX_AUDITS_PER_DAY` cap already bounds total spend; no new cap needed.
"""

import json
import os
import re
from typing import List, NamedTuple, Optional

# Read at import time: on Render these are set before the process starts.
# Tests monkeypatch the module attributes directly rather than the env.
JUDGE_ENABLED = os.environ.get("NIRUT_JUDGE_ENABLED", "1") not in ("0", "false", "False")
JUDGE_MODEL = os.environ.get("NIRUT_JUDGE_MODEL", "claude-haiku-4-5")
try:
    JUDGE_TIMEOUT = float(os.environ.get("NIRUT_JUDGE_TIMEOUT", "20"))
except (TypeError, ValueError):
    # A typo'd env var must not take the whole app down at import time.
    JUDGE_TIMEOUT = 20.0

MAX_BUSINESSES_NAMED = 15
VALID_STATUSES = ("top_pick", "listed", "absent")


class JudgeResult(NamedTuple):
    status: str                      # "top_pick" | "listed" | "absent"
    matched_as: Optional[str]
    businesses_named: List[str]
    ok: bool                         # False => caller must use the regex fallback
    error: Optional[str]


def _failed(reason: str) -> JudgeResult:
    """Every failure returns the same shape, with ok=False. Callers branch on
    `ok`, never on the status, when the judge didn't work."""
    return JudgeResult(status="absent", matched_as=None, businesses_named=[],
                       ok=False, error=reason)


JUDGE_PROMPT = """You are evaluating one answer produced by an AI assistant in response to a
consumer question about local businesses. The answer is usually in Hebrew.

BUSINESS: {business_name}
KNOWN ALTERNATE SPELLINGS: {aliases}
CATEGORY: {category_label}
CITY: {city}

ANSWER TEXT:
<<<
{text}
>>>

Decide how the answer treats BUSINESS, and list every other real business
the answer names.

status:
- "top_pick" - the answer presents BUSINESS as a recommendation: named as
               the best or a recommended choice, singled out positively, or
               included in a list the answer introduces as recommended /
               best / highly regarded.
- "listed"   - BUSINESS is named only neutrally: an example, an aside, a
               "there are also..." mention, or a member of a list the
               answer does NOT frame as a recommendation.
- "absent"   - BUSINESS is not named at all. A DIFFERENT business with a
               similar name does not count as present.

Rules:
- Judge only what this text says. Do not use outside knowledge.
- A hedging preamble ("it is hard to say objectively", "it depends on
  personal taste") does NOT by itself prevent "top_pick". What matters is
  how BUSINESS itself is framed.
- Hebrew and Latin spellings of the same name are the same business.
- businesses_named: every OTHER business named as somewhere a consumer
  could actually go, written exactly as it appears in the text. Exclude
  BUSINESS itself. Exclude directories, review sites and aggregators
  (Google, Google Maps, Waze, Zap, דפי זהב, TripAdvisor, Facebook, Rest).
  Exclude neighbourhoods, streets, dishes and generic category words.
- matched_as: the exact substring that names BUSINESS, or null if absent.

Return ONLY a JSON object. No prose, no markdown code fence.
{"status": "...", "matched_as": "...", "businesses_named": ["...", "..."]}"""


def _build_prompt(text: str, business_name: str, aliases, category_label, city) -> str:
    """Fill the prompt template.

    Uses str.replace, NOT str.format: the template's last line is a literal
    JSON example containing { and }, which str.format would try to parse as
    format fields and raise on. The spec requires the prompt verbatim, so the
    substitution has to leave those braces alone.
    """
    alias_str = ", ".join(a for a in (aliases or []) if a and a.strip()) or "(none)"
    return (
        JUDGE_PROMPT
        .replace("{business_name}", business_name or "")
        .replace("{aliases}", alias_str)
        .replace("{category_label}", (category_label or "").strip() or "(unspecified)")
        .replace("{city}", city or "")
        .replace("{text}", text or "")
    )


def _call_judge_model(prompt: str) -> str:
    """The ONLY network call in this module — deliberately tiny so tests can
    monkeypatch it and never touch the network.

    Same convention as `engines.py`: a missing key raises, and the caller
    (`judge_response`) degrades gracefully rather than propagating.
    """
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    client = anthropic.Anthropic(api_key=key, timeout=JUDGE_TIMEOUT)
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=300,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in resp.content if hasattr(block, "text"))


def _strip_code_fence(raw: str) -> Optional[str]:
    """Return the contents of a ```json ... ``` fence, or None if there is none.

    The prompt asks for bare JSON, but models wrap output in a fence often
    enough that treating it as a hard failure would throw away good answers.
    """
    match = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    return match.group(1).strip() if match else None


def _clean_businesses_named(value) -> List[str]:
    """Coerce to a list of non-empty strings, de-duplicated case-insensitively
    (first spelling wins), capped at MAX_BUSINESSES_NAMED."""
    if not isinstance(value, list):
        return []
    cleaned, seen = [], set()
    for item in value:
        if not isinstance(item, str):
            continue
        name = item.strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(name)
        if len(cleaned) >= MAX_BUSINESSES_NAMED:
            break
    return cleaned


def _parse_judge_output(raw: str) -> JudgeResult:
    data = None
    for candidate in (raw.strip(), _strip_code_fence(raw)):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
            break
        except (ValueError, TypeError):
            continue

    if not isinstance(data, dict):
        return _failed("judge returned unparseable output")

    status = data.get("status")
    if status not in VALID_STATUSES:
        return _failed("judge returned unrecognised status: %r" % (status,))

    matched_as = data.get("matched_as")
    if not isinstance(matched_as, str) or not matched_as.strip():
        matched_as = None
    else:
        matched_as = matched_as.strip()

    return JudgeResult(
        status=status,
        matched_as=matched_as,
        businesses_named=_clean_businesses_named(data.get("businesses_named")),
        ok=True,
        error=None,
    )


def judge_response(text: str, business_name: str, aliases=None,
                   category_label: str = "", city: str = "") -> JudgeResult:
    """Judge one engine answer. Never raises — a failure returns ok=False and
    the caller falls back to the regex path."""
    if not JUDGE_ENABLED:
        return _failed("judge disabled via NIRUT_JUDGE_ENABLED")
    if not (text or "").strip():
        return _failed("empty answer text")

    # Everything below is inside the guard, not just the network call: this
    # function is called from inside app.py's ThreadPoolExecutor worker, which
    # only catches EngineError. Anything else escaping here would fail the
    # whole audit with a 500 — a judge problem must degrade to the regex
    # fallback, never take the request down.
    try:
        prompt = _build_prompt(text, business_name, aliases, category_label, city)
        raw = _call_judge_model(prompt)
        return _parse_judge_output(raw)
    except Exception as e:  # noqa: BLE001 - any failure must degrade, not raise
        return _failed("judge call failed: %s" % (e,))
