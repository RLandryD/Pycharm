"""fetcher/error_recommender.py

The error → recommendation engine (Tier 1).

Takes a tenant failure — the HTTP status + the OData error body returned on
upload / deploy / runtime — and turns it into a STRUCTURED RECOMMENDATION: what
went wrong, at which stage, the likely cause, and a concrete fix. This is the
foundation both the recommend-only paths and the (later) bounded auto-fix loops
build on: you cannot fix what you have not diagnosed.

Tiers this enables:
  * Tier 1 (this module): recommend what to fix. SAFE — surfaces, never changes.
  * Tier 2 (later): suggest a specific fix the user approves.
  * Tier 3 (later): bounded auto-fix loop — but ONLY for structural (upload)
    errors that are self-verifiable, and only against a real correctness gate
    for semantic (deploy) errors. This module marks each recommendation with
    `auto_fixable` + `fix_class` so the loop knows what it may touch.

HONEST SCOPE: the error→cause→fix rules are grounded in the documented CPI OData
error shapes + the auth/CSRF knowledge already in cpi_diagnostics + SAP's
documented deploy/runtime error patterns. Claude cannot test against the live
tenant (access wall) — these rules are validated against documented/sample error
bodies; the user confirms against real tenant responses. Outputs are reasoned
recommendations, never tenant-certain.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as _field


# stage of the pipeline a failure occurred at
STAGE_UPLOAD = "upload"      # design-time create — STRUCTURAL errors
STAGE_DEPLOY = "deploy"      # deploy — SEMANTIC / config errors
STAGE_RUNTIME = "runtime"    # execution — behavioral errors (MPL)

# fix_class drives what an auto-fix loop is later allowed to do
FIX_STRUCTURAL = "structural"        # self-verifiable, no side effects → safe loop
FIX_SUBSTITUTION = "substitution"    # known equivalence (e.g. unsupported fn) → bounded
FIX_SEMANTIC = "semantic"            # logic / output — recommend only (needs ref)
FIX_AUTH = "auth"                    # credentials/role/CSRF — user action
FIX_UNKNOWN = "unknown"


@dataclass
class Recommendation:
    stage: str
    status_code: int = 0
    cause: str = ""                 # plain-language likely cause
    recommendation: str = ""        # concrete fix to apply
    fix_class: str = FIX_UNKNOWN
    auto_fixable: bool = False      # may a bounded loop attempt it?
    raw_error: str = ""             # the tenant's actual message (untruncated)
    error_code: str = ""            # OData error code if present
    confidence: str = "reasoned"
    evidence: list = _field(default_factory=list)   # what in the error matched


def parse_odata_error(body: str) -> dict:
    """Extract the real message + code from a CPI OData error body. CPI returns
    JSON like {"error":{"code":"...","message":{"value":"..."}}} (sometimes
    nested differently, sometimes plain text). Returns {code, message} with the
    FULL message — no truncation (the prior uploader truncated to 300 chars and
    lost detail)."""
    if not body:
        return {"code": "", "message": ""}
    body = body.strip()
    # try JSON first
    try:
        d = json.loads(body)
        err = d.get("error", d)
        code = err.get("code", "") if isinstance(err, dict) else ""
        msg = ""
        m = err.get("message") if isinstance(err, dict) else None
        if isinstance(m, dict):
            msg = m.get("value", "")
        elif isinstance(m, str):
            msg = m
        return {"code": str(code), "message": msg or body}
    except (ValueError, AttributeError):
        pass
    # fall back: try to pull an XML <message> or just use the text
    m = re.search(r"<message[^>]*>([^<]+)</message>", body)
    if m:
        return {"code": "", "message": m.group(1)}
    return {"code": "", "message": body}


# ── the rule base: (stage, matcher) → cause + fix + class ────────────────────
# Each matcher is a list of CONCEPTS; a concept is a tuple of synonyms. A rule
# matches when EVERY concept is present (AND across concepts) and ANY synonym of
# that concept appears (OR within a concept). e.g. [("resource",), ("not found",
# "missing")] = "resource" AND ("not found" OR "missing").
_RULES = [
    # ---- UPLOAD: structural (self-verifiable, safe to auto-fix) ----
    (STAGE_UPLOAD, [("inputstream cannot be null", "inputstream",
                     "cannot be null")],
     "The upload sent empty/near-empty artifact content — CPI got no bundle to "
     "read (a real failure seen as HTTP 500 'InputStream cannot be null'). "
     "Usually the .iflw bundle zip is empty, missing META-INF/MANIFEST.MF + "
     ".project, or the ArtifactContent base64 wasn't attached to the POST.",
     "Verify the bundle: it must contain META-INF/MANIFEST.MF (with the OSGi "
     "Import-Package block) and .project, and the POST body's ArtifactContent "
     "must be the non-empty base64 zip. Check _package_iflow produced bytes and "
     "they reached the request (a tiny POST body = empty content).",
     FIX_STRUCTURAL, True),
    (STAGE_UPLOAD, [("resource",), ("not found", "missing", "does not exist")],
     "The iFlow references a resource (script/mapping/schema) that isn't in the "
     "uploaded bundle.",
     "Add the missing artifact to the bundle, or fix the reference name in the "
     "iFlow. The capability corpus can supply the matching real artifact.",
     FIX_STRUCTURAL, True),
    (STAGE_UPLOAD, [("bundle-symbolicname", "symbolic")],
     "The uploaded bundle's internal Bundle-SymbolicName doesn't match the "
     "stored artifact (common for re-uploads of externally-built bundles).",
     "Repackage with a consistent symbolic name, or delete-then-recreate the "
     "artifact (the uploader's stage-2 fallback handles this).",
     FIX_STRUCTURAL, True),
    (STAGE_UPLOAD, [("invalid", "malformed"), ("bpmn", "iflow", "xml")],
     "The iFlow BPMN is malformed or has an unresolved reference.",
     "Re-validate the .iflw structure (bundle_validator); fix the broken "
     "element/reference before re-upload.",
     FIX_STRUCTURAL, True),
    (STAGE_UPLOAD, [("already exist",)],
     "An artifact with this Id already exists in the package.",
     "Switch to update (PUT) instead of create, or use overwrite=True.",
     FIX_STRUCTURAL, True),
    (STAGE_UPLOAD, [("package",), ("not found", "does not exist")],
     "The target integration package doesn't exist yet.",
     "Create the package first (POST IntegrationPackages), then upload.",
     FIX_STRUCTURAL, True),
    (STAGE_UPLOAD, [("content-type", "content type", "mediatype")],
     "Wrong Content-Type or payload field on the create POST (the tenant "
     "rejects Type/Content fields).",
     "Send JSON body {Id, Name, PackageId, ArtifactContent:<base64>} with "
     "Content-Type application/json — no Type/Content fields.",
     FIX_STRUCTURAL, True),

    # ---- DEPLOY: semantic / config ----
    (STAGE_DEPLOY, [("compilation", "compile", "cannot resolve",
                     "unable to resolve"), ("groovy", "script", "class")],
     "A Groovy script in the iFlow does not compile.",
     "Fix the script syntax. (A bounded loop may NOT silently strip it — that "
     "would change behavior; recommend the fix and re-test.)",
     FIX_SEMANTIC, False),
    (STAGE_DEPLOY, [("parameter",), ("unresolved", "no value", "not bound")],
     "An externalized parameter ({{...}}) has no value bound at deploy time.",
     "Provide values for the externalized parameters (the field-spec layer "
     "surfaces exactly which ones) before deploy.",
     FIX_STRUCTURAL, True),
    (STAGE_DEPLOY, [("credential", "security material", "alias", "keystore")],
     "A referenced credential / security-material alias isn't deployed on the "
     "tenant.",
     "Deploy the credential (User Credentials / OAuth2) under the expected "
     "alias before deploying the iFlow.",
     FIX_STRUCTURAL, True),
    (STAGE_DEPLOY, [("not supported", "not accepted", "unknown function",
                     "unsupported")],
     "The artifact uses a function/adapter the tenant doesn't accept (e.g. a "
     "PI construct with no direct CPI equivalent).",
     "Substitute the unsupported construct with its CPI equivalent (the PI→CPI "
     "translation table maps several). Flagged — confirm the substitution.",
     FIX_SUBSTITUTION, True),

    # ---- AUTH (any stage) ----
    (STAGE_UPLOAD, [("csrf",)],
     "Missing/invalid X-CSRF-Token on the write request.",
     "Fetch a fresh X-CSRF-Token (GET with header 'X-CSRF-Token: Fetch') and "
     "include it on the POST/PUT.",
     FIX_AUTH, True),
]


def _concepts_match(concepts, msg: str) -> bool:
    """Every concept present (AND); any synonym within a concept (OR)."""
    return all(any(syn in msg for syn in concept) for concept in concepts)


def _auth_recommendation(stage: str, status: int) -> Recommendation:
    if status == 401:
        return Recommendation(
            stage=stage, status_code=401, fix_class=FIX_AUTH, auto_fixable=False,
            cause="401 Unauthorized — credentials/token not valid for writes.",
            recommendation="Use an OAuth2 client (BTP service key), not the "
            "tenant login user/password. Verify token_url/client_id/secret.",
            evidence=["HTTP 401"])
    if status == 403:
        return Recommendation(
            stage=stage, status_code=403, fix_class=FIX_AUTH, auto_fixable=False,
            cause="403 Forbidden — auth works but the OAuth client lacks the "
            "write role.",
            recommendation="Grant the client the write role "
            "(e.g. 'WorkspacePackagesEdit' / 'IntegrationContent.Write').",
            evidence=["HTTP 403"])
    return None


def recommend(stage: str, status_code: int, error_body: str) -> Recommendation:
    """Diagnose a single tenant failure into a structured recommendation."""
    parsed = parse_odata_error(error_body)
    msg = (parsed["message"] or "").lower()

    # auth statuses take precedence (they mask the body)
    if status_code in (401, 403) and "csrf" not in msg:
        rec = _auth_recommendation(stage, status_code)
        if rec:
            rec.raw_error = parsed["message"]
            rec.error_code = parsed["code"]
            return rec

    for rstage, concepts, cause, fix, fclass, auto in _RULES:
        # a rule matches if its stage matches (or it's an auth/global rule) and
        # all its concepts are present in the message
        stage_ok = (rstage == stage) or (fclass == FIX_AUTH)
        if stage_ok and _concepts_match(concepts, msg):
            return Recommendation(
                stage=stage, status_code=status_code, cause=cause,
                recommendation=fix, fix_class=fclass, auto_fixable=auto,
                raw_error=parsed["message"], error_code=parsed["code"],
                evidence=[c[0] for c in concepts])

    # no rule matched — honest unknown, surface the raw tenant message
    return Recommendation(
        stage=stage, status_code=status_code, fix_class=FIX_UNKNOWN,
        auto_fixable=False,
        cause="Unrecognized error — no rule matched.",
        recommendation="Review the tenant's message below; this pattern isn't "
        "in the rule base yet.",
        raw_error=parsed["message"] or error_body, error_code=parsed["code"])


def recommend_all(failures: list) -> list:
    """Diagnose a list of (stage, status_code, error_body) failures."""
    return [recommend(s, c, b) for (s, c, b) in failures]
