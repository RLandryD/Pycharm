"""
extractor/iflow_parser.py

Parse a CPI integration flow (.iflw BPMN) into a normalized model — the
intermediate representation both the CPI and (later) PI/PO front-ends map to.

The model captures what a migration actually needs to preserve:
  - processes:   main Integration Process + Local Integration Processes (grouping)
  - steps:       every flow node with its CPI activityType (Enricher, Script,
                 Mapping, ProcessCallElement, ExternalCall, ExclusiveGateway, …),
                 name, and a light config dict
  - routes:      gateway routes with their condition expressions + the default
  - endpoints:   sender/receiver participants + message flows
  - parameters:  externalized {{param}} names
  - sequence:    main-process step order (topological, following sequence flows)

This is the EXTRACT half of the round-trip. It records whatever it finds, so a
construct we can't yet *generate* is still faithfully *captured* — that's how we
measure how far the generator has to go.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

NS = {"bpmn2": "http://www.omg.org/spec/BPMN/20100524/MODEL",
      "ifl": "http:///com.sap.ifl.model/Ifl.xsd"}

_FLOW_TAGS = {"callActivity", "startEvent", "endEvent", "exclusiveGateway",
              "parallelGateway", "subProcess", "serviceTask", "receiveTask",
              "sendTask"}
_PARAM_RX = re.compile(r"\{\{([A-Za-z0-9_.\-]+)\}\}")


def _ln(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _props(el) -> dict:
    """Direct ifl:property key/value pairs of an element's own extensionElements."""
    out = {}
    ext = el.find("bpmn2:extensionElements", NS)
    if ext is not None:
        for p in ext.findall("ifl:property", NS):
            k = p.findtext("key")
            if k:
                out[k] = p.findtext("value")
    return out


def _activity_type(el) -> str | None:
    """An element's own activityType — from its extensionElements, or one level
    into an event definition (timer/message/error events keep it there). If
    there is no activityType property, derive it from the cmdVariantUri's cname
    (some real steps — e.g. Request-Reply — carry only the cmdVariant), so they
    classify correctly instead of falling back to the bare element tag."""
    own = _props(el)
    if "activityType" in own:
        return own["activityType"]
    for child in el:
        if _ln(child.tag).endswith("EventDefinition"):
            p = _props(child)
            if "activityType" in p:
                return p["activityType"]
    cv = own.get("cmdVariantUri", "")
    if cv:
        m = _CNAME_RX.search(cv)
        if m and m.group(1) in _CNAME_TO_ACTIVITY:
            return _CNAME_TO_ACTIVITY[m.group(1)]
    return None


_CNAME_RX = re.compile(r"cname::([^/]+)")
# cnames whose step has no activityType property but a recognizable variant
_CNAME_TO_ACTIVITY = {"ExternalCall": "ExternalCall"}


_DERIVE = {"startEvent": "StartEvent", "endEvent": "EndEvent",
           "exclusiveGateway": "ExclusiveGateway", "parallelGateway": "ParallelGateway",
           "subProcess": "SubProcess", "serviceTask": "ServiceTask",
           "receiveTask": "ReceiveTask", "sendTask": "SendTask"}


@dataclass
class Step:
    id: str
    kind: str
    name: str
    process_id: str
    config: dict = field(default_factory=dict)
    incoming: list = field(default_factory=list)
    outgoing: list = field(default_factory=list)
    parent_subprocess: str = ""
    #: Looping Process Call characteristics, decoded verbatim from the source
    #: (202/202 corpus instances carry exactly: element id + loopMaximum, and a
    #: loopCondition child with id + xsi:type + XPath text). None when absent.
    #: Dropping this emitted an empty Condition Expression in the editor.
    loop: dict | None = None


@dataclass
class Route:
    flow_id: str
    name: str
    gateway: str
    target: str
    condition: str | None      # None == default route
    expr_type: str = ""


@dataclass
class Endpoint:
    id: str
    direction: str             # sender | receiver
    name: str
    etype: str = ""            # ifl:type verbatim (EndpointSender / EndpointRecevier)


@dataclass
class MessageFlow:
    id: str
    name: str
    source: str                # participant or flow-node id
    target: str
    config: dict = field(default_factory=dict)   # adapter props (ComponentType, urlPath, …)


@dataclass
class Process:
    id: str
    name: str
    is_main: bool
    step_ids: list = field(default_factory=list)


