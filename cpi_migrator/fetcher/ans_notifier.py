"""fetcher/ans_notifier.py — SAP Alert Notification Service wiring (#3).

The seed of the operations-retainer offering: poller detects failed CPI
message runs → events post to ANS → ANS routes to email/webhook. The same
code provisions a CLIENT's ANS instance identically, turning alerting
setup into a one-button deliverable.

Key handling: reads the service key from the per-client wallet
(profile.get_service_key("ans")). Key shapes vary by plan — flat
(client_id/client_secret/oauth_url/url) or nested under "uaa" — both
supported. Secrets are NEVER logged; masked tails only.

ANS surfaces used:
  producer API       POST {url}/cf/producer/v1/resource-events
  configuration API  GET/POST {url}/cf/configuration/v1/{conditions,
                     actions,subscriptions}
Provisioning is idempotent: GET-by-name first, POST only when absent,
409 tolerated. Email actions require a one-time confirmation click by the
recipient (ANS sends it on creation) — the only non-automatable step.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass

import requests

logger = logging.getLogger("fetcher.ans_notifier")

EVENT_TYPE = "CPI_MESSAGE_FAILED"
_PREFIX = "cpi_migrator"


def _mask(s: str) -> str:
    s = str(s or "")
    return f"…{s[-4:]}" if len(s) >= 4 else "…"


@dataclass
class ANSKey:
    url: str
    client_id: str
    client_secret: str
    oauth_url: str

    @classmethod
    def from_dict(cls, key: dict) -> "ANSKey":
        """Accept flat keys and uaa-nested keys."""
        uaa = key.get("uaa") if isinstance(key.get("uaa"), dict) else {}
        cid = key.get("client_id") or uaa.get("clientid") or \
            key.get("clientid") or ""
        sec = key.get("client_secret") or uaa.get("clientsecret") or \
            key.get("clientsecret") or ""
        oauth = key.get("oauth_url") or uaa.get("url") or ""
        url = key.get("url") or key.get("sb_url") or ""
        if oauth and "/oauth/token" not in oauth:
            oauth = oauth.rstrip("/") + "/oauth/token"
        return cls(url=url.rstrip("/"), client_id=cid, client_secret=sec,
                   oauth_url=oauth)

    def valid(self) -> bool:
        return bool(self.url and self.client_id and self.client_secret
                    and self.oauth_url)


class ANSClient:
    def __init__(self, key: dict, session: "requests.Session | None" = None):
        self.key = ANSKey.from_dict(key)
        self.session = session or requests.Session()
        self._token = ""
        self._token_exp = 0.0

    # ── auth ─────────────────────────────────────────────────────────────
    def _ensure_token(self):
        if self._token and time.time() < self._token_exp - 60:
            return
        r = self.session.post(
            self.key.oauth_url,
            data={"grant_type": "client_credentials"},
            auth=(self.key.client_id, self.key.client_secret), timeout=30)
        r.raise_for_status()
        d = r.json()
        self._token = d["access_token"]
        self._token_exp = time.time() + int(d.get("expires_in", 3600))
        logger.info("ANS token obtained (client %s), expires in %ss",
                    _mask(self.key.client_id), d.get("expires_in"))

    def _headers(self) -> dict:
        self._ensure_token()
        return {"Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json"}

    # ── producer ─────────────────────────────────────────────────────────
    def produce_event(self, event: dict) -> bool:
        url = f"{self.key.url}/cf/producer/v1/resource-events"
        r = self.session.post(url, headers=self._headers(),
                              data=json.dumps(event), timeout=30)
        ok = r.status_code in (200, 201, 202)
        (logger.info if ok else logger.warning)(
            "ANS event %s → HTTP %s", event.get("eventType"), r.status_code)
        return ok

    # ── configuration (idempotent provisioning) ─────────────────────────
    def _cfg(self, entity: str) -> str:
        return f"{self.key.url}/cf/configuration/v1/{entity}"

    def _exists(self, entity: str, name: str) -> bool:
        try:
            r = self.session.get(f"{self._cfg(entity)}/{name}",
                                 headers=self._headers(), timeout=30)
            return r.status_code == 200
        except Exception:
            return False

    def _create(self, entity: str, payload: dict) -> bool:
        r = self.session.post(self._cfg(entity), headers=self._headers(),
                              data=json.dumps(payload), timeout=30)
        if r.status_code in (200, 201):
            logger.info("ANS %s '%s' created", entity, payload.get("name"))
            return True
        if r.status_code == 409:
            logger.info("ANS %s '%s' already exists", entity,
                        payload.get("name"))
            return True
        logger.warning("ANS %s create failed: HTTP %s %s", entity,
                       r.status_code, r.text[:200])
        return False

    def ensure_provisioning(self, target: str,
                            target_type: str = "email") -> dict:
        """Idempotently create condition + action + subscription routing
        CPI_MESSAGE_FAILED events to `target` (email address or webhook
        URL). Returns {entity: ok} for the UI."""
        cond_name = f"{_PREFIX}_failed_cond"
        act_name = f"{_PREFIX}_notify_act"
        sub_name = f"{_PREFIX}_failed_sub"
        out = {}
        if not self._exists("conditions", cond_name):
            out["condition"] = self._create("conditions", {
                "name": cond_name,
                "description": "cpi_migrator: failed CPI message run",
                "propertyKey": "eventType",
                "predicate": "EQUALS",
                "propertyValue": EVENT_TYPE})
        else:
            out["condition"] = True
        if not self._exists("actions", act_name):
            if target_type == "webhook":
                payload = {"name": act_name, "type": "WEBHOOK",
                           "description": "cpi_migrator notify",
                           "properties": {"url": target}}
            else:
                payload = {"name": act_name, "type": "EMAIL",
                           "description": "cpi_migrator notify",
                           "properties": {"destination": target,
                                          "useHtml": "false"}}
            out["action"] = self._create("actions", payload)
        else:
            out["action"] = True
        if not self._exists("subscriptions", sub_name):
            out["subscription"] = self._create("subscriptions", {
                "name": sub_name,
                "description": "cpi_migrator: route failed-run events",
                "conditions": [cond_name],
                "actions": [act_name]})
        else:
            out["subscription"] = True
        return out


def build_failure_event(run: dict, tenant: str = "") -> dict:
    """ANS resource-event from a CPI message-processing-log run dict."""
    iflow = run.get("IntegrationFlowName") or run.get("iflow") or "unknown"
    guid = run.get("MessageGuid") or run.get("guid") or ""
    status = run.get("Status") or "FAILED"
    when = run.get("LogEnd") or run.get("LogStart") or ""
    return {
        "eventType": EVENT_TYPE,
        "severity": "ERROR",
        "category": "ALERT",
        "subject": f"CPI message FAILED: {iflow}",
        "body": (f"Integration flow '{iflow}' reported status {status}.\n"
                 f"MessageGuid: {guid}\nLogEnd: {when}\nTenant: {tenant}\n"
                 f"Source: cpi_migrator background poller"),
        "resource": {"resourceName": iflow, "resourceType": "cpi.iflow",
                     "tags": {"messageGuid": guid}},
        "tags": {"ans:correlationId": guid or None,
                 "cpi:status": status},
    }


_FAILED_STATUSES = {"FAILED", "ESCALATED", "ABANDONED"}


def notify_failures(runs: list, state_path: str, client: ANSClient,
                    tenant: str = "") -> int:
    """Send one event per newly-seen failed run. Dedupe persisted at
    state_path (JSON list of MessageGuids). Returns events sent."""
    seen: set = set()
    try:
        if os.path.exists(state_path):
            with open(state_path) as fh:
                seen = set(json.load(fh))
    except Exception:
        seen = set()
    sent = 0
    for run in runs or []:
        if str(run.get("Status", "")).upper() not in _FAILED_STATUSES:
            continue
        guid = run.get("MessageGuid") or ""
        if not guid or guid in seen:
            continue
        if client.produce_event(build_failure_event(run, tenant)):
            seen.add(guid)
            sent += 1
    if sent:
        try:
            os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
            with open(state_path, "w") as fh:
                json.dump(sorted(seen)[-5000:], fh)
        except Exception as exc:
            logger.warning("ANS dedupe state save failed: %s", exc)
    return sent
