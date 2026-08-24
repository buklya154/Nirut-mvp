# -*- coding: utf-8 -*-
"""
Buyer-intent prompt templates for the Nir'ut AI-Findability audit.

These are adapted from the validated Hebrew prompt set used in prior
research (business-plan-hebrew-ai-visibility.md / operating-blueprint
Part 8). Each prompt takes a {city} and is run multiple times per engine
to average out normal LLM run-to-run variance.

IMPORTANT: prompts never name the business being audited (only the city
and, implicitly, the category via which prompt is chosen) — this tests
UNPROMPTED visibility, which is the whole point of the audit. Naming the
business would just get it echoed back and prove nothing.
"""

# Core buyer-intent prompts, by category. Add more categories here as the
# product expands beyond the first vertical.
PROMPTS_BY_CATEGORY = {
    "renovation": [
        "מי קבלן שיפוצים מומלץ ב{city}?",
        "מי הכי טוב לשיפוץ מטבחים ב{city}?",
        "איזה קבלן שיפוצים אמין באזור {city}?",
        "מי מבצע שיפוץ אמבטיה ב{city} ומומלץ?",
        "מי הכי מומלץ לשיפוץ כללי של דירה ב{city}?",
        "חברת שיפוצים מקצועית ב{city}",
        "קבלן שיפוצים עם ביקורות טובות ב{city}",
    ],
    "dentist": [
        "מי רופא שיניים מומלץ ב{city}?",
        "איזו מרפאת שיניים הכי טובה ב{city}?",
        "מי הכי טוב להשתלות שיניים ב{city}?",
        "רופא שיניים אמין באזור {city}",
        "מרפאת שיניים עם ביקורות טובות ב{city}",
    ],
    "lawyer": [
        "מי עורך דין מומלץ ב{city}?",
        "איזה משרד עורכי דין הכי טוב ב{city}?",
        "עורך דין אמין באזור {city}",
        "מי מומלץ לענייני נדל\"ן ב{city}?",
        "משרד עורכי דין עם ביקורות טובות ב{city}",
    ],
    "restaurant": [
        "איזו מסעדה מומלצת ב{city}?",
        "מה המסעדה הכי טובה ב{city}?",
        "איפה כדאי לאכול ב{city}?",
        "מסעדה עם ביקורות טובות ב{city}",
    ],
    "real_estate": [
        "מי סוכן נדל\"ן מומלץ ב{city}?",
        "איזו סוכנות נדל\"ן הכי טובה ב{city}?",
        "סוכן נדל\"ן אמין באזור {city}",
        "מי מומלץ למכירת דירה ב{city}?",
    ],
    "generic": [
        # Fallback for a category not in the list above — {category} is a
        # free-text Hebrew phrase the business owner types in, e.g. "מספרה".
        "מי {category} מומלץ ב{city}?",
        "איזה {category} הכי טוב ב{city}?",
        "{category} אמין באזור {city}",
        "{category} עם ביקורות טובות ב{city}",
    ],
}

RECOMMENDATION_MARKERS = [
    "ממליץ", "ממליצה", "מומלץ", "מומלצת", "הכי טוב", "הכי טובה",
    "מוביל", "מובילה", "עדיף", "כדאי לפנות", "הבחירה הטובה",
    "אני ממליץ", "כדאי לבדוק את", "אפשרות טובה",
]


def get_prompts(category: str, city: str, custom_category_label: str = None):
    """Return the filled-in prompt list for a category + city.

    category: one of the keys in PROMPTS_BY_CATEGORY, or "generic".
    custom_category_label: required when category == "generic" — the
        free-text Hebrew category phrase to substitute into the generic
        templates (e.g. "מספרה", "וטרינר").
    """
    templates = PROMPTS_BY_CATEGORY.get(category, PROMPTS_BY_CATEGORY["generic"])
    filled = []
    for t in templates:
        if "{category}" in t:
            if not custom_category_label:
                continue
            filled.append(t.format(city=city, category=custom_category_label))
        else:
            filled.append(t.format(city=city))
    return filled
