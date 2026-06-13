"""
scaffolder/pipeline_scaffolder.py

Implements the SAP-recommended Pipeline Concept for bulk migrations.
Instead of one iFlow per interface (which hits JMS limits at scale),
generates a 4-iFlow pipeline structure + Partner Directory JSON + 8 JMS queues.

Architecture (mirrors SAP PO Advanced Adapter Engine):
  1. Generic Inbound iFlow     — receives all messages, routes via Partner Directory
  2. Receiver Determination    — looks up routing rules from Partner Directory
  3. Interface Separation      — routes to correct scenario-specific iFlow
  4. Scenario-specific iFlow   — contains the actual mapping + receiver adapter
  5. Outbound Delivery Engine  — generic sender, reads endpoint from Partner Directory

All internal hops use ProcessDirect adapter (in-memory, zero latency).
Async interfaces use 4 core JMS queues + 4 dead-letter queues = 8 total.

Also handles:
  - Batch processing (Splitter + Aggregator pattern)
  - Package naming and grouping
  - Greenfield / Brownfield / Bluefield strategy per interface
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Migration strategy definitions
# ---------------------------------------------------------------------------

STRATEGIES = {
    "greenfield": {
        "label":       "Greenfield (Clean Slate)",
        "description": "Discard legacy logic. Rebuild using cloud-native APIs and standard content.",
        "when":        "Legacy is undocumented, heavily customised, or target has standard API.",
        "output":      "Clean iFlow stubs using standard CPI patterns. No legacy mapping reuse.",
    },
    "brownfield": {
        "label":       "Brownfield (Lift & Shift)",
        "description": "Extract and port existing mappings and channel config directly.",
        "when":        "Existing mappings are stable, complex, and working perfectly.",
        "output":      "iFlows that mirror PI/PO structure. Mapping programs referenced directly.",
    },
    "bluefield": {
        "label":       "Bluefield (Selective Hybrid)",
        "description": "Lift-and-shift stable core interfaces. Rewrite unoptimised ones.",
        "when":        "Mixed landscape — some standard, some heavily customised.",
        "output":      "Mix of Brownfield and Greenfield per interface based on complexity.",
    },
}

# ---------------------------------------------------------------------------
# Package naming
# ---------------------------------------------------------------------------

BUSINESS_DOMAINS = {
    "finance":      ["invoice", "payment", "fi", "accounting", "gl", "ar", "ap", "cost", "bank"],
    "procurement":  ["purchase", "po", "order", "vendor", "mm", "ariba", "sourcing", "goods"],
    "logistics":    ["delivery", "shipment", "warehouse", "transport", "ewm", "tm", "le"],
    "hr":           ["employee", "payroll", "hr", "hcm", "successfactors", "sf", "org", "position"],
    "sales":        ["sales", "crm", "customer", "sd", "opportunity", "quote", "contract"],
    "manufacturing":["production", "plant", "bom", "pp", "quality", "maintenance", "pm"],
    "integration":  ["idoc", "rfc", "bapi", "xi", "proxy", "generic", "utility"],
    "b2b":          ["as2", "as4", "edi", "edifact", "x12", "partner", "b2b", "trading"],
}

DIRECTION_MAP = {
    ("sender", "receiver"): "OUT",   # outbound from sender
    ("receiver", "sender"): "IN",    # inbound to sender
}


def detect_domain(interface_name: str, description: str = "") -> str:
    text = (interface_name + " " + description).lower()
    for domain, keywords in BUSINESS_DOMAINS.items():
        if any(kw in text for kw in keywords):
            return domain.capitalize()
    return "Integration"


_WORD_SEP = "_"


def set_word_separator(sep: str) -> None:
    """Client naming preference for GENERATED names (space | _ | -). Applies
    to iFlow and package names; CPI ids stay sanitized separately."""
    global _WORD_SEP
    _WORD_SEP = sep if sep in (" ", "_", "-") else "_"


def generate_package_display_name(
    sender_system: str,
    receiver_system: str,
    domain: str = "",
) -> str:
    """Human-readable package NAME following SAP's Discover convention.

    Verified against 97 real packages (corpus2): SAP names integration
    packages as:
        "SAP <Source> Integration [for <Scope>] with SAP <Target> [<Edition>]"
    e.g. "SAP Sales Cloud Version 2 Integration for Sales Processes with
           SAP S/4HANA Cloud Public Edition"
    And the per-iFlow description follows:
        "Replicate <Object> from <Source> to <Target>"

    We follow the package form here. No hardcoded "Migration" prefix — that
    was wrong; real packages don't use it.
    """
    src = _pretty_system(sender_system) or "Source System"
    tgt = _pretty_system(receiver_system) or "Target System"
    # Prefix "SAP " only when the system looks like an SAP product
    src_label = src if src.upper().startswith("SAP") else f"SAP {src}" if _looks_sap(src) else src
    tgt_label = tgt if tgt.upper().startswith("SAP") else f"SAP {tgt}" if _looks_sap(tgt) else tgt
    scope = _pretty_label(domain) if domain else ""
    if scope:
        return f"{src_label} Integration for {scope} with {tgt_label}"
    return f"{src_label} Integration with {tgt_label}"


def _looks_sap(name: str) -> bool:
    """Heuristic: is this an SAP product name (gets the 'SAP ' prefix)?"""
    n = (name or "").upper()
    sap_products = ("ERP", "ECC", "S/4", "S4", "HANA", "ARIBA", "SUCCESSFACTORS",
                    "SALES CLOUD", "SERVICE CLOUD", "FIELD SERVICE", "IBP",
                    "CPQ", "CONCUR", "FIORI", "BTP")
    return any(p in n for p in sap_products)


def generate_package_name(
    company_code: str,
    sender_system: str,
    receiver_system: str,
    domain: str,
) -> str:
    """
    Internal package grouping name (legacy underscore form, kept for
    compatibility). For the human display name use
    generate_package_display_name(); for the CPI Id the uploader sanitizes
    to alphanumeric.
    """
    parts = [
        _clean(company_code) if company_code else "",
        _clean(sender_system or "SRC"),
        _clean(receiver_system or "TGT"),
        _clean(domain or "Integration") if not _url_token(domain) else _clean(_url_token(domain)),
    ]
    name = _WORD_SEP.join(p for p in parts if p)
    return name[:60]


def _pretty_system(text: str) -> str:
    """Turn a raw system id / URL into a readable product-ish label.

    Strips URL schemes, hosts, and paths so endpoints like
    'http://company.com/ariba' don't leak into names as 'httpcompanycomariba'.
    """
    s = str(text or "").strip()
    if not s:
        return ""
    # Drop URL scheme + host: keep only a meaningful trailing token if it's a URL
    m = re.match(r"^[a-z][a-z0-9+.\-]*://([^/]+)(/.*)?$", s, re.IGNORECASE)
    if m:
        path = (m.group(2) or "").strip("/")
        host = m.group(1)
        # Prefer the last path segment (often the system), else the host's first label
        token = path.split("/")[-1] if path else host.split(".")[0]
        s = token
    # Replace separators with spaces, title-case modestly
    s = re.sub(r"[._\-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:40]


def _url_token(text: str) -> str:
    """If `text` is a URL/namespace, reduce it to its functionality token (the
    last path segment), e.g. 'http://company.com/ariba' -> 'ariba'. The company
    host is never kept. Non-URLs pass through unchanged."""
    s = str(text or "").strip()
    m = re.match(r"^[a-z][a-z0-9+.\-]*://([^/]+)(/.*)?$", s, re.IGNORECASE)
    if m:
        path = (m.group(2) or "").strip("/")
        return path.split("/")[-1] if path else m.group(1).split(".")[0]
    return s


def _pretty_label(text: str) -> str:
    s = re.sub(r"[._\-]+", " ", _url_token(text)).strip()
    return re.sub(r"\s+", " ", s)[:40].title()


def generate_iflow_name(
    direction: str,
    sender_system: str,
    receiver_system: str,
    business_object: str,
    action: str = "",
) -> str:
    """
    Standard SAP naming convention:
    <Direction>_<SourceSystem>_<TargetSystem>_<BusinessObject>_<Action>
    """
    parts = [
        direction.upper() or "OUT",
        _clean(sender_system or "SRC"),
        _clean(receiver_system or "TGT"),
        _clean(business_object or "Message"),
    ]
    if action:
        parts.append(_clean(action))
    name = _WORD_SEP.join(parts)
    return name[:80]  # CPI iFlow name limit


def _clean(text: str) -> str:
    """Remove special chars for an internal name token. Strips URL schemes and
    hosts first so endpoints don't leak as 'httpcompanycom...'."""
    s = str(text or "")
    # If it looks like a URL, reduce to a meaningful token
    m = re.match(r"^[a-z][a-z0-9+.\-]*://([^/]+)(/.*)?$", s, re.IGNORECASE)
    if m:
        path = (m.group(2) or "").strip("/")
        s = path.split("/")[-1] if path else m.group(1).split(".")[0]
    text = re.sub(r"[^\w\s]", "", s).strip()
    text = re.sub(r"\s+", "_", text)
    return text[:20]


