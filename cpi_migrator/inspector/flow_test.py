"""inspector/flow_test.py — test a payload against a SPECIFIC iFlow's
expectations, locally, without the tenant.

Walks the flow's main-process steps in sequence order and, for every step
whose behavior depends on the payload, evaluates the step's REAL config
against the payload:

  XmlValidator        → resolve its `xsd` from the bundle resources and
                        validate (xmlschema; error paths)
  Splitter (XPath)    → `splitExprValue` match count (0 = the splitter
                        produces nothing — the classic silent failure)
  Filter              → `wrapContent` XPath match count
  ExclusiveGateway    → evaluate each route's condition; report which route
                        fires (or that only the default would)
  XmlToCsvConverter   → `XPath_Field_Location` match count
  CsvToXmlConverter   → schema file presence in bundle + separator sanity
  EDI converters      → payload envelope vs the configured schema doc id
                        (e.g. ST-01 850 vs ASC-X12_850 schema)
  XSLT mappings       → optionally APPLY the stylesheet via lxml (XSLT 1.0;
                        2.0/3.0 stylesheets are reported as not locally
                        executable, which is itself useful information)

XPaths use lxml. Payloads frequently arrive without the namespaces the
design-time XPaths assume; when a path matches nothing, a local-name()
fallback re-tries it ignoring namespaces and the report says which of the
two matched — that distinction (namespace mismatch vs genuinely absent
node) localizes a whole class of client problems by itself.

Per-package testing = run this over every flow in the package.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from inspector.core import detect_format, inspect_payload


@dataclass
class StepFinding:
    step: str
    kind: str
    level: str          # PASS | FAIL | WARN | INFO | SKIP
    check: str
    detail: str


def _localname_xpath(path: str) -> str:
    """/a/b//c  →  /*[local-name()='a']/*[local-name()='b']//*[local-name()='c']
    Predicates and attributes are carried as-is where possible."""
    if not path or not path.strip().startswith(("/", "//")):
        return path
    out = []
    for seg in re.split(r"(/+)", path.strip()):
        if not seg or set(seg) == {"/"}:
            out.append(seg)
            continue
        m = re.match(r"([\w.\-]+|\*)(.*)$", seg)
        if not m:
            out.append(seg)
            continue
        name, rest = m.groups()
        if name == "*":
            out.append(seg)
        elif ":" in seg.split("[")[0]:
            name = seg.split("[")[0].split(":", 1)[1]
            rest = seg[len(seg.split("[")[0]):]
            out.append(f"*[local-name()='{name}']{rest}")
        else:
            out.append(f"*[local-name()='{name}']{rest}")
    return "".join(out)


def _xpath_count(root, path: str):
    """Returns (count, used_fallback) — tries the path verbatim, then the
    local-name() rewrite when nothing matched."""
    from lxml import etree
    try:
        hits = root.xpath(path)
        n = len(hits) if isinstance(hits, list) else 1
        if n:
            return n, False
    except etree.XPathError:
        pass
    try:
        hits = root.xpath(_localname_xpath(path))
        n = len(hits) if isinstance(hits, list) else 1
        return n, True
    except etree.XPathError:
        return -1, False


def _resolve_resource(name: str, files: dict):
    """Find a bundle resource by its configured path ('/xsd/Foo.xsd') or
    bare name, tolerant of the src/main/resources prefix."""
    if not name:
        return None
    tail = name.strip().lstrip("/")
    for k, v in files.items():
        if k.endswith("/" + tail) or k.endswith("/" + tail.split("/")[-1]) \
                or k == tail:
            return v if isinstance(v, str) else v.decode("utf-8", "replace")
    return None


def test_payload_against_flow(iflw_xml: str, payload: "str | bytes",
                              resources: dict | None = None,
                              apply_xslt: bool = False) -> list:
    """resources: {rel_path_or_name: content} — the flow's bundle files
    (scripts/xsd/xslt). Returns ordered StepFinding list."""
    from extractor.iflow_parser import parse_iflow
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", "replace")
    resources = resources or {}
    out: list = []
    model = parse_iflow(iflw_xml, "flowtest")
    fmt = detect_format(payload)

    xroot = None
    if fmt == "xml":
        from lxml import etree
        try:
            xroot = etree.fromstring(payload.encode("utf-8"))
        except etree.XMLSyntaxError as exc:
            out.append(StepFinding("(payload)", "xml", "FAIL",
                                   "well-formed XML", str(exc)))
            return out

    main = next((p for p in model.processes if p.is_main), None)
    order = [s for s in (model.sequence or [])
             if s in model.steps] or (main.step_ids if main else [])

    for sid in order:
        st = model.steps[sid]
        cfg = st.config or {}
        at = cfg.get("activityType", st.kind)

        if at == "XmlValidator":
            xsd_ref = cfg.get("xsd", "")
            xsd = _resolve_resource(xsd_ref, resources)
            if xroot is None:
                out.append(StepFinding(st.name, at, "WARN", "XSD validation",
                                       f"payload is {fmt}, not XML"))
            elif xsd is None:
                out.append(StepFinding(st.name, at, "WARN", "XSD validation",
                                       f"schema '{xsd_ref}' not in bundle"))
            else:
                rep = inspect_payload(payload, fmt="xml", schema=xsd,
                                      schema_kind="xsd")
                errs = [f for f in rep.findings
                        if f.check == "XSD validation" and f.level == "FAIL"]
                if not errs:
                    out.append(StepFinding(st.name, at, "PASS",
                                           "XSD validation",
                                           f"valid against {xsd_ref}"))
                for e in errs[:8]:
                    out.append(StepFinding(st.name, at, "FAIL",
                                           "XSD validation",
                                           f"{e.detail} @ {e.path}"))

        elif at == "Splitter" and cfg.get("exprType", "XPath") == "XPath":
            path = cfg.get("splitExprValue", "")
            if xroot is None or not path:
                continue
            n, fb = _xpath_count(xroot, path)
            note = " (matched only namespace-agnostically — check prefixes)" \
                if fb and n > 0 else ""
            lvl = "PASS" if n > 0 else "FAIL"
            out.append(StepFinding(
                st.name, at, lvl, "Splitter XPath",
                f"'{path}' → {max(n, 0)} split item(s){note}"
                + ("" if n > 0 else " — splitter emits NOTHING for this "
                                    "payload (downstream never runs)")))

        elif at == "Filter":
            path = cfg.get("wrapContent", "")
            if xroot is None or not path:
                continue
            n, fb = _xpath_count(xroot, path)
            note = " (namespace-agnostic match — check prefixes)" \
                if fb and n > 0 else ""
            lvl = "PASS" if n > 0 else "FAIL"
            out.append(StepFinding(
                st.name, at, lvl, "Filter XPath",
                f"'{path}' → {max(n, 0)} node(s){note}"
                + ("" if n > 0 else " — filter output is EMPTY")))

        elif at == "ExclusiveGateway":
            routes = [r for r in (model.routes or []) if r.gateway == sid]
            conds = [r for r in routes if r.condition]
            if xroot is None or not conds:
                continue
            fired = None
            for r in conds:
                cond = r.condition
                if cond and cond.strip().startswith("$"):
                    out.append(StepFinding(
                        st.name, at, "INFO", "Router condition",
                        f"'{cond}' uses headers/properties — not "
                        "payload-decidable locally"))
                    continue
                n, fb = _xpath_count(xroot, cond)
                if n > 0 and fired is None:
                    fired = r
                    note = " (namespace-agnostic)" if fb else ""
                    out.append(StepFinding(
                        st.name, at, "PASS", "Router condition",
                        f"route '{r.name}' fires: '{cond}'{note}"))
                elif n == 0:
                    out.append(StepFinding(
                        st.name, at, "INFO", "Router condition",
                        f"route '{r.name}' does not match: '{cond}'"))
            if fired is None and conds:
                out.append(StepFinding(
                    st.name, at, "WARN", "Router condition",
                    "no conditional route matches — DEFAULT route fires"))

        elif at == "XmlToCsvConverter":
            path = cfg.get("XPath_Field_Location", "")
            if xroot is None or not path:
                continue
            n, fb = _xpath_count(xroot, path)
            lvl = "PASS" if n > 0 else "FAIL"
            out.append(StepFinding(
                st.name, at, lvl, "XML→CSV source path",
                f"'{path}' → {max(n, 0)} row node(s)"
                + (" (namespace-agnostic)" if fb and n > 0 else "")
                + ("" if n > 0 else " — converter would emit an empty CSV")))

        elif at == "CsvToXmlConverter":
            schema_ref = cfg.get("XML_Schema_File_Path", "")
            ok = _resolve_resource(schema_ref, resources) is not None
            out.append(StepFinding(
                st.name, at, "PASS" if ok else "WARN", "CSV→XML schema",
                f"schema '{schema_ref}' "
                + ("present in bundle" if ok else "NOT found in bundle")))
            if fmt == "csv":
                sep = cfg.get("Field_Separator_in_CSV", ",")
                if not sep.startswith("{{"):
                    first = payload.splitlines()[0] if payload else ""
                    lvl = "PASS" if sep in first else "WARN"
                    out.append(StepFinding(
                        st.name, at, lvl, "CSV separator",
                        f"configured '{sep}' "
                        + ("found" if sep in first else "NOT found")
                        + " in first payload line"))

        elif at in ("EDItoXMLConverter", "XMLtoEDIConverter"):
            table = cfg.get("x12SchemaTable", "") + cfg.get(
                "ediSchemaTable", "")
            doc_ids = set(re.findall(r"ASC-X12_(\d{3})", table))
            if fmt == "edi" and doc_ids:
                m = re.search(r"(?:^|[~\n])ST([*+])(\d{3})", payload)
                st01 = m.group(2) if m else "?"
                lvl = "PASS" if st01 in doc_ids else "FAIL"
                out.append(StepFinding(
                    st.name, at, lvl, "EDI document type",
                    f"payload ST-01 = {st01}, configured schema(s): "
                    f"{sorted(doc_ids)}"))
            elif doc_ids:
                out.append(StepFinding(
                    st.name, at, "INFO", "EDI document type",
                    f"configured for X12 {sorted(doc_ids)}; payload is "
                    f"{fmt}"))

        elif at == "Mapping" and apply_xslt:
            res_ref = cfg.get("mappinguri", "") or cfg.get("mappingname", "")
            cand = None
            for k, v in resources.items():
                if k.endswith((".xsl", ".xslt")) and (
                        not res_ref or res_ref.split("/")[-1]
                        .split("?")[0] in k):
                    cand = (k, v if isinstance(v, str)
                            else v.decode("utf-8", "replace"))
                    break
            if cand and xroot is not None:
                from lxml import etree
                try:
                    xsl = etree.fromstring(cand[1].encode("utf-8"))
                    ver = xsl.get("version", "1.0")
                    if ver.startswith(("2", "3")):
                        out.append(StepFinding(
                            st.name, at, "SKIP", "XSLT apply",
                            f"{cand[0]} is XSLT {ver} — not locally "
                            "executable (lxml is 1.0); test on tenant"))
                    else:
                        res = etree.XSLT(xsl)(xroot)
                        out.append(StepFinding(
                            st.name, at, "PASS", "XSLT apply",
                            f"{cand[0]} transformed payload "
                            f"({len(str(res))} chars output)"))
                except Exception as exc:
                    out.append(StepFinding(st.name, at, "FAIL", "XSLT apply",
                                           f"{cand[0]}: {exc}"))

    if not out:
        out.append(StepFinding("(flow)", "-", "INFO", "coverage",
                               "no payload-dependent steps found in the "
                               "main process"))
    return out
