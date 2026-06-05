"""library_builder/code_engine.py

Analyze CPI Groovy/JS scripts into Solution entries.

Extraction is heuristic (regex-based), not a full language AST — pragmatic,
dependency-free, and reliable for the facts that matter: imports, method
signatures, the SAP Message API calls, recognizable operations, and a
normalized structural fingerprint for conservative deduplication.

What we pull out per script:
  * imports            -> interned into the IMPORTS registry
  * external services  -> ITApiFactory.getApi(X) / known service classes
  * operations         -> tags like json_parse, xml_parse, set_header,
                          set_property, set_body, throw, http_code, logging
  * requires/produces  -> headers/properties read vs written, body type
  * fingerprint        -> hash of the normalized control structure so cosmetic
                          differences (variable names, literals, spacing)
                          collapse, but genuinely different logic stays distinct
  * category           -> provisional, rule-based from the operations/imports

Variable normalization: common bindings are renamed to descriptive names
(map->headers/properties by source, slurper->jsonSlurper, etc.) both to make
stored code readable and to help cosmetic dedup.
"""
from __future__ import annotations

import re
from typing import Optional

from library_builder.extractor import Solution, _sha


# ---- regexes ---------------------------------------------------------------
RE_IMPORT      = re.compile(r"^\s*import\s+([\w.$]+)", re.M)
RE_JS_REQUIRE  = re.compile(r"require\(['\"]([^'\"]+)['\"]\)")
RE_METHOD      = re.compile(r"\b(?:def\s+)?(?:[\w.<>]+\s+)?(\w+)\s*\([^)]*\)\s*\{")
RE_GET_HEADER  = re.compile(r'\.get(?:Header)?\(\s*["\']([^"\']+)["\']')
RE_HDR_GET     = re.compile(r'(?:headers?|getHeaders\(\))\s*\.\s*get\(\s*["\']([^"\']+)["\']')
RE_SET_HEADER  = re.compile(r'setHeader\(\s*["\']([^"\']+)["\']')
RE_GET_PROP    = re.compile(r'(?:properties|getProperties\(\))\s*\.\s*get\(\s*["\']([^"\']+)["\']')
RE_SET_PROP    = re.compile(r'setProperty\(\s*["\']([^"\']+)["\']')
RE_GETAPI      = re.compile(r'ITApiFactory\.getApi\(\s*([\w.]+)')
RE_SERVICE_CLS = re.compile(r'\b([A-Z]\w+Service)\b')

# operation signatures -> tag. Order matters: more specific first.
OP_SIGNATURES = [
    # --- message-mapping UDF API (com.sap.it.api.mapping) ---
    ("udf_context_read",  re.compile(r"context\.getHeader|context\.getProperty")),
    ("udf_output",        re.compile(r"\bOutput\s+\w+|output\.addValue")),
    ("udf_mapping_api",   re.compile(r"com\.sap\.it\.api\.mapping|MappingContext")),
    # --- iFlow Message API ---
    ("json_parse",   re.compile(r"JsonSlurper|new\s+JsonSlurper")),
    ("json_build",   re.compile(r"JsonBuilder|JsonOutput")),
    ("xml_parse",    re.compile(r"XmlParser|XmlSlurper")),
    ("xml_serialize",re.compile(r"XmlUtil\.serialize|StreamingMarkupBuilder|MarkupBuilder")),
    ("set_header",   re.compile(r"setHeader\(")),
    ("set_property", re.compile(r"setProperty\(")),
    ("set_body",     re.compile(r"setBody\(")),
    ("read_body",    re.compile(r"getBody\(")),
    ("http_code",    re.compile(r"CamelHttpResponseCode|HttpResponseCode")),
    ("throw_error",  re.compile(r"throw\s+new\s+\w+(?:Exception|Error)")),
    ("logging",      re.compile(r"messageLog|MessageLog|addEntry|PipelineLogger|\bLogger\b")),
    ("loop",         re.compile(r"\.each\s*\{|\bfor\s*\(")),
    ("partner_dir",  re.compile(r"PartnerDirectoryService")),
    ("base64",       re.compile(r"encodeBase64|decodeBase64|\bBase64\b")),
    ("regex",        re.compile(r"=~|==~|Pattern\.compile")),
    ("external_call",re.compile(r"ITApiFactory\.getApi")),
]

