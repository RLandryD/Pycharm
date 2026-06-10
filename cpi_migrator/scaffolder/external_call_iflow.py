"""scaffolder/external_call_iflow.py

The ExternalCall (request-reply) construct — the single biggest coverage unlock
(+23 iFlows on its own). Decoded from the real corpus, not guessed:

  * the step is a <bpmn2:serviceTask> named "Request-Reply" with
    activityType=ExternalCall, cmdVariant ctype::FlowstepVariant/cname::ExternalCall
  * it is wired to a RECEIVER participant (ifl:type="EndpointRecevier" — SAP's
    own misspelling, reproduced exactly so round-trip parsing matches)
  * via a <bpmn2:messageFlow> carrying the receiver adapter config. HTTP is the
    most common adapter in the corpus (109/247), so it's the first one wired.

This is a standalone builder (like router_iflow) that proves the construct in
isolation: timer → Request-Reply(→HTTP receiver) → end, validated to re-parse as
an ExternalCall step with a receiver endpoint. Composing it among arbitrary other
steps needs the generalized envelope (receiver participants + message flows in the
linear builder) — that's the integration step that lifts measured coverage.
"""
from __future__ import annotations

import html

from scaffolder.minimal_iflow import (
    _sanitize_id, _timer_start_event, build_manifest, build_project,
    MinimalIFlowResult)

_HTTP_CMD = ("ctype::AdapterVariant/cname::sap:HTTP/tp::HTTP/mp::None/"
             "direction::Receiver/version::1.15.0")


def _prop(k: str, v: str = "") -> str:
    return f"<ifl:property><key>{k}</key><value>{html.escape(v)}</value></ifl:property>"


def _message_flow_from_props(mf_id: str, src: str, tgt: str, props: dict,
                             mf_name: str = "") -> str:
    """Re-emit a receiver message flow from the captured original property set,
    preserving the real adapter (ComponentType + cmdVariantUri + all config).
    Falls back to nothing when no props were captured (caller uses the HTTP
    default instead)."""
    if not props:
        return ""
    name = mf_name or props.get("Name", "") or props.get("ComponentType", "") or "Receiver"
    body = "".join(_prop(k, "" if v is None else str(v)) for k, v in props.items())
    return (f'        <bpmn2:messageFlow id="{mf_id}" name="{html.escape(name)}" '
            f'sourceRef="{src}" targetRef="{tgt}">\n'
            f'            <bpmn2:extensionElements>{body}</bpmn2:extensionElements>\n'
            f'        </bpmn2:messageFlow>\n')


def _http_message_flow(mf_id: str, src: str, tgt: str, address: str,
                       method: str = "POST") -> str:
    props = "".join([
        _prop("Name", "HTTP"),
        _prop("ComponentType", "HTTP"),
        _prop("TransportProtocol", "HTTP"),
        _prop("TransportProtocolVersion", "1.15.0"),
        _prop("MessageProtocol", "None"),
        _prop("MessageProtocolVersion", "1.15.0"),
        _prop("direction", "Receiver"),
        _prop("httpMethod", method),
        _prop("httpAddressWithoutQuery", address),
        _prop("authenticationMethod", "None"),
        _prop("componentVersion", "1.15"),
        _prop("cmdVariantUri", _HTTP_CMD),
    ])
    return (f'        <bpmn2:messageFlow id="{mf_id}" name="HTTP" '
            f'sourceRef="{src}" targetRef="{tgt}">\n'
            f'            <bpmn2:extensionElements>{props}</bpmn2:extensionElements>\n'
            f'        </bpmn2:messageFlow>\n')


def _external_call_task(task_id: str, name: str, incoming: str, outgoing: str) -> str:
    return (
        f'        <bpmn2:serviceTask id="{task_id}" name="{html.escape(name)}">\n'
        f'            <bpmn2:extensionElements>\n'
        f'                {_prop("activityType", "ExternalCall")}\n'
        f'                {_prop("cmdVariantUri", "ctype::FlowstepVariant/cname::ExternalCall")}\n'
        f'            </bpmn2:extensionElements>\n'
        f'            <bpmn2:incoming>{incoming}</bpmn2:incoming>\n'
        f'            <bpmn2:outgoing>{outgoing}</bpmn2:outgoing>\n'
        f'        </bpmn2:serviceTask>\n')


