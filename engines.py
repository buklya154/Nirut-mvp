# -*- coding: utf-8 -*-
"""
Pluggable LLM engine callers for the audit. Each function takes a Hebrew
prompt string and returns the model's raw text answer.

Every engine is optional and independent: if its API key env var isn't
set, `available_engines()` simply won't include it, and the audit runs
with whichever engines ARE configured (minimum: one). This means the
product can ship and be useful with just an Anthropic key, and grow
into a true multi-engine audit as more keys get added later.

None of this file has been tested against live APIs in this session
(no API keys are available in the build environment) - the HTTP call
shapes below follow each provider's current public API docs as of this
writing. Test with a real key before relying on this for a paying
customer, and pin exact model names to what's current when you deploy.
"""

import os
import time


class EngineError(Exception):
    pass


def _get_key(env_var: str):
    val = os.environ.get(env_var)
    return val if val else None


def call_anthropic(prompt: str, model: str = "claude-sonnet-4-5") -> str:
    import anthropic
    key = _get_key("ANTHROPIC_API_KEY")
    if not key:
        raise EngineError("ANTHROPIC_API_KEY not set")
    client = anthropic.Anthropic(api_key=key)
    resp = client.messages.create(
        model=model,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in resp.content if hasattr(block, "text"))


def call_openai(prompt: str, model: str = "gpt-4o") -> str:
    import openai
    key = _get_key("OPENAI_API_KEY")
    if not key:
        raise EngineError("OPENAI_API_KEY not set")
    client = openai.OpenAI(api_key=key)
    resp = client.chat.completions.create(
        model=model,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content or ""


def call_gemini(prompt: str, model: str = "gemini-2.0-flash") -> str:
    import google.generativeai as genai
    key = _get_key("GEMINI_API_KEY")
    if not key:
        raise EngineError("GEMINI_API_KEY not set")
    genai.configure(api_key=key)
    m = genai.GenerativeModel(model)
    resp = m.generate_content(prompt)
    return resp.text or ""


def call_perplexity(prompt: str, model: str = "sonar") -> str:
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


def run_prompt(engine: str, prompt: str, retries: int = 2, backoff_seconds: float = 1.5) -> str:
    """Call one engine with basic retry on transient failure."""
    fn = ENGINE_FUNCS[engine]
    last_err = None
    for attempt in range(retries + 1):
        try:
            return fn(prompt)
        except EngineError:
            raise  # missing key - no point retrying
        except Exception as e:  # noqa: BLE001 - genuinely want to catch/retry anything transient
            last_err = e
            if attempt < retries:
                time.sleep(backoff_seconds * (attempt + 1))
    raise EngineError(f"{engine} failed after {retries + 1} attempts: {last_err}")
