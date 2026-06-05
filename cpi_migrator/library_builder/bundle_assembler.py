"""library_builder/bundle_assembler.py

Assemble a deployable SAP Integration Suite package-export bundle from content.

The bundle format (reverse-engineered from real RCI093 exports, confirmed by
diffing two exports of identical content — 29/35 files byte-identical, the 6
differing ones being only the wrapper bookkeeping + name-bearing descriptors):

  <bundle>.zip
    ├── <guid>_content              (one nested zip per iFlow)
    │     ├── .project              templated; only <name> = symbolic name
    │     ├── META-INF/MANIFEST.MF  templated; name/version vary, Import-Package static
    │     └── src/main/resources/   the actual content (iflw, mmap, groovy, xsd, ...)
    ├── resources.cnt               base64(JSON registry of the iFlows)
    ├── contentmetadata.md          base64(near-static properties block)
    ├── hash                        ["<contenthash>", "<constant>"]
    └── ExportInformation.info      "Name= X\nDate= ..."

HONEST NOTES:
- The `hash` first element is an internal SAP algorithm we did not reproduce
  exactly; resources.cnt carries isValidHash=0, strongly implying the tenant
  revalidates/recomputes on import. We emit a best-effort sha256 and rely on
  the deploy-test to confirm the tenant accepts/recomputes it.
- This assembler produces the STRUCTURE faithfully; the only unproven step is
  whether import requires a byte-exact hash. That is the one tenant test left.
- Content authoring (the actual .iflw/.mmap correctness) is the consultant's
  job or comes from clone-and-adapt; this module only packages content into a
  deployable bundle.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import time
import zipfile
from dataclasses import dataclass, field
from typing import Optional

# constant second hash element seen across all real exports (format/schema id)
_CONST_HASH = "0a0c520cfab66af3374ff74130cb895a3bd77062965558f74f26c001a459e022"

# the giant Import-Package list is identical across all iFlow MANIFESTs
_IMPORT_PACKAGE = (
    "com.sap.esb.application.services.cxf.interceptor,com.sap.esb.security,"
    "com.sap.it.op.agent.api,com.sap.it.op.agent.collector.camel,"
    "com.sap.it.op.agent.collector.cxf,com.sap.it.op.agent.mpl,javax.jms,"
    "javax.jws,javax.wsdl,javax.xml.bind.annotation,javax.xml.namespace,"
    "javax.xml.ws,org.apache.camel;version=\"2.8\","
    "org.apache.camel.builder;version=\"2.8\","
    "org.apache.camel.builder.xml;version=\"2.8\","
    "org.apache.camel.component.cxf,org.apache.camel.model;version=\"2.8\","
    "org.apache.camel.processor;version=\"2.8\","
    "org.apache.camel.processor.aggregate;version=\"2.8\","
    "org.apache.camel.spring.spi;version=\"2.8\",org.apache.commons.logging,"
    "org.apache.cxf.binding,org.apache.cxf.binding.soap,"
    "org.apache.cxf.binding.soap.spring,org.apache.cxf.bus,"
    "org.apache.cxf.bus.resource,org.apache.cxf.bus.spring,"
    "org.apache.cxf.buslifecycle,org.apache.cxf.catalog,"
    "org.apache.cxf.configuration.jsse;version=\"2.5\","
    "org.apache.cxf.configuration.spring,org.apache.cxf.endpoint,"
    "org.apache.cxf.headers,org.apache.cxf.interceptor,"
    "org.apache.cxf.management.counters;version=\"2.5\",org.apache.cxf.message,"
    "org.apache.cxf.phase,org.apache.cxf.resource,org.apache.cxf.service.factory,"
    "org.apache.cxf.service.model,org.apache.cxf.transport,"
    "org.apache.cxf.transport.common.gzip,org.apache.cxf.transport.http,"
    "org.apache.cxf.transport.http.policy,org.apache.cxf.workqueue,"
    "org.apache.cxf.ws.rm.persistence,org.apache.cxf.wsdl11,"
    "org.osgi.framework;version=\"1.6.0\",org.slf4j;version=\"1.6\","
    "org.springframework.beans.factory.config;version=\"3.0\","
    "com.sap.esb.camel.security.cms,org.apache.camel.spi,"
    "com.sap.esb.webservice.audit.log,"
    "com.sap.esb.camel.endpoint.configurator.api,"
    "com.sap.esb.camel.jdbc.idempotency.reorg,javax.sql,"
    "org.apache.camel.processor.idempotent.jdbc,"
    "org.osgi.service.blueprint;version=\"[1.0.0,2.0.0)\""
)

_PROJECT_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?><projectDescription>\n'
    '   <name>{symbolic}</name>\n'
    '   <comment/>\n'
    '   <projects/>\n'
    '   <buildSpec>\n'
    '      <buildCommand>\n'
    '         <name>org.eclipse.jdt.core.javabuilder</name>\n'
    '         <arguments/>\n'
    '      </buildCommand>\n'
    '   </buildSpec>\n'
    '   <natures>\n'
    '      <nature>org.eclipse.jdt.core.javanature</nature>\n'
    '      <nature>com.sap.ide.ifl.project.support.project.nature</nature>\n'
    '      <nature>com.sap.ide.ifl.bsn</nature>\n'
    '   </natures>\n'
    '</projectDescription>\n'
)


@dataclass
class IFlowContent:
    """One iFlow's content to be packaged."""
    display_name: str                       # e.g. "RCI093_SuccessFactors_to_OpenText"
    symbolic_name: str = ""                  # e.g. "RCI093SuccessFactorstoOpenText"
    version: str = "1.0.0"
    # resource_path (under src/main/resources/...) -> bytes
    files: dict = field(default_factory=dict)
    guid: str = ""                           # 32-hex; generated if empty

    def __post_init__(self):
        if not self.symbolic_name:
            self.symbolic_name = re.sub(r"[^A-Za-z0-9]", "", self.display_name)
        if not self.guid:
            self.guid = hashlib.md5(
                (self.symbolic_name + str(time.time())).encode()).hexdigest()


