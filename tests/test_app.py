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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("NIRUT_DB_PATH", os.path.join(tempfile.gettempdir(), "nirut_test.db"))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-real")

import app as app_module  # noqa: E402  (env vars above must be set first)


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


def test_audit_missing_required_fields_returns_json_400():
    client = app_module.app.test_client()
    resp = client.post("/api/audit", json={})
    assert resp.status_code == 400
    assert resp.is_json
    assert "error" in resp.get_json()


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
