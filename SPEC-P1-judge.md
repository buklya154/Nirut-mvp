# SPEC — P1/P2: replace the regex "recommended" detector with an LLM judge

Written 2026-08-30. Status: **approved to build.** Scope is fenced (§9) — do
not extend it. Implements P1 and P2 from `../../algorithm-improvement-plan.md`;
one judge call powers both.

---

## 1. The problem, precisely

`scoring.analyze_response()` decides `recommended` with two text rules:

```python
if idx != -1 and idx < 150:            # name in first 150 chars
    recommended = True
if not recommended:
    window = norm_text[max(0, idx-40): idx+matched_len+40]
    if any(_normalize(m) in window for m in recommendation_markers):
        recommended = True
```

Both rules fail structurally on real Hebrew LLM answers:

- **The 150-character rule is dead code in production.** Every answer opens
  with a hedging preamble ("קשה לקבוע 'הכי טובה' באופן אובייקטיבי כי זה
  תלוי בטעם אישי…"), which always consumes the headline zone. It fires only
  in unit tests, where the fixtures have no preamble.
- **The ±40-character window sees the wrong text.** Answers are markdown
  category lists. The recommendation verb that governs the list ("מסעדות
  שנחשבות לטובות ומומלצות:") sits *above* the list; the window around the
  business name contains the category header ("מטבח ים תיכוני —"). Real
  recommendations are missed. Conversely a name buried 14th in a list can
  sit near a stray "מומלץ" and false-positive.

Consequence: the live OCD audit reported `recommendation_rate` 8% (1/12)
while the excerpts show OCD presented positively several times. **That 8% is
an artifact of the detector.** Selling a paid report built on it is selling a
wrong number.

---

## 2. What we build

A new module `judge.py`. One cheap model call per engine response, returning
structured JSON. `scoring.py` stays network-free (that separation is
deliberate and stated in its docstring — keep it).

### Output contract

```json
{
  "status": "top_pick" | "listed" | "absent",
  "matched_as": "אאוצ'ד (OCD)",
  "businesses_named": ["הרברט סמואל", "אוזה", "הצמרת"]
}
```

- **`status`** — the three-state replacement for the `recommended` boolean.
- **`matched_as`** — the exact substring in the answer that names the
  business, or `null`. This is what makes the report auditable: the UI can
  highlight it, and you can defend any single data point to a skeptical
  agency. Do not skip this field; it is the "receipts, not scores"
  differentiator in one column.
- **`businesses_named`** — every *other* real business named. This is P2.

### Python surface

```python
# judge.py
JUDGE_ENABLED = os.environ.get("NIRUT_JUDGE_ENABLED", "1") not in ("0", "false", "False")
JUDGE_MODEL   = os.environ.get("NIRUT_JUDGE_MODEL", "claude-haiku-4-5")
JUDGE_TIMEOUT = float(os.environ.get("NIRUT_JUDGE_TIMEOUT", "20"))

class JudgeResult(NamedTuple):
    status: str                 # "top_pick" | "listed" | "absent"
    matched_as: Optional[str]
    businesses_named: List[str]
    ok: bool                    # False => caller must use the regex fallback
    error: Optional[str]

def judge_response(text, business_name, aliases, category_label, city) -> JudgeResult
def _call_judge_model(prompt: str) -> str    # the ONLY network call; test seam
```

`_call_judge_model` must be a separate one-line-ish function so tests can
monkeypatch it and **never touch the network**. Same convention as
`engines.py`: missing `ANTHROPIC_API_KEY` raises, caller degrades gracefully.

> **Verify the model name before running.** `engines.py` already warns that
> model names drift. `claude-haiku-4-5` above is a placeholder default —
> check Anthropic's current model list and set `NIRUT_JUDGE_MODEL`
> accordingly. Use the cheapest Haiku-class model; this task is easy.

Call it with `temperature=0`, `max_tokens=300`.

---

## 3. The judge prompt (use verbatim)

```
You are evaluating one answer produced by an AI assistant in response to a
consumer question about local businesses. The answer is usually in Hebrew.

BUSINESS: {business_name}
KNOWN ALTERNATE SPELLINGS: {aliases}
CATEGORY: {category_label}
CITY: {city}

ANSWER TEXT:
<<<
{text}
>>>

Decide how the answer treats BUSINESS, and list every other real business
the answer names.

status:
- "top_pick" - the answer presents BUSINESS as a recommendation: named as
               the best or a recommended choice, singled out positively, or
               included in a list the answer introduces as recommended /
               best / highly regarded.
- "listed"   - BUSINESS is named only neutrally: an example, an aside, a
               "there are also..." mention, or a member of a list the
               answer does NOT frame as a recommendation.
- "absent"   - BUSINESS is not named at all. A DIFFERENT business with a
               similar name does not count as present.

Rules:
- Judge only what this text says. Do not use outside knowledge.
- A hedging preamble ("it is hard to say objectively", "it depends on
  personal taste") does NOT by itself prevent "top_pick". What matters is
  how BUSINESS itself is framed.
- Hebrew and Latin spellings of the same name are the same business.
- businesses_named: every OTHER business named as somewhere a consumer
  could actually go, written exactly as it appears in the text. Exclude
  BUSINESS itself. Exclude directories, review sites and aggregators
  (Google, Google Maps, Waze, Zap, דפי זהב, TripAdvisor, Facebook, Rest).
  Exclude neighbourhoods, streets, dishes and generic category words.
- matched_as: the exact substring that names BUSINESS, or null if absent.

Return ONLY a JSON object. No prose, no markdown code fence.
{"status": "...", "matched_as": "...", "businesses_named": ["...", "..."]}
```

**Parsing:** `json.loads` first; if that fails, strip a ```json fence and
retry; if that fails, return `ok=False`. Validate `status` is one of the
three literals — anything else is `ok=False`. Coerce `businesses_named` to a
list of non-empty strings, cap at 15, de-duplicate case-insensitively.

---

## 4. How it wires into the existing code

### 4.1 `scoring.RunResult` — add fields, keep the old ones

```python
@dataclass
class RunResult:
    engine: str
    prompt: str
    mentioned: bool
    recommended: bool
    competitors_mentioned: List[str] = field(default_factory=list)
    raw_text: str = ""
    # new:
    status: str = "absent"                                   # judge 3-state
    matched_as: Optional[str] = None
    businesses_named: List[str] = field(default_factory=list)
    judged: bool = False                                     # False => fallback used
```

`mentioned` and `recommended` stay so `summarize()` and every existing test
keep working unchanged. This is deliberate — see §9.

### 4.2 `app.py` `_do()` — the only orchestration change

After `engines.run_prompt(...)` returns `text`, and after the existing
`scoring.analyze_response(...)` call (keep it — it produces the fallback and
the manual-competitor matching):

```python
jr = judge.judge_response(text, business_name, business_name_aliases,
                          custom_category_label or category, city)
if jr.ok:
    mentioned   = analysis["mentioned"] or jr.status != "absent"
    recommended = jr.status == "top_pick"
    status      = jr.status if (jr.status != "absent" or not analysis["mentioned"]) else "listed"
else:
    mentioned   = analysis["mentioned"]
    recommended = analysis["recommended"]
    status      = "top_pick" if recommended else ("listed" if mentioned else "absent")
```

**Why `mentioned` is OR-ed, not replaced:** the substring/alias match is
high-precision and free; the judge is high-recall (catches inflections and
spellings the substring misses). OR-ing raises recall without giving up the
deterministic floor. The judge alone never *removes* a mention the substring
found — that line is the guard against a judge false-negative silently
zeroing a real business's score.

`recommended` comes purely from the judge, because that is the broken thing.

The judge call happens inside `_do()`, so it runs inside the existing
`ThreadPoolExecutor` — no new concurrency, no new failure mode.

### 4.3 `scoring.summarize()` — additive only

Add, derived from `status` across runs:

```python
top_pick_rate  = count(status == "top_pick") / total
listed_rate    = count(status == "listed")   / total
judge_coverage = count(judged) / total
```

Add all three to `AuditSummary` and to the `/api/audit` JSON under
`summary`. **`recommendation_rate` keeps its current definition and the score
formula does not change** — `recommendation_rate` will simply now equal
`top_pick_rate`, because `recommended` is now `status == "top_pick"`.

`judge_coverage` gets surfaced in the report ("12 מתוך 12 תשובות נותחו").
Methodology transparency is the thing buyers say every competitor lacks —
publish it rather than hiding it.

### 4.4 P2 — named competitors (rides on the same call)

In `summarize()`:

```python
named_competitor_counts = Counter()      # from every run's businesses_named
```

Return it sorted descending, capped at 10. Then fix the share-of-voice tile:

- Manual competitors supplied → current behaviour, unchanged.
- No manual competitors but `named_competitor_counts` is non-empty →
  compute SoV as `business_mentions / (business_mentions + sum(counts))`.
- Neither → **`share_of_voice = None`.** The UI shows
  "לא נבדק — לא זוהו מתחרים", never 100%.

That last bullet matters commercially: a green "נתח קול 100%" tile next to a
23/100 score is exactly the inconsistency a skeptical agency uses to dismiss
the whole report.

The payoff line for the report, straight from real answers with zero typing
from the customer:

> **מי שה-AI כן ממליץ עליו:** הרברט סמואל ×7 · אוזה ×5 · הצמרת ×4 — **אתם: ×2**

---

## 5. Do this BEFORE writing code (30 minutes, non-negotiable)

You cannot tell whether the judge is better than the regex without something
to compare both against. Build a labelled set first:

1. Run one audit on OCD (or any business you can eyeball), and **save every
   `raw_text`** — dump the `runs` array from the API response to
   `tests/fixtures/ocd_2026-08-30.json`.
2. Read all 12 answers yourself and hand-label each `top_pick` / `listed` /
   `absent`. Ten minutes. Your labels are the ground truth.
3. Record how many the **current regex** gets right against your labels.
4. After building, record how many the **judge** gets right.

This gives you (a) a real regression test set, and (b) a sentence you can put
in the paid report's methodology note and say to an agency out loud:
*"the old rule agreed with a human on 7 of 12 answers; this one agrees on
12 of 12."* Nobody else in this category publishes that. It is the cheapest
credibility you will ever buy.

---

## 6. Tests (`tests/test_judge.py`, plus additions to `test_scoring.py`)

All tests monkeypatch `judge._call_judge_model`. **No test may hit the
network.**

| Test | Asserts |
|---|---|
| valid JSON response | parsed correctly, `ok=True` |
| JSON wrapped in a ```json fence | fence stripped, parsed, `ok=True` |
| model returns prose / garbage | `ok=False`, no exception raised |
| `status` is an unrecognised string | `ok=False` |
| `businesses_named` has dupes + blanks | de-duplicated, blanks dropped |
| raises inside `_call_judge_model` | `ok=False`, `error` populated |
| `NIRUT_JUDGE_ENABLED=0` | no call attempted, `ok=False` |
| **fallback path in `_do()`** | judge fails → run scored exactly as today |
| **real Hebrew list-format fixture** | preamble + markdown list where business is under a "מומלצות" heading → `top_pick` (the case the regex misses) |
| **similar-name fixture** | a different business with a near-identical name → `absent` |
| `summarize()` with mixed statuses | `top_pick_rate` / `listed_rate` / `judge_coverage` correct |
| `summarize()` with no competitors of any kind | `share_of_voice is None` |
| `summarize()` with `businesses_named` only | SoV computed from them |

Every existing test in `test_scoring.py` must still pass untouched. If one
breaks, the change is out of scope — stop and re-read §9.

---

## 7. Cost

One extra small-model call per run. A 12-run audit → 12 judge calls, each
roughly 600 tokens in / 60 out at Haiku-class pricing ≈ **$0.06–0.15 per
audit**. Negligible against the engine calls already being billed, and far
below the grounded-search cost documented in `engines.py`.

The existing daily cap (`MAX_AUDITS_PER_DAY`) already bounds total spend —
no new cap needed. Verify current Haiku pricing before quoting the number to
anyone.

---

## 8. Definition of done

Status as of 2026-08-30 (build pass by Claude Code):

- [ ] **BLOCKED** `tests/fixtures/ocd_2026-08-30.json` exists with 12
      hand-labelled answers — needs a live audit (real spend; no
      `ANTHROPIC_API_KEY` present) **and Timur's own labels**. Capture it with
      `tools/capture_audit.py`. A model cannot supply these labels: they are
      the reference the model is scored against, and the resulting number goes
      into a paid report's methodology note.
- [ ] **BLOCKED (on the above)** Regex baseline accuracy recorded (§5 step 3).
      `tools/eval_judge.py --regex-only` prints it the moment labels exist.
      *(Not a substitute: against the three synthetic shape-fixtures the regex
      scores 0/3 — but those were written to break it, so that number is a
      demonstration, not a baseline.)*
- [x] `judge.py` exists; `scoring.py` still makes zero network calls
- [x] Full test suite green, including every pre-existing test — 46 passed,
      up from 19; every pre-existing test untouched and passing
- [ ] **BLOCKED (on the above)** Judge accuracy ≥11/12 recorded here.
      `tools/eval_judge.py` enforces and prints this gate.
- [ ] **BLOCKED** One live audit re-run on OCD (`matched_as` correctness,
      sane `recommendation_rate`) — needs the API key.
- [x] `README.md` documents `NIRUT_JUDGE_ENABLED` / `_MODEL` / `_TIMEOUT`
- [x] `CLAUDE.md` known-issues section updated (P1/P2 → done, with the
      unmeasured-accuracy caveat recorded)

Also settled during the build:

- **Model name verified** (§2 asked): `claude-haiku-4-5` is still the current
  cheapest Haiku-class model as of 2026-08-30 — the placeholder was correct,
  no change needed.
- **Cost corrected** (§7 asked): ~**$0.011 per 12-run audit** at Haiku's
  $1.00/$5.00 per 1M, not $0.06–0.15. The spec's estimate was ~6–14× high.
- **§4.4 gap found:** `summarize()` could not tell "owner named rivals who
  never appeared" (a real 100% SoV) from "owner named nobody" (never
  measured) — both leave `competitors_mentioned` empty. Added an optional
  `manual_competitors` parameter so "manual supplied → unchanged" is
  actually true; defaults to `None`, so no existing caller changes.

---

## 9. Out of scope — do not touch in this task

- **The score formula.** That is P4 and it needs a product decision from
  Timur first. `recommendation_rate` keeps its weight and meaning.
- **Display precision / bands** (P5).
- **The `runs` table** (P7) — next task, and the judge output is exactly what
  it will persist, so it lands cleanly afterwards.
- **Enabling OpenAI/Gemini** (P6) and the per-engine breakdown (P3).
- **Any UI or RTL work** (P8).
- Do not delete `RECOMMENDATION_MARKERS` or the regex path — they are the
  fallback and they are load-bearing.

Estimated: one evening (~2–3 hours) with Claude Code, plus the 30 minutes in
§5 first.
