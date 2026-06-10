"""Adzuna source (free API, country code mx).

Keys in env vars ADZUNA_APP_ID / ADZUNA_APP_KEY. If the mx endpoint is
unavailable for an account tier, we log the gap and continue.
"""
import requests

from ..config import Settings
from ..log import feature, log_event
from ..models import Posting

API_URL = "https://api.adzuna.com/v1/api/jobs/mx/search/{page}"


def parse_adzuna_payload(payload: dict, query: str) -> list:
    out = []
    for item in payload.get("results", []) or []:
        out.append(Posting(
            source="adzuna",
            company=(item.get("company") or {}).get("display_name", ""),
            title=item.get("title") or "",
            location=(item.get("location") or {}).get("display_name", ""),
            description=(item.get("description") or "")[:4000],
            url=item.get("redirect_url") or "",
            posted_date=(item.get("created") or "")[:10],
            query=query,
        ))
    return out


def fetch(settings: Settings, query: str, pages: int = 1) -> list:
    if not (settings.adzuna_app_id and settings.adzuna_app_key):
        log_event("source_skipped", source="adzuna", reason="ADZUNA keys not set")
        return []
    postings = []
    with feature("adzuna_fetch", query=query, pages=pages):
        for page in range(1, pages + 1):
            params = {"app_id": settings.adzuna_app_id,
                      "app_key": settings.adzuna_app_key,
                      "what": query, "results_per_page": 50,
                      "max_days_old": 365, "content-type": "application/json"}
            try:
                r = requests.get(API_URL.format(page=page), params=params,
                                 timeout=settings.request_timeout)
                r.raise_for_status()
                batch = parse_adzuna_payload(r.json(), query)
                postings.extend(batch)
                log_event("adzuna_page", page=page, results=len(batch))
                if not batch:
                    break
            except requests.RequestException as e:
                log_event("source_gap", source="adzuna", page=page, error=str(e))
                break
    return postings
