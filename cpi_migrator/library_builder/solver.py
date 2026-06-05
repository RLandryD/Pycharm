"""library_builder/solver.py

The reasoning layer (architecture "B"): turn the per-type capability catalogs
into a PROBLEM SOLVER, not a snippet library. Formalizes what a consultant does
when asked "solve this in CPI":

    requirement
        -> EVALUATE  : decompose into needed capability intents
        -> FETCH     : match each need against the catalogs
        -> SELECT    : pick the best-fitting capability per need
        -> ADAPT     : fill the "what-varies" with the requirement's specifics
        -> COMPOSE   : assemble into a coherent solution outline

Design truth discovered from the real catalogs: the four catalogs have DIFFERENT
shapes (groovy/xslt = behavior capabilities; schema = identity-for-reuse; mmap =
per-field). So the solver works over a NORMALIZED view — `NormalizedCapability`
— that each catalog maps into. This is the single contract the solver consumes;
new types just provide a normalizer.

HONEST BOUNDARY (held throughout): the sandbox can prove the solver FETCHES
sensibly, ADAPTS the varying parts, and COMPOSES coherently. It CANNOT prove the
SELECT always picks the objectively best solution (that has judgment) nor that
the composed solution runs in SAP (tenant confirms). Those are flagged, not
hidden. `confidence` on each step reflects this.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as _field


# ───────────────────────── normalized capability ──────────────────────────
@dataclass
class NormalizedCapability:
    """The single shape the solver reasons over. Every catalog maps into this."""
    cap_id: str                     # unique: "<type>:<name>[:<sub>]"
    ctype: str                      # groovy | xslt | schema | mmap
    intent: str                     # what it does, normalized (the FETCH key)
    keywords: set = _field(default_factory=set)   # matchable terms
    varies: list = _field(default_factory=list)   # adaptable params
    when_to_use: str = ""
    weight: int = 0                 # complexity (directional)
    needs_binding: bool = False     # has a SAP binding the tenant must validate
    source_ref: str = ""            # the underlying file/field
    raw: object = None              # the original catalog object (for ADAPT)


_KW = re.compile(r"[a-z0-9]+")

# fragments from canned purpose phrases that carry no discriminating meaning
# (e.g. "write-back" -> "back"). Filtered so they don't dilute FETCH.
_NOISE_KW = {"back", "the", "general", "passthrough", "handling", "manipulation",
             "payloads", "via"}


def _kw(*parts) -> set:
    out = set()
    for p in parts:
        if not p:
            continue
        for tok in _KW.findall(str(p).lower()):
            if len(tok) > 2:
                out.add(tok)
    return out


def normalize(catalog: dict, ctype: str) -> list:
    """Map any catalog into a list of NormalizedCapability."""
    caps = []
    if ctype == "groovy":
        for c in catalog["capabilities"]:
            caps.append(NormalizedCapability(
                cap_id=f"groovy:{c.name}", ctype="groovy",
                intent=c.purpose,
                keywords=(_kw(c.purpose, c.when_to_use, *c.portable_ops,
                              *c.what_varies)
                          | _kw(*getattr(c, "op_keywords", []))) - _NOISE_KW,
                varies=list(c.what_varies), when_to_use=c.when_to_use,
                weight=c.weight, needs_binding=bool(c.bindings),
                source_ref=c.name, raw=c))
    elif ctype == "xslt":
        for c in catalog["capabilities"]:
            caps.append(NormalizedCapability(
                cap_id=f"xslt:{c.name}", ctype="xslt",
                intent=c.purpose,
                keywords=_kw(c.purpose, c.when_to_use, c.output_method,
                             *c.what_varies),
                varies=list(c.what_varies), when_to_use=c.when_to_use,
                weight=c.weight, needs_binding=bool(c.extension_calls),
                source_ref=c.name, raw=c))
    elif ctype == "js":
        for c in catalog["capabilities"]:
            caps.append(NormalizedCapability(
                cap_id=f"js:{c.name}", ctype="js",
                intent=c.purpose,
                keywords=(_kw(c.purpose, c.when_to_use, *c.portable_ops,
                              *c.what_varies)
                          | _kw(*getattr(c, "op_keywords", []))) - _NOISE_KW,
                varies=list(c.what_varies), when_to_use=c.when_to_use,
                weight=c.weight, needs_binding=bool(c.bindings),
                source_ref=c.name, raw=c))
    elif ctype == "props":
        for c in catalog["capabilities"]:
            pnames = [p.name for p in c.parameters]
            ptypes = [p.type for p in c.parameters if p.type]
            caps.append(NormalizedCapability(
                cap_id=f"props:{c.name}", ctype="props",
                intent=c.purpose,
                keywords=(_kw(c.purpose, c.when_to_use, *pnames, *ptypes)
                          - _NOISE_KW),
                varies=list(c.what_varies), when_to_use=c.when_to_use,
                weight=len(c.parameters), needs_binding=False,
                source_ref=c.name, raw=c))
    elif ctype == "iflw":
        for c in catalog["capabilities"]:
            caps.append(NormalizedCapability(
                cap_id=f"iflw:{c.name}", ctype="iflw",
                intent=c.purpose,
                keywords=(_kw(c.purpose, c.when_to_use, *c.op_keywords,
                              *getattr(c, "sender_adapters", []),
                              *getattr(c, "receiver_adapters", [])) - _NOISE_KW),
                varies=list(c.what_varies), when_to_use=c.when_to_use,
                weight=c.weight, needs_binding=True,   # iFlows deploy to tenant
                source_ref=c.name, raw=c))
    elif ctype == "pi":
        for c in catalog["capabilities"]:
            caps.append(NormalizedCapability(
                cap_id=f"pi:{c.name}", ctype="pi",
                intent=c.purpose,
                keywords=(_kw(c.purpose, c.when_to_use, c.cpi_target,
                              *c.op_keywords, *getattr(c, "udf_methods", []))
                          - _NOISE_KW),
                varies=list(c.what_varies), when_to_use=c.when_to_use,
                weight=c.weight, needs_binding=True,   # migration → tenant build
                source_ref=c.name, raw=c))
    elif ctype == "schema":
        for i in catalog["identities"]:
            if not i.well_formed:
                continue            # damaged schemas are not reusable
            caps.append(NormalizedCapability(
                cap_id=f"schema:{i.name}", ctype="schema",
                intent=f"reuse {i.kind} structure",
                keywords=_kw(i.target_namespace, *i.roots[:20],
                             *i.types[:20], *i.entities[:20]),
                varies=[], when_to_use=f"reuse existing {i.kind} (never generate)",
                weight=0, needs_binding=False, source_ref=i.name, raw=i))
    elif ctype == "mmap":
        for (fname, c) in catalog["capabilities"]:
            caps.append(NormalizedCapability(
                cap_id=f"mmap:{fname}:{c.target_field}", ctype="mmap",
                intent=f"{c.category} mapping",
                keywords=_kw(c.category, c.target_field, *c.functions,
                             *c.sources),
                varies=list(c.sources) + list(c.constants),
                when_to_use=f"map a field via {c.category}",
                weight=c.weight, needs_binding=False,
                source_ref=f"{fname}:{c.target_field}", raw=c))
    return caps


# ───────────────────────────── the solver ─────────────────────────────────
@dataclass
class Need:
    text: str                       # the requirement fragment
    keywords: set = _field(default_factory=set)
    hint_type: str = ""             # optional preferred ctype


@dataclass
class Match:
    need: Need
    capability: NormalizedCapability
    score: float
    why: str = ""


@dataclass
class Solution:
    requirement: str
    matches: list = _field(default_factory=list)   # selected Match per need
    unmet: list = _field(default_factory=list)     # needs with no good match
    needs_tenant_test: bool = False
    confidence: str = "reasoned"    # reasoned (sandbox) — tenant confirms


# verbs/keywords that signal a distinct capability need.
# Multi-word phrases included (audit fix: "look up" was missed because only the
# single token "lookup" was matched). Phrases are checked as substrings of the
# clause text, so spaced variants map to the same normalized intent.
_INTENT_SIGNALS = {
    "look up": "lookup", "lookup": "lookup", "enrich": "lookup",
    "value map": "lookup", "value-map": "lookup",
    "parse": "parse", "read": "parse",
    "json": "json", "xml": "xml", "csv": "csv", "edi": "csv",
    "transform": "transform", "convert": "transform", "map ": "mapping",
    "mapping": "mapping",
    "date": "date", "format": "format",
    "log": "log", "attach": "attach", "attachment": "attach",
    "write back": "write", "set property": "property", "property": "property",
    "set header": "header", "header": "header",
    "validate": "validate", "validation": "validate",
    "split": "split", "filter": "filter", "sort": "sort",
    "concat": "string", "replace": "string", "substring": "string",
    "schema": "schema", "structure": "schema", "wsdl": "schema",
    "xsd": "schema",
}


def evaluate(requirement: str) -> list:
    """EVALUATE: decompose a requirement into discrete capability needs.
    Splits on clause boundaries, then tags each clause with the intent
    signals it contains. Honest: this is keyword-driven decomposition — a real
    first layer, but the deep semantic split is where human/LLM judgment helps.
    """
    # split into clauses on conjunctions / punctuation
    clauses = re.split(r"\b(?:then|and then|,|;|\.|and )\b", requirement.lower())
    needs = []
    for cl in clauses:
        cl = cl.strip()
        if len(cl) < 4:
            continue
        kws = _kw(cl)
        # add normalized intent signals found in the clause
        for sig, norm in _INTENT_SIGNALS.items():
            if sig in cl:
                kws.add(norm)
        if kws:
            needs.append(Need(text=cl, keywords=kws))
    # de-dup near-identical needs
    seen, out = set(), []
    for n in needs:
        key = frozenset(n.keywords)
        if key and key not in seen:
            seen.add(key)
            out.append(n)
    return out


def _idf(normalized: list) -> dict:
    """Inverse document frequency per keyword, computed FROM the corpus itself
    (not tuned to any query): a term in few capabilities is more discriminating
    than a common one. Adapts to whatever the corpus contains, so it scales to
    the full 34k without baked-in bias."""
    import math
    n = max(1, len(normalized))
    df = {}
    for c in normalized:
        for k in c.keywords:
            df[k] = df.get(k, 0) + 1
    return {k: math.log(1 + n / v) for k, v in df.items()}


def fetch(need: Need, normalized: list, idf: dict = None) -> list:
    """FETCH: score every capability against the need by IDF-weighted keyword
    overlap + intent/when-to-use match. IDF (corpus-derived) makes a rare,
    specific term (e.g. 'substring') count more than a common one (e.g.
    'payload'), improving precision without query-specific tuning. Returns
    ranked Matches (best first)."""
    if idf is None:
        idf = _idf(normalized)
    matches = []
    for cap in normalized:
        overlap = need.keywords & cap.keywords
        if not overlap:
            continue
        # IDF-weighted overlap, normalized by the need's own IDF mass
        need_mass = sum(idf.get(k, 1.0) for k in need.keywords) or 1.0
        score = sum(idf.get(k, 1.0) for k in overlap) / need_mass
        if any(k in cap.intent.lower() or k in cap.when_to_use.lower()
               for k in need.keywords):
            score += 0.25
        matches.append(Match(need=need, capability=cap, score=round(score, 3),
                             why="overlap: " + ",".join(sorted(overlap))))
    matches.sort(key=lambda m: m.score, reverse=True)
    return matches


def select(matches: list):
    """SELECT: pick the best-fitting capability. Honest: 'best' here = highest
    keyword/intent score, lightly preferring simpler (lower-weight) capabilities
    on ties. True best-fit has judgment the tenant/user confirms."""
    if not matches:
        return None
    top = matches[0].score
    tied = [m for m in matches if m.score >= top - 1e-9]
    # tie-break: prefer lower weight (simpler), then no-binding (more portable)
    tied.sort(key=lambda m: (m.capability.weight, m.capability.needs_binding))
    return tied[0]


def adapt(match: Match, requirement: str) -> dict:
    """ADAPT: fill the capability's 'what-varies' with specifics pulled from the
    requirement where detectable. Returns an adaptation plan (what to change).
    Honest: detects obvious substitutions (quoted names, field-like tokens);
    the rest are listed as 'to confirm' for the user."""
    plan = {"capability": match.capability.cap_id, "substitutions": {},
            "to_confirm": []}
    # candidate concrete values in the requirement: quoted strings, CamelCase,
    # UPPER_SNAKE tokens (typical field/property names)
    concrete = set(re.findall(r"'([^']+)'|\"([^\"]+)\"", requirement))
    concrete = {a or b for a, b in concrete}
    concrete |= set(re.findall(r"\b([A-Z][A-Za-z0-9]{3,}|[A-Z_]{4,})\b",
                               requirement))
    varies = list(match.capability.varies)
    for v in varies:
        # if a concrete value looks related, propose it; else flag to confirm
        hit = next((c for c in concrete if c.lower() in v.lower()
                    or v.lower() in c.lower()), None)
        if hit:
            plan["substitutions"][v] = hit
        else:
            plan["to_confirm"].append(v)
    return plan


def solve(requirement: str, normalized: list) -> Solution:
    """Full pipeline: EVALUATE -> FETCH -> SELECT -> ADAPT -> COMPOSE."""
    sol = Solution(requirement=requirement)
    needs = evaluate(requirement)
    idf = _idf(normalized)            # corpus-derived, computed once
    for need in needs:
        ranked = fetch(need, normalized, idf)
        best = select(ranked)
        if best is None:
            sol.unmet.append(need)
            continue
        best.why += f" | adapt: {adapt(best, requirement)}"
        sol.matches.append(best)
        if best.capability.needs_binding:
            sol.needs_tenant_test = True
    # COMPOSE: order matches by a natural pipeline (read/parse -> transform ->
    # write/emit) using ctype + intent hints; keep simple + explainable
    def stage(m):
        i = m.capability.intent.lower()
        if any(w in i for w in ("parse", "read", "reuse")):
            return 0
        if any(w in i for w in ("write", "emit", "log", "attach")):
            return 2
        return 1
    sol.matches.sort(key=stage)
    return sol


def solution_summary(sol: Solution) -> dict:
    return {
        "requirement": sol.requirement,
        "steps": [{"need": m.need.text, "use": m.capability.cap_id,
                   "score": m.score, "ctype": m.capability.ctype}
                  for m in sol.matches],
        "unmet": [n.text for n in sol.unmet],
        "needs_tenant_test": sol.needs_tenant_test,
        "confidence": sol.confidence,
    }
