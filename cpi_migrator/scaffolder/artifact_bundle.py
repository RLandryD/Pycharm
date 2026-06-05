"""
scaffolder/artifact_bundle.py

Generates the supporting artifacts that a wired iFlow REFERENCES — the Groovy
script(s) and the message mapping — so the package is self-contained instead
of pointing at files that don't exist.

When wire_iflow() builds an iFlow it adds a Script step referencing
"<iface>_process.groovy" and a Mapping step referencing "<iface>_mapping".
This module materialises those: a generic-but-valid Groovy script (chosen by
detected capability) and a mapping artifact (draft, from schemas when present,
placeholder otherwise). The uploader then bundles them with the .iflw.

Honest scope:
  - The Groovy script is a real, runnable generic template (e.g. logging +
    passthrough, or a capability-matched one). It is NOT the final business
    logic — it's a correct starting point a consultant completes.
  - The mapping is a structurally-valid draft. If source+target schemas are
    available it auto-drafts direct field matches; otherwise it's a wired
    placeholder flagged for completion. (Full schema->mmap is Phase 2.)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class GeneratedArtifact:
    kind: str                    # "script" | "mapping"
    rel_path: str                # path inside the package zip
    content: str
    note: str = ""


@dataclass
class ArtifactBundle:
    iflow_path: Path
    artifacts: list[GeneratedArtifact] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", str(text or "")).strip("_") or "iflow"


# ── Script capability detection ──────────────────────────────────────────────
# Maps a detected need to a generic script body. Learned from the kinds of
# things real scripts do (logging, dedup, date formatting) — our own generic
# versions, not copied logic.

def _detect_script_capability(interface) -> str:
    desc = (getattr(interface, "description", "") or "").lower()
    name = (getattr(interface, "name", "") or "").lower()
    text = f"{desc} {name}"
    if any(k in text for k in ("idempot", "dedup", "duplicate")):
        return "idempotency"
    if any(k in text for k in ("date", "timestamp", "time zone", "timezone")):
        return "date_format"
    if any(k in text for k in ("mask", "gdpr", "sensitive", "pii")):
        return "masking"
    if any(k in text for k in ("split", "batch", "aggregate")):
        return "logging"   # batch handled by splitter step; script just logs
    return "logging"       # default: log + passthrough


_SCRIPT_TEMPLATES = {
    "logging": '''import com.sap.gateway.ip.core.customdev.util.Message
import java.util.HashMap

// Generic processing + logging script (starting point — complete as needed).
def Message processData(Message message) {
    def body = message.getBody(java.lang.String) as String

    // Log for traceability (visible in MPL)
    def messageLog = messageLogFactory.getMessageLog(message)
    if (messageLog != null) {
        messageLog.setStringProperty("ProcessedBy", "MigrationTool")
        messageLog.addAttachmentAsString("IncomingPayload", body ?: "", "text/plain")
    }

    // TODO: add interface-specific processing here.
    message.setBody(body)
    return message
}
''',
    "date_format": '''import com.sap.gateway.ip.core.customdev.util.Message
import java.text.SimpleDateFormat

// Date formatting helper (starting point). Uses SimpleDateFormat (the idiom
// real production scripts use).
def Message processData(Message message) {
    def body = message.getBody(java.lang.String) as String
    def sdf = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'")
    sdf.setTimeZone(TimeZone.getTimeZone("UTC"))
    message.setHeader("ProcessedAt", sdf.format(new Date()))
    message.setBody(body)
    return message
}
''',
    "idempotency": '''import com.sap.gateway.ip.core.customdev.util.Message

// Idempotency check stub (starting point). A real implementation would use a
// Write/Get Data Store step or external store to track seen message ids.
def Message processData(Message message) {
    def msgId = message.getHeaders().get("SAP_MessageProcessingLogID") as String
    message.setProperty("IdempotencyKey", msgId ?: "")
    // TODO: look up the key in a data store and skip if already processed.
    return message
}
''',
    "masking": '''import com.sap.gateway.ip.core.customdev.util.Message

// Field masking for logs (starting point). Masks obvious PII before logging.
def Message processData(Message message) {
    def body = message.getBody(java.lang.String) as String
    def masked = body?.replaceAll(/(?i)(password|ssn|iban)\\s*[:=]\\s*\\S+/, '$1=****')
    def messageLog = messageLogFactory.getMessageLog(message)
    if (messageLog != null) {
        messageLog.addAttachmentAsString("MaskedPayload", masked ?: "", "text/plain")
    }
    message.setBody(body)   // original body passes through; only the log is masked
    return message
}
''',
}


def _generate_script(interface) -> GeneratedArtifact:
    cap = _detect_script_capability(interface)
    body = _SCRIPT_TEMPLATES.get(cap, _SCRIPT_TEMPLATES["logging"])
    fname = f"{_slug(getattr(interface,'name',''))}_process.groovy"
    return GeneratedArtifact(
        kind="script",
        rel_path=f"src/main/resources/script/{fname}",
        content=body,
        note=f"Generic '{cap}' script — complete the TODO with interface logic.")


def _generate_mapping(interface, source_fields=None, target_fields=None) -> GeneratedArtifact:
    """Generate a draft message mapping.

    With source+target field lists, auto-draft direct name matches. Otherwise
    emit a structurally-valid placeholder mapping flagged for completion.
    Full schema->.mmap (the 74-function engine) is Phase 2; this is the wired
    draft so the iFlow's Mapping step resolves to a real artifact.
    """
    name = _slug(getattr(interface, "name", ""))
    matched = []
    if source_fields and target_fields:
        tgt_lower = { _norm(t): t for t in target_fields }
        for s in source_fields:
            key = _norm(s)
            if key in tgt_lower:
                matched.append((s, tgt_lower[key]))

    # Minimal valid .mmap-style XML (graphical mapping descriptor). This is a
    # draft skeleton; the real field logic is completed in the mapping editor.
    rows = ""
    for s, t in matched:
        rows += (f'    <mapping sourcePath="{s}" targetPath="{t}" '
                 f'function="direct"/>\n')
    if not rows:
        rows = ('    <!-- No schema field matches available — '
                'complete mappings in the editor -->\n')

    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<messageMapping name="{name}_mapping" draft="true">\n'
        f'{rows}'
        '</messageMapping>\n'
    )
    note = (f"Auto-drafted {len(matched)} direct field match(es); "
            "review + complete complex mappings."
            if matched else
            "Placeholder mapping — provide source/target schemas to auto-draft.")
    return GeneratedArtifact(
        kind="mapping",
        rel_path=f"src/main/resources/mapping/{name}_mapping.mmap",
        content=content, note=note)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _rel_path_for(kind: str, name: str, source_ref: str = "") -> str:
    """Where each artifact kind lives inside the package."""
    if kind == "script":
        return f"src/main/resources/script/{name}_process.groovy"
    if kind == "mapping":
        return f"src/main/resources/mapping/{name}_mapping.mmap"
    if kind == "xslt":
        return f"src/main/resources/mapping/{name}_transform.xsl"
    if kind == "js":
        return f"src/main/resources/script/{name}.js"
    if kind == "schema":
        bn = (source_ref or "schema").rsplit("/", 1)[-1]
        if not bn.endswith((".xsd", ".wsdl", ".edmx")):
            bn = f"{bn}.xsd"
        return f"src/main/resources/{bn}"
    return f"src/main/resources/{name}_{kind}"


def generate_bundle(interface, iflow_path: Path,
                    source_fields=None, target_fields=None,
                    corpus=None) -> ArtifactBundle:
    """Generate ALL supporting artifacts a wired iFlow may reference.

    With a `corpus`, CAPABILITY-MODE assembles the full relevant set across
    types — Groovy script(s), message mapping (.mmap), XSLT transform, JS
    resource, and supporting schemas (xsd/wsdl/edmx) — each selected from REAL
    learned artifacts and independently gated, so a package gets exactly the
    files it has evidence for. STRICTLY ADDITIVE: the two core types (script +
    mapping) always fall back to a generic-but-valid draft if capability-mode
    finds no confident match, so output is never worse — only richer.
    """
    bundle = ArtifactBundle(iflow_path=iflow_path)
    name = _slug(getattr(interface, "name", ""))

    artifacts: list[GeneratedArtifact] = []
    if corpus is not None:
        try:
            from scaffolder.capability_generator import (
                generate_artifacts_from_capability)
            for a in generate_artifacts_from_capability(interface, corpus) or []:
                artifacts.append(GeneratedArtifact(
                    kind=a.kind,
                    rel_path=_rel_path_for(a.kind, name, a.source_capability),
                    content=a.content, note=a.note))
        except Exception:
            artifacts = []   # any failure → clean generic fallback below

    have = {a.kind for a in artifacts}
    # the two core types are guaranteed (generic fallback fills any gap)
    if "script" not in have:
        artifacts.insert(0, _generate_script(interface))
    if "mapping" not in have:
        artifacts.append(_generate_mapping(interface, source_fields, target_fields))

    bundle.artifacts = artifacts
    bundle.notes = [a.note for a in bundle.artifacts]

    # Also write them next to the iflow on disk so the packager + user see them.
    base = iflow_path.parent.parent  # .../output/iflows -> .../output
    for art in bundle.artifacts:
        out = base / "artifacts" / name / art.rel_path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(art.content, encoding="utf-8")
    return bundle
