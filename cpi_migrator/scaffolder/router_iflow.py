"""
scaffolder/router_iflow.py

Generates an isolated ROUTER iFlow: Timer → CM(set routing property) →
ExclusiveGateway → N parallel branches → ends. Built entirely from structure
decoded out of the real corpus (cname::ExclusiveGateway/version::1.1.2 and
cname::GatewayRoute/version::1.0.0), not guessed:

  - the gateway carries default="<flowid>" (the fallback route) + throwException
  - each route is a GatewayRoute sequenceFlow with an expressionType
    (NonXML for ${property.x}/${header.x} conditions, XML for xpath/count())
  - conditional routes carry a <conditionExpression>; the default route carries
    none and is named in the gateway's default= attribute
  - routes are evaluated in PRIORITY order (first match wins), so order matters

Isolated on purpose (one construct per iFlow) so a routing fault can't be
masked by another step. N branches, not just 2.
"""
from __future__ import annotations

import html

from scaffolder.minimal_iflow import (
    _content_modifier_step, _timer_start_event, _sanitize_id,
    build_manifest, build_project, _ifl, MinimalIFlowResult,
)


def _message_end_event(eid: str, incoming: str, name: str = "End") -> str:
    return (f'        <bpmn2:endEvent id="{eid}" name="{html.escape(name)}">\n'
            f'            <bpmn2:extensionElements>\n'
            f'                <ifl:property><key>componentVersion</key><value>1.1</value></ifl:property>\n'
            f'                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::MessageEndEvent/version::1.1.0</value></ifl:property>\n'
            f'            </bpmn2:extensionElements>\n'
            f'            <bpmn2:incoming>{incoming}</bpmn2:incoming>\n'
            f'            <bpmn2:messageEventDefinition/>\n'
            f'        </bpmn2:endEvent>\n')


def _gateway_route_flow(fid, label, source, target, condition, expr_type):
    cond = ""
    if condition is not None:
        cond = (f'            <bpmn2:conditionExpression id="FormalExpression_{fid}" '
                f'xsi:type="bpmn2:tFormalExpression">{html.escape(condition)}</bpmn2:conditionExpression>\n')
    return (f'        <bpmn2:sequenceFlow id="{fid}" name="{html.escape(label)}" '
            f'sourceRef="{source}" targetRef="{target}">\n'
            f'            <bpmn2:extensionElements>\n'
            f'                <ifl:property><key>expressionType</key><value>{expr_type}</value></ifl:property>\n'
            f'                <ifl:property><key>componentVersion</key><value>1.0</value></ifl:property>\n'
            f'                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::GatewayRoute/version::1.0.0</value></ifl:property>\n'
            f'            </bpmn2:extensionElements>\n'
            f'{cond}        </bpmn2:sequenceFlow>\n')


def default_routes(prop: str):
    """The example router: matches-Y, is-populated, empty→default. Priority order."""
    p = "${property." + prop + "}"
    return [
        {"label": "Matches Y",       "condition": f"{p} = 'Y'",                    "expr_type": "NonXML", "process": True},
        {"label": "Is Populated",    "condition": f"{p} != null and {p} != ''",   "expr_type": "NonXML", "process": True},
        {"label": "Empty (default)", "condition": None,                            "expr_type": "NonXML", "process": False},
    ]


