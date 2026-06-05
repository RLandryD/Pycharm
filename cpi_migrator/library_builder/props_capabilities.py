"""library_builder/props_capabilities.py

Parameter / property capability extractor — the iFlow's externalized
CONFIGURATION SURFACE. Unlike the code engines (groovy/js/xslt), props are not
behavior; they are config, so the model is structural (like schema identity).

Two paired artifacts make one config capability:
  * .propdef  — XML CONTRACT: the parameters an iFlow exposes, each with
                name / type / isRequired / constraint / description.
  * .prop     — INI-style key=value VALUES for those parameters (environment-
                specific; '#'-comment timestamps ignored).

Identity = the SET of parameter names (the config surface). Locked principle
(inherited from the retired kv_engine, confirmed against real specimens): two
configs with the SAME key set are the SAME config solution — the key structure
is what's reusable; the values are environment-specific EXAMPLES, not identity.
So:
  * what-varies = the values (swap per environment)
  * what's stable = the parameter names + types + required flags (the contract)

A .prop and .propdef with the same parameter names are recognized as a PAIR
(contract + its example values). Catalogued by config-surface identity, with
dedup by key set.

Grounded in the real specimens (parameters.prop ↔ parameters.propdef: same 4
keys jmsQueueName/userRole/Address/transactionHandling). Pure config, no SAP
runtime, no tenant — fully sandbox-testable.

SCOPE (honest): targets iFlow config — .prop (values) and .propdef (contract).
Generic Java/OSGi .properties BUILD files (e.g. build.properties with
bin.includes/source..) are OUT of scope — different artifact, different purpose.
Real .prop specimens are flat key=value (no line-continuation), so the parser
is deliberately simple; if java-.properties line-continuation ever appears in a
real iFlow .prop, extend _parse_prop then (don't pre-build for an unseen case).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field as _field


@dataclass
class PropParameter:
    name: str
    type: str = ""
    required: bool = False
    constraint: str = ""
    description: str = ""
    value: str = ""             # from the paired .prop, if present


@dataclass
class PropsCapability:
    name: str
    kind: str                          # propdef | prop
    parameters: list = _field(default_factory=list)   # PropParameter[]
    surface_hash: str = ""             # identity: hash of the key set
    has_contract: bool = False         # propdef seen
    has_values: bool = False           # prop seen
    # facets (config flavor)
    purpose: str = ""
    what_varies: list = _field(default_factory=list)   # the values (env-specific)
    shape: str = ""
    when_to_use: str = ""

    def keys(self) -> set:
        return {p.name for p in self.parameters}

    def signature(self) -> str:
        return f"props:{len(self.parameters)}params"


def _surface_hash(names) -> str:
    """Identity = the sorted set of parameter names (the config surface)."""
    key = "|".join(sorted(set(names)))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _parse_propdef(text: str) -> list:
    """Parse the propdef XML contract into PropParameters."""
    params = []
    for block in re.findall(r"<parameter>(.*?)</parameter>", text, re.S):
        name = re.search(r"<name>([^<]*)</name>", block)
        if not name or not name.group(1).strip():
            continue
        typ = re.search(r"<type>([^<]*)</type>", block)
        req = re.search(r"<isRequired>([^<]*)</isRequired>", block)
        con = re.search(r"<constraint>([^<]*)</constraint>", block)
        desc = re.search(r"<description>([^<]*)</description>", block)
        params.append(PropParameter(
            name=name.group(1).strip(),
            type=(typ.group(1).strip() if typ else ""),
            required=(req.group(1).strip().lower() == "true" if req else False),
            constraint=(con.group(1).strip() if con else ""),
            description=(desc.group(1).strip() if desc else ""),
        ))
    return params


def _parse_prop(text: str) -> list:
    """Parse the INI-style .prop values (key=value), ignoring # comments."""
    params = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            if k.strip():
                params.append(PropParameter(name=k.strip(), value=v.strip()))
    return params


def extract_capability(name: str, text: str) -> PropsCapability:
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    if ext == "propdef" or "<parameter>" in text:
        params = _parse_propdef(text)
        cap = PropsCapability(name=name, kind="propdef", parameters=params,
                              has_contract=True)
        cap.purpose = "parameter contract (defines iFlow config surface)"
        cap.when_to_use = "reuse this parameter set as an iFlow's config contract"
        cap.what_varies = [p.name for p in params]   # values supplied per-env
    else:
        params = _parse_prop(text)
        cap = PropsCapability(name=name, kind="prop", parameters=params,
                              has_values=True)
        cap.purpose = "parameter values (environment-specific config)"
        cap.when_to_use = "example values for this config surface (swap per env)"
        cap.what_varies = [p.value for p in params if p.value]
    cap.surface_hash = _surface_hash(p.name for p in params)
    types = sorted({p.type for p in params if p.type})
    reqd = [p.name for p in params if p.required]
    cap.shape = (f"{cap.kind}: {len(params)} params"
                 + (f"; types={','.join(types)}" if types else "")
                 + (f"; required={len(reqd)}" if reqd else ""))
    return cap


def pair_configs(caps: list) -> list:
    """Recognize prop↔propdef pairs (same parameter-name set) and merge values
    into the contract. Returns a list of merged/standalone config capabilities."""
    by_surface = {}
    for c in caps:
        by_surface.setdefault(c.surface_hash, []).append(c)
    merged = []
    for surface, group in by_surface.items():
        contract = next((c for c in group if c.has_contract), None)
        values = next((c for c in group if c.has_values), None)
        if contract and values:
            vmap = {p.name: p.value for p in values.parameters}
            for p in contract.parameters:
                p.value = vmap.get(p.name, "")
            contract.has_values = True
            contract.name = f"{contract.name} (+values)"
            merged.append(contract)
        else:
            merged.extend(group)
    return merged


def build_catalog(corpus: dict) -> dict:
    caps = [extract_capability(n, t) for n, t in corpus.items()]
    paired = pair_configs(caps)
    # dedup by config surface (same key set = same config solution)
    by_surface = {}
    for c in paired:
        by_surface.setdefault(c.surface_hash, []).append(c.name)
    dups = {h: names for h, names in by_surface.items() if len(names) > 1}
    index = {}
    for c in paired:
        index.setdefault(c.signature(), []).append(c.name)
    return {
        "capabilities": paired,
        "raw_count": len(caps),
        "count": len(paired),
        "duplicate_surfaces": dups,
        "index": index,
    }
