"""
extractor/discovery_hints.py

Answer the question "where could we re-discover this schema, instead of trusting
a local copy that may have been hand-edited?" by mining hints out of the schema
files themselves and the iFlow corpus.

What the data shows (and this module encodes):
  * EDMX  — the OData service name is recoverable (Schema Namespace). Re-fetch
            with  <HOST>/sap/opu/odata/sap/<SERVICE>/$metadata  (V2) against the
            real source system, using your credentials.
  * WSDL  — carries the real SOAP service PATH (e.g. /sap/bc/srt/scs_ext/sap/<svc>)
            but usually a placeholder host (host:port). Re-fetch with
            <HOST><path>?wsdl  (or via SOAMANAGER) on the actual system.
  * The SAP Gateway CATALOG service  /sap/opu/odata/IWFND/CATALOGSERVICE
            enumerates EVERY activated OData service on a system — the true
            live-discovery entry point. (V4: /sap/opu/odata4/iwfnd/config/...)
  * iFlows reference schemas as BUNDLED resources (edmx/…, /wsdl/…, /xsd/…), not
            live URLs — evidence that CPI snapshots schemas at design time rather
            than discovering at runtime. So: discover-once → version → bundle.

Output: a discovery manifest (CSV) of {identity, type, service_path, host_hint,
refetch_url_template, source}. Fill {HOST} with your system and feed OData rows
to the fetcher (which can now use your own credentials and base host).
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, asdict
from pathlib import Path
import xml.etree.ElementTree as ET

GATEWAY_CATALOG_V2 = "/sap/opu/odata/IWFND/CATALOGSERVICE;v=2/ServiceCollection"
GATEWAY_CATALOG_V4 = "/sap/opu/odata4/iwfnd/config/default/iwfnd/catalog/0002/ServiceGroups"

_PLACEHOLDER = ("host:port", "localhost", "example.org", "example.com",
                "www.example", "<host>", "yourhost")


def _ln(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


@dataclass
class Hint:
    identity: str            # service / namespace / portType
    type: str                # edmx | wsdl | catalog
    service_path: str        # path on the system (host-independent)
    host_hint: str           # a real host found, or "" if placeholder/none
    refetch_url_template: str  # {HOST} placeholder for your system
    source: str              # file it came from


def from_edmx(path: str) -> list[Hint]:
    raw = Path(path).read_bytes()
    try:
        root = ET.fromstring(raw)
    except Exception:
        return []
    ns = sorted({e.get("Namespace") for e in root.iter()
                 if _ln(e.tag) == "Schema" and e.get("Namespace")})
    svc = ns[0] if ns else Path(path).stem
    # service technical name sometimes embedded in the filename path segments
    m = re.search(r"odata[_/]sap[_/]([A-Za-z0-9_]+)", Path(path).name)
    tech = m.group(1) if m else svc
    spath = f"/sap/opu/odata/sap/{tech}"
    return [Hint(svc, "edmx", spath, "",
                 "{HOST}" + spath + "/$metadata", Path(path).name)]


def from_wsdl(path: str) -> list[Hint]:
    raw = Path(path).read_bytes()
    try:
        root = ET.fromstring(raw)
    except Exception:
        return []
    tns = root.get("targetNamespace", "")
    ptypes = sorted({e.get("name") for e in root.iter()
                     if _ln(e.tag) in ("portType", "service") and e.get("name")})
    ident = (ptypes[0] if ptypes else tns) or Path(path).stem
    out = []
    for e in root.iter():
        if _ln(e.tag) == "address" and e.get("location"):
            loc = e.get("location")
            low = loc.lower()
            is_placeholder = any(p in low for p in _PLACEHOLDER)
            # split host vs path
            m = re.match(r"(https?://[^/]+)(/.*)?$", loc)
            host = "" if (is_placeholder or not m) else m.group(1)
            spath = (m.group(2) if m and m.group(2) else loc)
            tmpl = "{HOST}" + spath + ("?wsdl" if "?" not in spath else "")
            out.append(Hint(ident, "wsdl", spath, host, tmpl, Path(path).name))
    if not out:  # no address element — still record identity for SOAMANAGER lookup
        out.append(Hint(ident, "wsdl", "", "", "{HOST}/<SOAMANAGER endpoint>?wsdl",
                        Path(path).name))
    return out


def from_iflow_corpus(pkl_path: str, limit: int = 0) -> dict:
    """Summarize how iFlows reference schemas — evidence for snapshot-not-live."""
    import pickle
    data = pickle.load(open(pkl_path, "rb"))
    prop = re.compile(r'<key>([^<]+)</key>\s*<value>([^<]*)</value>')
    bundled = re.compile(r'^/?(?:edmx|wsdl|xsd)/', re.I)
    counts = {"bundled_refs": 0, "externalized_endpoints": 0, "iflows": len(data)}
    examples = {"bundled": set(), "endpoint_params": set()}
    for _, xml in (data[:limit] if limit else data):
        for k, v in prop.findall(xml):
            kl = k.lower()
            if any(t in kl for t in ("edmxfilepath", "soapwsdlurl", "schemaresourceuri", "xml_schema_file_path", "xsd")):
                if bundled.match(v.strip()):
                    counts["bundled_refs"] += 1
                    if len(examples["bundled"]) < 6:
                        examples["bundled"].add(v.strip()[:60])
            if kl in ("address", "httpaddresswithoutquery") and ("{{" in v or "${" in v):
                counts["externalized_endpoints"] += 1
                if len(examples["endpoint_params"]) < 6:
                    examples["endpoint_params"].add(v.strip()[:50])
    counts["examples"] = {k: sorted(v) for k, v in examples.items()}
    return counts


def build_manifest(sources, out_csv: str, iflow_pkl: str = "") -> dict:
    hints: list[Hint] = []
    for src in sources:
        for p in Path(src).rglob("*"):
            if not p.is_file():
                continue
            try:
                head = p.read_bytes()[:512].lstrip()
            except Exception:
                continue
            if b"<edmx:Edmx" in head or b":Edmx" in head:
                hints += from_edmx(str(p))
            elif b":definitions" in head or b"<definitions" in head:
                hints += from_wsdl(str(p))
    # dedupe identical rows
    seen = set(); rows = []
    for h in hints:
        key = (h.identity, h.type, h.service_path, h.refetch_url_template)
        if key in seen:
            continue
        seen.add(key); rows.append(h)
    # always include the Gateway catalog entry-points as top discovery sources
    rows.insert(0, Hint("ALL OData services (enumerate)", "catalog",
                        GATEWAY_CATALOG_V2, "", "{HOST}" + GATEWAY_CATALOG_V2,
                        "SAP Gateway catalog (V2)"))
    rows.insert(1, Hint("ALL OData services V4 (enumerate)", "catalog",
                        GATEWAY_CATALOG_V4, "", "{HOST}" + GATEWAY_CATALOG_V4,
                        "SAP Gateway catalog (V4)"))
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[f.name for f in Hint.__dataclass_fields__.values()])
        w.writeheader()
        for h in rows:
            w.writerow(asdict(h))
    summary = {"hints": len(rows),
               "edmx": sum(1 for h in rows if h.type == "edmx"),
               "wsdl": sum(1 for h in rows if h.type == "wsdl"),
               "wsdl_with_real_host": sum(1 for h in rows if h.type == "wsdl" and h.host_hint)}
    if iflow_pkl and Path(iflow_pkl).exists():
        summary["iflow_corpus"] = from_iflow_corpus(iflow_pkl)
    return summary


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser(description="Extract schema re-discovery hints → manifest CSV.")
    ap.add_argument("sources", nargs="+")
    ap.add_argument("--out", default="discovery_manifest.csv")
    ap.add_argument("--iflow-pkl", default="")
    args = ap.parse_args()
    print(json.dumps(build_manifest(args.sources, args.out, args.iflow_pkl), indent=2))
    print("wrote", args.out)