# ---------------------------------------------------------------------------
# Pipeline Concept data model
# ---------------------------------------------------------------------------

@dataclass
class PipelinePackage:
    """One pipeline package = one sender/receiver system pair."""
    package_id: str
    package_name: str
    company_code: str
    sender_system: str
    receiver_system: str
    domain: str
    strategy: str                           # greenfield / brownfield / bluefield
    interfaces: list = field(default_factory=list)   # list[MigrationAssessment]
    jms_queues: list[str] = field(default_factory=list)
    partner_directory: dict = field(default_factory=dict)
    iflow_paths: list[Path] = field(default_factory=list)


@dataclass
class BatchConfig:
    """Configuration for batch processing interfaces."""
    enabled: bool = False
    split_by: str = "element"              # "element" / "size" / "xpath"
    split_expression: str = ""             # XPath for xpath split
    chunk_size: int = 100                  # for size-based split
    aggregation_condition: str = ""        # completion condition
    correlation_expression: str = ""       # correlation header/xpath
    timeout_minutes: int = 30


# ---------------------------------------------------------------------------
# Pipeline Scaffolder
# ---------------------------------------------------------------------------

class PipelineScaffolder:
    """
    Generates the SAP-recommended Pipeline Concept iFlow structure
    for bulk migrations (10+ interfaces or user-selected).

    Outputs per package:
      - Generic_Inbound_{PackageId}.iflw
      - Receiver_Determination_{PackageId}.iflw
      - Interface_Separation_{PackageId}.iflw
      - Outbound_Delivery_{PackageId}.iflw
      - {InterfaceName}.iflw  (one per scenario-specific interface)
      - PartnerDirectory_{PackageId}.json
      - JMSQueues_{PackageId}.json
    """

    # JMS queue naming
    JMS_QUEUES = [
        "{pkg}_A2A_Sync_In",          # Core queue 1: A2A synchronous inbound
        "{pkg}_A2A_Async_In",         # Core queue 2: A2A asynchronous inbound
        "{pkg}_B2B_In",               # Core queue 3: B2B inbound
        "{pkg}_Internal_In",          # Core queue 4: internal/utility
        "{pkg}_A2A_Sync_DLQ",         # Dead-letter 1
        "{pkg}_A2A_Async_DLQ",        # Dead-letter 2
        "{pkg}_B2B_DLQ",              # Dead-letter 3
        "{pkg}_Internal_DLQ",         # Dead-letter 4
    ]

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.iflows_dir = self.output_dir / "pipeline_iflows"
        self.iflows_dir.mkdir(parents=True, exist_ok=True)

    def group_into_packages(
        self,
        assessments: list,
        configs: dict,
        company_code: str = "COMP",
        strategy: str = "bluefield",
        custom_package_names: dict = None,     # {(sender, receiver): package_name}
    ) -> list[PipelinePackage]:
        """Group interfaces by sender+receiver system into pipeline packages."""
        custom_package_names = custom_package_names or {}
        groups: dict[tuple, list] = {}

        for a in assessments:
            iface = a.interface
            key = (iface.sender_system or "SRC", iface.receiver_system or "TGT")
            groups.setdefault(key, []).append(a)

        packages = []
        for (sender, receiver), iface_list in groups.items():
            # Detect domain from interface names
            all_names = " ".join(a.interface.name for a in iface_list)
            domain = detect_domain(all_names)

            # Package name — custom override or auto-generated
            override = custom_package_names.get((sender, receiver))
            pkg_name = override or generate_package_name(company_code, sender, receiver, domain)
            pkg_id   = _clean(pkg_name)

            # Per-interface strategy (bluefield: use complexity to decide)
            pkg_strategy = strategy
            if strategy == "bluefield":
                pkg_strategy = "bluefield"  # resolved per interface during scaffold

            pkg = PipelinePackage(
                package_id=pkg_id,
                package_name=pkg_name,
                company_code=company_code,
                sender_system=sender,
                receiver_system=receiver,
                domain=domain,
                strategy=pkg_strategy,
                interfaces=iface_list,
                jms_queues=[q.format(pkg=pkg_id[:15]) for q in self.JMS_QUEUES],
            )
            packages.append(pkg)

        logger.info("Grouped %d interfaces into %d pipeline packages",
                    len(assessments), len(packages))
        return packages

    def scaffold_package(
        self,
        pkg: PipelinePackage,
        configs: dict,
    ) -> PipelinePackage:
        """Generate all iFlows + Partner Directory for one package."""
        pkg_dir = self.iflows_dir / pkg.package_id
        pkg_dir.mkdir(parents=True, exist_ok=True)

        paths = []

        # 1. Generic Inbound iFlow
        paths.append(self._write_generic_inbound(pkg, pkg_dir))

        # 2. Receiver Determination iFlow
        paths.append(self._write_receiver_determination(pkg, pkg_dir))

        # 3. Interface Separation iFlow
        paths.append(self._write_interface_separation(pkg, pkg_dir))

        # 4. Outbound Delivery iFlow
        paths.append(self._write_outbound_delivery(pkg, pkg_dir))

        # 5. Scenario-specific iFlows (one per interface)
        for a in pkg.interfaces:
            cfg        = configs.get(a.interface.name)
            iface_strat = self._resolve_strategy(a, pkg.strategy)
            batch_cfg  = self._detect_batch(a, cfg)
            path       = self._write_scenario_iflow(a, cfg, pkg, iface_strat, batch_cfg, pkg_dir)
            paths.append(path)

        # 6. Partner Directory JSON
        pd_path = self._write_partner_directory(pkg, configs, pkg_dir)
        paths.append(pd_path)

        # 7. JMS Queue config
        jms_path = self._write_jms_queues(pkg, pkg_dir)
        paths.append(jms_path)

        pkg.iflow_paths = paths
        logger.info("Scaffolded package %s: %d files", pkg.package_name, len(paths))
        return pkg

    def scaffold_all(
        self,
        assessments: list,
        configs: dict,
        company_code: str = "COMP",
        strategy: str = "bluefield",
        custom_package_names: dict = None,
    ) -> list[PipelinePackage]:
        packages = self.group_into_packages(
            assessments, configs, company_code, strategy, custom_package_names
        )
        for pkg in packages:
            self.scaffold_package(pkg, configs)
        return packages

    # ── iFlow writers ─────────────────────────────────────────────────

    def _write_generic_inbound(self, pkg: PipelinePackage, pkg_dir: Path) -> Path:
        content = f'''<?xml version="1.0" encoding="UTF-8"?>
<!--
  PIPELINE STEP 1: Generic Inbound iFlow
  Package  : {pkg.package_name}
  Purpose  : Receives ALL inbound messages for this package.
             Routes to Receiver Determination via ProcessDirect.
  Adapter  : Configured per sender system (HTTPS/IDoc/File/SOAP)
  Internal : Uses ProcessDirect to call Receiver Determination
  JMS      : Async messages queued to {pkg.jms_queues[1] if len(pkg.jms_queues) > 1 else "async_queue"}
-->
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             xmlns:ifl="http:///com.sap.ifl.model/Ifl.xsd"
             id="Generic_Inbound_{pkg.package_id}"
             name="Generic_Inbound_{pkg.package_id}">
  <process id="Generic_Inbound_{pkg.package_id}_process" ifl:type="IntegrationFlow">

    <!-- START: Inbound from {pkg.sender_system} -->
    <startEvent id="Start_1" name="Receive from {pkg.sender_system}">
      <extensionElements>
        <ifl:property><ifl:key>Transport</ifl:key>
          <ifl:value>HTTPS</ifl:value><!-- TODO: Set to actual sender adapter -->
        </ifl:property>
      </extensionElements>
    </startEvent>

    <!-- CONTENT MODIFIER: Set routing headers from Partner Directory -->
    <serviceTask id="SetHeaders_1" name="Set Routing Headers" ifl:type="ContentModifier">
      <extensionElements>
        <!-- TODO: Set SAP_Sender, SAP_Receiver, SAP_MsgType from incoming message -->
        <ifl:property><ifl:key>CamelSapSender</ifl:key><ifl:value>{pkg.sender_system}</ifl:value></ifl:property>
      </extensionElements>
    </serviceTask>

    <!-- PROCESSDIRECT: Call Receiver Determination -->
    <serviceTask id="CallReceiverDet_1" name="→ Receiver Determination" ifl:type="RequestReply">
      <extensionElements>
        <ifl:property><ifl:key>Transport</ifl:key><ifl:value>ProcessDirect</ifl:value></ifl:property>
        <ifl:property><ifl:key>Address</ifl:key>
          <ifl:value>/pd/ReceiverDetermination_{pkg.package_id}</ifl:value>
        </ifl:property>
      </extensionElements>
    </serviceTask>

    <endEvent id="End_1" name="End"/>
    <sequenceFlow sourceRef="Start_1" targetRef="SetHeaders_1"/>
    <sequenceFlow sourceRef="SetHeaders_1" targetRef="CallReceiverDet_1"/>
    <sequenceFlow sourceRef="CallReceiverDet_1" targetRef="End_1"/>
  </process>
</definitions>'''
        path = pkg_dir / f"Generic_Inbound_{pkg.package_id}.iflw"
        path.write_text(content, "utf-8")
        return path

    def _write_receiver_determination(self, pkg: PipelinePackage, pkg_dir: Path) -> Path:
        interface_routes = "\n".join([
            f"        <!-- Route: {a.interface.name} → {a.interface.receiver_system} -->"
            for a in pkg.interfaces
        ])
        content = f'''<?xml version="1.0" encoding="UTF-8"?>
<!--
  PIPELINE STEP 2: Receiver Determination iFlow
  Package  : {pkg.package_name}
  Purpose  : Queries Partner Directory to determine correct receiver.
             Routes to Interface Separation via ProcessDirect.
  Internal : ProcessDirect address /pd/ReceiverDetermination_{pkg.package_id}
  Partner Directory keys queried at runtime for each message.
-->
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             xmlns:ifl="http:///com.sap.ifl.model/Ifl.xsd"
             id="Receiver_Determination_{pkg.package_id}"
             name="Receiver_Determination_{pkg.package_id}">
  <process id="ReceiverDet_{pkg.package_id}_process" ifl:type="IntegrationFlow">

    <startEvent id="Start_PD" name="From Generic Inbound">
      <extensionElements>
        <ifl:property><ifl:key>Transport</ifl:key><ifl:value>ProcessDirect</ifl:value></ifl:property>
        <ifl:property><ifl:key>Address</ifl:key>
          <ifl:value>/pd/ReceiverDetermination_{pkg.package_id}</ifl:value>
        </ifl:property>
      </extensionElements>
    </startEvent>

    <!-- GROOVY: Query Partner Directory for receiver routing -->
    <serviceTask id="QueryPD_1" name="Query Partner Directory" ifl:type="Script">
      <extensionElements>
        <ifl:property><ifl:key>ScriptLanguage</ifl:key><ifl:value>Groovy</ifl:value></ifl:property>
        <ifl:property><ifl:key>Script</ifl:key><ifl:value>
// Query Partner Directory for receiver routing
import com.sap.gateway.ip.core.customdev.util.Message
import com.sap.it.api.pd.PartnerDirectoryService

def Message processData(Message message) {{
    def service = ITApiFactory.getApi(PartnerDirectoryService.class)
    def sender  = message.getHeaders().get("SAP_Sender")
    def msgType = message.getHeaders().get("SAP_MsgType")
    def pdKey   = "ReceiverRouting_${{sender}}_${{msgType}}"

    def receiver = service.getParameter(pdKey, sender, String.class)
    message.setHeader("SAP_Receiver", receiver ?: "UNKNOWN")

    // Determine interface name for routing
    def ifaceKey = "InterfaceName_${{sender}}_${{msgType}}"
    def ifaceName = service.getParameter(ifaceKey, sender, String.class)
    message.setHeader("SAP_InterfaceName", ifaceName ?: msgType)
    return message
}}
        </ifl:value></ifl:property>
      </extensionElements>
    </serviceTask>

    <!-- Registered routes for this package: -->
{interface_routes}

    <!-- PROCESSDIRECT: Forward to Interface Separation -->
    <serviceTask id="CallIfaceSep_1" name="→ Interface Separation" ifl:type="RequestReply">
      <extensionElements>
        <ifl:property><ifl:key>Transport</ifl:key><ifl:value>ProcessDirect</ifl:value></ifl:property>
        <ifl:property><ifl:key>Address</ifl:key>
          <ifl:value>/pd/InterfaceSeparation_{pkg.package_id}</ifl:value>
        </ifl:property>
      </extensionElements>
    </serviceTask>

    <endEvent id="End_PD"/>
    <sequenceFlow sourceRef="Start_PD" targetRef="QueryPD_1"/>
    <sequenceFlow sourceRef="QueryPD_1" targetRef="CallIfaceSep_1"/>
    <sequenceFlow sourceRef="CallIfaceSep_1" targetRef="End_PD"/>
  </process>
</definitions>'''
        path = pkg_dir / f"Receiver_Determination_{pkg.package_id}.iflw"
        path.write_text(content, "utf-8")
        return path

    def _write_interface_separation(self, pkg: PipelinePackage, pkg_dir: Path) -> Path:
        routes = "\n".join([
            f'''    <sequenceFlow id="route_{i}" sourceRef="Router_1"
      targetRef="Call_{_clean(a.interface.name)}_1">
      <!-- Condition: ${{header.SAP_InterfaceName}} = '{a.interface.name}' -->
    </sequenceFlow>
    <serviceTask id="Call_{_clean(a.interface.name)}_1"
      name="→ {a.interface.name}" ifl:type="RequestReply">
      <extensionElements>
        <ifl:property><ifl:key>Transport</ifl:key><ifl:value>ProcessDirect</ifl:value></ifl:property>
        <ifl:property><ifl:key>Address</ifl:key>
          <ifl:value>/scenario/{_clean(a.interface.name)}</ifl:value>
        </ifl:property>
      </extensionElements>
    </serviceTask>'''
            for i, a in enumerate(pkg.interfaces)
        ])
        content = f'''<?xml version="1.0" encoding="UTF-8"?>
<!--
  PIPELINE STEP 3: Interface Separation iFlow
  Package  : {pkg.package_name}
  Purpose  : Routes to the correct scenario-specific iFlow
             based on SAP_InterfaceName header set by Receiver Determination.
  Internal : ProcessDirect address /pd/InterfaceSeparation_{pkg.package_id}
-->
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             xmlns:ifl="http:///com.sap.ifl.model/Ifl.xsd"
             id="Interface_Separation_{pkg.package_id}"
             name="Interface_Separation_{pkg.package_id}">
  <process id="IfaceSep_{pkg.package_id}_process" ifl:type="IntegrationFlow">

    <startEvent id="Start_IS" name="From Receiver Determination">
      <extensionElements>
        <ifl:property><ifl:key>Transport</ifl:key><ifl:value>ProcessDirect</ifl:value></ifl:property>
        <ifl:property><ifl:key>Address</ifl:key>
          <ifl:value>/pd/InterfaceSeparation_{pkg.package_id}</ifl:value>
        </ifl:property>
      </extensionElements>
    </startEvent>

    <!-- ROUTER: Branch to scenario-specific iFlow -->
    <exclusiveGateway id="Router_1" name="Route by Interface Name"/>

{routes}

    <endEvent id="End_IS"/>
    <sequenceFlow sourceRef="Start_IS" targetRef="Router_1"/>
  </process>
</definitions>'''
        path = pkg_dir / f"Interface_Separation_{pkg.package_id}.iflw"
        path.write_text(content, "utf-8")
        return path

    def _write_outbound_delivery(self, pkg: PipelinePackage, pkg_dir: Path) -> Path:
        content = f'''<?xml version="1.0" encoding="UTF-8"?>
<!--
  PIPELINE STEP 4: Outbound Delivery Engine iFlow
  Package  : {pkg.package_name}
  Purpose  : Generic sender — reads endpoint from Partner Directory at runtime.
             Handles retry, dead-letter, and error notification.
  Dead-letter queues:
{chr(10).join(f"    {q}" for q in pkg.jms_queues[4:])}
-->
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             xmlns:ifl="http:///com.sap.ifl.model/Ifl.xsd"
             id="Outbound_Delivery_{pkg.package_id}"
             name="Outbound_Delivery_{pkg.package_id}">
  <process id="Outbound_{pkg.package_id}_process" ifl:type="IntegrationFlow">

    <startEvent id="Start_OD" name="From Scenario iFlow">
      <extensionElements>
        <ifl:property><ifl:key>Transport</ifl:key><ifl:value>ProcessDirect</ifl:value></ifl:property>
        <ifl:property><ifl:key>Address</ifl:key>
          <ifl:value>/outbound/{pkg.package_id}</ifl:value>
        </ifl:property>
      </extensionElements>
    </startEvent>

    <!-- GROOVY: Read receiver endpoint from Partner Directory -->
    <serviceTask id="ReadEndpoint_1" name="Read Endpoint from PD" ifl:type="Script">
      <extensionElements>
        <ifl:property><ifl:key>ScriptLanguage</ifl:key><ifl:value>Groovy</ifl:value></ifl:property>
        <ifl:property><ifl:key>Script</ifl:key><ifl:value>
import com.sap.gateway.ip.core.customdev.util.Message
import com.sap.it.api.pd.PartnerDirectoryService

def Message processData(Message message) {{
    def service  = ITApiFactory.getApi(PartnerDirectoryService.class)
    def receiver = message.getHeaders().get("SAP_Receiver")
    def endpoint = service.getParameter("ReceiverEndpoint", receiver, String.class)
    def credAlias = service.getParameter("ReceiverCredential", receiver, String.class)
    message.setHeader("ReceiverEndpoint", endpoint)
    message.setHeader("CredentialAlias",  credAlias)
    return message
}}
        </ifl:value></ifl:property>
      </extensionElements>
    </serviceTask>

    <!-- RECEIVER: Dynamic endpoint from Partner Directory -->
    <!-- TODO: Set adapter type based on receiver — HTTPS/SOAP/IDoc/etc -->
    <endEvent id="End_OD" name="Delivered to {pkg.receiver_system}"/>
    <sequenceFlow sourceRef="Start_OD" targetRef="ReadEndpoint_1"/>
    <sequenceFlow sourceRef="ReadEndpoint_1" targetRef="End_OD"/>
  </process>
</definitions>'''
        path = pkg_dir / f"Outbound_Delivery_{pkg.package_id}.iflw"
        path.write_text(content, "utf-8")
        return path

    def _write_scenario_iflow(
        self,
        assessment,
        cfg,
        pkg: PipelinePackage,
        strategy: str,
        batch_cfg: BatchConfig,
        pkg_dir: Path,
    ) -> Path:
        iface    = assessment.interface
        safe     = _clean(iface.name)
        mapping  = (cfg.message.mapping_program if cfg else "") or iface.mapping_program or ""

        # Auto-generate iFlow name per convention
        domain   = detect_domain(iface.name, iface.description)
        obj_name = _clean(iface.message_interface or iface.name)
        iflow_display_name = generate_iflow_name(
            "OUT", iface.sender_system, iface.receiver_system, obj_name
        )

        strategy_comment = {
            "greenfield": "GREENFIELD: Clean cloud-native rebuild. No legacy mapping reuse.",
            "brownfield": f"BROWNFIELD: Lift-and-shift. Mapping '{mapping}' ported directly.",
            "bluefield":  f"BLUEFIELD: Hybrid. {'Brownfield' if assessment.complexity != 'HIGH' else 'Greenfield'} approach for this interface.",
        }.get(strategy, "")

        batch_section = ""
        if batch_cfg.enabled:
            batch_section = f'''
    <!-- BATCH PROCESSING: Splitter -->
    <serviceTask id="Splitter_1" name="Split Batch" ifl:type="Splitter">
      <extensionElements>
        <ifl:property><ifl:key>SplitType</ifl:key><ifl:value>{batch_cfg.split_by}</ifl:value></ifl:property>
        <ifl:property><ifl:key>Expression</ifl:key><ifl:value>{batch_cfg.split_expression or "/root/item"}</ifl:value></ifl:property>
        <ifl:property><ifl:key>ChunkSize</ifl:key><ifl:value>{batch_cfg.chunk_size}</ifl:value></ifl:property>
      </extensionElements>
    </serviceTask>'''

        mapping_section = ""
        if mapping and strategy in ("brownfield", "bluefield"):
            mapping_section = f'''
    <!-- MESSAGE MAPPING: {mapping} -->
    <serviceTask id="Mapping_1" name="{mapping}" ifl:type="MessageMapping">
      <extensionElements>
        <ifl:property><ifl:key>mappingUri</ifl:key>
          <ifl:value>mapping:///{mapping.replace(" ", "_")}</ifl:value>
        </ifl:property>
      </extensionElements>
    </serviceTask>'''

        content = f'''<?xml version="1.0" encoding="UTF-8"?>
<!--
  SCENARIO iFlow: {iface.name}
  Display Name : {iflow_display_name}
  Package      : {pkg.package_name}
  Strategy     : {strategy_comment}
  Complexity   : {assessment.complexity} (score: {assessment.score})
  Effort       : {assessment.effort_days} day(s)
  Sender       : {iface.sender_system} ({iface.sender_adapter})
  Receiver     : {iface.receiver_system} ({iface.receiver_adapter})
  Batch        : {"Yes — " + batch_cfg.split_by if batch_cfg.enabled else "No"}
-->
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             xmlns:ifl="http:///com.sap.ifl.model/Ifl.xsd"
             id="{safe}"
             name="{iflow_display_name}">
  <process id="{safe}_process" ifl:type="IntegrationFlow">

    <startEvent id="Start_SC" name="From Interface Separation">
      <extensionElements>
        <ifl:property><ifl:key>Transport</ifl:key><ifl:value>ProcessDirect</ifl:value></ifl:property>
        <ifl:property><ifl:key>Address</ifl:key>
          <ifl:value>/scenario/{safe}</ifl:value>
        </ifl:property>
      </extensionElements>
    </startEvent>
{batch_section}
{mapping_section}

    <!-- PROCESSDIRECT: Forward to Outbound Delivery -->
    <serviceTask id="CallOutbound_1" name="→ Outbound Delivery" ifl:type="RequestReply">
      <extensionElements>
        <ifl:property><ifl:key>Transport</ifl:key><ifl:value>ProcessDirect</ifl:value></ifl:property>
        <ifl:property><ifl:key>Address</ifl:key>
          <ifl:value>/outbound/{pkg.package_id}</ifl:value>
        </ifl:property>
      </extensionElements>
    </serviceTask>

    <endEvent id="End_SC"/>
    <sequenceFlow sourceRef="Start_SC" targetRef="{"Splitter_1" if batch_cfg.enabled else ("Mapping_1" if mapping and strategy in ("brownfield","bluefield") else "CallOutbound_1")}"/>
    {"<sequenceFlow sourceRef='Splitter_1' targetRef='" + ("Mapping_1" if mapping else "CallOutbound_1") + "'/>" if batch_cfg.enabled else ""}
    {"<sequenceFlow sourceRef='Mapping_1' targetRef='CallOutbound_1'/>" if mapping and strategy in ("brownfield","bluefield") else ""}
    <sequenceFlow sourceRef="CallOutbound_1" targetRef="End_SC"/>
  </process>
</definitions>'''
        path = pkg_dir / f"{safe}.iflw"
        path.write_text(content, "utf-8")
        return path

    def _write_partner_directory(
        self, pkg: PipelinePackage, configs: dict, pkg_dir: Path
    ) -> Path:
        """Generate the Partner Directory JSON — loaded into CPI at runtime."""
        pd = {"packageId": pkg.package_id, "packageName": pkg.package_name, "entries": []}

        for a in pkg.interfaces:
            iface = a.interface
            cfg   = configs.get(iface.name)
            safe  = _clean(iface.name)
            sender = iface.sender_system or "SRC"
            msg_type = iface.message_interface or iface.name

            entries = [
                # Routing keys
                {"pid": sender, "key": f"ReceiverRouting_{sender}_{msg_type}",
                 "value": iface.receiver_system or "TGT", "type": "String"},
                {"pid": sender, "key": f"InterfaceName_{sender}_{msg_type}",
                 "value": iface.name, "type": "String"},
                # Endpoint
                {"pid": iface.receiver_system or "TGT",
                 "key": "ReceiverEndpoint",
                 "value": (cfg.receiver_connectivity.address if cfg else "") or "[FILL_ENDPOINT]",
                 "type": "String"},
                # Credentials
                {"pid": iface.receiver_system or "TGT",
                 "key": "ReceiverCredential",
                 "value": (cfg.receiver_auth.credential_name if cfg else "") or "[FILL_CREDENTIAL]",
                 "type": "String"},
                # Retry config
                {"pid": sender, "key": f"MaxRetries_{safe}",
                 "value": str(cfg.reliability.retry_max_attempts if cfg else 3),
                 "type": "String"},
                {"pid": sender, "key": f"RetryDelay_{safe}",
                 "value": str(cfg.reliability.retry_delay_sec if cfg else 60),
                 "type": "String"},
            ]
            pd["entries"].extend(entries)

        path = pkg_dir / f"PartnerDirectory_{pkg.package_id}.json"
        path.write_text(json.dumps(pd, indent=2), "utf-8")
        pkg.partner_directory = pd
        return path

    def _write_jms_queues(self, pkg: PipelinePackage, pkg_dir: Path) -> Path:
        """Generate JMS queue configuration JSON."""
        queues = {
            "packageId": pkg.package_id,
            "totalQueues": 8,
            "coreQueues": pkg.jms_queues[:4],
            "deadLetterQueues": pkg.jms_queues[4:],
            "note": (
                "Load these 8 queues into CPI: Monitor → Manage Message Queues. "
                "4 core + 4 dead-letter = 8 total per package (SAP recommended maximum per package)."
            ),
        }
        path = pkg_dir / f"JMSQueues_{pkg.package_id}.json"
        path.write_text(json.dumps(queues, indent=2), "utf-8")
        return path

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _resolve_strategy(assessment, pkg_strategy: str) -> str:
        """For bluefield: use complexity to decide per interface."""
        if pkg_strategy != "bluefield":
            return pkg_strategy
        if assessment.complexity == "HIGH":
            return "greenfield"   # too complex to lift-and-shift safely
        elif assessment.complexity == "LOW":
            return "brownfield"   # simple enough to port directly
        else:
            return "brownfield"   # medium: port but review

    @staticmethod
    def _detect_batch(assessment, cfg) -> BatchConfig:
        """Auto-detect if interface needs batch processing."""
        iface = assessment.interface
        is_batch = (
            iface.sender_adapter in ("File", "FTP", "SFTP") or
            (cfg and bool(cfg.runtime.scheduler_cron)) or
            iface.has_multi_mapping
        )
        if not is_batch:
            return BatchConfig(enabled=False)

        split_expr = "/root/item"
        if iface.sender_adapter in ("File", "FTP", "SFTP"):
            split_expr = "/records/record"

        return BatchConfig(
            enabled=True,
            split_by="element",
            split_expression=split_expr,
            chunk_size=100,
            aggregation_condition="Complete",
            timeout_minutes=30,
        )