# A UDF is a mapping function: imports the mapping API or uses MappingContext,
# and does NOT use the iFlow processData(Message) entry point.
RE_UDF_MARKER = re.compile(r"com\.sap\.it\.api\.mapping|MappingContext|\bOutput\b\s+\w+")
RE_PROCESSDATA = re.compile(r"\bprocessData\s*\(\s*Message")
# UDF function definitions: def <ret> name(<params incl MappingContext/Output>)
RE_UDF_FUNC = re.compile(r"\bdef\s+\w+\s+(\w+)\s*\([^)]*(?:MappingContext|Output|String\[\])")

# provisional category rules: (category, predicate over op-set/imports)
def _categorize(ops: set, imports: list, services: list) -> str:
    has = lambda *o: any(x in ops for x in o)
    # Mapping UDFs are a distinct species — check first.
    if has("udf_mapping_api", "udf_context_read", "udf_output"):
        return "MAPPING_UDF"
    if "partner_dir" in ops or any("PartnerDirectory" in s for s in services):
        return "PARTNER_DIRECTORY"
    if has("xml_parse", "xml_serialize") and has("json_parse", "json_build"):
        return "CONVERTERS"
    if has("xml_parse", "xml_serialize"):
        return "XML_HANDLING"
    if has("json_parse", "json_build"):
        return "JSON_HANDLING"
    if "throw_error" in ops and not has("set_body", "set_header"):
        return "VALIDATION"
    if "logging" in ops and len(ops) <= 3:
        return "LOGGING"
    if "http_code" in ops:
        return "HTTP_RESPONSE_HANDLING"
    if has("set_header", "set_property") and not has("json_parse", "xml_parse"):
        return "HEADER_PROPERTY_MANIPULATION"
    if "base64" in ops:
        return "ENCODING"
    return "GENERAL_TRANSFORM"


# variable normalization (descriptive names; helps cosmetic dedup + readability)
_RENAME = [
    (re.compile(r'\bdef\s+(\w+)\s*=\s*message\.getHeaders\(\)'),  r'def headers = message.getHeaders()'),
    (re.compile(r'\bdef\s+(\w+)\s*=\s*message\.getProperties\(\)'), r'def properties = message.getProperties()'),
    (re.compile(r'\bnew\s+JsonSlurper\(\)'),                       r'new JsonSlurper()'),
]

def _normalize_structure(code: str) -> str:
    """Reduce code to a structural skeleton for fingerprinting:
       - strip comments and string/number literals (placeholders)
       - rename LOCAL variables (def X = ... and their uses) to generic tokens,
         so cosmetic renames collapse, while PRESERVING API/method/class names
         (getBody, JsonSlurper, setProperty…) which carry real meaning
       - collapse whitespace
       Genuinely different control flow / API usage stays distinct."""
    s = code
    s = re.sub(r"//[^\n]*", "", s)                 # line comments
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)    # block comments
    s = re.sub(r'"(?:\\.|[^"\\])*"', '"S"', s)     # string literals
    s = re.sub(r"'(?:\\.|[^'\\])*'", "'S'", s)     # single-quote strings
    s = re.sub(r"\b\d+\b", "N", s)                 # numbers

    # Collect locally-declared variable names: `def NAME =`, `String NAME =`,
    # `for (def NAME :`, and closure params `{ NAME ->`.
    locals_ = set()
    for rx in (
        re.compile(r'\bdef\s+(\w+)\s*='),
        re.compile(r'\b(?:String|int|Integer|boolean|Map|List|Object|Reader|def)\s+(\w+)\s*='),
        re.compile(r'\bfor\s*\(\s*def\s+(\w+)'),
        re.compile(r'\{\s*(\w+)\s*->'),
    ):
        locals_.update(rx.findall(s))
    # Don't rename things that are clearly not locals
    locals_.discard("message")
    # Replace each local name with a generic token V (whole-word)
    for name in sorted(locals_, key=len, reverse=True):
        if len(name) < 2:
            continue
        s = re.sub(rf'\b{re.escape(name)}\b', 'V', s)

    s = re.sub(r"\s+", " ", s).strip()
    return s


