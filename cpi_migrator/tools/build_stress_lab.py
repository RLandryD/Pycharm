"""tools/build_stress_lab.py — build the CPI "Stress Lab" iFlow.

A self-contained, timer-triggered iFlow that exercises (almost) the full CPI
step palette WITHOUT senders or receivers, plus a rich generated XML payload —
for pushing a tenant to the limit and watching how a complex flow behaves.

Step coverage (every ifl:property set harvested VERBATIM from the 263-package
standard-content corpus; only payload-specific VALUES — XPaths, file names,
schedule — are adapted):
  Timer start (run once) · Content Modifier (payload seed + properties) ·
  Groovy script · JavaScript · Filter · XML Validator (own XSD) ·
  Write Variables · Data Store put · Base64 Encoder/Decoder · Message Digest ·
  XML↔JSON converters · Process Call → LIP (XSLT mapping inside) ·
  Looping Process Call → LIP (standardLoopCharacteristics + condition) ·
  General Splitter → Gather · Parallel Multicast → [XML→CSV→XML | CM] → Join →
  Gather · Router (ExclusiveGateway, XML condition + default) ·
  Exception Subprocess (error start → alert script).

Deliberately excluded (require sender/receiver participants or deploy-time
endpoints): Request-Reply, Content Enricher (lookup), Send, Poll, Idempotent
Process Call w/ external id, AS2/AS4 steps.

Usage:
    python3 tools/build_stress_lab.py [outdir]
Writes: StressLab_bundle.zip (importable artifact), stress_payload.xml,
        STRESS_LAB_README.md.
"""
from __future__ import annotations

import io
import random
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extractor.iflow_parser import IFlowModel, Process, Step   # noqa: E402
from extractor.iflow_parser import Route                        # noqa: E402

NAME = "Stress Lab All Steps"
IFLOW_ID = "StressLabAllSteps"


# ── payload ──────────────────────────────────────────────────────────────────
def build_payload(n_orders: int = 24, seed: int = 93) -> str:
    """Rich, deterministic test XML: multiple regions/doc types, nested lines,
    unicode + escaping hazards, dates, decimals — splitter/router/converters
    all get real material."""
    rng = random.Random(seed)
    regions = ["EU", "US", "MX", "APJ"]
    docs = ["INV", "ORD", "CRM"]
    names = ["Müller & Söhne", "O'Brien Ltd", "Diaz <Hermanos>",
             "Quoted \"Co\"", "日本商事", "Ångström AB"]
    rows = []
    for i in range(1, n_orders + 1):
        lines = "".join(
            f'<line sku="{rng.choice("ABC")}-{rng.randint(1, 99)}" '
            f'qty="{rng.randint(1, 12)}" price="{rng.randint(10, 999)}.'
            f'{rng.randint(0, 99):02d}"/>'
            for _ in range(rng.randint(1, 4)))
        nm = (names[i % len(names)].replace("&", "&amp;")
              .replace("<", "&lt;").replace(">", "&gt;")
              .replace('"', "&quot;"))
        rows.append(
            f'<Order id="{1000 + i}">'
            f'<region>{regions[i % len(regions)]}</region>'
            f'<docType>{docs[i % len(docs)]}</docType>'
            f'<customer>{nm}</customer>'
            f'<created>2026-{(i % 12) + 1:02d}-{(i % 27) + 1:02d}</created>'
            f'<amount>{rng.randint(100, 9999)}.{rng.randint(0, 99):02d}</amount>'
            f'<priority>{rng.randint(1, 5)}</priority>'
            f'<lines>{lines}</lines>'
            f'</Order>')
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            f'<Lab run="stress"><Batch>{"".join(rows)}</Batch></Lab>')


