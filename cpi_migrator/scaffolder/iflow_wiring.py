"""
scaffolder/iflow_wiring.py

iFlow WIRING engine (Phase 1). Generates a configured, importable iFlow with
real steps, adapters, externalized parameters, and a standard exception
subprocess — derived from the structural grammar of 97 real SAP iFlows
(see reference/IFLOW_STRUCTURE_REFERENCE.md).

This replaces the old skeleton (Start→End, zero steps) with a wired flow:
  Sender adapter → Content Modifier → [Mapping] → [Script] → Receiver call → End
plus a standard exception subprocess folded in by default.

Honest scope (Phase 1):
  - Produces correct STRUCTURE: valid BPMN envelope, real callActivity steps,
    sender/receiver message flows, externalized params, exception subprocess.
  - Step CONTENTS are wired but generic: the mapping references an artifact
    (logic filled by the mapping generator / human), scripts are generic
    capability templates, CM sets sensible headers/properties.
  - This is "review-ready structure," not "final business logic."

The generator builds the iFlow as BPMN2 XML matching the real envelope:
1 collaboration, 3 participants (sender/process/receiver), message flows for
adapters, 1 process with the step chain, + ErrorEventSubProcessTemplate.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Optional


# ── Flow variant detection ───────────────────────────────────────────────────
VARIANT_LINEAR    = "linear"
VARIANT_ROUTER    = "router"
VARIANT_SPLITTER  = "splitter"
VARIANT_SCHEDULED = "scheduled"


@dataclass
class WiringStep:
    """One step to place in the main process flow."""
    step_id: str
    name: str
    activity_type: str           # Enricher | Script | Mapping | ExternalCall | ExclusiveGateway
    component_version: str = "1.1"
    extra_props: dict = field(default_factory=dict)
    # for Script: script_ref; for Mapping: mapping_ref
    artifact_ref: str = ""


@dataclass
class WiredIFlow:
    iflow_id: str
    name: str
    xml: str
    variant: str
    steps: list[str]             # step names in order
    parameters: dict             # externalized {{param}} -> value
    has_exception_handler: bool
    referenced_artifacts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]", "_", str(text or "")).strip("_")
    return s or "iflow"


def detect_variant(interface) -> str:
    """Pick the flow variant from interface characteristics."""
    if getattr(interface, "has_multi_mapping", False):
        return VARIANT_SPLITTER
    desc = (getattr(interface, "description", "") or "").lower()
    if getattr(interface, "has_bpm", False) or "rout" in desc or "condition" in desc:
        return VARIANT_ROUTER
    if "schedul" in desc or "poll" in desc or "timer" in desc:
        return VARIANT_SCHEDULED
    return VARIANT_LINEAR


# ── BPMN fragment builders ───────────────────────────────────────────────────

def _ifl_prop(key: str, value: str) -> str:
    return (f"                        <ifl:property>\n"
            f"                            <key>{html.escape(key)}</key>\n"
            f"                            <value>{value}</value>\n"
            f"                        </ifl:property>\n")


def _call_activity(step: WiringStep, incoming: str, outgoing: str) -> str:
    props = ""
    props += _ifl_prop("componentVersion", step.component_version)
    props += _ifl_prop("activityType", step.activity_type)
    for k, v in step.extra_props.items():
        props += _ifl_prop(k, html.escape(str(v), quote=True))
    return (
        f'            <bpmn2:callActivity id="{step.step_id}" name="{html.escape(step.name, quote=True)}">\n'
        f'                <bpmn2:extensionElements>\n{props}                </bpmn2:extensionElements>\n'
        f'                <bpmn2:incoming>{incoming}</bpmn2:incoming>\n'
        f'                <bpmn2:outgoing>{outgoing}</bpmn2:outgoing>\n'
        f'            </bpmn2:callActivity>\n'
    )


def _exception_subprocess() -> str:
    """The standard exception subprocess, folded into every iFlow.

    Mirrors the real pattern: StartErrorEvent → capture message (Enricher) →
    prepare error response (Enricher) → EndEvent.
    """
    return (
'            <bpmn2:subProcess id="SubProcess_Error" name="Exception Subprocess">\n'
'                <bpmn2:extensionElements>\n'
'                    <ifl:property><key>activityType</key><value>ErrorEventSubProcessTemplate</value></ifl:property>\n'
'                </bpmn2:extensionElements>\n'
'                <bpmn2:startEvent id="ErrorStart_1" name="Error Start">\n'
'                    <bpmn2:extensionElements>\n'
'                        <ifl:property><key>activityType</key><value>StartErrorEvent</value></ifl:property>\n'
'                    </bpmn2:extensionElements>\n'
'                    <bpmn2:outgoing>SeqErr_1</bpmn2:outgoing>\n'
'                    <bpmn2:errorEventDefinition id="ErrorDef_1"/>\n'
'                </bpmn2:startEvent>\n'
'                <bpmn2:callActivity id="ErrGetMsg" name="Get Exception Message">\n'
'                    <bpmn2:extensionElements>\n'
'                        <ifl:property><key>componentVersion</key><value>1.6</value></ifl:property>\n'
'                        <ifl:property><key>activityType</key><value>Enricher</value></ifl:property>\n'
'                        <ifl:property><key>bodyType</key><value>expression</value></ifl:property>\n'
'                        <ifl:property><key>propertyTable</key><value>'
'&lt;root&gt;&lt;row&gt;&lt;cell id=\'Action\'&gt;Create&lt;/cell&gt;'
'&lt;cell id=\'Type\'&gt;expression&lt;/cell&gt;'
'&lt;cell id=\'Value\'&gt;${exception.message}&lt;/cell&gt;'
'&lt;cell id=\'Default\'&gt;&lt;/cell&gt;'
'&lt;cell id=\'Name\'&gt;ErrorMessage&lt;/cell&gt;'
'&lt;cell id=\'Datatype\'&gt;&lt;/cell&gt;&lt;/row&gt;&lt;/root&gt;</value></ifl:property>\n'
'                    </bpmn2:extensionElements>\n'
'                    <bpmn2:incoming>SeqErr_1</bpmn2:incoming>\n'
'                    <bpmn2:outgoing>SeqErr_2</bpmn2:outgoing>\n'
'                </bpmn2:callActivity>\n'
'                <bpmn2:callActivity id="ErrPrepare" name="Prepare Error Response">\n'
'                    <bpmn2:extensionElements>\n'
'                        <ifl:property><key>componentVersion</key><value>1.6</value></ifl:property>\n'
'                        <ifl:property><key>activityType</key><value>Enricher</value></ifl:property>\n'
'                    </bpmn2:extensionElements>\n'
'                    <bpmn2:incoming>SeqErr_2</bpmn2:incoming>\n'
'                    <bpmn2:outgoing>SeqErr_3</bpmn2:outgoing>\n'
'                </bpmn2:callActivity>\n'
'                <bpmn2:endEvent id="ErrEnd_1" name="Error End">\n'
'                    <bpmn2:extensionElements>\n'
'                        <ifl:property><key>activityType</key><value>EndErrorEvent</value></ifl:property>\n'
'                    </bpmn2:extensionElements>\n'
'                    <bpmn2:incoming>SeqErr_3</bpmn2:incoming>\n'
'                </bpmn2:endEvent>\n'
'                <bpmn2:sequenceFlow id="SeqErr_1" sourceRef="ErrorStart_1" targetRef="ErrGetMsg"/>\n'
'                <bpmn2:sequenceFlow id="SeqErr_2" sourceRef="ErrGetMsg" targetRef="ErrPrepare"/>\n'
'                <bpmn2:sequenceFlow id="SeqErr_3" sourceRef="ErrPrepare" targetRef="ErrEnd_1"/>\n'
'            </bpmn2:subProcess>\n'
    )


def build_steps(interface, variant: str, configs=None) -> list[WiringStep]:
    """Build the ordered step list for the main flow, based on variant + what
    the interface needs."""
    steps: list[WiringStep] = []
    sender_adapter = getattr(interface, "sender_adapter", "") or "HTTPS"

    # 1. Content Modifier — set correlation headers/properties (always)
    steps.append(WiringStep(
        "CM_SetContext", "Set Context", "Enricher", "1.6",
        extra_props={"bodyType": "constant"}))

    # 2. Splitter (only for multi-mapping/splitter variant)
    if variant == VARIANT_SPLITTER:
        steps.append(WiringStep("Splitter_1", "General Splitter", "Splitter", "1.6"))

    # 3. Mapping — references a mapping artifact (logic filled later)
    map_ref = f"{_slug(getattr(interface,'name',''))}_mapping"
    steps.append(WiringStep(
        "Mapping_1", "Message Mapping", "Mapping", "1.1",
        artifact_ref=map_ref,
        extra_props={"mappingname": map_ref, "mappingType": "MessageMapping"}))

    # 4. Router (only for router variant)
    if variant == VARIANT_ROUTER:
        steps.append(WiringStep("Router_1", "Router", "ExclusiveGateway", "1.0"))

    # 5. Script — generic helper (logging / processing)
    script_ref = f"{_slug(getattr(interface,'name',''))}_process.groovy"
    steps.append(WiringStep(
        "Script_1", "Process Script", "Script", "1.1",
        artifact_ref=script_ref,
        extra_props={"scriptFunction": "processData", "script": script_ref}))

    # 6. Receiver call — request-reply to the receiver system
    steps.append(WiringStep("ReceiverCall_1", "Send to Receiver", "ExternalCall", "1.1"))

    return steps


def wire_iflow(interface, configs=None, parameters: Optional[dict] = None,
               include_exception_handler: bool = True) -> WiredIFlow:
    """Generate a fully wired iFlow for an interface."""
    iflow_id = _slug(getattr(interface, "name", "") or "iflow")
    name = getattr(interface, "name", iflow_id)
    variant = detect_variant(interface)
    steps = build_steps(interface, variant, configs)

    sender_sys   = getattr(interface, "sender_system", "") or "Sender"
    receiver_sys = getattr(interface, "receiver_system", "") or "Receiver"
    sender_adapter   = getattr(interface, "sender_adapter", "") or "HTTPS"
    receiver_adapter = getattr(interface, "receiver_adapter", "") or "HTTPS"

    # Externalized parameters (decoded format: {{name}} in flow, value in .prop)
    params = dict(parameters or {})
    params.setdefault("sender_endpoint", f"/{iflow_id.lower()}")
    params.setdefault("receiver_address", "https://CHANGE_ME/endpoint")
    params.setdefault("receiver_credential", "RECEIVER_CRED")

    # ── Build the main process flow with sequence flows chaining steps ──
    start_id = "StartEvent_1"
    end_id   = "EndEvent_1"
    seq_flows = []
    step_xml = ""
    prev_out = "Seq_start"
    # start event
    flow_steps_xml = (
        f'            <bpmn2:startEvent id="{start_id}" name="Start">\n'
        f'                <bpmn2:extensionElements>\n'
        f'                    <ifl:property><key>activityType</key><value>StartEvent</value></ifl:property>\n'
        f'                </bpmn2:extensionElements>\n'
        f'                <bpmn2:outgoing>Seq_0</bpmn2:outgoing>\n'
        f'            </bpmn2:startEvent>\n'
    )
    n = len(steps)
    for i, step in enumerate(steps):
        incoming = f"Seq_{i}"
        outgoing = f"Seq_{i+1}"
        flow_steps_xml += _call_activity(step, incoming, outgoing)
        seq_flows.append((incoming, (start_id if i == 0 else steps[i-1].step_id), step.step_id))
    # end event
    flow_steps_xml += (
        f'            <bpmn2:endEvent id="{end_id}" name="End">\n'
        f'                <bpmn2:extensionElements>\n'
        f'                    <ifl:property><key>activityType</key><value>EndEvent</value></ifl:property>\n'
        f'                </bpmn2:extensionElements>\n'
        f'                <bpmn2:incoming>Seq_{n}</bpmn2:incoming>\n'
        f'            </bpmn2:endEvent>\n'
    )
    # sequence flows
    sf_xml = f'            <bpmn2:sequenceFlow id="Seq_0" sourceRef="{start_id}" targetRef="{steps[0].step_id}"/>\n'
    for i in range(len(steps)):
        src = steps[i].step_id
        tgt = steps[i+1].step_id if i+1 < len(steps) else end_id
        sf_xml += f'            <bpmn2:sequenceFlow id="Seq_{i+1}" sourceRef="{src}" targetRef="{tgt}"/>\n'

    exception_xml = _exception_subprocess() if include_exception_handler else ""

    # ── Message flows (adapters) ──
    msgflow_xml = (
        f'        <bpmn2:messageFlow id="MsgFlow_Sender" name="{html.escape(sender_adapter)}" '
        f'sourceRef="Participant_Sender" targetRef="{start_id}">\n'
        f'            <bpmn2:extensionElements>\n'
        f'                <ifl:property><key>ComponentType</key><value>{html.escape(sender_adapter)}</value></ifl:property>\n'
        f'                <ifl:property><key>direction</key><value>Sender</value></ifl:property>\n'
        f'                <ifl:property><key>address</key><value>{{{{sender_endpoint}}}}</value></ifl:property>\n'
        f'            </bpmn2:extensionElements>\n'
        f'        </bpmn2:messageFlow>\n'
        f'        <bpmn2:messageFlow id="MsgFlow_Receiver" name="{html.escape(receiver_adapter)}" '
        f'sourceRef="ReceiverCall_1" targetRef="Participant_Receiver">\n'
        f'            <bpmn2:extensionElements>\n'
        f'                <ifl:property><key>ComponentType</key><value>{html.escape(receiver_adapter)}</value></ifl:property>\n'
        f'                <ifl:property><key>direction</key><value>Receiver</value></ifl:property>\n'
        f'                <ifl:property><key>address</key><value>{{{{receiver_address}}}}</value></ifl:property>\n'
        f'                <ifl:property><key>credentialName</key><value>{{{{receiver_credential}}}}</value></ifl:property>\n'
        f'            </bpmn2:extensionElements>\n'
        f'        </bpmn2:messageFlow>\n'
    )

    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<bpmn2:definitions xmlns:bpmn2="http://www.omg.org/spec/BPMN/20100524/MODEL"
    xmlns:ifl="http:///com.sap.ifl.model/Ifl.xsd"
    xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
    id="Definitions_{iflow_id}" targetNamespace="http://sap.com/cpi/{iflow_id}">
    <bpmn2:collaboration id="Collaboration_1" name="{html.escape(name, quote=True)}">
        <bpmn2:extensionElements>
            <ifl:property><key>namespaceMapping</key><value></value></ifl:property>
            <ifl:property><key>variant</key><value>{variant}</value></ifl:property>
        </bpmn2:extensionElements>
        <bpmn2:participant id="Participant_Sender" ifl:type="EndpointSender" name="{html.escape(sender_sys, quote=True)}"/>
        <bpmn2:participant id="Participant_Process" ifl:type="IntegrationProcess" name="Integration Process" processRef="Process_1"/>
        <bpmn2:participant id="Participant_Receiver" ifl:type="EndpointReceiver" name="{html.escape(receiver_sys, quote=True)}"/>
{msgflow_xml}    </bpmn2:collaboration>
    <bpmn2:process id="Process_1" name="Integration Process">
{flow_steps_xml}{sf_xml}{exception_xml}    </bpmn2:process>
</bpmn2:definitions>
'''

    return WiredIFlow(
        iflow_id=iflow_id, name=name, xml=xml, variant=variant,
        steps=[s.name for s in steps],
        parameters=params,
        has_exception_handler=include_exception_handler,
        referenced_artifacts=[s.artifact_ref for s in steps if s.artifact_ref],
        notes=[
            f"Variant: {variant}",
            "Structure is review-ready; mapping logic + script bodies are "
            "generic placeholders to be completed.",
        ],
    )


def parameters_prop(params: dict) -> str:
    """Render externalized parameters as a .prop file (name=value, escaped)."""
    lines = []
    for k, v in params.items():
        val = str(v).replace(":", "\\:")
        lines.append(f"{k}={val}")
    return "\n".join(lines) + "\n"
