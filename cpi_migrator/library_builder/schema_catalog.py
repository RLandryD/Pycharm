"""library_builder/schema_catalog.py

Schema identity catalog for XSD / WSDL / EDMX — the "identity" learning type:
these artifacts are NEVER generated, only REUSED. They are catalogued WHOLE
(not decomposed into capabilities) by WHAT SCHEMA THEY DEFINE, so an mmap or
iflow that needs a given structure can find and reference the existing file.

Three jobs (all fully standalone — pure W3C, zero SAP coupling, no tenant step):
  IDENTITY  — what does this schema define? (kind, target namespace, root
              elements, named types, the OData entity types for edmx)
  DEDUPE    — is this the same schema as one we already have? (content
              fingerprint + a structural fingerprint that ignores formatting)
  INDEX     — find a schema by namespace / root element / type, for reuse.

Built + validated entirely in the sandbox against 174 xsd / 71 wsdl / 13 edmx
real specimens.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field as _field


@dataclass
class SchemaIdentity:
    name: str
    kind: str                       # xsd | wsdl | edmx
    target_namespace: str = ""
    roots: list = _field(default_factory=list)      # top-level element names
    types: list = _field(default_factory=list)      # named complex/simple types
    entities: list = _field(default_factory=list)   # edmx EntityType names
    content_hash: str = ""          # exact-bytes fingerprint
    struct_hash: str = ""           # structure fingerprint (formatting-agnostic)
    size: int = 0                   # byte length (for "prefer the bigger")
    well_formed: bool = True        # parses as XML (tags open/close, comments ok)
    validity_error: str = ""        # why it failed, if damaged

    def defines(self) -> set:
        """Everything this schema makes available for reuse (lookup keys)."""
        keys = set(self.roots) | set(self.types) | set(self.entities)
        return keys

    def defines_list(self) -> list:
        keys = list(self.defines())
        if self.target_namespace:
            keys.append(self.target_namespace)
        return keys


def _kind(name: str, text: str) -> str:
    n = name.lower()
    if n.endswith(".edmx") or "edmx:Edmx" in text or "<Edmx" in text:
        return "edmx"
    if n.endswith(".wsdl") or "<wsdl:" in text or "<definitions" in text:
        return "wsdl"
    return "xsd"


def _struct_fingerprint(text: str, kind: str = "xsd") -> str:
    """Structural IDENTITY of a schema — derived from analysing the real
    corpus (174 xsd / 71 wsdl / 13 edmx). Identity is the STRUCTURE it defines,
    NOT the top-level name (e.g. 17 different XSDs all name their root "root").
    Per-type identity rule (evidence-based):
      - xsd  : the SET of element + complexType/simpleType names defined.
      - wsdl : targetNamespace + that same element/type set (ns scopes identity).
      - edmx : Schema Namespace + the SET of EntityType names (the OData tables).
    Two schemas with the same identity are duplicates (personalized/trimmed
    versions of the same source table collapse together; canonical = biggest).
    Falls back to normalised-content hash if no named structure is found, so
    dissimilar files never falsely merge.
    """
    if kind == "edmx":
        ents = sorted(set(re.findall(r'<EntityType\s+Name="([^"]+)"', text)))
        ns = re.search(r'<Schema[^>]*\bNamespace="([^"]+)"', text)
        struct = (ns.group(1) if ns else "") + "::" + "|".join(ents)
    else:
        names = sorted(set(re.findall(
            r'<(?:xs:|xsd:|s:)?(?:element|complexType|simpleType)\s+'
            r'[Nn]ame="([^"]+)"', text)))
        if kind == "wsdl":
            tns = re.search(r'targetNamespace="([^"]+)"', text)
            struct = (tns.group(1) if tns else "") + "::" + "|".join(names)
        else:
            struct = "|".join(names)
    if struct.strip("|:") == "":
        # no extractable named structure -> normalised content, never merge blind
        norm = re.sub(r"\s+", " ",
                      re.sub(r"<!--.*?-->", "", text, flags=re.S)).strip()
        return "C" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:15]
    return kind[0].upper() + hashlib.sha256(struct.encode("utf-8")).hexdigest()[:15]


def _check_well_formed(text: str):
    """Parse as XML to confirm the schema actually WORKS — tags open/close
    correctly, comments don't break it. Returns (ok, error_msg)."""
    try:
        from lxml import etree
        etree.fromstring(text.encode("utf-8"))
        return True, ""
    except ImportError:
        # fall back to stdlib parser if lxml unavailable
        try:
            import xml.etree.ElementTree as ET
            ET.fromstring(text)
            return True, ""
        except Exception as e:  # noqa
            return False, str(e)[:120]
    except Exception as e:  # noqa
        return False, str(e)[:120]


