"""library_builder/requirement_bridge.py

The INPUT → CAPABILITY bridge (architecture part 1).

Turns the structured inputs the workbench already parses —
  * RequirementResult  (intake.requirement_parser: Excel/Word/text requirements)
  * InterfaceRecord    (intake.sap_ma_parser / extractor.pi_extractor: MA Excel,
                        PI/PO interface inventory)
  * PiCapability       (library_builder.pi_capabilities: a read PI mapping)
— into a CAPABILITY REQUIREMENT the solver consumes (EVALUATE → FETCH → SELECT).

Before this, the inputs stopped at config shapes and never reached the capability
catalogs. This bridge closes that gap: a parsed requirement becomes a solver
query, so the learned capabilities (groovy / xslt / schema / mmap / iflw / props /
js / pi) are actually matched against what the client asked for.

It does NOT guess hidden intent — it translates the fields that are present into
explicit needs, and carries source/target + adapters through as structured hints
so the downstream field-spec layer (part 2) can pre-fill them. Pure translation,
fully sandbox-testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field as _field


@dataclass
class CapabilityRequirement:
    """A normalized, solver-ready statement of what an interface needs, plus the
    structured hints the field-spec layer (part 2) uses to pre-fill the UI."""
    name: str = ""
    requirement_text: str = ""          # the natural-language string for EVALUATE
    senders: list = _field(default_factory=list)    # [{system, adapter}]
    receivers: list = _field(default_factory=list)  # [{system, adapter}]
    needs_mapping: bool = False
    mapping_program: str = ""
    message_format: str = ""
    is_async: bool = False
    scheduler: str = ""
    has_bpm: bool = False
    notes: list = _field(default_factory=list)
    source_input: str = ""              # which parser produced this
    confidence: float = 0.0

    def source_target_slots(self) -> dict:
        """The variable number of source/target slots for the UI (part 2):
        one per sender + one per receiver, pre-filled where known."""
        return {
            "sources": [{"role": "sender", "system": s.get("system", ""),
                         "adapter": s.get("adapter", ""), "value": ""}
                        for s in self.senders],
            "targets": [{"role": "receiver", "system": r.get("system", ""),
                         "adapter": r.get("adapter", ""), "value": ""}
                        for r in self.receivers],
        }


def _verbs_from_fields(req: CapabilityRequirement) -> list:
    """Turn structured flags into the capability-intent words EVALUATE keys on."""
    verbs = []
    if (req.needs_mapping or req.mapping_program) and not req.mapping_program:
        verbs.append("mapping")     # generic; if a named program exists it's
                                    # added explicitly in _compose_text instead
    if req.message_format.upper() in ("JSON",):
        verbs.append("json")
    if req.message_format.upper() in ("XML",):
        verbs.append("xml")
    if req.message_format.upper() in ("CSV", "FLAT", "EDI"):
        verbs.append("csv")
    if req.scheduler:
        verbs.append("scheduled")
    if req.has_bpm:
        verbs.append("process orchestration")
    return verbs


def _compose_text(req: CapabilityRequirement) -> str:
    """Build the natural-language requirement string the solver's EVALUATE reads.
    Reads as: '<sender adapter(s)> to <receiver adapter(s)> [with mapping]
    [<format>] [scheduled] ...'. Only includes what the input actually states."""
    s = "/".join(sorted({x["adapter"] for x in req.senders if x.get("adapter")})) \
        or ("scheduled" if req.scheduler else "")
    r = "/".join(sorted({x["adapter"] for x in req.receivers if x.get("adapter")}))
    parts = []
    if s and r:
        parts.append(f"{s} to {r} integration")
    elif s:
        parts.append(f"{s} inbound integration")
    elif r:
        parts.append(f"send to {r}")
    parts.extend(_verbs_from_fields(req))
    if req.is_async:
        parts.append("asynchronous")
    if req.mapping_program:
        parts.append(f"mapping {req.mapping_program}")
    base = ", ".join(p for p in parts if p)
    # include the human description if present (richer EVALUATE signal)
    if req.requirement_text and req.requirement_text not in base:
        base = (base + ". " + req.requirement_text).strip(". ")
    return base or req.name


