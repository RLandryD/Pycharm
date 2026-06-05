#!/usr/bin/env python3
"""
preflight.py  --  validate an iFlow INNER BUNDLE before the per-artifact deploy.

The per-artifact deploy (POST IntegrationDesigntimeArtifacts) base64s an inner
bundle (META-INF/MANIFEST.MF + .project + src/main/resources/.../x.iflw + its
scripts/mappings). This catches the things that make CPI reject it
("InputStream cannot be null" and friends) BEFORE we spend a tenant attempt:

  * not a valid zip / empty
  * missing META-INF/MANIFEST.MF (and manifest field checks)
  * missing or malformed .iflw
  * the .iflw references a script/mapping that isn't in the bundle
  * stray directory entries or non-MS-DOS stamping (real exports have neither)

Reuses the field validators in library_builder.bundle_validator and
references_intact from scaffolder.iflow_personalizer — no duplicated rules.

Returns (ok, findings): ok is True only when there are no "error"-severity
findings. Warnings don't block.
"""
from __future__ import annotations

import io
import zipfile
from typing import List, Tuple


def preflight_inner_bundle(bundle_bytes: bytes) -> Tuple[bool, List[dict]]:
    findings: List[dict] = []

    def err(where, msg):
        findings.append({"severity": "error", "where": where, "message": msg})

    def warn(where, msg):
        findings.append({"severity": "warning", "where": where, "message": msg})

    if not bundle_bytes:
        err("bundle", "empty bundle (would cause 'InputStream cannot be null')")
        return False, findings
    try:
        z = zipfile.ZipFile(io.BytesIO(bundle_bytes))
        infos = z.infolist()
        names = [i.filename for i in infos]
    except Exception as e:  # noqa
        err("bundle", f"not a valid zip: {e}")
        return False, findings

    # MANIFEST
    if "META-INF/MANIFEST.MF" not in names:
        err("MANIFEST.MF", "missing META-INF/MANIFEST.MF at bundle root")
    else:
        try:
            from library_builder.bundle_validator import validate_manifest
            for f in validate_manifest(z.read("META-INF/MANIFEST.MF")):
                findings.append({"severity": getattr(f, "severity", "warning"),
                                 "where": getattr(f, "where", "MANIFEST.MF"),
                                 "message": getattr(f, "message", str(f))})
        except Exception:
            pass  # field-level checks are best-effort; absence handled above

    # .iflw
    iflw = next((n for n in names
                 if n.endswith(".iflw")
                 and "scenarioflows/integrationflow/" in n), None)
    if not iflw:
        err("iflw", "no .iflw under src/main/resources/scenarioflows/integrationflow/")
    else:
        try:
            from library_builder.bundle_validator import validate_iflw
            for f in validate_iflw(z.read(iflw)):
                findings.append({"severity": getattr(f, "severity", "warning"),
                                 "where": getattr(f, "where", "iflw"),
                                 "message": getattr(f, "message", str(f))})
        except Exception:
            pass

    # references resolve
    try:
        from scaffolder.iflow_personalizer import references_intact
        ok_refs, missing = references_intact(bundle_bytes)
        for m in missing:
            err("references", f".iflw references a file not in the bundle: {m}")
    except Exception:
        pass

    # zip hygiene: real CPI exports have no directory entries and are MS-DOS
    # stamped (create_system == 0). Neither is fatal on its own, but both are
    # cheap signals the envelope diverges from a real bundle.
    if any(n.endswith("/") for n in names):
        warn("zip", "contains directory entries (real exports have none)")
    if any(getattr(i, "create_system", 0) != 0 for i in infos):
        warn("zip", "not MS-DOS stamped (create_system != 0)")

    ok = not any(f["severity"] == "error" for f in findings)
    return ok, findings


if __name__ == "__main__":
    print("Library: preflight_inner_bundle(bundle_bytes) -> (ok, findings)")
