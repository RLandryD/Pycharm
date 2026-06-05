"""library_builder/mmap_capabilities.py

Capability-section catalog for message mappings — the concrete proof of the
"type-aware capability extraction" architecture (the user's blocker #2):
decompose an artifact into tagged FUNCTIONAL SECTIONS so the program can find
"the thing that solves X exclusively" rather than whole files containing X.

For an mmap, the natural capability unit is a single TARGET-FIELD MAPPING: one
output field produced by one expression tree (functions + sources + constants).
Each becomes a `Capability` tagged with:
  - target field, source fields, functions used, constants
  - a semantic CATEGORY (date / numeric / string / conditional / lookup /
    aggregation / context / copy / constant) inferred from the functions
  - a COMPLEXITY weight, using SAP's complexity-driver taxonomy
    (see reporter/SAP_MIGRATION_ASSESSMENT_ALIGNMENT.md): custom UDFs, lookups,
    value-maps, context handling score higher.

This turns the (read-only, fingerprint) catalog into a searchable library of
adaptable mapping capabilities. Built on mmap_parser (proven 100% on 120 mmaps).
"""
from __future__ import annotations

from dataclasses import dataclass, field as _field

from .mmap_parser import parse_mmap, ParsedNode


# --- semantic categorisation of functions ----------------------------------
_CATEGORY = {
    # date
    "currentDate": "date", "TransformDate": "date", "DateBefore": "date",
    "DateAfter": "date", "CompareDates": "date",
    # numeric
    "add": "numeric", "sub": "numeric", "mul": "numeric", "div": "numeric",
    "abs": "numeric", "sqrt": "numeric", "sqr": "numeric", "power": "numeric",
    "round": "numeric", "ceil": "numeric", "floor": "numeric", "sign": "numeric",
    "inv": "numeric", "max": "numeric", "min": "numeric", "average": "numeric",
    "sum": "aggregation", "count": "aggregation", "counter": "aggregation",
    "formatNumber": "numeric",
    # string
    "concat": "string", "substring": "string", "toUpperCase": "string",
    "toLowerCase": "string", "trim": "string", "replaceString": "string",
    "length": "string", "indexOf2": "string", "indexOf3": "string",
    "lastIndexOf2": "string", "lastIndexOf3": "string", "startWith2": "string",
    "startWith3": "string", "endWith": "string", "formatByExample": "string",
    # conditional / logical
    "iF": "conditional", "iFS": "conditional", "ifWithoutElse": "conditional",
    "ifSWithoutElse": "conditional", "createIf": "conditional",
    "and": "logical", "or": "logical", "not": "logical",
    "equals": "logical", "equalsA": "logical", "notEquals": "logical",
    "greater": "logical", "less": "logical", "compare": "logical",
    "stringEquals": "logical", "exists": "logical", "isNil": "logical",
    # lookup / value-mapping (SAP high-complexity drivers)
    "valuemap": "lookup", "FixValues": "lookup", "mapWithDefault": "lookup",
    # context / structural (N:M handling — SAP complexity driver)
    "useOneAsMany": "context", "removeContexts": "context",
    "collapseContexts": "context", "SplitByValue": "context",
    "sort": "context", "sortByKey": "context", "replaceValue": "context",
    # header / property access
    "getHeader": "header", "getProperty": "header",
    # copy / passthrough
    "CopyValue": "copy", "copyValue": "copy",
    # constant
    "const": "constant", "constant": "constant",
}

# complexity weights echoing SAP's taxonomy (higher = harder to migrate/adapt)
_WEIGHT = {
    "lookup": 15, "context": 15, "aggregation": 10, "conditional": 5,
    "date": 5, "header": 5, "numeric": 1, "string": 1, "logical": 1,
    "copy": 1, "constant": 1, "direct": 0,
}


@dataclass
class Capability:
    target_field: str
    category: str
    functions: list = _field(default_factory=list)
    sources: list = _field(default_factory=list)
    constants: list = _field(default_factory=list)
    weight: int = 0
    depth: int = 0           # nesting depth of the expression tree

    def signature(self) -> str:
        """A compact, matchable signature of what this capability does."""
        fns = "+".join(self.functions) or "direct"
        return f"{self.category}:{fns}({len(self.sources)}src)"


def _walk(node: ParsedNode, funcs, srcs, consts, depth=0):
    if node is None:
        return depth
    d = depth
    if node.kind == "func" and node.value != "__DST__":
        funcs.append(node.value)
    elif node.kind == "src":
        srcs.append(node.value.split(":")[-1])
    elif node.kind == "const":
        consts.append(node.value)
    for a in node.args:
        d = max(d, _walk(a, funcs, srcs, consts, depth + 1))
    # bindings constants
    for (_pn, val) in getattr(node, "bindings", []) or []:
        consts.append(val[:40])
    return d


def extract_capabilities(mmap_text: str) -> list:
    """Return one Capability per target field in the mmap."""
    pm = parse_mmap(mmap_text)
    caps = []
    for fld in pm.fields:
        funcs, srcs, consts = [], [], []
        depth = _walk(fld.tree, funcs, srcs, consts)
        # category from the OUTERMOST function (the field's primary intent)
        cat = "direct"
        for f in funcs:
            if f in _CATEGORY:
                cat = _CATEGORY[f]
                break
        if not funcs:
            cat = "direct" if srcs else ("constant" if consts else "direct")
        weight = sum(_WEIGHT.get(_CATEGORY.get(f, ""), 1) for f in funcs) or \
            _WEIGHT.get(cat, 0)
        caps.append(Capability(
            target_field=fld.target_path.split(":")[-1],
            category=cat, functions=funcs, sources=srcs, constants=consts,
            weight=weight, depth=depth))
    return caps


def catalog_summary(mmap_text: str) -> dict:
    """Aggregate a mapping into a capability profile (for the catalog index)."""
    caps = extract_capabilities(mmap_text)
    from collections import Counter
    by_cat = Counter(c.category for c in caps)
    return {
        "field_count": len(caps),
        "total_weight": sum(c.weight for c in caps),
        "max_depth": max((c.depth for c in caps), default=0),
        "categories": dict(by_cat),
        "signatures": sorted({c.signature() for c in caps}),
    }
