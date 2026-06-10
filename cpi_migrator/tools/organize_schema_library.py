"""
tools/organize_schema_library.py

Turn a messy pile of schema files (your Resources collection + anything the
$metadata fetcher pulled) into a clean, de-duplicated, typed library:

    out/
      edmx/      canonical OData metadata  (richest per service)
      wsdl/      canonical SOAP/IDoc WSDL  (richest per portType)
      xsd/       canonical XSD             (richest per namespace/root)
      openapi/   generated from each canonical EDMX (if odata-openapi present)
      _library_report.md
      suggested_fetch_targets.csv   (OData services worth fetching canonical)

What is and isn't automatic (honest):
  * EDMX + the OpenAPI generated from it: fully automatic here.
  * WSDL/XSD: there is no key-based bulk download, so "automatic" means
    de-duplicating the ones you already have into a canonical set — not
    fetching fresh ones from SAP.

Type is decided by SNIFFING the file (root element / namespace / JSON keys),
not by extension, so a WSDL or EDMX saved as `.xml` still lands in the right
folder. Dedup uses extractor.schema_deduper (exact → structural → family,
keeping the richest member of each family).
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from extractor.schema_deduper import fingerprint, canonical  # noqa: E402


def _ln(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def classify(path: Path) -> str:
    """Return edmx | wsdl | xsd | openapi | unknown by sniffing content."""
    try:
        raw = path.read_bytes()
    except Exception:
        return "unknown"
    head = raw[:4096].lstrip()
    if head[:1] in (b"{", b"["):
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict) and (obj.get("openapi") or obj.get("swagger")):
                return "openapi"
        except Exception:
            return "unknown"
        return "unknown"
    # YAML OpenAPI
    if head[:8].lower().startswith(b"openapi:") or head[:8].lower().startswith(b"swagger:"):
        return "openapi"
    try:
        root = ET.fromstring(raw)
    except Exception:
        return "unknown"
    name = _ln(root.tag).lower()
    if name == "edmx":
        return "edmx"
    if name == "definitions":
        return "wsdl"
    if name == "schema":
        return "xsd"
    # nested: some WSDLs wrap; some EDMX have odd roots
    tags = {_ln(e.tag).lower() for e in list(root)[:6]}
    if "dataservices" in tags:
        return "edmx"
    return "unknown"


def _clean_name(fp, kind: str) -> str:
    """Stable filename from the family identity, not the messy original."""
    fam = fp.family.split(":", 1)[1] if ":" in fp.family else fp.family
    fam = fam.split(",")[0].split("::")[-1]  # first/portType segment
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in fam)[:80].strip("_")
    return safe or Path(fp.path).stem


def organize(sources, out_dir: str, generate_openapi: bool = True,
             clean: bool = False) -> dict:
    out = Path(out_dir)
    if clean:
        for sub in ("edmx", "wsdl", "xsd", "openapi"):
            if (out / sub).exists():
                shutil.rmtree(out / sub)
    for sub in ("edmx", "wsdl", "xsd", "openapi"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    # 1) classify everything
    buckets: dict[str, list[Path]] = {"edmx": [], "wsdl": [], "xsd": [], "openapi": []}
    for src in sources:
        for p in Path(src).rglob("*"):
            if not p.is_file():
                continue
            k = classify(p)
            if k in buckets:
                buckets[k].append(p)

    summary = {"scanned": sum(len(v) for v in buckets.values()), "kept": {}, "families": {}}

    # 2) dedup XML types by family, keep richest → copy into typed folder
    canon_edmx: list[Path] = []
    for kind in ("edmx", "wsdl", "xsd"):
        fams: dict[str, list] = {}
        for p in buckets[kind]:
            fp = fingerprint(str(p), kind=kind)
            fams.setdefault(fp.family, []).append(fp)
        summary["families"][kind] = len(fams)
        kept = 0
        for fam, members in fams.items():
            best = canonical(members)
            src_hash = hashlib.sha1(Path(best.path).read_bytes()).hexdigest()
            dest = out / kind / f"{_clean_name(best, kind)}.{kind}"
            i = 2
            skip = False
            while dest.exists():
                # identical bytes already present (e.g. a prior run) → don't duplicate
                if hashlib.sha1(dest.read_bytes()).hexdigest() == src_hash:
                    skip = True
                    break
                dest = out / kind / f"{_clean_name(best, kind)}_{i}.{kind}"
                i += 1
            if skip:
                if kind == "edmx":
                    canon_edmx.append(dest)   # point at the existing identical copy
                continue
            shutil.copyfile(best.path, dest)
            kept += 1
            if kind == "edmx":
                canon_edmx.append(dest)
        summary["kept"][kind] = kept

    # 3) OpenAPI: keep existing + generate from each canonical EDMX
    seen_openapi = set()
    for p in buckets["openapi"]:
        dest = out / "openapi" / p.name
        shutil.copyfile(p, dest)
        seen_openapi.add(dest.stem)
    gen = 0
    if generate_openapi and canon_edmx and _has_odata_openapi():
        for e in canon_edmx:
            if _edmx_to_openapi(e, out / "openapi"):
                gen += 1
    summary["kept"]["openapi"] = len(list((out / "openapi").glob("*")))
    summary["openapi_generated"] = gen
    summary["openapi_tool_available"] = _has_odata_openapi()

    # 4) suggested fetch targets (OData service identities found)
    _write_fetch_targets(canon_edmx, out / "suggested_fetch_targets.csv")
    _write_report(summary, out / "_library_report.md")
    return summary


def _has_odata_openapi() -> bool:
    try:
        subprocess.run(["npx", "--version"], capture_output=True, timeout=20)
        return True
    except Exception:
        return False


def _edmx_to_openapi(edmx: Path, out_dir: Path) -> bool:
    """Generate <name>.openapi3.json next to the EDMX, move into out_dir."""
    try:
        subprocess.run(
            ["npx", "-y", "-p", "odata-openapi", "odata-openapi3", str(edmx)],
            capture_output=True, timeout=300, cwd=str(edmx.parent))
        produced = edmx.with_suffix(".openapi3.json")
        if produced.exists():
            target = out_dir / f"{edmx.stem}.openapi3.json"
            shutil.move(str(produced), str(target))
            return True
    except Exception:
        pass
    return False


def _write_fetch_targets(edmx_files, csv_path: Path):
    rows = []
    for e in edmx_files:
        try:
            root = ET.fromstring(e.read_bytes())
            ns = sorted({el.get("Namespace") for el in root.iter()
                         if _ln(el.tag) == "Schema" and el.get("Namespace")})
            svc = ns[0] if ns else e.stem
        except Exception:
            svc = e.stem
        rows.append({"name": svc, "product": "s4hanacloud", "service": svc, "url": ""})
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["name", "product", "service", "url"])
        w.writeheader()
        w.writerows(rows)


def _write_report(summary: dict, path: Path):
    L = ["# Schema library report", "",
         f"Scanned **{summary['scanned']}** files.", "", "## Canonical kept per type", ""]
    for k in ("edmx", "wsdl", "xsd", "openapi"):
        fams = summary["families"].get(k)
        kept = summary["kept"].get(k, 0)
        extra = f" (from {fams} families)" if fams else ""
        L.append(f"- **{k}**: {kept}{extra}")
    if summary.get("openapi_tool_available"):
        L.append(f"- generated **{summary.get('openapi_generated', 0)}** OpenAPI specs from EDMX")
    else:
        L.append("- OpenAPI generation skipped (npx / odata-openapi not available)")
    L += ["", "See `suggested_fetch_targets.csv` for OData services you can pull "
          "canonical copies of via the $metadata fetcher."]
    path.write_text("\n".join(L))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Organize schemas into a deduped, typed library.")
    ap.add_argument("sources", nargs="+", help="one or more source dirs to scan")
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-openapi", action="store_true", help="skip EDMX→OpenAPI generation")
    ap.add_argument("--clean", action="store_true",
                    help="wipe edmx/wsdl/xsd/openapi in --out before building (fresh rebuild)")
    args = ap.parse_args()
    s = organize(args.sources, args.out, generate_openapi=not args.no_openapi,
                 clean=args.clean)
    print(json.dumps(s, indent=2))
