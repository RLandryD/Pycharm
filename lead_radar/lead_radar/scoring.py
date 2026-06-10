"""Posting and company scoring.

Numeric additive scoring (not categorical buckets): each signal adds points,
the total ranks the lead. All matching is accent-insensitive and case-insensitive.
"""
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime

from .models import CompanyLead, Posting

# ---- signal tables -----------------------------------------------------------

PIPO_TERMS = ["pi/po", "pi-po", "pi po", "process orchestration",
              "process integration", "netweaver", "sap po 7", "sap pi 7"]
CPI_TERMS = ["integration suite", "sap cpi", "cloud integration",
             "cloud platform integration", "sap btp", "api management"]
MIGRATION_TERMS = ["migration", "migracion", "migrar", "migrate", "sunset",
                   "decommission", "modernizacion", "modernization"]
DEPTH_TERMS = ["iflow", "groovy", "idoc", "odata", "edi", "edifact", "x12",
               "sftp", "soap", "rfc", "bapi", "cloud connector", "isa-m",
               "integration assessment", "partner directory", "tpm"]
MX_TERMS = ["mexico", "monterrey", "guadalajara", "queretaro", "puebla",
            "san luis potosi", "cdmx", "ciudad de mexico", "tijuana",
            "aguascalientes", "leon", "toluca", "saltillo", "remote mexico"]

# Known staffing / SI / consultancy firms -> channel leads, not end clients.
CHANNEL_COMPANIES = {
    "infosys", "tcs", "tata consultancy", "accenture", "deloitte", "capgemini",
    "cognizant", "wipro", "hcl", "luxoft", "globant", "neoris", "kyndryl",
    "ibm", "htc global", "epam", "softtek", "mhp", "westernacher",
    "msg global", "ids comercial", "manpower", "experis", "randstad", "adecco",
    "hays", "michael page", "eas consulting", "towa", "nttdata", "ntt data",
    "birlasoft", "techmahindra", "tech mahindra", "lti", "ltimindtree",
    "entropia", "acute talent", "data solutions", "xideral", "allianceit",
    "alliance it",
}
CHANNEL_HINTS = ["consulting", "consultoria", "staffing", "recruiting",
                 "recruitment", "talent", "outsourcing", "headhunt",
                 "it services", "servicios de ti"]


def load_blocklist(path: str = "blocklist.txt") -> set:
    """One company name per line; '#' comments allowed. Matching is
    accent/case-insensitive substring on the normalized company name."""
    import os
    if not os.path.exists(path):
        return set()
    out = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                out.add(norm(line))
    return out


def is_blocked(company: str, blocklist: set) -> bool:
    c = norm(company)
    return any(b in c for b in blocklist)


def norm(text: str) -> str:
    """Lowercase + strip accents so 'Migración' matches 'migracion'."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower()


def _hit(text: str, terms: list) -> list:
    return [t for t in terms if t in text]


def _days_ago(posted_date: str, today: date = None) -> int:
    if not posted_date:
        return 9999
    today = today or date.today()
    try:
        d = datetime.fromisoformat(posted_date.replace("Z", "+00:00")).date()
        return (today - d).days
    except ValueError:
        return 9999


def score_posting(p: Posting, today: date = None) -> Posting:
    """Additive scoring. Mutates and returns the posting."""
    text = norm(f"{p.title} {p.description}")
    score, reasons = 0, []

    pipo = _hit(text, PIPO_TERMS)
    if pipo:
        score += 40
        reasons.append(f"+40 PI/PO stack ({pipo[0]})")

    cpi = _hit(text, CPI_TERMS)
    if cpi:
        score += 20
        reasons.append(f"+20 Integration Suite/CPI ({cpi[0]})")

    if _hit(text, MIGRATION_TERMS) and (pipo or cpi):
        score += 25
        reasons.append("+25 migration context")

    depth = _hit(text, DEPTH_TERMS)
    if depth:
        pts = min(10, 2 * len(depth))
        score += pts
        reasons.append(f"+{pts} technical depth ({', '.join(depth[:5])})")

    days = _days_ago(p.posted_date, today)
    if days <= 30:
        score += 10
        reasons.append("+10 posted <=30d")
    elif days <= 90:
        score += 5
        reasons.append("+5 posted <=90d")

    if _hit(norm(p.location) + " " + text, MX_TERMS):
        score += 5
        reasons.append("+5 Mexico location")

    p.score, p.score_reasons = score, reasons
    return p


def classify_company(company: str) -> str:
    c = norm(company)
    if any(k in c for k in CHANNEL_COMPANIES):
        return "channel"
    if any(h in c for h in CHANNEL_HINTS):
        return "channel"
    return "end_client"


def _company_key(company: str) -> str:
    """Collapse near-duplicate company names (suffixes, punctuation)."""
    c = norm(company)
    c = re.sub(r"\b(s\.?a\.?b?\.?|de c\.?v\.?|s de rl|inc|llc|gmbh|group|grupo|corp(oration)?|ltd|co)\b", " ", c)
    c = re.sub(r"[^a-z0-9 ]", " ", c)
    return re.sub(r"\s+", " ", c).strip()


def aggregate_companies(postings: list) -> list:
    """Roll postings up to ranked CompanyLead list."""
    buckets = defaultdict(list)
    for p in postings:
        if p.company:
            buckets[_company_key(p.company)].append(p)

    leads = []
    for _, plist in buckets.items():
        plist.sort(key=lambda x: x.score, reverse=True)
        best = plist[0]
        bonus = min(10, 5 * (len(plist) - 1))   # multiple postings = bigger program
        signals = []
        for p in plist[:3]:
            signals.extend(p.score_reasons)
        leads.append(CompanyLead(
            company=best.company,
            score=best.score + bonus,
            lead_type=classify_company(best.company),
            n_postings=len(plist),
            best_posting_title=best.title,
            best_posting_url=best.url,
            signals=sorted(set(signals)),
            postings=plist,
        ))
    leads.sort(key=lambda l: l.score, reverse=True)
    return leads