@dataclass
class IFlowModel:
    name: str
    processes: list = field(default_factory=list)
    steps: dict = field(default_factory=dict)      # id -> Step
    routes: list = field(default_factory=list)
    endpoints: list = field(default_factory=list)
    parameters: set = field(default_factory=set)
    sequence: list = field(default_factory=list)   # ordered main-process step ids
    message_flows: list = field(default_factory=list)  # MessageFlow (adapters)
    collab_config: dict = field(default_factory=dict)  # collaboration-level props
                                                       # (namespaceMapping, allowedHeaderList, CORS, …)

    def kinds(self) -> set:
        return {s.kind for s in self.steps.values()}


def _collect_steps(parent, process_id: str, steps: dict, sub: str = ""):
    """Recursively collect flow nodes (recursing into subProcesses)."""
    for el in list(parent):
        ln = _ln(el.tag)
        if ln not in _FLOW_TAGS:
            continue
        kind = _activity_type(el) or _DERIVE.get(ln, ln)
        # error/timer events are often authored WITHOUT an activityType — the
        # nature lives in the event-definition child (real CPI form). Derive it
        # structurally so the kind is faithful either way, and merge the event
        # definition's OWN extensionElements (schedule, variant) into the step
        # config so they survive regeneration (own config wins on conflicts).
        evdef = None
        for defname, start_kind, end_kind in (
                ("bpmn2:errorEventDefinition", "StartErrorEvent", "EndErrorEvent"),
                ("bpmn2:escalationEventDefinition", None,
                 "EscalationEndEvent"),
                ("bpmn2:timerEventDefinition", "StartTimerEvent", None),
                # C4C-era hybrid (decoded 2026-06-11 BigBatch): activityType
                # stays EndEvent while the BPMN child is a terminate def —
                # capture it so the def re-emits; do NOT remap the kind.
                ("bpmn2:terminateEventDefinition", None, None)):
            d = el.find(defname, NS)
            if d is None:
                continue
            evdef = d
            if kind == "StartEvent" and start_kind:
                kind = start_kind
            elif kind == "EndEvent" and end_kind:
                kind = end_kind
            break
        cfg = _props(el)
        node_props = dict(cfg)            # node-level props BEFORE def merge
        if evdef is not None:
            for k, v in _props(evdef).items():
                cfg.setdefault(k, v)
        sid = el.get("id") or f"{ln}_{len(steps)}"
        steps[sid] = Step(
            id=sid, kind=kind, name=el.get("name", "") or "",
            process_id=process_id, config=cfg,
            incoming=[i.text for i in el.findall("bpmn2:incoming", NS)],
            outgoing=[o.text for o in el.findall("bpmn2:outgoing", NS)],
            parent_subprocess=sub)
        # event-definition fidelity: the EDITOR keys checks off the def child
        # (plain LIP starts/ends have NONE; adding messageEventDefinition makes
        # it demand an incoming message flow / reject the end-event variant).
        # Record exactly what the source had so emission mirrors it.
        st = steps[sid]
        # Looping Process Call: the loop condition lives in a BPMN
        # standardLoopCharacteristics CHILD (referenced by the loopId prop),
        # not in the ifl properties — decode it so the emitter can re-emit it
        # with its ORIGINAL ids (empty Condition Expression otherwise).
        slc = el.find("bpmn2:standardLoopCharacteristics", NS)
        if slc is not None:
            cond = slc.find("bpmn2:loopCondition", NS)
            st.loop = {
                "id": slc.get("id", "") or "",
                "loop_maximum": slc.get("loopMaximum", "") or "",
                "cond_id": (cond.get("id", "") or "") if cond is not None else "",
                "cond_type": (cond.get(
                    "{http://www.w3.org/2001/XMLSchema-instance}type", "")
                    or "bpmn2:tFormalExpression") if cond is not None
                    else "bpmn2:tFormalExpression",
                "condition": (cond.text or "") if cond is not None else "",
            }
        if ln in ("startEvent", "endEvent"):
            if evdef is not None:
                _evt = _ln(evdef.tag).lower()
                st.event_def = ("timer" if "timer" in _evt
                                else "escalation" if "escalation" in _evt
                                else "terminate" if "terminate" in _evt
                                else "error")
                st.event_def_id = evdef.get("id", "") or ""
                st.def_props = _props(evdef)
            elif el.find("bpmn2:messageEventDefinition", NS) is not None:
                st.event_def = "message"
            else:
                st.event_def = None
            st.node_props = node_props
        if ln == "subProcess":
            _collect_steps(el, process_id, steps, sub=sid)