def _fold_manifest_line(key: str, value: str) -> str:
    """OSGi manifest line folding: continuation lines start with a single
    space, wrapped so each physical line is <= 72 chars."""
    line = f"{key}: {value}"
    out = []
    while len(line) > 72:
        out.append(line[:72])
        line = " " + line[72:]
    out.append(line)
    return "\r\n".join(out)


def build_manifest(c: IFlowContent) -> bytes:
    lines = [
        _fold_manifest_line("Manifest-Version", "1.0"),
        _fold_manifest_line("Bundle-ManifestVersion", "2"),
        _fold_manifest_line("Bundle-Name", c.display_name),
        _fold_manifest_line("Bundle-SymbolicName", c.symbolic_name),
        _fold_manifest_line("Bundle-Version", c.version),
        _fold_manifest_line("SAP-BundleType", "IntegrationFlow"),
        _fold_manifest_line("SAP-NodeType", "IFLMAP"),
        _fold_manifest_line("SAP-RuntimeProfile", "iflmap"),
        _fold_manifest_line("Import-Package", _IMPORT_PACKAGE),
        _fold_manifest_line("Origin-Bundle-Name", c.display_name),
        _fold_manifest_line("Origin-Bundle-SymbolicName", c.symbolic_name),
    ]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")


def build_project(c: IFlowContent) -> bytes:
    return _PROJECT_TEMPLATE.format(symbolic=c.symbolic_name).encode("utf-8")


def _build_content_zip(c: IFlowContent) -> bytes:
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(".project", build_project(c))
        z.writestr("META-INF/MANIFEST.MF", build_manifest(c))
        for path, data in c.files.items():
            # ensure under src/main/resources/
            p = path if path.startswith("src/main/resources/") \
                else f"src/main/resources/{path.lstrip('/')}"
            z.writestr(p, data)
    return bio.getvalue()


