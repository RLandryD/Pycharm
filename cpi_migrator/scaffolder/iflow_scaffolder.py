"""
scaffolder/iflow_scaffolder.py
Generates CPI iFlow XML stubs from MigrationAssessment objects using
Jinja2 templates. Optionally enriches stubs with ResolvedDestination
data (recommended adapters, Hub matches, migration hints).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from analyzer.complexity_analyzer import MigrationAssessment

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text).strip()
    slug = re.sub(r"[\s_-]+", "_", slug)
    return slug[:80]


class IFlowScaffolder:

    def __init__(self, output_dir: str, templates_dir: str = None):
        self.output_dir = Path(output_dir) / "iflows"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # The skeleton template is only needed for the legacy fallback path.
        # Wired mode (default) doesn't use it, so templates_dir is optional.
        self.env = None
        self.template = None
        if templates_dir:
            self.env = Environment(
                loader=FileSystemLoader(templates_dir),
                autoescape=select_autoescape(disabled_extensions=("xml", "j2")),
                trim_blocks=True,
                lstrip_blocks=True,
            )
            try:
                self.template = self.env.get_template("iflow_base.xml.j2")
            except Exception:
                self.template = None

    def scaffold(
        self,
        assessment: MigrationAssessment,
        resolved: Optional[object] = None,   # ResolvedDestination | None
        wired: bool = True,                  # use the validated generator
        shape: str = "timer",                # "timer" (default) | "minimal"
    ) -> Path:
        iface    = assessment.interface
        iflow_id = _slugify(iface.name) or f"iflow_{iface.id}"

        # Default shape = the proven, self-contained Timer → CM → CM → End flow
        # (validated end-to-end on the tenant: imports, deploys, STARTED, runs
        # once, COMPLETED). It has NO sender/receiver and NO message flow, so it
        # carries no dependency on a standard package or endpoint — unlike the
        # clone-and-adapt output, whose baked-in endpoints made the artifacts
        # impossible to edit or detach. CM1 self-documents the interface; CM2
        # writes a marker ack. shape="minimal" keeps the older Start→End path
        # available as an escape hatch.
        if wired:
            try:
                from scaffolder.minimal_iflow import (
                    generate_timer_interface_iflow, generate_minimal_iflow)
                if shape == "minimal":
                    result = generate_minimal_iflow(iface.name, iflow_id)
                else:
                    result = generate_timer_interface_iflow(
                        iface.name, iflow_id,
                        properties=self._interface_properties(iface),
                        note=f"Scaffolded from PI/PO interface '{iface.name}'")
                out_path = self.output_dir / f"{result.iflow_id}.iflw"
                out_path.write_text(result.iflw_xml, encoding="utf-8")
                # Stash the manifest + .project so the packager uses the
                # validated ones (not the uploader's hand-built manifest).
                meta_dir = self.output_dir / f"{result.iflow_id}__meta"
                meta_dir.mkdir(parents=True, exist_ok=True)
                (meta_dir / "MANIFEST.MF").write_text(result.manifest, encoding="utf-8")
                (meta_dir / ".project").write_text(result.project_xml, encoding="utf-8")
                logger.info("Generated CPI-valid iFlow (%s) → %s", shape, out_path.name)
                return out_path
            except Exception as exc:
                logger.warning("minimal_iflow generation failed for %s (%s); "
                               "falling back to skeleton", iface.name, exc)

        return self._scaffold_skeleton(assessment, resolved, iflow_id)

    @staticmethod
    def _interface_properties(iface):
        """Channel fields → CM1 exchange properties, so the deployed flow
        self-documents what PI/PO interface it represents (visible in the
        message trace). Defensive getattr — fields vary by extractor."""
        fields = [
            ("SenderAdapter",   getattr(iface, "sender_adapter", "")),
            ("ReceiverAdapter", getattr(iface, "receiver_adapter", "")),
            ("SenderSystem",    getattr(iface, "sender_system", "")),
            ("ReceiverSystem",  getattr(iface, "receiver_system", "")),
            ("Namespace",       getattr(iface, "namespace", "")),
        ]
        return [(k, v) for k, v in fields if v]

    @staticmethod
    def likely_needs_sender(iface) -> bool:
        """Heuristic: does this interface look sender-initiated/push (sync HTTP,
        SOAP, IDoc/proxy, REST, AS2)? The timer shape can't represent those —
        it's outbound/scheduled only — so the UI can pre-flag them for the
        (still-pending) sender path. Default toggle stays ON regardless; this is
        only a hint."""
        push = {"IDOC", "SOAP", "HTTPS", "HTTP", "REST", "AS2", "ODATA", "XI", "PROXY"}
        sa = str(getattr(iface, "sender_adapter", "") or "").upper()
        return any(p in sa for p in push)

    def _scaffold_skeleton(self, assessment, resolved, iflow_id) -> Path:
        iface = assessment.interface

        # If a ResolvedDestination is provided, use its recommended adapters
        if resolved is not None:
            effective_sender   = resolved.sender_recommendation.recommended_adapter
            effective_receiver = resolved.receiver_recommendation.recommended_adapter
            extra_hints        = resolved.migration_hints
            hub_matches        = resolved.hub_matches
            target_label       = resolved.target.label
            warnings           = resolved.compatibility_warnings
        else:
            effective_sender   = iface.sender_adapter
            effective_receiver = iface.receiver_adapter
            extra_hints        = []
            hub_matches        = []
            target_label       = ""
            warnings           = []

        xml_content = self.template.render(
            interface=iface,
            iflow_id=iflow_id,
            complexity=assessment.complexity,
            score=assessment.score,
            effort_days=assessment.effort_days,
            notes=assessment.notes + extra_hints,
            pattern=assessment.recommended_pattern,
            # Destination-enriched fields
            effective_sender_adapter=effective_sender,
            effective_receiver_adapter=effective_receiver,
            hub_matches=hub_matches,
            target_label=target_label,
            compatibility_warnings=warnings,
        )

        # If destination-aware, namespace the filename
        suffix = f"_{_slugify(target_label)}" if target_label else ""
        out_path = self.output_dir / f"{iflow_id}{suffix}.iflw"
        out_path.write_text(xml_content, encoding="utf-8")
        logger.debug("Scaffolded iFlow → %s", out_path)
        return out_path

    def scaffold_all(
        self,
        assessments: list[MigrationAssessment],
        resolutions: Optional[dict] = None,    # {iface_name: {target_id: ResolvedDestination}}
        target_ids: Optional[list[str]] = None,
    ) -> list[Path]:
        """
        Scaffold iFlows for all assessments.
        If resolutions + target_ids are provided, generates one .iflw per
        (interface × target) combination so each has correct adapter config.
        """
        paths = []
        for a in assessments:
            try:
                if resolutions and target_ids:
                    iface_res = resolutions.get(a.interface.name, {})
                    for tid in target_ids:
                        resolved = iface_res.get(tid)
                        p = self.scaffold(a, resolved=resolved)
                        paths.append(p)
                else:
                    p = self.scaffold(a)
                    paths.append(p)
            except Exception as exc:
                logger.error("Failed to scaffold '%s': %s", a.interface.name, exc)

        logger.info("Scaffolded %d iFlow file(s) → %s", len(paths), self.output_dir)
        return paths
