"""library_builder/groovy_capabilities.py

Groovy capability extractor — the code-capability equivalent of
mmap_capabilities.py, built on the model worked out with the user:

  A groovy script = ENVELOPE (the `def Message processData(Message msg)`
  wrapper + SAP import — a known, swappable template)
                  + a graph of UNIVERSAL OPERATIONS (read / write / emit /
  parse / transform / lookup — the portable core logic)
                  + a BINDING TABLE (universal op -> the SAP runtime API call
  that realises it: message.getProperty, messageLog.addAttachmentAsString, ...)

The SAP runtime-API vocabulary is DISCOVERED EMPIRICALLY by scanning the corpus
(not hard-coded from memory) — `discover_bindings(corpus)` returns the observed
vocabulary. The per-capability record captures the five facets the user
identified as what's needed to ADAPT (not copy) a capability to a new problem:
  purpose | needs (inputs) | what-varies | shape (envelope+contract) | when-to-use

Validated against 498 real groovy specimens (see tests). The SAP binding layer
is the small, finite part the user validates against the tenant; the core
operations are portable.

NOTE: this is structural + operation-level extraction (high-confidence layers).
The deeper "intent" of arbitrary code is summarised, not guaranteed — flagged
honestly in the record (`intent_confidence`).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field as _field


# --- the SAP runtime API -> universal operation map (seed; extend empirically)
# Each maps a SAP binding call to a universal operation + role.
_BINDING_TO_OP = {
    "message.getBody": ("READ_BODY", "reader"),
    "message.getProperty": ("READ_PROPERTY", "reader"),
    "message.getProperties": ("READ_PROPERTY_ALL", "reader"),
    "message.getHeader": ("READ_HEADER", "reader"),
    "message.getHeaders": ("READ_HEADER_ALL", "reader"),
    "message.getAttachments": ("READ_ATTACHMENT", "reader"),
    "message.setBody": ("WRITE_BODY", "writer"),
    "message.setProperty": ("WRITE_PROPERTY", "writer"),
    "message.setHeader": ("WRITE_HEADER", "writer"),
    "messageLog.addAttachmentAsString": ("EMIT_ATTACHMENT", "sink"),
    "messageLog.getMessageLog": ("EMIT_LOG_OPEN", "sink"),
    "messageLog.addCustomHeaderProperty": ("EMIT_CUSTOM_HEADER", "sink"),
    "messageLog.setStringProperty": ("EMIT_LOG_PROPERTY", "sink"),
}

# portable operation signals (non-SAP libs -> what the core does)
_LIB_TO_OP = {
    "JsonSlurper": "PARSE_JSON", "JsonOutput": "BUILD_JSON",
    "XmlSlurper": "PARSE_XML", "XmlParser": "PARSE_XML",
    "MarkupBuilder": "BUILD_XML", "SAXBuilder": "PARSE_XML",
    "XMLOutputter": "BUILD_XML", "SimpleDateFormat": "TRANSFORM_DATE",
    "Pattern": "REGEX", "Matcher": "REGEX",
}


@dataclass
class GroovyCapability:
    name: str                       # file / function name
    envelope: str                   # the entry-point template id
    operations: list = _field(default_factory=list)   # universal ops, ordered
    bindings: list = _field(default_factory=list)      # SAP API calls used
    portable_ops: list = _field(default_factory=list)  # parse/transform/etc.
    imports: list = _field(default_factory=list)
    # ---- the five facets the user wants (to ADAPT, not copy) ----
    purpose: str = ""               # what it does (summary)
    needs: list = _field(default_factory=list)         # inputs it reads
    what_varies: list = _field(default_factory=list)   # the adaptable params
    shape: str = ""                 # envelope + io-contract descriptor
    when_to_use: str = ""           # selection hint
    intent_confidence: str = "structural"   # structural|operational|inferred
    weight: int = 0
    body_read_as: list = _field(default_factory=list)  # String/Reader/InputStream
    function_count: int = 0         # # of def fns (>=5 => library of capabilities)
    is_library: bool = False        # multi-capability file (don't treat as one)
    op_keywords: list = _field(default_factory=list)   # real method calls (FETCH)

    def signature(self) -> str:
        rd = sum(1 for o in self.operations if o.startswith("READ"))
        wr = sum(1 for o in self.operations if o.startswith("WRITE"))
        core = "+".join(self.portable_ops) or "passthrough"
        return f"groovy:{core}(r{rd}w{wr})"


def discover_bindings(corpus: dict) -> Counter:
    """Empirically discover the SAP runtime-API vocabulary across a corpus
    (dict name->text). This is how the binding table is LEARNED, not assumed.
    The (?<![\\w.]) lookbehind avoids false positives like
    `exception.message.replaceAll` (the .message PROPERTY of a Java exception,
    not the SAP Message object) — a bug caught in the rigor audit."""
    api = Counter()
    for t in corpus.values():
        for m in re.findall(r"(?<![\w.])message\.(\w+)\s*\(", t):
            api["message." + m] += 1
        for m in re.findall(r"messageLog(?:Factory)?\.(\w+)\s*\(", t):
            api["messageLog." + m] += 1
    return api


def _entry_point(text: str) -> str:
    m = re.search(r"def\s+(?:\w+\s+)?(\w+)\s*\(\s*Message\s+\w+\s*\)", text)
    return m.group(1) if m else "(none)"


def _extract_property_names(text: str) -> list:
    """The named context values it reads/writes — part of 'needs'/'what-varies'."""
    names = set()
    for m in re.finditer(r'(?:getProperty|getHeader|setProperty|setHeader)'
                         r'\(\s*["\']([^"\']+)["\']', text):
        names.add(m.group(1))
    return sorted(names)


def extract_capability(name: str, text: str) -> GroovyCapability:
    cap = GroovyCapability(name=name, envelope=_entry_point(text))
    cap.imports = sorted(set(re.findall(r"import\s+([\w.]+)", text)))

    # bindings -> universal operations (ordered by appearance).
    # (?<![\w.]) avoids matching exception.message etc. (audit fix)
    ops, binds = [], []
    for m in re.finditer(r"(?<![\w.])(message|messageLog(?:Factory)?)\.(\w+)\s*\(",
                         text):
        prefix = "message" if m.group(1) == "message" else "messageLog"
        call = f"{prefix}.{m.group(2)}"
        if call in _BINDING_TO_OP:
            op, _role = _BINDING_TO_OP[call]
            ops.append(op)
            binds.append(call)
    cap.operations = ops
    cap.bindings = sorted(set(binds))

    # capture HOW the body is read (String / Reader / InputStream) — part of the
    # I/O contract that matters for adaptation (audit finding: was flattened)
    body_types = sorted(set(
        re.findall(r"getBody\s*\(\s*(?:java\.(?:lang|io)\.)?(\w+)", text)))
    cap.body_read_as = [b for b in body_types
                        if b in ("String", "Reader", "InputStream", "byte")]

    # count defined functions — a multi-function file is a LIBRARY of several
    # capabilities, not one (audit finding). Flagged for honest downstream use.
    cap.function_count = len(re.findall(r"\bdef\s+\w+\s*\(", text))
    cap.is_library = cap.function_count >= 5

    # operation keywords drawn from the ACTUAL method calls in the code (not
    # canned phrases) — this is the discriminating vocabulary that keeps real
    # capabilities findable (validation finding: scripts like a substring helper
    # had empty what_varies and were only describable by generic phrase words).
    # Derived from the corpus, not a fixed assumption, so it does not narrow
    # what can be recognised. We keep meaningful, function-bearing method names.
    _op_calls = re.findall(r"\.(\w+)\s*\(", text)
    _noise = {"toString", "getClass", "equals", "length", "size", "get", "put",
              "add", "toUpperCase", "toLowerCase"}
    cap.op_keywords = sorted({m for m in _op_calls
                              if len(m) > 3 and m not in _noise})[:25]

    # portable ops from libraries used
    pops = []
    for lib, op in _LIB_TO_OP.items():
        if lib in text:
            pops.append(op)
    if re.search(r"\.replaceAll\(|=~|==~|Pattern\.compile", text):
        if "REGEX" not in pops:
            pops.append("REGEX")
    cap.portable_ops = sorted(set(pops))

    # five facets
    names = _extract_property_names(text)
    reads = [o for o in ops if o.startswith("READ")]
    writes = [o for o in ops if o.startswith("WRITE")]
    emits = [o for o in ops if o.startswith("EMIT")]
    cap.needs = (["body"] if "READ_BODY" in reads else []) + \
        [f"property:{n}" for n in names]
    cap.what_varies = names + cap.portable_ops   # names + which transforms
    cap.shape = (f"envelope={cap.envelope}(Message); "
                 f"reads={len(reads)} writes={len(writes)} emits={len(emits)}"
                 + (f"; body_as={'/'.join(cap.body_read_as)}"
                    if cap.body_read_as else "")
                 + (f"; LIBRARY({cap.function_count} fns)"
                    if cap.is_library else ""))
    # purpose: a structural summary (honest about confidence)
    verb = []
    if "PARSE_JSON" in pops or "PARSE_XML" in pops:
        verb.append("parse")
    if pops and any(p.startswith("TRANSFORM") or p in ("REGEX", "BUILD_JSON",
                    "BUILD_XML") for p in pops):
        verb.append("transform")
    if emits:
        verb.append("log/attach")
    if writes:
        verb.append("write-back")
    cap.purpose = (" + ".join(verb) or "passthrough/property-handling") + \
        (f" ({', '.join(pops)})" if pops else "")
    cap.intent_confidence = "operational" if pops else "structural"
    # when-to-use: a selection hint from the dominant operation
    if "EMIT_ATTACHMENT" in ops:
        cap.when_to_use = "logging/diagnostics: attach payload to MPL"
    elif "PARSE_JSON" in pops:
        cap.when_to_use = "JSON payloads: parse/restructure JSON"
    elif "PARSE_XML" in pops:
        cap.when_to_use = "XML payloads: parse/restructure XML"
    elif "TRANSFORM_DATE" in pops:
        cap.when_to_use = "date format conversion"
    elif writes and not pops:
        cap.when_to_use = "set properties/headers/body from context"
    else:
        cap.when_to_use = "general payload/property manipulation"
    # weight: portable ops + io complexity (directional, SAP-aligned)
    cap.weight = len(pops) * 5 + len(reads) + len(writes) + len(emits) * 3
    return cap


def build_catalog(corpus: dict) -> dict:
    """Build the groovy capability catalog from a corpus (name->text).
    Returns {capabilities: [...], binding_vocabulary: {...}, index: {sig:[names]}}.
    """
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
