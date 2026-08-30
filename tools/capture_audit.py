#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Capture one real audit's raw answers into a labelling file (SPEC §5 step 1).

This SPENDS REAL MONEY: it makes the same live LLM calls a real audit makes
(prompts x runs_per_prompt). It exists because the original OCD run's
`raw_text` values were never persisted — logging per-run results is P7, still
open — so the labelled set has to be re-generated from a fresh run.

Usage:
    export ANTHROPIC_API_KEY=sk-...
    python3 tools/capture_audit.py \\
        --business "אאוצ'ד" --city "תל אביב" --category restaurant \\
        --aliases "OCD" --out tests/fixtures/ocd_2026-08-30.json

Then open the output file and fill in each run's "label" by hand with one of
top_pick / listed / absent. Those labels are the ground truth — read the
answers yourself; do not have a model fill them in, or the accuracy number
you get out the other end measures nothing.

Then: python3 tools/eval_judge.py tests/fixtures/ocd_2026-08-30.json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import engines  # noqa: E402
from prompts import get_prompts  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--business", required=True)
    ap.add_argument("--city", required=True)
    ap.add_argument("--category", default="restaurant")
    ap.add_argument("--category-label", default=None,
                    help="required if --category generic")
    ap.add_argument("--aliases", default="", help="comma-separated")
    ap.add_argument("--runs", type=int, default=3, help="runs per prompt")
    ap.add_argument("--engine", default="anthropic")
    ap.add_argument("--grounded", action="store_true")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    aliases = [a.strip() for a in args.aliases.split(",") if a.strip()]
    prompts = get_prompts(args.category, args.city, args.category_label)
    if not prompts:
        sys.exit("No prompts for that category (generic needs --category-label).")

    total = len(prompts) * args.runs
    print("About to make %d live %s calls (%d prompts x %d runs)%s."
          % (total, args.engine, len(prompts), args.runs,
             " GROUNDED" if args.grounded else ""))
    if input("This costs real money. Type 'yes' to continue: ").strip() != "yes":
        sys.exit("Aborted.")

    runs = []
    for prompt in prompts:
        for i in range(args.runs):
            print("  [%d/%d] %s" % (len(runs) + 1, total, prompt))
            try:
                text = engines.run_prompt(args.engine, prompt, grounded=args.grounded)
            except Exception as e:  # noqa: BLE001
                print("      FAILED: %s" % e)
                continue
            runs.append({
                "engine": args.engine,
                "prompt": prompt,
                "raw_text": text,
                "label": "",          # <-- YOU fill this in: top_pick|listed|absent
            })

    payload = {
        "_comment": "Hand-labelled ground truth. Fill every 'label' with "
                    "top_pick, listed, or absent by reading the answer yourself.",
        "business_name": args.business,
        "aliases": aliases,
        "city": args.city,
        "category": args.category,
        "category_label": args.category_label or args.category,
        "grounded": args.grounded,
        "runs": runs,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print("\nWrote %d answers to %s" % (len(runs), args.out))
    print("Now label each run by hand, then run tools/eval_judge.py on it.")


if __name__ == "__main__":
    main()