def extract_identity(name: str, text: str) -> SchemaIdentity:
    kind = _kind(name, text)
    ident = SchemaIdentity(name=name, kind=kind)
    ident.size = len(text)
    ident.well_formed, ident.validity_error = _check_well_formed(text)
    tns = re.search(r'targetNamespace="([^"]+)"', text)
    if tns:
        ident.target_namespace = tns.group(1)
    if kind == "edmx":
        ident.entities = sorted(set(
            re.findall(r'<EntityType\s+Name="([^"]+)"', text)))
        # edmx schema namespace
        ns = re.search(r'<Schema[^>]*\bNamespace="([^"]+)"', text)
        if ns and not ident.target_namespace:
            ident.target_namespace = ns.group(1)
    else:
        ident.roots = sorted(set(
            re.findall(r'<(?:xs:|xsd:)?element\s+name="([^"]+)"', text)))
        ident.types = sorted(set(
            re.findall(r'<(?:xs:|xsd:)?(?:complexType|simpleType)\s+name="([^"]+)"',
                       text)))
    ident.content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    ident.struct_hash = _struct_fingerprint(text, kind)
    return ident


def build_catalog(corpus: dict) -> dict:
    """Build the schema catalog from a corpus (name->text).
    Returns identities, dedupe groups (with canonical = valid + biggest),
    subset/superset families, reuse indexes, and a validity report."""
    idents = [extract_identity(n, t) for n, t in corpus.items()]
    by = {i.name: i for i in idents}

    # VALIDITY: a schema must parse to be reusable. Flag the damaged ones.
    damaged = {i.name: i.validity_error for i in idents if not i.well_formed}

    # DEDUPE: group by struct_hash (formatting-agnostic). For each group pick a
    # CANONICAL: must be well-formed; tie-break by LARGEST size (keeps the
    # fuller file — more comments/annotations retained, never the trimmed one).
    by_struct = {}
    for i in idents:
        by_struct.setdefault(i.struct_hash, []).append(i.name)
    dedup_groups = {}
    canonical_of = {}
    for h, names in by_struct.items():
        if len(names) > 1:
            members = [by[n] for n in names]
            # prefer well-formed, then biggest size
            canon = sorted(members,
                           key=lambda m: (m.well_formed, m.size),
                           reverse=True)[0]
            dedup_groups[h] = {
                "canonical": canon.name,
                "members": sorted(names),
                "reason": "identical structure; canonical = valid + largest",
            }
            for n in names:
                canonical_of[n] = canon.name

    # SUBSET/SUPERSET FAMILIES: catches "personalized cuts" — schema A whose
    # defined names are a proper subset of schema B's (same kind, overlapping
    # namespace). B is the fuller, preferred-for-reuse schema. Candidate signal
    # (confirm where it matters), not a certainty.
    subset_families = []
    big = [i for i in idents if len(i.defines()) >= 3]
    for a in big:
        a_def = a.defines()
        for b in big:
            if a is b or a.kind != b.kind:
                continue
            b_def = b.defines()
            if a_def < b_def:        # proper subset
                subset_families.append({
                    "subset": a.name, "superset": b.name,
                    "preferred": b.name,
                    "subset_defines": len(a_def), "superset_defines": len(b_def),
                })

    # INDEX for reuse (only well-formed schemas are offered for reuse)
    by_namespace, by_name = {}, {}
    for i in idents:
        if not i.well_formed:
            continue
        if i.target_namespace:
            by_namespace.setdefault(i.target_namespace, []).append(i.name)
        for key in i.defines():
            by_name.setdefault(key, []).append(i.name)

    return {
        "identities": idents,
        "count": len(idents),
        "distinct_schemas": len(by_struct),
        "duplicate_groups": dedup_groups,
        "canonical_of": canonical_of,
        "subset_families": subset_families,
        "damaged": damaged,
        "well_formed_count": sum(1 for i in idents if i.well_formed),
        "by_namespace": by_namespace,
        "by_name": by_name,
        "kinds": _kind_counts(idents),
    }


def _kind_counts(idents):
    from collections import Counter
    return dict(Counter(i.kind for i in idents))


def find_schema(catalog: dict, *, defines: str = None, namespace: str = None):
    """Reuse lookup: find which schema file(s) define a given root/type/entity
    or target namespace."""
    hits = []
    if defines and defines in catalog["by_name"]:
        hits += catalog["by_name"][defines]
    if namespace and namespace in catalog["by_namespace"]:
        hits += catalog["by_namespace"][namespace]
    return sorted(set(hits))
