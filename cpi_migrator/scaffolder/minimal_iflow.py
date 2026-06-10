"""
scaffolder/minimal_iflow.py

STAGE 1 — Minimal valid iFlow generator.

Produces a from-scratch, CPI-VALID iFlow bundle that imports cleanly: sender
participant → (message flow) → start event → end event → receiver participant,
with the full OSGi manifest, the BPMN diagram section, and cmdVariantUri on
every element.

Every structural detail here was decoded byte-for-byte from a real CPI export
(see reference/CONFIGURED_IFLOW_REFERENCE.md). This is deliberately minimal —
the pillar that proves CPI accepts our generated content. Steps, mappings, and
configured adapters build on top of this in later stages.

The OSGi Import-Package block is fixed boilerplate (byte-identical across all
iFlows, SAP's and the user's alike) — standard OSGi bundle metadata, not
creative content.
"""
from __future__ import annotations

import html
import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Optional


# The OSGi Import-Package boilerplate, taken verbatim from a real working
# iFlow export (user's own "Test mmaps"). This exact (unversioned) variant is
# confirmed-importable. Standard OSGi bundle metadata, not creative content.
_IMPORT_PACKAGE = (
    "com.sap.esb.application.services.cxf.interceptor,com.sap.esb.security,"
    "com.sap.it.op.agent.api,com.sap.it.op.agent.collector.camel,"
    "com.sap.it.op.agent.collector.cxf,com.sap.it.op.agent.mpl,javax.jms,"
    "javax.jws,javax.wsdl,javax.xml.bind.annotation,javax.xml.namespace,"
    "javax.xml.ws,org.apache.camel,org.apache.camel.builder,"
    "org.apache.camel.component.cxf,org.apache.camel.model,"
    "org.apache.camel.processor,org.apache.camel.processor.aggregate,"
    "org.apache.camel.spring.spi,org.apache.commons.logging,"
    "org.apache.cxf.binding,org.apache.cxf.binding.soap,"
    "org.apache.cxf.binding.soap.spring,org.apache.cxf.bus,"
    "org.apache.cxf.bus.resource,org.apache.cxf.bus.spring,"
    "org.apache.cxf.buslifecycle,org.apache.cxf.catalog,"
    "org.apache.cxf.configuration.jsse,org.apache.cxf.configuration.spring,"
    "org.apache.cxf.endpoint,org.apache.cxf.headers,org.apache.cxf.interceptor,"
    "org.apache.cxf.management.counters,org.apache.cxf.message,"
    "org.apache.cxf.phase,org.apache.cxf.resource,org.apache.cxf.service.factory,"
    "org.apache.cxf.service.model,org.apache.cxf.transport,"
    "org.apache.cxf.transport.common.gzip,org.apache.cxf.transport.http,"
    "org.apache.cxf.transport.http.policy,org.apache.cxf.workqueue,"
    "org.apache.cxf.ws.rm.persistence,org.apache.cxf.wsdl11,org.osgi.framework,"
    "org.slf4j,org.springframework.beans.factory.config,"
    "com.sap.esb.camel.security.cms,org.apache.camel.spi,"
    "com.sap.esb.webservice.audit.log,"
    "com.sap.esb.camel.endpoint.configurator.api,"
    "com.sap.esb.camel.jdbc.idempotency.reorg,javax.sql,"
    "org.apache.camel.processor.idempotent.jdbc,org.osgi.service.blueprint"
)


@dataclass
class MinimalIFlowResult:
    iflow_id: str
    name: str
    iflw_xml: str
    manifest: str
    project_xml: str
    files: dict = field(default_factory=dict)   # rel_path -> content


def _sanitize_id(text: str) -> str:
    """CPI artifact Id: alphanumeric, must start with a letter."""
    s = re.sub(r"[^A-Za-z0-9]", "", str(text or ""))
    if not s or not s[0].isalpha():
        s = "iFlow" + s
    return s


def _wrap_manifest_line(key: str, value: str) -> str:
    """Write a manifest header with OSGi 72-column continuation (leading space
    on wrapped lines), CRLF endings — matching real CPI manifests exactly."""
    line = f"{key}: {value}"
    out = []
    while len(line) > 72:
        out.append(line[:72])
        line = " " + line[72:]
    out.append(line)
    return "\r\n".join(out) + "\r\n"


def build_manifest(iflow_id: str, name: str) -> str:
    # Header set matched field-for-field against REAL importable iFlow exports
    # (the proven clone + the corpus minimal iFlow). Both carry
    # SAP-ContentMode: ConfigureOnly and neither carries an empty
    # SAP-ArtifactTrait — so we add the former and drop the latter. (singleton
    # and Bundle-ClassPath appear only in some exports and are NOT required —
    # the corpus minimal importable iFlow omits both.)
    import time as _time
    sym = _sanitize_id(iflow_id)
    modified = str(int(_time.time() * 1000))
    parts = [
        _wrap_manifest_line("Manifest-Version", "1.0"),
        _wrap_manifest_line("Bundle-SymbolicName", sym),
        _wrap_manifest_line("Bundle-ManifestVersion", "2"),
        _wrap_manifest_line("Origin-Bundle-SymbolicName", sym),
        _wrap_manifest_line("Origin-Bundle-Version", "1.0.0"),
        _wrap_manifest_line("Import-Package", _IMPORT_PACKAGE),
        _wrap_manifest_line("Origin-Bundle-Name", name),
        _wrap_manifest_line("SAP-RuntimeProfile", "iflmap"),
        _wrap_manifest_line("Bundle-Name", name),
        _wrap_manifest_line("Bundle-Version", "1.0.0"),
        _wrap_manifest_line("SAP-NodeType", "IFLMAP"),
        _wrap_manifest_line("Origin-ModifiedDate", modified),
        _wrap_manifest_line("SAP-BundleType", "IntegrationFlow"),
        _wrap_manifest_line("SAP-ContentMode", "ConfigureOnly"),
        _wrap_manifest_line(
            "Import-Service",
            "com.sap.esb.webservice.audit.log.AuditLogger,"
            "com.sap.esb.security.KeyManagerFactory;multiple:=false,"
            "com.sap.esb.security.TrustManagerFactory;multiple:=false,"
            "javax.sql.DataSource;multiple:=false;"
            'filter="(dataSourceName=default)",'
            "org.apache.cxf.ws.rm.persistence.RMStore;multiple:=false,"
            "com.sap.esb.camel.security.cms.SignatureSplitter;multiple:=false"),
    ]
    return "".join(parts) + "\r\n"


def build_project(iflow_id: str) -> str:
    # Matches the REAL CPI iFlow .project structure (verified against a real
    # production specimen in library_builder/bundle_assembler.py): the Java
    # builder + the three correct natures. The previous value used an ABAP
    # nature with no buildSpec, which CPI's importer could not read — a likely
    # cause of the "InputStream cannot be null" 500 on upload.
    sym = _sanitize_id(iflow_id)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\r\n'
        "<projectDescription>\r\n"
        f"\t<name>{html.escape(sym)}</name>\r\n"
        "\t<comment></comment>\r\n"
        "\t<projects>\r\n\t</projects>\r\n"
        "\t<buildSpec>\r\n"
        "\t\t<buildCommand>\r\n"
        "\t\t\t<name>org.eclipse.jdt.core.javabuilder</name>\r\n"
        "\t\t\t<arguments>\r\n\t\t\t</arguments>\r\n"
        "\t\t</buildCommand>\r\n"
        "\t</buildSpec>\r\n"
        "\t<natures>\r\n"
        "\t\t<nature>org.eclipse.jdt.core.javanature</nature>\r\n"
        "\t\t<nature>com.sap.ide.ifl.project.support.project.nature</nature>\r\n"
        "\t\t<nature>com.sap.ide.ifl.bsn</nature>\r\n"
        "\t</natures>\r\n"
        "</projectDescription>"
    )


def _ifl(key: str, value: str = "") -> str:
    return (f"                <ifl:property>\n"
            f"                    <key>{key}</key>\n"
            f"                    <value>{value}</value>\n"
            f"                </ifl:property>\n")


def build_iflw(iflow_id: str, name: str) -> str:
    """Build the minimal valid .iflw XML, matching the verified real structure:
    3 participants, 1 sender message flow connected start, start→end process,
    full diagram with shapes + edges."""
    nm = html.escape(name, quote=True)

    # ── collaboration extensionElements (verified property set) ──
    collab_props = (
        _ifl("namespaceMapping") +
        _ifl("httpSessionHandling", "None") +
        _ifl("returnExceptionToSender", "false") +
        _ifl("log", "All events") +
        _ifl("componentVersion", "1.2") +
        _ifl("ServerTrace", "false") +
        _ifl("cmdVariantUri", "ctype::IFlowVariant/cname::IFlowConfiguration/version::1.2.4")
    )

    # ── sender message flow (HTTPS — verified version from real corpus) ──
    # Real HTTPS sender: tp::HTTPS, version 1.4.1, componentVersion 1.4, plus
    # the required adapter properties (without these CPI errors "Enter adapter
    # details for channel" / "component version not supported").
    msgflow = f"""        <bpmn2:messageFlow id="MessageFlow_10" name="HTTPS" sourceRef="Participant_1" targetRef="StartEvent_2">
            <bpmn2:extensionElements>
                <ifl:property><key>ComponentType</key><value>HTTPS</value></ifl:property>
                <ifl:property><key>Description</key><value/></ifl:property>
                <ifl:property><key>ComponentNS</key><value>sap</value></ifl:property>
                <ifl:property><key>urlPath</key><value>/{iflow_id.lower()}</value></ifl:property>
                <ifl:property><key>senderAuthType</key><value>RoleBased</value></ifl:property>
                <ifl:property><key>userRole</key><value>ESBMessaging.send</value></ifl:property>
                <ifl:property><key>componentVersion</key><value>1.4</value></ifl:property>
                <ifl:property><key>ComponentSWCVName</key><value>1.4.1</value></ifl:property>
                <ifl:property><key>Name</key><value>HTTPS</value></ifl:property>
                <ifl:property><key>TransportProtocol</key><value>HTTPS</value></ifl:property>
                <ifl:property><key>TransportProtocolVersion</key><value>1.4.1</value></ifl:property>
                <ifl:property><key>cmdVariantUri</key><value>ctype::AdapterVariant/cname::sap:HTTPS/tp::HTTPS/mp::None/direction::Sender/version::1.4.1</value></ifl:property>
                <ifl:property><key>MessageProtocol</key><value>None</value></ifl:property>
                <ifl:property><key>MessageProtocolVersion</key><value>1.4.1</value></ifl:property>
                <ifl:property><key>maximumBodySize</key><value>40</value></ifl:property>
                <ifl:property><key>direction</key><value>Sender</value></ifl:property>
                <ifl:property><key>system</key><value>Sender</value></ifl:property>
            </bpmn2:extensionElements>
        </bpmn2:messageFlow>
"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn2:definitions xmlns:bpmn2="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" xmlns:ifl="http:///com.sap.ifl.model/Ifl.xsd" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" id="Definitions_1">
    <bpmn2:collaboration id="Collaboration_1" name="Default Collaboration">
        <bpmn2:extensionElements>
{collab_props}        </bpmn2:extensionElements>
        <bpmn2:participant id="Participant_1" ifl:type="EndpointSender" name="Sender">
            <bpmn2:extensionElements>
                <ifl:property><key>enableBasicAuthentication</key><value>false</value></ifl:property>
                <ifl:property><key>ifl:type</key><value>EndpointSender</value></ifl:property>
            </bpmn2:extensionElements>
        </bpmn2:participant>
        <bpmn2:participant id="Participant_2" ifl:type="EndpointRecevier" name="Receiver">
            <bpmn2:extensionElements>
                <ifl:property><key>ifl:type</key><value>EndpointRecevier</value></ifl:property>
            </bpmn2:extensionElements>
        </bpmn2:participant>
        <bpmn2:participant id="Participant_Process_1" ifl:type="IntegrationProcess" name="Integration Process" processRef="Process_1">
            <bpmn2:extensionElements/>
        </bpmn2:participant>
{msgflow}    </bpmn2:collaboration>
    <bpmn2:process id="Process_1" name="Integration Process">
        <bpmn2:extensionElements>
            <ifl:property><key>transactionTimeout</key><value>30</value></ifl:property>
            <ifl:property><key>componentVersion</key><value>1.2</value></ifl:property>
            <ifl:property><key>cmdVariantUri</key><value>ctype::FlowElementVariant/cname::IntegrationProcess/version::1.2.1</value></ifl:property>
            <ifl:property><key>transactionalHandling</key><value>Not Required</value></ifl:property>
        </bpmn2:extensionElements>
        <bpmn2:startEvent id="StartEvent_2" name="Start">
            <bpmn2:extensionElements>
                <ifl:property><key>componentVersion</key><value>1.0</value></ifl:property>
                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::MessageStartEvent/version::1.0</value></ifl:property>
            </bpmn2:extensionElements>
            <bpmn2:outgoing>SequenceFlow_3</bpmn2:outgoing>
            <bpmn2:messageEventDefinition/>
        </bpmn2:startEvent>
        <bpmn2:endEvent id="EndEvent_2" name="End">
            <bpmn2:extensionElements>
                <ifl:property><key>componentVersion</key><value>1.1</value></ifl:property>
                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::MessageEndEvent/version::1.1.0</value></ifl:property>
            </bpmn2:extensionElements>
            <bpmn2:incoming>SequenceFlow_3</bpmn2:incoming>
            <bpmn2:messageEventDefinition/>
        </bpmn2:endEvent>
        <bpmn2:sequenceFlow id="SequenceFlow_3" sourceRef="StartEvent_2" targetRef="EndEvent_2"/>
    </bpmn2:process>
    <bpmndi:BPMNDiagram id="BPMNDiagram_1" name="Default Collaboration Diagram">
        <bpmndi:BPMNPlane bpmnElement="Collaboration_1" id="BPMNPlane_1">
            <bpmndi:BPMNShape bpmnElement="Participant_1" id="BPMNShape_Participant_1">
                <dc:Bounds height="140.0" width="100.0" x="40.0" y="100.0"/>
            </bpmndi:BPMNShape>
            <bpmndi:BPMNShape bpmnElement="Participant_2" id="BPMNShape_Participant_2">
                <dc:Bounds height="140.0" width="100.0" x="900.0" y="100.0"/>
            </bpmndi:BPMNShape>
            <bpmndi:BPMNShape bpmnElement="Participant_Process_1" id="BPMNShape_Participant_Process_1">
                <dc:Bounds height="220.0" width="540.0" x="250.0" y="60.0"/>
            </bpmndi:BPMNShape>
            <bpmndi:BPMNShape bpmnElement="StartEvent_2" id="BPMNShape_StartEvent_2">
                <dc:Bounds height="32.0" width="32.0" x="292.0" y="142.0"/>
            </bpmndi:BPMNShape>
            <bpmndi:BPMNShape bpmnElement="EndEvent_2" id="BPMNShape_EndEvent_2">
                <dc:Bounds height="32.0" width="32.0" x="703.0" y="142.0"/>
            </bpmndi:BPMNShape>
            <bpmndi:BPMNEdge bpmnElement="SequenceFlow_3" id="BPMNEdge_SequenceFlow_3" sourceElement="BPMNShape_StartEvent_2" targetElement="BPMNShape_EndEvent_2">
                <di:waypoint x="323.5" xsi:type="dc:Point" y="158.0"/>
                <di:waypoint x="703.5" xsi:type="dc:Point" y="158.0"/>
            </bpmndi:BPMNEdge>
            <bpmndi:BPMNEdge bpmnElement="MessageFlow_10" id="BPMNEdge_MessageFlow_10" sourceElement="BPMNShape_Participant_1" targetElement="BPMNShape_StartEvent_2">
                <di:waypoint x="90.0" xsi:type="dc:Point" y="170.0"/>
                <di:waypoint x="308.0" xsi:type="dc:Point" y="158.0"/>
            </bpmndi:BPMNEdge>
        </bpmndi:BPMNPlane>
    </bpmndi:BPMNDiagram>
</bpmn2:definitions>
"""


