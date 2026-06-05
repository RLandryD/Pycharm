"""
fetcher/security_material.py

Access the client tenant's Security Material via the CPI API:
  - list credential names (UserCredentials)
  - list keystore entries / certificate aliases
  - check whether a named credential exists
  - (optionally) create a User Credential

IMPORTANT (and correct by design): the CPI API NEVER returns secret values.
You can see that a credential NAMED 'X' exists, but not its password. So this
module validates EXISTENCE and NAMES, and can CREATE credentials — it cannot
read secrets. That's the secure, intended behaviour and exactly what we want:
iFlows should reference a credential NAME, never embed a real password.

This also doubles as a connection self-test: listing security material is a
read that confirms auth + the right role from another angle.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import requests

logger = logging.getLogger("cpi.security_material")


@dataclass
class CredentialInfo:
    name: str
    kind: str = "UserCredentials"      # UserCredentials | OAuth2 | etc.
    description: str = ""


@dataclass
class SecurityMaterialReport:
    credentials: list[CredentialInfo] = field(default_factory=list)
    keystore_aliases: list[str] = field(default_factory=list)
    reachable: bool = False
    error: str = ""

    def has_credential(self, name: str) -> bool:
        n = (name or "").strip().lower()
        return any(c.name.strip().lower() == n for c in self.credentials)


class SecurityMaterialClient:
    def __init__(self, base_url: str, session: requests.Session):
        self.base_url = base_url.rstrip("/")
        self.session  = session
        self._csrf: Optional[str] = None

    def _ensure_csrf(self) -> Optional[str]:
        if self._csrf:
            return self._csrf
        try:
            r = self.session.get(f"{self.base_url}/api/v1/",
                                 headers={"X-CSRF-Token": "Fetch"}, timeout=20)
            self._csrf = r.headers.get("X-CSRF-Token")
        except Exception as exc:
            logger.error("CSRF fetch failed: %s", exc)
        return self._csrf

    def list_credentials(self) -> SecurityMaterialReport:
        """List UserCredentials names (no secrets). Also validates connection."""
        report = SecurityMaterialReport()
        try:
            r = self.session.get(
                f"{self.base_url}/api/v1/UserCredentials",
                params={"$format": "json"}, timeout=30)
            if r.status_code == 200:
                report.reachable = True
                data = r.json()
                results = (data.get("d", {}).get("results")
                           if isinstance(data.get("d"), dict) else data.get("d")) or []
                for item in results:
                    report.credentials.append(CredentialInfo(
                        name=item.get("Name", ""),
                        kind=item.get("Kind", "UserCredentials"),
                        description=item.get("Description", "")))
                logger.info("Listed %d credentials", len(report.credentials))
            elif r.status_code == 403:
                report.error = ("403: auth works but the OAuth client lacks the "
                                "security-material read role.")
                logger.warning(report.error)
            else:
                report.error = f"HTTP {r.status_code}: {r.text[:150]}"
        except Exception as exc:
            report.error = str(exc)
            logger.error("list_credentials failed: %s", exc)
        return report

    def create_user_credential(self, name: str, user: str, password: str,
                               description: str = "") -> tuple[bool, str]:
        """Create a User Credential so iFlows can reference it by NAME instead
        of embedding a raw password. Returns (ok, message)."""
        token = self._ensure_csrf()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-CSRF-Token"] = token
        payload = {
            "Name": name, "Kind": "default",
            "User": user, "Password": password,
            "Description": description or f"Created by migration tool",
        }
        try:
            r = self.session.post(
                f"{self.base_url}/api/v1/UserCredentials",
                json=payload, headers=headers, timeout=30)
            if r.status_code in (200, 201):
                logger.info("Created credential %s", name)
                return True, f"Credential '{name}' created"
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as exc:
            return False, str(exc)


def audit_interface_credentials(interfaces: list,
                                report: SecurityMaterialReport) -> list[dict]:
    """Cross-check interfaces against existing credentials. Flags interfaces
    that reference a credential that doesn't exist, or that appear to use a
    raw/'dangerous' auth instead of a named credential."""
    findings = []
    for iface in interfaces:
        cred = getattr(iface, "receiver_credential", "") or \
               getattr(iface, "credential_name", "")
        name = getattr(iface, "name", "?")
        if cred:
            if not report.has_credential(cred):
                findings.append({
                    "interface": name, "severity": "warning",
                    "issue": f"References credential '{cred}' which does NOT "
                             f"exist in Security Material — create it first."})
        else:
            findings.append({
                "interface": name, "severity": "caution",
                "issue": "No named credential — ensure it doesn't embed a raw "
                         "user/password (use a Security Material alias)."})
    return findings
