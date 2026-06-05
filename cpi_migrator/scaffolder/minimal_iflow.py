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
    # Header set matched field-for-field against a REAL importable iFlow export.
    # Differences that previously made CPI reject ours as "not a valid manifest":
    #  - Bundle-SymbolicName had a trailing "; singleton:=true" the real one omits
    #  - missing SAP-ArtifactTrait, Require-Capability, Origin-ModifiedDate,
    #    Import-Service headers that real iFlow manifests always carry
    import time as _time
    sym = _sanitize_id(iflow_id)
    modified = str(int(_time.time() * 1000))
    parts = [
        _wrap_manifest_line("Manifest-Version", "1.0"),
        _wrap_manifest_line("Bundle-SymbolicName", sym),
        _wrap_manifest_line("Bundle-ManifestVersion", "2"),
        _wrap_manifest_line("Origin-Bundle-SymbolicName", sym),
        _wrap_manifest_line("SAP-ArtifactTrait", ""),
        _wrap_manifest_line("Origin-Bundle-Version", "1.0.0"),
        _wrap_manifest_line("Import-Package", _IMPORT_PACKAGE),
        _wrap_manifest_line("Origin-Bundle-Name", name),
        _wrap_manifest_line("SAP-RuntimeProfile", "iflmap"),
        _wrap_manifest_line("Bundle-Name", name),
        _wrap_manifest_line("Bundle-Version", "1.0.0"),
        _wrap_manifest_line("SAP-NodeType", "IFLMAP"),
        _wrap_manifest_line("Origin-ModifiedDate", modified),
        _wrap_manifest_line("SAP-BundleType", "IntegrationFlow"),
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
    # Property order matches the proven-deployable real Enricher:
    # bodyType, propertyTable, headerTable, wrapContent, componentVersion,
    # activityType, cmdVariantUri.
    return f"""        <bpmn2:callActivity id="{cm_id}" name="{html.escape(name, quote=True)}">
            <bpmn2:extensionElements>
                <ifl:property><key>bodyType</key><value>expression</value></ifl:property>
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
                                   note: str = None) -> MinimalIFlowResult:
    """Parametrized Timer → CM(set properties) → CM(set body) → End bundle.

    This is the proven self-contained shape: no sender, no receiver, no message
    flow, and therefore NO dependency on any standard package or endpoint (the
    entanglement that made the clone-and-adapt artifacts impossible to edit).
    CM1 records migration provenance plus any caller-supplied ``properties``
    (e.g. the PI/PO channel fields); CM2 emits a small marker ack body so the
    run leaves a visible, self-describing trace. Returns a MinimalIFlowResult,
    same contract as the other generators."""
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
    iflw = build_timer_two_cm_iflw(iflow_id, name, cm1_props, [], ack_body)
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
    """Zip the bundle into the artifact format CPI accepts."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_path, content in result.files.items():
            zf.writestr(rel_path, content)
    buf.seek(0)
    return buf.read()