def _content_modifier_step(cm_id: str, name: str, body_expr: str = "",
                           headers: Optional[list] = None,
                           properties: Optional[list] = None,
                           incoming: str = "SequenceFlow_3",
                           outgoing: str = "SequenceFlow_4") -> str:
    """Build a Content Modifier (Enricher) callActivity, schema copied verbatim
    from a real CPI export: body lives in `wrapContent` (bodyType=expression),
    headers in `headerTable` rows + HEADER_n strings, exchange properties in
    `propertyTable` rows. Values are XML-escaped here, so callers pass raw text
    (incl. ${in.body} / ${property.X}). If body_expr is empty the body is left
    unchanged (wrapContent=${in.body})."""
    headers = headers or []
    properties = properties or []

    def _rows(items):
        return "".join(
            f"<row><cell id='Action'>Create</cell><cell id='Type'>constant</cell>"
            f"<cell id='Value'>{html.escape(v)}</cell><cell id='Default'></cell>"
            f"<cell id='Name'>{html.escape(k)}</cell><cell id='Datatype'></cell></row>"
            for k, v in items)

    header_lines = "".join(
        f"                <ifl:property><key>HEADER_{i}</key>"
        f"<value>Name:=:{html.escape(k)}:;Type:=:constant:;Datatype:=::;"
        f"Value:=:{html.escape(v)}:;Default:=:</value></ifl:property>\n"
        for i, (k, v) in enumerate(headers))
    # No body change → EMPTY wrapContent (the real "leave body" encoding; an
    # ${in.body} expression is what tripped the model loader on the timer pilot).
    body_val = html.escape(body_expr) if body_expr else ""
    # bodyType must match the content: a literal body (no ${...} parameters)
    # is 'constant'; only a body using ${...} is 'expression'. Tagging a
    # constant body as 'expression' triggers CPI's validation error
    # "Expression Text does not have any expression parameters". An empty body
    # keeps the proven 'expression'+empty encoding (leave body unchanged).
    body_type = "constant" if (body_expr and "${" not in body_expr) else "expression"
    # Property order matches the proven-deployable real Enricher:
    # bodyType, propertyTable, headerTable, wrapContent, componentVersion,
    # activityType, cmdVariantUri.
    return f"""        <bpmn2:callActivity id="{cm_id}" name="{html.escape(name, quote=True)}">
            <bpmn2:extensionElements>
                <ifl:property><key>bodyType</key><value>{body_type}</value></ifl:property>
                <ifl:property><key>propertyTable</key><value>{html.escape(_rows(properties))}</value></ifl:property>
                <ifl:property><key>headerTable</key><value>{html.escape(_rows(headers))}</value></ifl:property>
{header_lines}                <ifl:property><key>wrapContent</key><value>{body_val}</value></ifl:property>
                <ifl:property><key>componentVersion</key><value>1.5</value></ifl:property>
                <ifl:property><key>activityType</key><value>Enricher</value></ifl:property>
                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::Enricher/version::1.5.1</value></ifl:property>
            </bpmn2:extensionElements>
            <bpmn2:incoming>{incoming}</bpmn2:incoming>
            <bpmn2:outgoing>{outgoing}</bpmn2:outgoing>
        </bpmn2:callActivity>
"""


# Schedule string decoded verbatim from a proven-deployable minimal timer iFlow
# (corpus: SetURLAsGlobalVariable). triggerType=simple / schedule1=fireNow=true
# → runs ONCE immediately on deploy (ideal for a pilot). Stored raw; escaped at
# emit time.
_SCHEDULE_FIRE_NOW = (
    "<row><cell>dateType</cell><cell></cell></row><row><cell>timeType</cell><cell></cell></row>"
    "<row><cell>dayValue</cell><cell></cell></row><row><cell>monthValue</cell><cell></cell></row>"
    "<row><cell>yearValue</cell><cell></cell></row><row><cell>onWeekly</cell><cell></cell></row>"
    "<row><cell>onMonthly</cell><cell></cell></row><row><cell>OnEveryMinute</cell><cell></cell></row>"
    "<row><cell>fromInterval</cell><cell></cell></row><row><cell>toInterval</cell><cell></cell></row>"
    "<row><cell>timeZone</cell><cell>( UTC 0:00 ) Greenwich Mean Time(Etc/GMT)</cell></row>"
    "<row><cell>secondValue</cell><cell>0</cell></row><row><cell>minutesValue</cell><cell></cell></row>"
    "<row><cell>hourValue</cell><cell></cell></row><row><cell>triggerType</cell><cell>simple</cell></row>"
    "<row><cell>noOfSchedules</cell><cell>1</cell></row><row><cell>schedule1</cell><cell>fireNow=true</cell></row>"
)


def _timer_start_event(schedule_raw: str = _SCHEDULE_FIRE_NOW,
                       outgoing: str = "SequenceFlow_3") -> str:
    """Timer (scheduler) start event — schema cloned from the proven-deployable
    minimal timer iFlow (SetURLAsGlobalVariable): cname::intermediatetimer
    version 1.3.0, componentVersion 1.3, activityType=StartTimerEvent, schedule
    inlined in scheduleKey. (Earlier 1.4.0 wasn't resolvable on the trial → the
    editor model loader returned 500.)"""
    return f"""        <bpmn2:startEvent id="StartEvent_2" name="Start Timer 1">
            <bpmn2:outgoing>{outgoing}</bpmn2:outgoing>
            <bpmn2:timerEventDefinition id="TimerEventDefinition_1">
                <bpmn2:extensionElements>
                    <ifl:property><key>scheduleKey</key><value>{html.escape(schedule_raw)}</value></ifl:property>
                    <ifl:property><key>componentVersion</key><value>1.3</value></ifl:property>
                    <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::intermediatetimer/version::1.3.0</value></ifl:property>
                    <ifl:property><key>activityType</key><value>StartTimerEvent</value></ifl:property>
                </bpmn2:extensionElements>
            </bpmn2:timerEventDefinition>
        </bpmn2:startEvent>
"""


# ── Referenced-file bodies ───────────────────────────────────────────────────
# Steps that point at a resource (Script→.groovy, Mapping→.xsl) MUST ship a
# valid file at the referenced path or the import/deploy fails. These generate
# minimal-but-real bodies: a pass-through Groovy and an identity XSLT — genuine,
# parseable artifacts that don't alter the payload (safe scaffolding default).

def _groovy_body(fn: str = "processData") -> str:
    """Valid self-contained Groovy for a Script step, in the canonical CPI form.
    Drops the unused `java.util.HashMap` import (CPI's Groovy 2.0 editor flags it
    as a problem and strips it on save), creates a real **exchange property**
    via `message.setProperty(...)` (so the step demonstrably produces something —
    a pass-through body alone looks like a no-op), and also logs to the MPL for
    monitoring visibility. `new Date()` needs no import (Groovy auto-imports
    java.util)."""
    return (
        "import com.sap.gateway.ip.core.customdev.util.Message\n\n"
        "// Generated pass-through script — complete the transform as needed.\n"
        "def Message %s(Message message) {\n"
        "    def body = message.getBody(java.lang.String) as String\n"
        "    // create an exchange property (readable by later steps)\n"
        "    message.setProperty('ScriptProcessedAt', new Date().toString())\n"
        "    // log to the Message Processing Log for monitoring visibility\n"
        "    def messageLog = messageLogFactory.getMessageLog(message)\n"
        "    if (messageLog != null) {\n"
        "        messageLog.setStringProperty('ScriptStep', 'executed')\n"
        "    }\n"
        "    message.setBody(body)\n"
        "    return message\n"
        "}\n"
    ) % fn


def _xslt_identity_body() -> str:
    """Valid identity XSLT for a Mapping step — copies input to output unchanged.
    Real/parseable without inventing a target schema."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" '
        'version="1.0">\n'
        '    <xsl:output method="xml" indent="yes"/>\n'
        '    <xsl:template match="@*|node()">\n'
        '        <xsl:copy>\n'
        '            <xsl:apply-templates select="@*|node()"/>\n'
        '        </xsl:copy>\n'
        '    </xsl:template>\n'
        '</xsl:stylesheet>\n'
    )


def _xslt_to_csv_body() -> str:
    """An XSLT Mapping that CONVERTS XML→CSV (output method=text). Demonstrates
    format conversion done by a mapping (vs a converter step). Pair with a
    CSV to XML Converter to return to XML."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" '
        'version="1.0">\n'
        '    <xsl:output method="text" encoding="UTF-8"/>\n'
        '    <xsl:template match="/Orders">\n'
        '        <xsl:for-each select="Order">'
        '<xsl:value-of select="@id"/>,<xsl:value-of select="region"/>,'
        '<xsl:value-of select="amount"/><xsl:text>&#10;</xsl:text></xsl:for-each>\n'
        '    </xsl:template>\n'
        '</xsl:stylesheet>\n'
    )


def _xslt_to_json_body() -> str:
    """An XSLT Mapping that CONVERTS XML→JSON (output method=text). Pair with a
    JSON to XML Converter to return to XML."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" '
        'version="1.0">\n'
        '    <xsl:output method="text" encoding="UTF-8"/>\n'
        '    <xsl:template match="/Orders">'
        '<xsl:text>{"orders":[</xsl:text>'
        '<xsl:for-each select="Order">'
        '<xsl:if test="position()&gt;1"><xsl:text>,</xsl:text></xsl:if>'
        '<xsl:text>{"id":"</xsl:text><xsl:value-of select="@id"/>'
        '<xsl:text>","region":"</xsl:text><xsl:value-of select="region"/>'
        '<xsl:text>"}</xsl:text></xsl:for-each>'
        '<xsl:text>]}</xsl:text>'
        '    </xsl:template>\n'
        '</xsl:stylesheet>\n'
    )


# ── Decoded step builders ────────────────────────────────────────────────────
# Each callActivity schema is copied verbatim from the 166-iFlow corpus: the
# load-bearing constants (activityType / cmdVariantUri cname+version /
# componentVersion) are exactly the values found in real, deploy-accepted
# iFlows. Values are XML-escaped here so callers pass raw text.

def _script_step(step_id: str, name: str, script_file: str,
                 function: str = "processData",
                 incoming: str = "", outgoing: str = "") -> str:
    """Groovy Script (callActivity). Corpus: 436. cname::GroovyScript (no
    version), subActivityType=GroovyScript, script=<file>. The .groovy MUST be
    bundled at src/main/resources/script/<file>."""
    return f"""        <bpmn2:callActivity id="{step_id}" name="{html.escape(name, quote=True)}">
            <bpmn2:extensionElements>
                <ifl:property><key>scriptFunction</key><value>{html.escape(function)}</value></ifl:property>
                <ifl:property><key>activityType</key><value>Script</value></ifl:property>
                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::GroovyScript</value></ifl:property>
                <ifl:property><key>subActivityType</key><value>GroovyScript</value></ifl:property>
                <ifl:property><key>script</key><value>{html.escape(script_file)}</value></ifl:property>
            </bpmn2:extensionElements>
            <bpmn2:incoming>{incoming}</bpmn2:incoming>
            <bpmn2:outgoing>{outgoing}</bpmn2:outgoing>
        </bpmn2:callActivity>
"""


def _mapping_step(step_id: str, name: str, mapping_name: str,
                  incoming: str = "", outgoing: str = "") -> str:
    """XSLT Mapping (callActivity), **bundled-stylesheet** variant. Corpus: 204.
    cname::XSLTMapping/version::1.2.0, mappingSource=mappingSrcIflow (load the
    stylesheet from the bundle, NOT from a runtime header). The load-bearing
    pointer is `mappinguri = dir://mapping/xslt/<path>.xsl` (WITH extension);
    `mappingpath` is the same path without extension. The bundled file is
    src/main/resources/mapping/<name>.xsl."""
    path = f"src/main/resources/mapping/{mapping_name}"
    uri = f"dir://mapping/xslt/src/main/resources/mapping/{mapping_name}.xsl"
    return f"""        <bpmn2:callActivity id="{step_id}" name="{html.escape(name, quote=True)}">
            <bpmn2:extensionElements>
                <ifl:property><key>mappingoutputformat</key><value>Bytes</value></ifl:property>
                <ifl:property><key>mappinguri</key><value>{html.escape(uri)}</value></ifl:property>
                <ifl:property><key>mappingname</key><value>{html.escape(mapping_name)}</value></ifl:property>
                <ifl:property><key>mappingpath</key><value>{html.escape(path)}</value></ifl:property>
                <ifl:property><key>mappingSource</key><value>mappingSrcIflow</value></ifl:property>
                <ifl:property><key>componentVersion</key><value>1.2</value></ifl:property>
                <ifl:property><key>activityType</key><value>Mapping</value></ifl:property>
                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::XSLTMapping/version::1.2.0</value></ifl:property>
                <ifl:property><key>subActivityType</key><value>XSLTMapping</value></ifl:property>
            </bpmn2:extensionElements>
            <bpmn2:incoming>{incoming}</bpmn2:incoming>
            <bpmn2:outgoing>{outgoing}</bpmn2:outgoing>
        </bpmn2:callActivity>
"""


def _splitter_step(step_id: str, name: str, xpath: str = "/root/Record",
                   incoming: str = "", outgoing: str = "") -> str:
    """General Splitter (callActivity). Corpus: 26. cname::GeneralSplitter/
    version::1.5.1. Splits the message on an XPath into N messages."""
    return f"""        <bpmn2:callActivity id="{step_id}" name="{html.escape(name, quote=True)}">
            <bpmn2:extensionElements>
                <ifl:property><key>exprType</key><value>XPath</value></ifl:property>
                <ifl:property><key>Streaming</key><value>true</value></ifl:property>
                <ifl:property><key>StopOnExecution</key><value>true</value></ifl:property>
                <ifl:property><key>SplitterThreads</key><value>10</value></ifl:property>
                <ifl:property><key>splitExprValue</key><value>{html.escape(xpath)}</value></ifl:property>
                <ifl:property><key>ParallelProcessing</key><value>false</value></ifl:property>
                <ifl:property><key>componentVersion</key><value>1.5</value></ifl:property>
                <ifl:property><key>activityType</key><value>Splitter</value></ifl:property>
                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::GeneralSplitter/version::1.5.1</value></ifl:property>
                <ifl:property><key>splitType</key><value>GeneralSplitter</value></ifl:property>
                <ifl:property><key>timeOut</key><value>300</value></ifl:property>
            </bpmn2:extensionElements>
            <bpmn2:incoming>{incoming}</bpmn2:incoming>
            <bpmn2:outgoing>{outgoing}</bpmn2:outgoing>
        </bpmn2:callActivity>
"""


def _gather_step(step_id: str, name: str,
                 incoming: str = "", outgoing: str = "") -> str:
    """Gather (callActivity). Corpus: 13. cname::Gather/version::1.2.0.
    Aggregates split messages (SameXMLFormat / identical-multi-mapping)."""
    return f"""        <bpmn2:callActivity id="{step_id}" name="{html.escape(name, quote=True)}">
            <bpmn2:extensionElements>
                <ifl:property><key>messageType</key><value>SameXMLFormat</value></ifl:property>
                <ifl:property><key>aggregationAlgorithm</key><value>sap-identical-multi-mapping</value></ifl:property>
                <ifl:property><key>componentVersion</key><value>1.2</value></ifl:property>
                <ifl:property><key>activityType</key><value>Gather</value></ifl:property>
                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::Gather/version::1.2.0</value></ifl:property>
            </bpmn2:extensionElements>
            <bpmn2:incoming>{incoming}</bpmn2:incoming>
            <bpmn2:outgoing>{outgoing}</bpmn2:outgoing>
        </bpmn2:callActivity>
"""


def _filter_step(step_id: str, name: str, xpath: str = "/*",
                 incoming: str = "", outgoing: str = "") -> str:
    """Filter (callActivity). Corpus: 67. cname::Filter/version::1.1.0.
    Keeps the node-list matched by an XPath. Default `/*` selects the single
    document element, so the result stays well-formed XML; `//*` would match
    EVERY element (root + children), producing a multi-root fragment that the
    next XML step (e.g. a Splitter) can't parse — the tenant failure that killed
    Mega/Bulk at Split Records."""
    return f"""        <bpmn2:callActivity id="{step_id}" name="{html.escape(name, quote=True)}">
            <bpmn2:extensionElements>
                <ifl:property><key>xpathType</key><value>Nodelist</value></ifl:property>
                <ifl:property><key>wrapContent</key><value>{html.escape(xpath)}</value></ifl:property>
                <ifl:property><key>componentVersion</key><value>1.1</value></ifl:property>
                <ifl:property><key>activityType</key><value>Filter</value></ifl:property>
                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::Filter/version::1.1.0</value></ifl:property>
            </bpmn2:extensionElements>
            <bpmn2:incoming>{incoming}</bpmn2:incoming>
            <bpmn2:outgoing>{outgoing}</bpmn2:outgoing>
        </bpmn2:callActivity>
"""


def _xml_to_json_step(step_id: str, name: str,
                      incoming: str = "", outgoing: str = "") -> str:
    """XML→JSON converter. Corpus: 19. cname::XmlToJsonConverter/version::1.0.8.
    Emits JSON — only a JSON-accepting step (JsonToXml, or a JSON-agnostic
    Script/Content Modifier) may follow it."""
    return f"""        <bpmn2:callActivity id="{step_id}" name="{html.escape(name, quote=True)}">
            <bpmn2:extensionElements>
                <ifl:property><key>xmlJsonUseStreaming</key><value>true</value></ifl:property>
                <ifl:property><key>xmlJsonSuppressRootElement</key><value>false</value></ifl:property>
                <ifl:property><key>xmlJsonConvertAllElements</key><value>all</value></ifl:property>
                <ifl:property><key>useNamespaces</key><value>true</value></ifl:property>
                <ifl:property><key>jsonNamespaceSeparator</key><value>:</value></ifl:property>
                <ifl:property><key>componentVersion</key><value>1.0</value></ifl:property>
                <ifl:property><key>activityType</key><value>XmlToJsonConverter</value></ifl:property>
                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::XmlToJsonConverter/version::1.0.8</value></ifl:property>
            </bpmn2:extensionElements>
            <bpmn2:incoming>{incoming}</bpmn2:incoming>
            <bpmn2:outgoing>{outgoing}</bpmn2:outgoing>
        </bpmn2:callActivity>
