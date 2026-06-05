"""
auth/authenticator.py
Handles authentication for both CPI environments:
  - Cloud Foundry (BTP): OAuth2 client credentials flow
  - Neo: HTTP Basic auth
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Optional

import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)


@dataclass
class TokenCache:
    access_token: str = ""
    expires_at: float = 0.0

    def is_valid(self) -> bool:
        return bool(self.access_token) and time.time() < self.expires_at - 30


class CFAuthenticator:
    """OAuth2 client credentials for Cloud Foundry / BTP tenants."""

    def __init__(self, token_url: str, client_id: str, client_secret: str):
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self._cache = TokenCache()

    def get_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({"Authorization": f"Bearer {self._get_token()}"})
        return session

    def _get_token(self) -> str:
        if self._cache.is_valid():
            return self._cache.access_token

        logger.info("Fetching OAuth2 token from %s", self.token_url)
        resp = requests.post(
            self.token_url,
            data={"grant_type": "client_credentials"},
            auth=HTTPBasicAuth(self.client_id, self.client_secret),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        self._cache.access_token = data["access_token"]
        self._cache.expires_at = time.time() + int(data.get("expires_in", 3600))
        logger.info("OAuth2 token obtained, expires in %ds", data.get("expires_in", 3600))
        return self._cache.access_token


class NeoAuthenticator:
    """Basic authentication for Neo (legacy) CPI tenants."""

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password

    def get_session(self) -> requests.Session:
        session = requests.Session()
        session.auth = HTTPBasicAuth(self.username, self.password)
        session.headers.update({"Accept": "application/json"})
        return session


class PIAuthenticator:
    """Basic authentication for SAP PI/PO Integration Directory REST API."""

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password

    def get_session(self) -> requests.Session:
        session = requests.Session()
        session.auth = HTTPBasicAuth(self.username, self.password)
        session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        return session


def build_cpi_authenticator(cfg: dict):
    """Factory — returns the right authenticator based on config environment."""
    env = cfg.get("environment", "cf").lower()
    if env == "cf":
        cf = cfg["cf"]
        return CFAuthenticator(cf["token_url"], cf["client_id"], cf["client_secret"])
    elif env == "neo":
        neo = cfg["neo"]
        return NeoAuthenticator(neo["username"], neo["password"])
    else:
        raise ValueError(f"Unknown environment: {env!r}. Use 'cf' or 'neo'.")


def build_pi_authenticator(cfg: dict) -> PIAuthenticator:
    pi = cfg["pi"]
    return PIAuthenticator(pi["username"], pi["password"])
