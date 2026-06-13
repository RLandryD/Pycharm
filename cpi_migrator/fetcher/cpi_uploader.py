"""
fetcher/cpi_uploader.py

Uploads generated .iflw artifacts directly to a CPI tenant via OData API.
Handles package creation, artifact upload, and deployment status tracking.

CPI OData endpoints used:
  GET  /api/v1/IntegrationPackages                          — list packages
  POST /api/v1/IntegrationPackages                          — create package
  POST /api/v1/IntegrationDesigntimeArtifacts               — upload iFlow
  POST /api/v1/IntegrationDesigntimeArtifacts(Id='{id}',Version='active')/Deploy — deploy
  GET  /api/v1/IntegrationRuntimeArtifacts('{id}')          — check deploy status
"""
from __future__ import annotations

import base64
import io
import json
import logging
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)


@dataclass
class UploadResult:
    interface_name: str
    package_id: str
    artifact_id: str
    status: str              # "uploaded" / "deployed" / "failed" / "skipped"
    message: str = ""
    cpi_url: str = ""
    recommendation: object = None   # error_recommender.Recommendation on failure


class CPIUploader:
    """
    Uploads and optionally deploys iFlow artifacts to a CPI tenant.
    Uses an already-authenticated requests.Session from auth/authenticator.py.
    """

    # ── Designtime artifact endpoints ─────────────────────────────────────
    # Every designtime artifact type has its OWN OData entity set (endpoint),
    # confirmed from the tenant $metadata. All are m:HasStream="true" media
    # entities, so all use the SAME proven upload form (JSON body with base64
    # in ArtifactContent, no Type field). Only the URL differs per type.
    # The key is the resourceType string as it appears in resources.cnt.
    ARTIFACT_ENDPOINTS = {
        "IFlow":            "IntegrationDesigntimeArtifacts",
        "MessageMapping":   "MessageMappingDesigntimeArtifacts",
        "ValueMapping":     "ValueMappingDesigntimeArtifacts",
        "ScriptCollection": "ScriptCollectionDesigntimeArtifacts",
        "MessageType":      "MessageTypeDesigntimeArtifacts",
        "DataType":         "DataTypeDesigntimeArtifacts",
        "ServiceInterface": "ServiceInterfaceDesigntimeArtifacts",
    }
    DEFAULT_ARTIFACT_TYPE = "IFlow"

    @classmethod
    def endpoint_for(cls, artifact_type: str) -> str:
        """Return the OData entity-set name for a given artifact resourceType.
        Falls back to the iFlow endpoint for unknown/blank types."""
        return cls.ARTIFACT_ENDPOINTS.get(
            artifact_type or cls.DEFAULT_ARTIFACT_TYPE,
            cls.ARTIFACT_ENDPOINTS[cls.DEFAULT_ARTIFACT_TYPE])

    def __init__(self, base_url: str, session: requests.Session):
        self.base_url = base_url.rstrip("/")
        self.session  = session
        self._csrf_token: Optional[str] = None

    def _ensure_csrf(self) -> Optional[str]:
        """Fetch + cache an X-CSRF-Token. CPI requires it on all writes;
        without it POSTs fail with 401/403 even when auth is valid."""
        if self._csrf_token:
            return self._csrf_token
        try:
            from fetcher import wire_log
            _url = f"{self.base_url}/api/v1/"
            wire_log.log_request("CSRF fetch", "GET", _url, {"X-CSRF-Token": "Fetch"})
            resp = self.session.get(
                _url,
                headers={"X-CSRF-Token": "Fetch"},
                timeout=20,
            )
            wire_log.log_response("CSRF fetch", resp.status_code, dict(resp.headers))
            tok = resp.headers.get("X-CSRF-Token")
            if tok:
                self._csrf_token = tok
                logger.info("CSRF token obtained for writes")
            else:
                logger.warning("No X-CSRF-Token returned (HTTP %d) — writes may 401/403",
                               resp.status_code)
            return self._csrf_token
        except Exception as exc:
            logger.error("CSRF token fetch failed: %s", exc)
            try:
                from fetcher import wire_log
                wire_log.log_note(f"CSRF fetch EXCEPTION: {exc}")
            except Exception:
                pass
            return None

    def _write_headers(self) -> dict:
        """Headers for POST/PUT/DELETE including CSRF token if available."""
        h = {"Content-Type": "application/json"}
        tok = self._ensure_csrf()
        if tok:
            h["X-CSRF-Token"] = tok
        return h

    # ── Package management ────────────────────────────────────────────

    @staticmethod
    def sanitize_package_id(raw: str) -> str:
        """CPI package Id must be alphanumeric only — no underscores, dots,
        slashes, or other special characters (the API rejects them with HTTP
        400 'Property Id value cannot have a special character'). URL fragments
        and separators that leak in from system names are stripped here.

        Name (display) can keep separators; only the Id is constrained.
        """
        import re as _re
        # Drop everything that isn't a letter or digit
        cleaned = _re.sub(r"[^A-Za-z0-9]", "", str(raw))
        if not cleaned:
            cleaned = "MigrationPackage"
        # Id must start with a letter (CPI convention); prefix if it doesn't
        if not cleaned[0].isalpha():
            cleaned = "P" + cleaned
        return cleaned[:120]

    @staticmethod
    def sanitize_artifact_name(raw: str) -> str:
        """CPI artifact/package *display Name* rules (HTTP 400 otherwise):
        must start with a letter or underscore, may then contain letters,
        digits, space, period (.) or hyphen (-), and must not end with a
        period. Anything else (& ( ) / : , etc.) is replaced with a space and
        runs of whitespace are collapsed. This is the fix for the tenant
        rejection of 'Order Sync & Validate (EU/US)'."""
        import re as _re
        s = _re.sub(r"[^A-Za-z0-9 ._-]", " ", str(raw))   # illegal -> space
        s = _re.sub(r"\s+", " ", s).strip()                # collapse runs
        s = s.rstrip(".").strip()                          # no trailing period
        if not s:
            s = "IFlow"
        if not _re.match(r"[A-Za-z_]", s):                 # must start letter/_
            s = "X" + s
        return s[:240]

    def ensure_package(self, package_id: str, package_name: str,
                       description: str = "", owner_email: str = "") -> bool:
        """Create package if it doesn't exist. Returns True if ready.

        owner_email, if given, is appended to the package ShortText so the
        human owner is recorded in the package (the system CreatedBy stamp is
        always the authenticated service account and cannot be overridden via
        the API)."""
        # Enforce a valid Id regardless of what the caller passed
        package_id = self.sanitize_package_id(package_id)

        if self._package_exists(package_id):
            logger.debug("Package %s already exists", package_id)
            return True

        short_text = description or f"Migrated from PI/PO — {package_name}"
        if owner_email:
            short_text = f"{short_text} | Owner: {owner_email}"

        # NOTE: do NOT send "Mode" — the CPI API rejects it with HTTP 400
        # ("Remove Mode from the request payload"). Confirmed against a real
        # trial tenant. ShortText/Version/Vendor are accepted.
        payload = {
            "Id":          package_id,
            "Name":        package_name,
            "ShortText":   short_text,
            "Version":     "1.0.0",
            "Vendor":      owner_email or "",
        }
        try:
            from fetcher import wire_log
            _url = f"{self.base_url}/api/v1/IntegrationPackages"
            _hdrs = self._write_headers()
            wire_log.log_request("create package", "POST", _url, _hdrs, str(payload))
            resp = self.session.post(
                _url,
                json=payload,
                headers=_hdrs,
                timeout=30,
            )
            wire_log.log_response("create package", resp.status_code,
                                  dict(resp.headers), resp.text)
            if resp.status_code in (200, 201):
                logger.info("Created package: %s", package_id)
                return True
            logger.error("Failed to create package %s: %d %s",
                         package_id, resp.status_code, resp.text[:200])
            return False
        except Exception as exc:
            logger.error("Package creation error: %s", exc)
            try:
                from fetcher import wire_log
                wire_log.log_note(f"create package EXCEPTION: {exc}")
            except Exception:
                pass
            return False

    def _package_exists(self, package_id: str) -> bool:
        try:
            resp = self.session.get(
                f"{self.base_url}/api/v1/IntegrationPackages('{package_id}')",
                params={"$format": "json"},
                timeout=15,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def list_packages(self) -> list[dict]:
        try:
            resp = self.session.get(
                f"{self.base_url}/api/v1/IntegrationPackages",
                params={"$format": "json", "$top": 200},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("d", {}).get("results", data.get("value", []))
        except Exception as exc:
            logger.error("Failed to list packages: %s", exc)
            return []

    # ── Artifact upload ───────────────────────────────────────────────

    def upload_raw_bundle(
        self,
        zip_bytes: bytes,
        package_id: str,
        artifact_id: str,
        artifact_name: str,
        overwrite: bool = True,
        artifact_type: str = "IFlow",
    ) -> UploadResult:
        """Upload a zip bundle EXACTLY as given — no repackaging, no manifest
        substitution. Used for the reverse-engineering test and for uploading
        real exported bundles. artifact_type selects the endpoint.

        Idempotent: if the artifact exists, _post_artifact updates it (new
        version) rather than colliding. overwrite=False skips existing ones.
        """
        package_id  = self.sanitize_package_id(package_id)
        artifact_id = self.sanitize_package_id(artifact_id)
        endpoint    = self.endpoint_for(artifact_type)

        result = UploadResult(
            interface_name=artifact_name, package_id=package_id,
            artifact_id=artifact_id, status="failed",
            cpi_url=f"{self.base_url}/api/v1/{endpoint}"
                    f"(Id='{artifact_id}',Version='active')")

        if not overwrite and self._artifact_exists(artifact_id, endpoint):
            result.status  = "skipped"
            result.message = "Artifact already exists (overwrite=False)"
            return result

        # Guard: never POST empty content. An empty/missing bundle produces a
        # 32-byte body and CPI's HTTP 500 "InputStream cannot be null". The
        # upload_iflow path already guards this; mirror it here so no upload
        # route can send empty ArtifactContent.
        if not zip_bytes:
            result.status  = "failed"
            result.message = ("Empty bundle — nothing to upload (would cause "
                              "CPI 'InputStream cannot be null'). Check the "
                              "artifact was generated/packaged correctly.")
            return result

        from fetcher import wire_log
        wire_log.log_note(f"RAW bundle upload [{artifact_type}] — "
                          f"{len(zip_bytes)} zip bytes, unchanged")
        try:
            self._post_artifact(zip_bytes, package_id, artifact_id,
                                artifact_name, result, artifact_type)
        except Exception as exc:
            result.message = str(exc)
            logger.error("Upload error for %s: %s", artifact_id, exc)
        return result

    def upload_iflow(
        self,
        iflw_path: Path,
        package_id: str,
        artifact_id: str,
        artifact_name: str,
        overwrite: bool = True,
        parameters_prop: str = "",
        extra_artifacts: Optional[list] = None,
        sender_adapter: str = "",
        receiver_adapter: str = "",
    ) -> UploadResult:
        """
        Upload a .iflw file to CPI.
        The iflw is packaged into a zip before upload (CPI requirement).
        extra_artifacts: (rel_path, content) tuples for referenced scripts/maps.

        Single build path: the .iflw produced by the generator (clean-room
        regeneration when a source iFlow is present, else the self-contained
        timer scaffold) is packaged and deployed as-is.
        """
        # Enforce valid IDs (same rules as ensure_package) so the package the
        # artifact targets matches the one that was created, and the artifact
        # Id itself is accepted by the API.
        package_id  = self.sanitize_package_id(package_id)
        artifact_id = self.sanitize_package_id(artifact_id)

        result = UploadResult(
            interface_name=artifact_name,
            package_id=package_id,
            artifact_id=artifact_id,
            status="failed",
            cpi_url=f"{self.base_url}/api/v1/IntegrationDesigntimeArtifacts"
                    f"(Id='{artifact_id}',Version='active')",
        )

        # Check if artifact already exists
        if self._artifact_exists(artifact_id) and not overwrite:
            result.status  = "skipped"
            result.message = "Artifact already exists (overwrite=False)"
            return result

        # Package the generated .iflw + its validated manifest/.project + every
        # resource the generator staged in <id>__meta/src/main/resources (real
        # scripts/mappings/schemas + the ORIGINAL parameter pair). Passing
        # extra_artifacts=None here used to silently drop the resolved
        # resources — the tenant then reported every script as 'not found'
        # even though the scaffolder had resolved them.
        zip_bytes = self._package_iflow(iflw_path, artifact_id, artifact_name,
                                        parameters_prop,
                                        extra_artifacts=extra_artifacts)
        if not zip_bytes:
            result.message = f"Failed to package iFlow from {iflw_path}"
            return result

        # Upload — _post_artifact creates if new, updates if it already exists
        # (idempotent, so re-runs don't collide).
        try:
            self._post_artifact(zip_bytes, package_id, artifact_id,
                                artifact_name, result, artifact_type="IFlow")
        except Exception as exc:
            result.message = str(exc)
            logger.error("Upload error for %s: %s", artifact_id, exc)
        return result

    def _post_artifact(self, zip_bytes: bytes, package_id: str, artifact_id: str,
                       artifact_name: str, result: "UploadResult",
                       artifact_type: str = "IFlow"):
        """Create or update a designtime artifact via its typed endpoint.

        Proven upload form (validated against a live tenant, HTTP 201):
          POST {endpoint}  with JSON body {Id, Name, PackageId, ArtifactContent:
          <base64 zip>} sent as a JSON *string* (data=), Content-Type
          application/json, X-CSRF-Token header. No `Type`/`Content` fields
          (the tenant rejects those with 400).

        Idempotency: if the artifact already exists, CPI's create POST fails;
        we then issue an UPDATE (PUT to the keyed entity with a bumped/again
        ArtifactContent) so re-runs don't collide. Create is tried first; on a
        "already exists"-type response we fall back to update.

        artifact_type selects the endpoint (iFlow, MessageMapping, …).
        """
        import json as _json
        from fetcher import wire_log

        # Deepest guard: refuse to build a payload with empty ArtifactContent.
        # This is the single choke-point every upload route passes through, so
        # guarding here makes the "InputStream cannot be null" (empty-content)
        # failure structurally impossible regardless of caller.
        if not zip_bytes:
            result.status  = "failed"
            result.message = ("Empty artifact content — refused (would cause CPI "
                              "'InputStream cannot be null'). Bundle was empty.")
            wire_log.log_note(f"BLOCKED empty-content upload for {artifact_id}")
            return

        endpoint = self.endpoint_for(artifact_type)
        zip_b64  = base64.b64encode(zip_bytes).decode("utf-8")
        coll_url = f"{self.base_url}/api/v1/{endpoint}"
        payload  = {
            "Id": artifact_id, "Name": self.sanitize_artifact_name(artifact_name),
            "PackageId": package_id, "ArtifactContent": zip_b64,
        }

        exists = self._artifact_exists(artifact_id, endpoint)
        if exists:
            self._replace_artifact(zip_bytes, package_id, artifact_id,
                                   artifact_name, result, artifact_type, endpoint)
            return

        # ── CREATE (POST to the collection) ──
        body_str = _json.dumps(payload)
        hdrs = self._write_headers()
        hdrs["Content-Type"] = "application/json"
        # Log the ACTUAL body (so wire_log's size reflects the real payload, not
        # the length of a description string — that was masking the true size).
        wire_log.log_note(f"create artifact [{artifact_type}] payload: "
                          f"{len(body_str)} body bytes, {len(zip_bytes)} zip "
                          f"bytes, {len(zip_b64)} b64 chars")
        wire_log.log_request(
            f"create artifact [{artifact_type}]", "POST", coll_url, hdrs,
            body_str)
        resp = self.session.post(coll_url, data=body_str, headers=hdrs, timeout=60)
        wire_log.log_response(f"create artifact [{artifact_type}]",
                              resp.status_code, dict(resp.headers), resp.text)

        if resp.status_code in (200, 201):
            result.status  = "uploaded"
            result.message = f"Created {artifact_type} in CPI Design"
            logger.info("Created %s %s in package %s",
                        artifact_type, artifact_id, package_id)
            return

        # If it already existed (race / stale existence check), replace it.
        if resp.status_code in (409,) or "already exist" in resp.text.lower():
            self._replace_artifact(zip_bytes, package_id, artifact_id,
                                   artifact_name, result, artifact_type, endpoint)
            return

        # Manually-named target package that nobody created yet (live run:
        # 'Package ID P1Test does not exist' × every artifact, three runs in
        # a row) — create it and retry the artifact ONCE.
        if resp.status_code == 404 and "does not exist" in resp.text.lower():
            wire_log.log_note(
                f"Target package '{package_id}' does not exist — creating it "
                "and retrying the artifact once")
            try:
                self.ensure_package(package_id, package_id,
                                    "Created by migration tool (auto)")
            except Exception as exc:
                logger.warning("auto-create package %s failed: %s",
                               package_id, exc)
                self._report_failure(resp, artifact_id, coll_url, result)
                return
            wire_log.log_request(
                f"retry create artifact [{artifact_type}]", "POST", coll_url,
                hdrs, body_str)
            resp2 = self.session.post(coll_url, data=body_str, headers=hdrs,
                                      timeout=60)
            wire_log.log_response(f"retry create artifact [{artifact_type}]",
                                  resp2.status_code, dict(resp2.headers),
                                  resp2.text)
            if resp2.status_code in (200, 201):
                result.status  = "uploaded"
                result.message = (f"Created {artifact_type} (target package "
                                  f"'{package_id}' auto-created)")
                logger.info("Created %s %s in auto-created package %s",
                            artifact_type, artifact_id, package_id)
                return
            self._report_failure(resp2, artifact_id, coll_url, result)
            return

        self._report_failure(resp, artifact_id, coll_url, result)

    def _existing_artifact_package(self, artifact_id: str,
                                   endpoint: str) -> str:
        """Which package does the EXISTING artifact with this id live in?
        Artifact ids are TENANT-GLOBAL — in a same-tenant pull→regenerate→
        upload round trip the regenerated copy collides with the SOURCE
        artifact in its original package. Returns '' when unknown."""
        try:
            r = self.session.get(
                f"{self.base_url}/api/v1/{endpoint}"
                f"(Id='{artifact_id}',Version='active')?$format=json",
                timeout=30)
            if r.status_code == 200:
                d = r.json().get("d", {})
                return d.get("PackageId", "") or ""
        except Exception:
            pass
        return ""

    @staticmethod
    def _suffixed_id(artifact_id: str, suffix: str = "WB") -> str:
        return (artifact_id[: 100 - len(suffix) - 1] + "_" + suffix)

    def _replace_artifact(self, zip_bytes: bytes, package_id: str,
                          artifact_id: str, artifact_name: str,
                          result: "UploadResult", artifact_type: str,
                          endpoint: str):
        """Existing artifact + a re-skinned bundle. Every bundle this tool
        uploads (clone-and-adapt or generated) gets its Bundle-SymbolicName
        rewritten by CPI on create, so an in-place PUT always returns HTTP 400
        ('change in Bundle-symbolicName'). Skip that doomed PUT and go straight
        to delete + recreate (the proven 201 path). The caller's deploy step
        then redeploys, restoring the runtime instance.

        SAFETY (learned from a live same-tenant showcase): the colliding
        artifact may live in a DIFFERENT package — it is then the SOURCE
        flow, not our previous upload, and deleting it would DESTROY the
        user's original. In that case we never delete: the new artifact is
        created under a suffixed id instead."""
        from fetcher import wire_log
        owner_pkg = self._existing_artifact_package(artifact_id, endpoint)
        if owner_pkg and owner_pkg != package_id:
            new_id = self._suffixed_id(artifact_id)
            wire_log.log_note(
                f"{artifact_id} already exists in package '{owner_pkg}' "
                f"(NOT the upload target '{package_id}') — that is the "
                f"source artifact; NOT deleting it. Creating as '{new_id}' "
                f"instead.")
            logger.warning(
                "Artifact id collision with source package %s — creating "
                "%s instead of deleting the original", owner_pkg, new_id)
            self._recreate_after_delete(zip_bytes, package_id, new_id,
                                        artifact_name, result, artifact_type)
            if result.status in ("created", "recreated"):
                result.message = (f"Created as {new_id} — id collided with "
                                  f"the source artifact in '{owner_pkg}' "
                                  "(original untouched)")
            return
        wire_log.log_note(
            f"{artifact_id} already exists — re-skinned bundle changes the "
            f"symbolic name, so deleting + recreating (skipping doomed PUT)")
        self._delete_artifact(artifact_id, package_id, endpoint)
        self._recreate_after_delete(zip_bytes, package_id, artifact_id,
                                    artifact_name, result, artifact_type)

    def _update_artifact(self, zip_bytes: bytes, package_id: str,
                         artifact_id: str, artifact_name: str,
                         result: "UploadResult", artifact_type: str = "IFlow"):
        """Update an existing artifact via PUT to the keyed entity, with a
        delete-then-recreate fallback.

        Two-stage strategy, learned from live-tenant behaviour:

        Stage 1 — PUT to (Id='…',Version='active') with the update payload.
        The update payload shape differs from create: it carries Name +
        ArtifactContent (the proven SAP/Piper update form). This works when the
        new bundle keeps the same Bundle-SymbolicName as the stored artifact.

        Stage 2 — fallback. The tenant rejects the PUT with HTTP 400
        "Could not update artifact … due to change in the Bundle-symbolicName"
        when the uploaded bundle's internal symbolic name doesn't match what's
        stored (common for re-uploads of externally-built bundles, where the
        manifest's symbolic name was regenerated). In that case we DELETE the
        existing artifact and re-CREATE it via the proven 201 create path. This
        sidesteps the symbolic-name comparison entirely.

        The fallback is safe because at this point the artifact is only in the
        design-time workspace (not yet deployed); delete+recreate yields the
        same end state as an in-place update.
        """
        import json as _json
        from fetcher import wire_log
        endpoint = self.endpoint_for(artifact_type)
        zip_b64  = base64.b64encode(zip_bytes).decode("utf-8")
        key_url  = (f"{self.base_url}/api/v1/{endpoint}"
                    f"(Id='{artifact_id}',Version='active')")
        payload  = {
            "Id": artifact_id, "Name": self.sanitize_artifact_name(artifact_name),
            "PackageId": package_id, "ArtifactContent": zip_b64,
        }
        body_str = _json.dumps(payload)
        hdrs = self._write_headers()
        hdrs["Content-Type"] = "application/json"
        wire_log.log_request(
            f"update artifact [{artifact_type}]", "PUT", key_url, hdrs,
            body_str)
        resp = self.session.put(key_url, data=body_str, headers=hdrs, timeout=60)
        wire_log.log_response(f"update artifact [{artifact_type}]",
                              resp.status_code, dict(resp.headers), resp.text)
        if resp.status_code in (200, 201, 202, 204):
            result.status  = "updated"
            result.message = f"Updated existing {artifact_type} in CPI Design"
            logger.info("Updated %s %s in package %s",
                        artifact_type, artifact_id, package_id)
            return

        # Stage 2 — symbolic-name mismatch (or any update rejection): fall back
        # to delete + recreate using the proven create path.
        symbolic_name_conflict = (
            resp.status_code == 400
            and "symbolicname" in resp.text.lower().replace("-", "").replace(" ", "")
        )
        if symbolic_name_conflict or resp.status_code in (400, 405, 501):
            wire_log.log_note(
                f"{artifact_id} update rejected (HTTP {resp.status_code}); "
                f"falling back to delete + recreate")
            self._delete_artifact(artifact_id, package_id, endpoint)
            self._recreate_after_delete(zip_bytes, package_id, artifact_id,
                                        artifact_name, result, artifact_type)
            return

        self._report_failure(resp, artifact_id, key_url, result)

    def _recreate_after_delete(self, zip_bytes: bytes, package_id: str,
                               artifact_id: str, artifact_name: str,
                               result: "UploadResult", artifact_type: str = "IFlow"):
        """Re-CREATE an artifact via the proven POST path, after a delete.

        Used by the update fallback. This is a straight create (no existence
        check, no further update fallback — we just deleted it) so re-runs that
        couldn't update in place still converge to the new content.
        """
        import json as _json
        from fetcher import wire_log
        endpoint = self.endpoint_for(artifact_type)
        zip_b64  = base64.b64encode(zip_bytes).decode("utf-8")
        coll_url = f"{self.base_url}/api/v1/{endpoint}"
        payload  = {
            "Id": artifact_id, "Name": self.sanitize_artifact_name(artifact_name),
            "PackageId": package_id, "ArtifactContent": zip_b64,
        }
        body_str = _json.dumps(payload)
        hdrs = self._write_headers()
        hdrs["Content-Type"] = "application/json"
        wire_log.log_request(
            f"recreate artifact [{artifact_type}]", "POST", coll_url, hdrs,
            body_str)
        resp = self.session.post(coll_url, data=body_str, headers=hdrs, timeout=60)
        wire_log.log_response(f"recreate artifact [{artifact_type}]",
                              resp.status_code, dict(resp.headers), resp.text)
        if resp.status_code in (200, 201):
            result.status  = "updated"
            result.message = f"Replaced existing {artifact_type} (delete + recreate)"
            logger.info("Recreated %s %s in package %s (after delete)",
                        artifact_type, artifact_id, package_id)
            return
        self._report_failure(resp, artifact_id, coll_url, result)

    def _report_failure(self, resp, artifact_id: str, url: str,
                        result: "UploadResult"):
        """Shared detailed failure logging + diagnosis. Runs the error
        recommender on the FULL response body (not truncated) and attaches a
        structured recommendation (cause + concrete fix) to the result."""
        full_body = resp.text or ""
        detail = full_body[:300]
        result.status = "failed"
        # diagnose: parse the real OData error + recommend a fix (upload stage)
        try:
            from fetcher.error_recommender import recommend
            rec = recommend("upload", resp.status_code, full_body)
            result.recommendation = rec
            # message carries the tenant's real reason + the recommended fix
            result.message = (f"Upload failed: {resp.status_code} — "
                              f"{rec.cause} FIX: {rec.recommendation}")
        except Exception:   # diagnosis must never mask the original failure
            result.message = f"Upload failed: {resp.status_code} — {detail}"
        auth_present = (
            "Authorization" in self.session.headers
            or "authorization" in {k.lower() for k in self.session.headers}
            or self.session.auth is not None
        )
        logger.error(
            "Upload failed for %s: HTTP %d\n  URL: %s\n"
            "  CSRF token present: %s\n  Auth configured on session: %s\n"
            "  Response: %s",
            artifact_id, resp.status_code, url,
            bool(self._csrf_token), auth_present, detail)
        if resp.status_code == 401:
            logger.error("  → 401 hint: needs OAuth2 client (BTP service key), "
                         "not tenant login user/password.")
        elif resp.status_code == 403:
            logger.error("  → 403 hint: auth works but the OAuth client lacks "
                         "the write role (e.g. 'WorkspacePackagesEdit').")



    def list_runtime_artifacts(self) -> list[dict]:
        """List all DEPLOYED runtime artifacts with their status, via the
        documented OData collection GET /api/v1/IntegrationRuntimeArtifacts.
        Returns [{Id, Version, Status, ...}]. This is "what's deployed and is it
        Started/Error" — distinct from MessageProcessingLogs (message runs).
        Records last_status/last_error so the UI can explain an empty result."""
        url = f"{self.base_url}/api/v1/IntegrationRuntimeArtifacts"
        self.last_runtime_status = 0
        self.last_runtime_error = ""
        try:
            resp = self.session.get(url, params={"$format": "json"}, timeout=30)
        except Exception as exc:                       # noqa
            self.last_runtime_status = -1
            self.last_runtime_error = str(exc)
            return []
        self.last_runtime_status = resp.status_code
        if resp.status_code != 200:
            self.last_runtime_error = (resp.text or "")[:300]
            return []
        try:
            data = resp.json()
        except Exception as exc:                       # noqa
            self.last_runtime_error = f"JSON parse failed: {exc}"
            return []
        rows = data.get("d", data)
        if isinstance(rows, dict):
            rows = rows.get("results", rows.get("value", []))
        return rows if isinstance(rows, list) else []

    def deploy_iflow(self, artifact_id: str) -> str:
        """
        Trigger deployment of an uploaded iFlow.
        Returns deployment status: "started" / "failed"

        On failure, diagnoses the error (deploy stage) and stores a structured
        recommendation on self.last_deploy_recommendation so callers can surface
        the tenant's real reason + a concrete fix.
        """
        self.last_deploy_recommendation = None
        try:
            # SAP's documented deploy endpoint is a function import at the
            # service root — POST /api/v1/DeployIntegrationDesigntimeArtifact
            # ?Id='{id}'&Version='active'. The bound-action form
            # IntegrationDesigntimeArtifacts(Id=..,Version=..)/Deploy does NOT
            # exist on this OData service and returns 404. Version='active'
            # deploys the current active version. The call is async: a 202 with
            # a task id means accepted; runtime_status/wait_for_deploy then poll
            # for STARTED / ERROR.
            resp = self.session.post(
                f"{self.base_url}/api/v1/DeployIntegrationDesigntimeArtifact"
                f"?Id='{artifact_id}'&Version='active'",
                headers=self._write_headers(),
                timeout=30,
            )
            if resp.status_code in (200, 202):
                logger.info("Deployment triggered for %s", artifact_id)
                return "started"
            logger.warning("Deploy returned %d for %s", resp.status_code, artifact_id)
            try:
                from fetcher.error_recommender import recommend
                self.last_deploy_recommendation = recommend(
                    "deploy", resp.status_code, resp.text or "")
            except Exception:
                pass
            return "failed"
        except Exception as exc:
            logger.error("Deploy error for %s: %s", artifact_id, exc)
            return "failed"

    def runtime_status(self, artifact_id: str) -> str:
        """Return the runtime deployment status of an artifact, e.g. STARTING /
        STARTED / ERROR, via the documented OData entity
        IntegrationRuntimeArtifacts('id'). Returns "" if not found yet (the
        runtime entry can lag a moment behind the Deploy call)."""
        url = (f"{self.base_url}/api/v1/IntegrationRuntimeArtifacts('{artifact_id}')")
        try:
            resp = self.session.get(url, params={"$format": "json"}, timeout=30)
            if resp.status_code == 200:
                d = resp.json().get("d", {})
                return str(d.get("Status", "") or "")
            return ""
        except Exception as exc:                       # noqa
            logger.info("runtime_status fetch failed for %s: %s", artifact_id, exc)
            return ""

    def wait_for_deploys(self, artifact_ids: list, timeout: int = 120,
                         interval: int = 5) -> dict:
        """Poll MANY artifacts together so their settle windows OVERLAP instead
        of summing. Each round does one quick runtime_status GET per still-
        pending artifact, then sleeps once; an artifact drops out as soon as it
        reads STARTED/ERROR. Total time ≈ the slowest single deploy, not the sum
        of all of them (the per-iFlow blocking wait was the main run-time cost).
        Returns {artifact_id: final_status} ("" if it never appeared)."""
        import time as _time
        pending = [a for a in artifact_ids if a]
        final = {a: "" for a in pending}
        if not pending:
            return final
        deadline = _time.time() + max(1, timeout)
        while pending and _time.time() < deadline:
            still = []
            for aid in pending:
                s = self.runtime_status(aid)
                if s:
                    final[aid] = s
                if s in ("STARTED", "ERROR"):
                    continue                       # settled — drop from polling
                still.append(aid)
            pending = still
            if pending:
                _time.sleep(max(1, interval))
        return final

    def wait_for_deploy(self, artifact_id: str, timeout: int = 90,
                        interval: int = 5) -> str:
        """Poll runtime_status until it settles (STARTED / ERROR) or timeout.
        Returns the final status seen ("" if it never appeared). This is the
        OData equivalent of the Web-UI's /deploystatus polling, so callers can
        report a real outcome instead of assuming 201 == running."""
        import time as _time
        deadline = _time.time() + max(1, timeout)
        last = ""
        while _time.time() < deadline:
            last = self.runtime_status(artifact_id)
            if last in ("STARTED", "ERROR"):
                return last
            _time.sleep(max(1, interval))
        return last

    def fetch_deploy_error_detail(self, artifact_id: str):
        """When a deploy ends in ERROR, fetch the tenant's REAL reason from the
        runtime artifact's ErrorInformation sub-resource and diagnose it.

        Endpoint:
          GET /api/v1/IntegrationRuntimeArtifacts('{id}')/ErrorInformation/$value

        Returns an error_recommender.Recommendation (deploy stage), or None.
        This is the deep deploy-failure detail that runtime_status (which only
        returns the coarse 'ERROR' string) discards. Claude cannot test this
        against the live tenant — the endpoint shape is per SAP docs; the user
        confirms the actual response."""
        try:
            resp = self.session.get(
                f"{self.base_url}/api/v1/IntegrationRuntimeArtifacts"
                f"('{artifact_id}')/ErrorInformation/$value",
                timeout=15,
            )
            if resp.status_code != 200 or not resp.text:
                return None
            from fetcher.error_recommender import recommend
            return recommend("deploy", 0, resp.text)
        except Exception:
            return None

    # ── Bulk operations ───────────────────────────────────────────────

    def upload_all(
        self,
        assessments: list,
        configs: dict,
        iflow_dir: Path,
        auto_deploy: bool = False,
        progress_callback=None,
    ) -> list[UploadResult]:
        """
        Upload all generated iFlows to CPI.
        Groups by package, creates packages as needed.
        """
        from scaffolder.pipeline_scaffolder import generate_package_name, generate_iflow_name

        results      = []
        packages_created = set()

        for i, a in enumerate(assessments):
            iface     = a.interface
            cfg       = configs.get(iface.name)

            # Determine package
            pkg_id    = generate_package_name(
                company_code="MIGRATION",
                sender_system=iface.sender_system or "SOURCE",
                receiver_system=iface.receiver_system or "TARGET",
                domain=iface.namespace or "",
            ).replace(" ", "_")[:50]

            art_id    = generate_iflow_name(
                direction="OUT" if iface.sender_system else "IN",
                sender_system=iface.sender_system or "SRC",
                receiver_system=iface.receiver_system or "TGT",
                business_object=iface.message_interface or iface.name,
                action="Process",
            ).replace(" ", "_")[:60]

            # Create package once
            if pkg_id not in packages_created:
                self.ensure_package(pkg_id, pkg_id.replace("_", " "))
                packages_created.add(pkg_id)

            # Find iflow file
            iflw_candidates = list(iflow_dir.glob(f"*{iface.name[:20]}*.iflw"))
            if not iflw_candidates:
                results.append(UploadResult(
                    interface_name=iface.name,
                    package_id=pkg_id,
                    artifact_id=art_id,
                    status="skipped",
                    message="No .iflw file found in output/iflows/",
                ))
                continue

            iflw_path = iflw_candidates[0]
            result    = self.upload_iflow(iflw_path, pkg_id, art_id, iface.name)

            # Deploy after a successful create OR an update/recreate. The
            # delete+recreate fallback (clone symbolic-name always changes →
            # update 400 → delete+recreate) UNDEPLOYS the running runtime
            # instance via the design-time delete; if we only redeployed on
            # "uploaded" (first-time create), a re-deploy would silently remove
            # the artifact from the runtime monitor. "updated" must redeploy too.
            if result.status in ("uploaded", "updated") and auto_deploy:
                deploy_status = self.deploy_iflow(art_id)
                if deploy_status == "started":
                    result.status  = "deployed"
                    result.message = "Uploaded and deployment triggered"

            results.append(result)

            if progress_callback:
                progress_callback(i + 1, len(assessments), result)

        return results

    # ── Helpers ───────────────────────────────────────────────────────

    def _artifact_exists(self, artifact_id: str,
                         endpoint: str = "IntegrationDesigntimeArtifacts") -> bool:
        try:
            resp = self.session.get(
                f"{self.base_url}/api/v1/{endpoint}"
                f"(Id='{artifact_id}',Version='active')",
                params={"$format": "json"},
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def _delete_artifact(self, artifact_id: str, package_id: str,
                         endpoint: str = "IntegrationDesigntimeArtifacts"):
        try:
            from fetcher import wire_log
            del_url = (f"{self.base_url}/api/v1/{endpoint}"
                       f"(Id='{artifact_id}',Version='active')")
            hdrs = self._write_headers()
            wire_log.log_request("delete artifact", "DELETE", del_url, hdrs, "")
            resp = self.session.delete(del_url, headers=hdrs, timeout=15)
            wire_log.log_response("delete artifact", resp.status_code,
                                  dict(resp.headers), resp.text)
        except Exception:
            pass

    def delete_package(self, package_id: str) -> bool:
        """DELETE an entire integration package (and everything in it) from the
        tenant. Used by the opt-in 'delete before upload' clean-slate path so a
        fresh whole-package POST lands on a non-existent id (avoids the 409).

        DESTRUCTIVE: removes ALL artifacts in the package. Returns True on a
        successful delete or if the package didn't exist (404 — already clean).
        """
        package_id = self.sanitize_package_id(package_id)
        try:
            from fetcher import wire_log
            del_url = (f"{self.base_url}/api/v1/"
                       f"IntegrationPackages('{package_id}')")
            hdrs = self._write_headers()
            wire_log.log_request(f"delete package '{package_id}'", "DELETE",
                                 del_url, hdrs, "")
            resp = self.session.delete(del_url, headers=hdrs, timeout=30)
            wire_log.log_response(f"delete package '{package_id}'",
                                  resp.status_code, dict(resp.headers), resp.text)
            # 200/204 = deleted; 404 = wasn't there = already clean
            return resp.status_code in (200, 202, 204, 404)
        except Exception as exc:   # noqa
            logger.error("Delete package error for %s: %s", package_id, exc)
            return False

    def build_package_export_zip(self, artifact_bundles: list,
                                 package_id: str, package_name: str) -> bytes:
        """Assemble a FULL package export zip (multi-artifact format), wrapping
        one or more artifact bundles — the whole-package alternative to the
        per-artifact create call; uploaded in one shot to IntegrationPackages.

        Structure (matches a real, import-validated export — see the
        assembler_built specimen):
            ExportInformation.info   (Name=/Date= — must be NON-blank; a blank
                                      file blocks the import)
            contentmetadata.md       (base64 property listing incl.
                                      HashVersion/EncodingVersion — must be
                                      NON-blank; CPI reads it to parse the pkg)
            hash                     (JSON array of per-artifact SHA-256; not
                                      strictly enforced but populated to match
                                      the real format)
            resources.cnt            (JSON manifest; each IFlow entry's `id`
                                      MUST match its `<id>_content` filename;
                                      MUST also include a ContentPackage entry
                                      for the package itself, or import fails)
            <guid>_content           (nested zip = single-artifact bundle,
                                      named by a generated GUID, not a readable
                                      name, to mirror real exports)
        artifact_bundles: list of (artifact_id, artifact_name, inner_zip_bytes).
        """
        # Delegate to the tested export builder, which emits a VALID export:
        # resources.cnt WITH the ContentPackage entry AND AGGREGATION relations,
        # contentmetadata.md, ExportInformation.info, a format-valid hash, and one
        # <id>_content per artifact. (The previous inline builder omitted the
        # relations and used a thin ContentPackage entry.)
        from fetcher.cpi_package_export import build_export_zip
        env = "it-design"
        try:
            host = (self.base_url or "").split("//", 1)[-1].split(".", 1)[0]
            if host:
                env = host
        except Exception:
            pass
        artifacts = [
            {"type": "IFlow", "name": aname, "content": inner}
            for (aid, aname, inner) in artifact_bundles
        ]
        return build_export_zip(
            package={"id": package_id, "name": package_name},
            artifacts=artifacts,
            environment=env,
        )

    def upload_as_package(self, iflw_path: Path, package_id: str,
                          package_name: str, artifact_id: str,
                          artifact_name: str, parameters_prop: str = "",
                          extra_artifacts=None, sender_adapter: str = "",
                          receiver_adapter: str = "") -> "UploadResult":
        """Upload an iFlow by building a FULL package zip and POSTing it to
        IntegrationPackages (whole-package import). If the package already
        exists, CPI does not support replacing it via $value (501), so we fall
        back to the proven per-artifact create into that package.
        """
        package_id  = self.sanitize_package_id(package_id)
        artifact_id = self.sanitize_package_id(artifact_id)
        result = UploadResult(
            interface_name=artifact_name, package_id=package_id,
            artifact_id=artifact_id, status="failed",
            cpi_url=f"{self.base_url}/api/v1/IntegrationPackages")
        # Single build path: package the generated .iflw.
        inner = self._package_iflow(iflw_path, artifact_id, artifact_name,
                                    parameters_prop, extra_artifacts=extra_artifacts)
        if not inner:
            result.message = f"Failed to package iFlow from {iflw_path}"
            return result
        pkg_zip = self.build_package_export_zip(
            [(artifact_id, artifact_name, inner)], package_id, package_name)
        import base64 as _b64, json as _json
        from fetcher import wire_log
        pkg_b64 = _b64.b64encode(pkg_zip).decode("utf-8")
        payload = {"Id": package_id, "Name": package_name,
                   "ShortText": "Migrated from PI/PO",
                   "Version": "1.0.0", "PackageContent": pkg_b64}
        body_str = _json.dumps(payload)
        # Plain POST to create the package WITH its content in one shot. (We do
        # NOT pre-create the package in this mode, so this lands on a new id.)
        # Note: ?Overwrite=true is unreliable for create (some tenants answer
        # "Not Implemented"), so create is a plain POST; if the package already
        # exists we fall back to a PUT update of its $value.
        url = f"{self.base_url}/api/v1/IntegrationPackages"
        hdrs = self._write_headers()
        hdrs["Content-Type"] = "application/json"
        try:
            wire_log.log_note(f"create package shell: {len(body_str)} body bytes")
            wire_log.log_request("create package shell", "POST", url, hdrs, body_str)
            resp = self.session.post(url, data=body_str, headers=hdrs, timeout=60)
            wire_log.log_response("create package shell", resp.status_code,
                                  dict(resp.headers), resp.text)
            created = resp.status_code in (200, 201)
            exists = resp.status_code == 409 or "already exist" in resp.text.lower()
            if not (created or exists):
                self._report_failure(resp, artifact_id, url, result)
                return result
            # A package POST only creates the SHELL — CPI does NOT extract the
            # artifacts from PackageContent (that 201 is an empty package, which
            # is why the iFlow "wasn't added" and deploy then 404'd). So ALWAYS
            # add the iFlow via the proven per-artifact create (creates if new,
            # updates if present) — this is what actually puts it in the package.
            wire_log.log_note(f"package {'created' if created else 'exists'} — "
                              "adding iFlow via per-artifact create")
            self._post_artifact(inner, package_id, artifact_id,
                                artifact_name, result, artifact_type="IFlow")
        except Exception as exc:   # noqa
            result.message = str(exc)
            logger.error("Package upload error for %s: %s", package_id, exc)
        return result

    def export_artifact_zip_to_disk(self, iflw_path: Path, artifact_id: str,
                                    artifact_name: str, out_dir: Path,
                                    parameters_prop: str = "",
                                    extra_artifacts: Optional[list] = None) -> Optional[Path]:
        """Write the single-artifact bundle as a ready-to-import .zip so the
        user never has to manually unzip/rezip the extensionless `_content`
        (which risks adding a wrapping folder and triggering "the project must
        contain a valid manifest file"). The .zip has the bundle at its ROOT
        (META-INF/, .project, src/ ...), MS-DOS stamped like real exports, and
        is named to match the CPI UI's expectation: <id>_content_FILES.zip.
        Import via Design -> Integrations -> Import (artifact import)."""
        artifact_id = self.sanitize_package_id(artifact_id)
        inner = self._package_iflow(iflw_path, artifact_id, artifact_name,
                                    parameters_prop, extra_artifacts=extra_artifacts)
        if not inner:
            logger.error("Could not build artifact bundle for %s", artifact_id)
            return None
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        # The bundle bytes ARE already a root-level zip; write straight to disk.
        zip_path = out_dir / f"{artifact_id}_content_FILES.zip"
        zip_path.write_bytes(inner)
        logger.info("Exported artifact .zip -> %s (CPI UI: Design -> Import artifact)",
                    zip_path)
        return zip_path

    def export_full_package_to_disk(self, iflw_path: Path, package_id: str,
                                    package_name: str, artifact_id: str,
                                    artifact_name: str, out_dir: Path,
                                    parameters_prop: str = "",
                                    extra_artifacts: Optional[list] = None) -> Optional[Path]:
        """Write the FULL package export zip to disk (not just the artifact
        bundle), so it can be imported in the CPI UI via Design -> Integrations
        -> Import. The package wraps the artifact as `<id>_content` (NO .zip
        extension) per the real export format.

        This is the diagnostic the user asked for: a manual package import gives
        a precise, human-readable error, isolating 'is the package/bundle
        structurally valid?' from 'is the API call right?'.
        """
        package_id  = self.sanitize_package_id(package_id)
        artifact_id = self.sanitize_package_id(artifact_id)
        inner = self._package_iflow(iflw_path, artifact_id, artifact_name,
                                    parameters_prop, extra_artifacts=extra_artifacts)
        if not inner:
            logger.error("Could not build artifact bundle for %s", artifact_id)
            return None
        pkg_zip = self.build_package_export_zip(
            [(artifact_id, artifact_name, inner)], package_id, package_name)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        zip_path = out_dir / f"{package_id}_package_import.zip"
        zip_path.write_bytes(pkg_zip)
        logger.info("Exported FULL package zip -> %s (CPI UI: Design -> Import)",
                    zip_path)
        return zip_path

    def export_package_zip(self, iflw_path: Path, artifact_id: str,
                           artifact_name: str, out_dir: Path,
                           parameters_prop: str = "",
                           extra_artifacts: Optional[list] = None) -> Optional[Path]:
        """Write the exact zip we WOULD upload to disk, so it can be imported
        manually in the CPI UI (Design → Integrations → Import) to verify the
        bundle is valid independent of the API. Returns the zip path.

        This is a diagnostic: manual UI import gives a far clearer error than
        the API's generic 500, so it isolates 'is the zip valid?' from 'is the
        API call right?'.
        """
        artifact_id = self.sanitize_package_id(artifact_id)
        data = self._package_iflow(iflw_path, artifact_id, artifact_name,
                                   parameters_prop, extra_artifacts=extra_artifacts)
        if not data:
            return None
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        zip_path = out_dir / f"{artifact_id}_import.zip"
        zip_path.write_bytes(data)
        logger.info("Exported importable zip → %s (try Design → Import in CPI UI)",
                    zip_path)
        return zip_path

    @staticmethod
    def _package_iflow(iflw_path: Path, artifact_id: str, artifact_name: str,
                       parameters_prop: str = "",
                       extra_artifacts: Optional[list] = None,
                       parameters_propdef: str = "") -> Optional[bytes]:
        """
        Package a .iflw XML file into the zip format CPI expects.

        Decoded from a real production package (REAL_PACKAGE_REFERENCE.md): a
        valid iFlow artifact bundle MUST contain META-INF/MANIFEST.MF and
        .project — without the manifest, CPI rejects the upload with HTTP 500
        "InputStream cannot be null" because it can't read the bundle.

        extra_artifacts: optional list of (rel_path, content) tuples for the
        scripts/mappings the iFlow references, so the package is self-contained.
        """
        try:
            iflw_content = iflw_path.read_text("utf-8")
            buf          = io.BytesIO()

            # Prefer the VALIDATED manifest/.project produced by the minimal
            # generator (stashed in <iflow_id>__meta/), which has the OSGi
            # Import-Package block CPI requires. Only fall back to a hand-built
            # manifest if the meta dir isn't there.
            meta_dir = iflw_path.parent / f"{iflw_path.stem}__meta"
            validated_manifest = None
            validated_project = None
            if meta_dir.is_dir():
                try:
                    # IMPORTANT: read as bytes, NOT read_text(). read_text()
                    # applies universal-newline translation (CRLF -> LF), which
                    # silently strips the carriage returns the JAR/OSGi manifest
                    # spec REQUIRES. CPI's OData artifact parser is strict about
                    # this (the UI importer is lenient, which is why a bare-LF
                    # manifest imports by hand but 500s "InputStream cannot be
                    # null" via the API). Reading bytes preserves the CRLF that
                    # the generator wrote.
                    validated_manifest = (meta_dir / "MANIFEST.MF").read_bytes().decode("utf-8")
                    validated_project = (meta_dir / ".project").read_bytes().decode("utf-8")
                except Exception:
                    pass

            def _dos_write(zf, name, content):
                # Stamp entries like a REAL CPI export: create_system=0 (MS-DOS/
                # FAT), not Python's default 3 (Unix). CPI's JAR/zip reader is
                # sensitive to this — Unix-stamped entries were a likely cause of
                # "the project must contain a valid manifest file".
                if isinstance(content, str):
                    content = content.encode("utf-8")
                zi = zipfile.ZipInfo(name)
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.create_system = 0          # MS-DOS, matches real exports
                zf.writestr(zi, content)

            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                # ── META-INF/MANIFEST.MF ──
                if validated_manifest:
                    manifest = validated_manifest
                    logger.info("Using validated manifest (with Import-Package) for %s",
                                artifact_id)
                else:
                    sym = artifact_id  # already sanitized alphanumeric
                    manifest = (
                        "Manifest-Version: 1.0\r\n"
                        "Bundle-ManifestVersion: 2\r\n"
                        f"Bundle-SymbolicName: {sym}\r\n"
                        f"Bundle-Name: {artifact_name}\r\n"
                        f"Bundle-Version: 1.0.0\r\n"
                        f"Origin-Bundle-SymbolicName: {sym}\r\n"
                        f"Origin-Bundle-Name: {artifact_name}\r\n"
                        "Origin-Bundle-Version: 1.0.0\r\n"
                        "SAP-BundleType: IntegrationFlow\r\n"
                        "SAP-NodeType: IFLMAP\r\n"
                        "SAP-RuntimeProfile: iflmap\r\n"
                        "\r\n"
                    )
                # JAR/OSGi manifests MUST use CRLF line endings and end with a
                # blank line. Normalize unconditionally so neither the validated
                # nor the fallback path can ship a bare-LF manifest (the cause of
                # CPI's HTTP 500 "InputStream cannot be null").
                manifest = manifest.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
                if not manifest.endswith("\r\n\r\n"):
                    manifest = manifest.rstrip("\r\n") + "\r\n\r\n"
                _dos_write(zf, "META-INF/MANIFEST.MF", manifest)

                # .project (validated one when available)
                if validated_project:
                    _dos_write(zf, ".project", validated_project)

                # NOTE: metainfo.prop intentionally omitted — real importable
                # bundles do NOT contain it (confirmed against a known-good
                # specimen).

                # The iflow file in correct path
                _dos_write(
                    zf,
                    f"src/main/resources/scenarioflows/integrationflow/{artifact_id}.iflw",
                    iflw_content,
                )

                # parameters.prop — real externalized values when provided.
                # Precedence: explicit caller arg > the ORIGINAL pair the
                # generator staged in __meta (source-bundle values, e.g. the
                # configured NZ URLs/roles) > a synthesized header-only stub.
                # The staged pair used to be force-skipped here, which is why
                # tenant flows showed every externalized parameter empty even
                # when the source package carried the values.
                _meta_prop = _meta_propdef = None
                if meta_dir.is_dir():
                    try:
                        _pp = meta_dir / "src" / "main" / "resources" / \
                            "parameters.prop"
                        _pd = meta_dir / "src" / "main" / "resources" / \
                            "parameters.propdef"
                        if _pp.is_file():
                            _meta_prop = _pp.read_bytes().decode("utf-8")
                        if _pd.is_file():
                            _meta_propdef = _pd.read_bytes().decode("utf-8")
                    except Exception:
                        pass
                # Real importable bundles never ship a 0-byte prop; they carry
                # at least a timestamp comment header. Match that.
                import time as _t
                _prop = parameters_prop or _meta_prop or (
                    "#" + _t.strftime("%a %b %d %H:%M:%S UTC %Y", _t.gmtime()) + "\n")
                _dos_write(zf, "src/main/resources/parameters.prop", _prop)

                # parameters.propdef — REQUIRED. Every real importable iFlow
                # bundle (verified across the corpus) carries BOTH parameters.prop
                # and parameters.propdef. CPI's OData create reads parameters.propdef
                # by name to build the parameter model; when it's absent the
                # lookup returns null and the create fails with HTTP 500
                # "InputStream cannot be null". A flow with no externalized
                # parameters still needs an (empty) <parameters/> definition.
                # This is what was missing from the timer scaffold (the UI
                # importer tolerated it; the API does not).
                _dos_write(
                    zf, "src/main/resources/parameters.propdef",
                    parameters_propdef or _meta_propdef
                    or '<?xml version="1.0" encoding="UTF-8" '
                       'standalone="no"?><parameters></parameters>',
                )

                # ── Resource files: __meta (REAL, scaffolder-staged) first,
                # then extra_artifacts (synthesized starting points) — meta
                # wins on collision, so a real script/mapping can never be
                # shadowed by a stub.
                if meta_dir.is_dir():
                    _res_root = meta_dir / "src" / "main" / "resources"
                    if _res_root.is_dir():
                        _already = set(zf.namelist())
                        for _p in _res_root.rglob("*"):
                            if not _p.is_file():
                                continue
                            _rel = "src/main/resources/" + \
                                _p.relative_to(_res_root).as_posix()
                            if _rel in _already:
                                continue
                            if _rel.startswith("src/main/resources/scenarioflows/"):
                                continue
                            if _rel in ("src/main/resources/parameters.prop",
                                        "src/main/resources/parameters.propdef"):
                                continue
                            _dos_write(zf, _rel, _p.read_bytes())
                            _already.add(_rel)

                _names_now = set(zf.namelist())
                for art in (extra_artifacts or []):
                    try:
                        rel_path, content = art
                        if rel_path in _names_now:
                            continue
                        _dos_write(zf, rel_path, content)
                        _names_now.add(rel_path)
                    except Exception as exc:
                        logger.warning("Skipping malformed extra artifact: %s", exc)

            buf.seek(0)
            data = buf.read()
            if not data:
                logger.error("Packaged zip for %s is empty", artifact_id)
                return None
            return data

        except FileNotFoundError:
            logger.error("iFlow file not found: %s (was it generated?)", iflw_path)
            return None
        except Exception as exc:
            logger.error("Failed to package iFlow %s: %s", artifact_id, exc)
            return None

    def fetch_mpls(self, artifact_id: str, top: int = 5) -> list:
        """Latest MessageProcessingLogs for one deployed artifact — the
        tenant-side 'did my test run work' check. Returns a list of dicts
        (Status, LogStart/LogEnd, CustomStatus, error text when failed),
        newest first. Empty list when the API call fails (logged)."""
        import json as _json
        out = []
        try:
            url = (f"{self.base_url}/api/v1/MessageProcessingLogs?"
                   f"$filter=IntegrationArtifact/Id eq '{artifact_id}'"
                   f"&$orderby=LogEnd desc&$top={int(top)}&$format=json")
            r = self.session.get(url, timeout=60)
            if r.status_code != 200:
                logger.warning("fetch_mpls %s -> HTTP %s", artifact_id,
                            r.status_code)
                return out
            for row in r.json().get("d", {}).get("results", []):
                rec = {k: row.get(k) for k in
                       ("MessageGuid", "Status", "LogStart", "LogEnd",
                        "CustomStatus", "ApplicationMessageType")}
                if row.get("Status") == "FAILED":
                    try:
                        eurl = (f"{self.base_url}/api/v1/"
                                f"MessageProcessingLogs('"
                                f"{row['MessageGuid']}')/ErrorInformation/"
                                f"$value")
                        er = self.session.get(eurl, timeout=30)
                        if er.status_code == 200:
                            rec["Error"] = er.text[:1500]
                    except Exception:
                        pass
                out.append(rec)
        except Exception as exc:
            logger.warning("fetch_mpls %s failed: %s", artifact_id, exc)
        return out
