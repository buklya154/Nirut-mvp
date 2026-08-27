# -*- coding: utf-8 -*-
"""
Pure scoring logic for the Nir'ut AI-Findability audit.

No network calls in this file on purpose — it's the part of the product
that's cheapest to get right and easiest to unit-test, so it's kept
completely separate from the LLM-calling code in engines.py.

Scoring methodology mirrors the previously-validated formula (see
operating-blueprint-local-findability.md Part 9), with one honest change:
Reputation Signal and Information Consistency need external data (Google
reviews, directory listings) that this MVP does not fetch automatically
(that requires paid, keyed APIs — Google Places, directory scraping).
Instead they're OPTIONAL self-reported inputs. If the business owner
supplies them, the original 5-factor weighting is used. If not, the
weight is redistributed proportionally across the three factors the
audit can always measure on its own (Mention Rate, Recommendation Rate,
Stability), so the score never silently understates a business that
just didn't fill in two extra fields.
"""

from dataclasses import dataclass, field
from statistics import pvariance
from typing import List, Optional


@dataclass
class RunResult:
    """One single prompt-run result from one engine."""
    engine: str
    prompt: str
    mentioned: bool
    recommended: bool
    competitors_mentioned: List[str] = field(default_factory=list)
    raw_text: str = ""


def _normalize(name: str) -> str:
    return " ".join(name.strip().split()).casefold()


def analyze_response(business_name: str, competitor_names: List[str],
                      text: str, recommendation_markers: List[str],
                      business_name_aliases: List[str] = None) -> dict:
    """Decide whether `text` mentions the business, whether that mention
    reads as a recommendation, and which named competitors also appear.

    Heuristics (documented, not hidden):
    - "mentioned": the business name (case/whitespace-insensitive) appears
      anywhere in the response text — OR any of `business_name_aliases`
      does. Aliases exist because a real, live test found a real business
      (a Georgian restaurant, "Batumi") whose owner might type the Latin
      spelling while a Hebrew-language AI answer names it in Hebrew script
      ("באטומי") — an exact-substring match against only the primary
      spelling would silently miss a real mention. Callers should pass in
      known alternate spellings/transliterations when available.
    - "recommended": the mention either falls in the first 150 characters
      of the response (i.e. it's a headline answer, not a footnote) OR
      appears within 40 characters of a recommendation marker word.
    - competitor mentions are matched the same way as business mentions,
      one at a time.
    """
    norm_text = text.casefold()
    norm_name = _normalize(business_name)
    all_names = [norm_name] + [_normalize(a) for a in (business_name_aliases or []) if a and a.strip()]

    mentioned = False
    idx = -1
    matched_len = len(norm_name)
    for name in all_names:
        if not name:
            continue
        found_idx = norm_text.find(name)
        if found_idx != -1:
            mentioned = True
            idx = found_idx
            matched_len = len(name)
            break  # first alias that hits is enough to score "mentioned"

    recommended = False
    if mentioned:
        if idx != -1 and idx < 150:
            recommended = True
        if not recommended:
            window = norm_text[max(0, idx - 40): idx + matched_len + 40]
            if any(_normalize(m) in window for m in recommendation_markers):
                recommended = True

    competitors_found = []
    for c in competitor_names:
        if not c:
            continue
        if _normalize(c) in norm_text:
            competitors_found.append(c)

    return {
        "mentioned": mentioned,
        "recommended": recommended,
        "competitors_mentioned": competitors_found,
    }


@dataclass
class AuditSummary:
    total_runs: int
    mention_rate: float
    recommendation_rate: float
    stability: float
    share_of_voice: float
    competitor_mention_rates: dict
    reputation_signal: Optional[float]
    information_consistency: Optional[float]
    findability_score: float
    weights_used: dict