# ---------------------------------------------------------------------------
# Simple mode scaffolder (≤10 interfaces — current behavior)
# ---------------------------------------------------------------------------

def should_use_pipeline(assessments: list, user_override: str = "auto") -> bool:
    """
    Decide whether to use Pipeline Concept or simple 1-iFlow-per-interface.
    auto: pipeline if >10 interfaces
    pipeline: always pipeline
    simple: always simple
    """
    if user_override == "pipeline":
        return True
    if user_override == "simple":
        return False
    return len(assessments) >= 10


# ---------------------------------------------------------------------------
# EOIO / DataStore pattern (Feature 24)
# ---------------------------------------------------------------------------

EOIO_TRIGGERS = {
    "XI", "xi", "JMS", "jms", "EOIO", "eoio", "sequential", "ordered"
}

def needs_eoio_pattern(record) -> bool:
    """Detect if an interface needs EOIO DataStore staging pattern."""
    return (
        record.sender_adapter in ("XI", "JMS", "ProcessDirect") or
        record.receiver_adapter in ("XI", "JMS") or
        "eoio" in record.name.lower() or
        "sequential" in record.name.lower() or
        "ordered" in record.name.lower()
    )


DATASTORE_IFLOW_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!--
  EOIO Staging Pattern — DataStore Wrapper
  Interface: {interface_name}
  Generated by CPI Migration Scaffolder

  Pattern:
    Step 1 (this iFlow): Receive message → write to DataStore with timestamp + seq ID
    Step 2: Sequential poller reads DataStore entries in order
    Step 3: Outbound iFlow sends to receiver

  SAP Note: XI Adapter does NOT support EOIO natively in CPI.
  This DataStore pattern is the recommended cloud-native substitute.