def generate_router_iflow(name: str, iflow_id: str = "", route_property: str = "value",
                          set_value: str = "Y", routes=None) -> MinimalIFlowResult:
    iflow_id = _sanitize_id(iflow_id or name)
    routes = routes or default_routes(route_property)
    # exactly one default route (condition is None); if none declared, make the last one default
    if not any(r["condition"] is None for r in routes):
        routes = routes[:-1] + [{**routes[-1], "condition": None}]

    GW = "ExclusiveGateway_1"
    SET = "CallActivity_SetProp"
    f_timer, f_to_gw = "SequenceFlow_2", "SequenceFlow_3"

    # ── process body ──────────────────────────────────────────────────────
    timer = _timer_start_event(outgoing=f_timer)
    set_cm = _content_modifier_step(
        SET, "Set Routing Property",
        properties=[(route_property, set_value)],
        incoming=f_timer, outgoing=f_to_gw)

    route_flows, branch_nodes, ends, outgoings, default_fid = "", "", "", "", None
    # layout rows for parallel branches, fanning from the gateway
    row_y = [70 + 90 * i for i in range(len(routes))]
    di_shapes, di_edges = [], []
    GW_X, GW_CX, GW_CY = 560, 580, 165
    for i, r in enumerate(routes):
        fid = f"SequenceFlow_R{i+1}"
        outgoings += f"            <bpmn2:outgoing>{fid}</bpmn2:outgoing>\n"
        y = row_y[i]
        if r["process"]:
            cm_id, end_id = f"CallActivity_R{i+1}", f"EndEvent_R{i+1}"
            f_cm_end = f"SequenceFlow_RE{i+1}"
            branch_nodes += _content_modifier_step(
                cm_id, f"Process {r['label']}", incoming=fid, outgoing=f_cm_end)
            ends += _message_end_event(end_id, f_cm_end, name=f"End {r['label']}")
            route_flows += _gateway_route_flow(fid, r["label"], GW, cm_id, r["condition"], r["expr_type"])
            route_flows += (f'        <bpmn2:sequenceFlow id="{f_cm_end}" '
                            f'sourceRef="{cm_id}" targetRef="{end_id}"/>\n')
            di_shapes.append(f'            <bpmndi:BPMNShape bpmnElement="{cm_id}" id="BPMNShape_{cm_id}"><dc:Bounds height="60.0" width="100.0" x="700.0" y="{y}.0"/></bpmndi:BPMNShape>\n')
            di_shapes.append(f'            <bpmndi:BPMNShape bpmnElement="{end_id}" id="BPMNShape_{end_id}"><dc:Bounds height="32.0" width="32.0" x="880.0" y="{y+14}.0"/></bpmndi:BPMNShape>\n')
            di_edges.append(f'            <bpmndi:BPMNEdge bpmnElement="{fid}" id="BPMNEdge_{fid}" sourceElement="BPMNShape_{GW}" targetElement="BPMNShape_{cm_id}"><di:waypoint x="{GW_CX}.0" xsi:type="dc:Point" y="{GW_CY}.0"/><di:waypoint x="700.0" xsi:type="dc:Point" y="{y+30}.0"/></bpmndi:BPMNEdge>\n')
            di_edges.append(f'            <bpmndi:BPMNEdge bpmnElement="{f_cm_end}" id="BPMNEdge_{f_cm_end}" sourceElement="BPMNShape_{cm_id}" targetElement="BPMNShape_{end_id}"><di:waypoint x="800.0" xsi:type="dc:Point" y="{y+30}.0"/><di:waypoint x="880.0" xsi:type="dc:Point" y="{y+30}.0"/></bpmndi:BPMNEdge>\n')
        else:
            end_id = f"EndEvent_R{i+1}"
            ends += _message_end_event(end_id, fid, name=f"End {r['label']}")
            route_flows += _gateway_route_flow(fid, r["label"], GW, end_id, r["condition"], r["expr_type"])
            di_shapes.append(f'            <bpmndi:BPMNShape bpmnElement="{end_id}" id="BPMNShape_{end_id}"><dc:Bounds height="32.0" width="32.0" x="720.0" y="{y+14}.0"/></bpmndi:BPMNShape>\n')
            di_edges.append(f'            <bpmndi:BPMNEdge bpmnElement="{fid}" id="BPMNEdge_{fid}" sourceElement="BPMNShape_{GW}" targetElement="BPMNShape_{end_id}"><di:waypoint x="{GW_CX}.0" xsi:type="dc:Point" y="{GW_CY}.0"/><di:waypoint x="720.0" xsi:type="dc:Point" y="{y+30}.0"/></bpmndi:BPMNEdge>\n')
        if r["condition"] is None:
            default_fid = fid

    gateway = (f'        <bpmn2:exclusiveGateway default="{default_fid}" id="{GW}" name="Route">\n'
               f'            <bpmn2:extensionElements>\n'
               f'                <ifl:property><key>componentVersion</key><value>1.1</value></ifl:property>\n'
               f'                <ifl:property><key>activityType</key><value>ExclusiveGateway</value></ifl:property>\n'
               f'                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::ExclusiveGateway/version::1.1.2</value></ifl:property>\n'
               f'                <ifl:property><key>throwException</key><value>false</value></ifl:property>\n'
               f'            </bpmn2:extensionElements>\n'
               f'            <bpmn2:incoming>{f_to_gw}</bpmn2:incoming>\n'
               f'{outgoings}        </bpmn2:exclusiveGateway>\n')

    plain = (f'        <bpmn2:sequenceFlow id="{f_timer}" sourceRef="StartEvent_2" targetRef="{SET}"/>\n'
             f'        <bpmn2:sequenceFlow id="{f_to_gw}" sourceRef="{SET}" targetRef="{GW}"/>\n')

    collab_props = (
        _ifl("namespaceMapping") + _ifl("httpSessionHandling", "None") +
        _ifl("returnExceptionToSender", "false") + _ifl("log", "All events") +
        _ifl("componentVersion", "1.2") + _ifl("ServerTrace", "false") +
        _ifl("cmdVariantUri", "ctype::IFlowVariant/cname::IFlowConfiguration/version::1.2.3"))

    pool_h = max(280, row_y[-1] + 120)
    di = ("".join(di_shapes) + "".join(di_edges))

    iflw = f"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn2:definitions xmlns:bpmn2="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" xmlns:ifl="http:///com.sap.ifl.model/Ifl.xsd" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" id="Definitions_1">
    <bpmn2:collaboration id="Collaboration_1" name="Default Collaboration">
        <bpmn2:extensionElements>
{collab_props}        </bpmn2:extensionElements>
        <bpmn2:participant id="Participant_Process_1" ifl:type="IntegrationProcess" name="Integration Process" processRef="Process_1">
            <bpmn2:extensionElements/>
        </bpmn2:participant>
    </bpmn2:collaboration>
    <bpmn2:process id="Process_1" name="Integration Process">
        <bpmn2:extensionElements>
            <ifl:property><key>transactionTimeout</key><value>30</value></ifl:property>
            <ifl:property><key>componentVersion</key><value>1.2</value></ifl:property>
            <ifl:property><key>cmdVariantUri</key><value>ctype::FlowElementVariant/cname::IntegrationProcess/version::1.2.1</value></ifl:property>
            <ifl:property><key>transactionalHandling</key><value>Not Required</value></ifl:property>
        </bpmn2:extensionElements>
{timer}{set_cm}{gateway}{branch_nodes}{ends}{plain}{route_flows}    </bpmn2:process>
    <bpmndi:BPMNDiagram id="BPMNDiagram_1" name="Default Collaboration Diagram">
        <bpmndi:BPMNPlane bpmnElement="Collaboration_1" id="BPMNPlane_1">
            <bpmndi:BPMNShape bpmnElement="Participant_Process_1" id="BPMNShape_Participant_Process_1"><dc:Bounds height="{pool_h}.0" width="760.0" x="240.0" y="40.0"/></bpmndi:BPMNShape>
            <bpmndi:BPMNShape bpmnElement="StartEvent_2" id="BPMNShape_StartEvent_2"><dc:Bounds height="32.0" width="32.0" x="290.0" y="149.0"/></bpmndi:BPMNShape>
            <bpmndi:BPMNShape bpmnElement="{SET}" id="BPMNShape_{SET}"><dc:Bounds height="60.0" width="100.0" x="400.0" y="135.0"/></bpmndi:BPMNShape>
            <bpmndi:BPMNShape bpmnElement="{GW}" id="BPMNShape_{GW}"><dc:Bounds height="40.0" width="40.0" x="{GW_X}.0" y="145.0"/></bpmndi:BPMNShape>
{di}        </bpmndi:BPMNPlane>
    </bpmndi:BPMNDiagram>
</bpmn2:definitions>
"""
    manifest = build_manifest(iflow_id, name)
    project = build_project(iflow_id)
    files = {
        "META-INF/MANIFEST.MF": manifest,
        ".project": project,
        f"src/main/resources/scenarioflows/integrationflow/{iflow_id}.iflw": iflw,
    }
    return MinimalIFlowResult(iflow_id=iflow_id, name=name, iflw_xml=iflw,
                              manifest=manifest, project_xml=project, files=files)
