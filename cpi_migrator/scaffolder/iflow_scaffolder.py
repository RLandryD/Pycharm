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
                 resources_dir: str = None, extra_resources: dict = None,
                 passthrough: dict = None,
                 gold_error_handling: str = None,
                 gold_eh_replace: bool = False,
                 gold_eh_notify: bool = False,
                 gold_eh_sftp: bool = False,
                 gold_eh_company: str = ""):
        self.output_dir = Path(output_dir) / "iflows"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Resources ingested from the UPLOADED source zips themselves
        # (container-qualified, same scheme as the corpus walk). Merged with
        # TOP precedence at regeneration time: the uploaded source is the
        # ground truth for its own flows, beating any same-named corpus file.
        self.extra_resources = extra_resources or {}
        self.passthrough = passthrough or {}
        # opt-in: inject the gold-standard exception subprocess (variant name
        # from scaffolder.error_handling.VARIANTS) into regenerated flows that
        # lack one; None (default) keeps pure fidelity.
        self.gold_error_handling = gold_error_handling
        # replace policy: when True, existing main-process exception
        # subprocesses are swapped for the chosen gold variant (client opts
        # to standardize); when False (default) flows that already handle
        # errors are untouched.
        self.gold_eh_replace = gold_eh_replace
        # when True, the injected subprocess also mails an alert (Mail
        # receiver with externalized {{ALERT_MAIL_*}} connection params —
        # pattern decoded from RCI093's production LIP3_Exception_Alert)
        self.gold_eh_notify = gold_eh_notify
        self.gold_eh_sftp = gold_eh_sftp
        self.gold_eh_company = gold_eh_company
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
        # Merge the distilled library (additive, content-hash-deduped) as a
        # second resource source — this is what makes raw package folders
        # deletable once library coverage is complete. Library keys use the
        # same '<container>/<path>' scheme, so resolver scoping is unchanged.
        try:
            from fetcher.user_settings import get_setting as _lib_gs
            _lib_dir = _lib_gs("library_dir", "")
            if _lib_dir:
                import os as _os
                if _os.path.isdir(_lib_dir):
                    from library_builder.library_store import LibraryStore
                    _lib_corpus = LibraryStore(_lib_dir).as_corpus()
                    for _k, _v in _lib_corpus.items():
                        self._corpus_cache.setdefault(_k, _v)
                    if _lib_corpus:
                        logger.info("Merged %d library files into the "
                                    "resource corpus", len(_lib_corpus))
        except Exception as exc:                           # pragma: no cover
            logger.warning("library corpus merge skipped: %s", exc)
        IFlowScaffolder._CORPUS_BY_DIR[self.resources_dir] = self._corpus_cache
        return self._corpus_cache

    # package names already topped up this session (per resources dir) — a
    # targeted walk per *new* name only, not per flow.
    _TOPPED_UP: dict = {}

    def _top_up_corpus(self, resources: dict, iface) -> None:
        """Guarantee the flow's OWN package is in the resource corpus even when
        the bulk walk capped out before reaching it (seen live: 'corpus walk
        hit the safety cap' → 0 resolved, stub scripts, empty parameters).
        Scans only zip names under the pinned dir and ingests just the matching
        package(s); merges into the shared cached dict so every later flow of
        the same package benefits. Graceful: never raises."""
        if not self.resources_dir:
            return
        names = [n for n in (getattr(iface, "package", None),
                             getattr(iface, "name", None)) if n]
        done = IFlowScaffolder._TOPPED_UP.setdefault(self.resources_dir, set())
        todo = [n for n in names if n not in done]
        if not todo:
            return
        done.update(todo)
        try:
            from library_builder.corpus_pipeline import (walk_corpus_for_names,
                                                          WIRING_EXTS)
            extra = walk_corpus_for_names(self.resources_dir, todo,
                                          exts=WIRING_EXTS)
            added = 0
            for k, v in extra.items():
                if k not in resources:
                    resources[k] = v
                    added += 1
            if added:
                logger.info("Targeted corpus top-up for %s: +%d files (bulk "
                            "walk had capped before this package)",
                            " / ".join(todo), added)
        except Exception as exc:                            # pragma: no cover
            logger.warning("Targeted corpus top-up failed for %s (%s); "
                           "falling back to the bulk corpus only",
                           " / ".join(todo), exc)

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
                    resources = self._resource_corpus()
                    self._top_up_corpus(resources, iface)
                    if self.extra_resources:
                        resources = {**resources, **self.extra_resources}
                    regen = regenerate_iflow_xml(
                        src_xml, iface.name,
                        resources=resources,
                        package=getattr(iface, "package", None),
                        gold_error_handling=self.gold_error_handling,
                        gold_eh_replace=self.gold_eh_replace,
                        gold_eh_notify=self.gold_eh_notify,
                        gold_eh_sftp=self.gold_eh_sftp,
                        gold_eh_company=self.gold_eh_company)
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
                        # Re-attach non-wiring cargo (lib jars, certs,
                        # deployment descriptors) collected at ingest.
                        try:
                            from scaffolder.passthrough import inject_cargo
                            inject_cargo(result, src_xml, self.passthrough)
                        except Exception as _pterr:
                            logger.warning("passthrough injection skipped: "
                                           "%s", _pterr)
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
        # stale staged resources from a PREVIOUS generation must not ride
        # along (the packager auto-includes everything under meta) — the set
        # on disk must equal exactly THIS generation's files.
        _stale = meta_dir / "src"
        if _stale.is_dir():
            import shutil as _sh
            _sh.rmtree(_stale, ignore_errors=True)
        meta_dir.mkdir(parents=True, exist_ok=True)
        (meta_dir / "MANIFEST.MF").write_text(result.manifest, encoding="utf-8")
        (meta_dir / ".project").write_text(result.project_xml, encoding="utf-8")
        for rel_path, content in (getattr(result, "files", {}) or {}).items():
            from scaffolder.passthrough import is_passthrough
            if (not rel_path.startswith("src/main/resources/")
                    and not is_passthrough(rel_path)):
                continue
            if rel_path.startswith("src/main/resources/scenarioflows/"):
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
        # MA-mode: the export names WHICH rules fired — render each as a
        # representative named step so the skeleton's size and step names
        # narrate the actual blockers (an all-rules ultra monster LOOKS like
        # one). Real-source regeneration never enters this builder.
        _ma_rules = (getattr(iface, "raw", None) or {}).get("sap_ma_rules") \
            or []
        _RULE_STEPS = {
            "Axis_Framework_Extension": [
                ("script", "Axis Framework Handler (custom module)")],
            "Java_Mapping_Detected": [
                ("script", "Java Mapping (port to Groovy)")],
            "Multi_Mapping_Split_Context": [
                ("splitter", "Split Records"),
                ("gather", "Gather Responses")],
            "PGP_Encryption_Topology_Shift": [
                ("content_modifier", "PGP Encrypt (Security Material)")],
            "File_Content_Conversion_Required": [
                ("content_modifier", "FCC: Flat to XML Conversion")],
            "Dynamic_Routing_Condition": [
                ("content_modifier", "Route by XPath Condition")],
            "Hardcoded_Deprecated_Security_Library": [
                ("script", "Replace Deprecated Crypto Library")],
            "Obsolete_Native_Conversion_Bean": [
                ("content_modifier", "Replace Native Conversion Bean")],
        }
        # Engine-scanned exports carry PIMAS rule ids with asset context
        # (e.g. 'xslt=7') — render those with human labels + real counts,
        # and DON'T render adapter-type rules as pipeline steps (they are
        # endpoint facts, not flow work).
        _assets = dict()
        _asset_pairs = (getattr(iface, "raw", None) or {}).get(
            "sap_ma_rule_assets") or []

        def _count(asset):
            try:
                return int(str(asset).split("=", 1)[1])
            except (IndexError, ValueError):
                return None

        _ENGINE_LABELS = {
            "GMMCustomUDFUsageCount": ("script", "Groovy Mappings"),
            "XSLTDependenciesCount":  ("mapping", "XSLT Transform Chain"),
            "OMStepCount":            ("content_modifier",
                                       "Operation Mapping Steps"),
            "OMParametersCount":      ("content_modifier",
                                       "OM Parameterization"),
            "ICOOperationCount":      ("content_modifier",
                                       "Multiple ICO Operations"),
            "ICOReceivers":           ("content_modifier",
                                       "Multiple Receivers"),
        }
        _SKIP_RULES = {"SenderAdapterType", "ReceiverAdapterType"}
        _pairs = _asset_pairs or [[r, ""] for r in _ma_rules]
        _seen_pairs = set()
        for _r, _asset in _pairs:
            if _r in _SKIP_RULES or (_r, _asset) in _seen_pairs:
                continue
            _seen_pairs.add((_r, _asset))
            if _r in _ENGINE_LABELS:
                kind, base = _ENGINE_LABELS[_r]
                n = _count(_asset)
                steps.append({"kind": kind,
                              "name": f"{base} (x{n})" if n else base})
            elif _r == "MappingType":
                val = (str(_asset).split("=", 1)[1]
                       if "=" in str(_asset) else str(_asset))
                steps.append({"kind": "mapping",
                              "name": f"Mapping: {val}" if val
                              else "Mapping"})
            else:
                for kind, label in _RULE_STEPS.get(
                        _r, [("content_modifier", _r.replace("_", " "))]):
                    steps.append({"kind": kind, "name": label})
        if getattr(iface, "mapping_program", "") and not _ma_rules:
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
        if ch > 1 and not any(st["kind"] == "splitter" for st in steps):
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
