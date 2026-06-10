"""JSearch (RapidAPI) source — aggregates Google for Jobs, covers Mexico.

Free tier exists; key goes in env var RAPIDAPI_KEY. Failures degrade gracefully:
we log the gap and return partial results instead of crashing the run.
"""
import requests

from ..config import Settings
from ..log import feature, log_event
from ..models import Posting

API_URL = "https://jsearch.p.rapidapi.com/search"


def parse_jsearch_payload(payload: dict, query: str) -> list:
    """Pure parser, unit-testable without network."""
    out = []
    for item in payload.get("data", []) or []:
        out.append(Posting(
            source="jsearch",
            company=item.get("employer_name") or "",
            title=item.get("job_title") or "",
            location=", ".join(filter(None, [item.get("job_city"),
                                             item.get("job_state"),
                                             item.get("job_country")])),
            description=(item.get("job_description") or "")[:4000],
            url=item.get("job_apply_link") or item.get("job_google_link") or "",
            posted_date=(item.get("job_posted_at_datetime_utc") or "")[:10],
            query=query,
        ))
    return out


def fetch(settings: Settings, query: str, pages: int = 1) -> list:
    if not settings.rapidapi_key:
        log_event("source_skipped", source="jsearch", reason="RAPIDAPI_KEY not set")
        return []
    postings = []
    with feature("jsearch_fetch", query=query, pages=pages):
        headers = {"X-RapidAPI-Key": settings.rapidapi_key,
                   "X-RapidAPI-Host": "jsearch.p.rapidapi.com"}
        for page in range(1, pages + 1):
            params = {"query": query, "page": page, "num_pages": 1,
                      "country": "mx", "date_posted": "year"}
            try:
                r = requests.get(API_URL, headers=headers, params=params,
                                 timeout=settings.request_timeout)
                r.raise_for_status()
                batch = parse_jsearch_payload(r.json(), query)
                postings.extend(batch)
                log_event("jsearch_page", page=page, results=len(batch))
                if not batch:
                    break
            except requests.RequestException as e:
                # graceful degradation: partial result + logged gap
                log_event("source_gap", source="jsearch", page=page, error=str(e))
                break
    return postings
