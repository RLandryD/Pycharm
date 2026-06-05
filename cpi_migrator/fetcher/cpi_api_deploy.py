#!/usr/bin/env python3
"""
cpi_api_deploy.py  --  Deploy a CPI package + its artifacts via the OData API,
bypassing the UI-export envelope and its SHA-256 hash entirely.

WHY THIS EXISTS
    The UI import format (resources.cnt + contentmetadata + hash + <id>_content)
    is gated by a server-side hash we cannot reproduce offline. The programmatic
    path does NOT use that envelope at all: you create an empty package, then push
    each artifact's INNER bundle (the META-INF/.project/src zip) to its OData
    entity set. No resources.cnt, no hash, ever. The tenant builds those itself.

WHAT IT DOES (in order)
    1. Reads OAuth2 client credentials from your CF service key (NEVER pasted to
       anyone -- it stays on your machine).
    2. Gets a bearer token from XSUAA (client_credentials grant).
    3. Fetches a CSRF token (CPI requires X-CSRF-Token on every POST).
    4. Reads a package export .zip you already have, enumerates its artifacts
       straight from resources.cnt, and pulls each artifact's <id>_content blob
       (that blob IS the base64 ArtifactContent the API wants -- verified: it has
       META-INF/MANIFEST.MF, .project and src/ at its root).
    5. Creates the package (POST /IntegrationPackages).
    6. For each artifact, POSTs to its designtime entity set with
       ArtifactContent = base64(inner-bundle zip).

INPUT
    Any genuine package export .zip (start with a SIMPLE one -- a single iFlow --
    so the very first test has one variable: the API path itself, with content you
    already know imports). Once that works, point it at a generated package.

USAGE (see the printed help for all flags)
    python3 cpi_api_deploy.py --service-key key.json --zip MyPackage.zip --first-only --dry-run
    python3 cpi_api_deploy.py --service-key key.json --zip MyPackage.zip --first-only
    python3 cpi_api_deploy.py --service-key key.json --zip MyPackage.zip

REQUIREMENTS
    pip install requests           (the only dependency)
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import zipfile

try:
    import requests
except ImportError:
    sys.exit("This script needs 'requests'. Install it with:  pip install requests")


# ---------------------------------------------------------------------------
# OData entity set per resource type. IntegrationDesigntimeArtifacts (iFlow) is
# the well-documented one we test first. The mapping artifacts are documented
# too; ScriptCollection's exact entity set is the one to confirm on your tenant.
# ---------------------------------------------------------------------------
ENTITY_SET = {
    "IFlow": "IntegrationDesigntimeArtifacts",
    "MessageMapping": "MessageMappingDesigntimeArtifacts",
    "ValueMapping": "ValueMappingDesigntimeArtifacts",
    "ScriptCollection": "ScriptCollectionDesigntimeArtifacts",  # confirm on tenant
}
# Types we never push as standalone artifacts (the package itself / link-only / files):
SKIP_TYPES = {"ContentPackage", "Url", "PartnerLogo", "MediaLink", "File"}


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
def load_credentials(args):
    """
    Resolve (token_url, base_url, client_id, client_secret).

    Priority: explicit CLI flags > --service-key JSON > environment variables.
    The service key is the CF 'Process Integration Runtime' / Integration Suite
    key with an "oauth" block: clientid, clientsecret, tokenurl, url.
    """
    cid = args.client_id or os.environ.get("CPI_CLIENT_ID")
    csec = args.client_secret or os.environ.get("CPI_CLIENT_SECRET")
    token_url = args.token_url or os.environ.get("CPI_TOKEN_URL")
    base_url = args.base_url or os.environ.get("CPI_BASE_URL")

    if args.service_key:
        with open(args.service_key, "r", encoding="utf-8") as fh:
            key = json.load(fh)
        oauth = key.get("oauth", key)  # some keys nest under "oauth", some don't
        cid = cid or oauth.get("clientid")
        csec = csec or oauth.get("clientsecret")
        token_url = token_url or oauth.get("tokenurl")
        base_url = base_url or oauth.get("url") or key.get("url")

    missing = [n for n, v in [("client id", cid), ("client secret", csec),
                              ("token url", token_url), ("base url", base_url)] if not v]
    if missing:
        sys.exit("Missing credentials: " + ", ".join(missing) +
                 "\nProvide --service-key <key.json>, or the individual flags, or "
                 "CPI_CLIENT_ID / CPI_CLIENT_SECRET / CPI_TOKEN_URL / CPI_BASE_URL env vars.")

    # Normalise the token URL: must end at the /oauth/token endpoint.
    token_url = token_url.rstrip("/")
    if not token_url.endswith("/oauth/token"):
        token_url += "/oauth/token"

    # Normalise the API base: must end at /api/v1.
    base_url = base_url.rstrip("/")
    if "/api/v1" in base_url:
        base_url = base_url[: base_url.index("/api/v1") + len("/api/v1")]
    elif base_url.endswith("/api"):
        base_url += "/v1"
    else:
        base_url += "/api/v1"

    return token_url, base_url, cid, csec


def get_token(token_url, client_id, client_secret, verbose=False):
    if verbose:
        print(f"  -> token URL: {token_url}")
    resp = requests.post(
        token_url,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        headers={"Accept": "application/json"},
        timeout=30,
    )
    if resp.status_code != 200:
        sys.exit(f"Token request failed: HTTP {resp.status_code}\n{resp.text}")
    tok = resp.json().get("access_token")
    if not tok:
        sys.exit(f"Token response had no access_token:\n{resp.text}")
    return tok


def make_session(base_url, token, verbose=False):
    """Build a requests.Session with bearer + a fetched CSRF token + cookies."""
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}",
                      "Accept": "application/json"})
    # CSRF handshake: a GET with 'Fetch' returns the token + sets the session cookie.
    r = s.get(f"{base_url}/IntegrationPackages?$top=1",
              headers={"X-CSRF-Token": "Fetch"}, timeout=30)
    if verbose:
        print(f"  -> CSRF fetch: HTTP {r.status_code}")
    csrf = r.headers.get("x-csrf-token") or r.headers.get("X-CSRF-Token")
    if not csrf:
        sys.exit("Could not fetch a CSRF token (the GET handshake returned none). "
                 f"HTTP {r.status_code}. Check the base URL and that the token is valid.\n"
                 f"{r.text[:500]}")
    s.headers.update({"X-CSRF-Token": csrf})
    return s


# ---------------------------------------------------------------------------
# Reading the package export
# ---------------------------------------------------------------------------
def _alnum_id(text, prefix="A"):
    """API Ids must be alphanumeric and start with a letter."""
    s = re.sub(r"[^A-Za-z0-9]", "", str(text or ""))
    if not s or not s[0].isalpha():
        s = prefix + s
    return s


def read_package(zip_path):
    """
    Parse a package export .zip into {package: {...}, artifacts: [{...}]}.
    Each artifact carries the raw inner-bundle bytes from its <id>_content blob.
    """
    z = zipfile.ZipFile(zip_path)
    resources = json.loads(base64.b64decode(z.read("resources.cnt")))["resources"]

    package = None
    artifacts = []
    for e in resources:
        rtype = e.get("resourceType")
        if rtype == "ContentPackage":
            package = {
                "id": _alnum_id(e.get("name") or e.get("uniqueId"), "Pkg"),
                "name": e.get("displayName") or e.get("name"),
            }
            continue
        if rtype in SKIP_TYPES:
            continue
        if rtype not in ENTITY_SET:
            print(f"  ! skipping unsupported resourceType '{rtype}' ({e.get('name')})")
            continue
        blob_name = f"{e['id']}_content"
        if blob_name not in z.namelist():
            print(f"  ! no content blob for {e.get('name')} ({blob_name}); skipping")
            continue
        display = e.get("displayName") or re.sub(r"\.zip$", "", e.get("name", ""))
        artifacts.append({
            "type": rtype,
            "id": _alnum_id(e.get("uniqueId") or display, "Art"),
            "name": display,
            "content": z.read(blob_name),  # raw inner-bundle zip bytes
        })
    if package is None:
        sys.exit("No ContentPackage entry found in resources.cnt -- is this a real export?")
    return {"package": package, "artifacts": artifacts}


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------
def create_package(session, base_url, pkg, dry_run=False):
    body = {"Id": pkg["id"], "Name": pkg["name"],
            "ShortText": pkg["name"], "Version": "1.0.0"}
    url = f"{base_url}/IntegrationPackages"
    print(f"\n[package] POST {url}\n          Id={pkg['id']!r}  Name={pkg['name']!r}")
    if dry_run:
        print("          (dry-run: not sent)")
        return True
    r = session.post(url, json=body, timeout=60)
    if r.status_code in (200, 201):
        print(f"          OK ({r.status_code}) -- package created")
        return True
    # An existing package is fine: we can still add artifacts under it.
    if r.status_code in (400, 409, 500) and "exist" in r.text.lower():
        print(f"          package already exists ({r.status_code}) -- continuing")
        return True
    print(f"          FAILED ({r.status_code})\n{_indent(r.text)}")
    return False


def create_artifact(session, base_url, art, package_id, dry_run=False):
    entity = ENTITY_SET[art["type"]]
    body = {
        "Id": art["id"],
        "Name": art["name"],
        "PackageId": package_id,
        "ArtifactContent": base64.b64encode(art["content"]).decode("ascii"),
    }
    url = f"{base_url}/{entity}"
    kb = len(art["content"]) / 1024.0
    print(f"\n[{art['type']}] POST {url}\n          Id={art['id']!r}  Name={art['name']!r}  ({kb:.1f} KB content)")
    if dry_run:
        print("          (dry-run: not sent)")
        return True
    r = session.post(url, json=body, timeout=120)
    if r.status_code in (200, 201):
        print(f"          OK ({r.status_code}) -- artifact created")
        return True
    print(f"          FAILED ({r.status_code})\n{_indent(r.text)}")
    return False


def _indent(text, n=10):
    pad = " " * n
    return "\n".join(pad + line for line in (text or "").splitlines()[:40])


# ---------------------------------------------------------------------------
# Importable entry point  (the workbench Tab-5 button calls THIS, in-process)
# ---------------------------------------------------------------------------
def deploy_package(token_url, base_url, client_id, client_secret,
                   package, artifacts, *, first_only=False, all_types=False,
                   only_type=None, skip_package=False, dry_run=False, verbose=False,
                   log=print):
    """
    Deploy a package + artifacts via the OData API. No export zip, no resources.cnt,
    no hash -- the tenant builds those itself.

    package   = {"id", "name"}
    artifacts = [{"type","id","name","content"(bytes)} ...]  (id must be alphanumeric)

    Returns {"package_ok": bool, "results": [(type, id, ok), ...]}.
    Reuse cpi_package_export.artifacts_for_deploy(...) to produce `artifacts`
    straight from generated inner bundles.
    """
    arts = list(artifacts)
    if only_type:
        arts = [a for a in arts if a["type"] == only_type]
    elif not all_types:
        arts = [a for a in arts if a["type"] == "IFlow"]
    if first_only:
        arts = arts[:1]

    base = _normalize_base(base_url)
    session = None
    if not dry_run:
        token = get_token(_normalize_token(token_url), client_id, client_secret, verbose)
        session = make_session(base, token, verbose)

    pkg_ok = True
    if not skip_package:
        pkg_ok = create_package(session, base, package, dry_run)
        if not pkg_ok:
            return {"package_ok": False, "results": []}

    results = []
    for a in arts:
        ok = create_artifact(session, base, a, package["id"], dry_run)
        results.append((a["type"], a["id"], ok))
    return {"package_ok": pkg_ok, "results": results}


def _normalize_token(token_url):
    t = (token_url or "").rstrip("/")
    return t if t.endswith("/oauth/token") else t + "/oauth/token"


def _normalize_base(base_url):
    b = (base_url or "").rstrip("/")
    if "/api/v1" in b:
        return b[: b.index("/api/v1") + len("/api/v1")]
    if b.endswith("/api"):
        return b + "/v1"
    return b + "/api/v1"


def infer_type(content_bytes):
    """Infer the artifact type from an inner-bundle zip's contents."""
    try:
        names = zipfile.ZipFile(io.BytesIO(content_bytes)).namelist()
    except Exception:
        return None
    if any(n.endswith(".iflw") for n in names):
        return "IFlow"
    if any(n.endswith(".mmap") for n in names):
        return "MessageMapping"
    if any(n.endswith(".vmap") or "valuemapping" in n.lower() for n in names):
        return "ValueMapping"
    return "IFlow"  # safe default for a project bundle


