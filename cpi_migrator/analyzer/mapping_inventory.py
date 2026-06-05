"""
analyzer/mapping_inventory.py

Catalogs the mappings and user-defined functions (UDFs) across a set of
interfaces, so a consultant can see at a glance: how many mappings exist, how
many are graphical vs XSLT vs Java, which use UDFs, and which are reusable
across interfaces (the same mapping referenced by several).

This is the "mapping inventory / UDF cataloging" feature. It reads what the
interfaces declare (mapping program names, UDF references) and produces a
structured inventory + reuse analysis. It does NOT execute or rewrite
mappings — it inventories them for planning.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MappingEntry:
    name: str
    mapping_type: str            # "graphical" | "xslt" | "java" | "unknown"
    interfaces: list[str] = field(default_factory=list)   # which interfaces use it
    udfs: list[str] = field(default_factory=list)
    is_reused: bool = False

    @property
    def reuse_count(self) -> int:
        return len(self.interfaces)


@dataclass
class MappingInventory:
    mappings: list[MappingEntry] = field(default_factory=list)
    udf_catalog: dict = field(default_factory=dict)   # udf_name -> [mapping names]

    def summary(self) -> dict:
        by_type = defaultdict(int)
        for m in self.mappings:
            by_type[m.mapping_type] += 1
        reused = [m for m in self.mappings if m.is_reused]
        return {
            "total_mappings":   len(self.mappings),
            "graphical":        by_type.get("graphical", 0),
            "xslt":             by_type.get("xslt", 0),
            "java":             by_type.get("java", 0),
            "reused_mappings":  len(reused),
            "total_udfs":       len(self.udf_catalog),
            "reuse_opportunity": sum(m.reuse_count - 1 for m in reused),  # saved builds
        }


def _classify_mapping(name: str) -> str:
    n = (name or "").lower()
    if n.endswith((".xsl", ".xslt")) or "xslt" in n:
        return "xslt"
    if n.endswith(".mmap") or "messagemapping" in n or "graphical" in n:
        return "graphical"
    if "java" in n or n.endswith(".jar") or "udf" in n:
        return "java"
    return "unknown"


def build_inventory(interfaces: list, configs: Optional[dict] = None) -> MappingInventory:
    """Build a mapping inventory from interface records.

    interfaces : list of InterfaceRecord (have .name, .mapping_program,
                 optionally .description / raw with mapping refs)
    """
    inv = MappingInventory()
    by_name: dict[str, MappingEntry] = {}
    udf_catalog: dict[str, list] = defaultdict(list)

    for iface in interfaces:
        iface_name = getattr(iface, "name", "?")
        mapping = getattr(iface, "mapping_program", None)
        if not mapping:
            continue

        mtype = _classify_mapping(mapping)
        if mapping not in by_name:
            by_name[mapping] = MappingEntry(name=mapping, mapping_type=mtype)
        by_name[mapping].interfaces.append(iface_name)

        # Extract UDF references from description / mapping name heuristically
        desc = getattr(iface, "description", "") or ""
        udf_refs = re.findall(r"\b(?:UDF|udf)[ _:]?(\w+)", desc)
        for udf in udf_refs:
            by_name[mapping].udfs.append(udf)
            udf_catalog[udf].append(mapping)

    for m in by_name.values():
        m.is_reused = len(m.interfaces) > 1
        m.udfs = sorted(set(m.udfs))
        inv.mappings.append(m)

    inv.udf_catalog = {k: sorted(set(v)) for k, v in udf_catalog.items()}
    # Sort: reused first, then by name
    inv.mappings.sort(key=lambda m: (not m.is_reused, m.name))
    return inv