"""


def _json_to_xml_step(step_id: str, name: str,
                      incoming: str = "", outgoing: str = "") -> str:
    """JSON→XML converter. Corpus: 7. cname::JsonToXmlConverter/version::1.1.2.
    Requires JSON input; emits XML."""
    return f"""        <bpmn2:callActivity id="{step_id}" name="{html.escape(name, quote=True)}">
            <bpmn2:extensionElements>
                <ifl:property><key>jsonNamespaceSeparator</key><value>:</value></ifl:property>
                <ifl:property><key>additionalRootElementName</key><value>root</value></ifl:property>
                <ifl:property><key>addXMLRootElement</key><value>true</value></ifl:property>
                <ifl:property><key>useNamespaces</key><value>true</value></ifl:property>
                <ifl:property><key>componentVersion</key><value>1.1</value></ifl:property>
                <ifl:property><key>activityType</key><value>JsonToXmlConverter</value></ifl:property>
                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::JsonToXmlConverter/version::1.1.2</value></ifl:property>
            </bpmn2:extensionElements>
            <bpmn2:incoming>{incoming}</bpmn2:incoming>
            <bpmn2:outgoing>{outgoing}</bpmn2:outgoing>
        </bpmn2:callActivity>
"""


def _xml_to_csv_step(step_id: str, name: str, xpath: str = "/root/row",
                     incoming: str = "", outgoing: str = "") -> str:
    """XML→CSV converter. Corpus: 1. cname::XmlToCsvConverter/version::1.1.0.
    Requires XML input matching XPath_Field_Location; emits CSV."""
    return f"""        <bpmn2:callActivity id="{step_id}" name="{html.escape(name, quote=True)}">
            <bpmn2:extensionElements>
                <ifl:property><key>Field_Separator_in_CSV</key><value>,</value></ifl:property>
                <ifl:property><key>Include_Attribute</key><value>false</value></ifl:property>
                <ifl:property><key>Include_Header</key><value>false</value></ifl:property>
                <ifl:property><key>Include_Master</key><value>false</value></ifl:property>
                <ifl:property><key>XPath_Field_Location</key><value>{html.escape(xpath)}</value></ifl:property>
                <ifl:property><key>componentVersion</key><value>1.1</value></ifl:property>
                <ifl:property><key>activityType</key><value>XmlToCsvConverter</value></ifl:property>
                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::XmlToCsvConverter/version::1.1.0</value></ifl:property>
            </bpmn2:extensionElements>
            <bpmn2:incoming>{incoming}</bpmn2:incoming>
            <bpmn2:outgoing>{outgoing}</bpmn2:outgoing>
        </bpmn2:callActivity>
"""


def _csv_to_xml_step(step_id: str, name: str, xpath: str = "d/results",
                     schema_path: str = "/xsd/CsvTarget.xsd",
                     incoming: str = "", outgoing: str = "") -> str:
    """CSV→XML converter. Corpus: 2. cname::CsvToXmlConverter/version::1.1.
    Requires CSV input; emits XML. The `XML_Schema_File_Path` attribute is
    MANDATORY (CPI design-time error "Attribute 'XML Schema' is mandatory"
    otherwise) — it points to a bundled XSD describing the target row structure."""
    return f"""        <bpmn2:callActivity id="{step_id}" name="{html.escape(name, quote=True)}">
            <bpmn2:extensionElements>
                <ifl:property><key>Field_Separator_in_CSV</key><value>,</value></ifl:property>
                <ifl:property><key>ignoreFirstLineAsHeader</key><value>true</value></ifl:property>
                <ifl:property><key>XML_Schema_File_Path</key><value>{html.escape(schema_path)}</value></ifl:property>
                <ifl:property><key>XPath_Field_Location</key><value>{html.escape(xpath)}</value></ifl:property>
                <ifl:property><key>componentVersion</key><value>1.1</value></ifl:property>
                <ifl:property><key>activityType</key><value>CsvToXmlConverter</value></ifl:property>
                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::CsvToXmlConverter/version::1.1</value></ifl:property>
            </bpmn2:extensionElements>
            <bpmn2:incoming>{incoming}</bpmn2:incoming>
            <bpmn2:outgoing>{outgoing}</bpmn2:outgoing>
        </bpmn2:callActivity>
"""


def _csv_target_xsd() -> str:
    """Permissive target schema for the CSV→XML converter (matches d/results)."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'elementFormDefault="qualified">\n'
        '  <xs:element name="d">\n'
        '    <xs:complexType><xs:sequence>\n'
        '      <xs:element name="results" maxOccurs="unbounded">\n'
        '        <xs:complexType><xs:sequence>\n'
        '          <xs:any processContents="lax" minOccurs="0" maxOccurs="unbounded"/>\n'
        '        </xs:sequence></xs:complexType>\n'
        '      </xs:element>\n'
        '    </xs:sequence></xs:complexType>\n'
        '  </xs:element>\n'
        '</xs:schema>\n'
    )


# --- Content-type contract per step kind --------------------------------------
# (input_format, output_format). "XML"/"JSON"/"CSV" are hard requirements; "ANY"
# means format-agnostic (pass-through). This is what the tenant enforces with
# errors like "JSON To XML Converter supports JSON input only" — we validate
# adjacency at build time so a converter never feeds an incompatible step.
_STEP_IO = {
    "content_modifier": ("ANY", "ANY"),
    "script":           ("ANY", "ANY"),
    "external_call":    ("ANY", "ANY"),
    "mapping":          ("XML", "XML"),
    "splitter":         ("XML", "XML"),
    "gather":           ("XML", "XML"),
    "filter":           ("XML", "XML"),
    "xml_to_json":      ("XML", "JSON"),
    "json_to_xml":      ("JSON", "XML"),
    "xml_to_csv":       ("XML", "CSV"),
    "csv_to_xml":       ("CSV", "XML"),
    "xslt_to_csv":      ("XML", "CSV"),
    "xslt_to_json":     ("XML", "JSON"),
}


def validate_step_chain(kinds, seed_format: str = "XML"):
    """Walk a linear list of step kinds and confirm each step receives a body
    format it accepts, starting from `seed_format`. Returns (ok, errors).
    A format-agnostic step ("ANY" output) passes the *current* format through
    unchanged, so it never breaks the chain. Raises nothing — pure check."""
    errors = []
    cur = seed_format
    for i, k in enumerate(kinds):
        in_fmt, out_fmt = _STEP_IO.get(k, ("ANY", "ANY"))
        if in_fmt != "ANY" and in_fmt != cur:
            errors.append(
                f"step {i} ({k}) needs {in_fmt} but body is {cur} "
                f"(insert a converter before it)")
        cur = cur if out_fmt == "ANY" else out_fmt
    return (not errors), errors


# Middle step kinds that render as a callActivity in the linear chain. Each maps
# to a decoded builder above; unknown kinds fall back to a Content Modifier so
# the flow is always valid.
_MIDDLE_KINDS = {"content_modifier", "script", "mapping",
                 "splitter", "gather", "filter",
                 "xml_to_json", "json_to_xml", "xml_to_csv", "csv_to_xml",
                 "xslt_to_csv", "xslt_to_json", "external_call", "process_call"}

# Single-step ("linear") constructs: one flow node carrying an activityType +
# cmdVariant, no special topology — exactly like the converters. Decoded from
# the real corpus (element tag + cmdVariantUri verbatim). gen-kind == the
# parser's activityType, so they round-trip 1:1.
_PASSTHROUGH = {
    "Encoder":               ("callActivity", "ctype::FlowstepVariant/cname::Base64 Encode/version::1.0.1"),
    "Decoder":               ("callActivity", "ctype::FlowstepVariant/cname::Base64 Decode/version::1.0.1"),
    "DBstorage":             ("callActivity", "ctype::FlowstepVariant/cname::put/version::1.7.1"),
    "XMLDigitalSignMessage": ("callActivity", "ctype::FlowstepVariant/cname::XMLDigitalSignMessage/version::1.2.0"),
    "SimpleSignMessage":     ("callActivity", "ctype::FlowstepVariant/cname::SimpleSignMessage"),
    "Send":                  ("serviceTask",  "ctype::FlowstepVariant/cname::Send/version::1.0.4"),
    "Variables":             ("callActivity", "ctype::FlowstepVariant/cname::Variables/version::1.2.0"),
    "XmlModifier":           ("callActivity", "ctype::FlowstepVariant/cname::XmlModifier/version::1.1.0"),
    "contentEnricherWithLookup": ("serviceTask", "ctype::FlowstepVariant/cname::contentEnricherWithLookup/version::1.2.0"),
    "Persist":               ("callActivity", "ctype::FlowstepVariant/cname::Persist"),
    "XmlValidator":          ("callActivity", "ctype::FlowstepVariant/cname::XmlValidator/version::2.1.0"),
}
_MIDDLE_KINDS |= set(_PASSTHROUGH)


def _passthrough_step(step_id, name, incoming, outgoing, activity_type, cmd_variant,
                      tag="callActivity", config=None):
    """A single flow node carrying an activityType + cmdVariant (no extra
    topology). Round-trips to `activity_type`. When `config` (the real step's
    captured properties) is supplied, every property is re-emitted verbatim so
    the step is fully specified for CPI; otherwise the two structural
    properties are emitted (synthetic fallback)."""
    if config:
        props = "".join(
            f'                <ifl:property><key>{html.escape(str(k))}</key>'
            f'<value>{html.escape("" if v is None else str(v))}</value></ifl:property>\n'
            for k, v in config.items())
    else:
        props = (
            f'                <ifl:property><key>activityType</key><value>{activity_type}</value></ifl:property>\n'
            f'                <ifl:property><key>cmdVariantUri</key><value>{cmd_variant}</value></ifl:property>\n')
    return (
        f'        <bpmn2:{tag} id="{step_id}" name="{html.escape(name)}">\n'
        f'            <bpmn2:extensionElements>\n'
        f'{props}'
        f'            </bpmn2:extensionElements>\n'
        f'            <bpmn2:incoming>{incoming}</bpmn2:incoming>\n'
        f'            <bpmn2:outgoing>{outgoing}</bpmn2:outgoing>\n'
        f'        </bpmn2:{tag}>\n')


def _build_middle_step(kind: str, step_id: str, spec: dict,
                       incoming: str, outgoing: str):
    """Dispatch one middle-step spec to its decoded builder.
    Returns (xml, extra_files) where extra_files maps bundle path -> content
    (Script/Mapping ship a referenced file; the rest ship nothing)."""
    name = spec.get("name", step_id)
    files = {}
    if kind in _PASSTHROUGH:
        tag, cmd = _PASSTHROUGH[kind]
        xml = _passthrough_step(step_id, name, incoming, outgoing,
                                activity_type=kind, cmd_variant=cmd, tag=tag,
                                config=spec.get("config"))
        return xml, files
    if kind == "script":
        fn = spec.get("function", "processData")
        fname = spec.get("script_file") or (_sanitize_id(name) + ".groovy")
        xml = _script_step(step_id, name, fname, fn, incoming, outgoing)
        files[f"src/main/resources/script/{fname}"] = _groovy_body(fn)
    elif kind == "mapping":
        mname = spec.get("mapping_name") or _sanitize_id(name)
        xml = _mapping_step(step_id, name, mname, incoming, outgoing)
        files[f"src/main/resources/mapping/{mname}.xsl"] = _xslt_identity_body()
    elif kind in ("xslt_to_csv", "xslt_to_json"):
        mname = spec.get("mapping_name") or _sanitize_id(name)
        xml = _mapping_step(step_id, name, mname, incoming, outgoing)
        files[f"src/main/resources/mapping/{mname}.xsl"] = (
            _xslt_to_csv_body() if kind == "xslt_to_csv" else _xslt_to_json_body())
    elif kind == "splitter":
        if spec.get("config"):
            xml = _passthrough_step(step_id, name, incoming, outgoing,
                                    activity_type="Splitter", cmd_variant="",
                                    tag="callActivity", config=spec["config"])
        else:
            xml = _splitter_step(step_id, name, spec.get("xpath", "/root/Record"),
                                 incoming, outgoing)
    elif kind == "gather":
        xml = _gather_step(step_id, name, incoming, outgoing)
    elif kind == "filter":
        if spec.get("config"):
            xml = _passthrough_step(step_id, name, incoming, outgoing,
                                    activity_type="Filter", cmd_variant="",
                                    tag="callActivity", config=spec["config"])
        else:
            xml = _filter_step(step_id, name, spec.get("xpath", "/*"),
                               incoming, outgoing)
    elif kind == "xml_to_json":
        xml = _xml_to_json_step(step_id, name, incoming, outgoing)
    elif kind == "json_to_xml":
        xml = _json_to_xml_step(step_id, name, incoming, outgoing)
    elif kind == "xml_to_csv":
        xml = _xml_to_csv_step(step_id, name, spec.get("xpath", "/root/row"),
                               incoming, outgoing)
    elif kind == "csv_to_xml":
        xml = _csv_to_xml_step(step_id, name, spec.get("xpath", "d/results"),
                               schema_path="/xsd/CsvTarget.xsd",
                               incoming=incoming, outgoing=outgoing)
        files["src/main/resources/xsd/CsvTarget.xsd"] = _csv_target_xsd()
    elif kind == "process_call":
        # Re-emit the ProcessCall verbatim (processId targets the real local
        # integration process emitted alongside the main process).
        xml = _passthrough_step(step_id, name, incoming, outgoing,
                                activity_type="ProcessCallElement", cmd_variant="",
                                tag="callActivity", config=spec.get("config"))
    else:  # content_modifier / unknown → proven Enricher
        xml = _content_modifier_step(
            step_id, name, body_expr=spec.get("body", ""),
            headers=spec.get("headers", []), properties=spec.get("properties", []),
            incoming=incoming, outgoing=outgoing)
    return xml, files


