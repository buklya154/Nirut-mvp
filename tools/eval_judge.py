#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Score the regex baseline AND the LLM judge against hand labels (SPEC §5
steps 3-4, and the accuracy line in §8's Definition of Done).

Input: a file produced by tools/capture_audit.py with every "label" filled in
by hand. Output: the two accuracy numbers, plus a per-answer disagreement
table so you can see WHERE each detector goes wrong rather than trusting a
single percentage.

    python3 tools/eval_judge.py tests/fixtures/ocd_2026-08-30.json

The judge pass makes one real (cheap) model call per answer — about a cent
for a 12-answer set at Haiku pricing. Use --regex-only to skip it and get the
baseline for free.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import judge  # noqa: E402
import scoring  # noqa: E402
from prompts import RECOMMENDATION_MARKERS  # noqa: E402

VALID = ("top_pick", "listed", "absent")


def regex_status(run, business, aliases):
    """What the OLD detector would have concluded, mapped onto the 3-state
    vocabulary so it can be compared with the labels."""
    a = scoring.analyze_response(business, [], run["raw_text"],
                                 RECOMMENDATION_MARKERS,
                                 business_name_aliases=aliases)
    if a["recommended"]:
        return "top_pick"
    return "listed" if a["mentioned"] else "absent"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("labelled_file")
    ap.add_argument("--regex-only", action="store_true")
    args = ap.parse_args()

    with open(args.labelled_file, encoding="utf-8") as f:
        data = json.load(f)

    business = data["business_name"]
    aliases = data.get("aliases", [])
    runs = data["runs"]

    unlabelled = [i for i, r in enumerate(runs) if r.get("label") not in VALID]
    if unlabelled:
        sys.exit("Runs %s have no valid label yet. Fill every 'label' with one "
                 "of %s first — those labels are the ground truth."
                 % (unlabelled, "/".join(VALID)))

    rows, regex_hits, judge_hits, judge_failures = [], 0, 0, 0

    for i, run in enumerate(runs, 1):
        label = run["label"]
        rx = regex_status(run, business, aliases)
        regex_hits += (rx == label)

        jd = "(skipped)"
        if not args.regex_only:
            jr = judge.judge_response(run["raw_text"], business, aliases,
                                      data.get("category_label", ""),
                                      data.get("city", ""))
            if jr.ok:
                jd = jr.status
                judge_hits += (jd == label)
            else:
                jd = "FAILED"
                judge_failures += 1
        rows.append((i, label, rx, jd))

    n = len(runs)
    print("\n  #   human      regex      judge")
    print("  " + "-" * 40)
    for i, label, rx, jd in rows:
        flag = "" if (rx == label and jd in (label, "(skipped)")) else "   <-"
        print("  %-3d %-10s %-10s %-10s%s" % (i, label, rx, jd, flag))

    print("\n  Regex baseline: %d/%d correct (%.0f%%)"
          % (regex_hits, n, 100.0 * regex_hits / n))
    if not args.regex_only:
        print("  LLM judge:      %d/%d correct (%.0f%%)"
              % (judge_hits, n, 100.0 * judge_hits / n))
        if judge_failures:
            print("  Judge call failed on %d answer(s) — those count as wrong."
                  % judge_failures)
        print("\n  SPEC §8 gate: judge must reach >=11/12 (>=92%%) before this "
              "ships. Currently %.0f%%." % (100.0 * judge_hits / n))
    print()


if __name__ == "__main__":
    main()
