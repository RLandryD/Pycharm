"""library_builder/field_spec.py

The FIELD-SPEC layer (architecture part 2, logic half).

From a solver solution + the bridge's CapabilityRequirement, derive the list of
EDITABLE FIELDS the workbench should render for the consultant to feed:

  * SOURCE/TARGET slots  — a VARIABLE number (one per sender + one per receiver
    the requirement implies), pre-filled with system/adapter where known.
  * EXTERNALIZED PARAMETERS — the {{...}} params from any matched iFlow/config
    capability, surfaced so they're set via parameter, NOT hardcoded.
  * STEP CONFIG fields   — key config a matched capability exposes.

Design principles (agreed):
  - Capabilities PROPOSE, the human DISPOSES. Every field is editable.
  - Pre-fill priority: requirement value → capability default → blank.
  - EDITED-VS-DEFAULT is tracked, so re-running the solver (e.g. requirements
    change mid-call) RE-PROPOSES defaults WITHOUT clobbering values the user
    already hand-edited. `merge_edits` enforces this.
  - Externalized over hardcoded: params are presented as parameters to set.

Pure logic, fully sandbox-testable. The Streamlit rendering that consumes these
specs is the workbench half (user tests visually).
"""
from __future__ import annotations

from dataclasses import dataclass, field as _field


@dataclass
class Field:
    key: str                         # stable id (for session-state binding)
    label: str                       # human label
    value: str = ""                  # current value (suggested or user-edited)
    suggested: str = ""              # the proposed default (for re-propose diff)
    source: str = "blank"            # requirement | capability | blank
    group: str = "general"           # source | target | parameter | config
    editable: bool = True
    user_edited: bool = False        # set True once the user changes it
    hint: str = ""                   # where this came from / what it's for

    def is_at_default(self) -> bool:
        return (not self.user_edited) and self.value == self.suggested


@dataclass
class FieldSpec:
    interface: str = ""
    fields: list = _field(default_factory=list)   # Field[]

    def by_group(self, group: str) -> list:
        return [f for f in self.fields if f.group == group]

    def unfilled(self) -> list:
        return [f for f in self.fields if not f.value]

    def as_dict(self) -> dict:
        return {"interface": self.interface,
                "fields": [vars(f) for f in self.fields]}


def _source_target_fields(slots: dict) -> list:
    """Variable N source/target fields, pre-filled from the requirement slots."""
    out = []
    for i, s in enumerate(slots.get("sources", []), 1):
        pre = s.get("system") or s.get("adapter") or ""
        out.append(Field(
            key=f"source_{i}", label=f"Source {i}"
            + (f" ({s['adapter']})" if s.get("adapter") else ""),
            value=pre, suggested=pre,
            source="requirement" if pre else "blank", group="source",
            hint=f"sender adapter={s.get('adapter','?')}, "
                 f"system={s.get('system','?')}"))
    for i, t in enumerate(slots.get("targets", []), 1):
        pre = t.get("system") or t.get("adapter") or ""
        out.append(Field(
            key=f"target_{i}", label=f"Target {i}"
            + (f" ({t['adapter']})" if t.get("adapter") else ""),
            value=pre, suggested=pre,
            source="requirement" if pre else "blank", group="target",
            hint=f"receiver adapter={t.get('adapter','?')}, "
                 f"system={t.get('system','?')}"))
    return out


def _externalized_param_fields(solution: dict, corpus) -> list:
    """Collect {{externalized}} params from the matched capabilities so they're
    set via parameter (not hardcoded). Looks up each matched capability's raw
    object for externalized_params / what_varies."""
    out, seen = [], set()
    norm_by_id = {}
    if corpus is not None:
        norm_by_id = {c.cap_id: c for c in getattr(corpus, "normalized", [])}
    for step in solution.get("steps", []):
        cap = norm_by_id.get(step.get("use"))
        if cap is None:
            continue
        raw = getattr(cap, "raw", None)
        params = list(getattr(raw, "externalized_params", []) or [])
        for p in params:
            name = p.split()[0] if p else ""
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(Field(
                key=f"param_{name}", label=name, value="", suggested="",
                source="blank", group="parameter", editable=True,
                hint=f"externalized parameter from {step.get('use','')} — "
                     "set via parameter, do not hardcode"))
    return out


def build_field_spec(solution: dict, corpus=None) -> FieldSpec:
    """Derive the editable field spec from a solver solution (which must carry
    `source_target_slots` — produced by requirement_bridge.solve_for)."""
    spec = FieldSpec(interface=solution.get("requirement", "")[:60])
    slots = solution.get("source_target_slots",
                         {"sources": [], "targets": []})
    spec.fields.extend(_source_target_fields(slots))
    spec.fields.extend(_externalized_param_fields(solution, corpus))
    return spec


def merge_edits(new_spec: FieldSpec, prior_values: dict) -> FieldSpec:
    """Re-propose without clobbering user edits. `prior_values` maps
    key -> {"value", "user_edited"} from the previous render (session state).
    For each field: if the user had edited it, KEEP their value; otherwise take
    the freshly-suggested default. This is the 'requirements change mid-call'
    safety — new suggestions flow in, hand-edited values are preserved.
    """
    for f in new_spec.fields:
        prior = prior_values.get(f.key)
        if prior and prior.get("user_edited"):
            f.value = prior.get("value", f.value)
            f.user_edited = True
            f.source = "user"
    return new_spec


def apply_user_value(spec: FieldSpec, key: str, value: str) -> FieldSpec:
    """Record a user edit (called when the workbench field changes)."""
    for f in spec.fields:
        if f.key == key:
            f.value = value
            f.user_edited = (value != f.suggested)
            f.source = "user" if f.user_edited else f.source
            break
    return spec
