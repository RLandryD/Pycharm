"""
extractor/esr_extractor.py

Connects to SAP PI/PO Enterprise Services Repository (ESR) to extract:
  - Message Mappings (graphical + Java)
  - Data Types (XSD structures)
  - Service Interfaces (WSDL operations)
  - Operation Mappings
  - Value Mappings

ESR REST endpoints:
  GET /CommunicationChannel/MessageMapping
  GET /CommunicationChannel/DataType
  GET /CommunicationChannel/ServiceInterface
  GET /CommunicationChannel/OperationMapping
  GET /CommunicationChannel/ValueMapping
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional
import requests

logger = logging.getLogger(__name__)


@dataclass
class ESRObject:
    id: str
    name: str
    namespace: str
    software_component: str
    obj_type: str            # MessageMapping / DataType / ServiceInterface / OperationMapping / ValueMapping
    description: str = ""
    content_url: str = ""    # URL to fetch full content/XSD/WSDL
    mapping_type: str = ""   # Java / Graphical / XSLT (for MessageMapping)
    operations: list[str] = field(default_factory=list)
    content: str = ""        # FULL file text (XSD/WSDL/EDMX) — bundled verbatim
    content_sha: str = ""    # sha1 of content, for dedupe
    raw: dict = field(default_factory=dict, repr=False)


class ESRExtractor:
    """
    Extracts design-time artifacts from the SAP PI/PO
    Enterprise Services Repository via REST API.
    """

    ENDPOINTS = {
        "MessageMapping":   "/CommunicationChannel/MessageMapping",
        "DataType":         "/CommunicationChannel/DataType",
        "ServiceInterface": "/CommunicationChannel/ServiceInterface",
        "OperationMapping": "/CommunicationChannel/OperationMapping",
        "ValueMapping":     "/CommunicationChannel/ValueMapping",
    }

    def __init__(self, base_url: str, session: requests.Session):
        self.base_url = base_url.rstrip("/")
        self.session  = session

    def extract_all(self) -> list[ESRObject]:
        """Extract all ESR object types."""
        objects = []
        for obj_type, endpoint in self.ENDPOINTS.items():
            try:
                items = self._fetch_objects(obj_type, endpoint)
                objects.extend(items)
                logger.info("ESR: %d %s objects", len(items), obj_type)
            except Exception as exc:
                logger.warning("ESR %s extraction failed: %s", obj_type, exc)
        return objects

    def extract_message_mappings(self) -> list[ESRObject]:
        return self._fetch_objects("MessageMapping",
                                   self.ENDPOINTS["MessageMapping"])

    def extract_data_types(self) -> list[ESRObject]:
        return self._fetch_objects("DataType",
                                   self.ENDPOINTS["DataType"])

    def extract_service_interfaces(self) -> list[ESRObject]:
        return self._fetch_objects("ServiceInterface",
                                   self.ENDPOINTS["ServiceInterface"])

    def _fetch_objects(self, obj_type: str, endpoint: str) -> list[ESRObject]:
        url     = f"{self.base_url}{endpoint}"
        results = []
        skip    = 0

        while True:
            resp = self.session.get(
                url,
                params={"$format": "json", "$top": 100, "$skip": skip},
                timeout=30,
            )
            resp.raise_for_status()
            data    = resp.json()
            entries = data.get("d", {}).get("results",
                      data.get("value", []))
            if not entries:
                break

            for entry in entries:
                obj = self._parse_entry(entry, obj_type)
                if obj:
                    results.append(obj)

            skip += 100
            if len(entries) < 100:
                break

        return results

    def _parse_entry(self, entry: dict, obj_type: str) -> Optional[ESRObject]:
        name = (entry.get("Name") or entry.get("MappingName") or
                entry.get("TypeName") or "")
        if not name:
            return None

        obj = ESRObject(
            id=entry.get("ID", entry.get("Id", "")),
            name=name,
            namespace=entry.get("Namespace", ""),
            software_component=entry.get("SoftwareComponentName", ""),
            obj_type=obj_type,
            description=entry.get("Description", ""),
            raw=entry,
        )

        if obj_type == "MessageMapping":
            obj.mapping_type = entry.get("MappingType", "Graphical")
        elif obj_type == "ServiceInterface":
            ops = entry.get("Operations", {})
            if isinstance(ops, dict):
                obj.operations = ops.get("results", [])

        return obj

    def get_mapping_content(self, obj: ESRObject) -> Optional[str]:
        """Fetch the full mapping content (XSL/Java) for a MessageMapping."""
        if not obj.id:
            return None
        url = f"{self.base_url}/CommunicationChannel/MessageMapping('{obj.id}')/$value"
        try:
            resp = self.session.get(url, timeout=30)
            if resp.status_code == 200:
                return resp.text
        except Exception as exc:
            logger.warning("Could not fetch mapping content for %s: %s",
                           obj.name, exc)
        return None

    def find_mappings_for_interface(
        self,
        interface_name: str,
        all_objects: list[ESRObject],
    ) -> list[ESRObject]:
        """Find ESR objects related to a specific interface by name matching."""
        name_lower = interface_name.lower().replace("_", "").replace("-", "")
        return [
            obj for obj in all_objects
            if name_lower in obj.name.lower().replace("_", "").replace("-", "")
            or name_lower in obj.description.lower()
        ]


class ESRFileParser:
    """
    Parses ESR content from exported files (.xsd, .wsdl, .mmap).
    Used when live ESR connection is not available.
    """

    def parse_uploaded_files(
        self,
        files: dict[str, bytes],
    ) -> list[ESRObject]:
        """Parse uploaded ESR export files. XSD/WSDL/EDMX are retained in FULL
        (stored verbatim in obj.content) so they can be bundled into a generated
        iFlow as-is; identical files are de-duplicated by content hash."""
        objects = []
        seen_sha = set()
        for filename, content in files.items():
            ext = filename.lower().split(".")[-1]
            if ext == "xsd":
                obj = self._parse_xsd(filename, content)
            elif ext in ("wsdl",):
                obj = self._parse_wsdl(filename, content)
            elif ext in ("edmx", "xml"):
                # .xml may be a WSDL or an EDMX; sniff the root
                head = (content[:400].decode("utf-8", "ignore")
                        if isinstance(content, (bytes, bytearray)) else str(content[:400]))
                if "Edmx" in head or "edmx" in head:
                    obj = self._parse_edmx(filename, content)
                else:
                    obj = self._parse_wsdl(filename, content)
            elif ext in ("mmap", "xim"):
                obj = self._parse_mmap(filename, content)
            else:
                continue
            if not obj:
                continue
            # de-dupe identical schema files (same bytes → keep first)
            if obj.content_sha and obj.content_sha in seen_sha:
                logger.info("Deduped identical %s (%s)", obj.obj_type, filename)
                continue
            if obj.content_sha:
                seen_sha.add(obj.content_sha)
            objects.append(obj)
        return objects

    @staticmethod
    def _full_text(content) -> tuple[str, str]:
        """Return (full_text, sha1) for a file's raw bytes/str — no truncation."""
        import hashlib
        if isinstance(content, (bytes, bytearray)):
            text = bytes(content).decode("utf-8", "replace")
        else:
            text = str(content)
        sha = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()
        return text, sha

    def _parse_xsd(self, filename: str, content: bytes) -> Optional[ESRObject]:
        try:
            import xml.etree.ElementTree as ET
            text, sha = self._full_text(content)
            ns = ""
            try:
                ns = ET.fromstring(text).get("targetNamespace", "")
            except Exception:
                pass  # keep the full file even if it doesn't parse cleanly
            name = filename.rsplit("/", 1)[-1].replace(".xsd", "")
            return ESRObject(
                id=name, name=name, namespace=ns,
                software_component="", obj_type="DataType",
                description=f"XSD data type from {filename}",
                content=text, content_sha=sha,
            )
        except Exception:
            return None

    def _parse_wsdl(self, filename: str, content: bytes) -> Optional[ESRObject]:
        try:
            import xml.etree.ElementTree as ET
            text, sha = self._full_text(content)
            ops = []
            try:
                ops = [el.get("name", "") for el in ET.fromstring(text).iter()
                       if "operation" in el.tag.lower()]
            except Exception:
                pass
            name = filename.rsplit("/", 1)[-1].replace(".wsdl", "").replace(".xml", "")
            return ESRObject(
                id=name, name=name, namespace="",
                software_component="", obj_type="ServiceInterface",
                description=f"WSDL service interface from {filename}",
                operations=list(dict.fromkeys(o for o in ops if o)),
                content=text, content_sha=sha,
            )
        except Exception:
            return None

    def _parse_edmx(self, filename: str, content: bytes) -> Optional[ESRObject]:
        try:
            text, sha = self._full_text(content)
            name = filename.rsplit("/", 1)[-1].replace(".edmx", "").replace(".xml", "")
            return ESRObject(
                id=name, name=name, namespace="",
                software_component="", obj_type="EDMX",
                description=f"OData EDMX metadata from {filename}",
                content=text, content_sha=sha,
            )
        except Exception:
            return None

    def _parse_mmap(self, filename: str, content: bytes) -> Optional[ESRObject]:
        name = filename.replace(".mmap", "").replace(".xim", "")
        return ESRObject(
            id=name, name=name, namespace="",
            software_component="", obj_type="MessageMapping",
            mapping_type="Graphical",
            description=f"Message mapping exported from PI/PO: {filename}",
        )
