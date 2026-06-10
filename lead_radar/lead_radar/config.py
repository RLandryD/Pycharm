"""Configuration. All credentials come from environment variables / .env — never hardcoded."""
import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # graceful degradation: .env support is optional
    pass

# Default signal queries (Spanish + English). Override with --query on the CLI.
DEFAULT_QUERIES = [
    'SAP PI/PO migration Mexico',
    'SAP Integration Suite Mexico',
    'SAP CPI Mexico',
    'SAP PI PO migración México',
    'SAP Process Orchestration Mexico',
]


@dataclass
class Settings:
    rapidapi_key: str = field(default_factory=lambda: os.getenv("RAPIDAPI_KEY", ""))
    adzuna_app_id: str = field(default_factory=lambda: os.getenv("ADZUNA_APP_ID", ""))
    adzuna_app_key: str = field(default_factory=lambda: os.getenv("ADZUNA_APP_KEY", ""))
    serper_api_key: str = field(default_factory=lambda: os.getenv("SERPER_API_KEY", ""))
    google_cse_key: str = field(default_factory=lambda: os.getenv("GOOGLE_CSE_KEY", ""))
    google_cse_cx: str = field(default_factory=lambda: os.getenv("GOOGLE_CSE_CX", ""))
    request_timeout: int = 20

    def available_sources(self) -> list:
        out = []
        if self.rapidapi_key:
            out.append("jsearch")
        if self.adzuna_app_id and self.adzuna_app_key:
            out.append("adzuna")
        return out
