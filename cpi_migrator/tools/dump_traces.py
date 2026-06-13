"""tools/dump_traces.py — capture trace-mode run data for decode (#5).

The jars/bundles are the PROGRAM; this dumps the EXECUTIONS: for a given
message (or the latest runs of an iFlow), walk the MPL trace entity chain
and write everything to disk:

    MessageProcessingLogs('<guid>')
      └── /Runs
            └── /RunSteps?$expand=RunStepProperties
                  └── /TraceMessages          (per-step payload snapshots)
                        ├── $value            (the payload bytes)
                        ├── /Properties
                        └── /ExchangeProperties

Usage (user's machine — needs tenant reachability):
    python3 -m tools.dump_traces --service-key ~/.cpi_migrator/keys/key.json \
        --iflow "Stress Lab All Steps" --last 3 --out output/traces

    python3 -m tools.dump_traces --service-key key.json --guid <MessageGuid>

Notes:
  - Trace log level stays active only ~10 minutes per activation and trace
    payloads are retained briefly — run the flow, then dump PROMPTLY.
  - Entity paths follow the documented MPL OData model; this tool's first
    live contact is the user's run — unexpected 4xx → keep the JSON error
    files, they ARE the decode input.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("tools.dump_traces")


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(s))[:120]


def _get_json(session, url):
    r = session.get(url, headers={"Accept": "application/json"}, timeout=60)
    try:
        body = r.json()
    except Exception:
        body = {"_raw": r.text[:4000]}
    return r.status_code, body


def _results(body) -> list:
    d = body.get("d", body)
    if isinstance(d, dict):
        return d.get("results", []) or ([d] if d else [])
    return d if isinstance(d, list) else []


def dump_message(session, base_url: str, guid: str, out_dir: str) -> dict:
    """Dump one message's full trace chain. Returns a summary dict."""
    base = base_url.rstrip("/") + "/api/v1"
    root = os.path.join(out_dir, _safe(guid))
    os.makedirs(root, exist_ok=True)
    summary = {"guid": guid, "runs": 0, "steps": 0, "traces": 0,
               "payload_bytes": 0, "errors": []}

    def save(rel, data):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        mode = "wb" if isinstance(data, (bytes, bytearray)) else "w"
        with open(p, mode) as fh:
            if mode == "w":
                json.dump(data, fh, indent=1, default=str)
            else:
                fh.write(data)

    st, mpl = _get_json(session,
                        f"{base}/MessageProcessingLogs('{guid}')")
    save("mpl.json", mpl)
    if st != 200:
        summary["errors"].append(f"mpl HTTP {st}")
        return summary

    st, runs = _get_json(session,
                         f"{base}/MessageProcessingLogs('{guid}')/Runs")
    save("runs.json", runs)
    if st != 200:
        summary["errors"].append(f"runs HTTP {st}")
        return summary

    for run in _results(runs):
        run_id = run.get("Id") or run.get("RunId") or ""
        summary["runs"] += 1
        rdir = f"run_{_safe(run_id)}"
        st, steps = _get_json(
            session, f"{base}/MessageProcessingLogRuns('{run_id}')"
                     f"/RunSteps?$expand=RunStepProperties")
        save(f"{rdir}/run_steps.json", steps)
        if st != 200:
            summary["errors"].append(f"runsteps({run_id}) HTTP {st}")
            continue
        for step in _results(steps):
            summary["steps"] += 1
            sid = _safe(step.get("StepId") or step.get("Id") or
                        str(summary["steps"]))
            # trace messages hang off the run step via composite key
            run_key = step.get("RunId") or run_id
            cn = step.get("ChildCount")
            tm_url = (f"{base}/MessageProcessingLogRunSteps(RunId='{run_key}'"
                      f",ChildCount={cn})/TraceMessages")
            st2, traces = _get_json(session, tm_url)
            save(f"{rdir}/{sid}/trace_messages.json", traces)
            if st2 != 200:
                continue
            for tm in _results(traces):
                tid = tm.get("TraceId") or tm.get("Id") or ""
                summary["traces"] += 1
                # payload bytes
                pr = session.get(f"{base}/TraceMessages({tid})/$value",
                                 timeout=60)
                if pr.status_code == 200:
                    save(f"{rdir}/{sid}/trace_{_safe(tid)}_payload.bin",
                         pr.content)
                    summary["payload_bytes"] += len(pr.content)
                for sub in ("Properties", "ExchangeProperties"):
                    st3, props = _get_json(
                        session, f"{base}/TraceMessages({tid})/{sub}")
                    if st3 == 200:
                        save(f"{rdir}/{sid}/trace_{_safe(tid)}_"
                             f"{sub.lower()}.json", props)
    save("summary.json", summary)
    return summary


def latest_guids(session, base_url: str, iflow: str, last: int) -> list:
    """Newest MessageGuids. The MPL filter field IntegrationFlowName holds
    the artifact ID (e.g. 'StressLabAllSteps'), NOT the display name —
    so if the given name has spaces, an ID guess (spaces stripped) is
    tried too, and on zero matches the actual recent flow names are
    printed so the user can see real values."""
    from urllib.parse import quote
    base = base_url.rstrip("/") + "/api/v1"

    def fetch(flt: str) -> list:
        url = (f"{base}/MessageProcessingLogs?$top={last}"
               f"&$orderby={quote('LogEnd desc')}&$format=json"
               + (f"&$filter={quote(flt)}" if flt else ""))
        st, body = _get_json(session, url)
        if st != 200:
            logger.error("MPL list failed: HTTP %s", st)
            return []
        return _results(body)

    candidates = []
    if iflow:
        candidates.append(f"IntegrationFlowName eq '{iflow}'")
        guess = iflow.replace(" ", "")
        if guess != iflow:
            candidates.append(f"IntegrationFlowName eq '{guess}'")
    else:
        candidates.append("")
    for flt in candidates:
        rows = fetch(flt)
        if rows:
            if flt and "eq" in flt:
                logger.info("matched filter: %s", flt)
            return [r.get("MessageGuid") for r in rows
                    if r.get("MessageGuid")]
    # diagnostic: show what names actually exist in the newest logs
    recent = fetch("")
    if recent:
        names = sorted({str(r.get("IntegrationFlowName")) for r in recent})
        logger.error("no match for %r — recent flows on the tenant: %s "
                     "(use the artifact ID, or --last N without --iflow)",
                     iflow, names)
    return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Dump CPI trace-mode run data.")
    ap.add_argument("--service-key", required=True)
    ap.add_argument("--guid", default="", help="one MessageGuid")
    ap.add_argument("--iflow", default="", help="…or latest runs of this flow")
    ap.add_argument("--last", type=int, default=3)
    ap.add_argument("--out", default="output/traces")
    args = ap.parse_args(argv)

    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    from fetcher.run_poller import load_service_key, make_authenticator
    cfg = load_service_key(os.path.expanduser(args.service_key))
    session = make_authenticator(cfg).get_session()

    guids = [args.guid] if args.guid else latest_guids(
        session, cfg["base_url"], args.iflow, args.last)
    if not guids:
        logger.error("no message guids found — did the flow run?")
        return 2
    for g in guids:
        s = dump_message(session, cfg["base_url"], g, args.out)
        logger.info("dumped %s: %d run(s), %d step(s), %d trace(s), "
                    "%d payload bytes%s", g, s["runs"], s["steps"],
                    s["traces"], s["payload_bytes"],
                    f" — errors: {s['errors']}" if s["errors"] else "")
    logger.info("→ zip the '%s' folder and upload it for decode", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
