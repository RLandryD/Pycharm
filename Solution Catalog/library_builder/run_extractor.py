"""Run the solution-catalog extractor over harvested artifacts.

Usage:
    cd ~/PycharmProjects/cpi_migrator
    source venv/bin/activate
    python -m library_builder.run_extractor <input_dir_or_zip> [output_dir]

<input> may be a directory tree, a single .zip, or a folder full of the
harvested package zips — it recurses into zips (including nested _content
artifact bundles) automatically.

Output (in <output_dir>, default ./library_out):
    catalog_groovy.json, catalog_mmap.json, catalog_xsd.json, …  (per type,
        grouped by provisional category, conservative dedup applied)
    registry_imports.json / registry_services.json / registry_idioms.json
    rejected.log    (what was skipped and why — spot-check this)
    summary.json    (counts: seen / distinct / collapsed / rejected)
"""
import sys
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

from library_builder.extractor import LibraryBuilder


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    inp = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "./library_out"

    b = LibraryBuilder()
    root = Path(inp)
    if root.is_dir():
        # ingest every file and zip under the directory
        for p in sorted(root.rglob("*")):
            if p.is_file():
                b._maybe_read(p)
    else:
        b.ingest_path(inp)

    summary = b.write_catalog(out)
    print("\n===== EXTRACTION COMPLETE =====")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print(f"\nCatalog written to: {out}/")
    print("Review rejected.log to spot-check what was skipped.")


if __name__ == "__main__":
    main()
