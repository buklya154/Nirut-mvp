# -*- coding: utf-8 -*-
"""
Unit tests for engines.py's engine-selection and grounded-flag plumbing.

No real network calls here on purpose (same philosophy as
tests/test_scoring.py) - these tests fake out ENGINE_FUNCS so they run
instantly and never touch a real, billed API.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import engines


def test_run_prompt_passes_grounded_true_through(monkeypatch):
    calls = []

    def fake_fn(prompt, grounded=False):
        calls.append((prompt, grounded))
        return "ok"

    monkeypatch.setitem(engines.ENGINE_FUNCS, "anthropic", fake_fn)
    result = engines.run_prompt("anthropic", "hello", grounded=True)
    assert result == "ok"
    assert calls == [("hello", True)]


def test_run_prompt_defaults_to_ungrounded(monkeypatch):
    calls = []

    def fake_fn(prompt, grounded=False):
        calls.append(grounded)
        return "ok"

    monkeypatch.setitem(engines.ENGINE_FUNCS, "anthropic", fake_fn)
    engines.run_prompt("anthropic", "hi")
    assert calls == [False]


def test_run_prompt_retries_then_raises_engine_error(monkeypatch):
    attempts = []

    def flaky_fn(prompt, grounded=False):
        attempts.append(1)
        raise RuntimeError("transient network blip")

    monkeypatch.setitem(engines.ENGINE_FUNCS, "anthropic", flaky_fn)
    try:
        engines.run_prompt("anthropic", "hi", retries=1, backoff_seconds=0)
        assert False, "expected EngineError"
    except engines.EngineError:
        pass
    assert len(attempts) == 2  # initial attempt + 1 retry


def test_available_engines_reflects_env_vars(monkeypatch):
    for var in engines.ENGINE_KEY_VARS.values():
        monkeypatch.delenv(var, raising=False)
    assert engines.available_engines() == []

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert engines.available_engines() == ["anthropic"]
