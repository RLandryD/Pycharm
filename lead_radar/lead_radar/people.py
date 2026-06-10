"""People discovery via Google X-ray search.

Two modes:
1. Always: generate ready-to-click Google X-ray URLs per company x role tier
   (you click, review, decide — fully manual on LinkedIn itself).
2. Optional: Google Custom Search API (official, ToS-compliant) pulls the
   indexed name/title/snippet for candidate profiles so you triage in the
   spreadsheet first. We never fetch linkedin.com pages directly.
"""
import urllib.parse

import requests

from .config import Settings
from .log import feature, log_event
from .models import PersonCandidate
from .scoring import norm

# Ranked outreach roles (rank 1 = highest priority), EN + ES variants.
ROLE_PRIORITY = [
    (1, "SAP Delivery Manager", ["sap delivery manager", "gerente de entrega sap",
                                 "sap project manager", "delivery lead sap"]),
    (2, "Director of Enterprise Integration", ["director of enterprise integration",
                                               "integration director",
                                               "director de integracion"]),
    (3, "Head of SAP CoE", ["sap coe", "center of excellence", "centro de excelencia sap",
                            "sap competence center", "lider sap"]),
    (4, "SAP Integration Architect", ["integration architect", "arquitecto de integracion",
                                      "sap cpi", "integration suite", "pi/po"]),
    (5, "SAP Basis / NetWeaver Manager", ["sap basis", "netweaver", "basis manager",
                                          "basis lead"]),
    (6, "Director of Global ERP", ["director of global erp", "erp director",
                                   "director erp", "head of erp", "s/4hana program"]),
    (7, "Enterprise Architecture Manager", ["enterprise architect", "enterprise architecture",
                                            "arquitecto empresarial"]),
    (8, "VP of Enterprise Applications", ["vp of enterprise applications",
                                          "vice president enterprise applications",
                                          "vp applications", "cio", "cto"]),
    (9, "IT Infrastructure Director", ["it infrastructure director",
                                       "director de infraestructura"]),
    (10, "Head of Digital Transformation", ["digital transformation",
                                            "transformacion digital"]),
]

RECRUITER_TERMS = ["recruiter", "recruiting", "recruitment", "talent acquisition",
                   "atraccion de talento", "headhunter", "sourcer", "staffing",
                   "recursos humanos", "people & culture", "people and culture",
                   "hiring", "rrhh"]
XRAY_EXCLUSIONS = ' -recruiter -"talent acquisition" -recruitment -headhunter'

CSE_URL = "https://www.googleapis.com/customsearch/v1"
SERPER_URL = "https://google.serper.dev/search"


def xray_query(company: str, variants: list) -> str:
    roles = " OR ".join(f'"{v}"' for v in variants[:4])
    return f'site:linkedin.com/in "{company}" ({roles})' + XRAY_EXCLUSIONS


def is_recruiter(candidate) -> bool:
    """True when the profile reads as recruiting/HR rather than a problem owner."""
    text = norm(f"{candidate.title} {candidate.snippet}")
    return any(term in text for term in RECRUITER_TERMS)


def xray_url(company: str, variants: list) -> str:
    return "https://www.google.com/search?q=" + urllib.parse.quote(
        xray_query(company, variants))


def build_search_links(companies: list, top_roles: int = 6) -> list:
    """[(company, rank, role_label, url), ...] for the SearchLinks sheet."""
    links = []
    for company in companies:
        for rank, label, variants in ROLE_PRIORITY[:top_roles]:
            links.append((company, rank, label, xray_url(company, variants)))
    return links


def match_role(title_text: str):
    """Return (rank, label) of the best-priority role found in a title, or (None, '')."""
    t = norm(title_text)
    for rank, label, variants in ROLE_PRIORITY:
        if any(v in t for v in variants):
            return rank, label
    return None, ""


