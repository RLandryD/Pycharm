"""
scaffolder/schema_binding.py

Bridge between extractor.schema_matcher and the iFlow scaffolder: given an
interface's message type (name + namespace), find the canonical schema and
bundle it into a generated iFlow so the flow ships with the *real* message-type
schema instead of a placeholder.

Design: this is a POST-PROCESSING step over an already-built MinimalIFlowResult.
It only ADDS a resource file to result.files and returns a record of the match.
It does NOT touch the generator's signature, its dataclass, or the BPMN process
XML — so it cannot destabilize the runtime-critical generator. When no match is
found (or no index supplied) the result is left unchanged.

Two levels of "configured":
  Level 1 (here): the matched schema is bundled at src/main/resources/xsd/<root>.<ext>.
                  Harmless if unreferenced — it's a dormant resource until a step
                  points at it.
  Level 2 (later, validated on tenant): a schema-aware step (e.g. the XML
                  converter's XML_Schema_File_Path) is pointed at `reference_path`.
                  Left out here because wiring it into the BPMN is step-specific
                  and must be confirmed against a real import.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

from extractor.schema_matcher import SchemaIndex, match_for_interface

_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _safe_name(s: str) -> str:
    return _SAFE.sub("_", (s or "").strip()) or "Schema"


@dataclass
class SchemaBinding:
    matched_path: str        # source file in the canonical library
    resource_path: str       # bundle path: src/main/resources/xsd/<root>.<ext>
    reference_path: str      # in-iFlow ref: /xsd/<root>.<ext>  (for XML_Schema_File_Path)
    kind: str                # xsd | wsdl | edmx
    score: int
    reasons: list = field(default_factory=list)
    confident: bool = False  # score high enough to trust automatically


def bind_interface_schema(message_interface: str, namespace: str,
                          index: SchemaIndex, prefer_kind: str = "",
                          min_score: int = 3) -> SchemaBinding | None:
    """Resolve an interface's message type to a library schema. None if nothing
    scores at/above min_score."""
    m = match_for_interface(index, message_interface=message_interface,
                            namespace=namespace, prefer_kind=prefer_kind)
    if not m or m.score < min_score:
        return None
    src = m.entry.path
    root = _safe_name(Path(src).stem)
    ext = (Path(src).suffix.lstrip(".").lower() or "xsd")
    return SchemaBinding(
        matched_path=src,
        resource_path=f"src/main/resources/xsd/{root}.{ext}",
        reference_path=f"/xsd/{root}.{ext}",
        kind=m.entry.kind,
        score=m.score,
        reasons=list(m.reasons),
        confident=m.score >= 6,   # namespace exact + name exact
    )


def bundle_matched_schema(result, message_interface: str, namespace: str,
                          index: SchemaIndex, prefer_kind: str = "",
                          min_score: int = 3) -> SchemaBinding | None:
    """Enrich an already-built iFlow result with the matched schema (additive).

    Adds the schema's bytes to result.files at the bundle resource path and
    returns the SchemaBinding for traceability/reporting. Returns None (and
    leaves result untouched) when no confident-enough match exists.
    """
    binding = bind_interface_schema(message_interface, namespace, index,
                                    prefer_kind=prefer_kind, min_score=min_score)
    if binding is None:
        return None
    files = getattr(result, "files", None)
    if files is None:
        return None
    files[binding.resource_path] = Path(binding.matched_path).read_bytes()
    return binding
