"""
engine/feedback_loop.py

Manages the iterative refinement loop per interface:
  Iteration 1: Claude generates initial solution → deploy to DEV
  Iteration 2+: Consultant gives feedback → Claude refines → re-deploy

Stores full history per interface so Claude has context for each refinement.
History persists in ~/.cpi_migrator/solver_sessions/ as JSON.

Feedback types supported:
  - Free text (anything)
  - Common issue checkboxes (field wrong, missing field, wrong adapter, etc.)
  - Diff annotation (specific line/field corrections)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SESSIONS_DIR = Path.home() / ".cpi_migrator" / "solver_sessions"

# Common issue options shown as checkboxes in the UI
COMMON_ISSUES = [
    "Field mapping incorrect — wrong source field path",
    "Field mapping incomplete — missing required fields",
    "Wrong target structure / namespace",
    "Wrong adapter configuration",
    "Missing error handling",
    "Test payload doesn't match real message structure",
    "Wrong authentication method configured",
    "Credential alias name incorrect",
    "IDoc segment path wrong",
    "Value mapping entries missing or wrong",
    "Parameters.prop has wrong key names",
    "Groovy syntax error",
    "Performance issue — too many XML re-parses",
]


@dataclass
class FeedbackEntry:
    iteration: int
    timestamp: str
    free_text: str = ""
    checked_issues: list[str] = field(default_factory=list)
    diff_annotations: list[dict] = field(default_factory=list)  # [{line, comment}]

    def to_prompt_text(self) -> str:
        """Format feedback for Claude's prompt."""
        parts = []
        if self.checked_issues:
            parts.append("Issues identified:\n" +
                         "\n".join(f"- {i}" for i in self.checked_issues))
        if self.free_text:
            parts.append(f"Additional notes:\n{self.free_text}")
        if self.diff_annotations:
            parts.append("Specific corrections:\n" +
                         "\n".join(f"- Line {a['line']}: {a['comment']}"
                                   for a in self.diff_annotations))
        return "\n\n".join(parts) if parts else "General review needed"


@dataclass
class SolverSession:
    """Full iteration history for one interface."""
    interface_name: str
    created_at: str
    iterations: list[dict] = field(default_factory=list)  # {result, feedback, deployed}
    approved: bool = False
    promoted_to_qa: bool = False
    promoted_to_prod: bool = False

    def add_iteration(self, result, feedback: Optional[FeedbackEntry] = None):
        self.iterations.append({
            "iteration":        result.iteration,
            "timestamp":        datetime.now().isoformat(),
            "confidence":       result.confidence,
            "reasoning":        result.reasoning,
            "artifacts_count":  len(result.artifacts),
            "remaining_manual": result.remaining_manual,
            "feedback":         asdict(feedback) if feedback else None,
            "deployed":         False,
            "test_passed":      None,
        })

    def mark_deployed(self, iteration: int):
        for it in self.iterations:
            if it["iteration"] == iteration:
                it["deployed"] = True

    def mark_test_result(self, iteration: int, passed: bool):
        for it in self.iterations:
            if it["iteration"] == iteration:
                it["test_passed"] = passed

    @property
    def current_iteration(self) -> int:
        return len(self.iterations)

    @property
    def last_result_reasoning(self) -> str:
        if self.iterations:
            return self.iterations[-1].get("reasoning", "")
        return ""

    @property
    def needs_feedback(self) -> bool:
        if not self.iterations:
            return False
        last = self.iterations[-1]
        return last.get("deployed") and last.get("test_passed") is False

    def save(self, sessions_dir: Path = SESSIONS_DIR):
        sessions_dir.mkdir(parents=True, exist_ok=True)
        safe = self.interface_name.replace("/", "_").replace(" ", "_")[:50]
        path = sessions_dir / f"{safe}.session.json"
        path.write_text(json.dumps(asdict(self), indent=2), "utf-8")

    @classmethod
    def load(cls, interface_name: str,
             sessions_dir: Path = SESSIONS_DIR) -> Optional["SolverSession"]:
        safe = interface_name.replace("/", "_").replace(" ", "_")[:50]
        path = sessions_dir / f"{safe}.session.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text("utf-8"))
            session = cls(
                interface_name=data["interface_name"],
                created_at=data["created_at"],
                iterations=data.get("iterations", []),
                approved=data.get("approved", False),
                promoted_to_qa=data.get("promoted_to_qa", False),
                promoted_to_prod=data.get("promoted_to_prod", False),
            )
            return session
        except Exception as exc:
            logger.warning("Could not load session for %s: %s",
                           interface_name, exc)
            return None

    @classmethod
    def get_or_create(cls, interface_name: str) -> "SolverSession":
        existing = cls.load(interface_name)
        if existing:
            return existing
        return cls(
            interface_name=interface_name,
            created_at=datetime.now().isoformat(),
        )


