const form = document.getElementById("audit-form");
const submitBtn = document.getElementById("submit-btn");
const resultCard = document.getElementById("result-card");
const errorCard = document.getElementById("error-card");
const categorySelect = document.getElementById("category-select");
const customCategoryWrap = document.getElementById("custom-category-wrap");

categorySelect.addEventListener("change", () => {
  customCategoryWrap.style.display = categorySelect.value === "generic" ? "block" : "none";
});

const METRIC_LABELS = {
  mention_rate: "אחוז הזכרות",
  recommendation_rate: "אחוז המלצות מפורשות",
  stability: "יציבות התוצאה",
  share_of_voice: "נתח קול מול המתחרים",
};

function pct(x) {
  if (x === null || x === undefined) return "—";
  return Math.round(x * 100) + "%";
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  errorCard.hidden = true;
  resultCard.hidden = true;
  submitBtn.disabled = true;
  submitBtn.textContent = "רץ עכשיו על כמה מודלים… (עד 40 שניות)";

  const fd = new FormData(form);
  const payload = {
    business_name: fd.get("business_name"),
    city: fd.get("city"),
    category: fd.get("category"),
    custom_category_label: fd.get("custom_category_label"),
    competitors: [fd.get("competitor_1"), fd.get("competitor_2"), fd.get("competitor_3")],
    contact_email: fd.get("contact_email"),
    review_count: fd.get("review_count") || null,
    review_rating: fd.get("review_rating") || null,
  };

  try {
    const res = await fetch("/api/audit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.error || "משהו השתבש");
    }

    renderResult(payload.business_name, data);
  } catch (err) {
    document.getElementById("error-text").textContent = "שגיאה: " + err.message;
    errorCard.hidden = false;
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "בדוק/י אותי עכשיו";
  }
});

function renderResult(businessName, data) {
  document.getElementById("result-title").textContent =
    `תוצאות עבור ${businessName} (${data.engines_used.join(", ")}, ${data.summary.total_runs} בדיקות)`;
  document.getElementById("score-number").textContent = data.summary.findability_score;

  const grid = document.getElementById("metrics-grid");
  grid.innerHTML = "";
  for (const key of ["mention_rate", "recommendation_rate", "stability", "share_of_voice"]) {
    const div = document.createElement("div");
    div.className = "metric";
    div.innerHTML = `<div class="val">${pct(data.summary[key])}</div><div class="lbl">${METRIC_LABELS[key]}</div>`;
    grid.appendChild(div);
  }

  const compBlock = document.getElementById("competitor-block");
  const rates = data.summary.competitor_mention_rates || {};
  const compNames = Object.keys(rates);
  if (compNames.length) {
    compBlock.innerHTML = "<h3>מתחרים שהוזכרו</h3><ul>" +
      compNames.map(n => `<li>${n} — הוזכר/ה ב-${pct(rates[n])} מהבדיקות</li>`).join("") +
      "</ul>";
  } else {
    compBlock.innerHTML = "";
  }

  const runsBlock = document.getElementById("runs-block");
  runsBlock.innerHTML = "<h3>דוגמאות מהבדיקות</h3>" +
    data.runs.slice(0, 6).map(r => `
      <div class="run-item">
        <span class="tag ${r.mentioned ? 'tag-yes' : 'tag-no'}">${r.mentioned ? 'הוזכר' : 'לא הוזכר'}</span>
        <strong>${r.prompt}</strong> (${r.engine})<br>
        <span style="color:#666">${r.excerpt}</span>
      </div>
    `).join("");

  resultCard.hidden = false;
  resultCard.scrollIntoView({ behavior: "smooth", block: "start" });
}
