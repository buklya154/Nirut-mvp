# Test fixtures

## `hebrew_answer_shapes.json` — SYNTHETIC

Hand-written by Claude Code to reproduce the two answer *shapes* that break
the regex detector (see `judge.py`'s docstring). They are **not** captured
model output and they are **not** ground truth. They exist so the wiring and
parsing have something realistically-shaped to run against.

Their `expected_status` values are structural facts about the fixture text
("this business sits under a heading that says מומלצות"), not human judgements
of a real answer.

## `ocd_2026-08-30.json` — NOT YET CAPTURED

This is the real labelled set required by SPEC-P1-judge.md §5, and it is the
gate on the accuracy claims in §8. It does **not** exist yet, because
producing it needs two things Claude Code cannot supply:

1. **A live audit run.** Needs `ANTHROPIC_API_KEY` and spends real money.
   The original OCD run's `raw_text` values were never persisted (that's P7),
   so they have to be re-generated.
2. **Timur's own labels.** §5 says "read all 12 answers yourself and
   hand-label each" — the whole point is that a *human* judgement is the
   reference the detectors are scored against. A label invented by a model
   and then used to score that same model measures nothing, and the resulting
   accuracy number would end up in a paid report's methodology note and be
   said out loud to agencies. It has to be real.

To produce it:

```bash
python3 tools/capture_audit.py --business "אאוצ'ד" --city "תל אביב" \
    --category restaurant --aliases "OCD" --out tests/fixtures/ocd_2026-08-30.json
```

Then open the file and set each run's `"label"` to `top_pick`, `listed`, or
`absent`. Then score both detectors against your labels:

```bash
python3 tools/eval_judge.py tests/fixtures/ocd_2026-08-30.json
```

That prints the regex baseline (§5 step 3) and the judge accuracy (§5 step 4)
to paste into the spec's Definition of Done.