def _build_resources_cnt(iflows: list[IFlowContent], client_id: str) -> bytes:
    now_ms = int(time.time() * 1000)
    resources = []
    for c in iflows:
        master_id = f"{hashlib.md5(client_id.encode()).hexdigest()}:{c.guid}:1"
        resources.append({
            "version": 1, "revision": 0, "masterId": master_id,
            "versionComment": "Resource Created",
            "globalModifiedDate": now_ms * 1000, "isValidHash": 0,
            "id": c.guid, "name": f"{c.display_name}.zip",
            "createdBy": client_id, "createdAt": now_ms,
            "uniqueId": c.symbolic_name,
            "modifiedBy": client_id, "modifiedAt": now_ms,
            "additionalAttributes": {
                "Description": {"isExtendedLargeAttribute": True,
                                "attributeValues": [""]},
                "OriginBundleSymbolicName": {"isExtendedLargeAttribute": False,
                                             "attributeValues": [c.symbolic_name]},
                "OriginBundleName": {"isExtendedLargeAttribute": False,
                                     "attributeValues": [c.display_name]},
            },
            "resourceType": "IFlow", "contentType": "application/octet-stream",
            "displayName": c.display_name, "semanticVersion": c.version,
            "privilegeState": "EDIT_ALLOWED", "isRestricted": False,
            "isGroupDefault": False, "resourceReferences": [],
        })
    payload = json.dumps({"resources": resources})
    return base64.b64encode(payload.encode("utf-8"))


def _build_contentmetadata(organization: str, environment: str) -> bytes:
    props = (
        "-- listing properties --\n"
        "HashVersion=2.0.0\n"
        "EncodingVersion=1.0.0\n"
        f"Organization={organization}\n"
        f"Environment={environment}\n"
        "RelationClassVersion=1.0.0\n"
        "ResourceClassVersion=1.2.0\n"
        "ExportModelVersion=1.0.0\n"
    )
    return base64.b64encode(props.encode("utf-8"))


def build_bundle(
    iflows: list[IFlowContent],
    package_name: str,
    client_id: str = "GENERATED",
    organization: str = "TEST",
    environment: str = "it-db-design.trial",
    minimal: bool = False,
) -> bytes:
    """Assemble the full deployable bundle zip; return its bytes.

    If minimal=True, the wrapper files (hash, resources.cnt, ExportInformation,
    contentmetadata) are written BLANK — confirmed by tenant tests to be
    regenerated on import. Only the content + MANIFEST + .project (which the
    tenant requires and reads) are fully populated. This is the simplest
    proven-deployable form.
    """
    bio = io.BytesIO()
    content_zips = {}
    for c in iflows:
        content_zips[f"{c.guid}_content"] = _build_content_zip(c)

    if minimal:
        resources_cnt = b""
        contentmetadata = b""
        hash_file = b""
        export_info = b""
    else:
        resources_cnt = _build_resources_cnt(iflows, client_id)
        contentmetadata = _build_contentmetadata(organization, environment)
        h = hashlib.sha256(b"".join(content_zips[k]
                                    for k in sorted(content_zips)))
        hash_file = json.dumps([h.hexdigest(), _CONST_HASH]).encode("utf-8")
        export_info = (
            f"Name= {package_name}\n"
            f"Date= {time.strftime('%a %b %d %H:%M:%S UTC %Y', time.gmtime())}"
        ).encode("utf-8")

    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in content_zips.items():
            z.writestr(name, data)
        z.writestr("resources.cnt", resources_cnt)
        z.writestr("contentmetadata.md", contentmetadata)
        z.writestr("hash", hash_file)
        z.writestr("ExportInformation.info", export_info)
    return bio.getvalue()


