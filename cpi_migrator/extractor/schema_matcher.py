"""
extractor/schema_matcher.py

Bridge between an extracted PI/PO interface and the canonical schema library:
given an interface's message type (name + namespace), find the schema file in
the library that should back its message mapping.

How matching works
------------------
A PI Message Interface points at a Message Type whose XSD has a root element of
that name in the interface's namespace. So we index every library schema by:
  - targetNamespace (XSD/WSDL) or Schema Namespace (EDMX)
  - the set of top-level element / entity / type NAMES (lowercased)
and score a query (name, namespace, kind) against that index:
  - namespace exact match            +3
  - name == a top-level name (ci)    +3
  - name ⊂ a name (or vice-versa)    +1
  - kind matches the requested kind  +1
The highest-scoring entries come back ranked, with the score so the caller can
decide whether the match is confident enough to bundle automatically.

This module is pure logic over files — no network, no tenant — so it is fully
testable offline. It reuses extractor.schema_deduper for fingerprints.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import xml.etree.ElementTree as ET

from extractor.schema_deduper import fingerprint, _ln


def _meta(path: str, kind: str) -> tuple[str, set]:
    """Return (namespace, {top-level names lowercased}) for a schema file."""
    try:
        root = ET.fromstring(Path(path).read_bytes())
    except Exception:
        return "", set()
    names: set = set()
    if kind == "edmx":
        ns = ""
        for e in root.iter():
            ln = _ln(e.tag)
            if ln == "Schema" and e.get("Namespace") and not ns:
                ns = e.get("Namespace")
            if ln in ("EntityType", "EntitySet", "ComplexType"):
                n = e.get("Name")
                if n:
                    names.add(n.lower())
        return ns, names
    # xsd / wsdl
    ns = root.get("targetNamespace", "")
    want = {"element", "complexType", "simpleType"} if kind == "xsd" else {"element", "portType", "message", "operation"}
    for e in root.iter():
        if _ln(e.tag) in want:
            n = e.get("name") or e.get("Name")
            if n:
                names.add(n.lower())
    return ns, names


@dataclass
class SchemaEntry:
    path: str
    kind: str
    namespace: str
    names: set
    family: str


@dataclass
class Match:
    entry: SchemaEntry
    score: int
    reasons: list = field(default_factory=list)


class SchemaIndex:
    def __init__(self):
        self.entries: list[SchemaEntry] = []

    @classmethod
    def build(cls, *library_dirs: str) -> "SchemaIndex":
        idx = cls()
        for d in library_dirs:
            for p in Path(d).rglob("*"):
                if not p.is_file():
                    continue
                ext = p.suffix.lower().lstrip(".")
                if ext not in ("xsd", "wsdl", "edmx"):
                    continue
                fp = fingerprint(str(p))
                ns, names = _meta(str(p), fp.kind)
                idx.entries.append(SchemaEntry(str(p), fp.kind, ns, names, fp.family))
        return idx

    def match(self, name: str = "", namespace: str = "", kind: str = "",
              top: int = 5) -> list[Match]:
        name_l = (name or "").lower().strip()
        ns = (namespace or "").strip()
        out: list[Match] = []
        for e in self.entries:
            if kind and e.kind != kind:
                continue
            score = 0
            reasons = []
            if ns and e.namespace and ns == e.namespace:
                score += 3
                reasons.append("namespace exact")
            if name_l:
                if name_l in e.names:
                    score += 3
                    reasons.append("name exact")
                elif any(name_l in n or n in name_l for n in e.names):
                    score += 1
                    reasons.append("name partial")
            if score > 0:
                out.append(Match(e, score, reasons))
        out.sort(key=lambda m: (-m.score, m.entry.path))
        return out[:top]


def match_for_interface(index: SchemaIndex, message_interface: str = "",
                        namespace: str = "", prefer_kind: str = "") -> Match | None:
    """Best schema match for an interface's message type, or None if nothing scores."""
    hits = index.match(name=message_interface, namespace=namespace, kind=prefer_kind, top=1)
    return hits[0] if hits else None
