#!/usr/bin/env python3
"""verify_extraction.py — confirm a flat, by-extension file extraction is
*faithful* to the source package archives and *consistent* with the catalogs
the workbench builds from them.

This version is memory-safe (streams + chunked hashing, depth/size caps) and
crash-diagnostic: it writes the report incrementally and traps every failure,
so if something goes wrong you get a clear message + partial report instead of
a console that just closes.

Checks
------
  [1] SOURCE RECONCILIATION (authoritative) — re-derive the file set from the
      source archives (raw bytes, by content hash) and diff vs your extraction.
      MISSED = a source file's content not in your extraction (a true gap).
  [2] CATALOG PARITY — run the project's own walk_corpus (the exact logic that
      feeds the catalogs). The catalog is a capped, text-only subset, so
      extraction-extras are EXPECTED; the only flag is catalog files your
      extraction is missing. Heavy, so it's guarded and skippable.
  [3] iFLOW REFERENCE COMPLETENESS — every resource an .iflw points at must be
      present, or resource lookup fails later.
  [4] INTEGRITY + ROUTING — zero-byte, content-vs-extension, bucket routing.

Usage
-----
    python3 verify_extraction.py \
        --extracted /home/landry/PycharmProjects/Resources/corpus \
        --source    /home/landry/PycharmProjects/Resources/Initial_Corpus \
        --report    verify_report.txt

    # if it still struggles on a huge source, add:
        --skip-catalog          # skip the heavy [2] step
        --max-file-mb 25        # skip leaf files bigger than this (default 50)
"""
from __future__ import annotations
import argparse
import faulthandler
import hashlib
import io
import os
import sys
import traceback
import zipfile
from collections import Counter, defaultdict

faulthandler.enable()

_RES_EXT = ("xsd", "xsl", "xslt", "groovy", "js", "wsdl", "mmap", "jar", "json",
            "edmx", "prop", "propdef", "iflw", "mf", "xml")
_XMLISH = {"xsd", "xsl", "xslt", "wsdl", "mmap", "iflw", "mf", "xml", "edmx",
           "project"}
_TEXTISH = {"groovy", "js", "json", "prop", "propdef", "txt", "properties"}
_MAX_DEPTH = 8
_CHUNK = 1 << 16

# Patterns covering every way an .iflw references an external file (regex import
# is local so the module still loads if re were ever shadowed).
import re
_REF_RE = re.compile(r'<value>\s*(dir://[^<]+|[^<]*\.(?:xsd|xsl|xslt|groovy|js|'
                     r'wsdl|mmap|jar))\s*</value>')


# ── hashing (chunked, never holds a whole large file) ────────────────────────
def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha_file(path: str) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as fh:
        while True:
            b = fh.read(_CHUNK)
            if not b:
                break
            n += len(b)
            h.update(b)
    return h.hexdigest(), n


# ── source walk: iterative (explicit stack), depth + size capped, streaming ──
def _walk_source(path: str, max_bytes: int):
    """Yield (internal_path, sha, size) for every leaf file, descending nested
    zips by PK magic. Iterative + capped so a huge/nested corpus can't blow the
    stack or memory."""
    def _iter_zip(zf, prefix, depth):
        for n in zf.namelist():
            if n.endswith("/"):
                continue
            try:
                raw = zf.read(n)
            except Exception:
                continue
            full = f"{prefix}!{n}" if prefix else n
            if (depth < _MAX_DEPTH and raw[:2] == b"PK"
                    and (n.endswith(".zip") or n.endswith("_content"))):
                try:
                    inner = zipfile.ZipFile(io.BytesIO(raw))
                    yield from _iter_zip(inner, full, depth + 1)
                    continue
                except Exception:
                    pass
            if len(raw) > max_bytes:
                continue
            yield full, _sha_bytes(raw), len(raw)

    if os.path.isdir(path):
        for root, _d, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                if f.endswith(".zip"):
                    try:
                        with zipfile.ZipFile(fp) as zf:
                            yield from _iter_zip(zf, os.path.relpath(fp, path), 1)
                    except Exception:
                        continue
                else:
                    try:
                        if os.path.getsize(fp) > max_bytes:
                            continue
                        sha, sz = _sha_file(fp)
                        yield os.path.relpath(fp, path), sha, sz
                    except Exception:
                        continue
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            yield from _iter_zip(zf, "", 1)
    elif os.path.isfile(path):
        try:
            sha, sz = _sha_file(path)
            yield os.path.basename(path), sha, sz
        except Exception:
            pass


