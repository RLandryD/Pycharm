"""resource_attach.py — ship the REAL files an iFlow's steps reference.

The generators emit a step's callActivity verbatim (so its config — script
name, mappinguri, etc. — is preserved), but a bundle is only deployable if the
referenced files actually travel with it. This layer reads each step's resource
reference, resolves it against a path-keyed corpus (the original package
exports) via resource_resolver, and returns {bundle_path: content} to merge into
the bundle — real `.groovy`/`.xsl`/`.mmap`, not synthetic stubs.

Reproduce metric is unaffected (it compares step kinds/order, not files); this is
purely deploy-fidelity. When no corpus is supplied the bundle is unchanged.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field

from scaffolder.resource_resolver import build_index, resolve


@dataclass
class AttachReport:
    shipped: dict = field(default_factory=dict)        # bundle_path -> content
    resolved: list = field(default_factory=list)       # (step_id, ref, corpus_path)
    unresolved: list = field(default_factory=list)     # (step_id, ref, kind)
    ambiguous: list = field(default_factory=list)       # (step_id, ref, candidates)


def _mapping_ext_kind(cfg: dict):
    cv = (cfg.get("cmdVariantUri") or "")
    uri = (cfg.get("mappinguri") or "").lower()
    if "MessageMapping" in cv:
        return ".mmap", "mmap"
    if "OperationMapping" in cv:
        return ".opmap", "mapping"
    if "XSLTMapping" in cv or "xslt" in uri or uri.endswith(".xsl"):
        return ".xsl", "xslt"
    return ".xsl", "xslt"


def step_resource_ref(step):
    """(reference, resolver_kind, bundle_path) for a step that references a
    resource file, else None. Covers Groovy/JS scripts and XSLT/Message
    mappings — the two reference styles seen in the corpus."""
    cfg = getattr(step, "config", None) or {}
    cv = cfg.get("cmdVariantUri", "")
    # script: config carries the bare filename (e.g. 'clientID.groovy')
    if cfg.get("script") or "GroovyScript" in cv or "JavaScript" in cv:
        ref = cfg.get("script")
        if ref:
            base = ref.rsplit("/", 1)[-1]
            kind = "groovy" if base.endswith(".groovy") else (
                "js" if base.endswith(".js") else "script")
            return ref, kind, f"src/main/resources/script/{base}"
    # mapping: a dir:// uri (has the extension) or mappingpath/mappingname
    if cfg.get("mappinguri") or cfg.get("mappingpath") or \
            "Mapping" in cv or step.kind in ("Mapping", "XSLTMapping",
                                             "MessageMapping"):
        ext, kind = _mapping_ext_kind(cfg)
        ref = cfg.get("mappinguri") or cfg.get("mappingpath") or \
            cfg.get("mappingname")
        if ref:
            base = (cfg.get("mappinguri") or "").rsplit("/", 1)[-1]
            if not base or "." not in base:
                base = (cfg.get("mappingname")
                        or ref.rsplit("/", 1)[-1] or "mapping") + ext
            return ref, kind, f"src/main/resources/mapping/{base}"
    return None


def _referenced_schemas(content: str) -> set:
    """Schema files a mapping references by name (the tenant resolves these at
    design time: 'Source element content is not found. File X.wsdl not found')."""
    import re as _re
    return set(_re.findall(r"[\w][\w .\-]*\.(?:wsdl|xsd)", html.unescape(content)))


def attach_resources(model, resource_files: dict,
                     package: str | None = None) -> AttachReport:
    """Resolve every step's resource reference against `resource_files`
    ({corpus_path: content}) and return an AttachReport. Mappings additionally
    pull the schema files (.wsdl/.xsd) they reference, shipped to the standard
    bundle folders. Graceful: an unresolved reference is logged, never fatal
    (the step still ships, just without its file — same as before)."""
    rep = AttachReport()
    if not resource_files:
        return rep
    index = build_index(resource_files)
    for s in model.steps.values():
        info = step_resource_ref(s)
        if not info:
            continue
        ref, kind, bundle_path = info
        r = resolve(ref, resource_files, index, package=package, kind=kind)
        if r.ok:
            rep.shipped[bundle_path] = r.content
            rep.resolved.append((s.id, ref, r.path))
            if r.ambiguous:
                rep.ambiguous.append((s.id, ref, r.candidates))
            if kind in ("xslt", "mmap", "mapping"):
                for schema in _referenced_schemas(r.content):
                    sub = "wsdl" if schema.endswith(".wsdl") else "xsd"
                    sr = resolve(schema, resource_files, index,
                                 package=package, kind="schema")
                    if sr.ok:
                        rep.shipped[f"src/main/resources/{sub}/{schema}"] = \
                            sr.content
                        rep.resolved.append((s.id, schema, sr.path))
                    else:
                        rep.unresolved.append((s.id, schema, "schema"))
        else:
            rep.unresolved.append((s.id, ref, kind))
    return rep