def _order_main(model: IFlowModel, main_id: str) -> list:
    """Best-effort topological order of the main process by following flows from
    its start event."""
    main_steps = {sid for sid, s in model.steps.items()
                  if s.process_id == main_id and not s.parent_subprocess}
    # map node -> next nodes via plain (non-gateway) sequence flows + routes
    nxt = {}
    for s in model.steps.values():
        if s.id in main_steps:
            nxt.setdefault(s.id, [])
    starts = [sid for sid in main_steps
              if model.steps[sid].kind in ("StartEvent", "StartTimerEvent",
                                            "MessageStartEvent")
              or _ln_is_start(model.steps[sid])]
    if not starts:
        starts = [sid for sid in main_steps if not model.steps[sid].incoming]
    # build adjacency from outgoing flow ids resolved against the flow map
    order, seen = [], set()
    stack = list(starts) or list(main_steps)
    # resolve outgoing flow-id -> target via the global flow table
    flow_target = model._flow_target  # set during parse
    while stack:
        sid = stack.pop(0)
        if sid in seen or sid not in main_steps:
            continue
        seen.add(sid)
        order.append(sid)
        for fid in model.steps[sid].outgoing:
            tgt = flow_target.get(fid)
            if tgt and tgt not in seen:
                stack.append(tgt)
    # append any unreached main steps so nothing is silently dropped
    for sid in main_steps:
        if sid not in seen:
            order.append(sid)
    return order


def _ln_is_start(step: Step) -> bool:
    return step.kind.endswith("StartEvent") or step.kind == "StartTimerEvent"


def parse_iflow(xml: str, name: str = "") -> IFlowModel:
    root = ET.fromstring(xml)
    model = IFlowModel(name=name or "")

    # processes (main vs Local Integration Process): every process may have an
    # IntegrationProcess participant (real CPI multi-process form), so the main
    # is the one NOT invoked by any ProcessCall's processId reference.
    called = set()
    for prop in root.iter("{%s}property" % NS["ifl"]):
        if prop.findtext("key") == "processId" and prop.findtext("value"):
            called.add(prop.findtext("value"))
    ip_refs = [part.get("processRef")
               for part in root.iter("{%s}participant" % NS["bpmn2"])
               if part.get("{%s}type" % NS["ifl"]) == "IntegrationProcess"
               and part.get("processRef")]
    main_ref = next((r for r in ip_refs if r not in called),
                    ip_refs[-1] if ip_refs else None)
    procs = list(root.iter("{%s}process" % NS["bpmn2"]))
    for p in procs:
        pid = p.get("id")
        is_main = (pid == main_ref) if main_ref else (p is procs[0])
        model.processes.append(Process(id=pid, name=p.get("name", "") or "",
                                       is_main=is_main))
        _collect_steps(p, pid, model.steps)

    # flow table (id -> (source, target)) + routes
    model._flow_target = {}
    gateways = {s.id for s in model.steps.values() if s.kind == "ExclusiveGateway"}
    for sf in root.iter("{%s}sequenceFlow" % NS["bpmn2"]):
        fid, src, tgt = sf.get("id"), sf.get("sourceRef"), sf.get("targetRef")
        model._flow_target[fid] = tgt
        fp = _props(sf)
        if fp:
            if not hasattr(model, "flow_props"):
                model.flow_props = {}
            model.flow_props[fid] = list(fp.items())
        if src in gateways:
            cond_el = sf.find("bpmn2:conditionExpression", NS)
            model.routes.append(Route(
                flow_id=fid, name=sf.get("name", "") or "", gateway=src,
                target=tgt, condition=(cond_el.text if cond_el is not None else None),
                expr_type=_props(sf).get("expressionType", "")))

    # link step ids to their process
    for pr in model.processes:
        pr.step_ids = [s.id for s in model.steps.values()
                       if s.process_id == pr.id and not s.parent_subprocess]

    # endpoints (participants) + parameters
    collab = root.find("bpmn2:collaboration", NS)
    if collab is not None:
        model.collab_config = _props(collab)
    for part in root.iter("{%s}participant" % NS["bpmn2"]):
        t = part.get("{%s}type" % NS["ifl"]) or ""
        if "Sender" in t:
            model.endpoints.append(Endpoint(part.get("id"), "sender",
                                            part.get("name", "") or "", t))
        elif "Recevier" in t or "Receiver" in t:
            model.endpoints.append(Endpoint(part.get("id"), "receiver",
                                            part.get("name", "") or "", t))
    model.parameters = set(_PARAM_RX.findall(xml))

    # capture every message flow with its adapter config, so endpoint-only
    # passthroughs (sender→start, end→receiver) can be reproduced with their
    # real adapter rather than dropped.
    for mf in root.iter("{%s}messageFlow" % NS["bpmn2"]):
        model.message_flows.append(MessageFlow(
            id=mf.get("id"), name=mf.get("name", "") or "",
            source=mf.get("sourceRef"), target=mf.get("targetRef"),
            config=_props(mf)))

    # link each ExternalCall (request-reply) step to its receiver message flow,
    # capturing the receiver name + address + adapter into the step config so the
    # generator can reproduce the real call, not a bare placeholder.
    part_name = {part.get("id"): (part.get("name", "") or "")
                 for part in root.iter("{%s}participant" % NS["bpmn2"])}
    mf_by_source = {}
    for mf in root.iter("{%s}messageFlow" % NS["bpmn2"]):
        mf_by_source[mf.get("sourceRef")] = (mf.get("targetRef"), _props(mf),
                                             mf.get("name", "") or "")
    # A bare serviceTask that sends a message flow to a receiver participant is a
    # Request-Reply (ExternalCall) authored without activityType/cmdVariant.
    # Reclassify it structurally (by its receiver wiring, not its name) so it
    # regenerates as a real ExternalCall with the endpoint on the right step.
    receiver_ids = {part.get("id") for part in root.iter("{%s}participant" % NS["bpmn2"])
                    if "Recevier" in (part.get("{%s}type" % NS["ifl"]) or "")
                    or "Receiver" in (part.get("{%s}type" % NS["ifl"]) or "")}
    for s in model.steps.values():
        if s.kind == "ServiceTask" and s.id in mf_by_source \
                and mf_by_source[s.id][0] in receiver_ids:
            s.kind = "ExternalCall"
    for s in model.steps.values():
        if s.kind == "ExternalCall" and s.id in mf_by_source:
            tgt, props, mfname = mf_by_source[s.id]
            s.config["receiver_name"] = part_name.get(tgt, "Receiver")
            s.config["adapter"] = props.get("ComponentType", "")
            s.config["address"] = (props.get("httpAddressWithoutQuery")
                                    or props.get("address", "") or "")
            # Full message-flow property set + name, so the generator can
            # reproduce the REAL receiver adapter (SuccessFactors, SOAP, SFTP, …)
            # verbatim instead of defaulting every receiver to HTTP.
            s.config["mf_props"] = props
            s.config["mf_name"] = mfname or props.get("Name", "") or "Receiver"

    # main-process sequence
    main = next((p for p in model.processes if p.is_main), None)
    model.sequence = _order_main(model, main.id) if main else []
    return model