-->
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             xmlns:ifl="http:///com.sap.ifl.model/Ifl.xsd"
             id="{safe_name}_EOIO_Inbound"
             name="{interface_name} — EOIO Inbound">
  <process id="{safe_name}_EOIO_proc" ifl:type="IntegrationFlow">

    <startEvent id="Start_1" name="Receive Message">
      <extensionElements>
        <ifl:property><ifl:key>Transport</ifl:key>
          <ifl:value>{sender_adapter}</ifl:value></ifl:property>
      </extensionElements>
    </startEvent>

    <!-- STEP 1: Inject sequence ID and timestamp -->
    <serviceTask id="SetSequence" name="Set Sequence ID" ifl:type="ContentModifier">
      <extensionElements>
        <ifl:property><ifl:key>CamelHeader.SequenceId</ifl:key>
          <ifl:value>${{property.CamelTimerCounter}}</ifl:value></ifl:property>
        <ifl:property><ifl:key>CamelHeader.Timestamp</ifl:key>
          <ifl:value>${{date:now:yyyyMMddHHmmssSSS}}</ifl:value></ifl:property>
      </extensionElements>
    </serviceTask>

    <!-- STEP 2: Write to DataStore for ordered processing -->
    <serviceTask id="WriteDataStore" name="Write to DataStore" ifl:type="DataStoreWrite">
      <extensionElements>
        <ifl:property><ifl:key>dataStoreName</ifl:key>
          <ifl:value>{safe_name}_EOIO_Queue</ifl:value></ifl:property>
        <ifl:property><ifl:key>entryId</ifl:key>
          <ifl:value>${{header.Timestamp}}_${{header.SequenceId}}</ifl:value></ifl:property>
        <ifl:property><ifl:key>visibility</ifl:key>
          <ifl:value>Integration Flow</ifl:value></ifl:property>
        <ifl:property><ifl:key>retention_threshold_deleted</ifl:key>
          <ifl:value>90</ifl:value></ifl:property>
      </extensionElements>
    </serviceTask>

    <endEvent id="End_1" name="Stored — Poller will process in order"/>

    <sequenceFlow id="f1" sourceRef="Start_1"      targetRef="SetSequence"/>
    <sequenceFlow id="f2" sourceRef="SetSequence"  targetRef="WriteDataStore"/>
    <sequenceFlow id="f3" sourceRef="WriteDataStore" targetRef="End_1"/>

  </process>
  <ifl:iFlowMetadata>
    <ifl:description>EOIO Inbound staging for {interface_name}.
