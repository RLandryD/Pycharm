"""
scaffolder/value_mapping_generator.py

Generates SAP CPI Value Mapping artifacts (.xml in the value-mapping format
CPI imports) from collected source->target value pairs.

PI/PO Value Mappings (fixed-value tables, agency/scheme lookups) migrate to
CPI Value Mappings. Today the workbench only references them as comments.
This builds the actual importable artifact.

A CPI Value Mapping groups entries by (sourceAgency, sourceScheme) ->
(targetAgency, targetScheme), each with a list of value pairs. This matches
the PI ES Repository value-mapping structure so migrated lookups behave the
same.

Verified: structural (XML well-formedness + round-trip). NOT imported into a
tenant — the element layout follows the documented CPI value-mapping schema
but should be confirmed by importing into Integration Suite.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field


@dataclass
class ValueMapEntry:
    source_value: str
    target_value: str


@dataclass
class ValueMapGroup:
    """One agency/scheme bucket of value pairs."""
    source_agency: str
    source_scheme: str
    target_agency: str
    target_scheme: str
    entries: list[ValueMapEntry] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.source_agency}:{self.source_scheme}->{self.target_agency}:{self.target_scheme}"


@dataclass
class ValueMappingArtifact:
    name: str
    groups: list[ValueMapGroup] = field(default_factory=list)

    def total_entries(self) -> int:
        return sum(len(g.entries) for g in self.groups)


def build_from_pairs(
    name: str,
    pairs: list[tuple[str, str]],
    source_agency: str = "SourceSystem",
    source_scheme: str = "SourceScheme",
    target_agency: str = "TargetSystem",
    target_scheme: str = "TargetScheme",
) -> ValueMappingArtifact:
    """Build a single-group value mapping from a flat list of (src, tgt) pairs.

    The common case: a consultant has a lookup table (country codes,
    unit-of-measure, payment terms) and wants it as a CPI artifact.
    """
    group = ValueMapGroup(source_agency, source_scheme, target_agency, target_scheme)
    for src, tgt in pairs:
        group.entries.append(ValueMapEntry(str(src), str(tgt)))
    return ValueMappingArtifact(name=name, groups=[group])


def _entry_xml(src_agency, src_scheme, tgt_agency, tgt_scheme, e: ValueMapEntry) -> str:
    """One value-mapping entry in CPI import format."""
    return (
        "    <vm:valueMapping>\n"
        "        <vm:sourceId>\n"
        f"            <vm:agency>{html.escape(src_agency)}</vm:agency>\n"
        f"            <vm:scheme>{html.escape(src_scheme)}</vm:scheme>\n"
        f"            <vm:value>{html.escape(e.source_value)}</vm:value>\n"
        "        </vm:sourceId>\n"
        "        <vm:targetId>\n"
        f"            <vm:agency>{html.escape(tgt_agency)}</vm:agency>\n"
        f"            <vm:scheme>{html.escape(tgt_scheme)}</vm:scheme>\n"
        f"            <vm:value>{html.escape(e.target_value)}</vm:value>\n"
        "        </vm:targetId>\n"
        "    </vm:valueMapping>\n"
    )


def render_artifact(artifact: ValueMappingArtifact) -> str:
    """Render the full importable Value Mapping XML."""
    rows = ""
    for g in artifact.groups:
        for e in g.entries:
            rows += _entry_xml(g.source_agency, g.source_scheme,
                               g.target_agency, g.target_scheme, e)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<vm:valueMappings xmlns:vm="http://sap.com/xi/BASIS/ValueMapping" '
        f'name="{html.escape(artifact.name)}">\n'
        f"{rows}"
        "</vm:valueMappings>\n"
    )


def render_descriptor(artifact: ValueMappingArtifact) -> dict:
    return {
        "name": artifact.name,
        "group_count": len(artifact.groups),
        "total_entries": artifact.total_entries(),
        "groups": [
            {"key": g.key, "entries": len(g.entries)} for g in artifact.groups
        ],
    }
