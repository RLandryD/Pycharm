"""tools/build_stress_lab_edi.py — build the CPI "Stress Lab EDI" iFlow.

Covers the two converters round 4 left unverified: **EDI to XML** and
**XML to EDI** — as a self-contained X12 850 round trip:

    Timer (fireNow) → CM seeds a raw X12 850 interchange → EDI to XML
    (ASC-X12 850/004010 schema) → Groovy logs the XML → XML to EDI (same
    schema) → Groovy logs the regenerated EDI → Message End
    (+ exception subprocess that attaches conversion errors to the MPL)

Both converter ifl:property sets are harvested VERBATIM from the
B2B Interface Migration Accelerator packages in the standard-content corpus
(EDItoXMLConverter v2.5.0 / XMLtoEDIConverter v2.5.0); only the schema-table
VALUE points at the 850 schema. How to configure them (what the editor wants):
  • "EDI Schema" source = Integration Project, schema = /xsd/<X12 xsd>
    (the x12SchemaTable rows do exactly this)
  • separators/encodings: the harvested defaults (ISO-8859-1, * : ~ ^)
  • the message itself must carry full ISA/GS … GE/IEA envelopes; the
    converter picks the schema by the ST-01 transaction id (850).

The ASC-X12 XSD is SAP-shipped content with third-party copyright (X12/WPC),
so this builder does NOT embed it: it pulls `ASC-X12_850_004010.xsd` out of
YOUR corpus (the B2B Accelerator package zip) at generation time and refuses
to build without it.

Usage:
    python3 tools/build_stress_lab_edi.py [outdir] [corpus_dir_or_zip]
corpus default: Resources/Packages (workbench convention).
Writes: StressLabEDI_bundle.zip, stress_payload_850.edi, into outdir.
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extractor.iflow_parser import IFlowModel, Process   # noqa: E402

NAME = "Stress Lab EDI"
IFLOW_ID = "StressLabEDI"
_X12_XSD_NAME = "ASC-X12_850_004010.xsd"

_SCHED = ("<row><cell>dateType</cell><cell></cell></row><row><cell>timeType"
          "</cell><cell></cell></row><row><cell>dayValue</cell><cell></cell>"
          "</row><row><cell>monthValue</cell><cell></cell></row><row><cell>"
          "yearValue</cell><cell></cell></row><row><cell>onWeekly</cell>"
          "<cell></cell></row><row><cell>onMonthly</cell><cell></cell></row>"
          "<row><cell>OnEveryMinute</cell><cell></cell></row><row><cell>"
          "fromInterval</cell><cell></cell></row><row><cell>toInterval</cell>"
          "<cell></cell></row><row><cell>timeZone</cell><cell>( UTC 0:00 ) "
          "Greenwich Mean Time(Etc/GMT)</cell></row><row><cell>secondValue"
          "</cell><cell>0</cell></row><row><cell>minutesValue</cell><cell>"
          "</cell></row><row><cell>hourValue</cell><cell></cell></row><row>"
          "<cell>triggerType</cell><cell>simple</cell></row><row><cell>"
          "noOfSchedules</cell><cell>1</cell></row><row><cell>schedule1"
          "</cell><cell>fireNow=true</cell></row>")


def build_x12_850(orders: int = 2) -> str:
    """A minimal, VALID X12 850 interchange matching the trimmed test schema
    (M_850 = ST + BEG + PO1+ + SE). Segment terminator '~' + newline for
    readability; element sep '*', component sep ':' (the harvested converter
    defaults). The author wrote these segments; X12 syntax itself is fact."""
    po1 = [f"PO1*{i}*{3 + i}*EA*{9 + i}.{25 + i:02d}**VP*SKU-{chr(64 + i)}{i}~"
           for i in range(1, orders + 1)]
    body = ["ST*850*0001~", "BEG*00*SA*PO-STRESS-93**20260610~",
            *po1, f"SE*{3 + len(po1)}*0001~"]
    return "\n".join([
        "ISA*00*          *00*          *ZZ*STRESSLAB      *ZZ*CPITENANT      "
        "*260610*1200*U*00401*000000093*0*T*:~",
        "GS*PO*STRESSLAB*CPITENANT*20260610*1200*93*X*004010~",
        *body,
        "GE*1*93~",
        "IEA*1*000000093~",
    ]) + "\n"


_GROOVY_LOG = """import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {
    def body = message.getBody(java.lang.String) as String
    def log = messageLogFactory.getMessageLog(message)
    if (log != null) {
        log.addAttachmentAsString('%s', body ?: '', 'text/plain')
    }
    return message
}
"""

from scaffolder.error_handling import GOLD_CAPTURE_SCRIPT as _GROOVY_ALERT

# harvested VERBATIM from B2B Interface Migration Accelerator (corpus);
# only the x12SchemaTable VALUE adapted to the 850 schema for the round trip
_EDI_TO_XML = {
    "tradacomsSourceEncoding": "ISO-8859-1", "x12SourceEncoding": "ISO-8859-1",
    "edifactSourceEncoding": "ISO-8859-1",
    "tradacomsConversionPreference": "No",
    "tradacomsEdiSchemaSource": "IntegrationProject",
    "componentVersion": "2.5", "edifactEnvelopeTruncator": "true",
    "edifactDecimalCharacter": "fromIncomingPayload",
    "x12EdiSchemaSource": "IntegrationProject",
    "x12EnvelopeTruncator": "false",
    "edifactEdiSchemaSource": "IntegrationProject",
    "activityType": "EDItoXMLConverter",
    "cmdVariantUri":
        "ctype::FlowstepVariant/cname::EDItoXMLConverter/version::2.5.0",
    "x12SchemaTable": "<row><cell id='x12SchemaName'>/xsd/"
                      + _X12_XSD_NAME + "</cell></row>",
    "edifactTargetEncoding": "ISO-8859-1", "tradacomsHeaderName": "",
    "edifactHeaderName": "", "x12HeaderName": "",
    "tradacomsSchemaTable": "", "edifactSchemaTable": "",
}
_XML_TO_EDI = {
    "edifactUseCustomSeparator": "false",
    "edifactSourceEncoding": "ISO-8859-1",
    "tradacomsConversionPreference": "No",
    "x12DataElementSeparator": "#x2a", "x12CompositeSeparator": "#x3a",
    "x12EdiSchemaSource": "IntegrationProject",
    "x12SchemaTable": "<row><cell id='x12SchemaName'>/xsd/"
                      + _X12_XSD_NAME + "</cell></row>",
    "edifactTargetEncoding": "ISO-8859-1",
    "tradacomsSourceEncoding": "ISO-8859-1",
    "edifactSegmentTerminator": "#x27", "x12SourceEncoding": "ISO-8859-1",
    "edifactCompositeSeparator": "#x3a",
    "edifactDataElementSeparator": "#x2b", "x12SegmentTerminator": "#x7e",
    "x12UseCustomSeparator": "false",
    "tradacomsEdiSchemaSource": "IntegrationProject",
    "componentVersion": "2.5", "edifactDecimalCharacter": "#x2e",
    "edifactEdiSchemaSource": "IntegrationProject",
    "activityType": "XMLtoEDIConverter",
    "cmdVariantUri":
        "ctype::FlowstepVariant/cname::XMLtoEDIConverter/version::2.5.0",
    "edifactEscapeCharacter": "#x3f", "x12RepetitionSeparator": "#x5e",
    "tradacomsHeaderName": "", "tradacomsSchemaTable": "",
    "edifactSchemaTable": "", "edifactHeaderName": "", "x12HeaderName": "",
}


def find_x12_schema(corpus: str) -> str:
    """Pull ASC-X12_850_004010.xsd from the user's corpus (dir of package
    zips, or one zip). Raises FileNotFoundError with guidance if absent."""
    import os

    def _scan_zip(zf):
        for n in zf.namelist():
            if n.endswith(_X12_XSD_NAME):
                return zf.read(n).decode("utf-8", "replace")
            if n.endswith("_content"):
                raw = zf.read(n)
                if raw[:2] == b"PK":
                    got = _scan_zip(zipfile.ZipFile(io.BytesIO(raw)))
                    if got:
                        return got
        return None

    paths = []
    if os.path.isdir(corpus):
        for root, _d, files in os.walk(corpus):
            paths += [os.path.join(root, f) for f in files
                      if f.endswith(".zip")
                      and "B2B" in f or f.endswith(".zip") and "X12" in f]
        # fall back to every zip if the name filter found nothing
        if not paths:
            for root, _d, files in os.walk(corpus):
                paths += [os.path.join(root, f) for f in files
                          if f.endswith(".zip")]
    elif os.path.isfile(corpus):
        paths = [corpus]
    for p in paths:
        try:
            got = _scan_zip(zipfile.ZipFile(p))
            if got:
                return got
        except Exception:
            continue
    raise FileNotFoundError(
        f"{_X12_XSD_NAME} not found under '{corpus}'. Download the "
        "'B2B Interface Migration Accelerator - ASCX12' package export into "
        "your Packages folder (it ships the schema), or pass its zip path as "
        "the second argument.")


def build_edi_model(payload: str) -> IFlowModel:
    from tools.build_stress_lab import _step, _wire   # shared helpers
    m = IFlowModel(name=NAME)
    steps, ft = m.steps, {}
    P = "Process_1"
    GS = {"componentVersion": "1.1", "activityType": "Script",
          "cmdVariantUri":
              "ctype::FlowstepVariant/cname::GroovyScript/version::1.1.2",
          "subActivityType": "GroovyScript", "scriptFunction": "",
          "scriptBundleId": ""}
    _step(steps, "StartEvent_1", "StartTimerEvent", "Start Timer 1", {
        "componentVersion": "1.1", "activityType": "StartTimerEvent",
        "cmdVariantUri":
            "ctype::FlowstepVariant/cname::intermediatetimer/version::1.1",
        "scheduleKey": _SCHED}, P)
    _step(steps, "CA_Seed", "Enricher", "CM_Seed_X12_850", {
        "bodyType": "constant", "componentVersion": "1.6",
        "activityType": "Enricher",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::Enricher/version::1.6.0",
        "wrapContent": "", "bodyContent": payload}, P)
    _step(steps, "CA_E2X", "EDItoXMLConverter", "EDI_to_XML_850",
          dict(_EDI_TO_XML), P)
    _step(steps, "CA_LogXml", "Script", "GS_Log_XML",
          dict(GS, script="edi_log_xml.groovy"), P)
    _step(steps, "CA_X2E", "XMLtoEDIConverter", "XML_to_EDI_850",
          dict(_XML_TO_EDI), P)
    _step(steps, "CA_LogEdi", "Script", "GS_Log_EDI",
          dict(GS, script="edi_log_edi.groovy"), P)
    _step(steps, "EndEvent_1", "EndEvent", "End", {
        "componentVersion": "1.1", "activityType": "EndEvent",
        "cmdVariantUri":
            "ctype::FlowstepVariant/cname::MessageEndEvent/version::1.1.0"}, P)
    steps["EndEvent_1"].event_def = "message"
    _step(steps, "SubProcess_Err", "ErrorEventSubProcessTemplate",
          "Exception Subprocess", {
              "componentVersion": "1.1",
              "activityType": "ErrorEventSubProcessTemplate",
              "cmdVariantUri": "ctype::FlowstepVariant/cname::"
                               "ErrorEventSubProcessTemplate/version::1.1.0"},
          P)
    _step(steps, "StartEvent_Err", "StartErrorEvent", "Error Start", {
        "componentVersion": "1.0", "activityType": "StartErrorEvent",
        "cmdVariantUri": "ctype::FlowstepVariant/cname::"
                         "ErrorStartEvent/version::1.0.1"}, P,
          sub="SubProcess_Err")
    _step(steps, "CA_Alert", "Script", "GS_EDI_Alert",
          dict(GS, script="edi_alert.groovy"), P, sub="SubProcess_Err")
    _step(steps, "EndEvent_Err", "EndEvent", "End Error", {
        "componentVersion": "1.1", "activityType": "EndEvent",
        "cmdVariantUri":
            "ctype::FlowstepVariant/cname::MessageEndEvent/version::1.1.0"},
          P, sub="SubProcess_Err")
    steps["EndEvent_Err"].event_def = "message"

    chain = ["StartEvent_1", "CA_Seed", "CA_E2X", "CA_LogXml", "CA_X2E",
             "CA_LogEdi", "EndEvent_1"]
    for i in range(len(chain) - 1):
        _wire(steps, ft, f"SequenceFlow_{i}", chain[i], chain[i + 1])
    _wire(steps, ft, "SequenceFlow_E1", "StartEvent_Err", "CA_Alert")
    _wire(steps, ft, "SequenceFlow_E2", "CA_Alert", "EndEvent_Err")

    m.processes = [Process(id=P, name="Integration Process", is_main=True,
                           step_ids=list(steps))]
    m.sequence = chain
    m._flow_target = ft
    m.flow_props = {}
    m.routes = []
    return m


def build(outdir: str = "/mnt/user-data/outputs",
          corpus: str = "Resources/Packages") -> dict:
    from scaffolder.model_generator import generate_from_model
    from extractor.iflow_parser import parse_iflow
    from fetcher.cpi_uploader import CPIUploader
    import tempfile

    xsd = find_x12_schema(corpus)
    payload = build_x12_850()
    res = generate_from_model(build_edi_model(payload), name=NAME)
    res.files.update({
        f"src/main/resources/xsd/{_X12_XSD_NAME}": xsd,
        "src/main/resources/script/edi_log_xml.groovy":
            _GROOVY_LOG % "EDItoXML_Output",
        "src/main/resources/script/edi_log_edi.groovy":
            _GROOVY_LOG % "XMLtoEDI_Output",
        "src/main/resources/script/edi_alert.groovy": _GROOVY_ALERT,
    })
    m2 = parse_iflow(res.iflw_xml, NAME)

    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "stress_payload_850.edi").write_text(payload, encoding="utf-8")
    tmp = Path(tempfile.mkdtemp())
    iflw = tmp / f"{IFLOW_ID}.iflw"
    iflw.write_text(res.iflw_xml, encoding="utf-8")
    meta = tmp / f"{IFLOW_ID}__meta"
    meta.mkdir()
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
    (out / "StressLabEDI_bundle.zip").write_bytes(zb)
    names = zipfile.ZipFile(io.BytesIO(zb)).namelist()
    return {"n_steps": len(m2.steps), "bundle_files": len(names),
            "bundle_bytes": len(zb),
            "schema_shipped": any(_X12_XSD_NAME in n for n in names)}


if __name__ == "__main__":
    outdir = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/outputs"
    corpus = sys.argv[2] if len(sys.argv) > 2 else "Resources/Packages"
    print(build(outdir, corpus))
