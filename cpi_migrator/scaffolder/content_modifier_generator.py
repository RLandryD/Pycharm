"""
scaffolder/content_modifier_generator.py

Generates SAP CPI Content Modifier step definitions from channel/config data.

A Content Modifier sets message headers, exchange properties, and (optionally)
the body before a message reaches the next step. This is the single biggest
lever for taking generated iFlows from "skeleton" to "partially configured":
instead of an empty Start->End flow, the iFlow arrives with the headers and
properties a real interface needs, derived from the source PI channel.

Produces two artifacts:
  1. A `<bpmn2:callActivity>` Content Modifier step (BPMN fragment) that can
     be spliced into an iFlow's process flow.
  2. A standalone descriptor (dict / JSON) listing the headers + properties,
     useful for documentation and for the workbench UI to display.

Verified: structural (XML well-formedness + round-trip parse). NOT deployed
to a tenant — the BPMN namespace/activityType strings follow documented CPI
conventions but must be validated by importing into Integration Suite.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ContentModifierEntry:
    """One header or property the Content Modifier will set."""
    name: str
    value: str
    source_type: str = "constant"   # constant | xpath | header | property | expression
    data_type: str = "java.lang.String"


@dataclass
class ContentModifierSpec:
    """Full specification for a Content Modifier step."""
    step_name: str
    headers: list[ContentModifierEntry] = field(default_factory=list)
    properties: list[ContentModifierEntry] = field(default_factory=list)
    body_expression: Optional[str] = None   # None = leave body untouched

    def is_empty(self) -> bool:
        return not self.headers and not self.properties and self.body_expression is None


def build_from_channel(channel, interface_name: str = "") -> ContentModifierSpec:
    """Derive a Content Modifier spec from a parsed PI ChannelConfig.

    Maps the channel's transport/auth/endpoint details into the headers and
    properties a CPI iFlow typically needs. Only emits entries for data that
    is actually present on the channel (no empty placeholders).
    """
    spec = ContentModifierSpec(step_name=f"CM_{interface_name or channel.channel_name}".replace(" ", "_"))

    # Correlation / tracing — always useful
    spec.properties.append(ContentModifierEntry(
        "OriginalChannel", channel.channel_name, "constant"))
    spec.properties.append(ContentModifierEntry(
        "AdapterType", channel.adapter_type, "constant"))
    spec.properties.append(ContentModifierEntry(
        "Direction", channel.direction, "constant"))

    # Endpoint info as properties (so downstream steps / dynamic receivers can use them)
    if getattr(channel, "endpoint_url", ""):
        spec.properties.append(ContentModifierEntry("TargetEndpoint", channel.endpoint_url, "constant"))
    if getattr(channel, "address", ""):
        spec.properties.append(ContentModifierEntry("TargetAddress", channel.address, "constant"))
    if getattr(channel, "path", ""):
        spec.properties.append(ContentModifierEntry("TargetPath", channel.path, "constant"))

    # Auth hint (never the credential itself — just the alias + type)
    if getattr(channel, "auth_type", ""):
        spec.properties.append(ContentModifierEntry("AuthType", channel.auth_type, "constant"))
    if getattr(channel, "credential_name", ""):
        spec.properties.append(ContentModifierEntry("CredentialAlias", channel.credential_name, "constant"))

    # Adapter-specific headers
    adapter = (channel.adapter_type or "").upper()
    if "IDOC" in adapter:
        if getattr(channel, "idoc_type", ""):
            spec.headers.append(ContentModifierEntry("SAP_IDocType", channel.idoc_type, "constant"))
        if getattr(channel, "idoc_message_type", ""):
            spec.headers.append(ContentModifierEntry("SAP_IDocMessageType", channel.idoc_message_type, "constant"))
        if getattr(channel, "idoc_partner_number", ""):
            spec.headers.append(ContentModifierEntry("SAP_IDocPartner", channel.idoc_partner_number, "constant"))
    elif "RFC" in adapter:
        if getattr(channel, "rfc_destination", ""):
            spec.properties.append(ContentModifierEntry("RFCDestination", channel.rfc_destination, "constant"))
        if getattr(channel, "function_module", ""):
            spec.properties.append(ContentModifierEntry("RFCFunctionModule", channel.function_module, "constant"))
    elif "FILE" in adapter or "SFTP" in adapter or "FTP" in adapter:
        if getattr(channel, "file_directory", ""):
            spec.properties.append(ContentModifierEntry("FileDirectory", channel.file_directory, "constant"))
        if getattr(channel, "file_pattern", ""):
            spec.headers.append(ContentModifierEntry("CamelFileName", channel.file_pattern, "constant"))
    elif "JDBC" in adapter:
        if getattr(channel, "jdbc_url", ""):
            spec.properties.append(ContentModifierEntry("JDBCUrl", channel.jdbc_url, "constant"))

    # Any extra channel parameters become properties
    for k, v in (getattr(channel, "parameters", {}) or {}).items():
        if v:
            spec.properties.append(ContentModifierEntry(f"param_{k}", str(v), "constant"))

    return spec


def render_descriptor(spec: ContentModifierSpec) -> dict:
    """Return a plain-dict descriptor for documentation / UI display."""
    return {
        "step_name": spec.step_name,
        "headers": [{"name": e.name, "value": e.value, "type": e.source_type} for e in spec.headers],
        "properties": [{"name": e.name, "value": e.value, "type": e.source_type} for e in spec.properties],
        "body_expression": spec.body_expression,
    }


def _entry_xml(entry: ContentModifierEntry, kind: str) -> str:
    """Render one header/property as the CPI property-table row XML.

    Real CPI format (decoded from a production package): each row uses
    <cell id='...'> elements, NOT <id>/<Name> children. Confirmed against
    RCI093 package. The cell ids are: Action, Type, Value, Default, Name,
    Datatype.
    """
    val  = html.escape(entry.value or "", quote=True)
    name = html.escape(entry.name, quote=True)
    dtype = html.escape(entry.data_type or "", quote=True)
    return (
        "<row>"
        "<cell id='Action'>Create</cell>"
        f"<cell id='Type'>{entry.source_type}</cell>"
        f"<cell id='Value'>{val}</cell>"
        "<cell id='Default'></cell>"
        f"<cell id='Name'>{name}</cell>"
        f"<cell id='Datatype'>{dtype}</cell>"
        "</row>"
    )


def render_bpmn_step(spec: ContentModifierSpec, step_id: str = "ContentModifier_1") -> str:
    """Render the Content Modifier as a BPMN callActivity fragment.

    This fragment is spliced into an iFlow process between two sequence-flow
    connected steps. The activityType=Enricher with the content-modifier
    properties is the documented CPI representation.
    """
    header_rows = "".join(_entry_xml(e, "header") for e in spec.headers)
    prop_rows = "".join(_entry_xml(e, "property") for e in spec.properties)
    # Real CPI stores the tables as an HTML-escaped <row>… string inside the
    # <value> (decoded from production). Empty table = empty value.
    header_value = html.escape(f"<root>{header_rows}</root>", quote=True) if spec.headers else ""
    prop_value   = html.escape(f"<root>{prop_rows}</root>", quote=True) if spec.properties else ""

    body_props = ""
    if spec.body_expression is not None:
        body_props = (
            "        <ifl:property>\n"
            "            <key>bodyType</key>\n"
            "            <value>expression</value>\n"
            "        </ifl:property>\n"
        )

    return f"""<bpmn2:callActivity id="{step_id}" name="{html.escape(spec.step_name, quote=True)}">
    <bpmn2:extensionElements>
        <ifl:property>
            <key>componentVersion</key>
            <value>1.6</value>
        </ifl:property>
        <ifl:property>
            <key>activityType</key>
            <value>Enricher</value>
        </ifl:property>
        <ifl:property>
            <key>bodyType</key>
            <value>{'expression' if spec.body_expression is not None else 'constant'}</value>
        </ifl:property>
        <ifl:property>
            <key>headerTable</key>
            <value>{header_value}</value>
        </ifl:property>
        <ifl:property>
            <key>propertyTable</key>
            <value>{prop_value}</value>
        </ifl:property>
        <ifl:property>
            <key>wrapContent</key>
            <value></value>
        </ifl:property>
{body_props}    </bpmn2:extensionElements>
</bpmn2:callActivity>"""
