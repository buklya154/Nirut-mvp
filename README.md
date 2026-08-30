# נראות (Nir'ut) — AI Findability Checker

## What this actually is

A self-serve tool: a business owner types in their name, city, and
category, and within ~30 seconds gets a real, live report on whether AI
assistants mention their business when someone asks "who's a good
[category] in [city]" — and if a competitor gets mentioned instead.

This is the productized version of the earlier "Local Findability"
research — same underlying insight (AI is now a real local-discovery
channel; most local businesses are invisible in it and don't know it),
but rebuilt as software instead of a manually-delivered service, so it
doesn't require you personally running every audit by hand.

## What's real vs. what's still a placeholder

**Real and tested in this session (no API key needed):**
- The scoring logic (`scoring.py`) — mention rate, recommendation rate,
  stability across repeated runs, share of voice vs. named competitors,
  the composite Findability Score. 8 unit tests pass (`pytest tests/`).
- The prompt sets (`prompts.py`) — reused from the already-validated
  Hebrew buyer-intent prompts, for 5 starter verticals plus a generic
  fallback for any category.
- The full request pipeline (form → audit → scoring → JSON → rendered
  report) — verified end-to-end with a mocked LLM response in this
  session; see the smoke test in the build log.

**Needs you to do one thing before it's live: add an API key.**
This app calls real LLM APIs to run the audit — that costs a small
amount of money per audit and requires an account. Nothing runs without
at least one key. Minimum viable setup is ONE key:

1. Go to console.anthropic.com, create an account, generate an API key.
2. Copy `.env.example` to `.env` and paste the key in as `ANTHROPIC_API_KEY`.
3. Run it (see below).

Adding `OPENAI_API_KEY` / `GEMINI_API_KEY` / `PERPLEXITY_API_KEY` later
turns this from a single-engine check into the real multi-engine audit
the product is supposed to be — the code already supports all four, it
just skips whichever ones aren't configured. Do this once you have a
few real customers and want to raise the price/credibility, not before.
Only `call_anthropic` has been tested against a real key so far — test
`call_openai`/`call_gemini`/`call_perplexity` for real as soon as you add
their keys (see the model-currency note at the top of `engines.py`).

**Not yet built (be honest with early customers about this):**
- Payments (Stripe or similar) — right now every audit is free. The
  `contact_email` field on the form is there so you can follow up
  manually and take payment by bank transfer, same as the original plan.
- Automated Google review count / NAP directory checking — these need
  paid, keyed APIs (Google Places, etc.) or scraping that's fragile and
  ToS-risky. For now the business owner can *optionally* type in their
  own review count/rating, and the score uses it if given. Full
  automation is a real v2 feature, not an MVP one.
- A PDF export — right now the report renders in-browser. Ctrl/Cmd+P →
  "Save as PDF" works fine as a stopgap; a real PDF endpoint is a small
  follow-up, not a big one.

## Running it locally

```
cd nirut-mvp
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit .env and paste in your API key
python3 app.py
```

Open http://localhost:5000

## Running the tests

```
pytest tests/ -v
```

These test the scoring/parsing logic only (no API key needed, no
network calls made) — they're what to run after you change anything in
`scoring.py` or `prompts.py` to make sure the math still holds up.

## Deploying it so it has a real URL

`gunicorn` is already in `requirements.txt` — no setup needed there.
Recommended path (checked Aug 2026):

- **Start here, free: Render.com.** Connect this folder as a Git repo
  (or use the dashboard's manual deploy), set the start command to
  `gunicorn app:app --timeout 120`, add your API key(s) as environment
  variables. No credit card needed.
  **Important: use `--timeout 120`, not just `gunicorn app:app`.**
  Gunicorn kills any request that runs longer than 30 seconds by
  default — but a real audit (several AI calls in a row) routinely
  takes 20-40+ seconds, especially on a free/shared instance. Without
  the longer timeout, gunicorn can kill the request *after* the AI
  calls already ran (and got billed) but *before* it can send back the
  answer — which shows up to the user as a confusing browser error
  instead of a real result.
  The other catch: the free instance sleeps after
  15 minutes of inactivity and takes ~1 minute to wake up on the next
  request — fine for sending self-serve links (Arm A of the field
  test), but open the link yourself 2 minutes before any live,
  in-person demo (Arm B) so it's already awake and doesn't stall in
  front of a real prospect.
- **Before doing real live demos: upgrade to a paid always-on tier**
  (Render's cheapest paid instance, or Railway's Hobby plan — both
  land around $5-7/month). Worth it once this is being shown to actual
  people, not worth paying for before then.
- **Fly.io**: more setup (a `fly.toml` + Dockerfile) — skip unless you
  outgrow Render.

None of these need you to buy a domain — they all give you a free
subdomain (e.g. `nirut.onrender.com`) which is plenty for an MVP.

**Note on data persistence:** free/low tiers on these platforms often
reset the filesystem on redeploy or restart, which means the SQLite
log (`nirut.db`) can get wiped. Fine during testing; if the field test
goes well and this needs to stay reliable, that's the point to move
logging to a small hosted Postgres (Render and Railway both offer one)
instead of local SQLite.

## Connecting a Lovable (or any other) frontend to this API

This backend can serve its own UI (`templates/index.html`) OR act as a pure
JSON API for a separately-hosted frontend — both at once, even. To wire a
Lovable-built site to real data instead of mock data, give Lovable this
contract and this URL (your deployed Render/Railway URL) — do not have
Lovable re-implement the scoring or prompt logic, only have it call this:

**`GET /api/engines`** → `{"available": ["anthropic", "openai", ...]}`
List of AI engines currently configured server-side (i.e. which API keys
are set). Useful to show/hide an "engines active" indicator.

**`POST /api/audit`** — the real audit. Takes 20-40+ seconds (it's running
several real LLM calls) — the frontend must show a loading state, not
assume this is instant.

Request body (JSON):
```json
{
  "business_name": "שיפוצי דורון",       // required
  "city": "נתניה",                        // required
  "category": "renovation",                // renovation|dentist|lawyer|restaurant|real_estate|generic
  "custom_category_label": "מספרה",       // required only if category="generic"
  "competitors": ["מתחרה א", "מתחרה ב"],  // optional, up to 3
  "business_name_aliases": ["Batumi"],     // optional, up to 3 - alternate
                                            // spellings/transliterations of
                                            // the business name (e.g. the
                                            // Latin spelling of a Hebrew-
                                            // script name) so a real mention
                                            // isn't missed on an exact-text match
  "contact_email": "you@example.com",      // optional
  "runs_per_prompt": 3,                    // optional, default 3, max 5
  "grounded": false                        // optional, default false - see
                                            // "Grounded (web-search) mode" below
}
```

**Rate limit:** `POST /api/audit` is limited per visitor (default 5 per
hour, tune with `NIRUT_RATE_LIMIT`) and capped server-wide at
`NIRUT_MAX_AUDITS_PER_DAY` (default 50) total audits per day — both exist
because every audit is several real, billed LLM calls. A visitor over
the per-IP limit gets a `429`; the server-wide cap also returns `429`
with a Hebrew message. Raise both once real usage/pricing justifies it.

Success response (200):
```json
{
  "business_name": "...", "city": "...", "engines_used": ["anthropic"],
  "summary": {
    "findability_score": 23.4,        // 0-100, the headline number
    "mention_rate": 0.11,
    "recommendation_rate": 0.0,
    "stability": 0.8,
    "share_of_voice": 0.05,           // may be null - see "share_of_voice can now be null"
    "competitor_mention_rates": {"מתחרה א": 0.82, "מתחרה ב": 0.64},
    "top_pick_rate": 0.08,            // judge: presented as a recommendation
    "listed_rate": 0.34,              // judge: named, but neutrally
    "judge_coverage": 1.0,            // fraction of answers the judge analysed
    "named_competitor_counts": {"הרברט סמואל": 7, "אוזה": 5},
    "total_runs": 27
  },
  "runs": [
    {"engine": "anthropic", "prompt": "...", "mentioned": false,
     "recommended": false, "competitors_mentioned": ["מתחרה א"],
     "status": "absent",              // top_pick | listed | absent
     "matched_as": null,              // exact substring naming the business
     "businesses_named": ["אוזה"],
     "judged": true,                  // false => scored by the text fallback
     "excerpt": "the first ~220 characters of the real AI answer..."}
  ],
  "errors": []
}
```

Error responses: `400` (missing business_name/city, or an unknown
category), `503` (no API key configured on the server yet), `502` (every
engine call failed — show a retry message, not a 0 score), `429` (rate
limit or daily cap hit — Hebrew message, JSON body, safe to retry
later), `500` (unexpected server error — also always JSON, never an HTML
page, so the frontend can rely on `.json()` working on every response
this API ever returns).

**CORS is already handled** (`flask-cors` is in `requirements.txt`, wired
in `app.py`). Once you know your Lovable project's URL, set
`NIRUT_ALLOWED_ORIGIN` to it in your host's environment variables instead
of leaving it open to any origin.

## How "recommended" is decided (the LLM judge)

Whether an answer *recommends* the business — as opposed to merely naming it
— is decided by a second, cheap model call per answer (`judge.py`), not by
text pattern-matching. It returns three states instead of a boolean:

- `top_pick` — presented as a recommendation (best / recommended / singled
  out positively / in a list the answer frames as recommended)
- `listed` — named neutrally, an example or an aside
- `absent` — not named at all

It also returns `matched_as` (the exact substring that named the business, so
the report can show its receipts) and `businesses_named` (every *other* real
business in the answer — this is where `named_competitor_counts` and the
"who the AI does recommend" line come from, with zero typing from the
customer).

The old text-matching path is still there and is still load-bearing: if the
judge call fails or is disabled, the run is scored exactly as it was before.
`judge_coverage` in the response reports what fraction of answers the judge
actually analysed, so the report can state its own methodology
("12 מתוך 12 תשובות נותחו") rather than hide it.

| Env var | Default | What it does |
|---|---|---|
| `NIRUT_JUDGE_ENABLED` | `1` | Set to `0` to turn the judge off entirely and fall back to text matching everywhere. |
| `NIRUT_JUDGE_MODEL` | `claude-haiku-4-5` | The judging model. Cheapest Haiku-class model; verified current 2026-08-30. |
| `NIRUT_JUDGE_TIMEOUT` | `20` | Per-call timeout in seconds. |

**Cost:** about **$0.011 per 12-run audit** (~7,200 input + 720 output tokens
at Haiku's $1.00/$5.00 per 1M). Roughly a cent — negligible next to the
engine calls already being billed. `NIRUT_MAX_AUDITS_PER_DAY` already caps
total spend.

### share_of_voice can now be `null`

If no competitors were supplied *and* the judge named none, `share_of_voice`
is `null`, meaning "not measured" — **the UI must render that as
"לא נבדק — לא זוהו מתחרים", never as 100%.** It previously returned `1.0`
whenever the business was mentioned even once, which put a green
"נתח קול 100%" tile next to a failing score.

## Grounded (web-search) mode

By default, every audit answers from the model's training data alone -
that measures what the model already "knows" about a business, not what
a real ChatGPT/Claude/Gemini user sees today, since real answers for
local businesses lean on live web/business-data lookups. Pass
`"grounded": true` in the `/api/audit` request body to turn on each
engine's live web-search tool instead (Anthropic web search, OpenAI web
search, Gemini Google Search grounding) - the response's `"grounded"`
field always echoes back which mode actually ran.

**This costs more per audit — keep it opt-in, don't flip the default.**
Web search is billed per search/query on top of normal token cost
(roughly $10-35 per 1,000 searches depending on provider). A full audit
makes `prompts × engines × runs_per_prompt` calls; see the cost comment
at the top of `engines.py` for the exact math and the search-count cap
that keeps a single grounded audit well under $1. Perplexity's `sonar`
is always web-grounded regardless of this flag - there's no ungrounded
mode for it to opt out of.

## Where the data goes

Every audit run gets logged — business name, city, category, score, and
the email if they gave one. This is the entire CRM for now, same
philosophy as the original plan's Google Sheet, just automatic instead
of hand-typed.

By default this goes to a local SQLite file (`nirut.db`) — fine for
local dev, but **on Render's free tier the filesystem is wiped on every
redeploy/restart, silently losing every lead.** Set `DATABASE_URL` (a
free Render Postgres add-on, or Supabase) to persist for real — the app
uses it automatically when set, no code changes needed. Without SQLite,
open the file with any SQLite browser or
`sqlite3 nirut.db "select * from audits;"`; with Postgres, use whatever
client the host gives you, or `psql "$DATABASE_URL" -c "select * from audits;"`.

## The honesty rules this product has to keep (carried over from the
## earlier research — don't relitigate these, they're load-bearing)

- Never promise "more customers" or a guaranteed ranking. The score
  measures how often AI mentions the business across a fixed set of
  real buyer questions — not a Google ranking, not a causal claim about
  revenue. Say this explicitly in the report, every time.
- Never claim a single run is reliable. LLM answers are stochastic —
  that's *why* the tool runs each prompt multiple times per engine
  before scoring. Don't ship a "run once, show the answer" shortcut
  even if a customer asks for something faster.
- If you add automated review/directory checks later, only ever use
  data the business itself controls or that's genuinely public — no
  scraping that violates a site's terms of service.

## Product decision context

This came out of a fresh opportunity scan (see
`phase1-fresh-opportunity-scan.md` in the parent folder) that ruled out
WordPress plugins (2026-specific: search algorithm now buries new
plugins, security crisis triggering "plugin minimalism"), most Shopify
app niches (median app under $1K MRR, technical depth beyond a
non-engineer solo build), and personal-brand-driven micro-SaaS (the
best-evidenced solo path in 2026, but it runs on the founder's own
Twitter/X following, which conflicts with running this behind a brand).
This Hebrew AI-findability niche was kept because it's the one option
that doesn't need an existing audience to make sense: Hebrew is a
meaningfully smaller, less-competitive content space than English "AI
SEO," and local cold outreach is available as a legitimate supplement
here specifically because there's a real local angle — unlike a generic
SaaS aimed at strangers worldwide.