# ── resources ────────────────────────────────────────────────────────────────
_XSD = """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" elementFormDefault="unqualified">
  <xs:element name="Lab">
    <xs:complexType>
      <xs:sequence><xs:element name="Batch">
        <xs:complexType><xs:sequence>
          <xs:element name="Order" maxOccurs="unbounded">
            <xs:complexType>
              <xs:sequence>
                <xs:element name="region" type="xs:string"/>
                <xs:element name="docType" type="xs:string"/>
                <xs:element name="customer" type="xs:string"/>
                <xs:element name="created" type="xs:string"/>
                <xs:element name="amount" type="xs:decimal"/>
                <xs:element name="priority" type="xs:integer"/>
                <xs:element name="lines">
                  <xs:complexType><xs:sequence>
                    <xs:element name="line" maxOccurs="unbounded">
                      <xs:complexType>
                        <xs:attribute name="sku" type="xs:string"/>
                        <xs:attribute name="qty" type="xs:integer"/>
                        <xs:attribute name="price" type="xs:decimal"/>
                      </xs:complexType>
                    </xs:element>
                  </xs:sequence></xs:complexType>
                </xs:element>
              </xs:sequence>
              <xs:attribute name="id" type="xs:integer"/>
            </xs:complexType>
          </xs:element>
        </xs:sequence></xs:complexType>
      </xs:element></xs:sequence>
      <xs:attribute name="run" type="xs:string"/>
    </xs:complexType>
  </xs:element>
</xs:schema>
"""

# row shape produced by XmlToCsv (one row per line item) for the CSV→XML leg
_CSV_XSD = """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" elementFormDefault="unqualified">
  <xs:element name="Rows">
    <xs:complexType><xs:sequence>
      <xs:element name="Row" maxOccurs="unbounded">
        <xs:complexType><xs:sequence>
          <xs:element name="region" type="xs:string" minOccurs="0"/>
          <xs:element name="docType" type="xs:string" minOccurs="0"/>
          <xs:element name="customer" type="xs:string" minOccurs="0"/>
          <xs:element name="created" type="xs:string" minOccurs="0"/>
          <xs:element name="amount" type="xs:string" minOccurs="0"/>
          <xs:element name="priority" type="xs:string" minOccurs="0"/>
        </xs:sequence></xs:complexType>
      </xs:element>
    </xs:sequence></xs:complexType>
  </xs:element>
</xs:schema>
"""

_XSL_NORMALIZE = """<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="2.0">
  <xsl:output method="xml" indent="no"/>
  <xsl:template match="@*|node()">
    <xsl:copy><xsl:apply-templates select="@*|node()"/></xsl:copy>
  </xsl:template>
  <!-- annotate every order with a computed total -->
  <xsl:template match="Order">
    <xsl:copy>
      <xsl:apply-templates select="@*"/>
      <xsl:attribute name="lineTotalQty">
        <xsl:value-of select="sum(lines/line/@qty)"/>
      </xsl:attribute>
      <xsl:apply-templates select="node()"/>
    </xsl:copy>
  </xsl:template>
</xsl:stylesheet>
"""

_GROOVY_LOG = """import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {
    def body = message.getBody(java.lang.String) as String
    def log = messageLogFactory.getMessageLog(message)
    if (log != null) {
        log.setStringProperty('StressLab', 'seeded')
        log.addAttachmentAsString('SeededPayload',
            body ?: '', 'text/xml')
    }
    message.setProperty('P_RUNSTAMP', String.valueOf(System.currentTimeMillis()))
    message.setProperty('P_ORDERCOUNT',
        String.valueOf((body =~ /<Order /).size()))
    return message
}
"""

_GROOVY_LOOPSTEP = """import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {
    def n = (message.getProperty('P_LOOPCOUNT') ?: '0') as Integer
    message.setProperty('P_LOOPCOUNT', String.valueOf(n + 1))
    def log = messageLogFactory.getMessageLog(message)
    if (log != null) log.setStringProperty('LoopIteration', String.valueOf(n + 1))
    return message
}
"""

# the gold-standard capture (SAP Design Guidelines pattern) — same script the
# error-handling upgrade injects, so the lab demos exactly what flows get
from scaffolder.error_handling import GOLD_CAPTURE_SCRIPT as _GROOVY_ALERT

_JS_HEADER = """importClass(com.sap.gateway.ip.core.customdev.util.Message);

function processData(message) {
    message.setHeader('X-StressLab', 'js-step');
    return message;
}
"""