def _refkey(name: str):
    """Normalize an extracted filename or an iflw reference to comparable
    key(s), undoing the extractor's '<package> - <name>' prefix and '-N'
    collision suffix so a bare reference matches the prefixed file."""
    n = name.lower().rsplit(" - ", 1)[-1]          # drop '<pkg> - ' prefix
    keys = [n]
    m = re.match(r'^(.*?)-\d+(\.[^.]+)$', n)         # drop collision -N suffix
    if m:
        keys.append(m.group(1) + m.group(2))
    return keys


def _iflw_refs(text: str):
    import html
    refs = set()
    for v in _REF_RE.findall(text):
        b = html.unescape(v.strip()).rsplit("/", 1)[-1]   # &amp; -> & etc.
        if "." in b:
            refs.add(b)
    return refs


def _sniff_ok(ext: str, head: bytes) -> bool:
    if not head:
        return False
    h = head.lstrip()[:1]
    if ext in _XMLISH:
        return h in (b"<", b"\xef")
    if ext == "json":
        return h in (b"{", b"[")
    if ext in _TEXTISH:
        try:
            head.decode("utf-8")
            return True
        except Exception:
            return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extracted", required=True)
    ap.add_argument("--source", default="")
    ap.add_argument("--report", default="")
    ap.add_argument("--skip-catalog", action="store_true")
    ap.add_argument("--max-file-mb", type=float, default=50.0)
    args = ap.parse_args()
    max_bytes = int(args.max_file_mb * 1024 * 1024)

    rep = open(args.report, "w", encoding="utf-8") if args.report else None

    def say(s=""):
        print(s, flush=True)
        if rep:
            rep.write(s + "\n")
            rep.flush()           # incremental: survives a hard crash

    if not os.path.isdir(args.extracted):
        say(f"ERROR: --extracted is not a directory: {args.extracted}")
        return 2

    say("=" * 64)
    say("EXTRACTION VERIFICATION  (max-file-mb=%g, skip-catalog=%s)"
        % (args.max_file_mb, args.skip_catalog))
    say("=" * 64)

    # ── index the extracted tree (streaming, chunked hashes) ─────────────────
    say("\nIndexing extracted tree … (progress every 2000 files)")
    by_sha = defaultdict(list)
    by_base = defaultdict(list)
    by_refname = defaultdict(list)     # normalized (deprefixed/desuffixed) name
    ext_counts = Counter()
    zero_byte = []
    heads = {}                    # sha -> (ext, first 8 bytes, relpath) for [4]
    count = 0
    for root, _d, files in os.walk(args.extracted):
        for f in files:
            fp = os.path.join(root, f)
            try:
                sha, sz = _sha_file(fp)
            except Exception:
                continue
            rel = os.path.relpath(fp, args.extracted)
            ext = f.rsplit(".", 1)[-1].lower() if "." in f else "(none)"
            if sz == 0:
                zero_byte.append(rel)
            by_sha[sha].append(rel)
            by_base[f.lower()].append(rel)
            for k in _refkey(f):
                by_refname[k].append(rel)
            ext_counts[ext] += 1
            if sha not in heads:
                try:
                    with open(fp, "rb") as fh:
                        heads[sha] = (ext, fh.read(8), rel)
                except Exception:
                    pass
            count += 1
            if count % 2000 == 0:
                say(f"  … {count} files indexed")
    n_files = sum(ext_counts.values())
    say(f"Extracted tree: {n_files} files, {len(by_sha)} unique by content "
        f"({n_files - len(by_sha)} content-duplicates).")
    say("  by extension: " + ", ".join(f"{e}={c}"
                                        for e, c in ext_counts.most_common()))

    # ── [1] authoritative source reconciliation ─────────────────────────────
    if args.source and os.path.exists(args.source):
        say("\n[1] SOURCE RECONCILIATION (authoritative, by content hash)")
        say("    walking source … (progress every 2000 files)")
        src_shas = set()
        src_first = {}
        src_total = 0
        try:
            for ipath, sha, _sz in _walk_source(args.source, max_bytes):
                src_shas.add(sha)
                src_first.setdefault(sha, ipath)
                src_total += 1
                if src_total % 2000 == 0:
                    say(f"    … {src_total} source files hashed")
        except MemoryError:
            say("    ✗ ran out of memory walking the source. Re-run with a "
                "smaller --max-file-mb, or point --source at one package "
                "subtree at a time.")
            src_shas = None
        if src_shas is not None:
            say(f"    source leaf files: {src_total}, {len(src_shas)} unique "
                "by content")
            missed = src_shas - set(by_sha)
            extra = set(by_sha) - src_shas
            if not missed:
                say("    ✓ every source file's content is present in the "
                    "extraction")
            else:
                # Classify: most misses are metadata your extraction skips on
                # purpose; only resource types matter for lookup.
                _META = {"project", "mf", "info", "hash", "rels", "(none)"}
                _RES = {"xsd", "xsl", "xslt", "mmap", "groovy", "gsh", "js",
                        "wsdl", "edmx", "opmap", "prop", "propdef", "json",
                        "xml", "odata"}
                meta_ext, res_ext, res_examples = Counter(), Counter(), {}
                for h in missed:
                    ip = src_first[h]
                    base = ip.rsplit("!", 1)[-1].rsplit("/", 1)[-1]
                    ext = base.rsplit(".", 1)[-1].lower() if "." in base else "(none)"
                    docxish = ("word/" in ip or "[Content_Types]" in ip
                               or "_rels" in ip or "META-INF" in ip)
                    if ext in _META or docxish:
                        meta_ext[ext] += 1
                    else:
                        res_ext[ext] += 1
                        res_examples.setdefault(ext, ip.rsplit("!", 1)[-1])
                say(f"    ✗ {len(missed)} source contents not in extraction. "
                    "Breakdown:")
                say(f"        metadata/infra (expected to skip): "
                    f"{sum(meta_ext.values())}  "
                    + ", ".join(f"{e}={c}" for e, c in meta_ext.most_common(8)))
                if res_ext:
                    say(f"        RESOURCE types (investigate — these affect "
                        f"lookup): {sum(res_ext.values())}")
                    for e, c in res_ext.most_common():
                        say(f"            {e}={c}   e.g. {res_examples[e]}")
                else:
                    say("        RESOURCE types missing: 0 — every "
                        "lookup-relevant file was extracted ✓")
            if extra:
                say(f"    • {len(extra)} extracted contents not in source "
                    "(extras / broader source — review if unexpected).")
    else:
        say("\n[1] SOURCE RECONCILIATION — skipped (no/absent --source). This "
            "is the authoritative check; provide --source for a real verdict.")

    # ── [2] catalog parity (heavy; guarded + skippable) ──────────────────────
    say("\n[2] CATALOG PARITY (project walk_corpus — the real catalog logic)")
    if args.skip_catalog:
        say("    skipped (--skip-catalog)")
    elif not (args.source and os.path.exists(args.source)):
        say("    needs --source; skipped")
    else:
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            sys.path.insert(0, os.path.dirname(here))
            from library_builder.corpus_pipeline import walk_corpus
            say("    building catalog file set (may take a moment) …")
            canon = walk_corpus(args.source)
            say(f"    catalog ingests {len(canon)} files (after oversize/budget/"
                "text-only skips — a subset of raw source is normal).")
            cat_missing = []
            for n, t in canon.items():
                base = n.rsplit("/", 1)[-1].lower()
                h = _sha_bytes(t.encode("utf-8", "replace"))
                if h not in by_sha and base not in by_base:
                    cat_missing.append(n)
            if not cat_missing:
                say("    ✓ every catalog file is present in the extraction")
            else:
                say(f"    ✗ {len(cat_missing)} catalog files NOT in extraction "
                    "(catalog saw content your extraction lost). Examples:")
                for n in cat_missing[:12]:
                    say(f"        - {n}")
        except MemoryError:
            say("    ✗ out of memory in catalog parity. Re-run with "
                "--skip-catalog; sections [1][3][4] still give a verdict.")
        except Exception as e:
            say(f"    (catalog parity unavailable: {e!r}; skipping — not fatal)")

    # ── [3] iflow reference completeness ─────────────────────────────────────
    say("\n[3] iFLOW REFERENCE COMPLETENESS")
    iflw_paths = [p for paths in by_base.values() for p in paths
                  if p.lower().endswith(".iflw")]
    all_refs, unresolved = set(), set()
    for rel in iflw_paths:
        try:
            with open(os.path.join(args.extracted, rel), "r",
                      encoding="utf-8", errors="replace") as fh:
                txt = fh.read()
        except Exception:
            continue
        for ref in _iflw_refs(txt):
            all_refs.add(ref)
            if not any(k in by_refname for k in _refkey(ref)):
                unresolved.add(ref)
    say(f"    {len(iflw_paths)} iflw files reference {len(all_refs)} distinct "
        "resources.")
    if not all_refs:
        say("    (no .iflw found in --extracted, or none reference resources)")
    elif not unresolved:
        say("    ✓ every referenced resource was extracted")
    else:
        say(f"    ✗ {len(unresolved)} referenced resources MISSING (lookups "
            "for these will fail). Examples:")
        for r in sorted(unresolved)[:15]:
            say(f"        - {r}")

    # ── [4] integrity + routing ──────────────────────────────────────────────
    say("\n[4] INTEGRITY + ROUTING")
    if zero_byte:
        say(f"    ✗ {len(zero_byte)} zero-byte files. Examples: {zero_byte[:5]}")
    else:
        say("    ✓ no zero-byte files")
    dups = sum(1 for ps in by_sha.values() if len(ps) > 1)
    say(f"    • {dups} content-duplicate groups (identical bytes, multiple "
        "names — usually fine).")
    bad_sniff, mis_routed = [], []
    for sha, (ext, head, rel) in heads.items():
        if ext in (_XMLISH | _TEXTISH | {"json"}) and not _sniff_ok(ext, head):
            bad_sniff.append(rel)
        parent = os.path.basename(os.path.dirname(rel)).lower()
        if ext not in ("(none)", "") and parent and \
                parent not in (ext, ext + "s") and ext not in parent:
            mis_routed.append(rel)
    if bad_sniff:
        say(f"    ✗ {len(bad_sniff)} files whose content ≠ their extension. "
            f"Examples: {bad_sniff[:5]}")
    else:
        say("    ✓ content matches extension for all checked files")
    if mis_routed:
        say(f"    • {len(mis_routed)} files whose folder ≠ their extension "
            f"(review bucketing). Examples: {mis_routed[:5]}")
    else:
        say("    ✓ extension-bucket routing consistent")

    say("\n" + "=" * 64)
    say("Verdict: [1] is the real completeness/integrity test; [2] is a "
        "cross-check (extras vs catalog are expected); [3] predicts whether "
        "resource lookup will work.")
    say("=" * 64)
    if rep:
        rep.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except MemoryError:
        print("\nFATAL: out of memory. Re-run with --skip-catalog and/or a "
              "smaller --max-file-mb (e.g. 25).", flush=True)
        raise SystemExit(3)
    except Exception:
        print("\nFATAL: verifier crashed — traceback below. Please paste this:",
              flush=True)
        traceback.print_exc()
        raise SystemExit(4)