def build_timer_two_cm_iflw(iflow_id: str, name: str,
                            cm1_properties: list, cm1_headers: list,
                            cm2_body: str) -> str:
    """Timer-triggered pilot: Timer → Content Modifier (set properties/headers)
    → Content Modifier (enhance/set body) → End. No sender, no receiver — the
    structure proven to deploy on the user's tenant. Same verified envelope
    (manifest/diagram/cmdVariantUri); timer + CM blocks decoded from real iFlows."""
    # Full collaboration property set cloned from the proven-deployable minimal
    # timer iFlow (SetURLAsGlobalVariable) — CORS/header keys present but empty;
    # omitting them is a likely cause of the editor model-loader 500.
    collab_props = (
        _ifl("namespaceMapping") +
        _ifl("httpSessionHandling", "None") +
        _ifl("accessControlMaxAge") +
        _ifl("returnExceptionToSender", "false") +
        _ifl("log", "All events") +
        _ifl("corsEnabled") +
        _ifl("exposedHeaders") +
        _ifl("componentVersion", "1.2") +
        _ifl("allowedHeaderList") +
        _ifl("ServerTrace", "false") +
        _ifl("allowedOrigins") +
        _ifl("accessControlAllowCredentials") +
        _ifl("allowedHeaders") +
        _ifl("allowedMethods") +
        _ifl("cmdVariantUri", "ctype::IFlowVariant/cname::IFlowConfiguration/version::1.2.3")
    )
    timer = _timer_start_event(outgoing="SequenceFlow_3")
    cm1 = _content_modifier_step("CallActivity_1", "Set Parameters",
                                 body_expr="", headers=cm1_headers,
                                 properties=cm1_properties,
                                 incoming="SequenceFlow_3", outgoing="SequenceFlow_4")
    cm2 = _content_modifier_step("CallActivity_2", "Enhance Message",
                                 body_expr=cm2_body,
                                 incoming="SequenceFlow_4", outgoing="SequenceFlow_5")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
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
{timer}{cm1}{cm2}        <bpmn2:endEvent id="EndEvent_2" name="End">
            <bpmn2:extensionElements>
                <ifl:property><key>componentVersion</key><value>1.1</value></ifl:property>
                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::MessageEndEvent/version::1.1.0</value></ifl:property>
            </bpmn2:extensionElements>
            <bpmn2:incoming>SequenceFlow_5</bpmn2:incoming>
            <bpmn2:messageEventDefinition/>
        </bpmn2:endEvent>
        <bpmn2:sequenceFlow id="SequenceFlow_3" sourceRef="StartEvent_2" targetRef="CallActivity_1"/>
        <bpmn2:sequenceFlow id="SequenceFlow_4" sourceRef="CallActivity_1" targetRef="CallActivity_2"/>
        <bpmn2:sequenceFlow id="SequenceFlow_5" sourceRef="CallActivity_2" targetRef="EndEvent_2"/>
    </bpmn2:process>
    <bpmndi:BPMNDiagram id="BPMNDiagram_1" name="Default Collaboration Diagram">
        <bpmndi:BPMNPlane bpmnElement="Collaboration_1" id="BPMNPlane_1">
            <bpmndi:BPMNShape bpmnElement="Participant_Process_1" id="BPMNShape_Participant_Process_1">
                <dc:Bounds height="200.0" width="700.0" x="240.0" y="80.0"/>
            </bpmndi:BPMNShape>
            <bpmndi:BPMNShape bpmnElement="StartEvent_2" id="BPMNShape_StartEvent_2">
                <dc:Bounds height="32.0" width="32.0" x="290.0" y="150.0"/>
            </bpmndi:BPMNShape>
            <bpmndi:BPMNShape bpmnElement="CallActivity_1" id="BPMNShape_CallActivity_1">
                <dc:Bounds height="60.0" width="100.0" x="400.0" y="136.0"/>
            </bpmndi:BPMNShape>
            <bpmndi:BPMNShape bpmnElement="CallActivity_2" id="BPMNShape_CallActivity_2">
                <dc:Bounds height="60.0" width="100.0" x="580.0" y="136.0"/>
            </bpmndi:BPMNShape>
            <bpmndi:BPMNShape bpmnElement="EndEvent_2" id="BPMNShape_EndEvent_2">
                <dc:Bounds height="32.0" width="32.0" x="780.0" y="150.0"/>
            </bpmndi:BPMNShape>
            <bpmndi:BPMNEdge bpmnElement="SequenceFlow_3" id="BPMNEdge_SequenceFlow_3" sourceElement="BPMNShape_StartEvent_2" targetElement="BPMNShape_CallActivity_1">
                <di:waypoint x="322.0" xsi:type="dc:Point" y="166.0"/>
                <di:waypoint x="400.0" xsi:type="dc:Point" y="166.0"/>
            </bpmndi:BPMNEdge>
            <bpmndi:BPMNEdge bpmnElement="SequenceFlow_4" id="BPMNEdge_SequenceFlow_4" sourceElement="BPMNShape_CallActivity_1" targetElement="BPMNShape_CallActivity_2">
                <di:waypoint x="500.0" xsi:type="dc:Point" y="166.0"/>
                <di:waypoint x="580.0" xsi:type="dc:Point" y="166.0"/>
            </bpmndi:BPMNEdge>
            <bpmndi:BPMNEdge bpmnElement="SequenceFlow_5" id="BPMNEdge_SequenceFlow_5" sourceElement="BPMNShape_CallActivity_2" targetElement="BPMNShape_EndEvent_2">
                <di:waypoint x="680.0" xsi:type="dc:Point" y="166.0"/>
                <di:waypoint x="780.0" xsi:type="dc:Point" y="166.0"/>
            </bpmndi:BPMNEdge>
        </bpmndi:BPMNPlane>
    </bpmndi:BPMNDiagram>
</bpmn2:definitions>
"""


# ── Consultant-defined structure: parse a path + wire it linearly ───────────
# Canonical step types the structure wirer can build TODAY, each mapped to a
# proven step builder. request_reply is recognized but NOT yet wireable (it
# needs a receiver participant + message flow — the endpoint path that isn't
# built yet), so the parser drops it with a note (matches "ignore request-reply").
_STRUCTURE_ALIASES = {
    "timer": "timer", "scheduler": "timer", "start": "timer", "timer start": "timer",
    "start timer": "timer",
    "content modifier": "content_modifier", "contentmodifier": "content_modifier",
    "cm": "content_modifier", "content_modifier": "content_modifier",
    "modifier": "content_modifier", "set body": "content_modifier",
    "end": "end", "end event": "end", "end message": "end",
    "request reply": "request_reply", "request-reply": "request_reply",
    "requestreply": "request_reply", "request_reply": "request_reply",
    "call": "request_reply",
}
_SUPPORTED_STRUCTURE_STEPS = {"timer", "content_modifier", "end"}


def parse_steps_spec(text: str, default_split_xpath: str = "/Orders/Order"):
    """Parse an explicit step pipeline written in CPI palette vocabulary into the
    mid_specs the wirer consumes. Steps are separated by '|' (also newline / ';').
    Each token is '<Step Type>[: <custom name>]', e.g.
      'Content Modifier: Seed Body | Message Mapping: Normalize | Script: Reformat'
      ' | Splitter | XML to JSON Converter | JSON to XML Converter | Gather'
    Unknown step types fall back to a Content Modifier so the flow stays valid.
    Returns (mid_specs, kinds)."""
    import re as _re
    alias = {
        "content modifier": "content_modifier", "contentmodifier": "content_modifier",
        "cm": "content_modifier", "enricher": "content_modifier",
        "message mapping": "mapping", "mapping": "mapping",
        "xslt mapping": "mapping", "xslt": "mapping",
        "script": "script", "groovy": "script", "groovy script": "script",
        "splitter": "splitter", "split": "splitter", "general splitter": "splitter",
        "gather": "gather", "filter": "filter",
        "xml to json converter": "xml_to_json", "xml to json": "xml_to_json",
        "json to xml converter": "json_to_xml", "json to xml": "json_to_xml",
        "xml to csv converter": "xml_to_csv", "xml to csv": "xml_to_csv",
        "csv to xml converter": "csv_to_xml", "csv to xml": "csv_to_xml",
        "xslt to csv": "xslt_to_csv", "mapping to csv": "xslt_to_csv",
        "xslt mapping to csv": "xslt_to_csv",
        "xslt to json": "xslt_to_json", "mapping to json": "xslt_to_json",
        "xslt mapping to json": "xslt_to_json",
    }
    pretty = {
        "content_modifier": "Content Modifier", "mapping": "Message Mapping",
        "script": "Script", "splitter": "Splitter", "gather": "Gather",
        "filter": "Filter", "xml_to_json": "XML to JSON Converter",
        "json_to_xml": "JSON to XML Converter", "xml_to_csv": "XML to CSV Converter",
        "csv_to_xml": "CSV to XML Converter", "xslt_to_csv": "XSLT Mapping (to CSV)",
        "xslt_to_json": "XSLT Mapping (to JSON)",
    }
    specs, kinds = [], []
    tokens = [t.strip() for t in _re.split(r"[|;\n]", text or "") if t.strip()]
    n = 0
    for tok in tokens:
        label = ""
        if ":" in tok:
            typ, label = tok.split(":", 1)
            typ, label = typ.strip(), label.strip()
        else:
            typ = tok.strip()
        kind = alias.get(_re.sub(r"[()]", "", typ).strip().lower().replace("_", " "))
        if kind in (None, "timer", "start", "end"):
            if typ.lower() in ("start", "timer", "start timer", "end", "end event"):
                continue  # start/end are added by the wirer
            kind = "content_modifier"  # unknown → safe CM
        n += 1
        spec = {"kind": kind, "name": label or f"{pretty.get(kind, kind)} {n}"}
        if kind in ("mapping", "xslt_to_csv", "xslt_to_json"):
            spec["mapping_name"] = _sanitize_id(spec["name"])
        if kind == "splitter":
            spec["xpath"] = default_split_xpath
        specs.append(spec)
        kinds.append(kind)
    return specs, kinds


def parse_consultant_structure(text: str):
    """Parse a consultant-written path like
    'timer -> content modifier -> request-reply -> end' into an ordered list of
    canonical step dicts. Separators: -> => , ; newline or the → arrow.
    Unsupported steps (e.g. request-reply, which needs the unbuilt receiver
    path) are dropped and reported. A timer start and an end event are always
    ensured (first/last). Returns (steps, notes)."""
    import re as _re
    raw = [t.strip() for t in _re.split(r"->|=>|→|[,;\n]", text or "") if t.strip()]
    mids, notes = [], []
    for tok in raw:
        canon = _STRUCTURE_ALIASES.get(tok.lower())
        if canon is None:
            notes.append(f"Unrecognized step '{tok}' — skipped.")
        elif canon == "request_reply":
            notes.append(
                f"'{tok}' (request-reply) needs a receiver + message flow — the "
                "endpoint path that isn't built yet — so it was ignored.")
        elif canon in ("timer", "end"):
            pass  # start/end are positional; added below
        else:
            mids.append({"type": canon})
    if not mids:
        notes.append("No content modifier in the structure — added one so the "
                     "timer has a step to run.")
        mids = [{"type": "content_modifier"}]
    steps = [{"type": "timer"}] + mids + [{"type": "end"}]
    return steps, notes


def build_flow_from_steps(iflow_id: str, name: str, mid_specs: list):
    """Wire a linear iFlow Timer → <middle steps> → End from an ordered list of
    middle-step specs, reusing the proven collaboration/process envelope and the
    decoded step builders. Each mid spec is a dict with a "kind"
    (content_modifier/script/mapping/splitter/gather/filter) plus kind-specific
    fields. Every middle step is a callActivity, so the accepted bundle shape is
    unchanged from the proven Content-Modifier flow; Script/Mapping additionally
    contribute a referenced resource file.
    Returns (iflw_xml, extra_files) where extra_files maps bundle path ->
    content for any files the steps reference (scripts, mappings)."""
    mid_specs = mid_specs or []
    collab_props = (
        _ifl("namespaceMapping") + _ifl("httpSessionHandling", "None") +
        _ifl("accessControlMaxAge") + _ifl("returnExceptionToSender", "false") +
        _ifl("log", "All events") + _ifl("corsEnabled") + _ifl("exposedHeaders") +
        _ifl("componentVersion", "1.2") + _ifl("allowedHeaderList") +
        _ifl("ServerTrace", "false") + _ifl("allowedOrigins") +
        _ifl("accessControlAllowCredentials") + _ifl("allowedHeaders") +
        _ifl("allowedMethods") +
        _ifl("cmdVariantUri",
             "ctype::IFlowVariant/cname::IFlowConfiguration/version::1.2.3"))

    n_mid = len(mid_specs)
    node_ids = (["StartEvent_2"] +
                [f"CallActivity_{i+1}" for i in range(n_mid)] + ["EndEvent_2"])
    flow_ids = [f"SequenceFlow_{3+i}" for i in range(len(node_ids) - 1)]

    timer = _timer_start_event(outgoing=flow_ids[0])
    cm_xml = ""
    extra_files = {}
    receivers = []      # external_call steps → receiver participant + message flow
    for i in range(n_mid):
        spec = dict(mid_specs[i] or {})
        spec.setdefault("name", f"Step {i+1}")
        kind = spec.get("kind", "content_modifier")
        nid = f"CallActivity_{i+1}"
        if kind == "external_call":
            from scaffolder.external_call_iflow import _external_call_task
            cm_xml += _external_call_task(
                nid, spec.get("name", "Request-Reply"),
                incoming=flow_ids[i], outgoing=flow_ids[i + 1])
            ridx = len(receivers) + 1
            receivers.append({
                "mid": i, "step_id": nid,
                "participant_id": f"Participant_Recv_{ridx}",
                "mf_id": f"MessageFlow_R{ridx}",
                "name": spec.get("receiver_name") or "Receiver",
                "address": spec.get("address") or "https://example.com/api",
                "method": spec.get("http_method", "POST"),
                "mf_props": spec.get("mf_props") or {},
                "mf_name": spec.get("mf_name") or "",
            })
        else:
            xml_i, files_i = _build_middle_step(
                kind, nid, spec,
                incoming=flow_ids[i], outgoing=flow_ids[i + 1])
            cm_xml += xml_i
            extra_files.update(files_i)

    seq_xml = "".join(
        f'        <bpmn2:sequenceFlow id="{fid}" sourceRef="{node_ids[i]}" '
        f'targetRef="{node_ids[i+1]}"/>\n'
        for i, fid in enumerate(flow_ids))

    # Diagram: linear layout. start/end 32×32 @y150, CMs 100×60 @y136.
    xs = [290] + [400 + 180 * i for i in range(n_mid)] + [400 + 180 * n_mid + 100]
    # Pool must span from just left of the start event to just past the end
    # event, or the steps overflow the Integration Process boundary.
    pool_w = xs[-1] - 180
    shapes = ""
    for nid, x in zip(node_ids, xs):
        if nid.startswith("CallActivity"):
            shapes += (f'            <bpmndi:BPMNShape bpmnElement="{nid}" '
                       f'id="BPMNShape_{nid}"><dc:Bounds height="60.0" '
                       f'width="100.0" x="{x}.0" y="136.0"/></bpmndi:BPMNShape>\n')
        else:
            shapes += (f'            <bpmndi:BPMNShape bpmnElement="{nid}" '
                       f'id="BPMNShape_{nid}"><dc:Bounds height="32.0" '
                       f'width="32.0" x="{x}.0" y="150.0"/></bpmndi:BPMNShape>\n')
    edges = ""
    for i, fid in enumerate(flow_ids):
        sx = xs[i] + (32 if node_ids[i].startswith("StartEvent") else 100)
        tx = xs[i + 1]
        edges += (f'            <bpmndi:BPMNEdge bpmnElement="{fid}" '
                  f'id="BPMNEdge_{fid}" sourceElement="BPMNShape_{node_ids[i]}" '
                  f'targetElement="BPMNShape_{node_ids[i+1]}">'
                  f'<di:waypoint x="{sx}.0" xsi:type="dc:Point" y="166.0"/>'
                  f'<di:waypoint x="{tx}.0" xsi:type="dc:Point" y="166.0"/>'
                  f'</bpmndi:BPMNEdge>\n')

    # ExternalCall receivers: a receiver participant + message flow per call,
    # laid out below the pool. No external_call steps → all blank (the linear
    # envelope is byte-for-byte unchanged).
    import html as _html
    from scaffolder.external_call_iflow import _http_message_flow, _message_flow_from_props
    recv_participants = "".join(
        f'        <bpmn2:participant id="{r["participant_id"]}" '
        f'ifl:type="EndpointRecevier" name="{_html.escape(r["name"])}">\n'
        f'            <bpmn2:extensionElements/>\n        </bpmn2:participant>\n'
        for r in receivers)
    recv_msgflows = "".join(
        (_message_flow_from_props(r["mf_id"], r["step_id"], r["participant_id"],
                                  r["mf_props"], r["mf_name"])
         or _http_message_flow(r["mf_id"], r["step_id"], r["participant_id"],
                               r["address"], r["method"]))
        for r in receivers)
    recv_di = ""
    for r in receivers:
        rx = xs[r["mid"] + 1]      # under the step's column
        recv_di += (
            f'            <bpmndi:BPMNShape bpmnElement="{r["participant_id"]}" '
            f'id="BPMNShape_{r["participant_id"]}"><dc:Bounds height="60.0" '
            f'width="100.0" x="{rx}.0" y="330.0"/></bpmndi:BPMNShape>\n'
            f'            <bpmndi:BPMNEdge bpmnElement="{r["mf_id"]}" '
            f'id="BPMNEdge_{r["mf_id"]}" sourceElement="BPMNShape_{r["step_id"]}" '
            f'targetElement="BPMNShape_{r["participant_id"]}">'
            f'<di:waypoint x="{rx+50}.0" xsi:type="dc:Point" y="196.0"/>'
            f'<di:waypoint x="{rx+50}.0" xsi:type="dc:Point" y="330.0"/>'
            f'</bpmndi:BPMNEdge>\n')

    definitions = f"""<?xml version="1.0" encoding="UTF-8"?>
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
{timer}{cm_xml}        <bpmn2:endEvent id="EndEvent_2" name="End">
            <bpmn2:extensionElements>
                <ifl:property><key>componentVersion</key><value>1.1</value></ifl:property>
                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::MessageEndEvent/version::1.1.0</value></ifl:property>
            </bpmn2:extensionElements>
            <bpmn2:incoming>{flow_ids[-1]}</bpmn2:incoming>
            <bpmn2:messageEventDefinition/>
        </bpmn2:endEvent>
{seq_xml}    </bpmn2:process>
    <bpmndi:BPMNDiagram id="BPMNDiagram_1" name="Default Collaboration Diagram">
        <bpmndi:BPMNPlane bpmnElement="Collaboration_1" id="BPMNPlane_1">
            <bpmndi:BPMNShape bpmnElement="Participant_Process_1" id="BPMNShape_Participant_Process_1">
                <dc:Bounds height="200.0" width="{pool_w}.0" x="240.0" y="80.0"/>
            </bpmndi:BPMNShape>
{shapes}{edges}        </bpmndi:BPMNPlane>
    </bpmndi:BPMNDiagram>