# ── model assembly (configs harvested verbatim from the 263-package corpus;
#    only payload-specific VALUES adapted) ────────────────────────────────────
def _step(steps, sid, kind, name, cfg, proc, sub=""):
    s = Step(id=sid, kind=kind, name=name, process_id=proc,
             config=dict(cfg), parent_subprocess=sub)
    steps[sid] = s
    return s


def _wire(steps, flow_target, fid, src, tgt):
    steps[src].outgoing.append(fid)
    steps[tgt].incoming.append(fid)
    flow_target[fid] = tgt


def build_stress_model(payload: str) -> IFlowModel:
    m = IFlowModel(name=NAME)
    steps, ft = m.steps, {}
    P, L1, L2 = "Process_1", "Process_Lab_Transform", "Process_Lab_Loop"

    GS = {"componentVersion": "1.1", "activityType": "Script",
          "cmdVariantUri": "ctype::FlowstepVariant/cname::GroovyScript/version::1.1.2",
          "subActivityType": "GroovyScript", "scriptFunction": "",
          "scriptBundleId": ""}
    CM = {"bodyType": "constant", "componentVersion": "1.6",
          "activityType": "Enricher",
          "cmdVariantUri": "ctype::FlowstepVariant/cname::Enricher/version::1.6.0",
          "wrapContent": ""}

    # ── main chain ──
    _step(steps, "StartEvent_1", "StartTimerEvent", "Start Timer 1", {
        "componentVersion": "1.1", "activityType": "StartTimerEvent",
        "cmdVariantUri":
            "ctype::FlowstepVariant/cname::intermediatetimer/version::1.1",
        "scheduleKey": '''<row><cell>dateType</cell><cell></cell></row><row><cell>timeType</cell><cell></cell></row><row><cell>dayValue</cell><cell></cell></row><row><cell>monthValue</cell><cell></cell></row><row><cell>yearValue</cell><cell></cell></row><row><cell>onWeekly</cell><cell></cell></row><row><cell>onMonthly</cell><cell></cell></row><row><cell>OnEveryMinute</cell><cell></cell></row><row><cell>fromInterval</cell><cell></cell></row><row><cell>toInterval</cell><cell></cell></row><row><cell>timeZone</cell><cell>( UTC 0:00 ) Greenwich Mean Time(Etc/GMT)</cell></row><row><cell>secondValue</cell><cell>0</cell></row><row><cell>minutesValue</cell><cell></cell></row><row><cell>hourValue</cell><cell></cell></row><row><cell>triggerType</cell><cell>simple</cell></row><row><cell>noOfSchedules</cell><cell>1</cell></row><row><cell>schedule1</cell><cell>fireNow=true</cell></row>''',
    }, P)
    _step(steps, "CA_Seed", "Enricher", "CM_Seed_Payload",
          dict(CM, bodyContent=payload), P)
    _step(steps, "CA_Log", "Script", "GS_Log_And_Count",
          dict(GS, script="stress_log.groovy"), P)
    _step(steps, "CA_JS", "Script", "JS_Set_Header", {
        "componentVersion": "1.0", "activityType": "Script",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::JavaScript/version::1.0.2",
        "subActivityType": "JavaScript", "script": "stress_header.js",
        "scriptFunction": ""}, P)
    _step(steps, "CA_Filter", "Filter", "Filter_Keep_Lab", {
        "xpathType": "Node", "wrapContent": "/Lab",
        "componentVersion": "1.1", "activityType": "Filter",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::Filter/version::1.1.0"}, P)
    _step(steps, "CA_Validate", "XmlValidator", "Validate_Lab_XSD", {
        "xmlSchemaSource": "iflowOption", "preventException": "true",
        "xsd": "/xsd/StressLab.xsd", "componentVersion": "2.2",
        "activityType": "XmlValidator",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::XmlValidator/version::2.2.3",
        "headerSource": ""}, P)
    _step(steps, "CA_Var", "Variables", "Write_RunStamp", {
        "visibility": "local", "encrypt": "false", "expire": "90",
        "variable": "<row><cell>LV_RUNSTAMP</cell><cell></cell>"
                    "<cell>property</cell><cell>P_RUNSTAMP</cell>"
                    "<cell>local</cell></row>",
        "componentVersion": "1.2", "activityType": "Variables",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::Variables/version::1.2.0"}, P)
    _step(steps, "CA_DSPut", "DBstorage", "DataStore_Put", {
        "visibility": "local", "alert": "2", "encrypt": "true", "expire": "7",
        "override": "true", "componentVersion": "1.5",
        "activityType": "DBstorage",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::put/version::1.5.1",
        "operation": "put", "storageName": "DS_StressLab",
        "includeMessageHeaders": "false", "messageId": ""}, P)
    _step(steps, "CA_Enc", "Encoder", "Base64_Encode", {
        "activityType": "Encoder",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::Base64 Encode",
        "encoderType": "Base64 Encode"}, P)
    _step(steps, "CA_Dec", "Decoder", "Base64_Decode", {
        "componentVersion": "1.0", "activityType": "Decoder",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::Base64 Decode/version::1.0.2",
        "encoderType": "Base64 Decode"}, P)
    _step(steps, "CA_Digest", "MessageDigest", "Digest_SHA256", {
        "canonicalizationMethod": "xml-c14n", "targetHeader": "SAPMessageDigest",
        "digestAlgorithm": "SHA-256", "componentVersion": "1.0",
        "activityType": "MessageDigest",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::MessageDigest/version::1.0.2",
        "filter": ""}, P)
    _step(steps, "CA_X2J", "XmlToJsonConverter", "XML_to_JSON", {
        "xmlJsonUseStreaming": "false", "xmlJsonSuppressRootElement": "false",
        "componentVersion": "1.0", "xmlJsonConvertAllElements": "specific",
        "activityType": "XmlToJsonConverter",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::XmlToJsonConverter/version::1.0.8",
        "useNamespaces": "true", "jsonNamespaceSeparator": ":",
        "xmlJsonPathTable": "", "jsonOutputEncoding": "",
        "jsonNamespaceMapping": ""}, P)
    _step(steps, "CA_J2X", "JsonToXmlConverter", "JSON_to_XML", {
        "additionalParameters": "", "useNamespaceMapping": "false",
        "jsonNamespaceMapping": "", "addXMLRootElement": "false",
        "componentVersion": "1.1", "activityType": "JsonToXmlConverter",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::JsonToXmlConverter/version::1.1.2",
        "rootElementName": "", "namespaceMapping": ""}, P)
    _step(steps, "CA_PC", "ProcessCallElement", "Call_LIP_Transform", {
        "processId": L1, "componentVersion": "1.0",
        "activityType": "ProcessCallElement",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::NonLoopingProcess/version::1.0.3",
        "subActivityType": "NonLoopingProcess"}, P)
    lpc = _step(steps, "CA_LPC", "ProcessCallElement", "Loop_LIP_Counter", {
        "postProcessing": "end", "loopId": "StandardLoopCharacteristics_StressLab1",
        "processId": L2, "expressionType": "XML", "componentVersion": "1.3",
        "activityType": "ProcessCallElement",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::LoopingProcess/version::1.3.0",
        "subActivityType": "LoopingProcess"}, P)
    lpc.loop = {"id": "StandardLoopCharacteristics_StressLab1",
                "loop_maximum": "3",
                "cond_id": "FormalExpression_StressLab1",
                "cond_type": "bpmn2:tFormalExpression",
                "condition": "/Lab/Batch/Order[1]"}
    _step(steps, "CA_Split", "Splitter", "General_Splitter", {
        "exprType": "XPath", "Streaming": "false", "StopOnExecution": "true",
        "SplitterThreads": "10", "splitExprValue": "/Lab/Batch/Order",
        "ParallelProcessing": "false", "componentVersion": "1.5",
        "activityType": "Splitter",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::GeneralSplitter/version::1.5.0",
        "grouping": "", "splitType": "GeneralSplitter", "timeOut": "300"}, P)
    _step(steps, "CA_GatherS", "Gather", "Gather_Splits", {
        "messageType": "SameXMLFormat",
        "aggregationAlgorithm": "sap-identical-multi-mapping",
        "componentVersion": "1.2", "activityType": "Gather",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::Gather/version::1.2.0",
        "targetXPath": "", "sourceXPath": "", "gatherFileNames": ""}, P)
    _step(steps, "GW_MC", "Multicast", "Parallel Multicast", {
        "componentVersion": "1.1", "activityType": "Multicast",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::Multicast/version::1.1.1",
        "subActivityType": "parallel"}, P)
    _step(steps, "CA_X2C", "XmlToCsvConverter", "XML_to_CSV", {
        "Field_Separator_in_CSV": ",", "Include_Attribute": "false",
        "Include_Header": "false", "Include_Master": "false",
        "componentVersion": "1.1", "activityType": "XmlToCsvConverter",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::XmlToCsvConverter/version::1.1.2",
        # runtime decode (round 5): JSON→XML wraps the body in <root>, so the
        # absolute /Lab path matched nothing → branch A produced an empty
        # <Rows/>
        "XPath_Field_Location": "/root/Lab/Batch/Order",
        "Master_XPath_Field_Location": ""}, P)
    _step(steps, "CA_C2X", "CsvToXmlConverter", "CSV_to_XML", {
        "Field_Separator_in_CSV": ",", "ignoreFirstLineAsHeader": "false",
        "XML_Schema_File_Path": "/xsd/StressLabRows.xsd",
        "headerMapping": "mapHeadersToXSD", "componentVersion": "1.4",
        "activityType": "CsvToXmlConverter",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::CsvToXmlConverter/version::1.4.0",
        "XPath_Field_Location": "/Rows/Row",
        "Record_Identifier_in_CSV": ""}, P)
    _step(steps, "CA_BranchB", "Enricher", "CM_Branch_B",
          dict(CM, bodyType="expression", bodyContent="${in.body}"), P)
    _step(steps, "GW_Join", "Join", "Join 1", {
        "componentVersion": "1.0", "activityType": "Join",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::Join/version::1.0.0",
        "subActivityType": "parallel"}, P)
    _step(steps, "CA_GatherMC", "Gather", "Gather_Multicast", {
        "messageType": "SameXMLFormat",
        "aggregationAlgorithm": "sap-identical-multi-mapping",
        "componentVersion": "1.2", "activityType": "Gather",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::Gather/version::1.2.0",
        "targetXPath": "", "sourceXPath": "", "gatherFileNames": ""}, P)
    _step(steps, "GW_Router", "ExclusiveGateway", "Router_Any_EU", {
        "componentVersion": "1.1", "activityType": "ExclusiveGateway",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::ExclusiveGateway/version::1.1.2",
        "throwException": "false"}, P)
    _step(steps, "CA_EU", "Enricher", "CM_EU_Branch",
          dict(CM, bodyType="expression", bodyContent="${in.body}"), P)
    _step(steps, "EndEvent_EU", "EndEvent", "End EU", {
        "componentVersion": "1.1", "activityType": "EndEvent",
        "cmdVariantUri":
            "ctype::FlowstepVariant/cname::MessageEndEvent/version::1.1.0"}, P)
    _step(steps, "EndEvent_Main", "EndEvent", "End Main", {
        "componentVersion": "1.1", "activityType": "EndEvent",
        "cmdVariantUri":
            "ctype::FlowstepVariant/cname::MessageEndEvent/version::1.1.0"}, P)

    # exception subprocess (children carry parent_subprocess)
    _step(steps, "SubProcess_Err", "ErrorEventSubProcessTemplate",
          "Exception Subprocess", {
              "componentVersion": "1.1",
              "activityType": "ErrorEventSubProcessTemplate",
              "cmdVariantUri": "ctype::FlowstepVariant/cname::"
                               "ErrorEventSubProcessTemplate/version::1.1.0"}, P)
    _step(steps, "StartEvent_Err", "StartErrorEvent", "Error Start", {
        "componentVersion": "1.0", "activityType": "StartErrorEvent",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::"
                         "ErrorStartEvent/version::1.0.1"}, P,
          sub="SubProcess_Err")
    _step(steps, "CA_Alert", "Script", "GS_Exception_Alert",
          dict(GS, script="stress_alert.groovy"), P, sub="SubProcess_Err")
    _step(steps, "EndEvent_Err", "EndEvent", "End Error", {
        "componentVersion": "1.1", "activityType": "EndEvent",
        "cmdVariantUri":
            "ctype::FlowstepVariant/cname::MessageEndEvent/version::1.1.0"},
          P, sub="SubProcess_Err")
    for _sid in ("EndEvent_EU", "EndEvent_Main", "EndEvent_Err"):
        steps[_sid].event_def = "message"

    main_chain = ["StartEvent_1", "CA_Seed", "CA_Log", "CA_JS", "CA_Filter",
                  "CA_Validate", "CA_Var", "CA_DSPut", "CA_Digest", "CA_X2J",
                  "CA_J2X", "CA_Enc", "CA_Dec", "CA_PC", "CA_LPC",
                  "CA_Split", "CA_GatherS", "GW_MC"]
    for i in range(len(main_chain) - 1):
        _wire(steps, ft, f"SequenceFlow_M{i}", main_chain[i], main_chain[i + 1])
    # multicast branches → join → gather
    _wire(steps, ft, "SequenceFlow_MC_A", "GW_MC", "CA_X2C")
    _wire(steps, ft, "SequenceFlow_A1", "CA_X2C", "CA_C2X")
    _wire(steps, ft, "SequenceFlow_A2", "CA_C2X", "GW_Join")
    _wire(steps, ft, "SequenceFlow_MC_B", "GW_MC", "CA_BranchB")
    _wire(steps, ft, "SequenceFlow_B1", "CA_BranchB", "GW_Join")
    _wire(steps, ft, "SequenceFlow_J1", "GW_Join", "CA_GatherMC")
    _wire(steps, ft, "SequenceFlow_G1", "CA_GatherMC", "GW_Router")
    # router: EU condition + default
    _wire(steps, ft, "SequenceFlow_R_EU", "GW_Router", "CA_EU")
    _wire(steps, ft, "SequenceFlow_EU1", "CA_EU", "EndEvent_EU")
    _wire(steps, ft, "SequenceFlow_R_DEF", "GW_Router", "EndEvent_Main")
    # exception subprocess internal chain
    _wire(steps, ft, "SequenceFlow_E1", "StartEvent_Err", "CA_Alert")
    _wire(steps, ft, "SequenceFlow_E2", "CA_Alert", "EndEvent_Err")

    m.routes = [
        Route(flow_id="SequenceFlow_R_EU", name="EU orders",
              gateway="GW_Router", target="CA_EU",
              # //Order: the body reaching the router is the Gather multimap
              # envelope wrapping <root><Lab>… (round-5 MPL), so only a
              # depth-agnostic path matches
              condition="//Order[region = 'EU']", expr_type="XML"),
        Route(flow_id="SequenceFlow_R_DEF", name="Default",
              gateway="GW_Router", target="EndEvent_Main",
              condition=None, expr_type=""),
    ]

    # ── LIP 1: XSLT transform ──
    _step(steps, "L1_Start", "StartEvent", "Start L1", {}, L1)
    _step(steps, "L1_XSLT", "Mapping", "XSLT_Annotate_Totals", {
        "mappingoutputformat": "String",
        "mappinguri": "dir://mapping/xslt/src/main/resources/mapping/"
                      "StressLab_Annotate.xsl",
        "mappingname": "StressLab_Annotate",
        "mappingpath": "src/main/resources/mapping/StressLab_Annotate",
        "mappingSource": "mappingSrcIflow", "componentVersion": "1.2",
        "activityType": "Mapping",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::XSLTMapping/version::1.2.0",
        "subActivityType": "XSLTMapping", "mappingHeaderNameKey": ""}, L1)
    _step(steps, "L1_CM", "Enricher", "CM_L1_Note",
          dict(CM, bodyType="expression", bodyContent="${in.body}"), L1)
    _step(steps, "L1_End", "EndEvent", "End L1", {
        "cmdVariantUri": "ctype::FlowstepVariant/cname::EndEvent"}, L1)
    _wire(steps, ft, "SequenceFlow_L1a", "L1_Start", "L1_XSLT")
    _wire(steps, ft, "SequenceFlow_L1b", "L1_XSLT", "L1_CM")
    _wire(steps, ft, "SequenceFlow_L1c", "L1_CM", "L1_End")
    for sid in ("L1_Start", "L1_End"):
        steps[sid].event_def = None

    # ── LIP 2: loop body ──
    _step(steps, "L2_Start", "StartEvent", "Start L2", {}, L2)
    _step(steps, "L2_GS", "Script", "GS_Loop_Counter",
          dict(GS, script="stress_loop.groovy"), L2)
    _step(steps, "L2_End", "EndEvent", "End L2", {
        "cmdVariantUri": "ctype::FlowstepVariant/cname::EndEvent"}, L2)
    _wire(steps, ft, "SequenceFlow_L2a", "L2_Start", "L2_GS")
    _wire(steps, ft, "SequenceFlow_L2b", "L2_GS", "L2_End")
    for sid in ("L2_Start", "L2_End"):
        steps[sid].event_def = None

    m.processes = [
        Process(id=P, name="Integration Process", is_main=True,
                step_ids=[s for s in steps if steps[s].process_id == P]),
        Process(id=L1, name="LIP_Transform", is_main=False,
                step_ids=[s for s in steps if steps[s].process_id == L1]),
        Process(id=L2, name="LIP_Loop", is_main=False,
                step_ids=[s for s in steps if steps[s].process_id == L2]),
    ]
    m.sequence = main_chain + ["CA_X2C", "CA_C2X", "CA_BranchB", "GW_Join",
                               "CA_GatherMC", "GW_Router", "CA_EU",
                               "EndEvent_EU", "EndEvent_Main"]
    m._flow_target = ft
    m.flow_props = {}
    return m


def resource_files() -> dict:
    return {
        "src/main/resources/xsd/StressLab.xsd": _XSD,
        "src/main/resources/xsd/StressLabRows.xsd": _CSV_XSD,
        "src/main/resources/mapping/StressLab_Annotate.xsl": _XSL_NORMALIZE,
        "src/main/resources/script/stress_log.groovy": _GROOVY_LOG,
        "src/main/resources/script/stress_loop.groovy": _GROOVY_LOOPSTEP,
        "src/main/resources/script/stress_alert.groovy": _GROOVY_ALERT,
        "src/main/resources/script/stress_header.js": _JS_HEADER,
    }


def build(outdir: str = "/mnt/user-data/outputs") -> dict:
    from scaffolder.model_generator import generate_from_model
    from extractor.iflow_parser import parse_iflow

    payload = build_payload()
    model = build_stress_model(payload)
    res = generate_from_model(model, name=NAME)
    res.files.update(resource_files())

    # verification: reparse + structural audit
    m2 = parse_iflow(res.iflw_xml, NAME)
    kinds_in = {s.kind for s in model.steps.values()}
    kinds_out = {s.kind for s in m2.steps.values()}
    missing = kinds_in - kinds_out
    loops = [s for s in m2.steps.values() if getattr(s, "loop", None)]

    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "stress_payload.xml").write_text(payload, encoding="utf-8")

    # package via the proven packager (offline)
    import tempfile
    from fetcher.cpi_uploader import CPIUploader
    tmp = Path(tempfile.mkdtemp())
    iflw = tmp / f"{IFLOW_ID}.iflw"
    iflw.write_text(res.iflw_xml, encoding="utf-8")
    meta = tmp / f"{IFLOW_ID}__meta"
    (meta).mkdir()
    (meta / "MANIFEST.MF").write_text(res.manifest, encoding="utf-8")
    (meta / ".project").write_text(res.project_xml, encoding="utf-8")
    for rel, content in res.files.items():
        if not rel.startswith("src/main/resources/") or \
                rel.startswith("src/main/resources/scenarioflows/"):
            continue
        dest = meta / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    u = CPIUploader.__new__(CPIUploader)
    zb = u._package_iflow(iflw, IFLOW_ID, NAME, "")
    (out / "StressLab_bundle.zip").write_bytes(zb)
    names = sorted(zipfile.ZipFile(io.BytesIO(zb)).namelist())
    return {"missing_kinds": sorted(missing), "n_steps": len(m2.steps),
            "loops": len(loops), "bundle_files": len(names),
            "bundle_bytes": len(zb), "payload_bytes": len(payload)}


if __name__ == "__main__":
    info = build(sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/outputs")
    print(info)