def from_requirement_result(rr) -> CapabilityRequirement:
    """intake.requirement_parser.RequirementResult -> CapabilityRequirement."""
    req = CapabilityRequirement(
        name=getattr(rr, "name", ""),
        senders=[{"system": getattr(rr, "sender_system", ""),
                  "adapter": getattr(rr, "sender_adapter", "")}],
        receivers=[{"system": getattr(rr, "receiver_system", ""),
                    "adapter": getattr(rr, "receiver_adapter", "")}],
        needs_mapping=bool(getattr(rr, "mapping_program", "")),
        mapping_program=getattr(rr, "mapping_program", "") or "",
        message_format=getattr(rr, "message_format", "") or "",
        is_async=bool(getattr(rr, "is_async", False)),
        scheduler=getattr(rr, "scheduler_cron", "") or "",
        has_bpm=bool(getattr(rr, "business_process", "")),
        notes=list(getattr(rr, "needs_review", []) or []),
        source_input="requirement",
        confidence=float(getattr(rr, "confidence", 0.0) or 0.0),
    )
    req.requirement_text = getattr(rr, "description", "") or ""
    req.requirement_text = _compose_text(req)
    return req


def from_interface_record(ir) -> CapabilityRequirement:
    """extractor.InterfaceRecord (MA Excel / PI inventory) -> requirement."""
    req = CapabilityRequirement(
        name=getattr(ir, "name", ""),
        senders=[{"system": getattr(ir, "sender_system", ""),
                  "adapter": getattr(ir, "sender_adapter", "")}],
        receivers=[{"system": getattr(ir, "receiver_system", ""),
                    "adapter": getattr(ir, "receiver_adapter", "")}],
        needs_mapping=bool(getattr(ir, "mapping_program", None)),
        mapping_program=getattr(ir, "mapping_program", None) or "",
        has_bpm=bool(getattr(ir, "has_bpm", False)),
        notes=([f"channels={getattr(ir, 'channel_count', 1)}"]
               + (["multi-mapping"] if getattr(ir, "has_multi_mapping", False)
                  else [])),
        source_input="migration_assessment",
    )
    req.requirement_text = getattr(ir, "description", "") or ""
    req.requirement_text = _compose_text(req)
    return req


def from_pi_capability(pc) -> CapabilityRequirement:
    """library_builder.pi_capabilities.PiCapability -> requirement (what the PI
    artifact needs rebuilt in CPI; carries the CPI target as a note)."""
    req = CapabilityRequirement(
        name=getattr(pc, "name", ""),
        needs_mapping=True,
        notes=[f"pi_type={getattr(pc, 'pi_type', '')}",
               f"build_in_cpi={getattr(pc, 'cpi_target', '')}"],
        source_input="pi_migration",
    )
    req.requirement_text = (getattr(pc, "purpose", "")
                            + " | " + getattr(pc, "cpi_target", "")).strip(" |")
    return req


def to_requirement(obj) -> CapabilityRequirement:
    """Dispatch any known input object to a CapabilityRequirement."""
    cls = type(obj).__name__
    if cls == "RequirementResult":
        return from_requirement_result(obj)
    if cls == "InterfaceRecord":
        return from_interface_record(obj)
    if cls == "PiCapability":
        return from_pi_capability(obj)
    # last resort: treat any object exposing sender/receiver like an interface
    if hasattr(obj, "sender_adapter") and hasattr(obj, "receiver_adapter"):
        return from_interface_record(obj)
    raise TypeError(f"don't know how to bridge {cls} to a CapabilityRequirement")


def solve_for(obj, corpus) -> dict:
    """End-to-end: input object -> CapabilityRequirement -> solver solution.
    `corpus` is a corpus_pipeline.Corpus (has .solve). Returns the solution
    summary plus the source/target slots for the field-spec layer (part 2)."""
    from library_builder.solver import solution_summary
    req = to_requirement(obj)
    sol = corpus.solve(req.requirement_text)
    summary = solution_summary(sol)
    summary["requirement"] = req.requirement_text
    summary["source_target_slots"] = req.source_target_slots()
    summary["input_source"] = req.source_input
    return summary
