# -*- coding: utf-8 -*-
"""
Nir'ut — AI Findability Checker
MVP web app: one form, one report. See README.md for what this is,
what it deliberately does NOT do yet, and how to deploy it.
"""

import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from flask import Flask, request, jsonify, render_template, g
from flask_cors import CORS

import engines
import scoring
from prompts import get_prompts, RECOMMENDATION_MARKERS

app = Flask(__name__)

# CORS: only needed because the Lovable-hosted frontend (a different origin)
# calls this API directly from the browser. No secrets cross this boundary —
# API keys stay server-side; the browser only ever sends business_name/city/
# etc. and gets the score back. Set NIRUT_ALLOWED_ORIGIN to your Lovable
# preview/published URL(s), comma-separated, once you have them; falls back
# to "*" (any origin) so local testing and early setup aren't blocked.
_allowed_origins = os.environ.get("NIRUT_ALLOWED_ORIGIN", "*")
CORS(
    app,
    resources={r"/api/*": {"origins": _allowed_origins.split(",") if _allowed_origins != "*" else "*"}},
)

DB_PATH = os.environ.get("NIRUT_DB_PATH", os.path.join(os.path.dirname(__file__), "nirut.db"))
MAX_WORKERS = int(os.environ.get("NIRUT_MAX_WORKERS", "4"))
DEFAULT_RUNS_PER_PROMPT = int(os.environ.get("NIRUT_RUNS_PER_PROMPT", "3"))
MAX_RUNS_PER_PROMPT = 5


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            business_name TEXT NOT NULL,
            city TEXT NOT NULL,
            category TEXT NOT NULL,
            contact_email TEXT,
            findability_score REAL,
            mention_rate REAL,
            engines_used TEXT,
            total_runs INTEGER
        )
        """
    )
    conn.commit()
    conn.close()


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    """Safety net: never let an unexpected crash fall through to Flask's
    default HTML error page. The frontend always expects JSON back — an
    HTML page in its place used to surface as a cryptic browser error
    ("The string did not match the expected pattern.") instead of a real
    message. This guarantees a JSON response either way, and Render's
    logs still get the full traceback for debugging.
    """
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    app.logger.exception("Unhandled error in %s", request.path)
    return jsonify({"error": "unexpected server error", "details": str(e)}), 500


@app.route("/")
def index():
    return render_template("index.html", engines_available=engines.available_engines())


@app.route("/api/engines")
def api_engines():
    return jsonify({"available": engines.available_engines()})


@app.route("/api/audit", methods=["POST"])
def api_audit():
    data = request.get_json(force=True, silent=True) or {}

    business_name = (data.get("business_name") or "").strip()
    city = (data.get("city") or "").strip()
    category = (data.get("category") or "generic").strip()
    custom_category_label = (data.get("custom_category_label") or "").strip() or None
    competitors = [c.strip() for c in (data.get("competitors") or []) if c and c.strip()][:3]
    contact_email = (data.get("contact_email") or "").strip() or None
    runs_per_prompt = min(int(data.get("runs_per_prompt") or DEFAULT_RUNS_PER_PROMPT), MAX_RUNS_PER_PROMPT)

    review_count = data.get("review_count")
    review_rating = data.get("review_rating")
    directories_checked = data.get("directories_checked")
    directories_consistent = data.get("directories_consistent")

    if not business_name or not city:
        return jsonify({"error": "business_name and city are required"}), 400

    prompts = get_prompts(category, city, custom_category_label)
    if not prompts:
        return jsonify({"error": "unknown category, or custom_category_label missing for a generic category"}), 400

    available = engines.available_engines()
    if not available:
        return jsonify({
            "error": "No AI engine is configured on this server yet. "
                     "Set ANTHROPIC_API_KEY (and optionally OPENAI_API_KEY / "
                     "GEMINI_API_KEY / PERPLEXITY_API_KEY) as environment "
                     "variables — see README.md."
        }), 503

    jobs = []
    for prompt in prompts:
        for engine_name in available:
            for _ in range(runs_per_prompt):
                jobs.append((engine_name, prompt))

    results = []
    errors = []

    def _do(job):
        engine_name, prompt = job
        try:
            text = engines.run_prompt(engine_name, prompt)
            analysis = scoring.analyze_response(business_name, competitors, text, RECOMMENDATION_MARKERS)
            return scoring.RunResult(
                engine=engine_name, prompt=prompt,
                mentioned=analysis["mentioned"], recommended=analysis["recommended"],
                competitors_mentioned=analysis["competitors_mentioned"], raw_text=text,
            )
        except engines.EngineError as e:
            return {"error": str(e), "engine": engine_name, "prompt": prompt}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(_do, job) for job in jobs]
        for f in as_completed(futures):
            r = f.result()
            if isinstance(r, dict):
                errors.append(r)
            else:
                results.append(r)

    if not results:
        return jsonify({"error": "every engine call failed", "details": errors}), 502

    summary = scoring.summarize(
        results,
        review_count=int(review_count) if review_count not in (None, "") else None,
        review_rating=float(review_rating) if review_rating not in (None, "") else None,
        directories_checked=int(directories_checked) if directories_checked not in (None, "") else None,
        directories_consistent=int(directories_consistent) if directories_consistent not in (None, "") else None,
    )

    # Log the run - this is the product's entire CRM for the MVP stage.
    try:
        db = sqlite3.connect(DB_PATH)
        db.execute(
            "INSERT INTO audits (created_at, business_name, city, category, contact_email, "
            "findability_score, mention_rate, engines_used, total_runs) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(), business_name, city, category, contact_email,
                summary.findability_score, summary.mention_rate, ",".join(available), summary.total_runs,
            ),
        )
        db.commit()
        db.close()
    except Exception:
        pass  # logging must never break the actual audit response

    example_runs = [
        {
            "engine": r.engine, "prompt": r.prompt, "mentioned": r.mentioned,
            "recommended": r.recommended, "competitors_mentioned": r.competitors_mentioned,
            "excerpt": (r.raw_text[:220] + "…") if len(r.raw_text) > 220 else r.raw_text,
        }
        for r in results
    ]

    return jsonify({
        "business_name": business_name,
        "city": city,
        "engines_used": available,
        "summary": {
            "findability_score": summary.findability_score,
            "mention_rate": summary.mention_rate,
            "recommendation_rate": summary.recommendation_rate,
            "stability": summary.stability,
            "share_of_voice": summary.share_of_voice,
            "competitor_mention_rates": summary.competitor_mention_rates,
            "reputation_signal": summary.reputation_signal,
            "information_consistency": summary.information_consistency,
            "weights_used": summary.weights_used,
            "total_runs": summary.total_runs,
        },
        "runs": example_runs,
        "errors": errors,
    })


if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
else:
    init_db()
