"""
scaffolder/monster_iflow.py

The "runnable monster" generators — rich, self-contained, timer-triggered iFlows
that exercise the full decoded step palette so a single deploy demonstrates
converters, mappings, scripts, splitter/gather, routing, parallel multicast, an
exception subprocess, and bundled XSD/WSDL/EDMX/XSL — all driven by a multi-record
"monster body" seeded internally (no sender needed).

Two generators:
  • build_linear_monster()    — HIGH confidence. Uses the PROVEN linear wirer
    (build_flow_from_steps). Converters run as XML→…→XML round-trips so the body
    stays valid for every downstream XML step; splitter/gather operate on the
    clean seeded body BEFORE any converter touches it. Verified end-to-end as a
    timer scaffold elsewhere; the only new elements (converters) are file-less.
  • build_branching_monster() — exercises router (exclusiveGateway) + parallel
    multicast → branches up/down → join → gather, plus an exception subprocess.
    Hand-assembled with self-consistent diagram geometry. Structurally validated
    in the sandbox (every shape↔element, every edge↔flow, every flow endpoint
    exists, every branch's content-type chain valid) but NOT tenant-verified —
    deploy it on its own to isolate the branching unknown.

Both return a MinimalIFlowResult; monster_to_zip() packages one into the exact
tenant-accepted bundle via the proven packager.
"""
from __future__ import annotations

import html
import tempfile
from pathlib import Path

from scaffolder.minimal_iflow import (
    MinimalIFlowResult, build_manifest, build_project, build_flow_from_steps,
    _sanitize_id, _timer_start_event, _content_modifier_step, _script_step,
    _groovy_body, _mapping_step, _xslt_identity_body, _gather_step, _filter_step,
    _xml_to_json_step, _json_to_xml_step, _xml_to_csv_step, _csv_to_xml_step,
    _csv_target_xsd,
    validate_step_chain, _ifl,
)


# ── Shared monster resources ─────────────────────────────────────────────────

def monster_body() -> str:
    """A multi-record XML payload that gives every XML step real data to work
    on: <Orders> with several <Order> rows carrying nested lines + a date to
    reformat. Root is <Orders>, rows are <Order> (splitter target /Orders/Order)."""
    return (
        "<Orders>"
        "<Order id=\"1001\"><region>EU</region><docType>INV</docType>"
        "<created>2026-01-15</created><amount>1250.00</amount>"
        "<lines><line sku=\"A-1\" qty=\"3\"/><line sku=\"A-2\" qty=\"1\"/></lines></Order>"
        "<Order id=\"1002\"><region>US</region><docType>ORD</docType>"
        "<created>2026-02-03</created><amount>880.50</amount>"
        "<lines><line sku=\"B-9\" qty=\"7\"/></lines></Order>"
        "<Order id=\"1003\"><region>EU</region><docType>INV</docType>"
        "<created>2026-03-21</created><amount>4400.00</amount>"
        "<lines><line sku=\"C-3\" qty=\"2\"/><line sku=\"C-4\" qty=\"5\"/></lines></Order>"
        "</Orders>"
    )


def _order_xsd() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'elementFormDefault="qualified">\n'
        '  <xs:element name="Orders">\n'
        '    <xs:complexType><xs:sequence>\n'
        '      <xs:element name="Order" maxOccurs="unbounded">\n'
        '        <xs:complexType><xs:sequence>\n'
        '          <xs:element name="region" type="xs:string"/>\n'
        '          <xs:element name="docType" type="xs:string"/>\n'
        '          <xs:element name="created" type="xs:date"/>\n'
        '          <xs:element name="amount" type="xs:decimal"/>\n'
        '          <xs:element name="lines"/>\n'
        '        </xs:sequence>\n'
        '        <xs:attribute name="id" type="xs:string"/>\n'
        '        </xs:complexType>\n'
        '      </xs:element>\n'
        '    </xs:sequence></xs:complexType>\n'
        '  </xs:element>\n'
        '</xs:schema>\n'
    )