def extract_endpoints(xml: str) -> dict:
    """Pull the real sender/receiver systems + adapter types from an iFlow's
    participants and message flows, so an uploaded package's interfaces show
    their true endpoints instead of defaulting to blank / HTTPS.

    Returns {sender_system, sender_adapter, receiver_system, receiver_adapter,
    all_receivers}. A timer-triggered flow with no sender legitimately returns
    empty sender fields (the UI then shows 'None', which is correct)."""
    try:
        root = ET.fromstring(xml)
    except Exception:
        return {}
    parts = {pt.get("id"): (pt.get("{%s}type" % NS["ifl"]) or "",
                            pt.get("name", "") or "")
             for pt in root.iter("{%s}participant" % NS["bpmn2"])}
    sender_ids = {pid for pid, (t, _) in parts.items() if "Sender" in t}
    receiver_ids = {pid for pid, (t, _) in parts.items()
                    if "Recevier" in t or "Receiver" in t}
    starts = {e.get("id") for e in root.iter() if _ln(e.tag) == "startEvent"}
    sender_adapter = receiver_adapter = ""
    receiver_names: list[str] = []
    for mf in root.iter("{%s}messageFlow" % NS["bpmn2"]):
        src, tgt = mf.get("sourceRef"), mf.get("targetRef")
        ct = _props(mf).get("ComponentType", "")
        if (src in sender_ids or tgt in starts) and not sender_adapter:
            sender_adapter = ct
        if tgt in receiver_ids:
            if not receiver_adapter:
                receiver_adapter = ct
            nm = parts[tgt][1]
            if nm and nm not in receiver_names:
                receiver_names.append(nm)
    sender_system = next((parts[i][1] for i in sender_ids if parts[i][1]), "")
    return {
        "sender_system": sender_system,
        "sender_adapter": sender_adapter,
        "receiver_system": receiver_names[0] if receiver_names else "",
        "receiver_adapter": receiver_adapter,
        "all_receivers": receiver_names,
    }
