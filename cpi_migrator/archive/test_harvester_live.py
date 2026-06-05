"""Standalone live test for the Discover harvester — run in PyCharm.

Tests against your real tenant. Fill in AUTH below the same way the workbench
builds its authenticated session (bearer token from the OAuth client-credentials
flow you already use for upload). Then run this file.

Usage:
  1. Set PACKAGE_REG_ID to a Discover package's registration id.
     (For the ServiceNow adapter you inspected: 51772143d916449e8b5e83684eb83861
      — but that's adapter-only; pick an iFlow package to test the iFlow path.)
  2. Run. It will try catalog-direct first, then copy-fallback.
"""
import logging
import requests

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from fetcher.discover_harvester import DiscoverHarvester

# ── Hosts (from your tenant) ─────────────────────────────────────────────
DESIGN_BASE  = "https://0140aa99trial.integrationsuite-trial.cfapps.us10-001.hana.ondemand.com"
RUNTIME_BASE = "https://0140aa99trial.it-cpitrial05.cfapps.us10-001.hana.ondemand.com"
TOKEN_URL    = "https://0140aa99trial.authentication.us10.hana.ondemand.com/oauth/token"

# ── AUTH — fill these from your service key (PIR plan 'api') ─────────────
CLIENT_ID     = "sb-c2bd402f-d456-496a-8a42-cec30076015e!b655221|it!b26655"
CLIENT_SECRET = "<PASTE_YOUR_ROTATED_SECRET>"   # rotate the old one!

# ── Which package to harvest ─────────────────────────────────────────────
PACKAGE_REG_ID = "51772143d916449e8b5e83684eb83861"  # ServiceNow (adapter-only)
# Better: replace with an iFlow-containing Discover package's reg id.


def build_session() -> requests.Session:
    """OAuth client-credentials -> bearer token on a requests.Session.

    Note: the Discover/design host may require the SAME bearer token your
    runtime API uses (same XSUAA tenant). If the design host rejects the token,
    that's the thing to flag — it may need a separate scope.
    """
    s = requests.Session()
    resp = requests.post(TOKEN_URL,
                         data={"grant_type": "client_credentials"},
                         auth=(CLIENT_ID, CLIENT_SECRET), timeout=30)
    resp.raise_for_status()
    token = resp.json()["access_token"]
    s.headers.update({"Authorization": f"Bearer {token}",
                      "Accept": "application/json"})
    return s


def main():
    session = build_session()
    h = DiscoverHarvester(design_base_url=DESIGN_BASE, session=session,
                          runtime_base_url=RUNTIME_BASE,
                          download_dir="./harvested")

    print("\n=== STRATEGY B first: catalog-direct, copy fallback ===")
    res = h.harvest_one(PACKAGE_REG_ID, prefer="catalog",
                        allow_copy_fallback=True, cleanup=True)
    print(f"\nPackage: {res.package_id}")
    print(f"Strategy used: {res.strategy}")
    print(f"Copied: {res.copied}  Deleted: {res.deleted}")
    print(f"Downloaded {res.n_downloaded}/{len(res.assets)} assets:")
    for a in res.assets:
        flag = "OK" if a.downloaded else "skip"
        extra = f"{a.bytes_len}B -> {a.path}" if a.downloaded else a.reason
        print(f"  [{flag}] {a.asset_type:18} {a.title[:30]:30} {extra}")
    if res.errors:
        print("Errors:", res.errors)


if __name__ == "__main__":
    main()