</bpmn2:definitions>
"""
    if receivers:
        # additive: receiver participants + message flows into the collaboration,
        # receiver shapes + edges into the diagram plane (local string, so this
        # touches only this flow — the no-receiver path is byte-identical).
        definitions = definitions.replace(
            "    </bpmn2:collaboration>",
            recv_participants + recv_msgflows + "    </bpmn2:collaboration>", 1)
        definitions = definitions.replace(
            "        </bpmndi:BPMNPlane>",
            recv_di + "        </bpmndi:BPMNPlane>", 1)
    return definitions, extra_files


def build_linear_iflw(iflow_id: str, name: str, steps: list,
                      cm_specs: Optional[list] = None) -> str:
    """Backward-compatible linear wirer (timer → CM* → end). Derives middle-step
    specs from `steps` + `cm_specs` and delegates to build_flow_from_steps,
    returning only the iFlw XML (CM-only chains reference no extra files). Steps
    whose type is a typed kind (script/mapping/…) are honoured; the consultant
    parser only emits content_modifier, so existing callers are unaffected."""
    cm_specs = cm_specs or []
    mid = []
    idx = 0
    for s in steps:
        st = s.get("type")
        if st in _MIDDLE_KINDS:
            spec = dict(cm_specs[idx]) if idx < len(cm_specs) else {}
            spec.setdefault("kind", st)
            mid.append(spec)
            idx += 1
    xml, _files = build_flow_from_steps(iflow_id, name, mid)
    return xml


def generate_structured_iflow(name: str, structure: str, iflow_id: str = "",
                              cm_specs: Optional[list] = None) -> "MinimalIFlowResult":
    """Build a complete iFlow bundle from a consultant-written `structure`
    string (e.g. 'timer -> content modifier -> end'). Returns a
    MinimalIFlowResult with a `.notes` list describing any steps that were
    dropped (e.g. request-reply). Reuses the proven manifest/.project/bundle."""
    iflow_id = _sanitize_id(iflow_id or name)
    steps, notes = parse_consultant_structure(structure)
    iflw = build_linear_iflw(iflow_id, name, steps, cm_specs=cm_specs)
    manifest = build_manifest(iflow_id, name)
    project = build_project(iflow_id)
    files = {
        "META-INF/MANIFEST.MF": manifest,
        ".project": project,
        f"src/main/resources/scenarioflows/integrationflow/{iflow_id}.iflw": iflw,
    }
    res = MinimalIFlowResult(
        iflow_id=iflow_id, name=name, iflw_xml=iflw,
        manifest=manifest, project_xml=project, files=files)
    try:
        res.notes = notes
    except Exception:
        pass
    return res


def generate_timer_pilot_iflow(name: str, iflow_id: str = "") -> MinimalIFlowResult:
    """Complete Timer → CM → CM → End pilot bundle (in-memory)."""
    iflow_id = _sanitize_id(iflow_id or name)
    cm1_props = [("PilotStatus", "MIGRATED_OK"), ("Source", "PI_PO_Migration")]
    cm1_headers = []   # known-good twin sets only properties here, no headers
    cm2_body = ("<PilotAck><status>${property.PilotStatus}</status>"
                "<source>${property.Source}</source>"
                "<note>S_ContentModifier_SetConstant timer pilot</note></PilotAck>")
    iflw = build_timer_two_cm_iflw(iflow_id, name, cm1_props, cm1_headers, cm2_body)
    manifest = build_manifest(iflow_id, name)
    project = build_project(iflow_id)
    files = {
        "META-INF/MANIFEST.MF": manifest,
        ".project": project,
        f"src/main/resources/scenarioflows/integrationflow/{iflow_id}.iflw": iflw,
    }
    return MinimalIFlowResult(
        iflow_id=iflow_id, name=name, iflw_xml=iflw,
        manifest=manifest, project_xml=project, files=files)


def generate_timer_interface_iflow(name: str, iflow_id: str = "",
                                   properties=None, ack_body: str = None,
                                   note: str = None,
                                   middle_steps: Optional[list] = None,
                                   seed_body: str = None) -> MinimalIFlowResult:
    """Parametrized Timer → CM(set properties) → [middle CMs] → CM(set body) → End.

    This is the proven self-contained shape: no sender, no receiver, no message
    flow, and therefore NO dependency on any standard package or endpoint (the
    entanglement that made the clone-and-adapt artifacts impossible to edit).
    CM1 records migration provenance plus any caller-supplied ``properties``
    (e.g. the PI/PO channel fields); CM2 emits a small marker ack body so the
    run leaves a visible, self-describing trace.

    ``middle_steps``: optional list of descriptive step names inserted between
    CM1 and the ack CM, one Content Modifier each. This is how generated iFlows
    scale with interface complexity — a simple interface gets the bare 2-CM
    shape, a complex one (mapping / multi-mapping / multi-channel / BPM) gets
    extra named steps so the deployed iFlow visibly reflects the work involved.
    All steps are the SAME proven Content-Modifier element that already deploys,
    so adding more does not change the accepted bundle structure.
    Returns a MinimalIFlowResult, same contract as the other generators."""
    iflow_id = _sanitize_id(iflow_id or name)
    base = [("InterfaceName", name),
            ("MigrationStatus", "SCAFFOLDED"),
            ("Source", "PI_PO_Migration")]
    extra = []
    if properties:
        items = properties.items() if isinstance(properties, dict) else properties
        for k, v in items:
            if not k:
                continue
            v = "" if v is None else str(v)
            if v:
                extra.append((str(k), v))
    cm1_props = base + extra
    # body_expr is XML-escaped downstream by _content_modifier_step, so pass raw.
    if ack_body is None:
        note_xml = f"<note>{note}</note>" if note else ""
        ack_body = ("<MigrationAck>"
                    f"<interface>{name}</interface>"
                    "<status>${property.MigrationStatus}</status>"
                    "<source>${property.Source}</source>"
                    f"{note_xml}</MigrationAck>")
    extra_files = {}
    if middle_steps:
        # Complexity-scaled shape: Timer → CM1(props) → [typed steps] → CM(ack) → End.
        # middle_steps entries may be a plain string (→ Content Modifier, the
        # proven default) or a dict carrying a decoded "kind" (script/mapping/
        # splitter/gather/filter) plus its fields. Script/Mapping steps also
        # contribute a referenced resource file, collected into the bundle.
        mid_specs = [{"kind": "content_modifier", "name": "Set Parameters",
                      "properties": cm1_props}]
        for st in middle_steps:
            if isinstance(st, dict):
                spec = dict(st)
                spec.setdefault("kind", "content_modifier")
                spec.setdefault("name", spec.get("kind", "Step").title())
            else:
                spec = {"kind": "content_modifier", "name": str(st)}
            mid_specs.append(spec)
        # XSLT Mapping and Splitter both require an XML message body; a bare
        # timer flow has none, which fails validation ("supports XML input
        # only"). If any such step is present, seed a minimal XML payload in the
        # first Content Modifier so the downstream steps have valid input
        # (identity mapping passes it through; the splitter finds /root/Record).
        if any(s.get("kind") in ("mapping", "splitter", "gather", "filter",
                                 "xml_to_json", "json_to_xml", "xml_to_csv",
                                 "csv_to_xml", "xslt_to_csv", "xslt_to_json")
               for s in mid_specs):
            mid_specs[0]["body"] = seed_body or "<root><Record/></root>"
        mid_specs.append({"kind": "content_modifier",
                          "name": "Build Acknowledgment", "body": ack_body})
        iflw, extra_files = build_flow_from_steps(iflow_id, name, mid_specs)
    else:
        iflw = build_timer_two_cm_iflw(iflow_id, name, cm1_props, [], ack_body)
    manifest = build_manifest(iflow_id, name)
    project = build_project(iflow_id)
    files = {
        "META-INF/MANIFEST.MF": manifest,
        ".project": project,
        f"src/main/resources/scenarioflows/integrationflow/{iflow_id}.iflw": iflw,
    }
    files.update(extra_files)
    return MinimalIFlowResult(
        iflow_id=iflow_id, name=name, iflw_xml=iflw,
        manifest=manifest, project_xml=project, files=files)


def build_content_modifier_iflw(iflow_id: str, name: str, body_expr: str,
                                headers: Optional[list] = None) -> str:
    """Minimal valid iFlow with ONE Content Modifier between start and end:
    HTTPS sender → start → Content Modifier (sets body/headers) → end. Same
    verified envelope as build_iflw (manifest/diagram/cmdVariantUri); the only
    addition is the Enricher step + its sequence flow + diagram shape/edge."""
    collab_props = (
        _ifl("namespaceMapping") +
        _ifl("httpSessionHandling", "None") +
        _ifl("returnExceptionToSender", "false") +
        _ifl("log", "All events") +
        _ifl("componentVersion", "1.2") +
        _ifl("ServerTrace", "false") +
        _ifl("cmdVariantUri", "ctype::IFlowVariant/cname::IFlowConfiguration/version::1.2.4")
    )
    msgflow = f"""        <bpmn2:messageFlow id="MessageFlow_10" name="HTTPS" sourceRef="Participant_1" targetRef="StartEvent_2">
            <bpmn2:extensionElements>
                <ifl:property><key>ComponentType</key><value>HTTPS</value></ifl:property>
                <ifl:property><key>Description</key><value/></ifl:property>
                <ifl:property><key>ComponentNS</key><value>sap</value></ifl:property>
                <ifl:property><key>urlPath</key><value>/{iflow_id.lower()}</value></ifl:property>
                <ifl:property><key>senderAuthType</key><value>RoleBased</value></ifl:property>
                <ifl:property><key>userRole</key><value>ESBMessaging.send</value></ifl:property>
                <ifl:property><key>componentVersion</key><value>1.4</value></ifl:property>
                <ifl:property><key>ComponentSWCVName</key><value>1.4.1</value></ifl:property>
                <ifl:property><key>Name</key><value>HTTPS</value></ifl:property>
                <ifl:property><key>TransportProtocol</key><value>HTTPS</value></ifl:property>
                <ifl:property><key>TransportProtocolVersion</key><value>1.4.1</value></ifl:property>
                <ifl:property><key>cmdVariantUri</key><value>ctype::AdapterVariant/cname::sap:HTTPS/tp::HTTPS/mp::None/direction::Sender/version::1.4.1</value></ifl:property>
                <ifl:property><key>MessageProtocol</key><value>None</value></ifl:property>
                <ifl:property><key>MessageProtocolVersion</key><value>1.4.1</value></ifl:property>
                <ifl:property><key>maximumBodySize</key><value>40</value></ifl:property>
                <ifl:property><key>direction</key><value>Sender</value></ifl:property>
                <ifl:property><key>system</key><value>Sender</value></ifl:property>
            </bpmn2:extensionElements>
        </bpmn2:messageFlow>
"""
    cm = _content_modifier_step("CallActivity_1", "Set Constant", body_expr, headers)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn2:definitions xmlns:bpmn2="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" xmlns:ifl="http:///com.sap.ifl.model/Ifl.xsd" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" id="Definitions_1">
    <bpmn2:collaboration id="Collaboration_1" name="Default Collaboration">
        <bpmn2:extensionElements>
{collab_props}        </bpmn2:extensionElements>
        <bpmn2:participant id="Participant_1" ifl:type="EndpointSender" name="Sender">
            <bpmn2:extensionElements>
                <ifl:property><key>enableBasicAuthentication</key><value>false</value></ifl:property>
                <ifl:property><key>ifl:type</key><value>EndpointSender</value></ifl:property>
            </bpmn2:extensionElements>
        </bpmn2:participant>
        <bpmn2:participant id="Participant_Process_1" ifl:type="IntegrationProcess" name="Integration Process" processRef="Process_1">
            <bpmn2:extensionElements/>
        </bpmn2:participant>
{msgflow}    </bpmn2:collaboration>
    <bpmn2:process id="Process_1" name="Integration Process">
        <bpmn2:extensionElements>
            <ifl:property><key>transactionTimeout</key><value>30</value></ifl:property>
            <ifl:property><key>componentVersion</key><value>1.2</value></ifl:property>
            <ifl:property><key>cmdVariantUri</key><value>ctype::FlowElementVariant/cname::IntegrationProcess/version::1.2.1</value></ifl:property>
            <ifl:property><key>transactionalHandling</key><value>Not Required</value></ifl:property>
        </bpmn2:extensionElements>
        <bpmn2:startEvent id="StartEvent_2" name="Start">
            <bpmn2:extensionElements>
                <ifl:property><key>componentVersion</key><value>1.0</value></ifl:property>
                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::MessageStartEvent/version::1.0</value></ifl:property>
            </bpmn2:extensionElements>
            <bpmn2:outgoing>SequenceFlow_3</bpmn2:outgoing>
            <bpmn2:messageEventDefinition/>
        </bpmn2:startEvent>
{cm}        <bpmn2:endEvent id="EndEvent_2" name="End">
            <bpmn2:extensionElements>
                <ifl:property><key>componentVersion</key><value>1.1</value></ifl:property>
                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowstepVariant/cname::MessageEndEvent/version::1.1.0</value></ifl:property>
            </bpmn2:extensionElements>
            <bpmn2:incoming>SequenceFlow_4</bpmn2:incoming>
            <bpmn2:messageEventDefinition/>
        </bpmn2:endEvent>
        <bpmn2:sequenceFlow id="SequenceFlow_3" sourceRef="StartEvent_2" targetRef="CallActivity_1"/>
        <bpmn2:sequenceFlow id="SequenceFlow_4" sourceRef="CallActivity_1" targetRef="EndEvent_2"/>
    </bpmn2:process>
    <bpmndi:BPMNDiagram id="BPMNDiagram_1" name="Default Collaboration Diagram">
        <bpmndi:BPMNPlane bpmnElement="Collaboration_1" id="BPMNPlane_1">
            <bpmndi:BPMNShape bpmnElement="Participant_1" id="BPMNShape_Participant_1">
                <dc:Bounds height="140.0" width="100.0" x="40.0" y="100.0"/>
            </bpmndi:BPMNShape>
            <bpmndi:BPMNShape bpmnElement="Participant_Process_1" id="BPMNShape_Participant_Process_1">
                <dc:Bounds height="220.0" width="640.0" x="250.0" y="60.0"/>
            </bpmndi:BPMNShape>
            <bpmndi:BPMNShape bpmnElement="StartEvent_2" id="BPMNShape_StartEvent_2">
                <dc:Bounds height="32.0" width="32.0" x="292.0" y="142.0"/>
            </bpmndi:BPMNShape>
            <bpmndi:BPMNShape bpmnElement="CallActivity_1" id="BPMNShape_CallActivity_1">
                <dc:Bounds height="60.0" width="100.0" x="480.0" y="128.0"/>
            </bpmndi:BPMNShape>
            <bpmndi:BPMNShape bpmnElement="EndEvent_2" id="BPMNShape_EndEvent_2">
                <dc:Bounds height="32.0" width="32.0" x="803.0" y="142.0"/>
            </bpmndi:BPMNShape>
            <bpmndi:BPMNEdge bpmnElement="SequenceFlow_3" id="BPMNEdge_SequenceFlow_3" sourceElement="BPMNShape_StartEvent_2" targetElement="BPMNShape_CallActivity_1">
                <di:waypoint x="324.0" xsi:type="dc:Point" y="158.0"/>
                <di:waypoint x="480.0" xsi:type="dc:Point" y="158.0"/>
            </bpmndi:BPMNEdge>
            <bpmndi:BPMNEdge bpmnElement="SequenceFlow_4" id="BPMNEdge_SequenceFlow_4" sourceElement="BPMNShape_CallActivity_1" targetElement="BPMNShape_EndEvent_2">
                <di:waypoint x="580.0" xsi:type="dc:Point" y="158.0"/>
                <di:waypoint x="803.0" xsi:type="dc:Point" y="158.0"/>
            </bpmndi:BPMNEdge>
            <bpmndi:BPMNEdge bpmnElement="MessageFlow_10" id="BPMNEdge_MessageFlow_10" sourceElement="BPMNShape_Participant_1" targetElement="BPMNShape_StartEvent_2">
                <di:waypoint x="90.0" xsi:type="dc:Point" y="170.0"/>
                <di:waypoint x="308.0" xsi:type="dc:Point" y="158.0"/>
            </bpmndi:BPMNEdge>
        </bpmndi:BPMNPlane>
    </bpmndi:BPMNDiagram>
</bpmn2:definitions>
"""


def generate_content_modifier_iflow(name: str, iflow_id: str = "",
                                    body_expr: str = "", headers=None
                                    ) -> MinimalIFlowResult:
    """Complete minimal Content Modifier iFlow bundle (in-memory)."""
    iflow_id = _sanitize_id(iflow_id or name)
    iflw = build_content_modifier_iflw(iflow_id, name, body_expr, headers)
    manifest = build_manifest(iflow_id, name)
    project = build_project(iflow_id)
    files = {
        "META-INF/MANIFEST.MF": manifest,
        ".project": project,
        f"src/main/resources/scenarioflows/integrationflow/{iflow_id}.iflw": iflw,
    }
    return MinimalIFlowResult(
        iflow_id=iflow_id, name=name, iflw_xml=iflw,
        manifest=manifest, project_xml=project, files=files)


