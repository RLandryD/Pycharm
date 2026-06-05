#!/usr/bin/env python3
"""
cpi_package_export.py  --  build a STRUCTURALLY-VALID CPI package export from
generated artifacts: a correct resources.cnt (with the ContentPackage entry and
AGGREGATION relations), contentmetadata.md, ExportInformation.info, a format-valid
hash, and one <id>_content blob per artifact.

WHY
    The workbench's downloaded package was missing the ContentPackage entry in
    resources.cnt, so cpi_api_deploy.py (and a UI import) couldn't read it. Every
    field shape below is copied from a real tenant export, not guessed.

TWO USES
    1. Produce a real, downloadable export zip (so the standalone deploy script and
       a UI import both accept it).
    2. Produce the per-artifact INNER bundle zips that the API deploy path POSTs as
       ArtifactContent (build_inner_bundle).

NOTE ON THE HASH
    The envelope `hash` here is a FORMAT-valid placeholder (a fresh 64-hex first
    element + the tenant's signature as the second). It is correct for the API
    deploy path, which ignores it entirely. It is NOT sufficient for a UI import,
    whose server-side check we cannot reproduce -- use the API path (or let the
    tenant re-export) for a hash the tenant will accept.
"""

from __future__ import annotations

import base64
import io
import json
import os
import time
import uuid
import zipfile

# This tenant's export signature (the 2nd hash element). Pass your own if it differs.
DEFAULT_TENANT_HASH = "0a0c520cfab66af3374ff74130cb895a3bd77062965558f74f26c001a459e022"
DEFAULT_ENVIRONMENT = "it-db-design.0140aa99trial"

# resourceType -> default contentType seen in real exports
_CONTENT_TYPE = {
    "IFlow": "application/x-zip-compressed",
    "MessageMapping": "application/x-zip-compressed",
    "ValueMapping": "application/x-zip-compressed",
    "ScriptCollection": "application/x-zip-compressed",
}


def _guid() -> str:
    """32-hex GUID, the form CPI uses for resource ids and the package master id."""
    return uuid.uuid4().hex


def _now_ms() -> int:
    return int(time.time() * 1000)


