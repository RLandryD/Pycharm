# lead_radar — PI/PO migration lead finder

Finds companies showing PI/PO → Integration Suite migration signals (job postings),
scores them, and accelerates finding the right people — without violating LinkedIn ToS.

## What it automates vs. what stays manual

| Step | How |
|---|---|
| Collect job postings (Mexico, last year) | JSearch + Adzuna APIs (legitimate aggregators) |
| Score & rank companies | Additive numeric scoring (PI/PO +40, migration +25, CPI +20, depth, recency, MX) |
| Classify end-client vs. staffing/SI channel | Curated firm list + heuristics |
| Find people | Google X-ray via official Custom Search API (Google's index, never scraping LinkedIn) + ready-to-click search URLs |
| Verify profiles & connect | **YOU — manually.** No bots touch LinkedIn. |

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m lead_radar.cli setup    # guided: opens signup pages, validates keys live, writes .env
python -m lead_radar.cli check    # re-validate keys anytime
```

(Manual alternative: `cp .env.example .env` and fill it in yourself.)

Keys (all have free tiers; none are required for `companies` mode):
- `RAPIDAPI_KEY` — JSearch: https://rapidapi.com (search "JSearch")
- `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` — https://developer.adzuna.com
- `SERPER_API_KEY` — https://serper.dev (2,500 free queries, no card). Legacy alternative: `GOOGLE_CSE_KEY`/`GOOGLE_CSE_CX` (closed to new customers, EOL Jan 2027).
  Create a Programmable Search Engine at https://programmablesearchengine.google.com
  with "Search the entire web" enabled; the CX is its Search engine ID.

Keep `.env` out of git (already covered if you reuse your global gitignore pattern;
add `.env` explicitly to be safe).

## Usage

```bash
# Weekly radar: scan boards, rank companies, generate people-search links
python -m lead_radar.cli scan -o leads.xlsx

# Custom queries, deeper pagination, plus Google CSE people lookup
python -m lead_radar.cli scan -q "SAP PI/PO migración" -q "Integration Suite Monterrey" \
    --pages 2 --people --min-score 40 -o leads.xlsx

# Skip the scan: people discovery for companies you already target
python -m lead_radar.cli companies -c CEMEX -c "Grupo Truper" --people -o targets.xlsx
```

## Output (xlsx, 4 sheets)

- **Companies** — ranked leads, end_client vs channel (channel rows highlighted),
  Tier/Status/Next action columns for your pipeline triage.
- **Postings** — every matched posting with score breakdown, so you can audit
  why a company ranked where it did.
- **People** — CSE candidates with role rank (1 = SAP Delivery Manager …
  10 = Head of Digital Transformation), profile URL, and a TO VERIFY status.
- **SearchLinks** — one click per company × role: opens Google with the X-ray
  query prefilled. Your manual fallback when CSE quota runs out.

## Blocklist & recruiter filtering

- `blocklist.txt` (one company per line, `#` comments) drops companies from all
  output. Seeded with Xideral, Deloitte, AllianceIT — edit freely, no code changes.
- Consulting/staffing firms are auto-classified as `channel`; they never consume
  people-search credits. Add `--exclude-channel` to remove them from the sheets
  entirely.
- People results exclude recruiters/talent-acquisition profiles automatically
  (both in the search query and post-filtering); removals are logged as
  `recruiters_filtered`.

## Workflow suggestion

1. Monday: `scan` → triage Companies sheet (10 min).
2. For each new company ≥ score 60: open its SearchLinks, verify 2 people
   (one architect/Basis, one delivery manager/CoE), set status.
3. Connect manually on LinkedIn with your template. Log date in People sheet.

## CSE quota math

100 free queries/day. Default `--people` uses `--top-roles` (4) queries per
company, capped at `--max-people-companies` (10) = 40 queries per run.

## Tests

```bash
python -m pytest tests/ -q
```
