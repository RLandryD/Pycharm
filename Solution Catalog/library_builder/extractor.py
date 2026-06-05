"""library_builder/extractor.py

Solution-catalog extractor for harvested SAP CPI artifacts.

Goal (locked with the user)
---------------------------
Turn tens of thousands of real artifacts (groovy/js, the XML family, and
key-value config) into a DEDUPLICATED, CATEGORIZED solution catalog the
workbench can query by intent ("I need to do X -> here are the distinct
solutions, with their requirements"). Coverage over brevity: every
FUNCTIONALLY DISTINCT solution is kept; only true duplicates collapse.

Design
------
* One program, three engines dispatched by file extension:
    - CODE      (groovy, gsh, js)  -> imports, methods, operations, idioms
    - XML_TREE  (iflw, mmap, opmap, xsl, xslt, xsd, wsdl, edmx, odata, xml)
                                   -> root, namespaces, node-structure patterns
    - KEYVALUE  (prop, propdef, properties, json)
                                   -> key signatures, value-shape patterns
* Normalized REGISTRIES (deduplicated building blocks referenced by id):
    IMPORTS, SERVICES, IDIOMS.
* SOLUTION ENTRIES filed under a provisional functional CATEGORY, referencing
  the registries, carrying mechanical requirements (what it needs) and
  produces (what it writes).
* CONSERVATIVE dedup: two entries merge only if their structural fingerprint
  AND import set AND operation set match. Anything differing is kept distinct
  (never lose a solution).
* Variable names are normalized to DESCRIPTIVE names so cosmetic differences
  collapse and stored code is readable.

Honest scope
------------
This phase extracts MECHANICAL facts reliably and assigns a PROVISIONAL,
rule-based category. Accurate plain-English intent and final categories are a
separate Phase-2 refinement over the (much smaller) deduplicated catalog.
This is a reference/learning tool: it mines patterns from harvested content;
it does not republish SAP's shipped files as product output.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger("library_builder")

# Extension -> engine family
CODE_EXT     = {"groovy", "gsh", "js"}
XML_EXT      = {"iflw", "mmap", "opmap", "xsl", "xslt", "xsd", "wsdl",
                "edmx", "odata", "xml"}
KEYVALUE_EXT = {"prop", "propdef", "properties", "json"}

# Filenames / patterns that are metadata or noise -> rejected, never cataloged.
REJECT_FILENAMES = {
    "manifest.mf", "metainfo.prop", "contentmetadata.md",
    "exportinformation.info", ".project", "resources.cnt", "hash",
}
REJECT_EXT = {"info", "tmp", "txt", "crt", "ini"}
# XML root elements that signal metadata rather than logic.
REJECT_XML_ROOTS = {"manifest", "metadata", "exportinformation", "project"}


def _norm_ext(path: str) -> str:
    return Path(path).suffix.lower().lstrip(".")


def _sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Registries — deduplicated building blocks referenced by id
# ---------------------------------------------------------------------------
class Registry:
    """A dedup-by-value store that hands out stable ids."""
    def __init__(self, prefix: str):
        self.prefix = prefix
        self._by_value: dict[str, str] = {}      # value -> id
        self._by_id: dict[str, str] = {}         # id -> value

    def intern(self, value: str) -> str:
        value = value.strip()
        if value in self._by_value:
            return self._by_value[value]
        new_id = f"{self.prefix}_{len(self._by_value) + 1:04d}"
        self._by_value[value] = new_id
        self._by_id[new_id] = value
        return new_id

    def as_dict(self) -> dict:
        return dict(sorted(self._by_id.items()))


# ---------------------------------------------------------------------------
# Solution entry
# ---------------------------------------------------------------------------
@dataclass
class Solution:
    fingerprint: str                 # dedup key
    type: str                        # groovy | mmap | xsd | ...
    category: str = "UNCATEGORIZED"  # provisional functional category
    intent: str = ""                 # filled in Phase 2 (left blank here)
    imports: list = field(default_factory=list)     # [IMP_xxxx]
    services: list = field(default_factory=list)     # [SVC_xxxx]
    operations: list = field(default_factory=list)   # detected op tags
    requires: dict = field(default_factory=dict)     # what it reads/needs
    produces: dict = field(default_factory=dict)     # what it writes
    code: str = ""                   # normalized, descriptive-variable code
    source_count: int = 1            # how many real files collapsed here
    source_examples: list = field(default_factory=list)  # a few file paths


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------
class LibraryBuilder:
    def __init__(self):
        self.imports = Registry("IMP")
        self.services = Registry("SVC")
        self.idioms = Registry("IDIOM")
        self.solutions: dict[str, Solution] = {}   # fingerprint -> Solution
        self.rejected: list[tuple[str, str]] = []  # (path, reason)
        self.counts = defaultdict(int)
        self._raw_hashes: dict[str, set] = {}       # fingerprint -> {raw hashes}
        self._collapse_paths: dict[str, list] = {}  # fingerprint -> [paths]

    # ---- ingest ----------------------------------------------------------
    def add_file(self, path: str, text: str):
        ext = _norm_ext(path)
        name = Path(path).name.lower()
        self.counts["seen"] += 1

        # rejection gate
        if name in REJECT_FILENAMES or ext in REJECT_EXT:
            self.rejected.append((path, f"metadata/noise ({ext or name})"))
            self.counts["rejected"] += 1
            return

        if ext in CODE_EXT:
            self._ingest_code(path, text, ext)
        elif ext in XML_EXT:
            self._ingest_xml(path, text, ext)
        elif ext in KEYVALUE_EXT:
            self._ingest_keyvalue(path, text, ext)
        else:
            self.rejected.append((path, f"unhandled extension '{ext}'"))
            self.counts["rejected"] += 1

    def _store(self, sol: Solution, path: str, raw_text: str = ""):
        """Conservative dedup: merge only on identical fingerprint.

        Verification: we also record a hash of the RAW file content per
        fingerprint. If files with the same fingerprint have DIFFERENT raw
        hashes, the dedup may be collapsing genuinely distinct scripts — those
        fingerprints are reported by collapse_report() for review.
        """
        import hashlib
        raw_hash = hashlib.sha1(
            raw_text.encode("utf-8", "replace")).hexdigest()[:16] if raw_text else ""
        existing = self.solutions.get(sol.fingerprint)
        if existing:
            existing.source_count += 1
            if len(existing.source_examples) < 5:
                existing.source_examples.append(path)
            # accumulate example value-sets for config entries (keep ~5)
            ex = sol.produces.get("example_values") if sol.produces else None
            if ex:
                cur = existing.produces.setdefault("example_values", [])
                if len(cur) < 5:
                    cur.extend(ex)
            self.counts["duplicate"] += 1
            if raw_hash:
                self._raw_hashes.setdefault(sol.fingerprint, set()).add(raw_hash)
                self._collapse_paths.setdefault(sol.fingerprint, []).append(path)
        else:
            sol.source_examples.append(path)
            self.solutions[sol.fingerprint] = sol
            self.counts["distinct"] += 1
            if raw_hash:
                self._raw_hashes.setdefault(sol.fingerprint, set()).add(raw_hash)
                self._collapse_paths.setdefault(sol.fingerprint, []).append(path)

    def collapse_report(self) -> dict:
        """Report fingerprints whose collapsed files are NOT byte-identical.

        A fingerprint with >1 distinct raw hash means the dedup merged files
        whose raw content differs — candidates for lost distinct solutions.
        Returns {fingerprint: {n_files, n_distinct_raw, paths}} for those.
        """
        suspect = {}
        for fp, hashes in self._raw_hashes.items():
            paths = self._collapse_paths.get(fp, [])
            if len(hashes) > 1:   # same fingerprint, different raw content
                sol = self.solutions.get(fp)
                # Config types (props/JSON) intentionally collapse same-key/
                # different-value files — that is the agreed behavior, not a
                # suspect over-collapse, so don't flag them.
                if sol and sol.requires.get("intended_value_dedup"):
                    continue
                suspect[fp] = {
                    "n_files": len(paths),
                    "n_distinct_raw": len(hashes),
                    "category": sol.category if sol else "",
                    "paths": paths[:10],
                }
        return suspect

    # ---- CODE engine (groovy/gsh/js) is in code_engine.py ---------------
    def _ingest_code(self, path, text, ext):
        from library_builder.code_engine import analyze_code
        sol = analyze_code(text, ext, self.imports, self.services, self.idioms)
        if sol is None:
            self.rejected.append((path, "code did not parse / empty"))
            self.counts["rejected"] += 1
            return
        self._store(sol, path, raw_text=text)

    # ---- XML engine ------------------------------------------------------
    def _ingest_xml(self, path, text, ext):
        from library_builder.xml_engine import analyze_xml
        sol, reject = analyze_xml(text, ext)
        if reject:
            self.rejected.append((path, reject))
            self.counts["rejected"] += 1
            return
        self._store(sol, path, raw_text=text)

    # ---- KEYVALUE engine -------------------------------------------------
    def _ingest_keyvalue(self, path, text, ext):
        from library_builder.kv_engine import analyze_keyvalue
        sol, reject = analyze_keyvalue(text, ext)
        if reject:
            self.rejected.append((path, reject))
            self.counts["rejected"] += 1
            return
        self._store(sol, path, raw_text=text)

    # ---- walk a directory or a zip --------------------------------------
    def ingest_path(self, root: str):
        root = Path(root)
        if root.is_dir():
            for p in root.rglob("*"):
                if p.is_file():
                    self._maybe_read(p)
        elif root.suffix.lower() == ".zip":
            self._ingest_zip(root)

    def _maybe_read(self, p: Path):
        ext = p.suffix.lower().lstrip(".")
        if ext == "zip":
            self._ingest_zip(p)
            return
        if ext not in CODE_EXT | XML_EXT | KEYVALUE_EXT | REJECT_EXT:
            return
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            self.add_file(str(p), text)
        except Exception as exc:
            self.rejected.append((str(p), f"read error: {exc}"))

    def _ingest_zip(self, zpath: Path):
        try:
            z = zipfile.ZipFile(zpath)
        except Exception as exc:
            self.rejected.append((str(zpath), f"bad zip: {exc}"))
            return
        for n in z.namelist():
            if n.endswith("/"):
                continue
            ext = _norm_ext(n)
            try:
                raw = z.read(n)
            except Exception:
                continue
            # nested zip (package export _content entries are zips)
            if raw[:2] == b"PK":
                try:
                    import io
                    self._ingest_zip_bytes(io.BytesIO(raw), f"{zpath}!{n}")
                    continue
                except Exception:
                    pass
            if ext not in CODE_EXT | XML_EXT | KEYVALUE_EXT:
                continue
            try:
                self.add_file(f"{zpath}!{n}", raw.decode("utf-8", "replace"))
            except Exception as exc:
                self.rejected.append((f"{zpath}!{n}", f"decode error: {exc}"))

    def _ingest_zip_bytes(self, bio, label):
        z = zipfile.ZipFile(bio)
        for n in z.namelist():
            if n.endswith("/"):
                continue
            ext = _norm_ext(n)
            raw = z.read(n)
            if raw[:2] == b"PK":
                import io
                try:
                    self._ingest_zip_bytes(io.BytesIO(raw), f"{label}!{n}")
                    continue
                except Exception:
                    pass
            if ext not in CODE_EXT | XML_EXT | KEYVALUE_EXT:
                continue
            self.add_file(f"{label}!{n}", raw.decode("utf-8", "replace"))

    # ---- output ----------------------------------------------------------
    def write_catalog(self, out_dir: str):
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)

        # registries
        (out / "registry_imports.json").write_text(
            json.dumps(self.imports.as_dict(), indent=2))
        (out / "registry_services.json").write_text(
            json.dumps(self.services.as_dict(), indent=2))
        (out / "registry_idioms.json").write_text(
            json.dumps(self.idioms.as_dict(), indent=2))

        # solutions grouped by type, then category
        by_type = defaultdict(lambda: defaultdict(list))
        for sol in self.solutions.values():
            by_type[sol.type][sol.category].append(asdict(sol))
        for typ, cats in by_type.items():
            (out / f"catalog_{typ}.json").write_text(
                json.dumps(cats, indent=2))

        # rejected log
        with open(out / "rejected.log", "w") as f:
            for path, reason in self.rejected:
                f.write(f"{reason}\t{path}\n")

        # collapse verification report — fingerprints that merged files whose
        # RAW content differs (possible over-aggressive dedup to review)
        suspects = self.collapse_report()
        (out / "collapse_review.json").write_text(json.dumps(suspects, indent=2))

        # summary
        summary = {
            "files_seen": self.counts["seen"],
            "distinct_solutions": self.counts["distinct"],
            "duplicates_collapsed": self.counts["duplicate"],
            "rejected": self.counts["rejected"],
            "imports": len(self.imports.as_dict()),
            "services": len(self.services.as_dict()),
            "idioms": len(self.idioms.as_dict()),
            "by_type": {t: sum(len(v) for v in c.values())
                        for t, c in by_type.items()},
            "collapses_to_review": len(suspects),
        }
        (out / "summary.json").write_text(json.dumps(summary, indent=2))
        return summary
