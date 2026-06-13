"""fetcher/pipo_directory.py — download PI/PO communication channels and
credential references via the Integration Directory REST API, and replicate
the missing pieces into CPI.

What PI/PO exposes (Directory REST API, PO 7.5 / 7.31 SP14+):
  GET /CommunicationChannel/CommunicationChannel        — all channels with
      AdapterType, Direction, and AdapterSpecificAttribute name/value pairs
      (URLs, hosts, users, auth modes, keystore VIEWS/aliases…)
What it does NOT expose: passwords and private keys — they are never
returned by any PI API. Replication therefore creates CPI **UserCredentials
with a placeholder password** the client re-keys once, and reports
certificate/keystore references for manual transport. Secrets are never
logged (project rule).

Channel attributes also tell us the real endpoint config (hosts, paths,
queues) — they're kept on the record for the scaffolder to consume.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)


# ── records ──────────────────────────────────────────────────────────────────
@dataclass
class ChannelRecord:
    party: str = ""
    component: str = ""            # business system / component
    name: str = ""
    adapter: str = ""              # SOAP, REST, SFTP, IDoc_AAE, JDBC, MAIL…
    direction: str = ""            # Sender | Receiver
    description: str = ""
    attributes: dict = field(default_factory=dict)   # name → value
    modules: list = field(default_factory=list)      # [(module, {param: v})]
    raw: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.party}|{self.component}|{self.name}"


# attribute names that reference a user (across adapter types)
_USER_ATTRS = ("user", "username", "userName", "authenticationUser",
               "proxyUser", "ftpUser", "sftpUser", "jdbcUser", "mailUser",
               "principal", "clientId", "client_id")
# attribute names whose value is an auth-mode selector
_AUTH_ATTRS = ("authenticationMode", "authenticationType", "authMethod",
               "authentication", "authType", "clientAuthentication")
# certificate / keystore references (report-only; keys can't be exported)
_CERT_ATTRS = ("keystoreView", "keystoreEntry", "privateKeyView",
               "privateKeyEntry", "certificateView", "certificateEntry",
               "trustedCertificate", "tlsKeystore", "assertionSigningKey")


@dataclass
class CredentialRecord:
    alias: str                     # suggested CPI Security Material name
    user: str
    auth: str = "Basic"
    adapter: str = ""
    channels: list = field(default_factory=list)     # ChannelRecord.key list
    exists_in_cpi: bool | None = None                # filled by replicate
    note: str = ""


@dataclass
class CertReference:
    channel: str
    adapter: str
    attribute: str
    value: str


# ── extractor ────────────────────────────────────────────────────────────────
class PIChannelExtractor:
    """Pulls every communication channel (with adapter-specific attributes)
    from the PI/PO Integration Directory REST API. Same endpoint family the
    interface extractor already uses, so the same host/session works."""

    def __init__(self, base_url: str, session: requests.Session):
        self.base_url = base_url.rstrip("/")
        self.session = session

    @staticmethod
    def looks_like_cpi(url: str) -> bool:
        """CPI tenant hosts are not PI/PO systems — the Directory API only
        exists on PI/PO. Catch the mix-up before the confusing 404."""
        u = (url or "").lower()
        return any(t in u for t in ("hana.ondemand.com", "cfapps.",
                                    "it-cpi", "integrationsuite"))

    def extract_all(self) -> list[ChannelRecord]:
        if not (self.base_url or "").strip():
            logger.warning("channel download refused: empty PI/PO host URL")
            raise RuntimeError(
                "PI/PO Host URL is empty — fill it in above (typically "
                "http://<pihost>:50000), or use 'Load sample channels "
                "(demo)' to test without a PI system.")
        url = f"{self.base_url}/CommunicationChannel/CommunicationChannel"
        host = self.base_url.split("://")[-1].split("/")[0]
        if self.looks_like_cpi(self.base_url):
            logger.warning("channel download refused: %s looks like a CPI "
                           "tenant, not a PI/PO system", host)
            raise RuntimeError(
                f"'{host}' looks like a CPI tenant. The channel download "
                "speaks the PI/PO Integration Directory API, which only "
                "exists on PI/PO systems (typically http://<pihost>:50000). "
                "To exercise the pipeline without a PI system, use 'Load "
                "sample channels (demo)' — replication then runs against "
                "your real CPI tenant.")
        logger.info("Fetching communication channels from %s …", host)
        out, skip, page = [], 0, 100
        while True:
            resp = self.session.get(
                url, params={"$format": "json", "$top": page, "$skip": skip},
                timeout=60)
            if resp.status_code == 404:
                logger.warning("Directory API not found at %s (HTTP 404)",
                               host)
                raise RuntimeError(
                    f"The PI/PO Directory API was not found at '{host}' "
                    "(HTTP 404). It exists on PI/PO 7.31 SP14+ / 7.5 "
                    "systems and must be active (service path "
                    "/CommunicationChannel). Check host/port — usually "
                    "http://<pihost>:50000.")
            if resp.status_code in (401, 403):
                logger.warning("Directory API auth failure at %s (HTTP %s)",
                               host, resp.status_code)
                raise RuntimeError(
                    f"HTTP {resp.status_code} from the Directory API — the "
                    "user needs a Directory role on the PI system (e.g. "
                    "SAP_XI_API_DISPLAY_J2EE) even when interface reads "
                    "work.")
            resp.raise_for_status()
            entries = resp.json().get("d", {}).get("results", [])
            if not entries:
                break
            out.extend(self._parse(e) for e in entries)
            skip += page
            if len(entries) < page:
                break
        logger.info("Extracted %d communication channels.", len(out))
        return out

    @staticmethod
    def _attr_pairs(raw: dict) -> dict:
        """AdapterSpecificAttribute arrives in a few shapes across PO
        patch levels — handle all defensively, values redactable later."""
        pairs = {}
        for key in ("AdapterSpecificAttribute",
                    "AdapterSpecificTableAttribute"):
            blob = raw.get(key)
            if isinstance(blob, dict):
                blob = blob.get("results", [])
            for item in blob or []:
                if not isinstance(item, dict):
                    continue
                n = item.get("Name") or item.get("name")
                v = item.get("Value")
                if v is None:
                    v = item.get("value", "")
                if n and "passw" not in n.lower():    # never even hold them
                    pairs[str(n)] = "" if v is None else str(v)
        return pairs

    @staticmethod
    def _module_chain(raw: dict) -> list:
        """PI module processor chain (PGP, custom EJBs…) — module name +
        its parameters. Shapes vary across PO levels; parsed defensively,
        password-named params never held."""
        out = []
        blob = raw.get("ModuleConfiguration") or raw.get("Modules")
        if isinstance(blob, dict):
            blob = blob.get("results", [])
        for item in blob or []:
            if not isinstance(item, dict):
                continue
            name = (item.get("ModuleName") or item.get("Name") or "")
            params = {}
            pb = item.get("ModuleParameters") or item.get("Parameters")
            if isinstance(pb, dict):
                pb = pb.get("results", [])
            for p in pb or []:
                if isinstance(p, dict):
                    k = p.get("Name") or p.get("name")
                    v = p.get("Value")
                    if v is None:
                        v = p.get("value", "")
                    if k and "passw" not in str(k).lower():
                        params[str(k)] = "" if v is None else str(v)
            if name:
                out.append((name, params))
        return out

    def _parse(self, raw: dict) -> ChannelRecord:
        return ChannelRecord(
            party=raw.get("PartyID", "") or raw.get("Party", "") or "",
            component=raw.get("ComponentID", "")
            or raw.get("Component", "") or "",
            name=raw.get("ChannelID", "") or raw.get("ChannelName", "")
            or raw.get("Name", ""),
            adapter=raw.get("AdapterType", "") or raw.get("adapterType", ""),
            direction=raw.get("Direction", "")
            or raw.get("direction", ""),
            description=raw.get("Description", "") or "",
            attributes=self._attr_pairs(raw),
            modules=self._module_chain(raw),
            raw=raw,
        )


# ── credential harvesting ────────────────────────────────────────────────────
def _alias_for(user: str, component: str, sep: str = "_") -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{component or 'PI'}{sep}{user}")
    return base[:60]


def harvest_credentials(channels: list) -> tuple:
    """Returns (CredentialRecord list deduped by user+component,
    CertReference list). PI has no named credential store — auth is per
    channel — so channels sharing a user under the same component collapse
    into ONE suggested CPI credential, with the channel list kept for the
    re-key worksheet."""
    creds: dict = {}
    certs: list = []
    for ch in channels:
        a = ch.attributes or {}
        user = next((a[k] for k in _USER_ATTRS if a.get(k)), "")
        auth = next((a[k] for k in _AUTH_ATTRS if a.get(k)), "")
        if user:
            key = (user, ch.component)
            rec = creds.get(key)
            if rec is None:
                rec = CredentialRecord(
                    alias=_alias_for(user, ch.component), user=user,
                    auth=auth or "Basic", adapter=ch.adapter)
                creds[key] = rec
            rec.channels.append(ch.key)
            if auth and rec.auth in ("", "Basic"):
                rec.auth = auth
        for k in _CERT_ATTRS:
            if a.get(k):
                certs.append(CertReference(channel=ch.key,
                                           adapter=ch.adapter,
                                           attribute=k, value=a[k]))
    return list(creds.values()), certs


@dataclass
class SecurityNeed:
    """A CPI Security Material item a channel requires beyond plain user
    credentials. `automated` says whether the workbench can create it via
    API — PGP keyrings can NOT be pushed (no public CPI API; UI upload
    only) and the secret keys never leave PI in the first place, so those
    become runbook items with the exact manual steps."""
    kind: str                  # PGP_SECRET_KEYRING | PGP_PUBLIC_KEYRING |
    #                            OAUTH2_CLIENT | SECURITY_MODULE
    channel: str
    adapter: str
    detail: str
    automated: bool
    cpi_action: str


_PGP_MODULES = ("PGPEncryption", "PGPDecryption")


def harvest_security_needs(channels: list) -> list:
    """Scan module chains + attributes for security material beyond user
    credentials: PGP keyrings (module params name the key files), OAuth2
    clients, and unknown security-looking modules worth a human glance."""
    needs = []
    for ch in channels:
        for mod, params in (ch.modules or []):
            short = mod.rsplit("/", 1)[-1]
            if short in _PGP_MODULES:
                if short == "PGPEncryption":
                    pub = params.get("publicKeyFileName") \
                        or params.get("encryptionKeyFileName") or "?"
                    needs.append(SecurityNeed(
                        kind="PGP_PUBLIC_KEYRING", channel=ch.key,
                        adapter=ch.adapter,
                        detail=f"encrypts with public key '{pub}'"
                               + (" + signs" if params.get(
                                   "applySignature", "").lower()
                                  in ("true", "1") else ""),
                        automated=False,
                        cpi_action="Upload PGP Public Keyring in Monitor → "
                                   "Security Material (UI only — no CPI "
                                   "API). Include the partner key "
                                   f"'{pub}'."))
                    if params.get("applySignature", "").lower() in \
                            ("true", "1"):
                        own = params.get("ownPrivateKeyFileName") \
                            or params.get("signKeyFileName") or "?"
                        needs.append(SecurityNeed(
                            kind="PGP_SECRET_KEYRING", channel=ch.key,
                            adapter=ch.adapter,
                            detail=f"signs with private key '{own}'",
                            automated=False,
                            cpi_action="Upload PGP Secret Keyring (UI "
                                       "only). The private key can NOT be "
                                       "exported from PI via API — obtain "
                                       "it from the key owner/basis team."))
                else:
                    own = params.get("ownPrivateKeyFileName") \
                        or params.get("decryptionKeyFileName") or "?"
                    needs.append(SecurityNeed(
                        kind="PGP_SECRET_KEYRING", channel=ch.key,
                        adapter=ch.adapter,
                        detail=f"decrypts with private key '{own}'",
                        automated=False,
                        cpi_action="Upload PGP Secret Keyring (UI only). "
                                   "Private keys never leave PI via API — "
                                   "obtain from the key owner/basis team."))
            elif any(t in short.lower()
                     for t in ("security", "encrypt", "sign", "wss")):
                needs.append(SecurityNeed(
                    kind="SECURITY_MODULE", channel=ch.key,
                    adapter=ch.adapter,
                    detail=f"module '{short}' "
                           f"({len(params)} parameter(s))",
                    automated=False,
                    cpi_action="Review manually — custom security module "
                               "has no 1:1 CPI equivalent."))
        a = ch.attributes or {}
        auth = next((a[k] for k in _AUTH_ATTRS if a.get(k)), "")
        if "oauth" in auth.lower() or a.get("clientId") or \
                a.get("client_id"):
            cid = a.get("clientId") or a.get("client_id") or ""
            tok = a.get("tokenUrl") or a.get("tokenServiceUrl") \
                or a.get("oauthTokenEndpoint") or ""
            needs.append(SecurityNeed(
                kind="OAUTH2_CLIENT", channel=ch.key, adapter=ch.adapter,
                detail=f"clientId '{cid}'"
                       + (f", token URL {tok}" if tok else ""),
                automated=True,
                cpi_action="OAuth2 Client Credentials created via API "
                           "(placeholder secret — re-key)."))
    return needs


def replicate_oauth2(needs: list, sm_client, placeholder_secret: str,
                     dry_run: bool = False, sep: str = "_") -> dict:
    """Create OAuth2 Client Credentials for harvested OAuth channels —
    fully API-supported on the CPI side; the client SECRET can't leave PI,
    so a placeholder ships and the item is flagged for re-keying."""
    report = sm_client.list_credentials()
    created, existing, failed = [], [], []
    errors: dict = {}
    if getattr(report, "error", ""):
        logger.warning("security material list problem: %s", report.error)
    for n in needs:
        if n.kind != "OAUTH2_CLIENT":
            continue
        import re as _re
        cid = (_re.search(r"clientId '([^']*)'", n.detail) or [None, ""])[1]
        comp = n.channel.split("|")[1] if n.channel.count("|") >= 2 else ""
        alias = _alias_for(cid or "oauth", comp, sep)
        tok_m = _re.search(r"token URL (\S+)", n.detail)
        tok = tok_m.group(1) if tok_m else "https://CHANGE-ME/oauth/token"
        if report.has_credential(alias):
            existing.append(alias)
            continue
        if dry_run:
            continue
        try:
            ok, msg = sm_client.create_oauth2_client_credentials(
                alias, tok, cid, placeholder_secret,
                description="Replicated from PI/PO — PLACEHOLDER secret, "
                            "re-key before go-live")
            (created if ok else failed).append(alias)
            if not ok:
                errors[alias] = msg
                logger.warning("create oauth2 %s FAILED: %s", alias, msg)
        except Exception as exc:
            logger.warning("create oauth2 %s failed: %s", alias, exc)
            failed.append(alias)
            errors[alias] = str(exc)
    return {"created": created, "existing": existing, "failed": failed,
            "errors": errors}


# ── replication into CPI ─────────────────────────────────────────────────────
def replicate_credentials(records: list, sm_client,
                          placeholder_password: str,
                          dry_run: bool = False) -> dict:
    """Create the credentials that don't exist on the tenant yet (compare by
    alias). Passwords can't leave PI, so every created credential carries
    the placeholder for the client to re-key — flagged in the summary.
    Secrets are never logged."""
    report = sm_client.list_credentials()
    created, existing, failed = [], [], []
    errors: dict = {}
    if getattr(report, "error", ""):
        logger.warning("security material list problem: %s", report.error)
    for rec in records:
        if report.has_credential(rec.alias):
            rec.exists_in_cpi = True
            existing.append(rec.alias)
            continue
        rec.exists_in_cpi = False
        if dry_run:
            continue
        try:
            ok, msg = sm_client.create_user_credential(
                rec.alias, rec.user, placeholder_password,
                description=f"Replicated from PI/PO ({rec.adapter}; "
                            f"{len(rec.channels)} channel(s)) — "
                            f"PLACEHOLDER password, re-key before go-live")
            (created if ok else failed).append(rec.alias)
            if ok:
                rec.exists_in_cpi = True
                rec.note = "created with placeholder — re-key"
            else:
                rec.note = msg[:160]
                errors[rec.alias] = msg
                logger.warning("create credential %s FAILED: %s",
                               rec.alias, msg)        # never log secrets
        except Exception as exc:                      # never log secrets
            logger.warning("create credential %s failed: %s",
                           rec.alias, exc)
            failed.append(rec.alias)
            errors[rec.alias] = str(exc)
    return {"created": created, "existing": existing, "failed": failed,
            "errors": errors,
            "missing": [r.alias for r in records
                        if r.exists_in_cpi is False]}


# ── worksheets ───────────────────────────────────────────────────────────────
def credentials_to_csv(records: list, certs: list | None = None,
                       needs: list | None = None) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["CPI alias", "User", "Auth", "Adapter",
                "Status on tenant", "Used by channels", "Note"])
    for r in records:
        status = {True: "exists", False: "MISSING"}.get(
            r.exists_in_cpi, "not checked")
        w.writerow([r.alias, r.user, r.auth, r.adapter, status,
                    "; ".join(r.channels), r.note])
    for c in certs or []:
        w.writerow([f"(certificate) {c.value}", "", c.attribute, c.adapter,
                    "manual transport", c.channel,
                    "keys/certs cannot be exported from PI — re-import "
                    "into CPI keystore"])
    for n in needs or []:
        w.writerow([f"({n.kind}) {n.detail}", "", n.kind, n.adapter,
                    "automated" if n.automated else "MANUAL", n.channel,
                    n.cpi_action])
    return out.getvalue()


def channels_to_csv(channels: list) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["Component", "Channel", "Adapter", "Direction",
                "Description", "Attributes"])
    for ch in channels:
        attrs = "; ".join(f"{k}={v}" for k, v in
                          sorted((ch.attributes or {}).items()))
        w.writerow([ch.component, ch.name, ch.adapter, ch.direction,
                    ch.description, attrs])
    return out.getvalue()


# ── demo mode ────────────────────────────────────────────────────────────────
def sample_channels() -> list:
    """Realistic demo channels so the WHOLE pipeline (harvest → tenant check
    → replicate → worksheets) is testable without a live PI/PO system —
    replication still runs against the real CPI tenant, safely (named
    placeholder credentials, easy to delete)."""
    raws = [
        {"ChannelID": "CC_SF_OData_Recv", "ComponentID": "BC_SUCCESSFACTORS",
         "AdapterType": "REST", "Direction": "Receiver",
         "Description": "SF OData upsert",
         "AdapterSpecificAttribute": {"results": [
             {"Name": "user", "Value": "SF_API_USER"},
             {"Name": "authenticationMode", "Value": "Basic"},
             {"Name": "url",
              "Value": "https://api12.successfactors.eu/odata/v2"}]}},
        {"ChannelID": "CC_SF_Query_Recv", "ComponentID": "BC_SUCCESSFACTORS",
         "AdapterType": "SOAP", "Direction": "Receiver",
         "AdapterSpecificAttribute": {"results": [
             {"Name": "user", "Value": "SF_API_USER"},
             {"Name": "authenticationMode", "Value": "Basic"}]}},
        {"ChannelID": "CC_Bank_SFTP_Out", "ComponentID": "BC_BANK",
         "AdapterType": "SFTP", "Direction": "Receiver",
         "Description": "payment files",
         "AdapterSpecificAttribute": {"results": [
             {"Name": "sftpUser", "Value": "bankops"},
             {"Name": "host", "Value": "sftp.bank.example:22"},
             {"Name": "privateKeyView", "Value": "PI_KEYSTORE"},
             {"Name": "privateKeyEntry", "Value": "bank_sftp_key"}]}},
        {"ChannelID": "CC_ERP_IDoc_Sender", "ComponentID": "BC_ECC",
         "AdapterType": "IDoc_AAE", "Direction": "Sender",
         "AdapterSpecificAttribute": {"results": [
             {"Name": "rfcDestination", "Value": "ECCCLNT100"}]}},
        {"ChannelID": "CC_CRM_JDBC_Recv", "ComponentID": "BC_CRM",
         "AdapterType": "JDBC", "Direction": "Receiver",
         "AdapterSpecificAttribute": {"results": [
             {"Name": "jdbcUser", "Value": "crm_integration"},
             {"Name": "url", "Value": "jdbc:sqlserver://crmdb:1433"}]}},
        {"ChannelID": "CC_Vendor_API_Recv", "ComponentID": "BC_VENDORPORTAL",
         "AdapterType": "REST", "Direction": "Receiver",
         "AdapterSpecificAttribute": {"results": [
             {"Name": "clientId", "Value": "vendor-portal-client"},
             {"Name": "authenticationType", "Value": "OAuth2"},
             {"Name": "url", "Value": "https://api.vendor.example/v1"},
             {"Name": "certificateView", "Value": "TrustedCAs"}]}},
        {"ChannelID": "CC_Bank_PGP_Out", "ComponentID": "BC_BANK",
         "AdapterType": "SFTP", "Direction": "Receiver",
         "Description": "PGP-encrypted payment files",
         "AdapterSpecificAttribute": {"results": [
             {"Name": "sftpUser", "Value": "bankops"}]},
         "ModuleConfiguration": {"results": [
             {"ModuleName": "localejbs/PGPEncryption",
              "ModuleParameters": {"results": [
                  {"Name": "publicKeyFileName",
                   "Value": "bank_partner_pub.asc"},
                  {"Name": "applySignature", "Value": "true"},
                  {"Name": "ownPrivateKeyFileName",
                   "Value": "company_sign.key"}]}}]}},
        {"ChannelID": "CC_Alert_Mail", "ComponentID": "BC_BASIS",
         "AdapterType": "Mail", "Direction": "Receiver",
         "AdapterSpecificAttribute": {"results": [
             {"Name": "mailUser", "Value": "donotreply"},
             {"Name": "authenticationMode", "Value": "Plain"}]}},
    ]
    ex = PIChannelExtractor.__new__(PIChannelExtractor)
    return [ex._parse(r) for r in raws]
