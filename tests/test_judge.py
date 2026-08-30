# -*- coding: utf-8 -*-
"""
Tests for judge.py.

Every test monkeypatches `judge._call_judge_model`. NO TEST MAY HIT THE
NETWORK — that is why `_call_judge_model` is a separate one-line-ish
function in the first place.

Scope note: these tests prove the *plumbing* — prompt construction, parsing,
coercion, and graceful failure. They do NOT prove the judge is more accurate
than the regex, because a mocked model returning the answer the test wants is
circular. Real accuracy needs the hand-labelled set described in
tests/fixtures/README.md (SPEC §5), scored with tools/eval_judge.py.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import judge
from prompts import RECOMMENDATION_MARKERS
from scoring import analyze_response

FIXTURES = json.load(open(
    os.path.join(os.path.dirname(__file__), "fixtures", "hebrew_answer_shapes.json"),
    encoding="utf-8",
))["fixtures"]


def _fixture(name):
    return next(f for f in FIXTURES if f["name"] == name)


def _judge_returning(monkeypatch, payload, calls=None):
    """Point the judge at a canned model response."""
    def fake(prompt):
        if calls is not None:
            calls.append(prompt)
        return payload
    monkeypatch.setattr(judge, "_call_judge_model", fake)


def _never_called(monkeypatch, calls):
    def fake(prompt):
        calls.append(prompt)
        raise AssertionError("model should not have been called")
    monkeypatch.setattr(judge, "_call_judge_model", fake)


# --- parsing ---------------------------------------------------------------

def test_valid_json_response_is_parsed(monkeypatch):
    _judge_returning(monkeypatch, json.dumps({
        "status": "top_pick",
        "matched_as": "אאוצ'ד (OCD)",
        "businesses_named": ["הרברט סמואל", "אוזה"],
    }))
    r = judge.judge_response("some answer", "אאוצ'ד")
    assert r.ok is True
    assert r.error is None
    assert r.status == "top_pick"
    assert r.matched_as == "אאוצ'ד (OCD)"
    assert r.businesses_named == ["הרברט סמואל", "אוזה"]


def test_json_wrapped_in_code_fence_is_unwrapped(monkeypatch):
    _judge_returning(monkeypatch,
                     '```json\n{"status": "listed", "matched_as": "אאוצ\'ד", '
                     '"businesses_named": []}\n```')
    r = judge.judge_response("some answer", "אאוצ'ד")
    assert r.ok is True
    assert r.status == "listed"
    assert r.matched_as == "אאוצ'ד"


def test_prose_garbage_returns_not_ok_without_raising(monkeypatch):
    _judge_returning(monkeypatch, "I think this business is probably recommended!")
    r = judge.judge_response("some answer", "אאוצ'ד")
    assert r.ok is False
    assert r.error
    assert r.status == "absent"          # safe shape, but caller must ignore it
    assert r.businesses_named == []


def test_unrecognised_status_returns_not_ok(monkeypatch):
    _judge_returning(monkeypatch, json.dumps({
        "status": "definitely_recommended",       # not one of the three
        "matched_as": "אאוצ'ד",
        "businesses_named": [],
    }))
    r = judge.judge_response("some answer", "אאוצ'ד")
    assert r.ok is False
    assert "definitely_recommended" in r.error


def test_businesses_named_is_deduped_and_blanks_dropped(monkeypatch):
    _judge_returning(monkeypatch, json.dumps({
        "status": "listed",
        "matched_as": None,
        "businesses_named": ["אוזה", "  ", "אוזה", "", "הרברט סמואל",
                             "אוזה  ", None, 42, "הצמרת"],
    }))
    r = judge.judge_response("some answer", "אאוצ'ד")
    assert r.ok is True
    assert r.businesses_named == ["אוזה", "הרברט סמואל", "הצמרת"]


def test_businesses_named_is_capped(monkeypatch):
    _judge_returning(monkeypatch, json.dumps({
        "status": "listed", "matched_as": None,
        "businesses_named": ["מסעדה %d" % i for i in range(40)],
    }))
    r = judge.judge_response("some answer", "אאוצ'ד")
    assert len(r.businesses_named) == judge.MAX_BUSINESSES_NAMED


def test_blank_matched_as_becomes_none(monkeypatch):
    _judge_returning(monkeypatch, json.dumps({
        "status": "absent", "matched_as": "   ", "businesses_named": [],
    }))
    r = judge.judge_response("some answer", "אאוצ'ד")
    assert r.ok is True
    assert r.matched_as is None


# --- failure modes ---------------------------------------------------------

def test_exception_inside_model_call_is_captured(monkeypatch):
    def boom(prompt):
        raise RuntimeError("connection reset")
    monkeypatch.setattr(judge, "_call_judge_model", boom)
    r = judge.judge_response("some answer", "אאוצ'ד")
    assert r.ok is False
    assert "connection reset" in r.error


def test_non_string_model_output_does_not_raise(monkeypatch):
    # judge_response runs inside app.py's thread-pool worker, which only
    # catches EngineError — anything else escaping would 500 the whole audit.
    monkeypatch.setattr(judge, "_call_judge_model", lambda prompt: None)
    r = judge.judge_response("some answer", "אאוצ'ד")
    assert r.ok is False


def test_malformed_aliases_do_not_raise(monkeypatch):
    monkeypatch.setattr(judge, "_call_judge_model", lambda prompt: "{}")
    r = judge.judge_response("some answer", "אאוצ'ד", aliases=12345)
    assert r.ok is False


def test_disabled_judge_makes_no_call(monkeypatch):
    calls = []
    _never_called(monkeypatch, calls)
    monkeypatch.setattr(judge, "JUDGE_ENABLED", False)
    r = judge.judge_response("some answer", "אאוצ'ד")
    assert r.ok is False
    assert calls == []
    assert "disabled" in r.error


def test_empty_answer_text_makes_no_call(monkeypatch):
    calls = []
    _never_called(monkeypatch, calls)
    r = judge.judge_response("   ", "אאוצ'ד")
    assert r.ok is False
    assert calls == []


# --- prompt construction ---------------------------------------------------

def test_prompt_contains_answer_text_and_survives_literal_braces(monkeypatch):
    """The prompt template ends with a literal JSON example containing { and }.
    str.format would raise on it; this pins that the substitution doesn't."""
    calls = []
    _judge_returning(monkeypatch, json.dumps(
        {"status": "absent", "matched_as": None, "businesses_named": []}), calls)
    judge.judge_response("תשובה בעברית", "אאוצ'ד", ["OCD"], "מסעדה", "תל אביב")
    assert len(calls) == 1
    prompt = calls[0]
    assert "תשובה בעברית" in prompt
    assert "אאוצ'ד" in prompt
    assert "OCD" in prompt
    assert "מסעדה" in prompt
    assert "תל אביב" in prompt
    assert '{"status": "...", "matched_as": "..."' in prompt   # example intact


