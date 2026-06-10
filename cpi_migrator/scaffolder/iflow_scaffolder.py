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

    def __init__(self, output_dir: str, templates_dir: str = None,
                 resources_dir: str = None):
        self.output_dir = Path(output_dir) / "iflows"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Original package exports (pinned Packages dir). When set, regenerated
        # bundles ship the REAL scripts/mappings each step references instead of
        # synthetic stubs. Loaded once, lazily; failure degrades gracefully.
        self.resources_dir = resources_dir
        self._corpus_cache = None

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

    # one corpus load per directory per session — scaffolders are constructed
    # per-flow in the upload loop, so an instance cache reloads thousands of
    # files for every iFlow (visible as repeated 'Loaded N resource files').
    _CORPUS_BY_DIR: dict = {}

    def _resource_corpus(self) -> dict:
        """Lazily load + cache (module-wide) the path-keyed resource corpus from
        the pinned packages dir. Returns {} (never raises) if unavailable, so
        the deploy path falls back to synthetic stubs rather than failing."""
        if self._corpus_cache is not None:
            return self._corpus_cache
        if self.resources_dir in IFlowScaffolder._CORPUS_BY_DIR:
            self._corpus_cache = IFlowScaffolder._CORPUS_BY_DIR[self.resources_dir]
            return self._corpus_cache
        self._corpus_cache = {}
        if self.resources_dir:
            try:
                from library_builder.corpus_pipeline import (walk_corpus,
                                                              WIRING_EXTS)
                self._corpus_cache = walk_corpus(self.resources_dir,
                                                 exts=WIRING_EXTS) or {}
                logger.info("Loaded %d resource files for bundle wiring (cached "
                            "for this session)", len(self._corpus_cache))
            except Exception as exc:                       # pragma: no cover
                logger.warning("Resource corpus unavailable (%s); bundles will "
                               "use synthetic stubs", exc)
        IFlowScaffolder._CORPUS_BY_DIR[self.resources_dir] = self._corpus_cache
        return self._corpus_cache

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

                # If this interface carries a real source CPI iFlow (uploaded
                # package), regenerate it from its true structure + config — the
                # clean-room path — instead of sizing a placeholder from metadata.
                src_xml = (getattr(iface, "source_iflow_xml", "") or "").strip()
                if src_xml and shape != "minimal":
                    from scaffolder.regenerate import regenerate_iflow_xml
                    regen = regenerate_iflow_xml(
                        src_xml, iface.name,
                        resources=self._resource_corpus(),
                        package=getattr(iface, "package", None))
                    rep = getattr(regen.result, "resource_report", None) \
                        if regen.result else None
                    if rep is not None and (rep.resolved or rep.unresolved):
                        logger.info("Resources for %s: %d resolved, %d shipped",
                                    iface.name, len(rep.resolved),
                                    len(rep.shipped))
                        for sid, ref, kind in rep.unresolved:
                            logger.warning(
                                "Resource NOT FOUND for %s — step %s references "
                                "%r (%s); the source package export is missing "
                                "from the Packages folder or outside the corpus "
                                "cap, so the tenant will report it as not found",
                                iface.name, sid, ref, kind)
                    if regen.reproduced and regen.result is not None:
                        result = regen.result
                        logger.info(
                            "Regenerated iFlow from real source structure "
                            "(%d steps) → %s", regen.n_steps, iface.name)
                        return self._write_result(result)
                    # Honest, loud fallback — do NOT pretend the stub is the flow.
                    logger.warning(
                        "Cannot fully regenerate '%s' yet (%d source steps); "
                        "blocked by: %s. Falling back to a placeholder — this is "
                        "NOT the real flow.", iface.name, regen.n_steps,
                        ", ".join(regen.blockers) or regen.note or "unknown")

                if shape == "minimal":
                    result = generate_minimal_iflow(iface.name, iflow_id)
                else:
                    # Explicit CPI step pipeline (Steps column) wins over the
                    # complexity heuristic: parse it verbatim and seed the rich
                    # multi-record monster body so every XML/convert step has
                    # real data to work on.
                    steps_spec = (getattr(iface, "steps_spec", "") or "").strip()
                    if steps_spec:
                        from scaffolder.minimal_iflow import parse_steps_spec
                        from scaffolder.monster_iflow import monster_body
                        mids, _kinds = parse_steps_spec(steps_spec)
                        result = generate_timer_interface_iflow(
                            iface.name, iflow_id,
                            properties=self._interface_properties(iface),
                            note=f"Scaffolded from PI/PO interface '{iface.name}'",
                            middle_steps=mids, seed_body=monster_body())
                    else:
                        result = generate_timer_interface_iflow(
                            iface.name, iflow_id,
                            properties=self._interface_properties(iface),
                            note=f"Scaffolded from PI/PO interface '{iface.name}'",
                            middle_steps=self._complexity_step_plan(iface))
                logger.info("Generated CPI-valid iFlow (%s) → %s.iflw",
                            shape, result.iflow_id)
                return self._write_result(result)
            except Exception as exc:
                logger.warning("minimal_iflow generation failed for %s (%s); "
                               "falling back to skeleton", iface.name, exc)

        return self._scaffold_skeleton(assessment, resolved, iflow_id)

    def _write_result(self, result):
        """Write a generated iFlow + its manifest/.project + referenced resource
        files to the output dir (so the packager bundles them). Returns the
        .iflw path. Shared by the metadata-scaffold and source-regeneration
        branches."""
        out_path = self.output_dir / f"{result.iflow_id}.iflw"
        out_path.write_text(result.iflw_xml, encoding="utf-8")
        meta_dir = self.output_dir / f"{result.iflow_id}__meta"
        meta_dir.mkdir(parents=True, exist_ok=True)
        (meta_dir / "MANIFEST.MF").write_text(result.manifest, encoding="utf-8")
        (meta_dir / ".project").write_text(result.project_xml, encoding="utf-8")
        for rel_path, content in (getattr(result, "files", {}) or {}).items():
            if not rel_path.startswith("src/main/resources/"):
                continue
            if rel_path.startswith("src/main/resources/scenarioflows/"):
                continue
            if rel_path in ("src/main/resources/parameters.prop",
                            "src/main/resources/parameters.propdef"):
                continue
            dest = meta_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                dest.write_bytes(content)
            else:
                dest.write_text(content, encoding="utf-8")
        return out_path

    @staticmethod
    def _complexity_step_plan(iface) -> list:
        """Derive descriptive middle Content-Modifier steps from the interface's
        complexity signals, so the generated iFlow scales with the work involved:
        a simple interface keeps the bare Timer→CM→CM→End shape (empty plan); a
        complex one gets extra named steps. Names mirror the real-iFlow
        vocabulary decoded from the corpus (map / script / xslt / value mapping /
        router / split / gather / orchestrate). All are the same proven
        Content-Modifier element, so the accepted bundle structure is unchanged."""
        steps = []
        if getattr(iface, "mapping_program", ""):
            steps.append({"kind": "mapping", "name": "Map Fields"})
        if getattr(iface, "has_multi_mapping", False):
            steps.append({"kind": "mapping", "name": "Apply Operation Mappings"})
        # Description-driven structural steps (keywords the complexity engine
        # also scores). (keyword, kind, label); built kinds render as the real
        # decoded BPMN element, the rest stay labelled Content Modifiers.
        desc = (getattr(iface, "description", "") or "").lower()
        kw = [("groovy", "script", "Run Groovy Script"),
              ("xslt", "mapping", "XSLT Transform"),
              (".xsl", "mapping", "XSLT Transform"),
              ("value mapping", "content_modifier", "Value Mapping"),
              ("router", "content_modifier", "Route by Condition"),
              ("multicast", "content_modifier", "Multicast"),
              ("converter", "content_modifier", "Convert Format"),
              ("rfc lookup", "content_modifier", "RFC Lookup"),
              ("jdbc lookup", "content_modifier", "JDBC Lookup"),
              ("filter", "filter", "Filter Content")]
        have = {s["name"] for s in steps}
        for k, kind, label in kw:
            if k in desc and label not in have:
                steps.append({"kind": kind, "name": label})
                have.add(label)
        ch = max(int(getattr(iface, "channel_count", 1) or 1), 1)
        if ch > 1:
            steps.append({"kind": "splitter", "name": "Split Records"})
            steps.append({"kind": "gather", "name": "Gather Responses"})
        if getattr(iface, "has_bpm", False):
            steps.append({"kind": "content_modifier",
                          "name": "Orchestrate Sub-Process"})
        return steps[:12]   # cap to keep the scaffold readable

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
