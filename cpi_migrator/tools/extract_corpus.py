#!/usr/bin/env python3
"""extract_corpus.py — standalone re-extractor: CPI package exports -> an
organized, browsable corpus tree.

Walks every package export under --src (default:
/home/landry/PycharmProjects/Resources/Packages), descending nested zip
containers and *_content bundle JARs (detected by PK magic bytes, not just
extension), and writes each relevant file into --dst (default:
/home/landry/PycharmProjects/Resources/Initial_Corpus) organized by type:

    Initial_Corpus/
        Groovy/   Xsl/   Xslt/   Mmap/   Xsd/   Wsdl/   Edmx/
        Json/     Xml/   Prop/   Propdef/  Iflw/   Other/
        manifest.csv

Output filenames are prefixed with the owning PACKAGE (the innermost .zip in
the container chain) so same-named files from different packages never
collide; residual collisions get a __2 / __3 suffix. manifest.csv maps every
extracted file back to its exact container-qualified source path
(outer.zip/inner.zip/hash_content::src/...), so anything can be traced to the
byte-level origin.

Usage:
    python3 tools/extract_corpus.py                  # defaults, additive
    python3 tools/extract_corpus.py --clean          # wipe dst, re-extract all
    python3 tools/extract_corpus.py --all            # every file type
    python3 tools/extract_corpus.py --ext groovy xsl # only these extensions
    python3 tools/extract_corpus.py --src P --dst C  # custom paths

Pure stdlib; safe to copy anywhere and run on its own.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import shutil
import sys
import zipfile

DEFAULT_SRC = "/home/landry/PycharmProjects/Resources/Packages"
DEFAULT_DST = "/home/landry/PycharmProjects/Resources/Initial_Corpus"

# current + foreseeable needs (schema work, mappings, scripts, payload
# analysis, parameter feeding). --all overrides; --ext narrows.
DEFAULT_EXTS = {".xsd", ".xsl", ".xslt", ".groovy", ".gsh", ".js", ".edmx",
                ".wsdl", ".mmap", ".opmap", ".json", ".xml", ".prop",
                ".propdef", ".iflw", ".project", ".mf"}

# extension -> Title-cased output folder (everything else -> Other/)
_FOLDER = {".groovy": "Groovy", ".gsh": "Groovy", ".js": "Javascript",
           ".xsl": "Xsl", ".xslt": "Xslt", ".mmap": "Mmap", ".opmap": "Mmap",
           ".xsd": "Xsd", ".wsdl": "Wsdl", ".edmx": "Edmx", ".json": "Json",
           ".xml": "Xml", ".prop": "Prop", ".propdef": "Propdef",
           ".iflw": "Iflw", ".project": "Project", ".mf": "Manifest"}

_MAX_DEPTH = 8


def _ext(name: str) -> str:
    base = name.rsplit("/", 1)[-1]
    return ("." + base.rsplit(".", 1)[-1].lower()) if "." in base else ""


def _sanitize(s: str) -> str:
    s = re.sub(r"\.zip$", "", s, flags=re.I)
    return re.sub(r"[^A-Za-z0-9._ -]", "_", s).strip() or "pkg"


def _package_of(chain: list) -> str:
    """Owning package = the INNERMOST .zip in the container chain (the package
    export itself, not an outer collection zip like part2.zip)."""
    zips = [c for c in chain if c.lower().endswith(".zip")]
    return _sanitize(zips[-1].rsplit("/", 1)[-1]) if zips else \
        (_sanitize(chain[0]) if chain else "pkg")


class Extractor:
    def __init__(self, dst: str, exts: set | None):
        self.dst = dst
        self.exts = exts                       # None => take everything
        self.manifest = []                     # (out_rel, source_path)
        self.counts = {}                       # folder -> n
        self.collisions = 0
        self._used = set()

    def want(self, name: str) -> bool:
        return self.exts is None or _ext(name) in self.exts

    def emit(self, chain: list, inner: str, raw: bytes):
        ext = _ext(inner)
        folder = _FOLDER.get(ext, "Other")
        pkg = _package_of(chain)
        base = inner.rsplit("/", 1)[-1]
        out_rel = f"{folder}/{pkg}__{base}"
        if out_rel in self._used:              # collision-safe suffixing
            stem, dot, tail = base.rpartition(".")
            n = 2
            while True:
                cand = (f"{folder}/{pkg}__{stem}__{n}.{tail}" if dot
                        else f"{folder}/{pkg}__{base}__{n}")
                if cand not in self._used:
                    out_rel = cand
                    self.collisions += 1
                    break
                n += 1
        self._used.add(out_rel)
        path = os.path.join(self.dst, out_rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:           # bytes: mmap/json may be
            fh.write(raw)                      # non-utf8 — never re-encode
        src_path = "/".join(chain)
        sep = "::" if (chain and chain[-1].endswith("_content")) else "/"
        self.manifest.append((out_rel, f"{src_path}{sep}{inner}"
                              if chain else inner))
        self.counts[folder] = self.counts.get(folder, 0) + 1

    def walk_zip(self, zf: zipfile.ZipFile, chain: list, depth: int = 0):
        if depth > _MAX_DEPTH:
            return
        for n in zf.namelist():
            if n.endswith("/"):
                continue
            try:
                raw = zf.read(n)
            except Exception:
                continue
            # nested container? (PK magic — extension lies sometimes)
            if raw[:2] == b"PK" and (n.lower().endswith(".zip")
                                     or n.endswith("_content")
                                     or n.lower().endswith(".jar")):
                try:
                    self.walk_zip(zipfile.ZipFile(io.BytesIO(raw)),
                                  chain + [n], depth + 1)
                    continue
                except Exception:
                    pass                       # fall through: keep as a leaf
            if self.want(n):
                self.emit(chain, n, raw)

    def walk(self, src: str):
        if os.path.isdir(src):
            for root, _d, files in os.walk(src):
                for f in sorted(files):
                    fp = os.path.join(root, f)
                    rel = os.path.relpath(fp, src)
                    try:
                        if zipfile.is_zipfile(fp):
                            with zipfile.ZipFile(fp) as zf:
                                self.walk_zip(zf, [rel])
                        elif self.want(f):
                            with open(fp, "rb") as fh:
                                self.emit([], rel, fh.read())
                    except Exception as exc:   # graceful: log gap, continue
                        print(f"  ! skipped {rel}: {exc}", file=sys.stderr)
        elif zipfile.is_zipfile(src):
            with zipfile.ZipFile(src) as zf:
                self.walk_zip(zf, [os.path.basename(src)])
        else:
            raise SystemExit(f"--src not found or not a dir/zip: {src}")

    def write_manifest(self):
        path = os.path.join(self.dst, "manifest.csv")
        os.makedirs(self.dst, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["extracted_file", "source_path"])
            w.writerows(sorted(self.manifest))
        return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Re-extract CPI package exports into an organized corpus")
    ap.add_argument("--src", default=DEFAULT_SRC,
                    help=f"packages dir or a single zip (default {DEFAULT_SRC})")
    ap.add_argument("--dst", default=DEFAULT_DST,
                    help=f"output corpus dir (default {DEFAULT_DST})")
    ap.add_argument("--clean", action="store_true",
                    help="wipe --dst first (full re-extract)")
    ap.add_argument("--all", action="store_true",
                    help="extract every file type, not just the default set")
    ap.add_argument("--ext", nargs="*", default=None, metavar="EXT",
                    help="only these extensions (e.g. --ext groovy xsl xsd)")
    args = ap.parse_args(argv)

    exts = None if args.all else (
        {"." + e.lstrip(".").lower() for e in args.ext} if args.ext
        else set(DEFAULT_EXTS))

    if args.clean and os.path.isdir(args.dst):
        shutil.rmtree(args.dst)
        print(f"cleaned {args.dst}")

    ex = Extractor(args.dst, exts)
    ex.walk(args.src)
    mpath = ex.write_manifest()

    total = sum(ex.counts.values())
    print(f"\nextracted {total} files -> {args.dst}")
    for folder in sorted(ex.counts):
        print(f"  {folder:11} {ex.counts[folder]:5}")
    if ex.collisions:
        print(f"  ({ex.collisions} same-named files kept via __N suffixes)")
    print(f"manifest: {mpath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
