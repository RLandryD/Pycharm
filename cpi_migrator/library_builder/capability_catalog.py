"""library_builder/capability_catalog.py

Unified facade over the per-type capability extractors. One entry point so the
workbench and the next build phases don't need to know each module's specific
API. As more types are added (js, prop/propdef/opmap, iflw), register them here.

Current registry (all sandbox-proven):
  mmap   -> mmap_capabilities      (per-target-field capabilities)
  groovy -> groovy_capabilities    (envelope + universal ops + binding table)
  schema -> schema_catalog         (xsd/wsdl/edmx identity: dedupe + reuse index)
  xslt   -> xslt_capabilities      (portable transform core + ext binding layer)

Usage:
  from library_builder.capability_catalog import catalog_for, TYPES
  cat = catalog_for("groovy", {name: text, ...})

This is the FETCH layer's single door: build per-type catalogs, then the
solver's EVALUATE/FETCH/SELECT works against them uniformly.
"""
from __future__ import annotations

TYPES = ("mmap", "groovy", "schema", "xslt", "js", "props", "iflw", "pi")

# map a file extension to its catalog type
EXT_TO_TYPE = {
    "mmap": "mmap",
    "groovy": "groovy", "gsh": "groovy",
    "xsd": "schema", "wsdl": "schema", "edmx": "schema",
    "xslt": "xslt", "xsl": "xslt",
    "js": "js",
    "prop": "props", "propdef": "props",
    "iflw": "iflw",
    # PI artifacts: .java PI mappings/UDFs, .tpz packages (reader = handoff)
    "tpz": "pi",
}


def type_for_ext(ext: str):
    return EXT_TO_TYPE.get(ext.lower().lstrip("."))


def catalog_for(kind: str, corpus: dict, **kw) -> dict:
    """Build the capability catalog for a given type from a corpus (name->text).
    Returns a dict with at least: {capabilities|identities, count, ...}.
    Normalises the mmap module (which exposes catalog_summary/extract per text)
    into the same shape as the others."""
    if kind == "groovy":
        from . import groovy_capabilities as m
        return m.build_catalog(corpus, **kw)
    if kind == "schema":
        from . import schema_catalog as m
        return m.build_catalog(corpus, **kw)
    if kind == "xslt":
        from . import xslt_capabilities as m
        return m.build_catalog(corpus, **kw)
    if kind == "js":
        from . import js_capabilities as m
        return m.build_catalog(corpus)
    if kind == "props":
        from . import props_capabilities as m
        return m.build_catalog(corpus)
    if kind == "iflw":
        from . import iflw_capabilities as m
        return m.build_catalog(corpus)
    if kind == "pi":
        from . import pi_capabilities as m
        return m.build_catalog(corpus)
    if kind == "mmap":
        from . import mmap_capabilities as m
        caps = []
        for name, text in corpus.items():
            try:
                for c in m.extract_capabilities(text):
                    caps.append((name, c))
            except Exception:
                continue
        return {"capabilities": caps, "count": len(caps),
                "per_file": {n: m.catalog_summary(t)
                             for n, t in corpus.items()}}
    raise ValueError(f"unknown capability type: {kind!r} (known: {TYPES})")


def build_all(corpus_by_type: dict, **kw) -> dict:
    """corpus_by_type = {"groovy": {name:text}, "schema": {...}, ...}
    -> {type: catalog}. Convenience for building everything at once."""
    return {k: catalog_for(k, c, **kw) for k, c in corpus_by_type.items()
            if k in TYPES}