DataStore: {safe_name}_EOIO_Queue
Next step: deploy {safe_name}_EOIO_Poller iFlow to process entries sequentially.</ifl:description>
    <ifl:version>1.0.0</ifl:version>
  </ifl:iFlowMetadata>
</definitions>"""


DATASTORE_POLLER_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!--
  EOIO Staging Pattern — Sequential Poller
  Interface: {interface_name}
  Reads DataStore entries in timestamp order and forwards to receiver.
-->
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             xmlns:ifl="http:///com.sap.ifl.model/Ifl.xsd"
             id="{safe_name}_EOIO_Poller"
             name="{interface_name} — EOIO Poller">
  <process id="{safe_name}_EOIO_poll_proc" ifl:type="IntegrationFlow">

    <!-- Scheduler: poll every 10 seconds -->
    <startEvent id="Start_Timer" name="Poll DataStore">
      <extensionElements>
        <ifl:property><ifl:key>Transport</ifl:key>
          <ifl:value>Timer</ifl:value></ifl:property>
        <ifl:property><ifl:key>scheduler_interval</ifl:key>
          <ifl:value>10</ifl:value></ifl:property>
        <ifl:property><ifl:key>scheduler_interval_unit</ifl:key>
          <ifl:value>Seconds</ifl:value></ifl:property>
      </extensionElements>
    </startEvent>

    <!-- Read next entry from DataStore (ordered by entry ID = timestamp) -->
    <serviceTask id="ReadDataStore" name="Read from DataStore" ifl:type="DataStoreGet">
      <extensionElements>
        <ifl:property><ifl:key>dataStoreName</ifl:key>
          <ifl:value>{safe_name}_EOIO_Queue</ifl:value></ifl:property>
        <ifl:property><ifl:key>deleteAfterRead</ifl:key>
          <ifl:value>true</ifl:value></ifl:property>
        <ifl:property><ifl:key>throwExceptionOnMissingEntry</ifl:key>
          <ifl:value>false</ifl:value></ifl:property>
      </extensionElements>
    </serviceTask>

    <!-- Forward to receiver via ProcessDirect -->
    <serviceTask id="ForwardMessage" name="Forward to Receiver" ifl:type="ProcessDirect">
      <extensionElements>
        <ifl:property><ifl:key>Address</ifl:key>
          <ifl:value>/{safe_name}_Outbound</ifl:value></ifl:property>
      </extensionElements>
    </serviceTask>

    <endEvent id="End_Poll" name="Message forwarded"/>

    <sequenceFlow id="f1" sourceRef="Start_Timer"   targetRef="ReadDataStore"/>
    <sequenceFlow id="f2" sourceRef="ReadDataStore" targetRef="ForwardMessage"/>
    <sequenceFlow id="f3" sourceRef="ForwardMessage" targetRef="End_Poll"/>

  </process>
</definitions>"""


def generate_eoio_pattern(
    assessment,
    output_dir: str,
) -> list:
    """
    Generate the 3-file EOIO DataStore pattern for an interface.
    Returns list of output paths.
    """
    import re as _re
    iface     = assessment.interface
    safe_name = _re.sub(r"[^\w]", "_", iface.name)[:60]
    out       = Path(output_dir) / "iflows"
    out.mkdir(parents=True, exist_ok=True)
    paths     = []

    # Inbound DataStore writer
    inbound = DATASTORE_IFLOW_TEMPLATE.format(
        interface_name=iface.name,
        safe_name=safe_name,
        sender_adapter=iface.sender_adapter,
    )
    p1 = out / f"{safe_name}_EOIO_Inbound.iflw"
    p1.write_text(inbound, "utf-8")
    paths.append(p1)

    # Sequential poller
    poller = DATASTORE_POLLER_TEMPLATE.format(
        interface_name=iface.name,
        safe_name=safe_name,
    )
    p2 = out / f"{safe_name}_EOIO_Poller.iflw"
    p2.write_text(poller, "utf-8")
    paths.append(p2)

    return paths
