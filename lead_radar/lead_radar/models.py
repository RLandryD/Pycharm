"""Dataclasses shared across the pipeline."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Posting:
    source: str                 # jsearch | adzuna | manual
    company: str
    title: str
    location: str = ""
    description: str = ""
    url: str = ""
    posted_date: str = ""       # ISO date string if known
    query: str = ""
    score: int = 0
    score_reasons: list = field(default_factory=list)


@dataclass
class CompanyLead:
    company: str
    score: int = 0
    lead_type: str = "end_client"   # end_client | channel
    n_postings: int = 0
    best_posting_title: str = ""
    best_posting_url: str = ""
    signals: list = field(default_factory=list)
    postings: list = field(default_factory=list)


@dataclass
class PersonCandidate:
    company: str
    name: str = ""
    title: str = ""
    profile_url: str = ""
    snippet: str = ""
    role_rank: Optional[int] = None   # 1 = top priority role
    role_matched: str = ""
    status: str = "TO VERIFY"
