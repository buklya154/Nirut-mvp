# -*- coding: utf-8 -*-
"""
Pluggable LLM engine callers for the audit. Each function takes a Hebrew
prompt string and returns the model's raw text answer.

Every engine is optional and independent: if its API key env var isn't
set, `available_engines()` simply won't include it, and the audit runs
with whichever engines ARE configured (minimum: one). This means the
product can ship and be useful with just an Anthropic key, and grow
into a true multi-engine audit as more keys get added later.

`call_anthropic` has been tested against the live API with real billing
and confirmed working end-to-end (2026-08-24), against claude-sonnet-4-5.
The default was moved to claude-sonnet-5 on 2026-08-27 because Sonnet
4.5 retires 2026-09-29 (about a month out) - re-run the live test above
after this change, since a different model can shift audit scores even
though the code path is identical. `call_openai` and `call_gemini`'s
model defaults were checked against provider docs on 2026-08-27 and are
current as of that date - neither has been tested against a live key yet
in this project, since no OPENAI_API_KEY/GEMINI_API_KEY has been added.
Test each with a real key as soon as one is added; model names drift, so
re-check whichever provider's docs are current before trusting a default
here. `call_perplexity` is also untested.

--- Grounded (web-search) mode -------------------------------------------
Every call_* function takes a `grounded: bool` flag. False (default) is
the original behavior: a plain chat completion, answered purely from the
model's training data. True turns on that provider's live web-search
tool, so the answer reflects what a real user asking ChatGPT/Claude/
Gemini right now would actually see - closer to the real product promise
than a memory-only answer, at extra cost:

    Anthropic web_search:        ~$10 / 1,000 searches
    OpenAI web_search (Responses):~$10 / 1,000 calls (standard path)
    Gemini google_search grounding: ~$14 / 1,000 grounded queries (3.x)
    Perplexity "sonar" is ALWAYS web-grounded by nature of the product -
        the grounded flag is accepted for signature consistency but has
        no separate effect; ungrounded Perplexity doesn't exist.

A full audit fires (prompts x engines x runs_per_prompt) LLM calls - e.g.
7 prompts x 2 engines x 3 runs = 42 calls. If EVERY call does one search,
that's ~42 x $0.01-$0.014 =~ $0.42-$0.59 in search fees alone, on top of
normal token cost. GROUNDED_MAX_SEARCHES_PER_CALL below caps the
Anthropic tool to one search per call for exactly this reason - don't
raise it, and don't casually raise runs_per_prompt when grounded=True,
without redoing this math.
"""

import os
import time


class EngineError(Exception):
    pass


GROUNDED_MAX_SEARCHES_PER_CALL = 1


def _get_key(env_var: str):
    val = os.environ.get(env_var)
    return val if val else None


def call_anthropic(prompt: str, model: str = "claude-sonnet-5", grounded: bool = False) -> str:
    import anthropic
    key = _get_key("ANTHROPIC_API_KEY")
    if not key:
        raise EngineError("ANTHROPIC_API_KEY not set")
    client = anthropic.Anthropic(api_key=key)
    kwargs = {}
    if grounded:
        kwargs["tools"] = [{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": GROUNDED_MAX_SEARCHES_PER_CALL,
        }]
    resp = client.messages.create(
        model=model,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
        **kwargs,
    )
    # hasattr(..., "text") already skips tool-use / web-search-result
    # blocks that show up in the content list when grounded=True - only
    # the model's own text blocks have a `.text` attribute, so this line
    # didn't need to change for grounded mode.
    return "".join(block.text for block in resp.content if hasattr(block, "text"))


def call_openai(prompt: str, model: str = "gpt-5.6-terra", grounded: bool = False) -> str:
    import openai
    key = _get_key("OPENAI_API_KEY")
    if not key:
        raise EngineError("OPENAI_API_KEY not set")
    client = openai.OpenAI(api_key=key)
    if grounded:
        # OpenAI's generic web_search tool only exists on the Responses
        # API, not Chat Completions - a different method and response
        # shape, hence the separate branch instead of just adding a
        # `tools=` kwarg to the call below.
        resp = client.responses.create(
            model=model,
            tools=[{"type": "web_search"}],
            input=prompt,
        )
        return resp.output_text or ""
    resp = client.chat.completions.create(
        model=model,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content or ""


def call_gemini(prompt: str, model: str = "gemini-3.7-flash", grounded: bool = False) -> str:
    # Uses the `google-genai` package, not the older `google-generativeai`
    # ("google.generativeai") package this function used before - Google
    # deprecated that older SDK in favor of this one, and its search-
    # grounding tool for current (Gemini 2.0+/3.x) models is only wired
    # up here. See requirements.txt: google-genai replaced
    # google-generativeai for this reason.
    from google import genai
    from google.genai import types
    key = _get_key("GEMINI_API_KEY")
    if not key:
        raise EngineError("GEMINI_API_KEY not set")
    client = genai.Client(api_key=key)
    config = None
    if grounded:
        config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
        )
    resp = client.models.generate_content(model=model, contents=prompt, config=config)
    return resp.text or ""


def call_perplexity(prompt: str, model: str = "sonar", grounded: bool = False) -> str:
    # `grounded` is accepted but unused: Perplexity's sonar models are
    # always web-search-backed, so there's no separate "ungrounded" mode
    # to opt out of. Kept in the signature so run_prompt() can call every
    # engine the same way regardless of which one it's talking to.
    import requests
    key = _get_key("PERPLEXITY_API_KEY")
    if not key:
        raise EngineError("PERPLEXITY_API_KEY not set")
    r = requests.post(
        "https://api.perplexity.ai/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]


ENGINE_FUNCS = {
    "anthropic": call_anthropic,
    "openai": call_openai,
    "gemini": call_gemini,
    "perplexity": call_perplexity,
}

ENGINE_KEY_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
}


def available_engines():
    return [name for name, var in ENGINE_KEY_VARS.items() if _get_key(var)]


def run_prompt(engine: str, prompt: str, grounded: bool = False,
                retries: int = 2, backoff_seconds: float = 1.5) -> str:
    """Call one engine with basic retry on transient failure."""
    fn = ENGINE_FUNCS[engine]
    last_err = None
    for attempt in range(retries + 1):
        try:
            return fn(prompt, grounded=grounded)
        except EngineError:
            raise  # missing key - no point retrying
        except Exception as e:  # noqa: BLE001 - genuinely want to catch/retry anything transient
            last_err = e
            if attempt < retries:
                time.sleep(backoff_seconds * (attempt + 1))
    raise EngineError(f"{engine} failed after {retries + 1} attempts: {last_err}")
