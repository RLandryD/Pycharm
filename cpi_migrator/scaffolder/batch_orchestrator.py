"""
scaffolder/batch_orchestrator.py

End-to-end batch processing for the "green path" — the interfaces the
migration-ceiling classifier marks AUTO (high automation, no specialist
decision needed). Instead of clicking each interface through match →
configure → generate → upload, this runs them all in one pass and STOPS
only for the GUIDED / SPECIALIST interfaces that genuinely need a human.

This is the "process all Ready/LOW interfaces, stop on exceptions" feature.

What it does per AUTO interface:
  1. scaffold the .iflw (via IFlowScaffolder)
  2. build externalized parameters (.prop)            [if available]
  3. optionally upload to the tenant                  [if a uploader given]

What it does NOT do:
  - GUIDED / SPECIALIST interfaces are skipped with a reason (they need the
    manual tabs). They are returned in `needs_attention` so the UI can list
    exactly what's left for the human.
  - It never deploys or makes irreversible changes unless explicitly asked.

Honest scope: this orchestrates the pieces that already exist. The generated
iFlow is still skeleton-grade until the iFlow-wiring feature lands; this makes
the *flow through the pipeline* automated, not the depth of each artifact.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger(__name__)

# Tiers from migration_ceiling
TIER_AUTO = "AUTO"
TIER_GUIDED = "GUIDED"
TIER_SPECIALIST = "SPECIALIST"


@dataclass
class BatchItemResult:
    interface_name: str
    tier: str
    action: str                  # "processed" | "skipped" | "failed"
    iflow_path: str = ""
    params_path: str = ""
    uploaded: bool = False
    upload_status: str = ""
    reason: str = ""             # why skipped / failed


@dataclass
class BatchRunReport:
    processed: list[BatchItemResult] = field(default_factory=list)
    needs_attention: list[BatchItemResult] = field(default_factory=list)
    failed: list[BatchItemResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.processed) + len(self.needs_attention) + len(self.failed)

    def summary(self) -> dict:
        return {
            "total":            self.total,
            "processed":        len(self.processed),
            "needs_attention":  len(self.needs_attention),
            "failed":           len(self.failed),
            "uploaded":         sum(1 for r in self.processed if r.uploaded),
        }


class BatchOrchestrator:
    """Runs the green-path pipeline end-to-end for AUTO-tier interfaces."""

    def __init__(self, scaffolder, output_dir: str,
                 uploader=None,
                 param_builder: Optional[Callable] = None):
        """
        scaffolder    : an IFlowScaffolder instance
        output_dir    : project output dir (iflows/ and parameters/ live under it)
        uploader      : optional CPIUploader; if given, AUTO iFlows are uploaded
        param_builder : optional callable(assessment, config) -> prop string
        """
        self.scaffolder = scaffolder
        self.output_dir = Path(output_dir)
        self.uploader = uploader
        self.param_builder = param_builder

    def run(
        self,
        assessments: list,
        ceilings: dict,              # {interface_name: MigrationCeiling}
        configs: Optional[dict] = None,
        resolutions: Optional[dict] = None,
        include_guided: bool = False,
        upload: bool = False,
        progress_cb: Optional[Callable] = None,
        shapes: Optional[dict] = None,         # {interface_name: "timer"|"minimal"}
        excluded_names: Optional[set] = None,  # user opted these out of generation
    ) -> BatchRunReport:
        """Process the batch.

        include_guided=True also processes GUIDED interfaces (still skips
        SPECIALIST). upload=True pushes processed iFlows to the tenant (needs
        an uploader). progress_cb(done, total, name) is called per interface.
        shapes overrides the per-interface iFlow shape (default "timer").
        excluded_names are interfaces the user chose not to generate; they are
        reported under needs_attention rather than silently dropped.
        """
        report = BatchRunReport()
        configs = configs or {}
        shapes = shapes or {}
        excluded_names = excluded_names or set()
        total = len(assessments)

        for idx, a in enumerate(assessments):
            name = a.interface.name
            ceiling = ceilings.get(name)
            tier = ceiling.tier if ceiling else TIER_AUTO   # no ceiling = treat as auto

            if progress_cb:
                progress_cb(idx + 1, total, name)

            # User opted this interface out of scaffolding (e.g. it's a genuine
            # push/inbound interface that needs the sender path instead).
            if name in excluded_names:
                report.needs_attention.append(BatchItemResult(
                    interface_name=name, tier=tier, action="skipped",
                    reason="Excluded by user (not generated)"))
                continue

            # Routing: SPECIALIST always needs a human; GUIDED unless opted in
            if tier == TIER_SPECIALIST or (tier == TIER_GUIDED and not include_guided):
                reason = ("Specialist decision required"
                          if tier == TIER_SPECIALIST
                          else "Guided review recommended")
                report.needs_attention.append(BatchItemResult(
                    interface_name=name, tier=tier, action="skipped",
                    reason=reason))
                continue

            # ── Process the green-path interface ─────────────────────────
            try:
                resolved = None
                if resolutions and name in resolutions:
                    # take the first resolved target if a dict of targets
                    rv = resolutions[name]
                    resolved = next(iter(rv.values())) if isinstance(rv, dict) else rv

                iflow_path = self.scaffolder.scaffold(
                    a, resolved=resolved, shape=shapes.get(name, "timer"))

                item = BatchItemResult(
                    interface_name=name, tier=tier, action="processed",
                    iflow_path=str(iflow_path))

                # Parameters
                if self.param_builder:
                    try:
                        prop = self.param_builder(a, configs.get(name))
                        if prop:
                            pdir = self.output_dir / "parameters"
                            pdir.mkdir(parents=True, exist_ok=True)
                            ppath = pdir / f"{iflow_path.stem}.prop"
                            ppath.write_text(prop, encoding="utf-8")
                            item.params_path = str(ppath)
                    except Exception as exc:
                        logger.warning("Param build failed for %s: %s", name, exc)

                # Upload
                if upload and self.uploader:
                    self._upload_item(a, iflow_path, item)

                report.processed.append(item)

            except Exception as exc:
                logger.error("Batch processing failed for %s: %s", name, exc)
                report.failed.append(BatchItemResult(
                    interface_name=name, tier=tier, action="failed",
                    reason=str(exc)))

        logger.info("Batch run complete: %s", report.summary())
        return report

    def _upload_item(self, assessment, iflow_path: Path, item: BatchItemResult):
        """Upload one processed iFlow, gating on package creation."""
        from scaffolder.pipeline_scaffolder import generate_package_name
        iface = assessment.interface
        pkg_id = generate_package_name(
            "MIGRATION",
            iface.sender_system or "SRC",
            iface.receiver_system or "TGT",
            iface.namespace or "",
        ).replace(" ", "_")[:50]

        if not self.uploader.ensure_package(pkg_id, pkg_id.replace("_", " ")):
            item.upload_status = "package creation failed (see log)"
            item.uploaded = False
            return

        result = self.uploader.upload_iflow(
            iflow_path, pkg_id,
            item.interface_name.replace(" ", "_")[:60],
            item.interface_name, overwrite=True)
        item.uploaded = result.status in ("uploaded", "deployed")
        item.upload_status = result.message
