# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scoring import RunResult, analyze_response, summarize
from prompts import RECOMMENDATION_MARKERS, get_prompts


def test_analyze_response_detects_mention_and_recommendation():
    text = "אני ממליץ לבדוק את שיפוצי דורון, שמוכרים באזור לעבודות איכותיות."
    result = analyze_response("שיפוצי דורון", ["שיפוצי בר-אל"], text, RECOMMENDATION_MARKERS)
    assert result["mentioned"] is True
    assert result["recommended"] is True
    assert result["competitors_mentioned"] == []


def test_analyze_response_no_mention():
    text = "אני ממליץ לבדוק את שיפוצי בר-אל, שמוכרים באזור לעבודות איכותיות."
    result = analyze_response("שיפוצי דורון", ["שיפוצי בר-אל"], text, RECOMMENDATION_MARKERS)
    assert result["mentioned"] is False
    assert result["recommended"] is False
    assert result["competitors_mentioned"] == ["שיפוצי בר-אל"]


def test_analyze_response_late_mention_not_recommended():
    # Business name shows up, but buried far into a long answer with no
    # recommendation marker nearby - should count as mentioned but not
    # recommended.
    filler = "יש כמה אפשרויות באזור. " * 20
    text = filler + "יש גם עסק בשם שיפוצי דורון בעיר."
    result = analyze_response("שיפוצי דורון", [], text, RECOMMENDATION_MARKERS)
    assert result["mentioned"] is True
    assert result["recommended"] is False


def test_summarize_basic_math():
    results = [
        RunResult(engine="anthropic", prompt="p1", mentioned=True, recommended=True, competitors_mentioned=[]),
        RunResult(engine="anthropic", prompt="p1", mentioned=True, recommended=True, competitors_mentioned=[]),
        RunResult(engine="anthropic", prompt="p1", mentioned=False, recommended=False, competitors_mentioned=["X"]),
        RunResult(engine="anthropic", prompt="p2", mentioned=False, recommended=False, competitors_mentioned=["X"]),
    ]
    summary = summarize(results)
    assert summary.total_runs == 4
    assert summary.mention_rate == 0.5
    assert summary.recommendation_rate == 0.5
    # No reputation/consistency data supplied -> weight redistributed,
    # reputation/consistency components are None and contribute 0.
    assert summary.reputation_signal is None
    assert summary.information_consistency is None
    assert summary.weights_used["reputation"] == 0
    assert summary.weights_used["consistency"] == 0
    # score should be strictly between 0 and 100 and reproducible
    assert 0 < summary.findability_score < 100


def test_summarize_with_optional_reputation_data_uses_full_weights():
    results = [
        RunResult(engine="anthropic", prompt="p1", mentioned=True, recommended=True, competitors_mentioned=[]),
    ]
    summary = summarize(results, review_count=12, review_rating=4.3,
                         directories_checked=4, directories_consistent=2)
    assert summary.reputation_signal is not None
    assert summary.information_consistency == 0.5
    assert summary.weights_used["reputation"] == 20
    assert summary.weights_used["consistency"] == 15


def test_summarize_never_mentioned_scores_near_zero():
    # Regression test for a real bug found in live testing: a business
    # that is NEVER mentioned has a perfectly stable (zero-variance) "not
    # mentioned" result, which used to hand the stability weight in full
    # and produce a ~15/100 headline score for a genuinely 0%-visibility
    # business. That reads as a scoring bug to anyone who runs this exact
    # case - stability must be scaled by mention_rate so a true zero
    # reads as a true zero.
    results = [
        RunResult(engine="anthropic", prompt="p1", mentioned=False, recommended=False, competitors_mentioned=["X"]),
        RunResult(engine="anthropic", prompt="p1", mentioned=False, recommended=False, competitors_mentioned=["X"]),
        RunResult(engine="anthropic", prompt="p2", mentioned=False, recommended=False, competitors_mentioned=["X"]),
        RunResult(engine="anthropic", prompt="p2", mentioned=False, recommended=False, competitors_mentioned=["X"]),
    ]
    summary = summarize(results)
    assert summary.mention_rate == 0.0
    assert summary.stability == 1.0  # still reported raw - "consistent" is true
    assert summary.findability_score == 0.0  # but must not leak into the headline score


def test_summarize_always_mentioned_unaffected_by_stability_fix():
    # A business that's consistently mentioned should score the same as
    # before the fix - the mention_rate=1 case leaves stability's
    # contribution untouched.
    results = [
        RunResult(engine="anthropic", prompt="p1", mentioned=True, recommended=True, competitors_mentioned=[]),
        RunResult(engine="anthropic", prompt="p1", mentioned=True, recommended=True, competitors_mentioned=[]),
    ]
    summary = summarize(results)
    assert summary.mention_rate == 1.0
    assert summary.stability == 1.0
    assert summary.findability_score == 100.0


def test_analyze_response_matches_alias_when_primary_spelling_absent():
    # Real case from live testing: "Batumi" (Latin) typed by the owner,
    # but the Hebrew AI answer names it "באטומי" - only matches via alias.
    text = "מסעדה מומלצת בנתניה היא באטומי, מסעדה גאורגית ידועה."
    result = analyze_response("Batumi", [], text, RECOMMENDATION_MARKERS,
                               business_name_aliases=["באטומי"])
    assert result["mentioned"] is True


def test_summarize_empty_raises():
    try:
        summarize([])
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_get_prompts_renovation():
    prompts = get_prompts("renovation", "נתניה")
    assert len(prompts) == 7
    assert all("נתניה" in p for p in prompts)


def test_get_prompts_generic_requires_label():
    # Without a custom label, generic templates that need {category} are skipped.
    prompts = get_prompts("generic", "נתניה")
    assert prompts == []
    prompts_with_label = get_prompts("generic", "נתניה", custom_category_label="מספרה")
    assert len(prompts_with_label) == 4
    assert all("מספרה" in p for p in prompts_with_label)