def parse_cse_payload(payload: dict, company: str) -> list:
    out = []
    for item in payload.get("items", []) or []:
        title = item.get("title") or ""          # usually "Name - Title - Company | LinkedIn"
        parts = [p.strip() for p in title.replace("| LinkedIn", "").split(" - ")]
        name = parts[0] if parts else ""
        role_text = " - ".join(parts[1:]) if len(parts) > 1 else ""
        rank, label = match_role(title + " " + (item.get("snippet") or ""))
        out.append(PersonCandidate(
            company=company,
            name=name,
            title=role_text,
            profile_url=item.get("link") or "",
            snippet=(item.get("snippet") or "")[:300],
            role_rank=rank,
            role_matched=label,
        ))
    return out


def parse_serper_payload(payload: dict, company: str) -> list:
    """Serper returns {'organic': [{'title','link','snippet'}, ...]} — same
    shape of information as CSE, different envelope."""
    out = []
    for item in payload.get("organic", []) or []:
        title = item.get("title") or ""
        parts = [p.strip() for p in title.replace("| LinkedIn", "").split(" - ")]
        name = parts[0] if parts else ""
        role_text = " - ".join(parts[1:]) if len(parts) > 1 else ""
        rank, label = match_role(title + " " + (item.get("snippet") or ""))
        out.append(PersonCandidate(
            company=company,
            name=name,
            title=role_text,
            profile_url=item.get("link") or "",
            snippet=(item.get("snippet") or "")[:300],
            role_rank=rank,
            role_matched=label,
        ))
    return out


def _fetch_role_serper(settings: Settings, company: str, variants: list,
                       per_role: int) -> list:
    r = requests.post(SERPER_URL,
                      headers={"X-API-KEY": settings.serper_api_key,
                               "Content-Type": "application/json"},
                      json={"q": xray_query(company, variants), "num": per_role},
                      timeout=settings.request_timeout)
    r.raise_for_status()
    return parse_serper_payload(r.json(), company)


def _fetch_role_cse(settings: Settings, company: str, variants: list,
                    per_role: int) -> list:
    params = {"key": settings.google_cse_key, "cx": settings.google_cse_cx,
              "q": xray_query(company, variants), "num": per_role}
    r = requests.get(CSE_URL, params=params, timeout=settings.request_timeout)
    r.raise_for_status()
    return parse_cse_payload(r.json(), company)


def people_provider(settings: Settings) -> str:
    """Serper preferred (open to new signups); CSE kept for grandfathered keys."""
    if settings.serper_api_key:
        return "serper"
    if settings.google_cse_key and settings.google_cse_cx:
        return "google_cse"
    return ""


def fetch_people(settings: Settings, company: str, top_roles: int = 4,
                 per_role: int = 5) -> list:
    """People lookup via the configured provider. Skips (logged) when none."""
    provider = people_provider(settings)
    if not provider:
        log_event("source_skipped", source="people_search",
                  reason="No SERPER_API_KEY or GOOGLE_CSE keys set")
        return []
    fetch_role = _fetch_role_serper if provider == "serper" else _fetch_role_cse
    people = []
    with feature("people_search", company=company, provider=provider):
        for rank, label, variants in ROLE_PRIORITY[:top_roles]:
            try:
                batch = fetch_role(settings, company, variants, per_role)
                for p in batch:
                    if p.role_rank is None:
                        p.role_rank, p.role_matched = rank, label
                people.extend(batch)
                log_event("people_role_query", provider=provider, company=company,
                          role=label, results=len(batch))
            except requests.RequestException as e:
                log_event("source_gap", source=provider, company=company,
                          role=label, error=str(e))
                break   # quota errors would repeat; stop early for this company
    n_before = len(people)
    people = [p for p in people if not is_recruiter(p)]
    if n_before != len(people):
        log_event("recruiters_filtered", company=company,
                  removed=n_before - len(people))
    # dedupe by profile URL, keep best (lowest) rank
    seen = {}
    for p in people:
        if p.profile_url not in seen or (p.role_rank or 99) < (seen[p.profile_url].role_rank or 99):
            seen[p.profile_url] = p
    out = sorted(seen.values(), key=lambda x: (x.role_rank or 99))
    return out
