"""
extractor/schema_deduper.py

Collapse a large pile of harvested schema files (XSD / WSDL / EDMX) into a
canonical set for feeding message mappings. Three tiers, increasingly tolerant
of "personalization" (host URLs, tenant ids, version suffixes, whitespace):

  1. EXACT      — sha1 of raw bytes. Byte-identical; keep one.
  2. STRUCTURAL — sha1 of the normalized element/entity/operation NAME-SET.
                  Same shape, differs only in host/URL/whitespace/comments.
                  Keep one canonical (they map identically).
  3. FAMILY     — the logical schema identity (EDMX Schema Namespace, XSD
                  targetNamespace+root element, WSDL targetNamespace). Same
                  service across RELEASES/versions (e.g. API_BUSINESS_PARTNER
                  on two S/4 hosts where one has an extra EntityType). Keep the
                  RICHEST member (the superset) — it covers the most fields, so
                  a mapping built on it works against the others too.

This is intentionally structure-only (never executes or trusts URLs), so it is
safe to run across thousands of files. `richness` (element/entity/operation
count) drives the canonical pick within a family.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from collections import defaultdict
import xml.etree.ElementTree as ET


def _ln(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _sha(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()


def _names(root, want: set) -> set:
    out = set()
    for e in root.iter():
        if _ln(e.tag) in want:
            n = e.get("Name") or e.get("name")
            if n:
                out.add(n)
    return out


@dataclass
class SchemaFP:
    path: str
    kind: str          # xsd | wsdl | edmx
    exact: str
    struct: str
    family: str
    richness: int
    parseable: bool = True


def fingerprint(path: str, raw: bytes = None, kind: str = "") -> SchemaFP:
    if raw is None:
        raw = open(path, "rb").read()
    exact = hashlib.sha1(raw).hexdigest()
    if not kind:
        kind = path.rsplit(".", 1)[-1].lower()
    if kind not in ("xsd", "wsdl", "edmx"):
        kind = "xsd"  # default bucket
    try:
        root = ET.fromstring(raw)
    except Exception:
        return SchemaFP(path, kind, exact, "UNPARSEABLE:" + exact[:12],
                        "UNPARSEABLE", 0, parseable=False)

    if kind == "edmx":
        ets = _names(root, {"EntityType"})
        sets = _names(root, {"EntitySet"})
        cts = _names(root, {"ComplexType"})
        struct = _sha("EDMX|ET=" + ",".join(sorted(ets)) +
                      "|ES=" + ",".join(sorted(sets)) +
                      "|CT=" + ",".join(sorted(cts)))
        schema_ns = sorted({e.get("Namespace") for e in root.iter()
                            if _ln(e.tag) == "Schema" and e.get("Namespace")})
        # OData Schema Namespace (e.g. API_BUSINESS_PARTNER) is a stable,
        # host-independent service identity — an excellent family key.
        family = "EDMX:" + ",".join(schema_ns) if schema_ns else "EDMX#" + struct
        richness = len(ets) + len(sets) + len(cts)
    elif kind == "wsdl":
        tns = root.get("targetNamespace", "")
        ops = _names(root, {"operation"})
        msgs = _names(root, {"message"})
        els = _names(root, {"element"})
        struct = _sha(f"WSDL|tns={tns}|op={','.join(sorted(ops))}"
                      f"|msg={','.join(sorted(msgs))}"
                      f"|el={','.join(sorted(els))}")
        # tns alone is too coarse: every SAP IDoc WSDL shares
        # urn:sap-com:document:sap:idoc:soap:messages. Discriminate by the
        # portType/service name (named after the IDoc/interface, e.g. INVOIC).
        ptypes = sorted(_names(root, {"portType", "service"}))
        disc = ",".join(ptypes) or ",".join(sorted(list(els)[:3]))
        family = f"WSDL:{tns}::{disc}" if disc else "WSDL#" + struct
        richness = len(ops) + len(msgs) + len(els)
    else:  # xsd
        tns = root.get("targetNamespace", "")
        els = _names(root, {"element"})
        cts = _names(root, {"complexType"})
        sts = _names(root, {"simpleType"})
        struct = _sha(f"XSD|tns={tns}|el={','.join(sorted(els))}"
                      f"|ct={','.join(sorted(cts))}|st={','.join(sorted(sts))}")
        # Family key = message-type IDENTITY, never the namespace alone. A single
        # PI namespace holds many distinct message types, so keying on tns alone
        # over-merges and discards real schemas. Identity = namespace + the
        # specific (non-generic) root element name(s). Generic-root or no-root
        # schemas fall back to their structure so they're never merged by tns.
        roots = sorted({c.get("name") for c in root
                        if _ln(c.tag) == "element" and c.get("name")})
        generic = {"root", "Root", "MT", "Message", "message", "Messages"}
        specific_roots = [r for r in roots if r not in generic]
        if specific_roots:
            key = ",".join(specific_roots)
            family = f"XSD:{tns}::{key}" if tns else "XSD:" + key
        else:
            family = "XSD#" + struct   # generic/no specific root → identity is structure
        richness = len(els) + len(cts) + len(sts)
    return SchemaFP(path, kind, exact, struct, family, richness)


@dataclass
class DedupResult:
    fps: list = field(default_factory=list)
    # tier -> {key: [paths]}
    exact: dict = field(default_factory=dict)
    struct: dict = field(default_factory=dict)
    family: dict = field(default_factory=dict)


def dedup(paths) -> DedupResult:
    res = DedupResult()
    exact = defaultdict(list)
    struct = defaultdict(list)
    family = defaultdict(list)
    for p in paths:
        fp = fingerprint(p)
        res.fps.append(fp)
        exact[(fp.kind, fp.exact)].append(fp)
        struct[(fp.kind, fp.struct)].append(fp)
        family[(fp.kind, fp.family)].append(fp)
    res.exact, res.struct, res.family = dict(exact), dict(struct), dict(family)
    return res


def canonical(members: list) -> SchemaFP:
    """Pick the richest member of a family (superset of fields) as canonical."""
    return max(members, key=lambda fp: (fp.richness, len(fp.path)))


def summarize(res: DedupResult) -> str:
    out = []
    for kind in ("xsd", "wsdl", "edmx"):
        fps = [f for f in res.fps if f.kind == kind]
        if not fps:
            continue
        n = len(fps)
        n_exact = len({f.exact for f in fps})
        n_struct = len({f.struct for f in fps})
        n_family = len({f.family for f in fps})
        out.append(f"{kind.upper()}: {n} files → {n_exact} exact-unique "
                   f"→ {n_struct} structural-unique → {n_family} families")
    return "\n".join(out)


def build_report(res: "DedupResult") -> str:
    """Markdown report: per-type collapse counts + every multi-member family
    with its recommended canonical (richest) member."""
    lines = ["# Schema dedup / clustering report", "", "## Collapse summary", "",
             "```", summarize(res), "```", "",
             "Tiers: **exact** (byte-identical, drop dupes) → **structural** "
             "(same shape, differs only in host/whitespace) → **family** "
             "(same logical schema across releases — keep the richest superset).", ""]
    total = len(res.fps)
    canon_count = len(res.family)
    lines.append(f"**{total} files → {canon_count} canonical schemas** "
                 f"(one richest member per family).\n")
    for kind in ("edmx", "wsdl", "xsd"):
        fams = {k: v for k, v in res.family.items() if k[0] == kind and len(v) > 1}
        if not fams:
            continue
        lines.append(f"## {kind.upper()} — {len(fams)} families with variants\n")
        for (k, fam), members in sorted(fams.items(), key=lambda kv: -len(kv[1])):
            canon = canonical(members)
            exacts = {m.exact for m in members}
            kindword = ("byte-identical" if len(exacts) == 1
                        else "personalized/versioned variants")
            label = fam.split(":", 1)[1] if ":" in fam else fam
            lines.append(f"### `{label[:70]}` — {len(members)} members ({kindword})")
            for m in members:
                star = " ⭐ KEEP" if m is canon else "        "
                lines.append(f"-{star} richness={m.richness} · "
                             f"`{os.path.basename(m.path)}`")
            lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys, glob
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    out = sys.argv[2] if len(sys.argv) > 2 else None
    paths = [f for f in glob.glob(root + "/**/*", recursive=True)
             if os.path.isfile(f) and f.rsplit(".", 1)[-1].lower() in ("xsd", "wsdl", "edmx")]
    result = dedup(paths)
    report = build_report(result)
    if out:
        open(out, "w").write(report)
        print("wrote", out)
    else:
        print(report)
