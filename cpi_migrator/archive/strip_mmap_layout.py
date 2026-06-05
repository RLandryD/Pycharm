#!/usr/bin/env python3
"""strip_mmap_layout.py  —  TEST 1: does the tenant tolerate a mapping with
no visual layout?

WHAT IT DOES
  Removes ONLY the visual layout coordinates from every .mmap inside a bundle:
    * <viewData x=".." y=".."/>   elements   (the canvas positions)
  It leaves ALL mapping LOGIC untouched:
    * <brick .../>  (field paths, types, functions)
    * <lnkRole>/<lnk> schema bindings (source/target xsd/wsdl + root element)
    * contexts, constants, function names
  As a belt-and-suspenders option you can also zero the x/y instead of deleting
  them (set MODE = "zero").

WHY
  If the tenant imports this stripped bundle AND the mapping still opens/works
  (the tenant recomputing positions), then mappings can be GENERATED from a
  logical field-pair spec without computing pixel layout. That is the gate for
  the mmap authoring engine.

USAGE
  python3 strip_mmap_layout.py  input_bundle.zip  output_stripped.zip
  (then import output_stripped.zip to the tenant and report what happens)

It recurses into nested *_content zips, handles flat bundles, and prints a
report of exactly what it changed so you can eyeball it before deploying.
"""
import io
import re
import sys
import zipfile

# "delete" removes the <viewData .../> elements entirely.
# "zero"   keeps them but sets x="0" y="0" (use this if delete fails to import).
MODE = "delete"

_VIEWDATA_RE = re.compile(r'<viewData\b[^>]*/>')
_XY_RE = re.compile(r'(\b[xy])="[^"]*"')


def strip_layout(mmap_text: str) -> tuple[str, int]:
    """Return (stripped_text, num_viewData_affected)."""
    count = len(_VIEWDATA_RE.findall(mmap_text))
    if MODE == "zero":
        def _z(m):
            return _XY_RE.sub(lambda mm: f'{mm.group(1)}="0"', m.group(0))
        out = _VIEWDATA_RE.sub(_z, mmap_text)
    else:  # delete
        out = _VIEWDATA_RE.sub("", mmap_text)
    return out, count


def process_zip_bytes(data: bytes, report: list, depth: int = 0) -> bytes:
    """Recursively rewrite .mmap files inside a (possibly nested) zip."""
    zin = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    zout = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
    for n in zin.namelist():
        if n.endswith("/"):
            continue
        d = zin.read(n)
        if d[:2] == b"PK" and (n.endswith(".zip") or n.endswith("_content")):
            d = process_zip_bytes(d, report, depth + 1)
        elif n.endswith(".mmap"):
            text = d.decode("utf-8", "replace")
            stripped, count = strip_layout(text)
            d = stripped.encode("utf-8")
            report.append((n.split("/")[-1], count,
                           len(text), len(stripped)))
        zout.writestr(n, d)
    zout.close()
    return buf.getvalue()


def main():
    if len(sys.argv) != 3:
        print("usage: python3 strip_mmap_layout.py <input.zip> <output.zip>")
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    report = []
    out = process_zip_bytes(open(src, "rb").read(), report)
    open(dst, "wb").write(out)

    print(f"MODE = {MODE}")
    if not report:
        print("WARNING: no .mmap files found in this bundle — nothing stripped.")
    else:
        print(f"Stripped layout from {len(report)} mapping file(s):")
        for name, count, before, after in report:
            print(f"  {name[:50]:50} viewData removed={count:4}  "
                  f"{before}b -> {after}b")
    print(f"\nWrote: {dst}")
    print("Next: import this bundle to the tenant and report:")
    print("  1) does it import?")
    print("  2) does the mapping open/render in the editor?")
    print("  3) if you re-export, did the tenant put <viewData> back?")


if __name__ == "__main__":
    raise SystemExit(main())
