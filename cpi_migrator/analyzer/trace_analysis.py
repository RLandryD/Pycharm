"""analyzer/trace_analysis.py — MPL trace decode + analysis (#5).

Input: a folder produced by tools/dump_traces.py. Decoded structure
(verified on real tenant captures 2026-06-12, zero API errors):

  <guid>/mpl.json                      message header (Status, flow, times)
  <guid>/runs.json
  <guid>/run_<id>/run_steps.json       results[] with ChildCount (=execution
        order, ascending), StepId, ModelStepId (joins to BPMN element ids
        in our parsed models), Status (FAILED on the failing step, with
        Error carrying the full Camel exception), StepStart/Stop
        (/Date(ms)/), BranchId, RunStepProperties
  <guid>/run_<id>/<step>/trace_messages.json   MimeType, PayloadSize,
        ModelStepId, TraceId
  <guid>/run_<id>/<step>/trace_<id>_payload.bin            payload bytes
  <guid>/run_<id>/<step>/trace_<id>_properties.json        headers
  <guid>/run_<id>/<step>/trace_<id>_exchangeproperties.json

This is the "70% of real troubleshooting" view: execution timeline,
failure pinpoint with the actual exception, and per-step payload
evolution (sizes, content sniff, where the body changed shape).
"""
from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass, field

_DATE_RX = re.compile(r"/Date\((\d+)\)/")


def _ms(v) -> int:
    m = _DATE_RX.search(str(v or ""))
    return int(m.group(1)) if m else 0


def _results(body) -> list:
    d = body.get("d", body) if isinstance(body, dict) else body
    if isinstance(d, dict):
        return d.get("results", [])
    return d if isinstance(d, list) else []


def _sniff(data: bytes) -> str:
    head = data[:64].lstrip()
    if head.startswith(b"<?xml") or head.startswith(b"<"):
        return "xml"
    if head[:1] in (b"{", b"["):
        return "json"
    if b"," in data[:200] and b"<" not in data[:200]:
        return "csv?"
    return "text/binary"


@dataclass
class TraceStep:
    order: int
    step_id: str
    model_step_id: str
    status: str
    error: str = ""
    start_ms: int = 0
    stop_ms: int = 0
    branch: str = ""
    payload_size: int = -1          # -1 = no payload captured
    payload_kind: str = ""
    payload_head: str = ""
    headers: dict = field(default_factory=dict)

    @property
    def duration_ms(self) -> int:
        return max(0, self.stop_ms - self.start_ms) if self.stop_ms else 0


@dataclass
class MessageTrace:
    guid: str
    status: str
    iflow: str
    log_end_ms: int
    steps: list = field(default_factory=list)        # TraceStep, exec order
    notes: list = field(default_factory=list)

    @property
    def failure(self) -> "TraceStep | None":
        for s in self.steps:
            if s.status == "FAILED":
                return s
        return None