def _order_wsdl() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/" '
        'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'xmlns:tns="urn:acme:orders" targetNamespace="urn:acme:orders" '
        'name="OrderService">\n'
        '  <wsdl:types>\n'
        '    <xs:schema targetNamespace="urn:acme:orders">\n'
        '      <xs:element name="OrderRequest" type="xs:anyType"/>\n'
        '      <xs:element name="OrderResponse" type="xs:anyType"/>\n'
        '    </xs:schema>\n'
        '  </wsdl:types>\n'
        '  <wsdl:message name="in"><wsdl:part name="body" element="tns:OrderRequest"/></wsdl:message>\n'
        '  <wsdl:message name="out"><wsdl:part name="body" element="tns:OrderResponse"/></wsdl:message>\n'
        '  <wsdl:portType name="OrderPort">\n'
        '    <wsdl:operation name="Process">\n'
        '      <wsdl:input message="tns:in"/><wsdl:output message="tns:out"/>\n'
        '    </wsdl:operation>\n'
        '  </wsdl:portType>\n'
        '</wsdl:definitions>\n'
    )


def _order_edmx() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0">\n'
        '  <edmx:DataServices>\n'
        '    <Schema xmlns="http://docs.oasis-open.org/odata/ns/edm" Namespace="acme.orders">\n'
        '      <EntityType Name="Order">\n'
        '        <Key><PropertyRef Name="id"/></Key>\n'
        '        <Property Name="id" Type="Edm.String"/>\n'
        '        <Property Name="region" Type="Edm.String"/>\n'
        '        <Property Name="amount" Type="Edm.Decimal"/>\n'
        '      </EntityType>\n'
        '      <EntityContainer Name="Container">\n'
        '        <EntitySet Name="Orders" EntityType="acme.orders.Order"/>\n'
        '      </EntityContainer>\n'
        '    </Schema>\n'
        '  </edmx:DataServices>\n'
        '</edmx:Edmx>\n'
    )


