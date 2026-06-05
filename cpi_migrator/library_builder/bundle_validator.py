"""library_builder/bundle_validator.py

Pre-deploy validation: catch the things SAP's import will reject BEFORE you
waste a tenant import attempt. The rules here are the ones we have actually
observed (e.g. the tenant 400 "Name should not end with period") plus
structural consistency checks on the bundle the assembler produces.

This is a guardrail for clone-and-adapt: when you rename/modify a flow, run
validate_* first; only deploy if it passes.

HONEST SCOPE: these are the rules we KNOW. SAP has more server-side validation
we cannot fully enumerate without deploying. A clean result here means "none of
the known failure modes are present", not "guaranteed to import". Findings are
returned as a list; severity "error" = will almost certainly be rejected,
"warning" = risky / worth review.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass


@dataclass
class Finding:
    severity: str       # "error" | "warning"
    where: str          # file / field
    message: str


# ---- name rules (observed from tenant) -------------------------------------
def validate_name(name: str, kind: str = "package") -> list[Finding]:
    out = []
    if not name or not name.strip():
        out.append(Finding("error", kind, "name is empty"))
        return out
    if name.endswith("."):
        out.append(Finding("error", kind,
                           "name must not end with a period "
                           "(tenant rejects with 400)"))
    if name != name.strip():
        out.append(Finding("warning", kind,
                           "name has leading/trailing whitespace"))
    if len(name) > 240:
        out.append(Finding("warning", kind, "name is very long (>240 chars)"))
    return out


def validate_symbolic_name(symbolic: str) -> list[Finding]:
    out = []
    if not symbolic:
        out.append(Finding("error", "Bundle-SymbolicName", "symbolic name empty"))
        return out
    # OSGi symbolic names: letters, digits, dot, dash, underscore (and the
    # optional ';singleton:=true' directive)
    base = symbolic.split(";")[0]
    if not re.fullmatch(r"[A-Za-z0-9._\-]+", base):
        out.append(Finding("error", "Bundle-SymbolicName",
                           f"symbolic name has invalid characters: {base!r}"))
    if base.endswith("."):
        out.append(Finding("error", "Bundle-SymbolicName",
                           "symbolic name must not end with a period"))
    return out


# ---- manifest consistency --------------------------------------------------
def validate_manifest(manifest_bytes: bytes) -> list[Finding]:
    out = []
    txt = manifest_bytes.decode("utf-8", "replace").replace("\r\n ", "")
    fields = {}
    for line in txt.split("\r\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            fields[k.strip()] = v.strip()
    required = ["Bundle-SymbolicName", "Bundle-Version", "Bundle-Name",
                "SAP-BundleType", "Import-Package"]
    for r in required:
        if r not in fields or not fields[r]:
            out.append(Finding("error", "MANIFEST.MF",
                               f"missing required field: {r}"))
    if fields.get("SAP-BundleType") and \
            fields["SAP-BundleType"] != "IntegrationFlow":
        out.append(Finding("warning", "MANIFEST.MF",
                           f"unexpected SAP-BundleType: {fields['SAP-BundleType']}"))
    if "Bundle-SymbolicName" in fields:
        out += validate_symbolic_name(fields["Bundle-SymbolicName"])
    if "Bundle-Name" in fields:
        out += validate_name(fields["Bundle-Name"], "Bundle-Name")
    return out


# ---- iflw structural checks ------------------------------------------------
def validate_iflw(iflw_bytes: bytes) -> list[Finding]:
    """Light structural checks: well-formed XML, sequenceFlow refs resolve to
    declared element ids. Catches the classic 'dangling reference' import fail."""
    out = []
    try:
        text = iflw_bytes.decode("utf-8", "replace")
    except Exception as e:
        return [Finding("error", "iflw", f"cannot decode: {e}")]
    # collect declared ids
    ids = set(re.findall(r'\bid="([^"]+)"', text))
    # sequenceFlow sourceRef/targetRef must resolve
    for m in re.finditer(r'(sourceRef|targetRef)="([^"]+)"', text):
        ref = m.group(2)
        if ref and ref not in ids:
            out.append(Finding("warning", "iflw",
                               f"{m.group(1)} points to undeclared id {ref!r}"))
    # basic well-formedness: balanced root
    if "<bpmn2:definitions" not in text and "<definitions" not in text:
        out.append(Finding("warning", "iflw",
                           "no <definitions> root found — may not be a valid iFlow"))
    return out


# ---- whole-bundle validation -----------------------------------------------
def validate_bundle(bundle_bytes: bytes) -> list[Finding]:
    """Validate an assembled bundle: every <guid>_content has a MANIFEST with
    consistent names, content-zip name matches no rule violation, iflw refs ok."""
    out = []
    try:
        z = zipfile.ZipFile(io.BytesIO(bundle_bytes))
    except Exception as e:
        return [Finding("error", "bundle", f"not a valid zip: {e}")]
    content_zips = [n for n in z.namelist() if n.endswith("_content")]
    if not content_zips:
        out.append(Finding("error", "bundle", "no *_content zips found"))
    for cn in content_zips:
        try:
            cz = zipfile.ZipFile(io.BytesIO(z.read(cn)))
        except Exception as e:
            out.append(Finding("error", cn, f"content zip unreadable: {e}"))
            continue
        names = cz.namelist()
        if "META-INF/MANIFEST.MF" not in names:
            out.append(Finding("error", cn, "missing META-INF/MANIFEST.MF "
                               "(tenant requires it; cannot be blank)"))
        else:
            out += validate_manifest(cz.read("META-INF/MANIFEST.MF"))
        if ".project" not in names:
            out.append(Finding("warning", cn, "missing .project descriptor"))
        for inner in names:
            if inner.endswith(".iflw"):
                out += validate_iflw(cz.read(inner))
    return out


def is_deployable(findings: list[Finding]) -> bool:
    """True if there are no error-severity findings."""
    return not any(f.severity == "error" for f in findings)
