"""Live test/runner for the Discover harvester — run as a normal script.

Place this at the project ROOT (~/PycharmProjects/cpi_migrator/run_harvester.py).
Run from PyCharm (right-click -> Run 'run_harvester') or terminal:
    cd ~/PycharmProjects/cpi_migrator
    source venv/bin/activate
    python run_harvester.py
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

# ── AUTH — fill from your service key (PIR plan 'api'). ──────────────────
CLIENT_ID     = "sb-c2bd402f-d456-496a-8a42-cec30076015e!b655221|it!b26655"
CLIENT_SECRET = "<PASTE_YOUR_CURRENT_SECRET>"

# ── Controls ─────────────────────────────────────────────────────────────
PACKAGE_REG_ID = ""    # paste an iFlow package's reg_id from the printed list
LIST_ONLY = True       # True = just list. False = harvest PACKAGE_REG_ID.
NAME_FILTER = ""       # optional: only show packages whose name contains this


def build_session() -> requests.Session:
    s = requests.Session()
    resp = requests.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(CLIENT_ID, CLIENT_SECRET),
        headers={"Accept": "application/json"},
        timeout=30,
    )
    if resp.status_code != 200:
        print("TOKEN REQUEST FAILED")
        print("  status:", resp.status_code)
        print("  body:", resp.text[:500])
        print("  secret length:", len(CLIENT_SECRET),
              "(26 = still the placeholder)")
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

    print("\n=== Listing Discover packages ===")
    pkgs = h.list_discover_packages(top=1000)
    if not pkgs:
        print("No packages returned. Does the design host accept your token?")
        return

    if NAME_FILTER:
        pkgs = [p for p in pkgs if NAME_FILTER.lower() in p["name"].lower()]

    print(f"Found {len(pkgs)} packages. Showing first 50 (I = #iFlows inline):")
    for p in pkgs[:50]:
        n_iflow = sum(1 for a in p["assets"] if a["type"] == "IntegrationFlow")
        print(f"  {p['id']:34}  I={n_iflow:<2}  {p['name'][:48]:48}  {p['vendor']}")
    print("\nPick a reg_id with I>=1, set PACKAGE_REG_ID + LIST_ONLY=False, re-run.")

    if LIST_ONLY or not PACKAGE_REG_ID:
        return

    chosen = next((p for p in pkgs if p["id"] == PACKAGE_REG_ID), None)
    known = chosen["assets"] if chosen else None

    print(f"\n=== Harvesting {PACKAGE_REG_ID} ===")
    if chosen:
        print(f"  {chosen['name']} — {len(known)} inline asset(s)")
    res = h.harvest_one(PACKAGE_REG_ID, prefer="catalog",
                        allow_copy_fallback=True, cleanup=True,
                        known_assets=known)
    print(f"\nStrategy used: {res.strategy}")
    print(f"Copied: {res.copied}   Deleted: {res.deleted}")
    print(f"Downloaded {res.n_downloaded}/{len(res.assets)} assets:")
    for a in res.assets:
        flag = "OK  " if a.downloaded else "skip"
        extra = f"{a.bytes_len}B -> {a.path}" if a.downloaded else a.reason
        print(f"  [{flag}] {a.asset_type:18} {(a.title or '')[:30]:30} {extra}")
    if res.errors:
        print("Errors:", res.errors)


if __name__ == "__main__":
    main()