def generate_minimal_iflow(name: str, iflow_id: str = "") -> MinimalIFlowResult:
    """Generate a complete minimal valid iFlow bundle (in-memory)."""
    iflow_id = _sanitize_id(iflow_id or name)
    iflw = build_iflw(iflow_id, name)
    manifest = build_manifest(iflow_id, name)
    project = build_project(iflow_id)
    files = {
        "META-INF/MANIFEST.MF": manifest,
        ".project": project,
        f"src/main/resources/scenarioflows/integrationflow/{iflow_id}.iflw": iflw,
    }
    return MinimalIFlowResult(
        iflow_id=iflow_id, name=name, iflw_xml=iflw,
        manifest=manifest, project_xml=project, files=files)


def build_bundle_zip(result: MinimalIFlowResult) -> bytes:
    """Zip the bundle into the artifact format CPI accepts.

    Every real importable iFlow bundle carries both src/main/resources/
    parameters.prop and parameters.propdef. CPI's OData create reads
    parameters.propdef by name; if it's absent the create fails with HTTP 500
    "InputStream cannot be null". A flow with no externalized parameters still
    needs an empty <parameters/> definition, so we inject both when missing.
    """
    _EMPTY_PROPDEF = ('<?xml version="1.0" encoding="UTF-8" '
                      'standalone="no"?><parameters></parameters>')
    files = dict(result.files)
    files.setdefault("src/main/resources/parameters.prop", "")
    files.setdefault("src/main/resources/parameters.propdef", _EMPTY_PROPDEF)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_path, content in files.items():
            zf.writestr(rel_path, content)
    buf.seek(0)
    return buf.read()


# ── multi-process: local integration process emission + injection ────────────
_LIP_PROPS = (
    '                <ifl:property><key>transactionTimeout</key><value>30</value></ifl:property>\n'
    '                <ifl:property><key>processType</key><value>directCall</value></ifl:property>\n'
    '                <ifl:property><key>componentVersion</key><value>1.1</value></ifl:property>\n'
    '                <ifl:property><key>cmdVariantUri</key><value>ctype::FlowElementVariant/cname::LocalIntegrationProcess/version::1.1.2</value></ifl:property>\n'
)


def _is_event_kind(kind: str) -> bool:
    return "StartEvent" in kind or "EndEvent" in kind or kind.endswith("Event")


def _local_process_block(proc, model, pid_override: str = "") -> str:
    """Emit a Local Integration Process <bpmn2:process>: LIP props + plain
    start/end + the process's middle steps re-emitted verbatim (real config) in
    step order, chained with sequence flows. Fresh element ids avoid collision
    with the main process. (Internal control flow such as a gateway inside the
    LIP is linearized — the reproduce metric checks the main process, and the
    real steps are preserved.)"""
    pid = pid_override or proc.id
    pname = html.escape(getattr(proc, "name", "") or pid)
    steps = [model.steps[sid] for sid in proc.step_ids
             if sid in model.steps and not _is_event_kind(model.steps[sid].kind)]
    n = len(steps)
    se, ee = f"SE_{pid}", f"EE_{pid}"
    nodes = [se] + [f"N_{pid}_{i}" for i in range(n)] + [ee]
    flows = [f"SF_{pid}_{i}" for i in range(len(nodes) - 1)]
    parts = [f'        <bpmn2:process id="{pid}" name="{pname}">\n',
             '            <bpmn2:extensionElements>\n', _LIP_PROPS,
             '            </bpmn2:extensionElements>\n',
             f'            <bpmn2:startEvent id="{se}" name="Start">\n'
             f'                <bpmn2:outgoing>{flows[0]}</bpmn2:outgoing>\n'
             f'            </bpmn2:startEvent>\n']
    for i, st in enumerate(steps):
        parts.append(_passthrough_step(
            nodes[i + 1], st.name or st.kind, flows[i], flows[i + 1],
            activity_type=st.kind, cmd_variant="", tag="callActivity",
            config=getattr(st, "config", None)))
    parts.append(f'            <bpmn2:endEvent id="{ee}" name="End">\n'
                 f'                <bpmn2:incoming>{flows[-1]}</bpmn2:incoming>\n'
                 f'            </bpmn2:endEvent>\n')
    for i in range(len(flows)):
        # a flow leaving a gateway-kind step needs a branch name even in the
        # linearized local-process chain ('Branch should have a name')
        src_is_gw = (i > 0 and (steps[i - 1].kind == "ExclusiveGateway"
                                or steps[i - 1].kind in _GW_PARALLEL))
        nm_attr = ' name="Branch 1"' if src_is_gw else ""
        parts.append(f'            <bpmn2:sequenceFlow id="{flows[i]}"{nm_attr} '
                     f'sourceRef="{nodes[i]}" targetRef="{nodes[i + 1]}"/>\n')
    parts.append('        </bpmn2:process>\n')
    return "".join(parts)



