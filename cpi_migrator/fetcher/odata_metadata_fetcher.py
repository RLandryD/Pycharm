"""
fetcher/odata_metadata_fetcher.py

Standalone fetcher for CANONICAL OData metadata (EDMX) from SAP's reference
sandbox on the Business Accelerator Hub.

Why this works without a browser login
---------------------------------------
The Hub's spec-DOWNLOAD buttons (EDMX/WSDL/OpenAPI) require an interactive
login. But every OData service is *also* callable on the live sandbox with the
plain `apikey` header, and appending `$metadata` to a service URL returns its
full EDMX — served from SAP's reference host, so it carries ZERO tenant
personalization. That is exactly the canonical schema we want, and the API key
you already have is all the auth it needs. No cookies, no SSO, no scraping.

Two honest constraints:
  * OData only. SOAP/IDoc WSDL+XSD have no `$metadata` equivalent — those still
    come from the Hub (login) or get generated from the source system.
  * The sandbox is rate-limited (~10 req/sec). We throttle well under that.

Usage
-----
    # key from env (preferred) or --key / --key-file
    export SAP_HUB_APIKEY=...
    python3 -m fetcher.odata_metadata_fetcher \
        --targets fetcher/seed_core_services.csv \
        --out ./fetched_schemas --dedup

targets CSV columns (header row required; any subset of these):
    name,product,service,url
  - If `url` is given, it is used verbatim (we append /$metadata if missing).
  - Else `product`+`service` build the URL from a known sandbox template.
  - `name` is the output filename stem (defaults to service or last URL seg).

The product templates cover the common cases; for anything else paste the exact
sandbox URL from the Hub's "Try it out / code snippet" panel into the `url`
column — that is always authoritative and bypasses template guesswork.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

SANDBOX_ROOT = "https://sandbox.api.sap.com"

# product -> base path that a service name is appended to (before /$metadata).
# VERIFY against the Hub "Try it out" panel if a fetch 404s — SAP occasionally
# revises these prefixes, and OData V4 services use /odata4/ with a group path.
PRODUCT_TEMPLATES = {
    # S/4HANA Cloud Public, OData V2:  <root>/s4hanacloud/sap/opu/odata/sap/<SERVICE>
    "s4hanacloud":  SANDBOX_ROOT + "/s4hanacloud/sap/opu/odata/sap/{service}",
    # S/4HANA on-prem / private flavour seen in the Hub
    "s4hana":       SANDBOX_ROOT + "/s4hanacloud/sap/opu/odata/sap/{service}",
    # SuccessFactors EC: one big OData V2 service; service name usually blank
    "successfactors": SANDBOX_ROOT + "/successfactors/odata/v2/{service}",
    # SAP Marketing Cloud
    "marketing":    SANDBOX_ROOT + "/s4hanamarketingcloud/sap/opu/odata/sap/{service}",
}

_SANITIZE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize(name: str) -> str:
    return _SANITIZE.sub("_", name).strip("_") or "schema"


def metadata_url(service: str = "", product: str = "", url: str = "") -> str:
    """Build the $metadata URL from an explicit url OR product+service."""
    if url:
        base = url.strip().rstrip("/")
        # strip an existing $metadata (any case) then re-append canonically
        base = re.sub(r"/\$metadata/?$", "", base, flags=re.IGNORECASE)
        return base + "/$metadata"
    if not product:
        raise ValueError("need either url or product")
    tmpl = PRODUCT_TEMPLATES.get(product.lower())
    if not tmpl:
        raise ValueError(f"unknown product {product!r}; supply an explicit url "
                         f"or add a template. Known: {sorted(PRODUCT_TEMPLATES)}")
    base = tmpl.format(service=service).rstrip("/")
    return base + "/$metadata"


@dataclass
class FetchResult:
    name: str
    url: str
    ok: bool
    status: int = 0
    bytes: int = 0
    path: str = ""
    error: str = ""


def _http_get(session, url, headers, timeout, auth=None):
    """Isolated so tests can monkeypatch the network boundary."""
    return session.get(url, headers=headers, timeout=timeout, auth=auth)


def _http_post_token(session, token_url, client_id, client_secret, timeout):
    """Isolated token POST so tests can monkeypatch it."""
    return session.post(token_url, data={"grant_type": "client_credentials"},
                        auth=(client_id, client_secret),
                        headers={"Accept": "application/json"}, timeout=timeout)


def _find_oauth_block(obj):
    """Find the dict that holds BOTH clientid and clientsecret (e.g. the `uaa`
    block of a BTP service key). Critical: the OAuth *token* server lives in this
    block's url — NOT the top-level service url (which points at the app/system).
    """
    if isinstance(obj, dict):
        keys = {k.lower().replace("_", "") for k in obj}
        if "clientid" in keys and "clientsecret" in keys:
            return obj
        for v in obj.values():
            b = _find_oauth_block(v)
            if b:
                return b
    elif isinstance(obj, list):
        for v in obj:
            b = _find_oauth_block(v)
            if b:
                return b
    return None


def _field_ci(d: dict, name: str) -> str:
    for k, v in d.items():
        if isinstance(v, str) and k.lower().replace("_", "") == name:
            return v
    return ""


def bearer_from_service_key(text: str, session=None) -> tuple[str, str]:
    """OAuth client-credentials → bearer, from a BTP/API-Management service key.

    Returns (token, error). Locates the OAuth credentials block (e.g. `uaa`) and
    reads clientid/clientsecret/tokenurl from THAT SAME block, so the token URL
    is the auth server (uaa.url) and not the top-level service/system url.
    """
    try:
        data = json.loads(text)
    except Exception:
        return "", "service key is not valid JSON"
    block = _find_oauth_block(data)
    if not block:
        return "", "service key has no clientid/clientsecret (not an OAuth key)"
    cid = _field_ci(block, "clientid")
    csec = _field_ci(block, "clientsecret")
    turl = _field_ci(block, "tokenurl") or _field_ci(block, "url")
    if not (cid and csec and turl):
        return "", "OAuth block missing clientid/clientsecret/tokenurl"
    if "/oauth/token" not in turl:
        turl = turl.rstrip("/") + "/oauth/token"
    session = session or requests.Session()
    try:
        resp = _http_post_token(session, turl, cid, csec, 30)
    except Exception as exc:
        return "", f"token request failed: {exc}"
    if resp.status_code != 200:
        return "", f"token endpoint HTTP {resp.status_code}"
    try:
        return resp.json().get("access_token", ""), ""
    except Exception:
        return "", "token response not JSON"


def fetch_one(session, url: str, api_key: str, *, header_name: str = "apikey",
              timeout: int = 30, max_retries: int = 4) -> tuple[int, bytes, str]:
    """GET a $metadata URL. Returns (status, body, error). Retries on 429/5xx."""
    headers = {header_name: api_key, "Accept": "application/xml"}
    delay = 1.0
    for attempt in range(max_retries + 1):
        try:
            resp = _http_get(session, url, headers, timeout)
        except Exception as exc:  # network/DNS/TLS
            if attempt < max_retries:
                time.sleep(delay); delay *= 2; continue
            return 0, b"", f"request failed: {exc}"
        sc = resp.status_code
        if sc == 200:
            return 200, resp.content, ""
        if sc == 429 or 500 <= sc < 600:
            if attempt < max_retries:
                wait = float(resp.headers.get("Retry-After", delay))
                time.sleep(wait); delay *= 2; continue
            return sc, b"", f"giving up after {max_retries} retries (HTTP {sc})"
        if sc in (401, 403):
            return sc, b"", ("auth rejected (HTTP %d) — check the API key and "
                             "that header name %r is what this service expects"
                             % (sc, header_name))
        if sc == 404:
            return sc, b"", "not found (HTTP 404) — verify the sandbox URL/path"
        return sc, b"", f"HTTP {sc}"
    return 0, b"", "exhausted retries"


def fetch_one(session, url: str, api_key: str, *, header_name: str = "apikey",
              timeout: int = 30, max_retries: int = 4, auth=None,
              bearer: str = "") -> tuple[int, bytes, str]:
    """GET a $metadata/?wsdl URL. Returns (status, body, error). Retries on 429/5xx.

    Auth precedence: bearer (OAuth) → auth=(user,pass) Basic → api_key in header.
    """
    if bearer:
        headers = {"Authorization": f"Bearer {bearer}", "Accept": "application/xml"}
    elif auth:
        headers = {"Accept": "application/xml"}
    else:
        headers = {header_name: api_key, "Accept": "application/xml"}
    delay = 1.0
    for attempt in range(max_retries + 1):
        try:
            resp = _http_get(session, url, headers, timeout, auth)
        except Exception as exc:  # network/DNS/TLS
            if attempt < max_retries:
                time.sleep(delay); delay *= 2; continue
            return 0, b"", f"request failed: {exc}"
        sc = resp.status_code
        if sc == 200:
            return 200, resp.content, ""
        if sc == 429 or 500 <= sc < 600:
            if attempt < max_retries:
                wait = float(resp.headers.get("Retry-After", delay))
                time.sleep(wait); delay *= 2; continue
            return sc, b"", f"giving up after {max_retries} retries (HTTP {sc})"
        if sc in (401, 403):
            return sc, b"", ("auth rejected (HTTP %d) — check credentials; for "
                             "your own system use --auth basic, for the Hub "
                             "sandbox the apikey header" % sc)
        if sc == 404:
            return sc, b"", "not found (HTTP 404) — verify the URL/path"
        return sc, b"", f"HTTP {sc}"
    return 0, b"", "exhausted retries"


def resolve_url(t: dict, host: str = "") -> str:
    """Target URL from a row: explicit url (with {HOST} substitution) or product+service."""
    raw = (t.get("url") or "").strip()
    if raw:
        if "{HOST}" in raw:
            if not host:
                raise ValueError("url contains {HOST} but no --host given")
            raw = raw.replace("{HOST}", host.rstrip("/"))
        # already a metadata / wsdl / query URL → use verbatim
        if re.search(r"\$metadata/?$|\?wsdl|\?", raw, re.IGNORECASE):
            return raw
        return raw.rstrip("/") + "/$metadata"
    return metadata_url(t.get("service", ""), t.get("product", ""))


def _sniff_kind(body: bytes) -> str:
    head = body[:600].lstrip()
    if b":Edmx" in head or b"<Edmx" in head or b"DataServices" in head:
        return "edmx"
    if b":definitions" in head or b"<definitions" in head:
        return "wsdl"
    if b":schema" in head or b"<schema" in head:
        return "xsd"
    return "edmx"


def fetch_all(targets: list[dict], api_key: str, out_dir: str, *,
              rate_per_sec: float = 7.0, header_name: str = "apikey",
              host: str = "", auth=None, bearer: str = "", session=None) -> list[FetchResult]:
    """Fetch each target, throttled. Routes EDMX/WSDL/XSD into typed subfolders.

    Pass host=... to fill {HOST} in manifest templates, and auth=(user,pass) to
    discover against your own system instead of the Hub sandbox.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    session = session or requests.Session()
    min_interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
    results: list[FetchResult] = []
    last = 0.0
    for t in targets:
        try:
            url = resolve_url(t, host)
        except ValueError as exc:
            results.append(FetchResult(t.get("name") or "?", "", False, error=str(exc)))
            continue
        name = sanitize(t.get("name") or t.get("service") or url.split("/")[-2])
        gap = time.monotonic() - last
        if gap < min_interval:
            time.sleep(min_interval - gap)
        last = time.monotonic()
        sc, body, err = fetch_one(session, url, api_key, header_name=header_name,
                                  auth=auth, bearer=bearer)
        if sc == 200 and body:
            kind = _sniff_kind(body)
            ext = {"edmx": "edmx", "wsdl": "wsdl", "xsd": "xsd"}[kind]
            (out / kind).mkdir(exist_ok=True)
            dest = out / kind / f"{name}.{ext}"
            dest.write_bytes(body)
            results.append(FetchResult(name, url, True, sc, len(body), str(dest)))
            logger.info("fetched %s → %s/ (%d bytes)", name, kind, len(body))
        else:
            results.append(FetchResult(name, url, False, sc, 0, "", err))
            logger.warning("failed %s: %s", name, err)
    (out / "_fetch_manifest.json").write_text(
        json.dumps([asdict(r) for r in results], indent=2))
    return results