def read_bundles_dir(path, package_id, package_name):
    """
    Build (package, artifacts) from a directory of inner-bundle zips. Each *.zip
    is one artifact; type is inferred from its contents; Id/Name from the filename.
    Lets you deploy generated bundles without first building an export envelope.
    """
    artifacts = []
    for fn in sorted(os.listdir(path)):
        if not fn.lower().endswith(".zip"):
            continue
        content = open(os.path.join(path, fn), "rb").read()
        rtype = infer_type(content)
        if rtype not in ENTITY_SET:
            print(f"  ! {fn}: could not infer a supported type; skipping")
            continue
        stem = re.sub(r"\.zip$", "", fn, flags=re.I)
        artifacts.append({"type": rtype, "id": _alnum_id(stem, "Art"),
                          "name": stem, "content": content})
    package = {"id": _alnum_id(package_id or package_name or "GeneratedPackage", "Pkg"),
               "name": package_name or package_id or "Generated Package"}
    return package, artifacts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(
        description="Deploy a CPI package + artifacts via the OData API (no hash).")
    p.add_argument("--zip", help="path to a package export .zip")
    p.add_argument("--bundles-dir",
                   help="path to a folder of inner-bundle .zips (one per artifact); "
                        "deploy generated bundles directly, no export envelope needed")
    p.add_argument("--service-key", help="path to your CF service key JSON (recommended)")
    p.add_argument("--base-url", help="override API base, e.g. https://HOST/api/v1")
    p.add_argument("--token-url", help="override XSUAA token URL")
    p.add_argument("--client-id", help="override OAuth client id")
    p.add_argument("--client-secret", help="override OAuth client secret")
    p.add_argument("--package-id", help="override the package Id to create")
    p.add_argument("--package-name", help="override the package display Name")
    p.add_argument("--first-only", action="store_true",
                   help="deploy only the FIRST artifact (clean one-variable test)")
    p.add_argument("--type", choices=sorted(ENTITY_SET),
                   help="deploy only artifacts of this type (default: iFlows only)")
    p.add_argument("--all-types", action="store_true",
                   help="deploy every supported type, not just iFlows")
    p.add_argument("--skip-package", action="store_true",
                   help="don't create the package (it already exists)")
    p.add_argument("--dry-run", action="store_true",
                   help="show exactly what would be sent, send nothing")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    if not args.zip and not args.bundles_dir:
        sys.exit("Provide one of --zip <export.zip> or --bundles-dir <folder>.")

    print("=== Reading package ===")
    if args.bundles_dir:
        pkg, arts = read_bundles_dir(args.bundles_dir, args.package_id, args.package_name)
    else:
        parsed = read_package(args.zip)
        pkg = parsed["package"]
        arts = parsed["artifacts"]
    if args.package_id:
        pkg["id"] = _alnum_id(args.package_id, "Pkg")
    if args.package_name:
        pkg["name"] = args.package_name

    if args.type:                       # explicit single type
        arts = [a for a in arts if a["type"] == args.type]
    elif not args.all_types:            # default: iFlows only (the clean first test)
        arts = [a for a in arts if a["type"] == "IFlow"]
    if args.first_only:
        arts = arts[:1]

    print(f"  package : {pkg['id']}  ({pkg['name']})")
    print(f"  artifacts to deploy: {len(arts)}")
    for a in arts:
        print(f"    - [{a['type']}] {a['id']}  ({a['name']})")
    if not arts:
        sys.exit("Nothing to deploy with the current filters. "
                 "Use --all-types or --type to widen, and check the zip.")

    token_url, base_url, cid, csec = load_credentials(args)
    print(f"\n  API base: {base_url}")

    session = None
    if not args.dry_run:
        print("\n=== Authenticating ===")
        token = get_token(token_url, cid, csec, args.verbose)
        print("  token acquired")
        session = make_session(base_url, token, args.verbose)
        print("  CSRF token acquired")

    print("\n=== Deploying ===")
    ok_pkg = True
    if not args.skip_package:
        ok_pkg = create_package(session, base_url, pkg, args.dry_run)
        if not ok_pkg:
            sys.exit("\nPackage creation failed -- stopping (use --skip-package if it "
                     "already exists, or fix the error above).")

    results = []
    for a in arts:
        ok = create_artifact(session, base_url, a, pkg["id"], args.dry_run)
        results.append((a["type"], a["id"], ok))

    print("\n=== Summary ===")
    good = sum(1 for *_, ok in results if ok)
    for t, i, ok in results:
        print(f"  {'OK  ' if ok else 'FAIL'}  [{t}] {i}")
    print(f"  {good}/{len(results)} artifact(s) deployed"
          + (" (dry-run)" if args.dry_run else ""))
    if not args.dry_run and good == len(results) and ok_pkg:
        print("\nNext: open the package in the Integration Suite designer and try to "
              "open the iFlow. Import (hash) gate is bypassed; the OPEN gate is separate.")
    sys.exit(0 if good == len(results) else 1)


if __name__ == "__main__":
    main()
