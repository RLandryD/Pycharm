"""
tools/audit_schema_library.py  —  READ-ONLY build integrity audit.

Scans the SAME sources the organizer scans and reports what the build actually
does, so you can trust the canonical library instead of assuming it:

  1. SCAN TALLY     per-kind counts, plus files that were UNPARSEABLE or
                    UNCLASSIFIED (silently dropped from the library).
  2. DEDUP SAFETY   for every family with >1 member, is the collapse:
                       EXACT      all members byte-identical        (lossless)
                       STRUCT     same structure, bytes differ      (lossless)
                       DIVERGENT  members differ structurally  -> the organizer
                                  keeps the richest and DISCARDS the rest, so
                                  these are potential lost schemas to review.
  3. RECONCILE      families per kind == files the organizer should write; if
                    --out is given, compare against what's actually on disk.

Nothing is modified. Run it before and after a rebuild to see the difference.

    python3 tools/audit_schema_library.py SRC [SRC ...] [--out CANON_LIB] [--full]
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from extractor.schema_deduper import fingerprint                  # noqa: E402
from tools.organize_schema_library import classify               # noqa: E402


def audit(sources, out_dir: str = "", full: bool = False) -> dict:
    buckets = defaultdict(list)       # kind -> [Path]
    unclassified, openapi = [], []
    for src in sources:
        for p in Path(src).rglob("*"):
            if not p.is_file():
                continue
            k = classify(p)
            if k in ("edmx", "wsdl", "xsd"):
                buckets[k].append(p)
            elif k == "openapi":
                openapi.append(p)
            else:
                unclassified.append(p)

    report = {"scanned": sum(len(v) for v in buckets.values()) + len(openapi) + len(unclassified),
              "by_kind": {}, "unparseable": [], "unclassified": [str(p) for p in unclassified],
              "openapi": len(openapi), "families": {}, "divergent": {}}

    for kind in ("edmx", "wsdl", "xsd"):
        fams = defaultdict(list)
        for p in buckets[kind]:
            fp = fingerprint(str(p), kind=kind)
            if not fp.parseable:
                report["unparseable"].append(str(p))
                continue
            fams[fp.family].append(fp)
        report["by_kind"][kind] = len(buckets[kind])
        report["families"][kind] = len(fams)
        # classify each multi-member family
        div = []
        for fam, members in fams.items():
            if len(members) == 1:
                continue
            if len({m.exact for m in members}) == 1:
                continue                              # EXACT — lossless
            if len({m.struct for m in members}) == 1:
                continue                              # STRUCT — lossless
            # DIVERGENT: members differ in structure -> organizer discards all but richest
            members_sorted = sorted(members, key=lambda m: -m.richness)
            div.append({
                "family": fam,
                "kept": Path(members_sorted[0].path).name,
                "kept_richness": members_sorted[0].richness,
                "discarded": [{"file": Path(m.path).name, "richness": m.richness}
                              for m in members_sorted[1:]],
            })
        report["divergent"][kind] = div

    if out_dir:
        out = Path(out_dir)
        report["on_disk"] = {}
        for kind in ("edmx", "wsdl", "xsd"):
            n = len(list((out / kind).glob(f"*.{kind}"))) if (out / kind).exists() else 0
            report["on_disk"][kind] = n
    return report


def _print(r: dict, full: bool):
    print("=" * 72)
    print(f"SCANNED {r['scanned']} files")
    print(f"  classified: {r['by_kind']}   openapi: {r['openapi']}")
    print(f"  UNPARSEABLE (dropped): {len(r['unparseable'])}")
    print(f"  UNCLASSIFIED (dropped): {len(r['unclassified'])}")
    print("-" * 72)
    for kind in ("edmx", "wsdl", "xsd"):
        n_in = r["by_kind"].get(kind, 0)
        n_fam = r["families"].get(kind, 0)
        n_div = len(r["divergent"].get(kind, []))
        disk = r.get("on_disk", {}).get(kind)
        disk_s = f"   on-disk: {disk}" + ("  ⚠ != families" if disk is not None and disk != n_fam else "  ✓") if disk is not None else ""
        print(f"{kind.upper():5} {n_in:>5} files -> {n_fam:>5} families "
              f"(collapsed {n_in - n_fam}); DIVERGENT families: {n_div}{disk_s}")
    # the dangerous part: divergent collapses (potential lost schemas)
    total_div = sum(len(r["divergent"][k]) for k in r["divergent"])
    if total_div:
        print("-" * 72)
        print(f"⚠ {total_div} DIVERGENT families — distinct schemas were collapsed; "
              f"the organizer kept the richest and discarded the rest:")
        for kind in ("edmx", "wsdl", "xsd"):
            for d in r["divergent"][kind][: (None if full else 10)]:
                disc = ", ".join(f"{x['file']}({x['richness']})" for x in d["discarded"])
                print(f"  [{kind}] {d['family'][:50]}")
                print(f"        kept {d['kept']}({d['kept_richness']})  DISCARDED: {disc}")
        if not full and total_div > 10:
            print("  …(run with --full to list all)")
    else:
        print("-" * 72)
        print("✓ No divergent collapses — every merge was byte- or structure-identical (lossless).")
    if r["unparseable"] and full:
        print("\nUNPARSEABLE files:")
        for p in r["unparseable"]:
            print("  ", p)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Read-only audit of the schema library build.")
    ap.add_argument("sources", nargs="+")
    ap.add_argument("--out", default="", help="canonical library dir to compare on-disk counts")
    ap.add_argument("--full", action="store_true", help="list all divergent families + unparseable files")
    a = ap.parse_args()
    _print(audit(a.sources, a.out, a.full), a.full)
