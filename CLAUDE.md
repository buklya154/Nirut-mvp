# נראות (Nir'ut) — project context for Claude Code

Read this before making changes. The founder (Timur) is a non-technical,
first-time builder — explain what you did and why in plain language, not
just "done." Default to Manual/Accept-edits permission mode expectations:
show a diff, don't assume he can read a stack trace.

## What this is

A self-serve Hebrew tool: a local business owner types their name, city,
and category, and gets a real report on whether AI assistants (Claude,
ChatGPT, Gemini) mention them when someone asks "who's a good
[category] in [city]." Flask backend + JSON API, currently serving its
own HTML too. Deployed on Render.com. See `README.md` for the full
picture — this file is the short version for you to act on.

Current stage: this codebase is the **free/low-cost lead-magnet
product**. A separate, paid second product is still being researched —
don't assume this repo becomes that paid product unless explicitly told
to build it.

## Non-negotiable product rules (do not relax these, even if asked to
## "simplify" or "speed up" something)

- Never let copy claim a guaranteed ranking or "more customers." The
  score measures how often AI mentions the business across a fixed set
  of real buyer questions — not a Google ranking, not a causal revenue
  claim.
- Never ship a "run once, show the answer" shortcut. LLM answers are
  stochastic — every prompt runs multiple times per engine before
  scoring, on purpose. Don't remove the repeat-runs logic to make things
  faster.
- Any automated review/directory data must come from sources the
  business controls or that are genuinely public — no ToS-violating
  scraping.

## Architecture

- `app.py` — Flask routes (`/`, `/api/engines`, `/api/audit`), rate
  limiting, daily spend cap, DB persistence (SQLite or Postgres).
- `engines.py` — pluggable LLM callers (Anthropic/OpenAI/Gemini/
  Perplexity). Each engine is optional — only active if its API key env
  var is set. Each `call_*` function also takes a `grounded: bool` flag
  — see "Grounded (web-search) mode" below.
- `scoring.py` — pure scoring logic, no network calls, fully unit
  tested. This is the part that must stay provably correct; any change
  here needs a matching test in `tests/test_scoring.py`.
- `prompts.py` — Hebrew buyer-intent prompt templates by category.
- `tests/` — `test_scoring.py` (scoring math, no network), `test_engines.py`
  (grounded-flag plumbing and retry logic, fakes out the engine functions
  — no network), `test_app.py` (the `/api/audit` route, monkeypatches
  `engines.run_prompt` — no network, no real DB file left behind).

## Grounded (web-search) mode

`POST /api/audit` accepts an optional `"grounded": true` field (default
false). False = the original behavior, answered purely from the model's
training data. True = each engine's live web-search tool is turned on
(Anthropic web search, OpenAI web search via the Responses API, Gemini
Google Search grounding), which is closer to what a real user of these
assistants actually sees for a "who's a good X in Y" question — training
data alone doesn't reflect what a live web-connected assistant surfaces.

**This costs real extra money per audit — keep it opt-in.** Read the
cost comment at the top of `engines.py` before touching anything here;
it has the exact per-provider pricing and the worst-case-audit math.
`GROUNDED_MAX_SEARCHES_PER_CALL` in `engines.py` caps Anthropic's tool to
one search per call on purpose — don't raise it without redoing that
math. Perplexity's `sonar` is always web-grounded regardless of this
flag; there's no ungrounded mode for it to opt out of.

## Known quirks — read before touching related code

- **Gunicorn timeout**: Render's start command must be
  `gunicorn app:app --timeout 120`, not the default. A real audit takes
  20-40+ seconds (several real LLM calls); the default 30s worker
  timeout used to kill the request after the AI calls were already
  billed but before the response could be sent, which surfaced as a
  cryptic browser error, not a clean failure.
- **Every response must be JSON, never an HTML error page — including
  Flask's own routing errors (404/405), not just app-level ones.** The
  frontend always calls `.json()` on the response. There's a global
  `@app.errorhandler(Exception)` and a specific `@app.errorhandler(429)`
  in `app.py` for exactly this reason — if you add new error paths,
  make sure they can't fall through to Flask's default HTML error page.
  (This bit us three times now: gunicorn's timeout, flask-limiter's
  default 429 page, and — found while checking this claim rather than
  trusting it — the generic handler used to re-return `HTTPException`
  instances like a plain 404 unchanged, which IS Flask's default HTML
  page for that status. Fixed by converting those to JSON too instead of
  passing them through; see `handle_unexpected_error()`.)
- **Scoring: stability is scaled by mention_rate.** A never-mentioned
  business has zero-variance ("perfectly stable") mention data, which
  used to leak the full stability weight into the score and produce a
  false ~15/100 for a genuinely invisible business. Don't revert
  `(stability * mention_rate) * weights["stability"]` back to plain
  `stability * weights["stability"]` — see
  `test_summarize_never_mentioned_scores_near_zero` in the test suite.
- **Name matching supports aliases.** `business_name_aliases` lets a
  request pass alternate spellings/transliterations (e.g. Latin
  "Batumi" vs. Hebrew "באטומי") so a real mention in a different script
  isn't missed.
- **Data persistence**: defaults to local SQLite (`nirut.db`), which
  gets wiped on every Render redeploy on the free tier. Set
  `DATABASE_URL` (Render Postgres or Supabase) to persist for real —
  the code already branches on whether it's set, no changes needed to
  turn it on.
- **Rate limit + daily cap**: `NIRUT_RATE_LIMIT` (per-visitor,
  default "5 per hour") and `NIRUT_MAX_AUDITS_PER_DAY` (server-wide,
  default 50) both exist because every audit is several real, billed
  LLM calls. Don't remove these without discussing it with Timur first
  — they're a spend-cap safety net, not just noise.
- **Model names drift — don't trust a hardcoded default without
  checking.** As of 2026-08-27: Anthropic defaults to `claude-sonnet-5`
  (moved off `claude-sonnet-4-5`, which retires 2026-09-29); OpenAI
  defaults to `gpt-5.6-terra`; Gemini defaults to `gemini-3.7-flash` via
  the `google-genai` package (NOT the older, deprecated
  `google-generativeai` / `google.generativeai` package — the search-
  grounding tool for current Gemini models needs the newer one). Only
  `call_anthropic` has been tested against a real key; re-verify the
  others' model IDs against provider docs before trusting them, same as
  before.
- **`.gitignore` / `.env.example` exist now** — they didn't before, even
  though older docs already assumed them (referencing `.env.example` in
  setup steps, claiming `.env` was gitignored). If either goes missing
  again, that's a regression, not a from-scratch task.

## Running things

```
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then paste in at least ANTHROPIC_API_KEY
python3 app.py
```

Tests (no API key needed, no network calls):
```
pytest tests/ -v
```

Run the full suite after touching `scoring.py` or `prompts.py` — it's
fast (well under a second) and it's what proves the math still holds.

## Deployment

GitHub → Render.com, gunicorn. Start command must include
`--timeout 120` (see above). Env vars are set in Render's dashboard,
not committed to the repo — `.env.example` documents what's expected,
`.env` itself is gitignored.

## When you're unsure

If a task touches business/product decisions (pricing, what to promote,
which niche, positioning copy) rather than pure engineering, flag it
instead of guessing — that side of the project is being decided
separately and deliberately. Pure backend engineering (bug fixes, new
endpoints, tests, performance, deployment config) is fair game to just
do well.
