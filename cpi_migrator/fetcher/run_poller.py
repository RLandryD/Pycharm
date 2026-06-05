"""
fetcher/run_poller.py — standalone background poller for CPI runs.

Runs independently of the Streamlit UI (so it keeps collecting while you do
other things) and appends new Message Processing Log entries into ONE deduped
file via run_collector. The workbench starts/stops it as a detached subprocess;
it can also be run by hand:

    python3 -m fetcher.run_poller --service-key key.json --file output/cpi_runs.json \
            --iflow XL_Orchestration_BPM_MultiMap --interval 60

Stop it with the workbench button, Ctrl-C, or by deleting the --pid-file.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── config / auth ──────────────────────────────────────────────────────────
def load_service_key(path: str) -> dict:
    """Extract {base_url, token_url, client_id, client_secret} from a CF service
    key JSON (handles both the nested `oauth` form and a flat form)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    oauth = data.get("oauth", data)
    base = data.get("url") or oauth.get("url") or ""
    token = oauth.get("tokenurl") or data.get("tokenurl") or ""
    if token and not token.rstrip("/").endswith("/oauth/token"):
        token = token.rstrip("/") + "/oauth/token"
    return {
        "base_url": base.rstrip("/"),
        "token_url": token,
        "client_id": oauth.get("clientid") or data.get("clientid") or "",
        "client_secret": oauth.get("clientsecret") or data.get("clientsecret") or "",
    }


def make_authenticator(cfg: dict):
    from auth.authenticator import CFAuthenticator
    return CFAuthenticator(cfg["token_url"], cfg["client_id"], cfg["client_secret"])


def refresh_session(session, authenticator):
    """Ensure the session carries a current bearer token (the authenticator's
    cache refreshes it when near expiry)."""
    try:
        token = authenticator._get_token()        # refreshes if stale
        session.headers.update({"Authorization": f"Bearer {token}"})
    except Exception as exc:                        # noqa
        logger.warning("token refresh failed: %s", exc)
    return session


# ── core (unit-testable) ─────────────────────────────────────────────────────
def poll_once(fetcher, file_path: str, iflow: str,
              cursor: Optional[datetime]) -> tuple[int, int, Optional[datetime]]:
    """One poll cycle: fetch runs newer than `cursor`, dedup-append to the file,
    return (added, total, new_cursor). Pure w.r.t. I/O so it can be tested with
    a mocked fetcher."""
    from fetcher.run_collector import append_runs, max_log_end
    runs = fetcher.runs_since(cursor, iflow_name=iflow, top=200)
    added, total = append_runs(file_path, runs)
    newest = max_log_end(runs)
    if newest and (cursor is None or newest > cursor):
        cursor = newest
    return added, total, cursor


# ── loop / CLI ───────────────────────────────────────────────────────────────
def run(cfg: dict, file_path: str, iflow: str = "", interval: int = 60,
        pid_file: str = "", stop_file: str = "", max_cycles: int = 0) -> None:
    """Long-running poll loop. `max_cycles>0` bounds it (used by tests)."""
    from fetcher.mpl_fetcher import MPLFetcher
    auth = make_authenticator(cfg)
    session = auth.get_session()
    fetcher = MPLFetcher(cfg["base_url"], session)

    if pid_file:
        Path(pid_file).parent.mkdir(parents=True, exist_ok=True)
        Path(pid_file).write_text(str(os.getpid()), encoding="utf-8")

    stop = {"flag": False}

    def _handle(signum, frame):                     # noqa
        stop["flag"] = True
    try:
        signal.signal(signal.SIGTERM, _handle)
        signal.signal(signal.SIGINT, _handle)
    except Exception:                                # noqa (e.g. non-main thread)
        pass

    cursor: Optional[datetime] = None
    cycles = 0
    logger.info("poller started → file=%s iflow=%s interval=%ss",
                file_path, iflow or "(all)", interval)
    try:
        while not stop["flag"]:
            if stop_file and Path(stop_file).exists():
                logger.info("stop file present — exiting")
                break
            refresh_session(session, auth)
            try:
                added, total, cursor = poll_once(fetcher, file_path, iflow, cursor)
                logger.info("cycle: +%d new (%d total)", added, total)
            except Exception as exc:                 # noqa — never let one cycle kill the loop
                logger.warning("poll cycle error: %s", exc)
            cycles += 1
            if max_cycles and cycles >= max_cycles:
                break
            # responsive sleep so stop is honored quickly
            for _ in range(max(1, interval)):
                if stop["flag"]:
                    break
                time.sleep(1)
    finally:
        if pid_file:
            try:
                Path(pid_file).unlink()
            except Exception:                        # noqa
                pass
        logger.info("poller stopped")


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Poll CPI MPL into one file.")
    ap.add_argument("--service-key", help="CF service key JSON path")
    ap.add_argument("--base-url"); ap.add_argument("--token-url")
    ap.add_argument("--client-id"); ap.add_argument("--client-secret")
    ap.add_argument("--file", required=True, help="output JSON file (deduped)")
    ap.add_argument("--iflow", default="", help="filter by iFlow name")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--pid-file", default="")
    ap.add_argument("--stop-file", default="")
    args = ap.parse_args(argv)

    if args.service_key:
        cfg = load_service_key(args.service_key)
    else:
        cfg = {"base_url": (args.base_url or "").rstrip("/"),
               "token_url": args.token_url or "", "client_id": args.client_id or "",
               "client_secret": args.client_secret or ""}
    if not cfg["base_url"] or not cfg["token_url"]:
        print("error: need --service-key or --base-url and --token-url", file=sys.stderr)
        return 2
    run(cfg, args.file, iflow=args.iflow, interval=args.interval,
        pid_file=args.pid_file, stop_file=args.stop_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
