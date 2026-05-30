from flask import Flask, request, jsonify, render_template_string
import requests, json, re, time, os
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from rapidfuzz import fuzz
from groq import Groq

app = Flask(__name__)
client = Groq(api_key=os.getenv("GROQ_API_KEY", "YOUR_KEY"))

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
FOCUS = ["about", "contact", "services", "team", "company", "solution", "who-we-are"]
DB = "results.json"


def fetch(url, delay=1.2):
    try:
        time.sleep(delay)
        r = requests.get(url, headers=HEADERS, timeout=12)
        return r if r.status_code == 200 else None
    except:
        return None

def to_text(html, limit=2500):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script","style","nav","footer","header","noscript","svg","iframe","form","aside"]):
        t.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()[:limit]

def fuzzy_links(html, base):
    soup = BeautifulSoup(html, "html.parser")
    domain = urlparse(base).netloc
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        url = urljoin(base, a["href"])
        path = urlparse(url).path.lower()
        if urlparse(url).netloc != domain or path in seen: continue
        if max(fuzz.partial_ratio(k, path) for k in FOCUS) >= 60:
            out.append(url); seen.add(path)
    return out[:4]

def scrape(url):
    base = url.rstrip("/")
    pages = []

    # 1. Sitemap
    r = fetch(base + "/sitemap.xml") or fetch(base + "/sitemap_index.xml")
    if r:
        try:
            soup = BeautifulSoup(r.text, "xml")
            locs = [l.text for l in soup.find_all("loc") if urlparse(l.text).netloc == urlparse(base).netloc]
            ranked = sorted(locs, key=lambda u: max(fuzz.partial_ratio(k, urlparse(u).path.lower()) for k in FOCUS), reverse=True)
            for u in ranked[:4]:
                pr = fetch(u)
                if pr: pages.append(to_text(pr.text))
        except:
            pass

    # 2. Homepage + fuzzy links
    if not pages:
        r = fetch(base)
        if r:
            pages.append(to_text(r.text, 1200))
            for u in fuzzy_links(r.text, base):
                pr = fetch(u)
                if pr: pages.append(to_text(pr.text))

    # 3. Direct paths
    if not pages:
        for path in ["/about", "/about-us", "/contact", "/contact-us", "/services"]:
            r = fetch(base + path)
            if r: pages.append(to_text(r.text))

    return " | ".join(pages)[:4000]


def extract_emails(text):
    found = re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
    seen, out = set(), []
    for e in found:
        if e.lower() not in seen: seen.add(e.lower()); out.append(e.lower())
    return out[:5]

def extract_phone(text):
    patterns = [
        r"\+\d[\d\s\-\(\)]{7,17}\d",
        r"\(?\d{3}\)?[\s\-\.]\d{3}[\s\-\.]\d{4}",
        r"\d{5,6}[\s\-]\d{5,6}",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            candidate = m.group(0).strip()
            if re.fullmatch(r"[\d,\.]+", candidate): continue
            return candidate
    return "N/A"

def llm_enrich(text, url):
    prompt = f"""You are a business intelligence extraction assistant.
Extract factual information from the website text below.

STRICT RULES:
- "address": copy ONLY if a real street/city address is explicitly in the text. Otherwise "N/A".
- "core_service", "target_customer", "probable_pain_point": always write a concise answer based on what the company does. Never return "N/A" for these.
- "outreach_opener": always write a short compelling 1-2 sentence cold outreach message referencing the company's actual service. Never return "N/A".
- NEVER invent or hallucinate phone numbers or email addresses.
- Return ONLY a valid raw JSON object. No markdown, no backticks, no explanation.

Website URL: {url}
Website Text: {text}

{{"website_name":"...","company_name":"...","address":"...","core_service":"...","target_customer":"...","probable_pain_point":"...","outreach_opener":"..."}}"""
    try:
        r = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role":"user","content":prompt}],
            temperature=0.1, max_tokens=600
        )
        raw = r.choices[0].message.content.strip()
        raw = re.sub(r"^```(?:json)?", "", raw).rstrip("```").strip()
        return json.loads(raw)
    except Exception as e:
        print(f"LLM error: {e}")
        return {}

def enrich(url):
    text = scrape(url)
    llm = llm_enrich(text, url) if text else {}
    domain = urlparse(url).netloc.replace("www.", "")
    return {
        "website_name":        llm.get("website_name")        or domain,
        "company_name":        llm.get("company_name")        or domain,
        "address":             llm.get("address")             or "N/A",
        "mobile_number":       extract_phone(text),
        "mail":                extract_emails(text),
        "core_service":        llm.get("core_service")        or "N/A",
        "target_customer":     llm.get("target_customer")     or "N/A",
        "probable_pain_point": llm.get("probable_pain_point") or "N/A",
        "outreach_opener":     llm.get("outreach_opener")     or "N/A",
    }


def load_db():
    return json.load(open(DB)) if os.path.exists(DB) else []

def save_db(data):
    rows = load_db()
    for i, r in enumerate(rows):
        if r.get("website_name") == data.get("website_name"):
            rows[i] = data; break
    else:
        rows.append(data)
    json.dump(rows, open(DB, "w"), indent=2)


