# -*- coding: utf-8 -*-
"""
Integration tests for the /api/audit route - specifically that it
forwards the `grounded` request flag through to engines.run_prompt(), and
that error responses stay JSON. Real engine calls are monkeypatched out
so this never touches a real, billed API.

The DB path is redirected to a temp file (below) so running this suite
doesn't create/pollute a real nirut.db in the repo.
"""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# A FRESH db per test session. Using a fixed path meant every run appended to
# the same `audits` table, so `_audits_today_count()` crept toward
# MAX_AUDITS_PER_DAY and eventually 429'd the whole suite on a busy day.
os.environ["NIRUT_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="nirut_test_"), "nirut.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-real")

import app as app_module  # noqa: E402  (env vars above must be set first)

# The per-IP rate limit (5/hour) would otherwise 429 the later tests in this
# file, since every test client shares one address. Rate limiting itself is
# covered by test_rate_limit_response_is_json below, which re-enables it.
app_module.limiter.enabled = False


@pytest.fixture
def rate_limited():
    """Turn the limiter back on for the one test that needs it."""
    app_module.limiter.enabled = True
    app_module.limiter.reset()
    yield
    app_module.limiter.enabled = False


def test_audit_forwards_grounded_flag_to_every_engine_call(monkeypatch):
    seen_grounded = []

    def fake_run_prompt(engine_name, prompt, grounded=False, **kwargs):
        seen_grounded.append(grounded)
        return "ממליצים בחום על העסק שלכם, הכי טוב באזור."

    monkeypatch.setattr(app_module.engines, "run_prompt", fake_run_prompt)
    client = app_module.app.test_client()

    resp = client.post("/api/audit", json={
        "business_name": "העסק שלכם",
        "city": "תל אביב",
        "category": "restaurant",
        "runs_per_prompt": 1,
        "grounded": True,
    })

    assert resp.status_code == 200
    assert resp.is_json
    assert resp.get_json()["grounded"] is True
    assert seen_grounded  # at least one engine call happened
    assert all(seen_grounded), "every engine call should have run grounded=True"


def test_audit_defaults_to_ungrounded_when_flag_omitted(monkeypatch):
    seen_grounded = []
    monkeypatch.setattr(
        app_module.engines, "run_prompt",
        lambda engine_name, prompt, grounded=False, **kwargs: seen_grounded.append(grounded) or "ok",
    )
    client = app_module.app.test_client()

    resp = client.post("/api/audit", json={
        "business_name": "עסק בדיקה",
        "city": "חיפה",
        "category": "restaurant",
        "runs_per_prompt": 1,
    })

    assert resp.status_code == 200
    assert resp.get_json()["grounded"] is False
    assert all(g is False for g in seen_grounded)


def _stub_engine(monkeypatch, text):
    monkeypatch.setattr(
        app_module.engines, "run_prompt",
        lambda engine_name, prompt, grounded=False, **kwargs: text,
    )


def test_audit_uses_judge_verdict_for_recommended(monkeypatch):
    # The whole point of P1: an answer the regex would score as "not
    # recommended" (business named after a preamble, far from any marker)
    # is scored as recommended when the judge says top_pick.
    _stub_engine(monkeypatch, "קשה לקבוע. " + ("מילים נוספות כאן. " * 20) + "אאוצ'ד קיימת.")
    monkeypatch.setattr(app_module.judge, "judge_response",
                        lambda *a, **k: app_module.judge.JudgeResult(
                            status="top_pick", matched_as="אאוצ'ד",
                            businesses_named=["אוזה"], ok=True, error=None))

    resp = app_module.app.test_client().post("/api/audit", json={
        "business_name": "אאוצ'ד", "city": "תל אביב",
        "category": "restaurant", "runs_per_prompt": 1,
    })

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["summary"]["recommendation_rate"] == 1.0
    assert body["summary"]["top_pick_rate"] == 1.0
    assert body["summary"]["judge_coverage"] == 1.0
    assert body["summary"]["named_competitor_counts"] == {"אוזה": 4}
    assert all(r["status"] == "top_pick" for r in body["runs"])
    assert all(r["matched_as"] == "אאוצ'ד" for r in body["runs"])


def test_audit_falls_back_to_regex_when_judge_fails(monkeypatch):
    # Judge outage must degrade to exactly today's behaviour, not break the
    # audit. This text IS caught by the regex (name in the first 150 chars).
    _stub_engine(monkeypatch, "אאוצ'ד היא מסעדה מומלצת מאוד בתל אביב.")
    monkeypatch.setattr(app_module.judge, "judge_response",
                        lambda *a, **k: app_module.judge.JudgeResult(
                            status="absent", matched_as=None, businesses_named=[],
                            ok=False, error="judge call failed: boom"))

    resp = app_module.app.test_client().post("/api/audit", json={
        "business_name": "אאוצ'ד", "city": "תל אביב",
        "category": "restaurant", "runs_per_prompt": 1,
    })

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["summary"]["mention_rate"] == 1.0
    assert body["summary"]["recommendation_rate"] == 1.0   # from the regex
    assert body["summary"]["judge_coverage"] == 0.0
    assert all(r["judged"] is False for r in body["runs"])
    assert all(r["matched_as"] is None for r in body["runs"])


def test_audit_judge_never_removes_a_substring_mention(monkeypatch):
    # §4.2: `mentioned` is OR-ed so a judge false-negative can't silently
    # zero a real business. Judge says absent, substring says present.
    _stub_engine(monkeypatch, "אאוצ'ד היא מסעדה בתל אביב.")
    monkeypatch.setattr(app_module.judge, "judge_response",
                        lambda *a, **k: app_module.judge.JudgeResult(
                            status="absent", matched_as=None, businesses_named=[],
                            ok=True, error=None))

    resp = app_module.app.test_client().post("/api/audit", json={
        "business_name": "אאוצ'ד", "city": "תל אביב",
        "category": "restaurant", "runs_per_prompt": 1,
    })

    body = resp.get_json()
    assert body["summary"]["mention_rate"] == 1.0        # kept
    assert body["summary"]["recommendation_rate"] == 0.0  # judge wins here
    assert all(r["status"] == "listed" for r in body["runs"])


def test_audit_share_of_voice_null_rather_than_100_percent(monkeypatch):
    _stub_engine(monkeypatch, "אאוצ'ד היא מסעדה מומלצת בתל אביב.")
    monkeypatch.setattr(app_module.judge, "judge_response",
                        lambda *a, **k: app_module.judge.JudgeResult(
                            status="top_pick", matched_as="אאוצ'ד",
                            businesses_named=[], ok=True, error=None))

    resp = app_module.app.test_client().post("/api/audit", json={
        "business_name": "אאוצ'ד", "city": "תל אביב",
        "category": "restaurant", "runs_per_prompt": 1,
    })

    assert resp.get_json()["summary"]["share_of_voice"] is None


def test_audit_missing_required_fields_returns_json_400():
    client = app_module.app.test_client()
    resp = client.post("/api/audit", json={})
    assert resp.status_code == 400
    assert resp.is_json
    assert "error" in resp.get_json()


def test_rate_limit_response_is_json(monkeypatch, rate_limited):
    # CLAUDE.md calls this out specifically: flask-limiter's default 429 is an
    # HTML page, and the frontend calls .json() on every response. Pinned here
    # so a limiter upgrade can't silently reintroduce the HTML body.
    _stub_engine(monkeypatch, "אאוצ'ד היא מסעדה בתל אביב.")
    monkeypatch.setattr(app_module.judge, "judge_response",
                        lambda *a, **k: app_module.judge.JudgeResult(
                            status="listed", matched_as="אאוצ'ד",
                            businesses_named=[], ok=True, error=None))
    client = app_module.app.test_client()
    body = {"business_name": "אאוצ'ד", "city": "תל אביב",
            "category": "restaurant", "runs_per_prompt": 1}

    statuses = [client.post("/api/audit", json=body).status_code for _ in range(7)]
    assert 429 in statuses, "rate limit never triggered"

    limited = client.post("/api/audit", json=body)
    assert limited.status_code == 429
    assert limited.is_json
    assert "error" in limited.get_json()


def test_unknown_route_returns_json_not_html_error_page():
    # Regression test: handle_unexpected_error() used to re-return
    # HTTPException instances (404, 405, ...) unchanged, which is what
    # produces Flask's default HTML error page for exactly those status
    # codes - a real gap in the "every response must be JSON" rule, found
    # by actually hitting a route that doesn't exist rather than assuming
    # the existing 429/500 handlers covered everything.
    client = app_module.app.test_client()
    resp = client.get("/this-route-does-not-exist")
    assert resp.status_code == 404
    assert resp.is_json
    assert "error" in resp.get_json()
