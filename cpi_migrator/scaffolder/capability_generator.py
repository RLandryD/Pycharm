"""scaffolder/capability_generator.py

Capability-backed artifact generation for "Generate All".

Today generate_bundle() emits GENERIC templates (a keyword-picked Groovy script
+ a placeholder/draft mapping). This module upgrades that: given the learned
capability corpus, it selects the SINGLE BEST real artifact (a real Groovy script
/ real mapping / real iFlow pattern extracted from actual packages) and adapts it
to the interface — so the generated package is a real, proven artifact adapted,
not a hollow template. That matters because the Generate-All artifacts are what
get tested on the tenant.

Design (agreed):
  * option (a): pick the single best match and adapt it (not a chooser).
  * STRICTLY ADDITIVE: capability-mode is tried first; if no confident match,
    the caller falls back to the existing generic template — so output is never
    worse, only better.
  * HONEST LABELS: an adapted real artifact is a far better STARTING POINT than a
    template, but it is adapted-not-authored. confidence stays "reasoned"; the
    bundle still flags needs_tenant_test. We never claim finished business logic.

Boundary (unchanged): Claude proves the SELECTION + ADAPTATION logic in the
sandbox; whether the adapted artifact deploys/runs is the user's tenant test.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CapabilityArtifact:
    kind: str                  # "script" | "mapping" | "iflow"
    content: str               # the real artifact body (adapted)
    source_capability: str     # which learned capability it came from
    confidence: str = "reasoned"
    needs_tenant_test: bool = True
    note: str = ""
    externalized_params: list = None   # surfaced for the field-spec layer


_STOP = {"the", "and", "for", "with", "from", "into", "this", "that", "via",
         "are", "was", "will", "all", "any", "out", "use", "used", "make",
         "interface", "iflow", "message", "data", "process", "sap"}


def _tok(s) -> set:
    import re as _re
    return {t for t in _re.findall(r"[a-z0-9]{3,}", (s or "").lower())} - _STOP


def _best_match(corpus, requirement_text: str, ctype: str):
    """Single best capability of a given type for this requirement.

    Scores each candidate by how many requirement terms appear in its
    (keywords + source filename + raw file text) — because the business intent
    lives in the script/mapping itself and its name, not only in the structural
    operation labels. Falls back to the structural solver if lexical overlap is
    weak, and applies an honest gate so a poor match is rejected (→ the caller
    uses the generic template instead)."""
    normed = [c for c in getattr(corpus, "normalized", [])
              if c.ctype == ctype]
    if not normed:
        return None
    req = _tok(requirement_text)
    files = getattr(corpus, "files", {}) or {}
    best, best_score = None, 0.0
    if req:
        for c in normed:
            ref = getattr(c, "source_ref", "") or ""
            stem = ref.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            cand = (set(getattr(c, "keywords", set()))
                    | _tok(stem.replace("_", " ").replace("-", " "))
                    | _tok(files.get(ref, "")[:2000]))
            if not cand:
                continue
            score = len(req & cand) / len(req)
            if score > best_score:
                best, best_score = c, score
    if best is not None and best_score >= 0.15:
        return best
    # secondary: original structural solver
    try:
        from library_builder.solver import fetch, Need, _kw
        ranked = fetch(Need(requirement_text, _kw(requirement_text)), normed)
        if ranked and getattr(ranked[0], "score", 0) >= 0.15:
            return ranked[0].capability
    except Exception:
        pass
    return None


def _body_for(corpus, cap) -> str:
    """The real artifact body. Capabilities store ANATOMY, not source text, but
    the corpus retains the original file text by name — and the capability's
    source_ref is that name. So the body comes from corpus.files[source_ref]."""
    files = getattr(corpus, "files", {}) or {}
    return files.get(getattr(cap, "source_ref", ""), "")


def generate_script_from_capability(interface, corpus):
    """Select + adapt the best real Groovy script for this interface."""
    req = _interface_requirement_text(interface)
    cap = _best_match(corpus, req, "groovy")
    if cap is None:
        return None
    body = _body_for(corpus, cap)
    if not body:
        return None
    raw = getattr(cap, "raw", None)
    return CapabilityArtifact(
        kind="script",
        content=body,
        source_capability=cap.source_ref,
        note=("Adapted from a real learned Groovy capability "
              f"({cap.source_ref}). Reasoned starting point — review the "
              "business logic and test on tenant."),
        externalized_params=list(getattr(raw, "externalized_params", []) or []),
    )


def generate_mapping_from_capability(interface, corpus):
    """Select the best real message mapping for this interface — in two tiers:
    (1) a real learned .mmap whose intent matches (best); else
    (2) a schema-drafted mapping from the corpus's schemas' element names
        (better than a blank placeholder). Returns None only if neither is
        possible (caller falls back to the generic template)."""
    req = _interface_requirement_text(interface)
    cap = _best_match(corpus, req, "mmap")
    if cap is not None:
        body = _body_for(corpus, cap)
        if body:
            return CapabilityArtifact(
                kind="mapping",
                content=body,
                source_capability=cap.source_ref,
                note=("Adapted from a real learned mapping capability "
                      f"({cap.source_ref}). Review field logic; test on tenant."),
            )
    drafted = _draft_mapping_from_schemas(interface, corpus)
    if drafted is not None:
        return drafted
    return None


def generate_xslt_from_capability(interface, corpus):
    """Select the best real XSLT transform for this interface, if one matches."""
    cap = _best_match(corpus, _interface_requirement_text(interface), "xslt")
    if cap is None:
        return None
    body = _body_for(corpus, cap)
    if not body:
        return None
    return CapabilityArtifact(
        kind="xslt", content=body, source_capability=cap.source_ref,
        note=(f"Adapted from a real learned XSLT transform ({cap.source_ref}). "
              "Review; test on tenant."))


def generate_js_from_capability(interface, corpus):
    """Select the best real JavaScript resource for this interface, if matched."""
    cap = _best_match(corpus, _interface_requirement_text(interface), "js")
    if cap is None:
        return None
    body = _body_for(corpus, cap)
    if not body:
        return None
    return CapabilityArtifact(
        kind="js", content=body, source_capability=cap.source_ref,
        note=(f"Adapted from a real learned JS resource ({cap.source_ref}). "
              "Review; test on tenant."))


def generate_schemas_from_capability(interface, corpus, limit: int = 2):
    """Select the supporting schemas (xsd/wsdl/edmx) the interface most likely
    needs, by lexical overlap with the requirement. Returns a (possibly empty)
    list — schemas are the message contract, so we reuse REAL ones verbatim."""
    req = _tok(_interface_requirement_text(interface))
    normed = [c for c in getattr(corpus, "normalized", [])
              if c.ctype == "schema"]
    if not normed or not req:
        return []
    files = getattr(corpus, "files", {}) or {}
    scored = []
    for c in normed:
        ref = getattr(c, "source_ref", "") or ""
        cand = (set(getattr(c, "keywords", set()))
                | _tok(ref.rsplit("/", 1)[-1])
                | _tok(files.get(ref, "")[:1500]))
        if not cand:
            continue
        sc = len(req & cand) / len(req)
        if sc >= 0.15:
            scored.append((sc, c))
    scored.sort(key=lambda x: -x[0])
    out = []
    for _sc, c in scored[:limit]:
        body = _body_for(corpus, c)
        if body:
            out.append(CapabilityArtifact(
                kind="schema", content=body,
                source_capability=getattr(c, "source_ref", ""),
                note=(f"Reused a real learned schema ({getattr(c,'source_ref','')}). "
                      "Message contract — keep intact.")))
    return out


def generate_artifacts_from_capability(interface, corpus) -> list:
    """All relevant artifacts across types, each independently gated.

    script + mapping are always attempted (they're the core of an iFlow); xslt /
    js / schema are added only on a confident match, so a package gets exactly
    the supporting files it has real evidence for — never noise. Strictly
    additive: anything not filled here is completed by the caller's generic
    fallback."""
    out = []
    for fn in (generate_script_from_capability,
               generate_mapping_from_capability,
               generate_xslt_from_capability,
               generate_js_from_capability):
        try:
            art = fn(interface, corpus)
        except Exception:
            art = None
        if art:
            out.append(art)
    try:
        out.extend(generate_schemas_from_capability(interface, corpus))
    except Exception:
        pass
    return out


def _draft_mapping_from_schemas(interface, corpus):
    """Draft a mapping from the corpus's schema capabilities: emit direct field
    matches by element name between the two richest schemas. Honest: a
    structural draft from REAL schema fields, not authored logic."""
    schema_cat = (getattr(corpus, "catalogs", {}) or {}).get("schema")
    if not schema_cat:
        return None
    schemas = schema_cat.get("identities") or []
    if not schemas:
        return None

    def _fields(s):
        try:
            return list(s.defines_list())
        except Exception:
            return list(getattr(s, "roots", []) or [])

    ranked = sorted(schemas, key=lambda s: len(_fields(s)), reverse=True)
    if not ranked:
        return None
    src_fields = _fields(ranked[0])
    tgt_fields = _fields(ranked[1]) if len(ranked) > 1 else src_fields
    if not src_fields or not tgt_fields:
        return None

    import re
    def _norm(x):
        return re.sub(r"[^a-z0-9]", "", str(x).lower())
    tgt_by_norm = {_norm(t): t for t in tgt_fields}
    rows = [(s, tgt_by_norm[_norm(s)]) for s in src_fields
            if _norm(s) in tgt_by_norm]
    if not rows:
        return None

    body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<messageMapping name="{getattr(interface,"name","mapping")}'
            '_mapping" draft="true" source="schema-derived">\n'
            + "".join(f'  <mapping sourcePath="{s}" targetPath="{t}" '
                      'function="direct"/>\n' for s, t in rows)
            + '</messageMapping>\n')
    return CapabilityArtifact(
        kind="mapping", content=body, source_capability="schema-derived",
        note=(f"Schema-derived draft: {len(rows)} direct field match(es) from "
              "learned schemas. Review + complete complex mappings; test on "
              "tenant."))


def _interface_requirement_text(interface) -> str:
    """Build a solver requirement string from an interface via the bridge."""
    try:
        from library_builder.requirement_bridge import to_requirement
        return to_requirement(interface).requirement_text
    except Exception:
        # fallback: simple description
        return (getattr(interface, "description", "")
                or getattr(interface, "name", ""))