def _dos_zip(members: dict) -> bytes:
    """Zip a {arcname: bytes|str} dict, DEFLATED + MS-DOS stamped (create_system=0)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for arcname, data in members.items():
            if isinstance(data, str):
                data = data.encode("utf-8")
            zi = zipfile.ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
            zi.create_system = 0          # MS-DOS, like real exports
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, data)
    return buf.getvalue()


def build_inner_bundle(files: dict) -> bytes:
    """
    Build one artifact's inner bundle (the <id>_content blob) from a
    {relative_path: content} map. The map MUST place bundle members at the root:
        META-INF/MANIFEST.MF
        .project
        src/main/resources/scenarioflows/integrationflow/<name>.iflw
        src/main/resources/script/*.groovy ... etc.
    This is exactly what the API expects as base64 ArtifactContent.
    """
    if not files:
        raise ValueError("build_inner_bundle: empty file map")
    return _dos_zip(files)


# ---------------------------------------------------------------------------
# resources.cnt entry builders (field shapes copied verbatim from a real export)
# ---------------------------------------------------------------------------
def _attr(value, extended=False):
    return {"isExtendedLargeAttribute": extended, "attributeValues": [value]}


def _artifact_entry(art, master_guid, created_by):
    rtype = art["type"]
    rid = art["id"]
    display = art["display_name"]
    unique = art["unique_id"]
    ts = _now_ms()
    entry = {
        "version": 1,
        "revision": 0,
        "masterId": f"{master_guid}:{rid}:1",
        "versionComment": "Resource Created",
        "globalModifiedDate": ts * 1000,        # microseconds in real exports
        "isValidHash": 0,
        "id": rid,
        "name": f"{display}.zip",                # readable name + .zip (verified 915/915)
        "createdBy": created_by,
        "createdAt": ts,
        "uniqueId": unique,
        "modifiedBy": created_by,
        "modifiedAt": ts,
        "additionalAttributes": {
            "Description": _attr("", extended=True),
            "OriginBundleSymbolicName": _attr(unique),
            "nodeType": _attr("IFLMAP"),
            "OriginBundleName": _attr(unique),
            "productProfile": _attr("iflmap"),
        },
        "resourceType": rtype,
        "contentType": _CONTENT_TYPE.get(rtype, "application/octet-stream"),
        "displayName": display,
        "semanticVersion": art.get("semantic_version", "1.0.0"),
        "privilegeState": "EDIT_ALLOWED",
        "isRestricted": False,
        "isGroupDefault": False,
        "resourceReferences": [],
    }
    return entry


def _package_entry(package, master_guid, created_by):
    pid = package["id"]
    ts = _now_ms()
    return {
        "version": 1,
        "revision": 0,
        "masterId": f"{master_guid}:{pid}:1",
        "globalModifiedDate": ts * 1000,
        "isValidHash": 0,
        "id": pid,
        "name": pid,
        "createdBy": created_by,
        "createdAt": ts,
        "uniqueId": pid,
        "modifiedBy": created_by,
        "modifiedAt": ts,
        "additionalAttributes": {
            "Description": _attr(package.get("description", "")),
            "Version": _attr(package.get("version", "1.0.0")),
            "shortText": _attr(package.get("short_text", package.get("name", pid))),
            "Product": _attr(""),
            "SupportedPlatform": _attr("SAP HANA Cloud Integration"),
            "category": _attr("Integration"),
        },
        "auxilaryProperties": {},               # SAP's spelling, kept verbatim
        "resourceType": "ContentPackage",
        "displayName": package.get("name", pid),
        "semanticVersion": "1.0.0",
        "privilegeState": "EDIT_ALLOWED",
        "isRestricted": False,
        "isGroupDefault": False,
        "resourceReferences": [],
    }


def _contentmetadata(environment) -> str:
    body = (
        "-- listing properties --\n"
        "HashVersion=2.0.0\n"
        "EncodingVersion=1.0.0\n"
        "Organization=TEST\n"
        f"Environment={environment}\n"
        "RelationClassVersion=1.0.0\n"
        "ResourceClassVersion=1.2.0\n"
        "ExportModelVersion=1.0.0\n"
    )
    return body


# ---------------------------------------------------------------------------
# The export
# ---------------------------------------------------------------------------
def normalize_artifact(art):
    """Fill in defaults; accept either ready `content` bytes or a `files` map."""
    a = dict(art)
    a.setdefault("type", "IFlow")
    a["display_name"] = a.get("display_name") or a.get("name") or "Artifact"
    a["unique_id"] = a.get("unique_id") or a["display_name"].replace(" ", "_")
    a["id"] = a.get("id") or _guid()
    if "content" not in a:
        if "files" not in a:
            raise ValueError(f"artifact {a['display_name']!r} has neither 'content' nor 'files'")
        a["content"] = build_inner_bundle(a["files"])
    return a


def build_export_zip(package, artifacts,
                     environment=DEFAULT_ENVIRONMENT,
                     tenant_hash=DEFAULT_TENANT_HASH,
                     created_by="generated@local") -> bytes:
    """
    package   = {"id", "name", optional "description"/"short_text"/"version"}
    artifacts = [{"type","name", optional "id"/"unique_id"/"display_name"/
                  "semantic_version", and one of "content"(bytes) or "files"(dict)} ...]
    Returns the export zip bytes (a valid CPI package export).
    """
    package = dict(package)
    package.setdefault("id", "GeneratedPackage")
    package.setdefault("name", package["id"])
    created_by = package.get("created_by", created_by)

    arts = [normalize_artifact(a) for a in artifacts]
    master_guid = _guid()

    resources = [_artifact_entry(a, master_guid, created_by) for a in arts]
    resources.append(_package_entry(package, master_guid, created_by))

    pkg_id = package["id"]
    relations = [
        {"sourceId": pkg_id, "targetId": a["id"],
         "relationType": "AGGREGATION", "RelationName": "Default"}
        for a in arts
    ]

    resources_cnt = json.dumps({"resources": resources, "relations": relations},
                               separators=(",", ":")).encode("utf-8")
    contentmetadata = _contentmetadata(environment).encode("utf-8")
    export_info = (f"Name= {package['name']}\n"
                   f"Date= {time.strftime('%a %b %d %H:%M:%S UTC %Y', time.gmtime())}\n")
    hash_blob = json.dumps([_guid() + _guid(), tenant_hash],
                           separators=(",", ":")).encode("utf-8")  # 2 x 64-hex

    members = {
        "ExportInformation.info": export_info.encode("utf-8"),
        "contentmetadata.md": base64.b64encode(contentmetadata),
        "resources.cnt": base64.b64encode(resources_cnt),
        "hash": hash_blob,
    }
    for a in arts:
        members[f"{a['id']}_content"] = a["content"]
    return _dos_zip(members)


def artifacts_for_deploy(package, artifacts):
    """
    Convenience for the API path: returns (package_meta, [{type,id,name,content}])
    ready to hand straight to cpi_api_deploy.deploy_package -- no export zip needed.
    """
    arts = [normalize_artifact(a) for a in artifacts]
    return (
        {"id": package["id"], "name": package.get("name", package["id"])},
        [{"type": a["type"], "id": a["unique_id"], "name": a["display_name"],
          "content": a["content"]} for a in arts],
    )


if __name__ == "__main__":
    print("This is a library. Import build_export_zip / build_inner_bundle / "
          "artifacts_for_deploy. See cpi_api_deploy.py for deployment.")