def _find_field(obj, wanted: set) -> str:
    """Recursively find the first string value under a key in `wanted` (ci)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and k.lower().replace("_", "") in wanted:
                return v
        for v in obj.values():
            hit = _find_field(v, wanted)
            if hit:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _find_field(v, wanted)
            if hit:
                return hit
    return ""


def _looks_like_oauth_servicekey(data) -> bool:
    return bool(_find_field(data, {"clientid"}) and _find_field(data, {"clientsecret"}))


def key_from_text(text: str) -> tuple[str, str]:
    """Resolve an API key from file text. Returns (key, error_message).

    Accepts a plain-text key OR a JSON file containing an `apikey`/`key` field.
    Diagnoses a BTP OAuth service key (wrong credential type for this method).
    """
    s = text.strip()
    if not s:
        return "", "key file is empty"
    try:
        data = json.loads(s)
    except Exception:
        return s, ""  # plain-text key, use verbatim
    hit = _find_field(data, {"apikey", "key", "hubapikey"})
    if hit:
        return hit, ""
    if _looks_like_oauth_servicekey(data):
        return "", ("this JSON is a BTP OAuth service key (clientid/clientsecret) "
                    "for your OWN tenant — not the Hub sandbox APIKey. The "
                    "$metadata sandbox method needs the Hub sandbox APIKey "
                    "string (the one you read under 'Show API Key' on the Hub). "
                    "Pass that with --key or env SAP_HUB_APIKEY. (A tenant key "
                    "would return personalized schemas, defeating the purpose.)")
    return "", ("no 'apikey'/'key' field found in the JSON; pass the Hub sandbox "
                "APIKey via --key or env SAP_HUB_APIKEY instead")


def load_targets(csv_path: str) -> list[dict]:
    rows: list[dict] = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
            name = (row.get("name") or "")
            if name.lstrip().startswith("#"):
                continue                      # comment line in the seed file
            if not any(row.values()):
                continue                      # blank row
            # need at least something to build a URL from
            if not (row.get("url") or row.get("product") or row.get("service")):
                continue
            rows.append(row)
    return rows


def _main(argv=None):
    ap = argparse.ArgumentParser(description="Fetch canonical OData EDMX ($metadata) from the SAP sandbox.")
    ap.add_argument("--targets", required=True, help="CSV with name/product/service/url columns")
    ap.add_argument("--out", default="./fetched_schemas")
    ap.add_argument("--key", default="", help="API key (else env SAP_HUB_APIKEY or --key-file)")
    ap.add_argument("--key-file", default="")
    ap.add_argument("--header-name", default="apikey", help="auth header name (default: apikey)")
    ap.add_argument("--host", default="", help="fill {HOST} in manifest URL templates, e.g. https://mysys:443")
    ap.add_argument("--auth", choices=["apikey", "basic", "oauth"], default="apikey",
                    help="apikey (Hub sandbox), basic (your S/4 system), or oauth (BTP service key)")
    ap.add_argument("--user", default="", help="username for --auth basic (password via env SAP_PWD)")
    ap.add_argument("--service-key", default="", help="BTP/API-Mgmt service key JSON for --auth oauth")
    ap.add_argument("--rate", type=float, default=7.0, help="max requests/sec (<10)")
    ap.add_argument("--dedup", action="store_true", help="run the schema deduper over --out afterwards")
    args = ap.parse_args(argv)

    auth = None
    bearer = ""
    key = "n/a"
    if args.auth == "basic":
        pwd = os.environ.get("SAP_PWD", "")
        if not args.user or not pwd:
            ap.error("--auth basic needs --user and env SAP_PWD")
        auth = (args.user, pwd)
    elif args.auth == "oauth":
        if not args.service_key:
            ap.error("--auth oauth needs --service-key <service-key.json>")
        bearer, err = bearer_from_service_key(Path(args.service_key).read_text())
        if err:
            ap.error(f"oauth: {err}")
    else:
        key = args.key or os.environ.get("SAP_HUB_APIKEY", "")
        if not key and args.key_file:
            key, err = key_from_text(Path(args.key_file).read_text())
            if err:
                ap.error(err)
        if not key:
            ap.error("no API key (use --key, --key-file, or env SAP_HUB_APIKEY)")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    targets = load_targets(args.targets)
    results = fetch_all(targets, key, args.out, rate_per_sec=args.rate,
                        header_name=args.header_name, host=args.host,
                        auth=auth, bearer=bearer)
    ok = sum(1 for r in results if r.ok)
    print(f"\n{ok}/{len(results)} fetched → {args.out}")
    for r in results:
        if not r.ok:
            print(f"  FAIL  {r.name}: {r.error}")

    if args.dedup and ok:
        try:
            from extractor.schema_deduper import dedup, build_report
            paths = [r.path for r in results if r.ok]
            res = dedup(paths)
            rep = Path(args.out) / "_dedup_report.md"
            rep.write_text(build_report(res))
            print(f"dedup report → {rep}")
        except Exception as exc:
            print(f"(dedup skipped: {exc})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_main())
