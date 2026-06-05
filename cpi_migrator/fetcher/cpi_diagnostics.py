"""
fetcher/cpi_diagnostics.py

Step-by-step CPI connection diagnostics. When an upload fails with 401/403,
this isolates WHICH step failed and WHY, with detailed logging, so the auth
problem can actually be debugged instead of guessed at.

The CPI API write flow has several places auth can break:
  1. OAuth2 token fetch (wrong token_url / client_id / client_secret)
  2. Token works for READ but the OAuth client lacks the right role
     (e.g. missing 'WorkspacePackagesEdit' / 'IntegrationContent.Write')
  3. CSRF token missing on POST (CPI requires X-CSRF-Token for writes —
     a very common cause of 401/403 that looks like an auth failure)
  4. Basic auth used where the API requires OAuth (tenant login user/pass is
     NOT the same as the API OAuth client credentials)

Each check logs its outcome and returns a structured result.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import requests

logger = logging.getLogger("cpi.diagnostics")


@dataclass
class DiagnosticStep:
    name: str
    ok: bool
    detail: str = ""
    status_code: Optional[int] = None


@dataclass
class DiagnosticReport:
    steps: list[DiagnosticStep] = field(default_factory=list)
    overall_ok: bool = False

    def add(self, step: DiagnosticStep):
        self.steps.append(step)
        logger.info("[%s] %s — %s%s",
                    "OK" if step.ok else "FAIL",
                    step.name,
                    step.detail,
                    f" (HTTP {step.status_code})" if step.status_code else "")

    def summary(self) -> str:
        lines = []
        for s in self.steps:
            icon = "✅" if s.ok else "❌"
            code = f" [HTTP {s.status_code}]" if s.status_code else ""
            lines.append(f"{icon} {s.name}{code}: {s.detail}")
        return "\n".join(lines)


def fetch_csrf_token(session: requests.Session, base_url: str) -> Optional[str]:
    """Fetch an X-CSRF-Token via a GET with the fetch header.

    CPI requires this token on all POST/PUT/DELETE. Without it, writes get
    401/403 even with valid auth. This is the single most common cause of
    "upload failed: 401" when READ operations work fine.
    """
    try:
        resp = session.get(
            f"{base_url.rstrip('/')}/api/v1/",
            headers={"X-CSRF-Token": "Fetch"},
            timeout=20,
        )
        token = resp.headers.get("X-CSRF-Token")
        if token:
            logger.info("CSRF token fetched successfully")
            return token
        logger.warning("No X-CSRF-Token in response headers (status %d)", resp.status_code)
        return None
    except Exception as exc:
        logger.error("CSRF token fetch error: %s", exc)
        return None


def run_diagnostics(base_url: str, session: requests.Session,
                    auth_kind: str = "unknown") -> DiagnosticReport:
    """Run the full connection diagnostic chain and return a structured report.

    auth_kind is just for reporting ('oauth' / 'basic' / 'unknown').
    """
    report = DiagnosticReport()
    base = base_url.rstrip("/")

    # Step 0: is there an Authorization header / auth on the session?
    has_bearer = "Authorization" in session.headers and \
                 str(session.headers.get("Authorization", "")).startswith("Bearer ")
    has_basic = session.auth is not None
    if has_bearer:
        report.add(DiagnosticStep("Auth method", True,
                                  "Session uses OAuth2 Bearer token (correct for CPI API)"))
    elif has_basic:
        report.add(DiagnosticStep("Auth method", True,
            "Session uses Basic auth. NOTE: the CPI OData API usually needs an "
            "OAuth2 client (from a BTP service key), NOT your tenant login "
            "user/password. If reads fail with 401, this is the likely cause."))
    else:
        report.add(DiagnosticStep("Auth method", False,
                                  "Session has no Authorization header and no auth set"))

    # Step 1: READ test — list packages (validates the token is accepted at all)
    try:
        resp = session.get(f"{base}/api/v1/IntegrationPackages",
                           params={"$format": "json", "$top": 1}, timeout=20)
        if resp.status_code == 200:
            report.add(DiagnosticStep("Read access (list packages)", True,
                                      "Token accepted for read", resp.status_code))
        elif resp.status_code == 401:
            report.add(DiagnosticStep("Read access (list packages)", False,
                "401 Unauthorized on READ. The credentials/token are not valid "
                "for this tenant. If using Basic auth, switch to the OAuth2 "
                "client from your BTP service key. If using OAuth, check "
                "token_url / client_id / client_secret.", resp.status_code))
            return report  # no point continuing
        elif resp.status_code == 403:
            report.add(DiagnosticStep("Read access (list packages)", False,
                "403 Forbidden on READ. Auth works but the OAuth client lacks "
                "the read role (e.g. 'IntegrationContent.Read').", resp.status_code))
            return report
        else:
            report.add(DiagnosticStep("Read access (list packages)", False,
                f"Unexpected status: {resp.text[:150]}", resp.status_code))
            return report
    except Exception as exc:
        report.add(DiagnosticStep("Read access (list packages)", False,
                                  f"Connection error: {exc}"))
        return report

    # Step 2: CSRF token fetch (needed for writes)
    token = fetch_csrf_token(session, base)
    if token:
        report.add(DiagnosticStep("CSRF token fetch", True,
                                  "X-CSRF-Token obtained (required for upload)"))
    else:
        report.add(DiagnosticStep("CSRF token fetch", False,
            "Could NOT obtain X-CSRF-Token. CPI requires this on every "
            "POST/PUT/DELETE — without it, uploads fail with 401/403 even "
            "though reads work. This is very likely your upload blocker."))

    # Step 3: WRITE capability probe — a harmless metadata GET on the write
    # endpoint to confirm the role is present (we don't actually create here).
    try:
        resp = session.get(f"{base}/api/v1/IntegrationDesigntimeArtifacts",
                           params={"$format": "json", "$top": 1}, timeout=20)
        if resp.status_code in (200, 201):
            report.add(DiagnosticStep("Designtime artifacts endpoint", True,
                                      "Write endpoint reachable", resp.status_code))
        else:
            report.add(DiagnosticStep("Designtime artifacts endpoint", False,
                f"Status {resp.status_code}: {resp.text[:120]}", resp.status_code))
    except Exception as exc:
        report.add(DiagnosticStep("Designtime artifacts endpoint", False, str(exc)))

    report.overall_ok = all(s.ok for s in report.steps)
    return report