def _normalize_xsl() -> str:
    """Identity-plus transform: copies everything and stamps a <normalized/>
    marker so the mapping demonstrably does work (not a bare identity)."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" '
        'version="1.0">\n'
        '  <xsl:template match="@*|node()">\n'
        '    <xsl:copy><xsl:apply-templates select="@*|node()"/></xsl:copy>\n'
        '  </xsl:template>\n'
        '  <xsl:template match="/Orders">\n'
        '    <Orders normalized="true">\n'
        '      <xsl:apply-templates select="@*|node()"/>\n'
        '    </Orders>\n'
        '  </xsl:template>\n'
        '</xsl:stylesheet>\n'
    )


def _date_reformat_groovy() -> str:
    """Groovy that reformats the <created> date yyyy-MM-dd → dd.MM.yyyy and sets
    an exchange property — the 'reformat date + enrich' the user asked for."""
    return (
        "import com.sap.gateway.ip.core.customdev.util.Message\n"
        "import java.text.SimpleDateFormat\n\n"
        "def Message processData(Message message) {\n"
        "    def body = message.getBody(java.lang.String) as String\n"
        "    def inFmt  = new SimpleDateFormat('yyyy-MM-dd')\n"
        "    def outFmt = new SimpleDateFormat('dd.MM.yyyy')\n"
        "    body = body.replaceAll(/<created>(\\d{4}-\\d{2}-\\d{2})<\\/created>/) { all, d ->\n"
        "        '<created>' + outFmt.format(inFmt.parse(d)) + '</created>'\n"
        "    }\n"
        "    message.setProperty('DatesReformatted', 'true')\n"
        "    def messageLog = messageLogFactory.getMessageLog(message)\n"
        "    if (messageLog != null) messageLog.setStringProperty('ScriptStep', 'executed')\n"
        "    message.setBody(body)\n"
        "    return message\n"
        "}\n"
    )


def _schema_files() -> dict:
    """The bundled, non-step-referenced schemas (convention: under their own
    folders; referenced from mappings/adapters in a real flow)."""
    return {
        "src/main/resources/xsd/Order.xsd": _order_xsd(),
        "src/main/resources/wsdl/OrderService.wsdl": _order_wsdl(),
        "src/main/resources/edmx/Order.edmx": _order_edmx(),
    }


# ── Linear monster (HIGH confidence) ─────────────────────────────────────────

def build_linear_monster(name: str = "Runnable Monster Linear",
                         iflow_id: str = "RunnableMonsterLinear") -> MinimalIFlowResult:
    """Timer → seed(monster body) → mapping(XSLT) → groovy(date) → filter →
    splitter → gather → 4-converter round-trip → ack. Splitter/gather run on the
    clean seeded body BEFORE converters; converters round-trip back to XML; the
    ack CM sets a constant body at the end. Bundles XSD/WSDL/EDMX/XSL + groovy."""
    iflow_id = _sanitize_id(iflow_id)
    specs = [
        {"kind": "content_modifier", "name": "Seed Monster Body",
         "body": monster_body(),
         "headers": [("Content-Type", "application/xml")]},
        {"kind": "mapping", "name": "Normalize (XSLT)",
         "mapping_name": "NormalizeOrders"},
        {"kind": "script", "name": "Reformat Date & Enrich",
         "script_file": "reformatDate.groovy"},
        {"kind": "filter", "name": "Filter Orders", "xpath": "/*"},
        {"kind": "splitter", "name": "Split Orders", "xpath": "/Orders/Order"},
        {"kind": "gather", "name": "Gather Orders"},
        {"kind": "xml_to_json", "name": "To JSON"},
        {"kind": "json_to_xml", "name": "Back to XML"},
        {"kind": "xml_to_csv", "name": "To CSV"},
        {"kind": "csv_to_xml", "name": "Back to XML 2"},
        {"kind": "content_modifier", "name": "Build Acknowledgement",
         "body": "<MonsterAck><status>OK</status>"
                 "<dates>${property.DatesReformatted}</dates></MonsterAck>"},
    ]
    # Content-type safety: confirm no converter feeds an incompatible step.
    ok, errs = validate_step_chain([s["kind"] for s in specs], seed_format="XML")
    if not ok:
        raise ValueError("monster step chain invalid: " + "; ".join(errs))

    iflw_xml, files = build_flow_from_steps(iflow_id, name, specs)
    # Override the auto-generated identity XSL with the normalize XSL, add groovy
    # + schemas. (build_flow_from_steps already created script/<file> +
    # mapping/<name>.xsl entries; we replace their content.)
    files["src/main/resources/mapping/NormalizeOrders.xsl"] = _normalize_xsl()
    files["src/main/resources/script/reformatDate.groovy"] = _date_reformat_groovy()
    files.update(_schema_files())

    return MinimalIFlowResult(
        iflow_id=iflow_id, name=name, iflw_xml=iflw_xml,
        manifest=build_manifest(iflow_id, name),
        project_xml=build_project(iflow_id), files=files)


# ── Packaging via the proven packager ────────────────────────────────────────

def monster_to_zip(result: MinimalIFlowResult) -> bytes:
    """Write the scaffold layout (<id>.iflw + <id>__meta/) to a temp dir and run
    the proven CPIUploader._package_iflow → tenant-accepted bundle bytes."""
    from fetcher.cpi_uploader import CPIUploader
    tmp = Path(tempfile.mkdtemp())
    iflw_path = tmp / f"{result.iflow_id}.iflw"
    iflw_path.write_text(result.iflw_xml, encoding="utf-8")
    meta = tmp / f"{result.iflow_id}__meta"
    (meta / "META-INF").mkdir(parents=True, exist_ok=True)
    (meta / "MANIFEST.MF").write_text(result.manifest, encoding="utf-8")
    (meta / ".project").write_text(result.project_xml, encoding="utf-8")
    for rel, content in (result.files or {}).items():
        if not rel.startswith("src/main/resources/"):
            continue
        if rel.startswith("src/main/resources/scenarioflows/"):
            continue
        if rel in ("src/main/resources/parameters.prop",
                   "src/main/resources/parameters.propdef"):
            continue
        dest = meta / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            dest.write_bytes(content)
        else:
            dest.write_text(content, encoding="utf-8")
    return CPIUploader._package_iflow(
        iflw_path, result.iflow_id, result.name)


# ── Branching monster (router + multicast + join + gather + exception SP) ─────
# Self-assembled topology. Geometry is computed from a NODES table so every edge
# derives its waypoints from real shape coordinates — guaranteeing shape↔edge
# consistency by construction. NOT tenant-verified; deploy on its own.

_W = {"event": 32, "activity": 100, "gateway": 40}
_H = {"event": 32, "activity": 60, "gateway": 40}
_LANE = {"up": 135, "main": 245, "down": 355}


def _route_flow(fid, src, tgt, label, expr_type="", condition="", default=False):
    """A gateway-route sequenceFlow (cname::GatewayRoute/1.0.0). Conditional
    routes carry expressionType (XML=XPath, NonXML=Camel Simple) + a
    <conditionExpression>; the default route omits both."""
    cond = ""
    if not default and condition:
        cond = (f'\n            <bpmn2:conditionExpression '
                f'id="FormalExpression_{fid}" xsi:type="bpmn2:tFormalExpression">'
                f'{html.escape(condition)}</bpmn2:conditionExpression>')
    et = "" if default else expr_type
    nm = f' name="{html.escape(label, quote=True)}"' if label else ""
    return (
        f'        <bpmn2:sequenceFlow id="{fid}"{nm} sourceRef="{src}" targetRef="{tgt}">\n'
        f'            <bpmn2:extensionElements>\n'
        f'                <ifl:property><key>expressionType</key><value>{et}</value></ifl:property>\n'
        f'                <ifl:property><key>componentVersion</key><value>1.0</value></ifl:property>\n'
        f'                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::GatewayRoute/version::1.0.0</value></ifl:property>\n'
        f'            </bpmn2:extensionElements>{cond}\n'
        f'        </bpmn2:sequenceFlow>\n'
    )


def _router(gw_id, name, in_flow, out_flows, default_flow):
    """exclusiveGateway (Router). out_flows rendered separately as _route_flow.
    raiseAlert/throwException are OMITTED: CPI only allows them when the default
    route ends in a Terminate End event (ours merges downstream), and raiseAlert
    is deprecated. Majority of real routers carry neither."""
    outs = "".join(f"            <bpmn2:outgoing>{f}</bpmn2:outgoing>\n" for f in out_flows)
    return (
        f'        <bpmn2:exclusiveGateway default="{default_flow}" id="{gw_id}" '
        f'name="{html.escape(name, quote=True)}">\n'
        f'            <bpmn2:extensionElements>\n'
        f'                <ifl:property><key>throwException</key><value>false</value></ifl:property>\n'
        f'                <ifl:property><key>activityType</key><value>ExclusiveGateway</value></ifl:property>\n'
        f'                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::ExclusiveGateway</value></ifl:property>\n'
        f'            </bpmn2:extensionElements>\n'
        f'            <bpmn2:incoming>{in_flow}</bpmn2:incoming>\n'
        f'{outs}'
        f'        </bpmn2:exclusiveGateway>\n'
    )


def _parallel_gateway(gw_id, name, in_flows, out_flows, activity, version):
    """parallelGateway for Multicast (1 in, N out) or Join (N in, 1 out)."""
    ins = "".join(f"            <bpmn2:incoming>{f}</bpmn2:incoming>\n" for f in in_flows)
    outs = "".join(f"            <bpmn2:outgoing>{f}</bpmn2:outgoing>\n" for f in out_flows)
    return (
        f'        <bpmn2:parallelGateway id="{gw_id}" name="{html.escape(name, quote=True)}">\n'
        f'            <bpmn2:extensionElements>\n'
        f'                <ifl:property><key>componentVersion</key><value>1.1</value></ifl:property>\n'
        f'                <ifl:property><key>activityType</key><value>{activity}</value></ifl:property>\n'
        f'                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::{activity}/version::{version}</value></ifl:property>\n'
        f'                <ifl:property><key>subActivityType</key><value>parallel</value></ifl:property>\n'
        f'            </bpmn2:extensionElements>\n'
        f'{ins}{outs}'
        f'        </bpmn2:parallelGateway>\n'
    )


def _error_start_event(ev_id, outgoing):
    return (
        f'        <bpmn2:startEvent id="{ev_id}" name="Error Start">\n'
        f'            <bpmn2:outgoing>{outgoing}</bpmn2:outgoing>\n'
        f'            <bpmn2:errorEventDefinition>\n'
        f'                <bpmn2:extensionElements>\n'
        f'                    <ifl:property><key>activityType</key><value>StartErrorEvent</value></ifl:property>\n'
        f'                    <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::ErrorStartEvent</value></ifl:property>\n'
        f'                </bpmn2:extensionElements>\n'
        f'            </bpmn2:errorEventDefinition>\n'
        f'        </bpmn2:startEvent>\n'
    )


def _message_end_event(ev_id, incoming, name="End"):
    return (
        f'        <bpmn2:endEvent id="{ev_id}" name="{name}">\n'
        f'            <bpmn2:extensionElements>\n'
        f'                <ifl:property><key>componentVersion</key><value>1.1</value></ifl:property>\n'
        f'                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::MessageEndEvent/version::1.1.0</value></ifl:property>\n'
        f'            </bpmn2:extensionElements>\n'
        f'            <bpmn2:incoming>{incoming}</bpmn2:incoming>\n'
        f'            <bpmn2:messageEventDefinition/>\n'
        f'        </bpmn2:endEvent>\n'
    )


def _shape(el_id, kind, x, y):
    w, h = _W[kind], _H[kind]
    return (
        f'            <bpmndi:BPMNShape bpmnElement="{el_id}" id="BPMNShape_{el_id}">\n'
        f'                <dc:Bounds height="{float(h)}" width="{float(w)}" x="{float(x)}" y="{float(y)}"/>\n'
        f'            </bpmndi:BPMNShape>\n'
    )


def _edge(fid, src, tgt, nodes):
    sk, sl, sx = nodes[src]; tk, tl, tx = nodes[tgt]
    sy = _LANE[sl] + _H[sk] / 2.0;  sxx = sx + _W[sk]
    ty = _LANE[tl] + _H[tk] / 2.0;  txx = tx
    return (
        f'            <bpmndi:BPMNEdge bpmnElement="{fid}" id="BPMNEdge_{fid}" '
        f'sourceElement="BPMNShape_{src}" targetElement="BPMNShape_{tgt}">\n'
        f'                <di:waypoint x="{float(sxx)}" xsi:type="dc:Point" y="{float(sy)}"/>\n'
        f'                <di:waypoint x="{float(txx)}" xsi:type="dc:Point" y="{float(ty)}"/>\n'
        f'            </bpmndi:BPMNEdge>\n'
    )


def build_branching_monster(name: str = "Runnable Monster Branching",
                            iflow_id: str = "RunnableMonsterBranching",
                            include_exception: bool = True) -> MinimalIFlowResult:
    """Timer → seed → mapping → groovy → Router(EU|default) → Merge →
    Multicast ⇉ {JSON round-trip ↑, Filter →, CSV round-trip ↓} → Join → Gather
    → ack → End, plus an exception subprocess. Branches sit on up/main/down lanes."""
    iflow_id = _sanitize_id(iflow_id)
    # id -> (kind, lane, x)
    nodes = {
        "StartEvent_2":      ("event", "main", 300),
        "CallActivity_1":    ("activity", "main", 380),   # Seed body
        "CallActivity_2":    ("activity", "main", 540),   # Mapping
        "CallActivity_3":    ("activity", "main", 700),   # Groovy
        "ExclusiveGateway_1":("gateway", "main", 870),    # Router
        "CallActivity_4":    ("activity", "up",   950),   # EU tag CM
        "CallActivity_5":    ("activity", "main", 1090),  # Merge CM
        "ParallelGateway_1": ("gateway", "main", 1250),   # Multicast
        "CallActivity_6":    ("activity", "up",   1330),  # XmlToJson
        "CallActivity_7":    ("activity", "up",   1490),  # JsonToXml
        "CallActivity_8":    ("activity", "main", 1330),  # Filter
        "CallActivity_9":    ("activity", "down", 1330),  # XmlToCsv
        "CallActivity_10":   ("activity", "down", 1490),  # CsvToXml
        "ParallelGateway_2": ("gateway", "main", 1660),   # Join
        "CallActivity_11":   ("activity", "main", 1740),  # Gather
        "CallActivity_12":   ("activity", "main", 1900),  # Ack CM
        "EndEvent_2":        ("event", "main", 2070),
    }
    # element body XML (process)
    E = []
    E.append(_timer_start_event(outgoing="SequenceFlow_3"))
    E.append(_content_modifier_step("CallActivity_1", "Seed Monster Body",
             body_expr=monster_body(), headers=[("Content-Type", "application/xml")],
             incoming="SequenceFlow_3", outgoing="SequenceFlow_4"))
    E.append(_mapping_step("CallActivity_2", "Normalize (XSLT)", "NormalizeOrders",
             incoming="SequenceFlow_4", outgoing="SequenceFlow_5"))
    E.append(_script_step("CallActivity_3", "Reformat Date & Enrich",
             "reformatDate.groovy", "processData",
             incoming="SequenceFlow_5", outgoing="SequenceFlow_6"))
    E.append(_router("ExclusiveGateway_1", "Route by Region", "SequenceFlow_6",
             ["SequenceFlow_7", "SequenceFlow_8"], default_flow="SequenceFlow_8"))
    E.append(_content_modifier_step("CallActivity_4", "Tag EU",
             properties=[("Region", "EU")],
             incoming="SequenceFlow_7", outgoing="SequenceFlow_9"))
    # Merge CM: two incoming (EU route via CM, and default route from gateway)
    merge = _content_modifier_step("CallActivity_5", "Merge",
             incoming="SequenceFlow_8", outgoing="SequenceFlow_10")
    merge = merge.replace(
        "<bpmn2:incoming>SequenceFlow_8</bpmn2:incoming>",
        "<bpmn2:incoming>SequenceFlow_8</bpmn2:incoming>\n"
        "            <bpmn2:incoming>SequenceFlow_9</bpmn2:incoming>")
    E.append(merge)
    E.append(_parallel_gateway("ParallelGateway_1", "Parallel Multicast",
             ["SequenceFlow_10"], ["SequenceFlow_11", "SequenceFlow_12", "SequenceFlow_13"],
             "Multicast", "1.1.1"))
    E.append(_xml_to_json_step("CallActivity_6", "To JSON", "SequenceFlow_11", "SequenceFlow_14"))
    E.append(_json_to_xml_step("CallActivity_7", "Back to XML", "SequenceFlow_14", "SequenceFlow_15"))
    E.append(_filter_step("CallActivity_8", "Filter Orders", "/*", "SequenceFlow_12", "SequenceFlow_16"))
    E.append(_xml_to_csv_step("CallActivity_9", "To CSV", "/Orders/Order", "SequenceFlow_13", "SequenceFlow_17"))
    E.append(_csv_to_xml_step("CallActivity_10", "Back to XML", "d/results",
             schema_path="/xsd/CsvTarget.xsd", incoming="SequenceFlow_17", outgoing="SequenceFlow_18"))
    E.append(_parallel_gateway("ParallelGateway_2", "Join",
             ["SequenceFlow_15", "SequenceFlow_16", "SequenceFlow_18"], ["SequenceFlow_19"],
             "Join", "1.0.0"))
    E.append(_gather_step("CallActivity_11", "Gather Orders", "SequenceFlow_19", "SequenceFlow_20"))
    E.append(_content_modifier_step("CallActivity_12", "Build Acknowledgement",
             body_expr="<MonsterAck><status>OK</status><region>${property.Region}</region></MonsterAck>",
             incoming="SequenceFlow_20", outgoing="SequenceFlow_21"))
    E.append(_message_end_event("EndEvent_2", "SequenceFlow_21"))

    # plain (non-route) sequence flows
    plain = [("SequenceFlow_3","StartEvent_2","CallActivity_1"),
             ("SequenceFlow_4","CallActivity_1","CallActivity_2"),
             ("SequenceFlow_5","CallActivity_2","CallActivity_3"),
             ("SequenceFlow_6","CallActivity_3","ExclusiveGateway_1"),
             ("SequenceFlow_9","CallActivity_4","CallActivity_5"),
             ("SequenceFlow_10","CallActivity_5","ParallelGateway_1"),
             ("SequenceFlow_11","ParallelGateway_1","CallActivity_6","JSON Branch"),
             ("SequenceFlow_12","ParallelGateway_1","CallActivity_8","Filter Branch"),
             ("SequenceFlow_13","ParallelGateway_1","CallActivity_9","CSV Branch"),
             ("SequenceFlow_14","CallActivity_6","CallActivity_7"),
             ("SequenceFlow_15","CallActivity_7","ParallelGateway_2"),
             ("SequenceFlow_16","CallActivity_8","ParallelGateway_2"),
             ("SequenceFlow_17","CallActivity_9","CallActivity_10"),
             ("SequenceFlow_18","CallActivity_10","ParallelGateway_2"),
             ("SequenceFlow_19","ParallelGateway_2","CallActivity_11"),
             ("SequenceFlow_20","CallActivity_11","CallActivity_12"),
             ("SequenceFlow_21","CallActivity_12","EndEvent_2")]
    plain = [(t + ("",))[:4] if len(t) == 3 else t for t in plain]
    flows_xml = "".join(
        f'        <bpmn2:sequenceFlow id="{f}"'
        + (f' name="{html.escape(nm, quote=True)}"' if nm else "")
        + f' sourceRef="{s}" targetRef="{t}"/>\n'
        for f, s, t, nm in plain)
    # route flows (router)
    flows_xml += _route_flow("SequenceFlow_7", "ExclusiveGateway_1", "CallActivity_4",
                             "Region EU", expr_type="XML",
                             condition="/Orders/Order[1]/region = 'EU'")
    flows_xml += _route_flow("SequenceFlow_8", "ExclusiveGateway_1", "CallActivity_5",
                             "Default", default=True)

    edge_ids = [f for f, _, _, _ in plain] + ["SequenceFlow_7", "SequenceFlow_8"]
    edge_pairs = {f: (s, t) for f, s, t, _ in plain}
    edge_pairs["SequenceFlow_7"] = ("ExclusiveGateway_1", "CallActivity_4")
    edge_pairs["SequenceFlow_8"] = ("ExclusiveGateway_1", "CallActivity_5")

    files = {
        "src/main/resources/mapping/NormalizeOrders.xsl": _normalize_xsl(),
        "src/main/resources/script/reformatDate.groovy": _date_reformat_groovy(),
        "src/main/resources/xsd/CsvTarget.xsd": _csv_target_xsd(),
    }
    files.update(_schema_files())

    # ── exception subprocess (optional) ──
    subproc_xml = ""
    if include_exception:
        subproc_xml = (
            '        <bpmn2:subProcess id="SubProcess_1" name="Exception Subprocess">\n'
            '            <bpmn2:extensionElements>\n'
            '                <ifl:property><key>activityType</key><value>ErrorEventSubProcessTemplate</value></ifl:property>\n'
            '                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::ErrorEventSubProcessTemplate</value></ifl:property>\n'
            '            </bpmn2:extensionElements>\n'
            + _error_start_event("StartEvent_3", "SequenceFlow_50")
            + _script_step("CallActivity_20", "Log Error", "logError.groovy",
                           "processData", "SequenceFlow_50", "SequenceFlow_51")
            + _message_end_event("EndEvent_3", "SequenceFlow_51", name="Error End")
            + '            <bpmn2:sequenceFlow id="SequenceFlow_50" sourceRef="StartEvent_3" targetRef="CallActivity_20"/>\n'
            + '            <bpmn2:sequenceFlow id="SequenceFlow_51" sourceRef="CallActivity_20" targetRef="EndEvent_3"/>\n'
            + '        </bpmn2:subProcess>\n'
        )
        files["src/main/resources/script/logError.groovy"] = (
            "import com.sap.gateway.ip.core.customdev.util.Message\n\n"
            "def Message processData(Message message) {\n"
            "    def ex = message.getProperty('CamelExceptionCaught')\n"
            "    def messageLog = messageLogFactory.getMessageLog(message)\n"
            "    if (messageLog != null) messageLog.setStringProperty('ErrorHandled', ex as String)\n"
            "    message.setProperty('ErrorHandled', 'true')\n"
            "    return message\n"
            "}\n")
        # subprocess shapes (below the down lane)
        nodes_sub = {
            "SubProcess_1": ("activity", 300, 430, 520, 130),
            "StartEvent_3": ("event", 330, 474, None, None),
            "CallActivity_20": ("activity", 430, 460, None, None),
            "EndEvent_3": ("event", 640, 474, None, None),
        }

    # validate content-type chains per branch (gateways/CM/script are ANY)
    seed = "XML"
    for branch in (
        ["content_modifier","mapping","script","content_modifier","content_modifier",
         "xml_to_json","json_to_xml","gather","content_modifier"],
        ["content_modifier","mapping","script","content_modifier","content_modifier",
         "filter","gather","content_modifier"],
        ["content_modifier","mapping","script","content_modifier","content_modifier",
         "xml_to_csv","csv_to_xml","gather","content_modifier"]):
        ok, errs = validate_step_chain(branch, seed)
        if not ok:
            raise ValueError("branch chain invalid: " + "; ".join(errs))

    # ── assemble envelope ──
    collab_props = (
        _ifl("namespaceMapping") + _ifl("httpSessionHandling", "None") +
        _ifl("returnExceptionToSender", "false") + _ifl("log", "All events") +
        _ifl("componentVersion", "1.2") + _ifl("ServerTrace", "false") +
        _ifl("cmdVariantUri", "ctype::IFlowVariant/cname::IFlowConfiguration/version::1.2.3"))

    shapes = "".join(_shape(i, k, x, _LANE[l]) for i, (k, l, x) in nodes.items())
    if include_exception:
        # subprocess + its children shapes
        shapes += _shape("SubProcess_1", "activity", 300, 510).replace(
            'height="60.0" width="100.0"', 'height="130.0" width="520.0"')
        shapes += (
            '            <bpmndi:BPMNShape bpmnElement="StartEvent_3" id="BPMNShape_StartEvent_3">\n'
            '                <dc:Bounds height="32.0" width="32.0" x="330.0" y="554.0"/>\n'
            '            </bpmndi:BPMNShape>\n'
            '            <bpmndi:BPMNShape bpmnElement="CallActivity_20" id="BPMNShape_CallActivity_20">\n'
            '                <dc:Bounds height="60.0" width="100.0" x="430.0" y="540.0"/>\n'
            '            </bpmndi:BPMNShape>\n'
            '            <bpmndi:BPMNShape bpmnElement="EndEvent_3" id="BPMNShape_EndEvent_3">\n'
            '                <dc:Bounds height="32.0" width="32.0" x="640.0" y="554.0"/>\n'
            '            </bpmndi:BPMNShape>\n')
    edges = "".join(_edge(f, edge_pairs[f][0], edge_pairs[f][1], nodes) for f in edge_ids)
    if include_exception:
        # straight edges inside subprocess (same lane, y=490)
        edges += (
            '            <bpmndi:BPMNEdge bpmnElement="SequenceFlow_50" id="BPMNEdge_SequenceFlow_50" sourceElement="BPMNShape_StartEvent_3" targetElement="BPMNShape_CallActivity_20">\n'
            '                <di:waypoint x="362.0" xsi:type="dc:Point" y="570.0"/>\n'
            '                <di:waypoint x="430.0" xsi:type="dc:Point" y="570.0"/>\n'
            '            </bpmndi:BPMNEdge>\n'
            '            <bpmndi:BPMNEdge bpmnElement="SequenceFlow_51" id="BPMNEdge_SequenceFlow_51" sourceElement="BPMNShape_CallActivity_20" targetElement="BPMNShape_EndEvent_3">\n'
            '                <di:waypoint x="530.0" xsi:type="dc:Point" y="570.0"/>\n'
            '                <di:waypoint x="640.0" xsi:type="dc:Point" y="570.0"/>\n'
            '            </bpmndi:BPMNEdge>\n')

    pool_w = 2070 + 32 - 240 + 60
    pool_h = 680 if include_exception else 420
    iflw_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
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
{''.join(E)}{subproc_xml}{flows_xml}    </bpmn2:process>
    <bpmndi:BPMNDiagram id="BPMNDiagram_1" name="Default Collaboration Diagram">
        <bpmndi:BPMNPlane bpmnElement="Collaboration_1" id="BPMNPlane_1">
            <bpmndi:BPMNShape bpmnElement="Participant_Process_1" id="BPMNShape_Participant_Process_1">
                <dc:Bounds height="{float(pool_h)}" width="{float(pool_w)}" x="240.0" y="60.0"/>
            </bpmndi:BPMNShape>
{shapes}{edges}        </bpmndi:BPMNPlane>
    </bpmndi:BPMNDiagram>
</bpmn2:definitions>
"""
    return MinimalIFlowResult(
        iflow_id=iflow_id, name=name, iflw_xml=iflw_xml,
        manifest=build_manifest(iflow_id, name),
        project_xml=build_project(iflow_id), files=files)
