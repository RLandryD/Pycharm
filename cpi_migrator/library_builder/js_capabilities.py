"""library_builder/js_capabilities.py

JavaScript capability extractor — the CPI JS script model, which mirrors groovy
(same envelope, same SAP Message API, same universal-op/binding split), in JS
syntax executed by the CPI JS engine (Rhino/Nashorn, with java interop).

  A CPI JS script = ENVELOPE  function processData(message){ ... return message }
                  + UNIVERSAL OPERATIONS (READ_/WRITE_/EMIT_ via the SAP Message
  API: message.getBody/setBody/getProperty/setProperty/getHeaders/setHeader, and
  messageLog.* ) + PORTABLE OPS (JSON.parse/stringify, String/Array methods,
  RegExp, etc.) — the same split proven for groovy.

JS-specific notes (from the real specimen + the CPI JS contract):
  - body read often uses java interop: message.getBody(new java.lang.String()
    .getClass()) — captured as body_read_as.
  - multi-function file = LIBRARY of capabilities (same as groovy).

HONEST VALIDATION LIMIT: the corpus contains only ONE real .js specimen, so —
unlike groovy (498) or xslt (131) — this extractor's classification is NOT
corpus-validated at scale. It is built on the groovy model (which IS validated)
+ that one specimen + the documented CPI JS contract. Treat its outputs as
reasoned until more JS specimens confirm. The structure is sound; the breadth of
real-world JS idioms it has seen is narrow.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as _field


# SAP Message API calls (the binding layer) -> universal operation
_BINDING_TO_OP = {
    "message.getBody": ("READ_BODY", "read"),
    "message.getProperty": ("READ_PROPERTY", "read"),
    "message.getProperties": ("READ_PROPERTY_ALL", "read"),
    "message.getHeader": ("READ_HEADER", "read"),
    "message.getHeaders": ("READ_HEADER_ALL", "read"),
    "message.setBody": ("WRITE_BODY", "write"),
    "message.setProperty": ("WRITE_PROPERTY", "write"),
    "message.setHeader": ("WRITE_HEADER", "write"),
    "messageLog.addAttachmentAsString": ("EMIT_ATTACHMENT", "emit"),
    "messageLog.setStringProperty": ("EMIT_LOG_PROPERTY", "emit"),
    "messageLog.addCustomHeaderProperty": ("EMIT_LOG_HEADER", "emit"),
}

# portable JS operations worth recognizing (the vendor-neutral transform core)
_PORTABLE_SIGNALS = {
    "JSON.parse": "PARSE_JSON", "JSON.stringify": "BUILD_JSON",
    "parseFloat": "PARSE_NUMBER", "parseInt": "PARSE_NUMBER",
    "RegExp": "REGEX", ".replace": "STRING_REPLACE",
    ".split": "STRING_SPLIT", ".substring": "SUBSTRING",
    ".substr": "SUBSTRING", ".map": "ITERATE", ".forEach": "ITERATE",
    ".filter": "FILTER", ".reduce": "REDUCE", ".sort": "SORT",
    ".trim": "TRIM", "Date": "DATE", "encodeURI": "URL_ENCODE",
}

# noise method names (not capability-bearing)
_NOISE = {"toString", "getClass", "hasOwnProperty", "valueOf", "length",
          "push", "call", "apply", "bind"}


@dataclass
class JsCapability:
    name: str
    envelope: str = ""
    operations: list = _field(default_factory=list)
    bindings: list = _field(default_factory=list)
    portable_ops: list = _field(default_factory=list)
    op_keywords: list = _field(default_factory=list)
    purpose: str = ""
    needs: list = _field(default_factory=list)
    what_varies: list = _field(default_factory=list)
    shape: str = ""
    when_to_use: str = ""
    intent_confidence: str = "structural"
    body_read_as: list = _field(default_factory=list)
    function_count: int = 0
    is_library: bool = False
    weight: int = 0

    def signature(self) -> str:
        env = self.envelope or "none"
        return (f"js:{env}({len([o for o in self.operations if o.startswith('READ')])}r,"
                f"{len([o for o in self.operations if o.startswith('WRITE')])}w)"
                + ("+lib" if self.is_library else ""))


def discover_bindings(corpus: dict):
    """Empirically discover the SAP Message API vocabulary used across a JS
    corpus — learned, not assumed (mirrors groovy.discover_bindings)."""
    from collections import Counter
    api = Counter()
    for t in corpus.values():
        for m in re.findall(r"(?<![\w.])message\.(\w+)\s*\(", t):
            api["message." + m] += 1
        for m in re.findall(r"messageLog\.(\w+)\s*\(", t):
            api["messageLog." + m] += 1
    return api


def extract_capability(name: str, text: str) -> JsCapability:
    cap = JsCapability(name=name)
    # envelope: CPI JS entry point. Matches both declaration form
    #   function processData(message){...}
    # and expression form (rigor-audit finding — a real JS idiom)
    #   var processData = function(message){...}  /  processData = (message) =>
    if re.search(r"function\s+processData\s*\(\s*message\s*\)", text) or \
       re.search(r"\bprocessData\s*=\s*function\s*\(\s*message\s*\)", text) or \
       re.search(r"\bprocessData\s*=\s*\(?\s*message\s*\)?\s*=>", text):
        cap.envelope = "processData"

    # universal operations via the SAP Message API (lookbehind avoids
    # exception.message etc. — the groovy rigor-audit lesson, applied here too)
    ops, binds = [], []
    for m in re.finditer(
            r"(?<![\w.])(message|messageLog)\.(\w+)\s*\(", text):
        call = f"{m.group(1)}.{m.group(2)}"
        if call in _BINDING_TO_OP:
            op, _role = _BINDING_TO_OP[call]
            ops.append(op)
            binds.append(call)
    cap.operations = ops
    cap.bindings = sorted(set(binds))

    # portable transform ops
    portable = []
    for sig, op in _PORTABLE_SIGNALS.items():
        if sig in text:
            portable.append(op)
    cap.portable_ops = sorted(set(portable))

    # body-read type (JS java-interop or direct) — part of the I/O contract
    body_types = []
    if "java.lang.String" in text and "getBody" in text:
        body_types.append("String")
    if re.search(r"getBody\s*\(\s*\)", text):
        body_types.append("default")
    for t in ("InputStream", "Reader", "byte"):
        if f"getBody" in text and t in text:
            body_types.append(t)
    cap.body_read_as = sorted(set(body_types))

    # function count -> library
    cap.function_count = len(re.findall(r"\bfunction\s+\w+\s*\(", text))
    cap.is_library = cap.function_count >= 5

    # op keywords from real calls (discriminating vocab, corpus-derived — same
    # anti-bias approach as groovy)
    _calls = re.findall(r"\.(\w+)\s*\(", text)
    cap.op_keywords = sorted({m for m in _calls
                              if len(m) > 3 and m not in _NOISE})[:25]

    # five facets
    reads = [o for o in ops if o.startswith("READ")]
    writes = [o for o in ops if o.startswith("WRITE")]
    emits = [o for o in ops if o.startswith("EMIT")]
    verbs = []
    if "PARSE_JSON" in cap.portable_ops:
        verbs.append("parse JSON")
    if "BUILD_JSON" in cap.portable_ops:
        verbs.append("build JSON")
    if any(o in cap.portable_ops for o in ("ITERATE", "FILTER", "REDUCE")):
        verbs.append("restructure")
    if any(o in cap.portable_ops for o in ("STRING_REPLACE", "SUBSTRING",
                                           "REGEX")):
        verbs.append("string-manipulate")
    cap.purpose = (", ".join(verbs) if verbs else "transform payload") \
        + (" + write-back" if writes else "")
    cap.needs = [b for b in cap.bindings if b.startswith("message.get")]
    # what-varies: concrete identifiers the script references (adaptation points)
    cap.what_varies = sorted(set(
        re.findall(r"\.(\w+)\b", text)) - _NOISE)[:30]
    cap.shape = (f"envelope={cap.envelope or 'none'}(message); "
                 f"reads={len(reads)} writes={len(writes)} emits={len(emits)}"
                 + (f"; body_as={'/'.join(cap.body_read_as)}"
                    if cap.body_read_as else "")
                 + (f"; LIBRARY({cap.function_count} fns)"
                    if cap.is_library else ""))
    cap.when_to_use = ("transform JSON payloads in JS"
                       if "JSON" in "".join(cap.portable_ops)
                       else "JS message transform")
    cap.intent_confidence = "operational" if cap.portable_ops else "structural"
    cap.weight = (len(ops) + len(cap.portable_ops)
                  + len(cap.bindings) * 2
                  + (3 if cap.is_library else 0))
    return cap


def build_catalog(corpus: dict) -> dict:
    caps = [extract_capability(n, t) for n, t in corpus.items()]
    index = {}
    for c in caps:
        index.setdefault(c.signature(), []).append(c.name)
    return {
        "capabilities": caps,
        "binding_vocabulary": dict(discover_bindings(corpus)),
        "index": index,
        "count": len(caps),
    }