def test_missing_aliases_render_as_none_placeholder(monkeypatch):
    calls = []
    _judge_returning(monkeypatch, json.dumps(
        {"status": "absent", "matched_as": None, "businesses_named": []}), calls)
    judge.judge_response("text", "אאוצ'ד", None, "", "")
    assert "(none)" in calls[0]


# --- the answer shapes the regex gets wrong --------------------------------

def test_regex_baseline_fails_on_list_format_answer():
    """No model involved. This pins the ACTUAL P1 bug: a business sitting
    under a 'מומלצות' heading, after a hedging preamble, is not detected as
    recommended by the regex. If someone 'fixes' the regex later, this test
    tells them the fallback's behaviour changed."""
    f = _fixture("list_format_under_recommended_heading")
    got = analyze_response(f["business_name"], [], f["text"],
                           RECOMMENDATION_MARKERS, business_name_aliases=f["aliases"])
    assert got["mentioned"] is True
    assert got["recommended"] is False        # ...but it IS a recommendation


def test_regex_baseline_false_positives_on_similar_name():
    """Also no model. The substring match reports a business that is not
    actually there: 'אאוצ'ד' is a substring of the different business
    'אאוצ'דו בר'. Documented because §4.2 ORs the substring match with the
    judge, so the judge cannot correct this particular error."""
    f = _fixture("similar_name_different_business")
    got = analyze_response(f["business_name"], [], f["text"],
                           RECOMMENDATION_MARKERS, business_name_aliases=f["aliases"])
    assert got["mentioned"] is True           # wrong: it's a different business


@pytest.mark.parametrize("fixture_name", [f["name"] for f in FIXTURES])
def test_judge_pipeline_passes_fixture_text_through(monkeypatch, fixture_name):
    """Wiring only: the fixture's full text reaches the model, and the model's
    verdict comes back out intact. Whether the model gets the verdict RIGHT is
    what the hand-labelled set in SPEC §5 measures — not this."""
    f = _fixture(fixture_name)
    calls = []
    _judge_returning(monkeypatch, json.dumps({
        "status": f["expected_status"],
        "matched_as": f["business_name"] if f["expected_status"] != "absent" else None,
        "businesses_named": [],
    }), calls)
    r = judge.judge_response(f["text"], f["business_name"], f["aliases"],
                             f["category_label"], f["city"])
    assert r.ok is True
    assert r.status == f["expected_status"]
    assert f["text"] in calls[0]