def load_message(msg_dir: str) -> MessageTrace:
    mpl = {}
    try:
        mpl = json.load(open(os.path.join(msg_dir, "mpl.json"))).get("d", {})
    except Exception:
        pass
    mt = MessageTrace(
        guid=mpl.get("MessageGuid") or os.path.basename(msg_dir),
        status=mpl.get("Status") or "",
        iflow=mpl.get("IntegrationFlowName") or "",
        log_end_ms=_ms(mpl.get("LogEnd")))

    run_dirs = sorted(glob.glob(os.path.join(msg_dir, "run_*")))
    if not run_dirs:
        mt.notes.append("no runs captured (trace not active?)")
        return mt
    rd = run_dirs[-1]

    # payloads indexed by ModelStepId via each step folder's trace catalog
    payloads: dict = {}
    for tm_path in glob.glob(os.path.join(rd, "*", "trace_messages.json")):
        sdir = os.path.dirname(tm_path)
        try:
            for tm in _results(json.load(open(tm_path))):
                mid = tm.get("ModelStepId") or ""
                tid = tm.get("TraceId") or ""
                entry = {"size": int(tm.get("PayloadSize") or 0),
                         "mime": tm.get("MimeType") or "", "head": "",
                         "kind": "", "headers": {}}
                pb = glob.glob(os.path.join(
                    sdir, f"trace_*{tid}*_payload.bin")) or glob.glob(
                    os.path.join(sdir, "trace_*_payload.bin"))
                if pb:
                    data = open(pb[0], "rb").read()
                    entry["size"] = len(data)
                    entry["kind"] = _sniff(data)
                    entry["head"] = data[:160].decode("utf-8", "replace")
                pj = glob.glob(os.path.join(
                    sdir, f"trace_*{tid}*_properties.json"))
                if pj:
                    try:
                        entry["headers"] = {
                            p.get("Name"): p.get("Value") for p in
                            _results(json.load(open(pj[0])))
                            if not str(p.get("Name", "")).startswith(
                                "SAP_TRACE_HEADER")}
                    except Exception:
                        pass
                if mid:
                    payloads.setdefault(mid, entry)
        except Exception:
            continue

    try:
        rows = _results(json.load(open(os.path.join(rd, "run_steps.json"))))
    except Exception:
        mt.notes.append("run_steps.json unreadable")
        return mt
    rows.sort(key=lambda r: int(r.get("ChildCount") or 0))
    for r in rows:
        mid = r.get("ModelStepId") or ""
        pay = payloads.get(mid, {})
        mt.steps.append(TraceStep(
            order=int(r.get("ChildCount") or 0),
            step_id=r.get("StepId") or "",
            model_step_id=mid,
            status=r.get("Status") or "",
            error=(r.get("Error") or "").strip(),
            start_ms=_ms(r.get("StepStart")),
            stop_ms=_ms(r.get("StepStop")),
            branch=r.get("BranchId") or "",
            payload_size=pay.get("size", -1) if pay else -1,
            payload_kind=pay.get("kind", ""),
            payload_head=pay.get("head", ""),
            headers=pay.get("headers", {})))
    return mt


def load_dump(folder: str) -> list:
    out = []
    for d in sorted(glob.glob(os.path.join(folder, "*"))):
        if os.path.isdir(d) and os.path.exists(
                os.path.join(d, "mpl.json")):
            out.append(load_message(d))
    return out


def analyze(mt: MessageTrace, model_names: dict | None = None) -> dict:
    """Findings: failure pinpoint, payload evolution, duration hotspots.
    model_names: optional {bpmn element id: human name} from parse_iflow."""
    names = model_names or {}

    def label(s: TraceStep) -> str:
        return names.get(s.model_step_id) or s.model_step_id or \
            s.step_id or f"step {s.order}"

    findings = {"guid": mt.guid, "status": mt.status, "iflow": mt.iflow,
                "n_steps": len(mt.steps), "failure": None,
                "payload_evolution": [], "hotspots": [], "notes": mt.notes}
    f = mt.failure
    if f:
        exc = f.error.split(":", 1)
        findings["failure"] = {
            "at": label(f), "order": f.order,
            "exception": exc[0].strip(),
            "message": (exc[1].strip() if len(exc) > 1 else "")[:500],
            "last_good_payload": next(
                (s.payload_head for s in reversed(mt.steps)
                 if s.order < f.order and s.payload_size >= 0), "")}
    prev = None
    for s in mt.steps:
        if s.payload_size < 0:
            continue
        delta = (s.payload_size - prev["size"]) if prev else 0
        changed_kind = prev and prev["kind"] != s.payload_kind
        findings["payload_evolution"].append({
            "step": label(s), "order": s.order, "size": s.payload_size,
            "delta": delta, "kind": s.payload_kind,
            "kind_changed": bool(changed_kind)})
        prev = {"size": s.payload_size, "kind": s.payload_kind}
    timed = [s for s in mt.steps if s.duration_ms > 0]
    for s in sorted(timed, key=lambda x: -x.duration_ms)[:5]:
        findings["hotspots"].append({"step": label(s),
                                     "ms": s.duration_ms})
    return findings


def model_names_from_iflw(iflw_xml: str) -> dict:
    """{element id: human name} join table from a parsed iFlow."""
    try:
        from extractor.iflow_parser import parse_iflow
        m = parse_iflow(iflw_xml, "trace_join")
        out = {sid: (s.name or s.kind or sid)
               for sid, s in m.steps.items()}
        return out
    except Exception:
        return {}