class FeedbackLoopManager:
    """
    Orchestrates the full solve → deploy → test → feedback → refine cycle.
    """

    def __init__(
        self,
        solver,           # ClaudeSolver
        uploader,         # CPIUploader
        replayer=None,    # PayloadReplayer | None
        output_dir: str = "./output",
    ):
        self.solver    = solver
        self.uploader  = uploader
        self.replayer  = replayer
        self.output_dir = Path(output_dir)
        self.sessions_dir = SESSIONS_DIR

    def run_iteration(
        self,
        assessment,
        cfg=None,
        channel_config=None,
        esr_objects=None,
        hub_artifacts=None,
        feedback: Optional[FeedbackEntry] = None,
        package_id: str = "MIGRATION_DEV",
        auto_deploy: bool = True,
        iflow_dir: Optional[Path] = None,
    ) -> tuple[object, SolverSession]:  # (SolverResult, SolverSession)
        """
        Run one iteration of the solve → deploy cycle.
        Returns (result, updated_session).
        """
        iface   = assessment.interface
        session = SolverSession.get_or_create(iface.name)
        iteration = session.current_iteration + 1

        # Build feedback text for Claude
        feedback_text      = ""
        previous_solution  = ""
        if feedback and session.iterations:
            feedback_text     = feedback.to_prompt_text()
            last              = session.iterations[-1]
            previous_solution = last.get("reasoning", "")

        # Call Claude
        logger.info("Solving %s (iteration %d)…", iface.name, iteration)
        result = self.solver.solve(
            assessment=assessment,
            cfg=cfg,
            channel_config=channel_config,
            esr_objects=esr_objects,
            hub_artifacts=hub_artifacts,
            feedback=feedback_text,
            previous_solution=previous_solution,
            iteration=iteration,
        )

        # Save artifacts to disk
        artifacts_dir = self.output_dir / "solver" / iface.name.replace(" ", "_")
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        for artifact in result.artifacts:
            path = artifacts_dir / artifact.filename
            path.write_text(artifact.content, "utf-8")
            logger.debug("Saved artifact → %s", path)

        # Add to session
        session.add_iteration(result, feedback)

        # Deploy to DEV if requested
        if auto_deploy and self.uploader and result.confidence >= 0.6:
            iflw_dir = iflow_dir or (self.output_dir / "iflows")
            candidates = list(iflw_dir.glob(f"*{iface.name[:20]}*.iflw")) \
                if iflw_dir.exists() else []
            if candidates:
                upload_result = self.uploader.upload_iflow(
                    candidates[0], package_id,
                    iface.name.replace(" ", "_")[:60], iface.name,
                    overwrite=True,
                )
                if upload_result.status in ("uploaded",):
                    self.uploader.deploy_iflow(upload_result.artifact_id)
                    session.mark_deployed(iteration)
                    logger.info("Deployed %s to DEV (iteration %d)",
                                iface.name, iteration)
            else:
                logger.warning("No .iflw file found to deploy for %s",
                               iface.name)
        elif result.confidence < 0.6:
            logger.warning(
                "Confidence %.0f%% too low to auto-deploy %s — review required",
                result.confidence * 100, iface.name
            )

        session.save()
        return result, session

    def approve_and_promote(
        self, interface_name: str, target: str = "qa"
    ) -> bool:
        """Mark interface as approved and trigger cTMS promotion."""
        session = SolverSession.load(interface_name)
        if not session:
            return False

        session.approved = True
        if target == "qa":
            session.promoted_to_qa = True
        elif target == "prod":
            session.promoted_to_prod = True
        session.save()

        logger.info("%s approved and marked for %s promotion",
                    interface_name, target.upper())
        return True

    def get_project_status(
        self, interface_names: list[str]
    ) -> dict:
        """Summary of solve progress across all interfaces."""
        total      = len(interface_names)
        approved   = 0
        in_progress = 0
        not_started = 0
        needs_review = 0

        for name in interface_names:
            session = SolverSession.load(name)
            if not session:
                not_started += 1
            elif session.approved:
                approved += 1
            elif session.needs_feedback:
                needs_review += 1
            else:
                in_progress += 1

        return {
            "total":        total,
            "approved":     approved,
            "in_progress":  in_progress,
            "needs_review": needs_review,
            "not_started":  not_started,
            "completion_pct": round(approved / total * 100, 1) if total else 0,
        }
