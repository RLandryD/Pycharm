"""
engine/claude_solver.py

AI-powered solution engine. Takes complete interface metadata,
calls Claude API with structured context, returns deployable artifacts.

For each interface produces:
  - Complete Groovy transformation script
  - Externalized parameters (parameters.prop)
  - iFlow XML modifications
  - Value mapping entries
  - Realistic test payload
  - Remaining manual steps

Uses claude-sonnet-4-20250514 via the Anthropic API.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import requests

logger = logging.getLogger(__name__)

ANTHROPIC_API = "https://api.anthropic.com/v1/messages"
MODEL         = "claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------

@dataclass
class SolverArtifact:
    """One deployable artifact produced by the solver."""
    artifact_type: str       # groovy / parameters / xslt / value_mapping / test_payload / instruction
    filename: str
    content: str
    description: str = ""
    confidence: float = 0.8  # 0.0–1.0


@dataclass
class SolverResult:
    """Complete solution for one interface."""
    interface_name: str
    artifacts: list[SolverArtifact]
    iflow_modifications: list[str]    # human-readable list of changes to apply
    remaining_manual: list[str]       # what still needs human work
    confidence: float                 # overall solution confidence
    reasoning: str                    # why Claude made these choices
    iteration: int = 1
    raw_response: str = ""


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an expert SAP CPI (Cloud Integration) architect with 15+ years 
of experience migrating SAP PI/PO interfaces to SAP Integration Suite.

You receive complete interface metadata and produce deployable CPI artifacts.
Always respond with valid JSON only — no markdown, no explanation outside the JSON.

Your output must follow this exact structure:
{
  "groovy_script": "...",
  "parameters_prop": "KEY=value\\nKEY2=value2",
  "iflow_modifications": ["step 1", "step 2"],
  "value_mappings": [{"source": "01", "target": "CREATED"}],
  "test_payload": "<?xml version...",
  "remaining_manual": ["task 1", "task 2"],
  "confidence": 0.85,
  "reasoning": "Brief explanation of key decisions made"
}

Rules:
- Use groovy.util.XmlSlurper (not groovy.xml) for CPI compatibility
- Always include messageLogFactory MPL logging
- Always include try/catch with CamelExceptionCaught
- Use externalized parameters for all endpoints and credentials
- Never hardcode URLs, passwords, or system names
- For unknown field mappings add TODO comments with the field name
- Confidence: 0.9+ means ready to test, 0.7-0.9 needs review, <0.7 needs significant work"""


def _build_prompt(
    assessment,
    cfg,
    channel_config=None,
    esr_objects: list = None,
    hub_artifacts: list = None,
    feedback: str = "",
    previous_solution: str = "",
    iteration: int = 1,
) -> str:
    iface = assessment.interface

    # Build context block
    context = {
        "interface": {
            "name":              iface.name,
            "namespace":         iface.namespace,
            "sender_system":     iface.sender_system,
            "sender_adapter":    iface.sender_adapter,
            "receiver_system":   iface.receiver_system,
            "receiver_adapter":  iface.receiver_adapter,
            "message_interface": iface.message_interface,
            "mapping_program":   iface.mapping_program,
            "has_bpm":           iface.has_bpm,
            "has_multi_mapping": iface.has_multi_mapping,
            "description":       iface.description,
            "complexity":        assessment.complexity,
            "score":             assessment.score,
        }
    }

    if cfg:
        context["configuration"] = {
            "sender_adapter":        cfg.sender_adapter,
            "sender_address":        cfg.sender_connectivity.address,
            "sender_auth_method":    cfg.sender_auth.method,
            "sender_credential":     cfg.sender_auth.credential_name,
            "receiver_adapter":      cfg.receiver_adapter,
            "receiver_address":      cfg.receiver_connectivity.address,
            "receiver_auth_method":  cfg.receiver_auth.method,
            "receiver_credential":   cfg.receiver_auth.credential_name,
            "receiver_token_url":    cfg.receiver_auth.token_url,
            "message_format":        cfg.message.format,
            "is_async":              cfg.message.is_async,
            "namespace":             cfg.message.namespace,
            "mapping_program":       cfg.message.mapping_program,
            "idoc_type":             cfg.message.idoc_type,
            "idoc_message_type":     cfg.message.idoc_message_type,
            "retry_enabled":         cfg.reliability.retry_enabled,
            "retry_max":             cfg.reliability.retry_max_attempts,
            "log_level":             cfg.reliability.log_level,
        }

    if channel_config:
        context["channel"] = {
            "address":        channel_config.address,
            "path":           channel_config.path,
            "auth_type":      channel_config.auth_type,
            "parameters":     channel_config.parameters,
            "wsdl_url":       channel_config.wsdl_url,
            "idoc_type":      channel_config.idoc_type,
            "function_module": channel_config.function_module,
            "jdbc_url":       channel_config.jdbc_url,
            "jdbc_query":     channel_config.jdbc_query,
        }

    if esr_objects:
        context["esr_objects"] = [
            {"name": o.name, "type": o.obj_type,
             "namespace": o.namespace, "mapping_type": o.mapping_type}
            for o in esr_objects[:5]
        ]

    if hub_artifacts:
        context["hub_alternatives"] = [
            {"id": a.id if hasattr(a,'id') else str(a),
             "name": a.name if hasattr(a,'name') else str(a),
             "description": a.short_text if hasattr(a,'short_text') else ""}
            for a in hub_artifacts[:3]
        ]

    prompt = f"Interface context:\n{json.dumps(context, indent=2)}\n\n"

    if iteration == 1:
        prompt += (
            "Generate a complete CPI migration solution for this interface. "
            "Produce production-ready artifacts that can be deployed directly to the CPI DEV tenant."
        )
    else:
        prompt += f"Previous solution (iteration {iteration-1}):\n{previous_solution}\n\n"
        prompt += f"Consultant feedback:\n{feedback}\n\n"
        if iteration == 2:
            prompt += (
                "Refine the solution based on the feedback above. "
                "Explain what you changed and why in the 'reasoning' field."
            )
        else:
            prompt += (
                "Refine the solution based on the feedback above. "
                "Update artifacts silently — keep 'reasoning' brief."
            )

    return prompt


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------

