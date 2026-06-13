#!/usr/bin/env python3
"""tools/payload_inspect.py — CLI for the payload inspector.

  # analyze + redact (safe-to-share copy written next to the input)
  python tools/payload_inspect.py customer_order.xml

  # validate against a schema
  python tools/payload_inspect.py order.xml --schema OrderRequest.xsd
  python tools/payload_inspect.py resp.json --schema api.schema.json

  # test against a specific iFlow bundle (zip or extracted dir)
  python tools/payload_inspect.py order.xml --flow MyFlow_bundle.zip

  # test against every iFlow in a package export
  python tools/payload_inspect.py order.xml --package SAP_Pkg.zip

  # keep specific routing-relevant values unredacted
  python tools/payload_inspect.py order.xml --keep EU --keep US
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inspector.core import inspect_payload                   # noqa: E402
from inspector.flow_test import test_payload_against_flow    # noqa: E402


def _bundles_from(path: Path):
    """Yield (name, iflw_xml, resources) from a bundle zip, package export,
    or extracted directory."""
    if path.is_dir():
        iflws = list(path.rglob("*.iflw"))
        for f in iflws:
            res = {str(p.relative_to(path)): p.read_bytes()
                   for p in path.rglob("*") if p.is_file() and p != f}
            yield f.stem, f.read_text("utf-8", "replace"), res
        return
    z = zipfile.ZipFile(path)
    names = z.namelist()
    if any(n.endswith(".iflw") for n in names):       # single bundle zip
        f = next(n for n in names if n.endswith(".iflw"))
        res = {n: z.read(n) for n in names if n != f}
        yield Path(f).stem, z.read(f).decode("utf-8", "replace"), res
        return
    for n in names:                                   # package export
        if not n.endswith("_content"):
            continue
        raw = z.read(n)
        if raw[:2] != b"PK":
            continue
        zb = zipfile.ZipFile(io.BytesIO(raw))
        bn = zb.namelist()
        for f in bn:
            if f.endswith(".iflw"):
                res = {m: zb.read(m) for m in bn if m != f}
                yield Path(f).stem, \
                    zb.read(f).decode("utf-8", "replace"), res


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("payload")
    ap.add_argument("--schema", help="XSD or JSON Schema file")
    ap.add_argument("--flow", help="iFlow bundle zip / extracted dir")
    ap.add_argument("--package", help="CPI package export zip")
    ap.add_argument("--keep", action="append", default=[],
                    help="value to leave unredacted (repeatable)")
    ap.add_argument("--xslt", action="store_true",
                    help="also apply XSLT 1.0 mappings locally")
    ap.add_argument("--no-redacted-file", action="store_true")
    args = ap.parse_args()

    data = Path(args.payload).read_text("utf-8", "replace")
    schema = Path(args.schema).read_text("utf-8", "replace") \
        if args.schema else None
    kind = None
    if args.schema:
        kind = "xsd" if args.schema.lower().endswith(".xsd") else "json"

    rep = inspect_payload(data, schema=schema, schema_kind=kind,
                          keep_values=args.keep)
    print(f"format: {rep.fmt}   parsed: {rep.parse_ok}"
          + (f"   ({rep.parse_error})" if rep.parse_error else ""))
    print(json.dumps(rep.profile, indent=1, ensure_ascii=False)[:3000])
    for f in rep.findings:
        print(f"  [{f.level}] {f.check}: {f.detail}"
              + (f"  @ {f.path}" if f.path else ""))

    if rep.redacted and not args.no_redacted_file:
        outp = Path(args.payload).with_suffix(
            ".redacted" + Path(args.payload).suffix)
        outp.write_text(rep.redacted, "utf-8")
        print(f"\nsafe-to-share copy: {outp}")

    src = args.flow or args.package
    if src:
        for name, iflw, res in _bundles_from(Path(src)):
            print(f"\n── flow: {name} ──")
            for f in test_payload_against_flow(iflw, data, res,
                                               apply_xslt=args.xslt):
                print(f"  [{f.level:5}] {f.kind:20} {f.step[:30]:30} "
                      f"{f.check}: {f.detail}")


if __name__ == "__main__":
    main()
