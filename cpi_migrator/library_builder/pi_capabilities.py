"""library_builder/pi_capabilities.py

PI/PO capability extractor + PI→CPI TRANSLATOR.

PURPOSE (per the project's intent): we never PRODUCE PI artifacts — we READ an
existing PI interface/package, understand what it does, and translate that into
"what must be built in Integration Suite (CPI)". So this module has two halves:

  1. EXTRACT — read PI mapping artifacts into the normalized capability shape.
  2. TRANSLATE — map each PI capability to its CPI equivalent (which CPI
     capability/approach realizes the same intent), using the documented SAP
     modernization rules + the confirmed tf7-engine equivalence.

GROUNDED IN REAL SPECIMENS (fetched from public GitHub — small but real):
  * PI Java mapping (AbstractTransformation):
      transform(TransformationInput, TransformationOutput); reads
      getInputPayload().getInputStream(), writes getOutputPayload()
      .getOutputStream(); header via getInputHeader().getMessageId().
      → CPI: a Groovy/Java script with processData(Message) — same
        read-payload → transform → write-payload shape, different API.
  * PI UDF function library (ESR):
      @LibraryMethod / @Argument / @Init / @Cleanup, ExecutionType.*,
      imports com.sap.aii.mappingtool.tf7.rt (THE SAME tf7 engine CPI uses),
      DynamicConfiguration (ASMA) for adapter attributes.
      → CPI: a Groovy UDF in a graphical message mapping (tf7 runtime carries
        over directly — UDF knowledge transfers).

HONEST VALIDATION LIMIT: built on a SMALL real sample (a Java mapping + a UDF
library) + the documented PI mapping contract + SAP's modernization rules. It is
genuinely grounded (not N=0) but NOT broadly corpus-validated like groovy(498)/
iflw(164). The .tpz/ESR-XML package READER (parsing a full PI export) remains a
handoff item needing a real .tpz. Treat outputs as reasoned; widen as more real
PI specimens arrive. No SAP runtime, no tenant — sandbox-testable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as _field


# PI mapping-type signatures (how to recognize each kind from real code)
_PI_JAVA_MAPPING = ("AbstractTransformation", "StreamTransformation")
_PI_UDF_MARKERS = ("@LibraryMethod", "com.sap.ide.esr.tools.mapping",
                   "ExecutionType.", "@Argument")
_PI_API = "com.sap.aii.mapping"
_TF7 = "com.sap.aii.mappingtool.tf7"

# PI input/output operations (the universal ops, PI binding)
_PI_BINDING_TO_OP = {
    "getInputPayload": "READ_BODY",
    "getInputHeader": "READ_HEADER",
    "getOutputPayload": "WRITE_BODY",
    "getOutputHeader": "WRITE_HEADER",
    "getInputAttachments": "READ_ATTACHMENT",
    "getOutputAttachments": "WRITE_ATTACHMENT",
    "getInputParameters": "READ_PARAMETER",
    "DynamicConfiguration": "DYNAMIC_CONFIG",
    "lookup": "LOOKUP",
    "RfcAccessor": "RFC_LOOKUP",
    "DbAccessor": "DB_LOOKUP",
    "SystemAccessor": "LOOKUP",
}

# PI → CPI translation rules (modernization). What a PI construct BECOMES in
# Integration Suite. Grounded in SAP's documented modernization guidance +
# the confirmed tf7-engine equivalence.
_PI_TO_CPI = {
    "java_mapping": ("CPI Groovy/Java script step (processData(Message))",
                     "same read→transform→write shape; rewrite API calls"),
    "udf_library": ("CPI Groovy UDF in a graphical message mapping",
                    "tf7 runtime is identical — UDF logic transfers, "
                    "re-author in Groovy"),
    "graphical_mapping": ("CPI graphical Message Mapping (.mmap)",
                          "SAP migration tooling outputs the CPI mmap format "
                          "we already master"),
    "DYNAMIC_CONFIG": ("CPI message header/property via "
                       "setHeader/setProperty + SAP_ApplicationID etc.",
                       "ASMA attributes become CPI exchange headers"),
    "RFC_LOOKUP": ("CPI RFC receiver (or OData/SOAP) + Request-Reply",
                   "PI RFC lookup → a CPI external call step"),
    "DB_LOOKUP": ("CPI JDBC receiver + Request-Reply",
                  "PI DB lookup → a CPI external call step"),
    "LOOKUP": ("CPI external call (Request-Reply to a receiver adapter)",
               "PI channel lookup → CPI receiver + request-reply"),
}


@dataclass
class PiCapability:
    name: str
    pi_type: str = ""               # java_mapping | udf_library | graphical | unknown
    operations: list = _field(default_factory=list)
    udf_methods: list = _field(default_factory=list)
    uses_tf7: bool = False
    imports: list = _field(default_factory=list)
    # facets
    purpose: str = ""
    needs: list = _field(default_factory=list)
    what_varies: list = _field(default_factory=list)
    shape: str = ""
    when_to_use: str = ""
    op_keywords: list = _field(default_factory=list)
    # the translation (PI -> CPI)
    cpi_target: str = ""
    cpi_notes: str = ""
    weight: int = 0

    def signature(self) -> str:
        return f"pi:{self.pi_type}({len(self.operations)}ops)"


def _classify(text: str) -> str:
    if any(m in text for m in _PI_UDF_MARKERS):
        return "udf_library"
    if any(m in text for m in _PI_JAVA_MAPPING):
        return "java_mapping"
    if "<mappingDescription" in text or "MappingComponent" in text:
        return "graphical_mapping"
    return "unknown"


def extract_capability(name: str, text: str) -> PiCapability:
    cap = PiCapability(name=name)
    cap.pi_type = _classify(text)
    cap.uses_tf7 = _TF7 in text
    cap.imports = sorted(set(re.findall(r"import\s+([\w.]+)\.\*?;", text)))[:30]

    # universal operations via the PI mapping API — detect from REAL USAGE, not
    # mere imports (rigor-audit: `import ...mapping.lookup` was falsely tagging
    # LOOKUP even when no lookup is performed). Strip import lines first.
    body = "\n".join(ln for ln in text.splitlines()
                     if not ln.strip().startswith("import"))
    ops = []
    for marker, op in _PI_BINDING_TO_OP.items():
        # require the marker as a call/usage in the body, not an import path
        if re.search(rf"\b{re.escape(marker)}\b", body):
            ops.append(op)
    cap.operations = sorted(set(ops))

    # UDF methods (the reusable functions in a function library)
    if cap.pi_type == "udf_library":
        cap.udf_methods = re.findall(
            r'@LibraryMethod\(title="([^"]+)"', text) or \
            re.findall(r"public\s+\w+\s+(\w+)\s*\(", text)

    # op keywords from real method calls (discriminating, corpus-derived)
    _calls = re.findall(r"\.(\w+)\s*\(", text)
    _noise = {"get", "put", "create", "getMessage", "concat", "add"}
    cap.op_keywords = sorted({m for m in _calls
                              if len(m) > 3 and m not in _noise})[:25]

    # facets ------------------------------------------------------------
    reads = [o for o in cap.operations if o.startswith("READ")]
    writes = [o for o in cap.operations if o.startswith("WRITE")]
    special = [o for o in cap.operations
               if o in ("DYNAMIC_CONFIG", "LOOKUP", "RFC_LOOKUP", "DB_LOOKUP")]
    if cap.pi_type == "udf_library":
        cap.purpose = ("PI UDF library: " +
                       ", ".join(cap.udf_methods[:5]) if cap.udf_methods
                       else "PI UDF library")
    elif cap.pi_type == "java_mapping":
        cap.purpose = "PI Java mapping (payload transform)"
    elif cap.pi_type == "graphical_mapping":
        cap.purpose = "PI graphical message mapping"
    else:
        cap.purpose = "PI mapping artifact"
    if special:
        cap.purpose += " + " + ", ".join(special)
    cap.needs = reads + [f"udf:{m}" for m in cap.udf_methods[:5]]
    cap.what_varies = cap.udf_methods + [i for i in cap.imports
                                         if not i.startswith("java.")][:10]
    cap.shape = (f"{cap.pi_type}; ops={len(cap.operations)}"
                 + (f"; udfs={len(cap.udf_methods)}" if cap.udf_methods else "")
                 + ("; tf7" if cap.uses_tf7 else ""))
    cap.when_to_use = "migrate this PI mapping to CPI (see cpi_target)"

    # TRANSLATE: PI -> CPI ---------------------------------------------
    target, notes = _PI_TO_CPI.get(cap.pi_type, ("CPI script/mapping step", ""))
    extra = [(_PI_TO_CPI[o][0]) for o in special if o in _PI_TO_CPI]
    cap.cpi_target = target + (("; also: " + "; ".join(extra)) if extra else "")
    cap.cpi_notes = notes
    cap.weight = (len(cap.operations) + len(cap.udf_methods)
                  + len(special) * 3)
    return cap


def translate_to_cpi(cap: PiCapability) -> dict:
    """The migration spec for one PI capability: what to build in CPI."""
    return {
        "pi_artifact": cap.name,
        "pi_type": cap.pi_type,
        "build_in_cpi": cap.cpi_target,
        "notes": cap.cpi_notes,
        "operations": cap.operations,
        "udf_methods": cap.udf_methods,
        "confidence": "reasoned",   # honest: tenant confirms the actual build
    }


def build_catalog(corpus: dict) -> dict:
    caps = [extract_capability(n, t) for n, t in corpus.items()]
    index = {}
    for c in caps:
        index.setdefault(c.signature(), []).append(c.name)
    return {
        "capabilities": caps,
        "count": len(caps),
        "migration_specs": [translate_to_cpi(c) for c in caps],
        "index": index,
    }