def summarize(results: List[RunResult], review_count: Optional[int] = None,
              review_rating: Optional[float] = None,
              directories_checked: Optional[int] = None,
              directories_consistent: Optional[int] = None) -> AuditSummary:
    total = len(results)
    if total == 0:
        raise ValueError("summarize() called with zero runs")

    mention_rate = sum(1 for r in results if r.mentioned) / total
    recommendation_rate = sum(1 for r in results if r.recommended) / total

    # Stability: group by prompt, compute variance of the mention flag
    # (0/1) across the runs of that same prompt, average, invert so
    # higher = more stable (1 - variance), matching the blueprint's
    # definition (variance of a 0/1 variable is at most 0.25 -> scale it
    # back to a 0..1 range so "1 - variance*4" spans the full range).
    by_prompt = {}
    for r in results:
        by_prompt.setdefault(r.prompt, []).append(1 if r.mentioned else 0)
    if by_prompt:
        variances = [pvariance(v) if len(v) > 1 else 0.0 for v in by_prompt.values()]
        avg_variance = sum(variances) / len(variances)
        stability = max(0.0, 1.0 - (avg_variance * 4))
    else:
        stability = 1.0

    # Share of voice vs named competitors.
    competitor_counts = {}
    for r in results:
        for c in r.competitors_mentioned:
            competitor_counts[c] = competitor_counts.get(c, 0) + 1
    business_mentions = sum(1 for r in results if r.mentioned)
    total_competitor_mentions = sum(competitor_counts.values())
    denom = business_mentions + total_competitor_mentions
    share_of_voice = (business_mentions / denom) if denom > 0 else (
        1.0 if business_mentions > 0 else 0.0
    )
    competitor_mention_rates = {
        name: count / total for name, count in competitor_counts.items()
    }

    reputation_signal = None
    if review_count is not None and review_rating is not None:
        review_count_component = min(review_count, 30) / 30  # capped at 30
        rating_component = max(0.0, min(review_rating, 5.0)) / 5.0
        reputation_signal = (review_count_component * 0.5 + rating_component * 0.5)

    information_consistency = None
    if directories_checked and directories_checked > 0 and directories_consistent is not None:
        information_consistency = min(1.0, directories_consistent / directories_checked)

    if reputation_signal is not None and information_consistency is not None:
        weights = {"mention": 30, "recommendation": 25, "reputation": 20,
                   "consistency": 15, "stability": 10}
    else:
        # Redistribute the 35 points (reputation 20 + consistency 15)
        # proportionally across mention(30):recommendation(25):stability(10) = 65
        weights = {
            "mention": round(30 / 65 * 100, 2),
            "recommendation": round(25 / 65 * 100, 2),
            "reputation": 0,
            "consistency": 0,
            "stability": round(10 / 65 * 100, 2),
        }

    # Stability is only meaningful in proportion to how often the business
    # actually shows up. Without this, "never mentioned, in 12 out of 12
    # identical zero results" reads as PERFECTLY stable (variance of an
    # all-zero list is 0) and used to hand a truly invisible business
    # ~15/100 instead of near-0 — which looks like a scoring bug to anyone
    # who runs that exact case (and did, more than once). Scaling by
    # mention_rate fixes the zero-case without changing anything for a
    # business that's consistently mentioned (mention_rate=1 leaves the
    # stability term untouched). `stability` itself is still reported raw
    # in the API response, since "how consistent are you WHEN you show up"
    # is a legitimate thing to show separately.
    score = (
        mention_rate * weights["mention"]
        + recommendation_rate * weights["recommendation"]
        + (reputation_signal or 0) * weights["reputation"]
        + (information_consistency or 0) * weights["consistency"]
        + (stability * mention_rate) * weights["stability"]
    )

    return AuditSummary(
        total_runs=total,
        mention_rate=round(mention_rate, 4),
        recommendation_rate=round(recommendation_rate, 4),
        stability=round(stability, 4),
        share_of_voice=round(share_of_voice, 4),
        competitor_mention_rates={k: round(v, 4) for k, v in competitor_mention_rates.items()},
        reputation_signal=round(reputation_signal, 4) if reputation_signal is not None else None,
        information_consistency=round(information_consistency, 4) if information_consistency is not None else None,
        findability_score=round(score, 1),
        weights_used=weights,
    )
