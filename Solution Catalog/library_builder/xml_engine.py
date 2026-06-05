"""library_builder/xml_engine.py

Analyze the XML artifact family (mmap, opmap, iflw, xsl/xslt, xsd, wsdl, edmx,
odata, generic xml) into Solution entries.

These are not code — they are structured documents — so "patterns" here means
recurring NODE STRUCTURE, not idioms. We extract:
  * root element + primary namespace        (what kind of document)
  * the set of distinct element local-names (the structural vocabulary)
  * for mappings: the transformation functions / node types used
  * a structural fingerprint = hash of the sorted (element, depth-bucket)
    signature, so two documents with the same shape collapse, while
    structurally different ones stay distinct
  * provisional category from root/namespace

Metadata/manifest XML is rejected by root element + filename signals.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Optional
from xml.etree import ElementTree as ET

from library_builder.extractor import Solution, _sha, REJECT_XML_ROOTS


# root/namespace -> (type, category) hints
def _classify_xml(ext: str, root_local: str, ns: str) -> tuple[str, str]:
    nsl = (ns or "").lower()
    rl = (root_local or "").lower()
    if ext in ("xsl", "xslt") or "xslt" in nsl or rl in ("stylesheet", "transform"):
        return "xslt", "XSLT_TRANSFORM"
    if ext == "xsd" or "xmlschema" in nsl or rl == "schema":
        return "xsd", "SCHEMA"
    if ext == "wsdl" or "wsdl" in nsl or rl == "definitions" and "wsdl" in nsl:
        return "wsdl", "SERVICE_CONTRACT"
    if ext == "edmx" or "edmx" in nsl or rl == "edmx":
        return "edmx", "ODATA_MODEL"
    if ext == "odata":
        return "odata", "ODATA_MODEL"
    if ext == "mmap":
        return "mmap", "MESSAGE_MAPPING"
    if ext == "opmap":
        return "opmap", "OPERATION_MAPPING"
    if ext == "iflw" or "bpmn" in nsl or rl == "definitions":
        return "iflw", "INTEGRATION_FLOW"
    return "xml", "XML_OTHER"


def _local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def analyze_xml(text: str, ext: str) -> tuple[Optional[Solution], Optional[str]]:
    """Returns (Solution, None) on success, or (None, reason) if rejected."""
    if not text or not text.strip():
        return None, "empty"
    try:
        root = ET.fromstring(text.encode("utf-8", "replace"))
    except Exception as exc:
        return None, f"xml parse error: {str(exc)[:60]}"

    root_local = _local(root.tag)
    if root_local.lower() in REJECT_XML_ROOTS:
        return None, f"metadata root <{root_local}>"

    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag[1:root.tag.index("}")]

    typ, category = _classify_xml(ext, root_local, ns)

    # structural vocabulary: count element local-names with a coarse depth
    elem_sig = Counter()
    func_names = Counter()       # for mappings: function-ish nodes
    def walk(node, depth):
        ln = _local(node.tag)
        bucket = min(depth, 4)   # coarse depth so minor nesting varies don't explode
        elem_sig[(ln, bucket)] += 1
        # mapping function heuristics
        low = ln.lower()
        if low in ("function", "func", "udf", "mappingfunction", "standardfunction"):
            name = (node.get("name") or node.get("Name") or
                    node.get("functionName") or "")
            if name:
                func_names[name] += 1
        for ch in list(node):
            walk(ch, depth + 1)
    walk(root, 0)

    # distinct element vocabulary (for display) and fingerprint
    vocab = sorted({ln for (ln, _b) in elem_sig})

    # CONTENT-AWARE fingerprint: two XML artifacts are the same solution only
    # if their meaningful content matches, not merely their structural shape.
    # We hash the full set of (path-ish element name, attribute names+values,
    # normalized text) so different mappings/schemas/wsdls that happen to share
    # a structure DO NOT collapse. This prevents losing distinct solutions.
    content_tokens = []
    def collect(node, path):
        ln = _local(node.tag)
        here = f"{path}/{ln}"
        # attribute name=value pairs (sorted, identity-bearing)
        for ak, av in sorted(node.attrib.items()):
            content_tokens.append(f"{here}@{_local(ak)}={av.strip()}")
        # element text if meaningful
        txt = (node.text or "").strip()
        if txt:
            content_tokens.append(f"{here}#={txt}")
        for ch in list(node):
            collect(ch, here)
    collect(root, "")
    content_hash = _sha("\n".join(content_tokens))
    fingerprint = _sha(typ + "|" + content_hash)

    operations = []
    if func_names:
        operations = [f"fn:{n}" for n, _ in func_names.most_common(30)]

    requires = {"namespace": ns, "root": root_local,
                "element_count": sum(elem_sig.values()),
                "distinct_elements": len(vocab)}
    produces = {}

    # store a compact structural summary as "code" (not the raw doc, which may
    # be huge / IP-sensitive); the vocabulary + functions describe the shape.
    summary = {
        "root": root_local, "namespace": ns,
        "vocabulary": vocab[:80],
        "functions": [n for n, _ in func_names.most_common(40)],
    }
    import json as _json
    code = _json.dumps(summary, indent=2)

    return Solution(
        fingerprint=fingerprint,
        type=typ,
        category=category,
        imports=[],
        services=[],
        operations=operations,
        requires=requires,
        produces=produces,
        code=code,
    ), None
