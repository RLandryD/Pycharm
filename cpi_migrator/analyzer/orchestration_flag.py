"""analyzer/orchestration_flag.py — ccBPM / orchestration-shape detection.

Why (2026-06-12): PI/PO ccBPM integration processes do NOT map 1:1 to a
single CPI iFlow — stateful orchestration typically splits into
CPI + SAP Build Process Automation (or needs redesign into stateless
patterns). An assessment that prices a ccBPM-shaped interface as a plain
mapping flow is wrong by an architecture decision, not by hours. This flag
makes the assessment say so explicitly.

Detection is heuristic and ADDITIVE-scored from three evidence classes:
  1. engine signals (call_activities, routers, participants, service_tasks)
  2. step-kind counts from the BPMN model (Splitter/Gather/Join, JMS-ish
     Send, DBstorage/Variables persistence, timers, multicast)
  3. naming (ccBPM / BPM / "integration process" / IP_ conventions)

Honest framing baked into the recommendation text: this is a SCOPE flag
for a human architect, not a verdict.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field

FLAG_THRESHOLD = 4


@dataclass
class OrchestrationFlag:
    flagged: bool
    score: int
    reasons: list = field(default_factory=list)
    recommendation: str = ""


_NAME_RX = re.compile(r"(ccbpm|\bbpm\b|integration[ _]?process|^ip_|_ip_)",
                      re.I)

_PERSIST_KINDS = ("DBstorage", "Variables", "WriteVariables", "Persist",
                  "DataStoreOperations")
_ASYNC_KINDS = ("Send",)          # fire-and-forget legs


def kinds_from_bundle(bundle_zip: bytes) -> dict:
    """Step-kind counts from a bundle's first .iflw (graceful: {} on any
    failure)."""
    try:
        from extractor.iflow_parser import parse_iflow
        bz = zipfile.ZipFile(io.BytesIO(bundle_zip))
        iflws = [n for n in bz.namelist() if n.endswith(".iflw")]
        if not iflws:
            return {}
        m = parse_iflow(bz.read(iflws[0]).decode("utf-8", "replace"), "o")
        out: dict = {}
        for s in m.steps.values():
            out[s.kind] = out.get(s.kind, 0) + 1
        out["__processes__"] = len(getattr(m, "processes", []) or [])
        return out
    except Exception:
        return {}


def assess_orchestration(signals: dict | None = None,
                         kinds: dict | None = None,
                         name: str = "") -> OrchestrationFlag:
    signals = signals or {}
    kinds = kinds or {}
    score = 0
    reasons = []

    def k(*names):
        return sum(kinds.get(n, 0) for n in names)

    ca = signals.get("call_activities", 0) or k("ProcessCallElement")
    if ca >= 5:
        score += 2
        reasons.append(f"{ca} process-call steps (heavy sub-process "
                       f"orchestration)")
    elif ca >= 3:
        score += 1
        reasons.append(f"{ca} process-call steps")

    nproc = kinds.get("__processes__", 0)
    if nproc >= 4:
        score += 2
        reasons.append(f"{nproc} BPMN processes in one flow")

    if k("Splitter") and k("Gather", "Join"):
        score += 2
        reasons.append("split/collect pattern (fork-join, ccBPM block "
                       "semantics)")
    if k("Multicast", "SequentialMulticast"):
        score += 1
        reasons.append("multicast fan-out")

    routers = signals.get("routers", 0) or k("ExclusiveGateway")
    if routers >= 2 and (ca >= 2 or nproc >= 3):
        score += 1
        reasons.append(f"{routers} routers across multiple processes "
                       f"(stateful branching)")

    if k(*_PERSIST_KINDS):
        score += 1
        reasons.append("persistence steps (data store/variables — "
                       "stateful processing)")
    if k(*_ASYNC_KINDS) >= 2 or (k(*_ASYNC_KINDS) and k("StartTimerEvent")):
        score += 1
        reasons.append("async send legs (correlation/wait semantics "
                       "likely)")
    if k("StartTimerEvent") and (ca >= 2 or nproc >= 3):
        score += 1
        reasons.append("timer-driven multi-process flow")

    if name and _NAME_RX.search(name):
        score += 3
        reasons.append(f"name suggests a ccBPM/integration-process origin "
                       f"({name!r})")

    flagged = score >= FLAG_THRESHOLD
    rec = ""
    if flagged:
        rec = ("Orchestration-shaped: in a PI/PO landscape this profile "
               "usually traces back to ccBPM/integration-process design. "
               "Plan an architecture decision, not a 1:1 port — typical "
               "outcomes are CPI + SAP Build Process Automation split, or "
               "redesign into stateless CPI patterns (JMS decoupling, "
               "ProcessDirect decomposition). Price the decision into the "
               "estimate.")
    return OrchestrationFlag(flagged=flagged, score=score, reasons=reasons,
                             recommendation=rec)
