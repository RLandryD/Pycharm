"""sample_payload.py — generate a minimal, well-formed sample XML instance from
an XSD, so a mock content modifier seeds a realistic body (resembling what the
real sender would deliver) instead of a generic placeholder.

Best-effort and defensive: handles the common element/complexType/sequence
shapes, caps recursion (schemas can be recursive), and falls back to a generic
stub on anything it can't parse — never raises.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

_XS = "http://www.w3.org/2001/XMLSchema"
_MAX_DEPTH = 12

_SAMPLE = {
    "string": "sample", "normalizedString": "sample", "token": "sample",
    "int": "0", "integer": "0", "long": "0", "short": "0", "byte": "0",
    "decimal": "0.0", "float": "0.0", "double": "0.0",
    "boolean": "true", "date": "2024-01-01", "dateTime": "2024-01-01T00:00:00",
    "time": "00:00:00", "anyURI": "http://example.com", "base64Binary": "AA==",
}


def _local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _strip_ns(name: str) -> str:
    return name.split(":")[-1] if name and ":" in name else (name or "")


def generic_payload() -> str:
    return ("<MockPayload>\n  <note>sample payload generated for mock "
            "testing</note>\n</MockPayload>")


def sample_payload_from_xsd(xsd: str, root_name: str | None = None) -> str:
    """Return a sample XML instance for the XSD's root element, or a generic
    stub if the schema can't be interpreted."""
    try:
        schema = ET.fromstring(xsd)
    except Exception:
        return generic_payload()

    complex_types, simple_types, top_elements = {}, {}, []
    for c in list(schema):
        lt = _local(c.tag)
        nm = c.get("name")
        if lt == "complexType" and nm:
            complex_types[nm] = c
        elif lt == "simpleType" and nm:
            simple_types[nm] = c
        elif lt == "element":
            top_elements.append(c)

    if not top_elements:
        return generic_payload()
    root = next((e for e in top_elements if e.get("name") == root_name),
                top_elements[0])

    try:
        node = _build_element(root, complex_types, simple_types, 0)
    except Exception:
        return generic_payload()
    if node is None:
        return generic_payload()
    try:
        return ET.tostring(node, encoding="unicode")
    except Exception:
        return generic_payload()


def _type_sample(type_name: str) -> str:
    return _SAMPLE.get(_strip_ns(type_name), "sample")


def _find_complex(el, complex_types):
    """Return the complexType element for `el` (inline child or named ref)."""
    for c in list(el):
        if _local(c.tag) == "complexType":
            return c
    t = _strip_ns(el.get("type", ""))
    return complex_types.get(t)


_GROUP = {"sequence", "all", "choice", "group", "complexContent",
          "simpleContent", "extension", "restriction"}


def _walk_group(container, elements, attributes):
    """Collect the element/attribute children of a complexType's model group,
    descending through group wrappers (sequence/choice/extension/...) but NOT
    crossing into a child element's own type — that's where .iter() leaked."""
    for c in list(container):
        lt = _local(c.tag)
        if lt == "element":
            elements.append(c)
        elif lt == "attribute":
            attributes.append(c)
        elif lt in _GROUP:
            _walk_group(c, elements, attributes)


def _build_element(el, complex_types, simple_types, depth):
    name = el.get("name") or el.get("ref") or "element"
    name = _strip_ns(name)
    node = ET.Element(name)
    if depth >= _MAX_DEPTH:
        node.text = "..."
        return node

    ctype = _find_complex(el, complex_types)
    if ctype is None:
        node.text = _type_sample(el.get("type", "string"))
        return node

    child_els, attrs = [], []
    _walk_group(ctype, child_els, attrs)
    for a in attrs:
        node.set(_strip_ns(a.get("name") or "attr"),
                 _type_sample(a.get("type", "string")))
    for ce in child_els:
        child = _build_element(ce, complex_types, simple_types, depth + 1)
        if child is not None:
            node.append(child)
    if not list(node) and not node.text:
        node.text = "sample"
    return node
