"""Interactive setup wizard + key health check.

`setup`: opens each provider's signup page, prompts for the key, validates it
with a minimal live API call, and writes .env (chmod 600). Keys are never
logged — only a masked tail.
`check`: re-validates whatever is currently configured.
"""
import os
import stat
import webbrowser

import requests

from .config import Settings
from .log import feature, log_event

ENV_PATH = ".env"


def mask(key: str) -> str:
    return ("*" * 4 + key[-4:]) if len(key) >= 8 else "*" * len(key)


# ---- live validators (return (ok: bool, detail: str)) -------------------------

def validate_jsearch(key: str, timeout: int = 20):
    try:
        r = requests.get("https://jsearch.p.rapidapi.com/search",
                         headers={"X-RapidAPI-Key": key,
                                  "X-RapidAPI-Host": "jsearch.p.rapidapi.com"},
                         params={"query": "SAP", "page": 1, "num_pages": 1,
                                 "country": "mx"},
                         timeout=timeout)
        return interpret_status("jsearch", r.status_code)
    except requests.RequestException as e:
        return None, f"could not verify (network): {e}"


def validate_adzuna(app_id: str, app_key: str, timeout: int = 20):
    try:
        r = requests.get("https://api.adzuna.com/v1/api/jobs/mx/search/1",
                         params={"app_id": app_id, "app_key": app_key,
                                 "what": "SAP", "results_per_page": 1},
                         timeout=timeout)
        return interpret_status("adzuna", r.status_code)
    except requests.RequestException as e:
        return None, f"could not verify (network): {e}"


def validate_serper(key: str, timeout: int = 20):
    try:
        r = requests.post("https://google.serper.dev/search",
                          headers={"X-API-KEY": key, "Content-Type": "application/json"},
                          json={"q": "test", "num": 1}, timeout=timeout)
        return interpret_status("serper", r.status_code)
    except requests.RequestException as e:
        return None, f"could not verify (network): {e}"


def validate_google_cse(key: str, cx: str, timeout: int = 20):
    try:
        r = requests.get("https://www.googleapis.com/customsearch/v1",
                         params={"key": key, "cx": cx, "q": "test", "num": 1},
                         timeout=timeout)
        return interpret_status("google_cse", r.status_code)
    except requests.RequestException as e:
        return None, f"could not verify (network): {e}"


def interpret_status(source: str, status: int):
    """Pure mapping from HTTP status to verdict — unit-testable."""
    if status == 200:
        return True, "valid"
    if status in (401, 403):
        return False, f"rejected by {source} (HTTP {status}) — wrong key or not subscribed"
    if status == 429:
        return True, f"key accepted but rate/quota limited right now (HTTP 429)"
    if status == 404 and source == "adzuna":
        return False, "adzuna mx endpoint returned 404 — check account country access"
    return None, f"inconclusive (HTTP {status})"


# ---- .env writing ---------------------------------------------------------------