@app.route("/enrich", methods=["POST"])
def api_enrich():
    body = request.get_json() or {}
    url = body.get("url", "").strip()
    if not url: return jsonify({"error": "url required"}), 400
    if not url.startswith("http"): url = "https://" + url
    try:
        result = enrich(url)
        save_db(result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/results")
def api_results():
    return jsonify(load_db())


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Prospect Research Agent</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f6fa;color:#111}
header{background:#18181b;color:#fff;padding:18px 32px;display:flex;align-items:center;gap:10px}
header h1{font-size:1.2rem;font-weight:600}
.pill{background:#6366f1;padding:2px 10px;border-radius:20px;font-size:.72rem}
.wrap{max-width:960px;margin:0 auto;padding:28px 16px}
.card{background:#fff;border-radius:10px;padding:24px;box-shadow:0 1px 4px rgba(0,0,0,.07);margin-bottom:20px}
h2{font-size:1rem;font-weight:600;margin-bottom:16px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
input{flex:1;min-width:180px;padding:9px 13px;border:1.5px solid #e4e4e7;border-radius:7px;font-size:.9rem;outline:none;transition:border .15s}
input:focus{border-color:#6366f1}
.btn{padding:9px 22px;border:none;border-radius:7px;font-size:.9rem;font-weight:500;cursor:pointer;transition:background .15s}
.primary{background:#6366f1;color:#fff}.primary:hover{background:#4f46e5}
.secondary{background:#f4f4f5;color:#52525b}.secondary:hover{background:#e4e4e7}
.btn:disabled{opacity:.5;cursor:not-allowed}
.loader{display:none;align-items:center;gap:8px;margin-top:14px;font-size:.85rem;color:#6366f1}
.loader.on{display:flex}
.spin{width:15px;height:15px;border:2px solid #e4e4e7;border-top-color:#6366f1;border-radius:50%;animation:spin .7s linear infinite;flex-shrink:0}
@keyframes spin{to{transform:rotate(360deg)}}
.steps{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.step{font-size:.75rem;padding:3px 10px;border-radius:20px;background:#f4f4f5;color:#71717a;transition:all .2s}
.step.active{background:#ede9fe;color:#6366f1;font-weight:500}
.step.done{background:#dcfce7;color:#166534}
.result{display:none;margin-top:18px;border:1.5px solid #e4e4e7;border-radius:9px;overflow:hidden}
.result.show{display:block}
.res-head{background:#fafafa;padding:12px 18px;border-bottom:1px solid #e4e4e7;display:flex;justify-content:space-between;align-items:center}
.res-head strong{font-size:.95rem}
.badge-ok{background:#dcfce7;color:#166534;padding:2px 10px;border-radius:20px;font-size:.72rem}
.grid{display:grid;grid-template-columns:1fr 1fr}
.field{padding:12px 18px;border-bottom:1px solid #f4f4f5}
.field:nth-child(odd){border-right:1px solid #f4f4f5}
.full{grid-column:1/-1}
.label{font-size:.68rem;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:#a1a1aa;margin-bottom:3px}
.val{font-size:.88rem;line-height:1.5;word-break:break-word}
.tag{display:inline-block;background:#eff6ff;color:#1d4ed8;border-radius:4px;padding:1px 7px;font-size:.78rem;margin:1px}
.opener{color:#6366f1;font-style:italic}
.err{background:#fff1f2;border:1px solid #fecdd3;border-radius:7px;padding:12px 16px;color:#be123c;font-size:.87rem;margin-top:12px;display:none}
.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
table{width:100%;border-collapse:collapse;font-size:.85rem}
th{background:#fafafa;padding:9px 13px;text-align:left;font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;color:#71717a;border-bottom:2px solid #e4e4e7}
td{padding:11px 13px;border-bottom:1px solid #f4f4f5;vertical-align:top;max-width:200px}
tr:hover td{background:#fafafe}
.empty{text-align:center;padding:40px;color:#a1a1aa;font-size:.9rem}
.opener-col{font-size:.8rem;color:#52525b;line-height:1.4;font-style:italic}
@media(max-width:600px){.grid{grid-template-columns:1fr}.field:nth-child(odd){border-right:none}}
</style>
</head>
<body>
<header>
  <h1>🔍 Prospect Research Agent</h1>
  <span class="pill">AI-Powered</span>
</header>
<div class="wrap">

  <div class="card">
    <h2>Enrich a Company</h2>
    <div class="row">
      <input id="inp-name" placeholder="Website name (e.g. Stripe)" />
      <input id="inp-url" type="url" placeholder="https://stripe.com" />
      <button class="btn primary" id="btn-enrich" onclick="go()">Enrich</button>
    </div>
    <div class="loader" id="loader">
      <div class="spin"></div>
      <span id="ltxt">Starting...</span>
    </div>
    <div class="steps" id="steps">
      <span class="step" id="s1">Scraping</span>
      <span class="step" id="s2">Finding pages</span>
      <span class="step" id="s3">Cleaning text</span>
      <span class="step" id="s4">Running AI</span>
      <span class="step" id="s5">Saving</span>
    </div>
    <div class="err" id="err"></div>
    <div class="result" id="res"></div>
  </div>

  <div class="card">
    <div class="top">
      <h2>All Results</h2>
      <button class="btn secondary" onclick="loadAll()">Show All Results</button>
    </div>
    <div id="tbl"><div class="empty">Click "Show All Results" to load enriched companies.</div></div>
  </div>

</div>
<script>
const stepLabels = ["Scraping website...","Finding relevant pages...","Cleaning text...","Running AI enrichment...","Saving result..."]
const stepIds = ["s1","s2","s3","s4","s5"]
let stepIndex = 0, stepTimer

function startSteps() {
  stepIndex = 0
  stepIds.forEach(id => document.getElementById(id).className = "step")
  document.getElementById("loader").className = "loader on"
  document.getElementById("ltxt").textContent = stepLabels[0]
  document.getElementById("s1").className = "step active"
  stepTimer = setInterval(() => {
    document.getElementById(stepIds[stepIndex]).className = "step done"
    stepIndex = Math.min(stepIndex + 1, stepIds.length - 1)
    document.getElementById(stepIds[stepIndex]).className = "step active"
    document.getElementById("ltxt").textContent = stepLabels[stepIndex]
  }, 3000)
}

function stopSteps() {
  clearInterval(stepTimer)
  stepIds.forEach(id => document.getElementById(id).className = "step done")
  document.getElementById("loader").className = "loader"
}

async function go() {
  const url = document.getElementById("inp-url").value.trim()
  if (!url) { alert("Please enter a URL"); return }
  document.getElementById("btn-enrich").disabled = true
  document.getElementById("res").className = "result"
  hideErr()
  startSteps()
  try {
    const r = await fetch("/enrich", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({url})
    })
    const d = await r.json()
    if (!r.ok) throw new Error(d.error || "Something went wrong")
    renderResult(d)
  } catch(e) {
    showErr(e.message)
  } finally {
    stopSteps()
    document.getElementById("btn-enrich").disabled = false
  }
}

function renderResult(d) {
  const mails = (d.mail && d.mail.length)
    ? d.mail.map(m => `<span class="tag">${m}</span>`).join(" ")
    : `<span style="color:#a1a1aa">—</span>`
  document.getElementById("res").innerHTML = `
    <div class="res-head">
      <strong>${d.company_name || "—"}</strong>
      <span class="badge-ok">✓ Enriched</span>
    </div>
    <div class="grid">
      <div class="field"><div class="label">Website Name</div><div class="val">${d.website_name || "—"}</div></div>
      <div class="field"><div class="label">Company Name</div><div class="val">${d.company_name || "—"}</div></div>
      <div class="field"><div class="label">Address</div><div class="val">${d.address || "N/A"}</div></div>
      <div class="field"><div class="label">Phone</div><div class="val">${d.mobile_number || "N/A"}</div></div>
      <div class="field full"><div class="label">Email(s)</div><div class="val">${mails}</div></div>
      <div class="field"><div class="label">Core Service</div><div class="val">${d.core_service || "—"}</div></div>
      <div class="field"><div class="label">Target Customer</div><div class="val">${d.target_customer || "—"}</div></div>
      <div class="field full"><div class="label">Probable Pain Point</div><div class="val">${d.probable_pain_point || "—"}</div></div>
      <div class="field full"><div class="label">Outreach Opener</div><div class="val opener">${d.outreach_opener || "—"}</div></div>
    </div>`
  document.getElementById("res").className = "result show"
}

async function loadAll() {
  const tbl = document.getElementById("tbl")
  tbl.innerHTML = `<div class="empty"><div class="spin" style="margin:0 auto 8px"></div>Loading...</div>`
  try {
    const rows = await fetch("/results").then(r => r.json())
    if (!rows.length) { tbl.innerHTML = `<div class="empty">No results yet. Enrich a company above.</div>`; return }
    tbl.innerHTML = `<table>
      <thead>
        <tr>
          <th>Company</th>
          <th>Website Name</th>
          <th>Email(s)</th>
          <th>Phone</th>
          <th>Core Service</th>
          <th>Outreach Opener</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map(d => `<tr>
          <td><strong>${d.company_name || "—"}</strong><br><span style="color:#a1a1aa;font-size:.75rem">${d.address || ""}</span></td>
          <td>${d.website_name || "—"}</td>
          <td>${(d.mail || []).map(m => `<span class="tag">${m}</span>`).join("<br>") || "—"}</td>
          <td>${d.mobile_number || "—"}</td>
          <td>${d.core_service || "—"}</td>
          <td class="opener-col">${d.outreach_opener || "—"}</td>
        </tr>`).join("")}
      </tbody>
    </table>`
  } catch {
    tbl.innerHTML = `<div class="empty" style="color:#be123c">Failed to load results.</div>`
  }
}

function showErr(msg) { const e = document.getElementById("err"); e.textContent = msg; e.style.display = "block" }
function hideErr() { document.getElementById("err").style.display = "none" }
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(PAGE)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