def extract_content_from_bundle(bundle_path: str) -> list[IFlowContent]:
    """Inverse: read a real bundle's content into IFlowContent objects, so we
    can clone-and-adapt an existing iFlow. Used for the round-trip test."""
    out = []
    z = zipfile.ZipFile(bundle_path)
    for n in z.namelist():
        if not n.endswith("_content"):
            continue
        guid = n.replace("_content", "")
        cz = zipfile.ZipFile(io.BytesIO(z.read(n)))
        display = symbolic = ""
        version = "1.0.0"
        files = {}
        for inner in cz.namelist():
            if inner.endswith("/"):
                continue
            data = cz.read(inner)
            if inner == "META-INF/MANIFEST.MF":
                txt = data.decode("utf-8", "replace").replace("\r\n ", "")
                for line in txt.splitlines():
                    if line.startswith("Bundle-Name:"):
                        display = line.split(":", 1)[1].strip()
                    elif line.startswith("Bundle-SymbolicName:"):
                        symbolic = line.split(":", 1)[1].strip()
                    elif line.startswith("Bundle-Version:"):
                        version = line.split(":", 1)[1].strip()
            elif inner == ".project":
                pass
            else:
                files[inner] = data
        out.append(IFlowContent(display_name=display, symbolic_name=symbolic,
                                version=version, files=files, guid=guid))
    return out


# ── Exposed MessageMapping support (standalone reusable mapping artifact) ───
# A MessageMapping content unit differs from an IntegrationFlow:
#   SAP-BundleType: MessageMapping  (not IntegrationFlow)
#   no SAP-RuntimeProfile
#   Provide-Capability: messagemapping.<symbolic>;version:Version="x"
#   a mapping-focused (smaller) Import-Package
# Structure: src/main/resources/mapping/*.mmap + wsdl|xsd schemas + .project + MANIFEST
_MM_IMPORT_PACKAGE = (
    'com.sap.esb.security,javax.jms,javax.jws,javax.wsdl,'
    'javax.xml.bind.annotation,javax.xml.namespace,javax.xml.ws,'
    'org.apache.commons.logging,org.osgi.framework;version="1.6.0",'
    'org.slf4j;version="1.6",com.sap.esb.camel.security.cms,'
    'org.apache.camel.spi,com.sap.esb.camel.endpoint.configurator.api,'
    'org.osgi.service.blueprint;version="[1.0.0,2.0.0)",'
    'com.sap.it.api.mapping,com.sap.aii.mapping.value.api,'
    'com.sap.aii.mapping.lookup,com.sap.aii.mappingtool.tfapi,'
    'com.sap.aii.mappingtool.tf7.rt,com.sap.aii.mappingtool.tf7,'
    'com.sap.aii.mappingtool.tf3.rt,com.sap.aii.mappingtool.tf3,'
    'com.sap.aii.mappingtool.flib7,com.sap.aii.mappingtool.flib3,'
    'com.sap.aii.mapping.api,com.sap.aii.ib.bom.flib.types,'
    'com.sap.xi.mapping.camel'
)


def build_messagemapping_manifest(c: "IFlowContent") -> bytes:
    """MANIFEST for an exposed MessageMapping (not an IntegrationFlow)."""
    lines = [
        _fold_manifest_line("Manifest-Version", "1.0"),
        _fold_manifest_line("Bundle-ManifestVersion", "2"),
        _fold_manifest_line("Bundle-Name", c.display_name),
        _fold_manifest_line("Bundle-SymbolicName", c.symbolic_name),
        _fold_manifest_line("Bundle-Version", c.version),
        _fold_manifest_line("SAP-BundleType", "MessageMapping"),
        _fold_manifest_line("SAP-NodeType", "IFLMAP"),
        _fold_manifest_line("Import-Package", _MM_IMPORT_PACKAGE),
        _fold_manifest_line(
            "Provide-Capability",
            f'messagemapping.{c.symbolic_name};version:Version="1.0.0"'),
        _fold_manifest_line("Origin-Bundle-Name", c.display_name),
        _fold_manifest_line("Origin-Bundle-SymbolicName", c.symbolic_name),
    ]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")


def build_messagemapping_content(c: "IFlowContent") -> bytes:
    """Build a standalone MessageMapping content zip.

    c.files should contain the mmap under mapping/ and its schemas under
    wsdl/ or xsd/ (paths under src/main/resources/)."""
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(".project", build_project(c))
        z.writestr("META-INF/MANIFEST.MF", build_messagemapping_manifest(c))
        for path, data in c.files.items():
            p = path if path.startswith("src/main/resources/") \
                else f"src/main/resources/{path.lstrip('/')}"
            z.writestr(p, data)
    return bio.getvalue()
