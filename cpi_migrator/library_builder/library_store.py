"""library_builder/library_store.py — the distilled, growing library.

Design (agreed 2026-06-12):
  - ADDITIVE, content-hash-indexed: files keyed by SHA-256; re-extraction
    adds only unseen content, duplicates just gain provenance. Memory is
    bounded by UNIQUE content, so the raw package zips become deletable
    once coverage hits 100% and a generation round resolves library-only.
  - SCOPED: client-tenant harvests are client IP. They live in separate
    client workspaces (scope=<label>) and never merge into the reusable
    cross-client library unless explicitly promote()d.
  - RESOLVER-COMPATIBLE: as_corpus() emits {key: text} with keys shaped
    'library/<source>/<original path>' — same scheme walk_corpus uses, so
    package scoping and basename indexing in resource_resolver keep
    working unchanged.

Layout on disk:
  <root>/library_index.json          main scope index
  <root>/files/<sha>__<basename>     deduped content (main scope)
  <root>/clients/<scope>/index.json  per-client index
  <root>/clients/<scope>/files/...   per-client content
  <root>/catalog.json                persisted merged capability catalog
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import time
import zipfile
from dataclasses import dataclass, field

logger = logging.getLogger("library_builder.library_store")

#: everything the library keeps: wiring resources + flows + bundle cargo
LIBRARY_EXTS = {".groovy", ".gsh", ".js", ".xsl", ".xslt", ".mmap", ".opmap",
                ".prop", ".propdef", ".wsdl", ".xsd", ".edmx", ".iflw",
                ".jar", ".crt", ".cer", ".pem", ".json"}
#: extensions stored as bytes (no text decode)
_BINARY_EXTS = {".jar", ".crt", ".cer", ".pem"}
_MAX_MEMBER = 8 * 1024 * 1024
_MAX_DEPTH = 5


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _ext(name: str) -> str:
    base = name.rsplit("/", 1)[-1]
    return ("." + base.rsplit(".", 1)[-1].lower()) if "." in base else ""


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s)[:80]


@dataclass
class AddReport:
    added: int = 0
    duplicates: int = 0
    skipped: int = 0
    by_type: dict = field(default_factory=dict)

    def note(self, ext: str, new: bool):
        self.by_type.setdefault(ext or "(noext)", [0, 0])
        self.by_type[ext or "(noext)"][0 if new else 1] += 1
        if new:
            self.added += 1
        else:
            self.duplicates += 1


class LibraryStore:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)

    # ── per-scope plumbing ────────────────────────────────────────────────
    def _scope_dir(self, scope: str | None) -> str:
        if not scope:
            return self.root
        d = os.path.join(self.root, "clients", _safe(scope))
        os.makedirs(d, exist_ok=True)
        return d

    def _index_path(self, scope: str | None) -> str:
        return os.path.join(self._scope_dir(scope),
                            "library_index.json" if not scope
                            else "index.json")

    def load_index(self, scope: str | None = None) -> dict:
        p = self._index_path(scope)
        if os.path.exists(p):
            try:
                with open(p) as fh:
                    return json.load(fh)
            except Exception as exc:
                logger.warning("index load failed (%s); starting empty", exc)
        return {}

    def _save_index(self, idx: dict, scope: str | None):
        with open(self._index_path(scope), "w") as fh:
            json.dump(idx, fh, indent=0, sort_keys=True)

    def _files_dir(self, scope: str | None) -> str:
        d = os.path.join(self._scope_dir(scope), "files")
        os.makedirs(d, exist_ok=True)
        return d

    # ── ingestion ─────────────────────────────────────────────────────────
    def add_file(self, original_path: str, content: bytes, source: str,
                 scope: str | None = None, idx: dict | None = None,
                 report: AddReport | None = None) -> bool:
        """Add one file. Returns True if NEW content. Caller may pass a
        loaded idx for batching (then must _save_index itself)."""
        own_idx = idx is None
        if own_idx:
            idx = self.load_index(scope)
        rep = report or AddReport()
        sha = _sha(content)
        ext = _ext(original_path)
        if sha in idx:
            ent = idx[sha]
            if source not in ent["sources"]:
                ent["sources"].append(source)
            if original_path not in ent["names"]:
                ent["names"].append(original_path)
            rep.note(ext, new=False)
            new = False
        else:
            fname = f"{sha[:16]}__{_safe(original_path.rsplit('/', 1)[-1])}"
            with open(os.path.join(self._files_dir(scope), fname), "wb") as fh:
                fh.write(content)
            idx[sha] = {"file": fname, "ext": ext, "size": len(content),
                        "names": [original_path], "sources": [source],
                        "first_seen": time.strftime("%Y-%m-%d")}
            rep.note(ext, new=True)
            new = True
        if own_idx:
            self._save_index(idx, scope)
        return new

    def add_from_zip(self, raw: bytes, source: str,
                     scope: str | None = None) -> AddReport:
        """Walk a zip (package export, zip-of-zips, bundle) and ingest every
        LIBRARY_EXTS member. Additive: only unseen hashes write files."""
        rep = AddReport()
        idx = self.load_index(scope)

        def walk(zf: zipfile.ZipFile, prefix: str, depth: int):
            for n in zf.namelist():
                if n.endswith("/"):
                    continue
                try:
                    info = zf.getinfo(n)
                    if info.file_size > _MAX_MEMBER:
                        rep.skipped += 1
                        continue
                    data = zf.read(n)
                except Exception:
                    rep.skipped += 1
                    continue
                if data[:2] == b"PK" and depth < _MAX_DEPTH and (
                        n.lower().endswith(".zip") or n.endswith("_content")):
                    try:
                        walk(zipfile.ZipFile(io.BytesIO(data)),
                             f"{prefix}{n}!", depth + 1)
                        continue
                    except Exception:
                        pass
                if _ext(n) in LIBRARY_EXTS:
                    self.add_file(f"{prefix}{n}", data, source,
                                  scope=scope, idx=idx, report=rep)
        try:
            walk(zipfile.ZipFile(io.BytesIO(raw)), "", 0)
        except Exception as exc:
            logger.warning("add_from_zip failed for %s: %s", source, exc)
        self._save_index(idx, scope)
        logger.info("library add (%s, scope=%s): +%d new, %d dupes, "
                    "%d skipped", source, scope or "main", rep.added,
                    rep.duplicates, rep.skipped)
        return rep

    def add_from_dir(self, folder: str, scope: str | None = None) -> AddReport:
        """Ingest every zip in a folder (non-recursive on the folder itself;
        zips walk nested)."""
        total = AddReport()
        for fn in sorted(os.listdir(folder)):
            if not fn.lower().endswith(".zip"):
                continue
            try:
                with open(os.path.join(folder, fn), "rb") as fh:
                    rep = self.add_from_zip(fh.read(), source=fn, scope=scope)
                total.added += rep.added
                total.duplicates += rep.duplicates
                total.skipped += rep.skipped
                for k, v in rep.by_type.items():
                    total.by_type.setdefault(k, [0, 0])
                    total.by_type[k][0] += v[0]
                    total.by_type[k][1] += v[1]
            except Exception as exc:
                logger.warning("library: %s failed: %s", fn, exc)
        return total

    # ── consumption ──────────────────────────────────────────────────────
    def as_corpus(self, scope: str | None = None,
                  include_main: bool = True) -> dict:
        """{resolver-compatible key: text} — text files only (binary cargo
        ships via passthrough, not the resolver)."""
        out: dict = {}

        def load(scope_):
            idx = self.load_index(scope_)
            fdir = self._files_dir(scope_)
            for sha, ent in idx.items():
                if ent["ext"] in _BINARY_EXTS:
                    continue
                try:
                    with open(os.path.join(fdir, ent["file"]), "rb") as fh:
                        text = fh.read().decode("utf-8", "replace")
                except Exception:
                    continue
                src = _safe(ent["sources"][0])
                # original path keeps package scoping + basename indexing
                key = f"library/{src}/{ent['names'][0].replace('!', '/')}"
                out.setdefault(key, text)
        if include_main:
            load(None)
        if scope:
            load(scope)
        return out

    def stats(self, scope: str | None = None) -> dict:
        idx = self.load_index(scope)
        by_ext: dict = {}
        for ent in idx.values():
            by_ext[ent["ext"]] = by_ext.get(ent["ext"], 0) + 1
        return {"unique_files": len(idx), "by_ext": dict(sorted(
            by_ext.items(), key=lambda kv: -kv[1])),
            "bytes": sum(e["size"] for e in idx.values())}

    def scopes(self) -> list:
        d = os.path.join(self.root, "clients")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def coverage(self, raw_or_dir, scope: str | None = None) -> dict:
        """How much of a zip/folder is already in the library — the
        'safe to delete' signal."""
        hashes = set(self.load_index(None))
        if scope:
            hashes |= set(self.load_index(scope))
        seen = [0, 0]

        def check(zf, depth):
            for n in zf.namelist():
                if n.endswith("/"):
                    continue
                try:
                    data = zf.read(n)
                except Exception:
                    continue
                if data[:2] == b"PK" and depth < _MAX_DEPTH and (
                        n.lower().endswith(".zip") or n.endswith("_content")):
                    try:
                        check(zipfile.ZipFile(io.BytesIO(data)), depth + 1)
                        continue
                    except Exception:
                        pass
                if _ext(n) in LIBRARY_EXTS:
                    seen[0] += 1
                    if _sha(data) in hashes:
                        seen[1] += 1
        if isinstance(raw_or_dir, (bytes, bytearray)):
            check(zipfile.ZipFile(io.BytesIO(raw_or_dir)), 0)
        else:
            for fn in sorted(os.listdir(raw_or_dir)):
                if fn.lower().endswith(".zip"):
                    try:
                        with open(os.path.join(raw_or_dir, fn), "rb") as fh:
                            check(zipfile.ZipFile(
                                io.BytesIO(fh.read())), 0)
                    except Exception:
                        pass
        total, covered = seen
        return {"total": total, "covered": covered,
                "pct": round(100 * covered / total, 1) if total else 100.0,
                "safe_to_delete": total > 0 and covered == total}

    def promote(self, sha: str, from_scope: str) -> bool:
        """Explicitly move one file from a client workspace into the main
        library (the ONLY path from client scope to reusable library)."""
        cidx = self.load_index(from_scope)
        ent = cidx.get(sha)
        if not ent:
            return False
        src = os.path.join(self._files_dir(from_scope), ent["file"])
        try:
            with open(src, "rb") as fh:
                content = fh.read()
        except Exception:
            return False
        self.add_file(ent["names"][0], content,
                      source=f"promoted:{from_scope}")
        logger.info("promoted %s from scope %s to main library",
                    ent["file"], from_scope)
        return True

    # ── persisted merged capability catalog ──────────────────────────────
    def merged_catalog(self, corpus_by_type: dict | None = None) -> dict:
        """Load the persisted catalog, optionally merge a fresh build into
        it (additive — existing entries kept, new ones added), persist."""
        path = os.path.join(self.root, "catalog.json")
        old: dict = {}
        if os.path.exists(path):
            try:
                with open(path) as fh:
                    old = json.load(fh)
            except Exception:
                old = {}
        if corpus_by_type:
            try:
                from library_builder.capability_catalog import build_all
                new = build_all(corpus_by_type)
                for kind, entries in (new or {}).items():
                    if isinstance(entries, dict):
                        merged = dict(old.get(kind) or {})
                        for k, v in entries.items():
                            merged.setdefault(k, v)
                        old[kind] = merged
                    elif isinstance(entries, list):
                        have = {json.dumps(e, sort_keys=True, default=str)
                                for e in (old.get(kind) or [])}
                        out = list(old.get(kind) or [])
                        for e in entries:
                            if json.dumps(e, sort_keys=True,
                                          default=str) not in have:
                                out.append(e)
                        old[kind] = out
                    else:
                        old.setdefault(kind, entries)
                with open(path, "w") as fh:
                    json.dump(old, fh, indent=0, default=str)
            except Exception as exc:
                logger.warning("catalog merge failed: %s", exc)
        return old