def _lip_graph_block(proc, model, pid, pool_x, pool_y):
    """Emit a Local Integration Process as its REAL graph — gateways as real
    gateway elements, events with their definitions, exception subprocesses
    nested, ORIGINAL flow ids and step ids preserved (so message flows keep
    their references without any renaming). Linearizing LIPs into callActivity
    chains turned e.g. a Multicast into a callActivity — the editor-load
    breaker class. Returns (process_xml, shapes_xml, edges_xml, (w, h))."""
    route_by_fid = {r.flow_id: r for r in (getattr(model, "routes", []) or [])}
    flow_target = getattr(model, "_flow_target", None) or {}
    steps = [model.steps[sid] for sid in proc.step_ids
             if sid in model.steps and not model.steps[sid].parent_subprocess]
    children_by_parent = {}
    for s in model.steps.values():
        if s.parent_subprocess:
            children_by_parent.setdefault(s.parent_subprocess, []).append(s)

    nodes_xml = []
    for s in steps:
        if s.id in children_by_parent:
            nodes_xml.append(_gw_emit_subprocess(
                s, children_by_parent[s.id], route_by_fid, flow_target))
        else:
            nodes_xml.append(_gw_emit_node(s, route_by_fid))

    gateway_ids = {s.id for s in steps
                   if s.kind == "ExclusiveGateway" or s.kind in _GW_PARALLEL}
    seqs = []
    for s in steps:
        for i, fid in enumerate(s.outgoing, start=1):
            tgt = flow_target.get(fid)
            if tgt is None:
                continue
            r = route_by_fid.get(fid)
            name_attr = ""
            if s.id in gateway_ids:
                bn = (r.name if r and r.name else
                      ("Default" if r and r.condition is None else f"Branch {i}"))
                name_attr = f' name="{html.escape(bn, quote=True)}"'
            seqs.append(_gw_emit_flow(
                s.id, fid, tgt, r, name_attr,
                getattr(model, "flow_props", None)))

    pname = html.escape(getattr(proc, "name", "") or pid, quote=True)
    process_xml = (f'    <bpmn2:process id="{pid}" name="{pname}">\n'
                   '        <bpmn2:extensionElements>\n' + _LIP_PROPS +
                   '        </bpmn2:extensionElements>\n'
                   + "".join(nodes_xml) + "".join(seqs)
                   + '    </bpmn2:process>\n')

    # DI: layered layout, then shifted into this LIP's pool; exception
    # subprocesses re-placed as wide boxes below the LIP's own lanes
    boxes = _gw_layout(steps, flow_target)
    sub_kids = {}
    for s in steps:
        kids = children_by_parent.get(s.id)
        if kids:
            sub_kids[s.id] = _chain_order(kids, flow_target)
    if boxes and sub_kids:
        base_y = max(b[1] + b[3] for b in boxes.values()) + 60
        for sid, kids in sub_kids.items():
            sw = 100 + len(kids) * 150
            boxes[sid] = (320, int(base_y), sw, 160)
            for i, k in enumerate(kids):
                kw, kh = _gw_size(k.kind)
                boxes[k.id] = (320 + 60 + i * 150 + (50 - kw // 2),
                               int(base_y) + 80 - kh // 2, kw, kh)
            base_y += 220
    if boxes:
        min_x = min(b[0] for b in boxes.values())
        min_y = min(b[1] for b in boxes.values())
        dx, dy = (pool_x + 60) - min_x, (pool_y + 55) - min_y
        boxes = {nid: (x + dx, y + dy, w, h)
                 for nid, (x, y, w, h) in boxes.items()}
        ext_w = max(b[0] + b[2] for b in boxes.values()) - (pool_x)
        ext_h = max(b[1] + b[3] for b in boxes.values()) - pool_y
    else:
        ext_w, ext_h = 400, 150
    di_steps = list(steps) + [k for kids in sub_kids.values() for k in kids]
    shapes, edges = [], []
    for s in di_steps:
        if s.id not in boxes:
            continue
        x, y, w, h = boxes[s.id]
        shapes.append(
            f'            <bpmndi:BPMNShape bpmnElement="{html.escape(s.id)}" '
            f'id="BPMNShape_{html.escape(s.id)}"><dc:Bounds height="{h}.0" '
            f'width="{w}.0" x="{x}.0" y="{y}.0"/></bpmndi:BPMNShape>\n')
    for s in di_steps:
        for fid in s.outgoing:
            tgt = flow_target.get(fid)
            if tgt is None or s.id not in boxes or tgt not in boxes:
                continue
            (ax, ay), (bx, by) = _gw_anchor(boxes[s.id], boxes[tgt])
            edges.append(
                f'            <bpmndi:BPMNEdge bpmnElement="{html.escape(fid)}" '
                f'id="BPMNEdge_{html.escape(fid)}" '
                f'sourceElement="BPMNShape_{html.escape(s.id)}" '
                f'targetElement="BPMNShape_{html.escape(tgt)}">'
                f'<di:waypoint x="{ax}.0" xsi:type="dc:Point" y="{ay}.0"/>'
                f'<di:waypoint x="{bx}.0" xsi:type="dc:Point" y="{by}.0"/>'
                f'</bpmndi:BPMNEdge>\n')
    return (process_xml, "".join(shapes), "".join(edges),
            (int(ext_w), int(ext_h)), boxes)


def inject_local_processes(iflw_xml: str, model) -> str:
    """Inject every non-main process as a Local Integration Process with its
    REAL graph structure (gateways, events, subprocesses — original step and
    flow ids preserved), its IntegrationProcess participant, and its DI
    pool/shapes/edges. The CPI editor refuses flows whose processes aren't
    referenced by participants AND flows whose LIP internals are linearized
    (e.g. a Multicast flattened to a callActivity) — both 'Error while
    loading' triggers decoded from tenant uploads. Local process IDS that
    collide with the built main id ('Process_1') are remapped (step ids inside
    are original and globally unique, so message-flow references stay valid
    without rewriting)."""
    locals_ = [p for p in model.processes if not getattr(p, "is_main", False)]
    if not locals_:
        return iflw_xml
    remap = {p.id: f"{p.id}_LIP" for p in locals_ if p.id == "Process_1"}

    def _fix_refs(text):
        for orig, new in remap.items():
            text = re.sub(r'(<key>processId</key>\s*<value>)' + re.escape(orig)
                          + r'(</value>)', r'\g<1>' + new + r'\g<2>', text)
        return text

    iflw_xml = _fix_refs(iflw_xml)
    blocks, parts, shapes, edges = [], [], [], []

    # stack LIP pools below everything already laid out
    ys = [float(m.group(1)) + float(m.group(2)) for m in re.finditer(
        r'<dc:Bounds height="([\d.]+)" width="[\d.]+" x="[\d.]+" y="([\d.]+)"',
        iflw_xml)]
    pool_y = (max(ys) if ys else 500) + 80
    pool_x = 240

    for p in locals_:
        pid = remap.get(p.id, p.id)
        pname = html.escape(getattr(p, "name", "") or pid, quote=True)
        block, lip_shapes, lip_edges, (w, h), lip_boxes = _lip_graph_block(
            p, model, pid, pool_x, int(pool_y))
        blocks.append(block)
        parts.append(
            f'        <bpmn2:participant id="Participant_{pid}" '
            f'ifl:type="IntegrationProcess" name="{pname}" processRef="{pid}">\n'
            f'            <bpmn2:extensionElements/>\n'
            f'        </bpmn2:participant>\n')
        pool_w, pool_h = max(w + 120, 420), h + 110
        shapes.append(
            f'            <bpmndi:BPMNShape bpmnElement="Participant_{pid}" '
            f'id="BPMNShape_Participant_{pid}"><dc:Bounds height="{pool_h}.0" '
            f'width="{pool_w}.0" x="{pool_x}.0" y="{int(pool_y)}.0"/>'
            f'</bpmndi:BPMNShape>\n')
        shapes.append(lip_shapes)
        edges.append(lip_edges)
        # endpoints whose partner step lives in THIS LIP: hang them right below
        # the pool (the main pass stacked them far right, drawing diagonal
        # message-flow edges across the whole canvas)
        moved, used_x = 0, []
        for mf in getattr(model, "message_flows", []) or []:
            for ep, st in ((mf.source, mf.target), (mf.target, mf.source)):
                if st in lip_boxes and ep and ep.startswith("Participant"):
                    pat = (r'(<bpmndi:BPMNShape bpmnElement="'
                           + re.escape(ep) + r'" id="BPMNShape_'
                           + re.escape(ep) + r'"><dc:Bounds height="140\.0" '
                           r'width="100\.0" )x="[\d.+-]+" y="[\d.+-]+"')
                    nx = max(40, int(lip_boxes[st][0]) - 10)
                    while any(abs(nx - ox) < 120 for ox in used_x):
                        nx += 130
                    new_b, nsub = re.subn(
                        pat, r'\g<1>x="%d.0" y="%d.0"'
                        % (nx, int(pool_y) + pool_h + 30), iflw_xml)
                    if nsub:
                        iflw_xml = new_b
                        used_x.append(nx)
                        moved += 1
        pool_y += pool_h + (240 if moved else 60)

    iflw_xml = iflw_xml.replace("    </bpmn2:collaboration>",
                                "".join(parts) + "    </bpmn2:collaboration>", 1)
    iflw_xml = iflw_xml.replace("        </bpmndi:BPMNPlane>",
                                "".join(shapes) + "".join(edges)
                                + "        </bpmndi:BPMNPlane>", 1)
    marker = "    <bpmndi:BPMNDiagram"
    iflw_xml = iflw_xml.replace(marker, "".join(blocks) + marker, 1)
    return repair_flow_di(iflw_xml)

def repair_flow_di(iflw_xml: str) -> str:
    """Generic DI repair: any sequence/message flow without a BPMNEdge whose
    BOTH endpoints have shapes gets a border-anchored edge appended (the editor
    wants complete DI; real flows ship it). Pure addition — never rewrites."""
    bounds = {}
    for m in re.finditer(r'BPMNShape bpmnElement="([^"]+)" id="[^"]*">'
                         r'<dc:Bounds height="([\d.]+)" width="([\d.]+)" '
                         r'x="([\d.]+)" y="([\d.]+)"', iflw_xml):
        nid, h, w, x, y = m.groups()
        bounds[nid] = (float(x), float(y), float(w), float(h))
    has_edge = set(re.findall(r'BPMNEdge bpmnElement="([^"]+)"', iflw_xml))
    add = []
    for m in re.finditer(r'<bpmn2:(?:sequenceFlow|messageFlow)\b[^>]*\bid='
                         r'"([^"]+)"[^>]*sourceRef="([^"]+)"[^>]*'
                         r'targetRef="([^"]+)"', iflw_xml):
        fid, s, t = m.groups()
        if fid in has_edge or s not in bounds or t not in bounds:
            continue
        (ax, ay), (bx, by) = _gw_anchor(bounds[s], bounds[t])
        add.append(
            f'            <bpmndi:BPMNEdge bpmnElement="{fid}" '
            f'id="BPMNEdge_{fid}" sourceElement="BPMNShape_{s}" '
            f'targetElement="BPMNShape_{t}">'
            f'<di:waypoint x="{int(ax)}.0" xsi:type="dc:Point" y="{int(ay)}.0"/>'
            f'<di:waypoint x="{int(bx)}.0" xsi:type="dc:Point" y="{int(by)}.0"/>'
            f'</bpmndi:BPMNEdge>\n')
    if add:
        iflw_xml = iflw_xml.replace("        </bpmndi:BPMNPlane>",
                                    "".join(add) + "        </bpmndi:BPMNPlane>",
                                    1)
    return iflw_xml


# ── ExclusiveGateway: full-graph emitter ─────────────────────────────────────
# Reconstructs the EXACT topology (every step + every sequence flow + gateway
# branches with conditions) from the parsed model, so the parser's topological
# BFS (_order_main, which appends targets in each step's <outgoing> order)
# traverses it identically → same mid-step kind sequence → faithful reproduce.
_GW_START = {"StartEvent", "StartTimerEvent", "MessageStartEvent"}
_GW_END = {"EndEvent", "MessageEndEvent"}
_GW_PARALLEL = {"Multicast", "SequentialMulticast", "ParallelGateway"}

# CPI-supported component variants decoded from the corpus (166 real CPI flows):
# Multicast v1.1.1 ×9, SequentialMulticast v1.1.0 ×7, ExclusiveGateway v1.1.2 ×181.
# Older PI/PO-era exports carry v1.0/1.1, which the tenant rejects ("component …
# not supported in Cloud Integration profile") — bump only when below the floor.
_CPI_VARIANT_FLOOR = {
    "Multicast": ("ctype::FlowstepVariant/cname::Multicast/version::1.1.1", "1.1"),
    "SequentialMulticast": (
        "ctype::FlowstepVariant/cname::SequentialMulticast/version::1.1.0", "1.1"),
    "ExclusiveGateway": (
        "ctype::FlowstepVariant/cname::ExclusiveGateway/version::1.1.2", "1.1"),
}


def _ver_tuple(uri: str):
    m = re.search(r"version::([\d.]+)", uri or "")
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def _gw_fix_versions(kind: str, cfg: dict) -> dict:
    """Upgrade carried component versions the tenant rejects, decoded from the
    corpus. Error start events use cname::ErrorStartEvent with NO version in
    real CPI flows — a carried 'StartErrorEvent …1.0' is what triggers
    'component StartErrorEvent with version 1.0 is not supported'."""
    cfg = dict(cfg)
    if kind in _CPI_VARIANT_FLOOR:
        uri, comp = _CPI_VARIANT_FLOOR[kind]
        cur = _ver_tuple(cfg.get("cmdVariantUri", ""))
        floor = _ver_tuple(uri)
        if cur is None or (floor and cur < floor):
            cfg["cmdVariantUri"] = uri
            cfg["componentVersion"] = comp
    if kind in ("StartErrorEvent", "ErrorStartEvent"):
        cfg.pop("componentVersion", None)
        cfg["cmdVariantUri"] = "ctype::FlowstepVariant/cname::ErrorStartEvent"
        cfg.pop("activityType", None)      # CPI derives it from errorEventDefinition
    return cfg


def _gw_props(config) -> str:
    return "".join(
        f"                <ifl:property><key>{html.escape(str(k))}</key>"
        f"<value>{html.escape('' if v is None else str(v))}</value></ifl:property>\n"
        for k, v in (config or {}).items())


def _gw_props_for(step) -> str:
    """Re-emit the step's captured properties, guaranteeing an activityType so
    the kind round-trips even when the real config is empty (e.g. EndErrorEvent,
    whose activityType lived in an errorEventDefinition child the model doesn't
    retain — the kind itself is the source of truth)."""
    cfg = dict(getattr(step, "config", None) or {})
    cfg.setdefault("activityType", step.kind)
    cfg = _gw_fix_versions(step.kind, cfg)
    return _gw_props(cfg)


def _gw_io(step) -> str:
    # incoming/outgoing in the model's recorded order (order drives BFS traversal)
    return ("".join(f"            <bpmn2:incoming>{html.escape(f)}</bpmn2:incoming>\n"
                    for f in step.incoming) +
            "".join(f"            <bpmn2:outgoing>{html.escape(f)}</bpmn2:outgoing>\n"
                    for f in step.outgoing))


def _gw_event_def(kind: str, config: dict | None = None) -> str:
    # error events keep an errorEventDefinition (CPI authors the variant INSIDE
    # it, decoded from corpus); timers a timerEventDefinition with the schedule
    # (a messageEventDefinition here makes the tenant demand an incoming
    # message flow: 'Start event should have an incoming message flow');
    # everything else a message one
    if "Timer" in kind:
        cfg = dict(config or {})
        inner = "".join(
            f"                    <ifl:property><key>{html.escape(str(k))}</key>"
            f"<value>{html.escape('' if v is None else str(v))}</value>"
            f"</ifl:property>\n"
            for k, v in cfg.items() if k != "activityType")
        if "cmdVariantUri" not in cfg:
            inner += ("                    <ifl:property><key>cmdVariantUri"
                      "</key><value>ctype::FlowstepVariant/cname::"
                      "intermediatetimer/version::1.4.0</value></ifl:property>\n")
        return ("            <bpmn2:timerEventDefinition>\n"
                "                <bpmn2:extensionElements>\n" + inner +
                "                </bpmn2:extensionElements>\n"
                "            </bpmn2:timerEventDefinition>\n")
    if kind in ("StartErrorEvent", "ErrorStartEvent"):
        return ("            <bpmn2:errorEventDefinition>\n"
                "                <bpmn2:extensionElements>\n"
                "                    <ifl:property><key>cmdVariantUri</key>"
                "<value>ctype::FlowstepVariant/cname::ErrorStartEvent</value>"
                "</ifl:property>\n"
                "                </bpmn2:extensionElements>\n"
                "            </bpmn2:errorEventDefinition>\n")
    if "Error" in kind:
        return "            <bpmn2:errorEventDefinition/>\n"
    return "            <bpmn2:messageEventDefinition/>\n"



def _gw_event_def_for(s) -> str:
    """Source-faithful event definition: plain LIP starts/ends get NONE (a
    messageEventDefinition there triggers 'Start event should have an incoming
    message flow' / 'LIP does not support this variant of end event'); timers
    and errors re-emit the def with its original id + its OWN props only.
    Steps without parsed fidelity (synthetic models) keep the kind heuristic."""
    ev = getattr(s, "event_def", "__missing__")
    if ev == "__missing__":
        return _gw_event_def(s.kind, s.config)
    if ev is None:
        return ""
    if ev == "message":
        return "            <bpmn2:messageEventDefinition/>\n"
    tag = "timerEventDefinition" if ev == "timer" else "errorEventDefinition"
    did = getattr(s, "event_def_id", "") or ""
    ida = f' id="{html.escape(did, quote=True)}"' if did else ""
    dp = getattr(s, "def_props", None) or {}
    if not dp:
        return f"            <bpmn2:{tag}{ida}/>\n"
    inner = "".join(
        f"                    <ifl:property><key>{html.escape(str(k))}</key>"
        f"<value>{html.escape('' if v is None else str(v))}</value>"
        f"</ifl:property>\n" for k, v in dp.items())
    return (f"            <bpmn2:{tag}{ida}>\n"
            "                <bpmn2:extensionElements>\n" + inner +
            "                </bpmn2:extensionElements>\n"
            f"            </bpmn2:{tag}>\n")


def _gw_node_ext_for(s, default_ext: str) -> str:
    """Node-level extensionElements for events: when the parser recorded the
    source's own node-level props, mirror them exactly (omit the block when the
    source had none — duplicating the timer def's props at node level is what
    broke 'Timer is not configured'). Synthetic steps keep the default."""
    np = getattr(s, "node_props", "__missing__")
    if np == "__missing__":
        return default_ext
    if not np:
        return ""
    inner = "".join(
        f"                <ifl:property><key>{html.escape(str(k))}</key>"
        f"<value>{html.escape('' if v is None else str(v))}</value>"
        f"</ifl:property>\n" for k, v in np.items())
    return ("            <bpmn2:extensionElements>\n" + inner +
            "            </bpmn2:extensionElements>\n")



def _gw_emit_flow(src_id, fid, tgt, r, name_attr, flow_props) -> str:
    """One sequence flow, round-tripping the source's OWN extension props
    (GatewayRoute variant, expressionType, componentVersion). Dropping them on
    a gateway's default branch makes the editor demand a condition ('Condition
    cannot be empty') even though the branch is the default."""
    fp = list((flow_props or {}).get(fid) or [])
    cond = r.condition if (r and r.condition is not None) else None
    if not fp and cond is not None:
        fp = [("expressionType", (r.expr_type or ""))]
    if not fp and cond is None:
        return (f'        <bpmn2:sequenceFlow id="{html.escape(fid)}"{name_attr} '
                f'sourceRef="{html.escape(src_id)}" '
                f'targetRef="{html.escape(tgt)}"/>\n')
    inner = "".join(
        f'                <ifl:property><key>{html.escape(str(k))}</key>'
        f'<value>{html.escape("" if v is None else str(v))}</value>'
        f'</ifl:property>\n' for k, v in fp)
    cx = (f'            <bpmn2:conditionExpression>{html.escape(cond)}'
          f'</bpmn2:conditionExpression>\n') if cond is not None else ""
    return (f'        <bpmn2:sequenceFlow id="{html.escape(fid)}"{name_attr} '
            f'sourceRef="{html.escape(src_id)}" targetRef="{html.escape(tgt)}">\n'
            f'            <bpmn2:extensionElements>\n{inner}'
            f'            </bpmn2:extensionElements>\n{cx}'
            f'        </bpmn2:sequenceFlow>\n')


def _gw_emit_node(s, route_by_fid) -> str:
    """Emit ONE flow node verbatim (start/end/gateway/callActivity), choosing the
    element tag + event definition from its kind so it round-trips."""
    nm = html.escape(s.name or s.kind, quote=True)
    io = _gw_io(s)
    props = _gw_props_for(s)
    ext = (f"            <bpmn2:extensionElements>\n{props}"
           f"            </bpmn2:extensionElements>\n")
    # error events (StartErrorEvent/EndErrorEvent) are real event ELEMENTS —
    # previously they fell through to callActivity, which the tenant rejects
    is_start = (s.kind in _GW_START or s.kind.endswith("StartEvent")
                or (s.kind.startswith("Start") and "Event" in s.kind))
    is_end = (s.kind in _GW_END or s.kind.endswith("EndEvent")
              or (s.kind.startswith("End") and "Event" in s.kind))
    if is_start:
        return (f'        <bpmn2:startEvent id="{s.id}" name="{nm}">\n'
                f'{_gw_node_ext_for(s, ext)}{io}'
                f'{_gw_event_def_for(s)}        </bpmn2:startEvent>\n')
    if is_end:
        return (f'        <bpmn2:endEvent id="{s.id}" name="{nm}">\n'
                f'{_gw_node_ext_for(s, ext)}{io}'
                f'{_gw_event_def_for(s)}        </bpmn2:endEvent>\n')
    if s.kind in _GW_PARALLEL:
        # real CPI multicasts are <bpmn2:parallelGateway> — a callActivity here
        # triggers 'Multiple outgoing connectors are not advisable' + 'Multicast
        # may not pass XML to Sequence Flow' on the tenant.
        return (f'        <bpmn2:parallelGateway id="{s.id}" name="{nm}">\n'
                f'{ext}{io}        </bpmn2:parallelGateway>\n')
    if s.kind == "ExclusiveGateway":
        default_fid = next((f for f in s.outgoing if f in route_by_fid
                            and route_by_fid[f].condition is None), "")
        da = f' default="{default_fid}"' if default_fid else ""
        return (f'        <bpmn2:exclusiveGateway id="{s.id}" name="{nm}"{da}>\n'
                f'{ext}{io}        </bpmn2:exclusiveGateway>\n')
    return (f'        <bpmn2:callActivity id="{s.id}" name="{nm}">\n'
            f'{ext}{io}        </bpmn2:callActivity>\n')


def _chain_order(kids, flow_target):
    """Order subprocess children along their internal chain (start first)."""
    by_id = {k.id: k for k in kids}
    targets = {flow_target.get(f) for k in kids for f in k.outgoing}
    head = next((k for k in kids if k.id not in targets), kids[0])
    seen, order, cur = set(), [], head
    while cur and cur.id not in seen:
        seen.add(cur.id)
        order.append(cur)
        nxt = next((flow_target.get(f) for f in cur.outgoing
                    if flow_target.get(f) in by_id), None)
        cur = by_id.get(nxt)
    order += [k for k in kids if k.id not in seen]
    return order


def _gw_emit_subprocess(s, children, route_by_fid, flow_target) -> str:
    """Emit an exception/event subprocess as a real <bpmn2:subProcess> with its
    handler children nested inside (error start event, handler steps, end) plus
    their internal sequence flows — NOT flattened to a childless node."""
    nm = html.escape(s.name or s.kind, quote=True)
    props = _gw_props_for(s)
    inner_nodes = "".join(_gw_emit_node(c, route_by_fid) for c in children)
    inner_flows = ""
    gw_kids = {c.id for c in children
               if c.kind == "ExclusiveGateway" or c.kind in _GW_PARALLEL}
    for c in children:
        for i, fid in enumerate(c.outgoing, start=1):
            tgt = flow_target.get(fid)
            if tgt is None:
                continue
            r = route_by_fid.get(fid)
            name_attr = ""
            if c.id in gw_kids:                  # branches need names here too
                bn = (r.name if r and r.name else
                      ("Default" if r and r.condition is None else f"Branch {i}"))
                name_attr = f' name="{html.escape(bn, quote=True)}"'
            inner_flows += (
                f'        <bpmn2:sequenceFlow id="{html.escape(fid)}"{name_attr} '
                f'sourceRef="{html.escape(c.id)}" '
                f'targetRef="{html.escape(tgt)}"/>\n')
    return (f'        <bpmn2:subProcess id="{s.id}" name="{nm}">\n'
            f'            <bpmn2:extensionElements>\n{props}'
            f'            </bpmn2:extensionElements>\n'
            f'{inner_nodes}{inner_flows}        </bpmn2:subProcess>\n')


def _gw_size(kind: str):
    if kind == "ExclusiveGateway" or kind in _GW_PARALLEL or "Gateway" in kind:
        return 40, 40
    if (kind in _GW_START or kind in _GW_END or kind.endswith("StartEvent")
            or kind.endswith("EndEvent") or kind.endswith("Event")):
        return 32, 32
    if kind == "ErrorEventSubProcessTemplate" or "SubProcess" in kind:
        return 170, 110
    return 100, 60


def _gw_layout(steps, flow_target):
    """Topological layered layout: column = BFS depth along sequence flows,
    row = branch lane (a branch keeps its lane; siblings fan into the next free
    lane). Gives the tidy left-to-right flow a person would draw, instead of an
    index grid that scatters connected nodes apart."""
    ids = {s.id for s in steps}
    by_id = {s.id: s for s in steps}
    out = {s.id: [flow_target.get(f) for f in s.outgoing
                  if flow_target.get(f) in ids] for s in steps}
    indeg = {i: 0 for i in ids}
    for ts in out.values():
        for t in ts:
            indeg[t] += 1

    from collections import deque
    roots = sorted(i for i in ids if indeg[i] == 0) or (sorted(ids)[:1])
    col = {}
    q = deque(roots)
    seen = set(roots)
    for r in roots:
        col[r] = 0
    while q:
        n = q.popleft()
        for t in out[n]:
            col[t] = max(col.get(t, 0), col[n] + 1)
            if t not in seen:
                seen.add(t)
                q.append(t)
    for i in ids:
        col.setdefault(i, 0)            # unreached nodes park in column 0

    # lanes: walk in column order; a node prefers its first predecessor's lane
    preds = {i: [] for i in ids}
    for s, ts in out.items():
        for t in ts:
            preds[t].append(s)
    row = {}
    taken = set()
    for nid in sorted(ids, key=lambda i: (col[i], i)):
        cand = min((row[p] for p in preds[nid] if p in row), default=None)
        r = cand if cand is not None else 0
        while (col[nid], r) in taken:
            r += 1
        row[nid] = r
        taken.add((col[nid], r))

    X0, Y0, DX, DY = 320, 130, 180, 150
    pos = {}
    for nid in ids:
        w, h = _gw_size(by_id[nid].kind)
        x = X0 + col[nid] * DX + (50 - w // 2)     # center within the column slot
        y = Y0 + row[nid] * DY + (30 - h // 2)
        pos[nid] = (x, y, w, h)
    return pos


def _gw_anchor(src_box, tgt_box):
    """Waypoints from the source shape's border to the target's, choosing the
    sides by relative position — so arrows touch the shapes instead of floating."""
    sx, sy, sw, sh = src_box
    tx, ty, tw, th = tgt_box
    if tx >= sx + sw:                              # target to the right
        return (sx + sw, sy + sh // 2), (tx, ty + th // 2)
    if tx + tw <= sx:                              # target to the left
        return (sx, sy + sh // 2), (tx + tw, ty + th // 2)
    if ty >= sy + sh:                              # target below
        return (sx + sw // 2, sy + sh), (tx + tw // 2, ty)
    return (sx + sw // 2, sy), (tx + tw // 2, ty + th)


def _gw_endpoints(model, boxes):
    """Emit sender/receiver participants + their message flows (with REAL adapter
    config) + DI. `boxes` maps flow-node id -> (x, y, w, h); senders sit left of
    the flow, receivers right of its widest column, and message-flow edges are
    anchored to shape borders (no floating arrows)."""
    parts, mflows, shapes, edges = [], [], [], []
    max_x = max((b[0] + b[2] for b in boxes.values()), default=900)
    min_y = min((b[1] for b in boxes.values()), default=110)
    max_y = max((b[1] + b[3] for b in boxes.values()), default=400)
    mid_y = (min_y + max_y) / 2
    # partner step of each endpoint (via its message flows) — real exports hang
    # a receiver directly above/below the step it talks to; stacking them all
    # at the far right draws long diagonal edges across the whole canvas
    partner = {}
    for mf in model.message_flows:
        for a, b in ((mf.source, mf.target), (mf.target, mf.source)):
            if a and b and b in boxes and a not in partner:
                partner[a] = b
    placed = {"above": [], "below": []}
    epbox = {}
    senders = receivers = 0
    for e in model.endpoints:
        etype = e.etype or ("EndpointSender" if e.direction == "sender"
                            else "EndpointRecevier")
        nm = html.escape(e.name or e.direction.title(), quote=True)
        parts.append(
            f'        <bpmn2:participant id="{html.escape(e.id)}" '
            f'ifl:type="{html.escape(etype)}" name="{nm}">\n'
            f'            <bpmn2:extensionElements/>\n'
            f'        </bpmn2:participant>\n')
        pstep = partner.get(e.id)
        if e.direction == "sender":
            x, y = 80, 110 + 170 * senders
            senders += 1
        elif pstep:
            px, py = boxes[pstep][0], boxes[pstep][1]
            side = "above" if (py < mid_y and min_y - 200 >= 10) else "below"
            x = max(40, int(px) - 10)
            while any(abs(x - ox) < 120 for ox in placed[side]):
                x += 130
            placed[side].append(x)
            y = int(min_y - 200) if side == "above" else int(max_y + 60)
        else:
            x, y = max_x + 160, 110 + 170 * receivers
            receivers += 1
        epbox[e.id] = (x, y, 100, 140)
        shapes.append(
            f'            <bpmndi:BPMNShape bpmnElement="{html.escape(e.id)}" '
            f'id="BPMNShape_{html.escape(e.id)}"><dc:Bounds height="140.0" '
            f'width="100.0" x="{x}.0" y="{y}.0"/></bpmndi:BPMNShape>\n')
    for mf in model.message_flows:
        cfg = dict(mf.config or {})
        # ProcessDirect requires an address starting with '/'; PI/PO sources
        # often don't carry one — default to a derivable, valid one (a
        # Configure-time value the user can change, not a deploy blocker).
        comp = (cfg.get("ComponentType") or cfg.get("Name") or "")
        if comp == "ProcessDirect":
            addr = (cfg.get("address") or "").strip()
            if not addr or not re.match(r"[A-Za-z0-9/]", addr):
                safe = re.sub(r"[^A-Za-z0-9_/-]", "",
                              (model.name or "flow").replace(" ", "_"))
                cfg["address"] = f"/{safe or 'flow'}"
        props = "".join(
            f'                <ifl:property><key>{html.escape(str(k))}</key>'
            f'<value>{html.escape("" if v is None else str(v))}</value>'
            f'</ifl:property>\n' for k, v in cfg.items())
        nm = html.escape(mf.name or "", quote=True)
        mflows.append(
            f'        <bpmn2:messageFlow id="{html.escape(mf.id)}" name="{nm}" '
            f'sourceRef="{html.escape(mf.source or "")}" '
            f'targetRef="{html.escape(mf.target or "")}">\n'
            f'            <bpmn2:extensionElements>\n{props}'
            f'            </bpmn2:extensionElements>\n        </bpmn2:messageFlow>\n')
        sb = epbox.get(mf.source) or boxes.get(mf.source)
        tb = epbox.get(mf.target) or boxes.get(mf.target)
        if sb and tb:
            (ax, ay), (bx, by) = _gw_anchor(sb, tb)
            edges.append(
                f'            <bpmndi:BPMNEdge bpmnElement="{html.escape(mf.id)}" '
                f'id="BPMNEdge_{html.escape(mf.id)}" '
                f'sourceElement="BPMNShape_{html.escape(mf.source)}" '
                f'targetElement="BPMNShape_{html.escape(mf.target)}">'
                f'<di:waypoint x="{ax}.0" xsi:type="dc:Point" y="{ay}.0"/>'
                f'<di:waypoint x="{bx}.0" xsi:type="dc:Point" y="{by}.0"/>'
                f'</bpmndi:BPMNEdge>\n')
    return "".join(parts), "".join(mflows), "".join(shapes), "".join(edges)


def build_gateway_flow(iflow_id: str, name: str, model):
    """Emit a single-process iFlow that contains an ExclusiveGateway, by
    reconstructing the full step+flow graph verbatim. Returns (iflw_xml,
    extra_files)."""
    main = next((p for p in model.processes if getattr(p, "is_main", False)), None)
    main_id = main.id if main else (model.processes[0].id if model.processes
                                    else "Process_1")
    steps = [s for s in model.steps.values()
             if s.process_id == main_id and not s.parent_subprocess]
    route_by_fid = {r.flow_id: r for r in (getattr(model, "routes", []) or [])}
    # children grouped by their subprocess parent (one nesting level)
    children_by_parent = {}
    for s in model.steps.values():
        if s.parent_subprocess:
            children_by_parent.setdefault(s.parent_subprocess, []).append(s)

    nodes_xml = []
    for s in steps:
        if s.id in children_by_parent:
            nodes_xml.append(_gw_emit_subprocess(
                s, children_by_parent[s.id], route_by_fid, model._flow_target))
        else:
            nodes_xml.append(_gw_emit_node(s, route_by_fid))
    body_xml = "".join(nodes_xml)

    # sequence flows: every edge, with conditions on conditional gateway
    # branches. Every gateway-outgoing branch gets a NAME (tenant check:
    # 'Branch should have a name') — the route's real name when it has one,
    # 'Default' for the default branch, 'Branch N' otherwise.
    seqs = []
    gateway_ids = {s.id for s in steps
                   if s.kind == "ExclusiveGateway" or s.kind in _GW_PARALLEL}
    for s in steps:
        for i, fid in enumerate(s.outgoing, start=1):
            tgt = model._flow_target.get(fid)
            if tgt is None:
                continue
            r = route_by_fid.get(fid)
            name_attr = ""
            if s.id in gateway_ids:
                bn = (r.name if r and r.name else
                      ("Default" if r and r.condition is None else f"Branch {i}"))
                name_attr = f' name="{html.escape(bn, quote=True)}"'
            seqs.append(_gw_emit_flow(
                s.id, fid, tgt, r, name_attr,
                getattr(model, "flow_props", None)))
    seq_xml = "".join(seqs)

    # DI: topological layered layout (column = flow depth, row = branch lane)
    # with edges anchored to shape BORDERS — tidy left-to-right flow, no
    # floating arrows, branches fan out instead of crossing.
    di_shapes, di_edges = [], []
    boxes = _gw_layout(steps, model._flow_target)
    # exception subprocesses move BELOW the flow as wide boxes with their
    # handler children laid out INSIDE (children without DI shapes are one of
    # the editor's 'Error while loading' triggers — real flows always shape them)
    sub_kids = {}
    for s in steps:
        kids = [k for k in model.steps.values() if k.parent_subprocess == s.id]
        if kids:
            sub_kids[s.id] = _chain_order(kids, model._flow_target)
    if sub_kids:
        base_y = max(b[1] + b[3] for b in boxes.values()) + 70 if boxes else 500
        for sid, kids in sub_kids.items():
            sw = 100 + len(kids) * 150
            boxes[sid] = (320, int(base_y), sw, 160)
            for i, k in enumerate(kids):
                kw, kh = _gw_size(k.kind)
                boxes[k.id] = (320 + 60 + i * 150 + (50 - kw // 2),
                               int(base_y) + 80 - kh // 2, kw, kh)
            base_y += 220
    pos = {nid: (b[0], b[1]) for nid, b in boxes.items()}      # for endpoints
    di_steps = list(steps) + [k for kids in sub_kids.values() for k in kids] \
        if sub_kids else list(steps)
    for s in di_steps:
        x, y, w, h = boxes[s.id]
        di_shapes.append(
            f'            <bpmndi:BPMNShape bpmnElement="{html.escape(s.id)}" '
            f'id="BPMNShape_{html.escape(s.id)}"><dc:Bounds height="{h}.0" '
            f'width="{w}.0" x="{x}.0" y="{y}.0"/></bpmndi:BPMNShape>\n')
    for s in di_steps:
        for fid in s.outgoing:
            tgt = model._flow_target.get(fid)
            if tgt is None or s.id not in boxes or tgt not in boxes:
                continue
            (ax, ay), (bx, by) = _gw_anchor(boxes[s.id], boxes[tgt])
            di_edges.append(
                f'            <bpmndi:BPMNEdge bpmnElement="{html.escape(fid)}" '
                f'id="BPMNEdge_{html.escape(fid)}" '
                f'sourceElement="BPMNShape_{html.escape(s.id)}" '
                f'targetElement="BPMNShape_{html.escape(tgt)}">'
                f'<di:waypoint x="{ax}.0" xsi:type="dc:Point" y="{ay}.0"/>'
                f'<di:waypoint x="{bx}.0" xsi:type="dc:Point" y="{by}.0"/>'
                f'</bpmndi:BPMNEdge>\n')

    # collaboration config: re-emit the REAL iFlow-level properties verbatim
    # (namespaceMapping for signer/XPath prefixes, allowedHeaderList, CORS, …);
    # synthesize sane defaults only when the source had none.
    real_cc = dict(getattr(model, "collab_config", None) or {})
    if real_cc:
        collab_props = "".join(
            f'            <ifl:property><key>{html.escape(str(k))}</key>'
            f'<value>{html.escape("" if v is None else str(v))}</value>'
            f'</ifl:property>\n' for k, v in real_cc.items())
    else:
        collab_props = (
            _ifl("namespaceMapping") + _ifl("httpSessionHandling", "None") +
            _ifl("returnExceptionToSender", "false") + _ifl("log", "All events") +
            _ifl("componentVersion", "1.2") +
            _ifl("cmdVariantUri",
                 "ctype::IFlowVariant/cname::IFlowConfiguration/version::1.2.3"))

    # the main pool must ENCLOSE the layout (nodes outside the pool render
    # detached in the editor)
    if boxes:
        min_x = min(b[0] for b in boxes.values())
        min_y = min(b[1] for b in boxes.values())
        max_x = max(b[0] + b[2] for b in boxes.values())
        max_y = max(b[1] + b[3] for b in boxes.values())
    else:
        min_x, min_y, max_x, max_y = 240, 80, 1440, 480
    pool_x, pool_y = min_x - 70, min_y - 70
    pool_w, pool_h = (max_x - min_x) + 140, (max_y - min_y) + 140

    ep_parts, ep_mflows, ep_shapes, ep_edges = _gw_endpoints(model, boxes)

    definitions = f"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn2:definitions xmlns:bpmn2="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" xmlns:ifl="http:///com.sap.ifl.model/Ifl.xsd" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" id="Definitions_1">
    <bpmn2:collaboration id="Collaboration_1" name="Default Collaboration">
        <bpmn2:extensionElements>
{collab_props}        </bpmn2:extensionElements>
        <bpmn2:participant id="Participant_{main_id}" ifl:type="IntegrationProcess" name="Integration Process" processRef="{main_id}">
            <bpmn2:extensionElements/>
        </bpmn2:participant>
{ep_parts}{ep_mflows}    </bpmn2:collaboration>
    <bpmn2:process id="{main_id}" name="Integration Process">
        <bpmn2:extensionElements>
            <ifl:property><key>transactionTimeout</key><value>30</value></ifl:property>
            <ifl:property><key>componentVersion</key><value>1.2</value></ifl:property>
            <ifl:property><key>cmdVariantUri</key><value>ctype::FlowElementVariant/cname::IntegrationProcess/version::1.2.1</value></ifl:property>
            <ifl:property><key>transactionalHandling</key><value>Not Required</value></ifl:property>
        </bpmn2:extensionElements>
{body_xml}{seq_xml}    </bpmn2:process>
    <bpmndi:BPMNDiagram id="BPMNDiagram_1" name="Default Collaboration Diagram">
        <bpmndi:BPMNPlane bpmnElement="Collaboration_1" id="BPMNPlane_1">
            <bpmndi:BPMNShape bpmnElement="Participant_{main_id}" id="BPMNShape_Participant_{main_id}">
                <dc:Bounds height="{pool_h}.0" width="{pool_w}.0" x="{pool_x}.0" y="{pool_y}.0"/>
            </bpmndi:BPMNShape>
{''.join(di_shapes)}{ep_shapes}{''.join(di_edges)}{ep_edges}        </bpmndi:BPMNPlane>
    </bpmndi:BPMNDiagram>
</bpmn2:definitions>
"""
    return definitions, {}


# ── iFlow-level carry: overlay real collaboration config ────────────────────
def apply_collab_config(iflw_xml: str, collab_config: dict) -> str:
    """Overlay the source iFlow's collaboration-level properties (namespace
    mapping, allowedHeaderList, CORS, …) onto a built iflw: existing keys get
    the real value; missing keys are appended inside the collaboration's
    extensionElements. Additive + regex-scoped to the collaboration block only."""
    if not collab_config:
        return iflw_xml
    m = re.search(r'(<bpmn2:collaboration\b.*?<bpmn2:extensionElements>)(.*?)'
                  r'(</bpmn2:extensionElements>)', iflw_xml, re.S)
    if not m:
        return iflw_xml
    head, block, tail = m.group(1), m.group(2), m.group(3)
    for k, v in collab_config.items():
        v_esc = html.escape("" if v is None else str(v))
        k_esc = html.escape(str(k))
        key_rx = re.compile(
            r'(<key>%s</key>\s*)(<value/>|<value\s*></value>|<value>.*?</value>)'
            % re.escape(k_esc), re.S)
        if key_rx.search(block):
            block = key_rx.sub(r'\g<1><value>%s</value>' % v_esc.replace('\\', r'\\'),
                               block, count=1)
        else:
            block += (f"                <ifl:property>\n"
                      f"                    <key>{k_esc}</key>\n"
                      f"                    <value>{v_esc}</value>\n"
                      f"                </ifl:property>\n")
    return iflw_xml[:m.start()] + head + block + tail + iflw_xml[m.end():]


def emit_parameter_files(params) -> dict:
    """parameters.prop + parameters.propdef for the bundle, from the iFlow's
    externalized {{param}} set. Schema decoded from real package exports:
    .prop = Java properties (keys escaped: space/:/= → backslash), values empty
    until Configure; .propdef = <parameters><parameter><key/><name>…</name>
    <type>xsd:string</type>… per param. Empty set ships the empty shells the
    real exporter ships (the previously-flagged bundle difference)."""
    names = sorted(p for p in (params or set()) if p)

    def _esc(k):
        return (k.replace("\\", "\\\\").replace(" ", "\\ ")
                 .replace(":", "\\:").replace("=", "\\="))
    prop = "#\n" + "".join(f"{_esc(n)}=\n" for n in names)
    if names:
        entries = "".join(
            "<parameter>\n    <key/>\n    <name>%s</name>\n"
            "    <type>xsd:string</type>\n    <isRequired>false</isRequired>\n"
            "    <constraint/>\n    <description/>\n"
            "    <additionalMetadata/>\n  </parameter>" % html.escape(n)
            for n in names)
        propdef = ('<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
                   f'<parameters>{entries}</parameters>')
    else:
        propdef = ('<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
                   '<parameters></parameters>')
    return {"src/main/resources/parameters.prop": prop,
            "src/main/resources/parameters.propdef": propdef}


def apply_timer_start(iflw_xml: str, model) -> str:
    """If the source flow starts with a timer, swap the linear template's fixed
    message start for the corpus timer form (timerEventDefinition carrying the
    captured schedule). A message start here makes the tenant demand an
    incoming message flow ('Start event should have an incoming message flow')."""
    main = next((p for p in model.processes if getattr(p, "is_main", False)), None)
    # ONLY when the main process's actual ENTRY step is a timer — a timer
    # somewhere else (e.g. inside a local process) must NOT convert a message
    # start, or the sender's message flow dangles and the flow won't deploy
    # ("this one has a start rather than a timer").
    timer = None
    if main:
        for sid in main.step_ids:
            s = model.steps.get(sid)
            if s is None or s.parent_subprocess:
                continue
            if "Timer" in s.kind and "Start" in s.kind and not s.incoming:
                timer = s
                break
    if timer is None:
        return iflw_xml
    m = re.search(r'(<bpmn2:startEvent id="StartEvent_2"[^>]*>)(.*?)'
                  r'(</bpmn2:startEvent>)', iflw_xml, re.S)
    if not m:
        return iflw_xml
    out_m = re.search(r'<bpmn2:outgoing>.*?</bpmn2:outgoing>', m.group(2), re.S)
    outgoing = out_m.group(0) if out_m else ""
    body = ("\n            " + outgoing + "\n"
            + _gw_event_def(timer.kind, timer.config) + "        ")
    return (iflw_xml[:m.start()] + m.group(1).replace('name="Start"',
            'name="Start Timer"') + body + m.group(3) + iflw_xml[m.end():])
