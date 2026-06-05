"""
apim/model.py

Data model for SAP API Management (Program 2). Mirrors the real APIM object
hierarchy so the rest of the module can reason about it:

    APIProxy      - the managed facade over a backend (an iFlow, an OData
                    service, any HTTP target). Has a base path + target.
    APIProduct    - a bundle of one or more proxies sold/exposed as a unit,
                    with a rate-limit quota.
    Application   - a consumer registration that subscribes to products and
                    holds credentials (API keys / OAuth client).
    APIKey        - a credential issued to an application, with lifecycle
                    state (active / revoked / expired).

This is the repurposed "app + API key lifecycle model" from the Program 2
plan. It's the shared spine the policy library and proxy generator build on,
and it maps cleanly onto the per-environment credential matrix Program 1
needs too (so the two programs share it).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional


class KeyState(Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"
    PENDING = "pending"


class ProxyAuthType(Enum):
    NONE = "none"
    API_KEY = "api_key"
    OAUTH2 = "oauth2"
    BASIC = "basic"
    CLIENT_CERT = "client_cert"


@dataclass
class APIProxy:
    name: str
    base_path: str                       # e.g. /v1/orders
    target_url: str                      # backend the proxy fronts
    auth_type: ProxyAuthType = ProxyAuthType.API_KEY
    description: str = ""
    policies: list[str] = field(default_factory=list)   # policy names attached
    # If this proxy fronts a migrated iFlow, link it for traceability
    source_iflow: str = ""


@dataclass
class APIProduct:
    name: str
    proxies: list[str] = field(default_factory=list)    # proxy names in bundle
    quota_requests: int = 1000           # requests per quota_interval
    quota_interval: str = "hour"         # second|minute|hour|day|month
    description: str = ""
    environments: list[str] = field(default_factory=lambda: ["dev"])


@dataclass
class APIKey:
    key_value: str
    state: KeyState = KeyState.ACTIVE
    issued_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None

    def is_valid(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.utcnow()
        if self.state != KeyState.ACTIVE:
            return False
        if self.expires_at and now >= self.expires_at:
            return False
        return True


@dataclass
class Application:
    name: str
    subscribed_products: list[str] = field(default_factory=list)
    keys: list[APIKey] = field(default_factory=list)
    developer_email: str = ""
    description: str = ""

    def issue_key(self, ttl_days: Optional[int] = None) -> APIKey:
        """Issue a new API key, optionally with a time-to-live."""
        expires = None
        if ttl_days is not None:
            expires = datetime.utcnow() + timedelta(days=ttl_days)
        key = APIKey(key_value=secrets.token_urlsafe(24), expires_at=expires)
        self.keys.append(key)
        return key

    def revoke_key(self, key_value: str) -> bool:
        for k in self.keys:
            if k.key_value == key_value:
                k.state = KeyState.REVOKED
                return True
        return False

    def active_keys(self, now: Optional[datetime] = None) -> list[APIKey]:
        return [k for k in self.keys if k.is_valid(now)]


@dataclass
class APIMLandscape:
    """Top-level container holding all APIM objects for a project."""
    proxies: list[APIProxy] = field(default_factory=list)
    products: list[APIProduct] = field(default_factory=list)
    applications: list[Application] = field(default_factory=list)

    def find_proxy(self, name: str) -> Optional[APIProxy]:
        return next((p for p in self.proxies if p.name == name), None)

    def find_product(self, name: str) -> Optional[APIProduct]:
        return next((p for p in self.products if p.name == name), None)

    def validate(self) -> list[str]:
        """Return a list of referential-integrity problems (empty = clean)."""
        issues = []
        proxy_names = {p.name for p in self.proxies}
        product_names = {p.name for p in self.products}
        for product in self.products:
            for pr in product.proxies:
                if pr not in proxy_names:
                    issues.append(f"Product '{product.name}' references missing proxy '{pr}'")
        for app in self.applications:
            for prod in app.subscribed_products:
                if prod not in product_names:
                    issues.append(f"Application '{app.name}' subscribes to missing product '{prod}'")
        return issues