def write_env(updates: dict, path: str = ENV_PATH) -> str:
    """Merge updates into .env, preserving unrelated lines. chmod 600."""
    lines, seen = [], set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f.read().splitlines():
                k = line.split("=", 1)[0].strip() if "=" in line else None
                if k in updates:
                    lines.append(f"{k}={updates[k]}")
                    seen.add(k)
                else:
                    lines.append(line)
    for k, v in updates.items():
        if k not in seen:
            lines.append(f"{k}={v}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)   # 600: owner read/write only
    return path


# ---- wizard ---------------------------------------------------------------------

PROVIDERS = [
    {
        "name": "JSearch (RapidAPI) — job postings source [recommended]",
        "url": "https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch",
        "steps": ["Create a free RapidAPI account",
                  "On the JSearch page choose Pricing -> subscribe to Basic (free)",
                  "Copy the X-RapidAPI-Key value from the code snippet panel"],
        "fields": [("RAPIDAPI_KEY", "Paste your RapidAPI key")],
        "validate": lambda vals: validate_jsearch(vals["RAPIDAPI_KEY"]),
    },
    {
        "name": "Serper.dev — people discovery (Google search API) [recommended]",
        "url": "https://serper.dev",
        "steps": ["Sign up free (2,500 queries included, no credit card)",
                  "Copy the API key from your dashboard"],
        "fields": [("SERPER_API_KEY", "Paste your Serper API key")],
        "validate": lambda vals: validate_serper(vals["SERPER_API_KEY"]),
    },
    {
        "name": "Google Custom Search — people discovery [legacy: existing customers only, "
                "closed to new signups, EOL Jan 2027]",
        "url": "https://programmablesearchengine.google.com",
        "steps": ["Only works for accounts that already had Custom Search JSON API access",
                  "New projects get 403 PERMISSION_DENIED — use Serper above instead"],
        "fields": [("GOOGLE_CSE_KEY", "Paste your Google API key (or Enter to skip)"),
                   ("GOOGLE_CSE_CX", "Paste your Search engine ID (cx)")],
        "validate": lambda vals: validate_google_cse(vals["GOOGLE_CSE_KEY"],
                                                     vals["GOOGLE_CSE_CX"]),
    },
    {
        "name": "Adzuna — second job source [optional]",
        "url": "https://developer.adzuna.com/signup",
        "steps": ["Register (free) — App ID and App Key are shown immediately"],
        "fields": [("ADZUNA_APP_ID", "Paste your Adzuna App ID"),
                   ("ADZUNA_APP_KEY", "Paste your Adzuna App Key")],
        "validate": lambda vals: validate_adzuna(vals["ADZUNA_APP_ID"],
                                                 vals["ADZUNA_APP_KEY"]),
    },
]


def run_setup(open_browser: bool = True, input_fn=input) -> int:
    with feature("setup_wizard"):
        collected = {}
        print("\nlead_radar setup — keys are validated live and saved to .env (chmod 600).")
        print("Press Enter on an empty prompt to skip a provider.\n")
        for prov in PROVIDERS:
            print(f"--- {prov['name']}")
            for s in prov["steps"]:
                print(f"    * {s}")
            print(f"    URL: {prov['url']}")
            if open_browser:
                try:
                    webbrowser.open(prov["url"])
                except Exception:
                    pass
            vals, skipped = {}, False
            for env_key, prompt in prov["fields"]:
                v = input_fn(f"    {prompt}: ").strip()
                if not v:
                    print("    skipped.\n")
                    skipped = True
                    break
                vals[env_key] = v
            if skipped:
                continue
            ok, detail = prov["validate"](vals)
            if ok is False:
                print(f"    INVALID: {detail} — not saved. Re-run setup to retry.\n")
                log_event("setup_key_rejected", provider=prov["name"], detail=detail)
                continue
            collected.update(vals)
            tag = "verified" if ok else "saved unverified"
            print(f"    OK ({tag}): {detail}\n")
            log_event("setup_key_accepted", provider=prov["name"],
                      keys={k: mask(v) for k, v in vals.items()}, detail=detail)
        if collected:
            write_env(collected)
            print(f"Saved {len(collected)} value(s) to .env")
        else:
            print("Nothing saved.")
    return 0


def run_check() -> int:
    with feature("key_check"):
        s = Settings()
        results = []
        if s.rapidapi_key:
            results.append(("JSearch", *validate_jsearch(s.rapidapi_key)))
        if s.adzuna_app_id and s.adzuna_app_key:
            results.append(("Adzuna", *validate_adzuna(s.adzuna_app_id, s.adzuna_app_key)))
        if s.serper_api_key:
            results.append(("Serper", *validate_serper(s.serper_api_key)))
        if s.google_cse_key and s.google_cse_cx:
            results.append(("Google CSE", *validate_google_cse(s.google_cse_key,
                                                               s.google_cse_cx)))
        if not results:
            print("No keys configured. Run: python -m lead_radar.cli setup")
            return 1
        bad = 0
        for name, ok, detail in results:
            status = "OK" if ok else ("FAIL" if ok is False else "?")
            print(f"  [{status:4}] {name}: {detail}")
            if ok is False:
                bad += 1
        return 1 if bad else 0