class ClaudeSolver:

    def __init__(self, api_key: str = ""):
        self.api_url = ANTHROPIC_API
        # API key resolution order: explicit arg > env var. When neither is
        # set (e.g. standalone workbench without a key), calls will 401 and we
        # surface a clear "no API key" message rather than a cryptic error.
        import os
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    def solve(
        self,
        assessment,
        cfg=None,
        channel_config=None,
        esr_objects: list = None,
        hub_artifacts: list = None,
        feedback: str = "",
        previous_solution: str = "",
        iteration: int = 1,
    ) -> SolverResult:
        """Call Claude API and return structured solution."""
        iface  = assessment.interface

        # Clear, early signal when no key is configured (the common cause of
        # the 401 in standalone use).
        if not self.api_key:
            logger.warning("AI Solver: no Anthropic API key configured "
                           "(set ANTHROPIC_API_KEY or add it in Settings)")
            return SolverResult(
                interface_name=iface.name, artifacts=[], iflow_modifications=[],
                remaining_manual=["AI Solver needs an Anthropic API key. Add it "
                                  "in Settings or set the ANTHROPIC_API_KEY "
                                  "environment variable."],
                confidence=0.0,
                reasoning="No Anthropic API key configured.",
            )

        prompt = _build_prompt(
            assessment, cfg, channel_config, esr_objects,
            hub_artifacts, feedback, previous_solution, iteration,
        )

        try:
            resp = requests.post(
                self.api_url,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model":      MODEL,
                    "max_tokens": 4000,
                    "system":     SYSTEM_PROMPT,
                    "messages":   [{"role": "user", "content": prompt}],
                },
                timeout=60,
            )
            resp.raise_for_status()
            data    = resp.json()
            content = next(
                (b["text"] for b in data.get("content", [])
                 if b.get("type") == "text"), ""
            )
            raw = content

        except Exception as exc:
            logger.error("Claude API call failed: %s", exc)
            hint = ""
            if "401" in str(exc):
                hint = " (check the API key is valid)"
            return SolverResult(
                interface_name=iface.name,
                artifacts=[],
                iflow_modifications=[],
                remaining_manual=[f"API call failed: {exc}{hint}"],
                confidence=0.0,
                reasoning="API unavailable",
                iteration=iteration,
            )

        # Parse JSON response
        try:
            # Strip accidental markdown fences
            clean = re.sub(r"```(?:json)?|```", "", content).strip()
            parsed = json.loads(clean)
        except json.JSONDecodeError:
            # Try to extract JSON block
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group())
                except Exception:
                    parsed = {}
            else:
                parsed = {}

        return self._build_result(iface.name, parsed, iteration, raw)

    def _build_result(
        self,
        interface_name: str,
        parsed: dict,
        iteration: int,
        raw: str,
    ) -> SolverResult:
        safe_name = re.sub(r"[^\w]", "_", interface_name)
        artifacts = []

        # Groovy script
        groovy = parsed.get("groovy_script", "")
        if groovy and len(groovy) > 50:
            artifacts.append(SolverArtifact(
                artifact_type="groovy",
                filename=f"{safe_name}_transform.groovy",
                content=groovy,
                description="Main transformation script",
                confidence=parsed.get("confidence", 0.8),
            ))

        # Parameters
        params = parsed.get("parameters_prop", "")
        if params:
            artifacts.append(SolverArtifact(
                artifact_type="parameters",
                filename="parameters.prop",
                content=params,
                description="Externalized parameters for iFlow",
                confidence=0.9,
            ))

        # Test payload
        payload = parsed.get("test_payload", "")
        if payload:
            ext = ".json" if payload.strip().startswith("{") else ".xml"
            artifacts.append(SolverArtifact(
                artifact_type="test_payload",
                filename=f"{safe_name}_test{ext}",
                content=payload,
                description="Realistic test payload for DEV testing",
                confidence=0.85,
            ))

        # Value mappings
        vms = parsed.get("value_mappings", [])
        if vms:
            vm_content = "\n".join(
                f"{v.get('source','')}={v.get('target','')}"
                for v in vms
            )
            artifacts.append(SolverArtifact(
                artifact_type="value_mapping",
                filename=f"{safe_name}_value_mappings.txt",
                content=vm_content,
                description=f"{len(vms)} value mapping entries",
                confidence=0.85,
            ))

        return SolverResult(
            interface_name=interface_name,
            artifacts=artifacts,
            iflow_modifications=parsed.get("iflow_modifications", []),
            remaining_manual=parsed.get("remaining_manual", []),
            confidence=float(parsed.get("confidence", 0.5)),
            reasoning=parsed.get("reasoning", ""),
            iteration=iteration,
            raw_response=raw,
        )
