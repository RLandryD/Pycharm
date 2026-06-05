"""library_builder/iflw_capabilities.py

Integration-flow (.iflw) capability extractor — the CAPSTONE type. An iFlow is
SAP's BPMN-based integration definition; its capability is its ANATOMY, and the
reuse model is CLONE-AND-ADAPT: find an iFlow whose shape matches the need, then
swap the environment-specific config.

The anatomy (extracted from the real BPMN/ifl XML):
  * ADAPTERS   — sender + receiver connectivity, from <messageFlow> participants'
                 ComponentType (HTTP / SOAP / SFTP / IDOC / Mail / JMS / OData /
                 ProcessDirect / SuccessFactors / RFC ...). Direction inferred
                 from source/target (Participant->Process = sender; Process->
                 Participant = receiver).
  * STEPS      — the ordered processing activities, from each flow step's
                 activityType (Enricher / Script / Mapping / Gateway / Splitter /
                 Filter / Converter / DBstorage / ExternalCall / Send ...). This
                 sequence is WHAT THE IFLOW DOES.
  * CONFIG     — the adapter + step properties, incl. {{externalized}} params and
                 ${expressions} — the what-VARIES surface for adaptation.

Identity = sender-adapters + step-type sequence + receiver-adapters (the shape).
Five facets mirror the other engines but at flow granularity:
  purpose (from the step composition) | needs (sender + inputs) |
  what-varies (adapter/step config, externalized params) | shape (the anatomy) |
  when-to-use (the integration pattern).

Grounded in 164 real specimens. Pure structure, no SAP runtime, no tenant —
sandbox-testable. (Deploying/running a cloned iFlow is the tenant step.)
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field as _field


# known adapter component types (the connectivity vocabulary, corpus-derived)
_KNOWN_ADAPTERS = {
    "HTTP", "HTTPS", "SOAP", "SFTP", "FTP", "IDOC", "Mail", "JMS", "RFC",
    "HCIOData", "OData", "ProcessDirect", "SuccessFactors", "AMQP", "Kafka",
    "AS2", "OpenConnectors", "JDBC", "XI", "PollingSFTP", "DataStoreConsumer",
    "Ariba", "ODataReceiver", "ELSTER", "SuccessFactorsOData",
}

# step activity types worth surfacing as the iFlow's "operations" vocabulary
_STEP_LABELS = {
    "Enricher": "enrich", "Script": "script", "Mapping": "map",
    "ExclusiveGateway": "route", "Splitter": "split", "Filter": "filter",
    "JsonToXmlConverter": "json-to-xml", "XmlToJsonConverter": "xml-to-json",
    "Encoder": "encode", "Decoder": "decode", "DBstorage": "datastore",
    "ExternalCall": "external-call", "ProcessCallElement": "process-call",
    "Send": "send", "Variables": "variables", "ContentModifier": "modify",
    "RequestReply": "request-reply", "Aggregator": "aggregate",
    "StartTimerEvent": "timer", "MessageDigest": "digest",
}


@dataclass
class IflwCapability:
    name: str
    sender_adapters: list = _field(default_factory=list)
    receiver_adapters: list = _field(default_factory=list)
    step_types: list = _field(default_factory=list)        # ordered-ish
    step_counts: dict = _field(default_factory=dict)
    externalized_params: list = _field(default_factory=list)  # {{...}} config
    step_count: int = 0
    trigger: str = "adapter"        # adapter | timer/scheduled | sub-process/local
    # facets
    purpose: str = ""
    needs: list = _field(default_factory=list)
    what_varies: list = _field(default_factory=list)
    shape: str = ""
    when_to_use: str = ""
    op_keywords: list = _field(default_factory=list)
    weight: int = 0

    def signature(self) -> str:
        s = "+".join(self.sender_adapters[:2]) or "?"
        r = "+".join(self.receiver_adapters[:2]) or "?"
        return f"iflw:{s}->[{len(self.step_types)}steps]->{r}"


def _adapters_with_direction(text: str):
    """Return (senders, receivers). A messageFlow whose sourceRef is a
    Participant feeds INTO the process (sender); whose targetRef is a
    Participant goes OUT (receiver). Falls back to undirected if refs unclear."""
    senders, receivers, undirected = [], [], []
    for mf in re.findall(r"<bpmn2?:messageFlow\b[^>]*>.*?</bpmn2?:messageFlow>",
                         text, re.S):
        comp = re.search(r"<key>ComponentType</key>\s*<value>([^<]+)</value>", mf)
        if not comp:
            continue
        adapter = comp.group(1).strip()
        src = re.search(r'sourceRef="([^"]+)"', mf)
        tgt = re.search(r'targetRef="([^"]+)"', mf)
        src_id = src.group(1) if src else ""
        tgt_id = tgt.group(1) if tgt else ""
        # participants are referenced; sender = participant is the SOURCE
        if "Participant" in src_id:
            senders.append(adapter)
        elif "Participant" in tgt_id:
            receivers.append(adapter)
        else:
            undirected.append(adapter)
    # if direction couldn't be resolved, list undirected under both-unknown
    if not senders and not receivers and undirected:
        senders = undirected
    return (sorted(set(senders)), sorted(set(receivers)))


def extract_capability(name: str, text: str) -> IflwCapability:
    cap = IflwCapability(name=name)
    cap.sender_adapters, cap.receiver_adapters = _adapters_with_direction(text)

    # step types (the processing anatomy), in document order
    steps = re.findall(r"<key>activityType</key>\s*<value>([^<]+)</value>", text)
    steps = [s.strip() for s in steps]
    cap.step_types = steps
    cap.step_counts = dict(Counter(steps))
    cap.step_count = len(steps)

    # trigger: how the flow starts (so a sender-less flow reads honestly as
    # "scheduled" rather than a misleading "?"). Rigor-audit refinement.
    if "StartTimerEvent" in steps:
        cap.trigger = "timer/scheduled"
    elif cap.sender_adapters:
        cap.trigger = "adapter"
    else:
        cap.trigger = "sub-process/local"

    # externalized config params {{...}} (the adapt surface) + a few ${expr}
    cap.externalized_params = sorted(set(
        re.findall(r"\{\{([^}]+)\}\}", text)))[:40]

    # five facets ---------------------------------------------------------
    labels = [_STEP_LABELS.get(s) for s in steps if _STEP_LABELS.get(s)]
    label_set = sorted(set(labels))
    # purpose from sender -> verbs -> receiver (or trigger if no sender adapter)
    s = "/".join(cap.sender_adapters) or (
        "timer" if cap.trigger == "timer/scheduled" else "sub-process")
    r = "/".join(cap.receiver_adapters) or "?"
    verbs = ", ".join(label_set[:5]) if label_set else "pass-through"
    cap.purpose = f"{s} → {verbs} → {r}"
    cap.needs = [f"sender:{a}" for a in cap.sender_adapters] \
        + [f"step:{l}" for l in label_set[:6]]
    cap.what_varies = cap.externalized_params + \
        [f"receiver:{a}" for a in cap.receiver_adapters]
    # shape = the anatomy
    top_steps = ", ".join(f"{k}×{v}" for k, v in
                          Counter(steps).most_common(6))
    cap.shape = (f"{s} → {r}; {cap.step_count} steps"
                 + (f" [{top_steps}]" if top_steps else ""))
    # when-to-use: the integration pattern
    if cap.sender_adapters and cap.receiver_adapters:
        cap.when_to_use = (f"reuse for {s}-to-{r} integrations"
                           + (f" needing {', '.join(label_set[:3])}"
                              if label_set else ""))
    else:
        cap.when_to_use = "reuse this flow pattern (clone + adapt config)"
    # discriminating keywords: adapters + step labels + a few param names
    cap.op_keywords = sorted(set(
        cap.sender_adapters + cap.receiver_adapters + label_set
        + [p.split()[0] for p in cap.externalized_params[:10]]))
    # weight: complexity (SAP-aligned: receivers + steps + gateways drive effort)
    gateways = cap.step_counts.get("ExclusiveGateway", 0)
    cap.weight = (cap.step_count
                  + len(cap.receiver_adapters) * 3
                  + gateways * 2
                  + cap.step_counts.get("Mapping", 0) * 2)
    return cap


def build_catalog(corpus: dict) -> dict:
    caps = [extract_capability(n, t) for n, t in corpus.items()]
    # adapter & step vocabularies discovered across the corpus
    adapter_vocab, step_vocab = Counter(), Counter()
    for c in caps:
        for a in c.sender_adapters + c.receiver_adapters:
            adapter_vocab[a] += 1
        for s in c.step_types:
            step_vocab[s] += 1
    index = {}
    for c in caps:
        index.setdefault(c.signature(), []).append(c.name)
    return {
        "capabilities": caps,
        "count": len(caps),
        "adapter_vocabulary": dict(adapter_vocab),
        "step_vocabulary": dict(step_vocab),
        "index": index,
    }
