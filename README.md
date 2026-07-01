# Prospect Research Agent

An AI-powered web application that takes a company website URL, scrapes relevant information, and returns structured business intelligence — including contact details, core services, target customers, pain points, and a personalized cold outreach opener.

## What it does

You paste a company URL, click Enrich, and within 30 seconds you get:
- Company name and website
- Address and contact details (email, phone)
- Core service and target customer
- Probable pain point
- A ready-to-send cold outreach opener

All results are saved and viewable in a searchable table.

## How it works

**Scraping** — Uses a 3-fallback approach to extract content from any website. First tries the sitemap, then fuzzy-matches internal links to find About/Contact/Services pages, and finally falls back to guessing common paths. Boilerplate like navbars, footers, and scripts are stripped before anything is processed.

**AI Enrichment** — Cleaned text is sent to Llama 3.3 70B via Groq API. The prompt is structured to extract business insights while strictly preventing hallucination of contact details.

**Contact Extraction** — Emails and phone numbers are extracted using regex only, completely separate from the LLM, to ensure accuracy.

## Tech Stack

- **Backend** — Python, Flask
- **Scraping** — Requests, BeautifulSoup, RapidFuzz
- **AI** — Llama 3.3 70B via Groq API
- **Frontend** — Vanilla HTML, CSS, JavaScript
- **Deployment** — Render

## Running locally

```bash
git clone https://github.com/ShivratnaK/prospect-research-agent.git
cd prospect-research-agent
pip install -r requirements.txt
```

Set your Groq API key:
```bash
# Windows
set GROQ_API_KEY=your_key_here

# Mac/Linux
export GROQ_API_KEY=your_key_here
```

Run the app:
```bash
python app.py
```

Open `http://localhost:5000` in your browser.

## API

**POST /enrich**
```json
{ "url": "https://stripe.com" }
```
Returns a structured company profile.

**GET /results**

Returns all previously enriched companies as a JSON array.

## Project Structure

```
prospect-research-agent/
├── app.py            # Flask app — scraping, AI enrichment, APIs, frontend
├── requirements.txt  # Dependencies
├── Procfile          # Render start command
└── render.yaml       # Render deployment config
```