def generate_external_call_iflow(name: str, address: str = "https://example.com/api",
                                 http_method: str = "POST",
                                 receiver_name: str = "Receiver",
                                 iflow_id: str = "") -> MinimalIFlowResult:
    """timer → Request-Reply (HTTP receiver) → end, built clean."""
    iflow_id = _sanitize_id(iflow_id or name)
    ST, RECV, MF = "ServiceTask_1", "Participant_2", "MessageFlow_1"
    f1, f2 = "SequenceFlow_3", "SequenceFlow_4"

    timer = _timer_start_event(outgoing=f1)
    task = _external_call_task(ST, "Request-Reply", incoming=f1, outgoing=f2)
    msg_flow = _http_message_flow(MF, ST, RECV, address, http_method)

    seq = (f'        <bpmn2:sequenceFlow id="{f1}" sourceRef="StartEvent_2" targetRef="{ST}"/>\n'
           f'        <bpmn2:sequenceFlow id="{f2}" sourceRef="{ST}" targetRef="EndEvent_2"/>\n')

    di = (
        '            <bpmndi:BPMNShape bpmnElement="Participant_Process_1" '
        'id="BPMNShape_Participant_Process_1"><dc:Bounds height="200.0" width="500.0" '
        'x="240.0" y="80.0"/></bpmndi:BPMNShape>\n'
        f'            <bpmndi:BPMNShape bpmnElement="{RECV}" id="BPMNShape_{RECV}">'
        '<dc:Bounds height="140.0" width="100.0" x="600.0" y="300.0"/></bpmndi:BPMNShape>\n'
        '            <bpmndi:BPMNShape bpmnElement="StartEvent_2" id="BPMNShape_StartEvent_2">'
        '<dc:Bounds height="32.0" width="32.0" x="290.0" y="150.0"/></bpmndi:BPMNShape>\n'
        f'            <bpmndi:BPMNShape bpmnElement="{ST}" id="BPMNShape_{ST}">'
        '<dc:Bounds height="60.0" width="100.0" x="430.0" y="136.0"/></bpmndi:BPMNShape>\n'
        '            <bpmndi:BPMNShape bpmnElement="EndEvent_2" id="BPMNShape_EndEvent_2">'
        '<dc:Bounds height="32.0" width="32.0" x="640.0" y="150.0"/></bpmndi:BPMNShape>\n'
        f'            <bpmndi:BPMNEdge bpmnElement="{MF}" id="BPMNEdge_{MF}" '
        f'sourceElement="BPMNShape_{ST}" targetElement="BPMNShape_{RECV}">'
        '<di:waypoint x="480.0" xsi:type="dc:Point" y="166.0"/>'
        '<di:waypoint x="650.0" xsi:type="dc:Point" y="300.0"/></bpmndi:BPMNEdge>\n')

    definitions = f"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn2:definitions xmlns:bpmn2="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" xmlns:ifl="http:///com.sap.ifl.model/Ifl.xsd" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" id="Definitions_1">
    <bpmn2:collaboration id="Collaboration_1" name="Default Collaboration">
        <bpmn2:extensionElements/>
        <bpmn2:participant id="Participant_Process_1" ifl:type="IntegrationProcess" name="Integration Process" processRef="Process_1">
            <bpmn2:extensionElements/>
        </bpmn2:participant>
        <bpmn2:participant id="{RECV}" ifl:type="EndpointRecevier" name="{html.escape(receiver_name)}">
            <bpmn2:extensionElements/>
        </bpmn2:participant>
{msg_flow}    </bpmn2:collaboration>
    <bpmn2:process id="Process_1" name="Integration Process">
        <bpmn2:extensionElements>
            <ifl:property><key>componentVersion</key><value>1.2</value></ifl:property>
            <ifl:property><key>cmdVariantUri</key><value>ctype::FlowElementVariant/cname::IntegrationProcess/version::1.2.1</value></ifl:property>
        </bpmn2:extensionElements>
{timer}{task}        <bpmn2:endEvent id="EndEvent_2" name="End">
            <bpmn2:extensionElements>
                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::MessageEndEvent/version::1.1.0</value></ifl:property>
            </bpmn2:extensionElements>
            <bpmn2:incoming>{f2}</bpmn2:incoming>
            <bpmn2:messageEventDefinition/>
        </bpmn2:endEvent>
{seq}    </bpmn2:process>
    <bpmndi:BPMNDiagram id="BPMNDiagram_1" name="Default Collaboration Diagram">
        <bpmndi:BPMNPlane bpmnElement="Collaboration_1" id="BPMNPlane_1">
{di}        </bpmndi:BPMNPlane>
    </bpmndi:BPMNDiagram>
</bpmn2:definitions>
"""
    manifest = build_manifest(iflow_id, name)
    project = build_project(iflow_id)
    files = {
        "META-INF/MANIFEST.MF": manifest,
        ".project": project,
        f"src/main/resources/scenarioflows/integrationflow/{iflow_id}.iflw": definitions,
    }
    return MinimalIFlowResult(
        iflow_id=iflow_id, name=name, iflw_xml=definitions,
        manifest=manifest, project_xml=project, files=files)