def analyze_code(text: str, ext: str, imports_reg, services_reg, idioms_reg
                 ) -> Optional[Solution]:
    if not text or not text.strip():
        return None
    # very small / non-code guard
    if len(text.strip()) < 20:
        return None

    # imports
    if ext == "js":
        raw_imports = RE_JS_REQUIRE.findall(text)
    else:
        raw_imports = RE_IMPORT.findall(text)
    import_ids = [imports_reg.intern(i) for i in dict.fromkeys(raw_imports)]

    # services
    svc = set(RE_GETAPI.findall(text))
    svc |= {m for m in RE_SERVICE_CLS.findall(text)}
    # only keep service-looking class names actually referenced via getApi or import
    svc = {s for s in svc if "Service" in s}
    service_ids = [services_reg.intern(s) for s in sorted(svc)]

    # operations
    ops = set()
    for tag, rx in OP_SIGNATURES:
        if rx.search(text):
            ops.add(tag)

    # requires / produces
    headers_in = sorted(set(RE_HDR_GET.findall(text)) | set(
        m for m in RE_GET_HEADER.findall(text)))
    headers_out = sorted(set(RE_SET_HEADER.findall(text)))
    props_in = sorted(set(RE_GET_PROP.findall(text)))
    props_out = sorted(set(RE_SET_PROP.findall(text)))
    body_type = ""
    m = re.search(r"getBody\(\s*([\w.]+)", text)
    if m:
        body_type = m.group(1)

    requires = {"headers_in": headers_in, "properties_in": props_in,
                "body_type": body_type, "services": sorted(svc)}
    produces = {"headers_out": headers_out, "properties_out": props_out,
                "body_written": "set_body" in ops}

    # --- UDF handling: mapping functions are a distinct species ---
    is_udf = bool(RE_UDF_MARKER.search(text)) and not RE_PROCESSDATA.search(text)
    if is_udf:
        udf_funcs = [f for f in RE_UDF_FUNC.findall(text)]
        # also catch plain `def String name(` style mapping functions
        udf_funcs += [m for m in re.findall(r"\bdef\s+\w+\s+(\w+)\s*\(", text)
                      if m not in udf_funcs]
        udf_funcs = list(dict.fromkeys(udf_funcs))
        # the function names ARE the "what it does" — record as operations
        ops |= {f"udf_fn:{fn}" for fn in udf_funcs[:30]}
        requires["udf_functions"] = udf_funcs
        # detect a template-only stub: strip comments; if almost nothing left,
        # it's the SAP-generated boilerplate, not real logic.
        stripped = re.sub(r"//[^\n]*", "", text)
        stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.S)
        stripped = re.sub(r"import[^\n]*", "", stripped)
        if len(stripped.strip()) < 40 or not udf_funcs:
            # near-empty after removing comments/imports -> template stub
            requires["template_stub"] = True

    # normalized code (descriptive renames applied for readability)
    normalized = text
    for rx, repl in _RENAME:
        normalized = rx.sub(repl, normalized)

    # fingerprint: structural skeleton + sorted op-set + sorted imports
    skeleton = _normalize_structure(text)
    fp_src = skeleton + "|" + ",".join(sorted(ops)) + "|" + ",".join(sorted(raw_imports))
    fingerprint = _sha(fp_src)

    category = _categorize(ops, raw_imports, sorted(svc))

    return Solution(
        fingerprint=fingerprint,
        type=("javascript" if ext == "js" else "groovy"),
        category=category,
        imports=import_ids,
        services=service_ids,
        operations=sorted(ops),
        requires=requires,
        produces=produces,
        code=normalized.strip(),
    )
