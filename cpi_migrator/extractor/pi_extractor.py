"""
extractor/pi_extractor.py
Extracts the interface inventory from SAP PI/PO via:
  1. PI/PO Integration Directory REST API  (preferred)
  2. Exported Excel/XML file               (fallback / offline mode)

Each extracted interface is normalised into an InterfaceRecord dataclass.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class InterfaceRecord:
    """Normalised representation of one PI/PO interface."""
    id: str
    name: str
    namespace: str
    software_component: str
    sender_system: str
    receiver_system: str
    sender_adapter: str          # e.g. FILE, IDOC, SOAP, RFC, JDBC, REST …
    receiver_adapter: str
    message_interface: str
    mapping_program: Optional[str] = None
    has_bpm: bool = False        # Business Process (BPM/ccBPM)
    has_multi_mapping: bool = False
    channel_count: int = 1
    description: str = ""
    steps_spec: str = ""   # explicit CPI step pipeline (Steps column)
    # Real source CPI iFlow XML, when this record came from an uploaded CPI
    # package. Present → the scaffolder regenerates from the true structure +
    # config (clean-room) instead of sizing a placeholder from metadata.
    source_iflow_xml: str = ""
    # Real SAP Migration Assessment figures, when imported from an MA export.
    # When ma_weight is set, the workbench uses the engine's Mode 1
    # (assess_true_ma) — calibrated SAP weight/size/category/effort — instead of
    # the keyword approximation. Left None for hand-built / PI-PO-export records.
    ma_weight: Optional[int] = None
    ma_size: str = ""
    ma_status: str = ""
    raw: dict = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# Adapter alias normalisation
# ---------------------------------------------------------------------------

ADAPTER_ALIASES = {
    "file":       "File",
    "ftp":        "FTP",
    "sftp":       "SFTP",
    "idoc":       "IDoc",
    "idocaae":    "IDoc",
    "soap":       "SOAP",
    "ws":         "SOAP",
    "rest":       "HTTP",
    "http":       "HTTP",
    "https":      "HTTPS",
    "rfc":        "RFC",
    "jdbc":       "JDBC",
    "jms":        "JMS",
    "mail":       "Mail",
    "smtp":       "Mail",
    "xi":         "ProcessDirect",
    "xi30":       "ProcessDirect",
    "as2":        "AS2",
    "as4":        "AS4",
    "odatav2":    "OData",
    "odata":      "OData",
}

def normalise_adapter(raw: str) -> str:
    return ADAPTER_ALIASES.get(raw.lower().strip(), raw.strip())


# ---------------------------------------------------------------------------
# REST extractor
# ---------------------------------------------------------------------------

class PIRestExtractor:
    """
    Pulls integrated configuration objects from SAP PI/PO via the
    Integration Directory REST API.

    Endpoint pattern:
      GET /CommunicationChannel/IntegratedConfiguration
    """

    def __init__(self, base_url: str, session: requests.Session):
        self.base_url = base_url.rstrip("/")
        self.session = session

    def extract_all(self) -> list[InterfaceRecord]:
        logger.info("Extracting integrated configurations from PI/PO …")
        configs = self._fetch_integrated_configs()
        records = [self._parse_config(c) for c in configs]
        logger.info("Extracted %d interface records.", len(records))
        return records

    def _fetch_integrated_configs(self) -> list[dict]:
        url = f"{self.base_url}/CommunicationChannel/IntegratedConfiguration"
        results, skip = [], 0
        page_size = 100

        while True:
            resp = self.session.get(
                url,
                params={"$format": "json", "$top": page_size, "$skip": skip},
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            entries = data.get("d", {}).get("results", [])
            if not entries:
                break
            results.extend(entries)
            skip += page_size
            if len(entries) < page_size:
                break

        return results

    def _parse_config(self, raw: dict) -> InterfaceRecord:
        sender = raw.get("SenderComponentName", "")
        receiver = raw.get("ReceiverComponentName", "")
        sender_ch = raw.get("SenderChannel", {}) or {}
        receiver_ch = raw.get("ReceiverChannel", {}) or {}

        return InterfaceRecord(
            id=raw.get("IntegratedConfigurationID", ""),
            name=raw.get("IntegratedConfigurationName", ""),
            namespace=raw.get("IntegratedConfigurationNamespace", ""),
            software_component=raw.get("SoftwareComponentName", ""),
            sender_system=sender,
            receiver_system=receiver,
            sender_adapter=normalise_adapter(sender_ch.get("AdapterType", "HTTPS")),
            receiver_adapter=normalise_adapter(receiver_ch.get("AdapterType", "HTTPS")),
            message_interface=raw.get("InboundMessageInterface", ""),
            mapping_program=raw.get("MappingProgram"),
            has_bpm="BPM" in str(raw).upper() or "CCBPM" in str(raw).upper(),
            has_multi_mapping=raw.get("HasMultiMapping", False),
            channel_count=int(raw.get("NumberOfChannels", 1)),
            steps_spec=raw.get("Steps", "") or "",
            description=raw.get("Description", ""),
            raw=raw,
        )


# ---------------------------------------------------------------------------
# Excel / file-based extractor (offline / export mode)
# ---------------------------------------------------------------------------

class PIFileExtractor:
    """
    Parses a PI/PO interface inventory exported to Excel.
    Expected columns (case-insensitive):
      Name, Namespace, SenderSystem, ReceiverSystem, SenderAdapter,
      ReceiverAdapter, MessageInterface, MappingProgram, Description
    """

    COLUMN_MAP = {
        "name":             "name",
        "namespace":        "namespace",
        "softwarecomponent": "software_component",
        "sendersystem":     "sender_system",
        "receiversystem":   "receiver_system",
        "senderadapter":    "sender_adapter",
        "receiveradapter":  "receiver_adapter",
        "messageinterface": "message_interface",
        "mappingprogram":   "mapping_program",
        "description":      "description",
        # Optional columns — drive HIGH-complexity scoring when present.
        # Absent columns default to False/1, preserving older Excel files.
        "hasbpm":           "has_bpm",
        "hasmultimapping":  "has_multi_mapping",
        "numberofchannels": "channel_count",
        "steps":            "steps_spec",
    }

    def __init__(self, file_path: str):
        self.file_path = file_path

    def extract_all(self) -> list[InterfaceRecord]:
        try:
            import openpyxl
        except ImportError:
            raise RuntimeError("openpyxl is required for Excel extraction. pip install openpyxl")

        logger.info("Reading PI/PO export from %s", self.file_path)
        wb = openpyxl.load_workbook(self.file_path, read_only=True, data_only=True)
        ws = wb.active

        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []

        # Map header row to normalised field names
        headers = [str(h).strip().lower().replace(" ", "").replace("_", "") if h else "" for h in rows[0]]
        field_idx = {}
        for col_idx, h in enumerate(headers):
            mapped = self.COLUMN_MAP.get(h)
            if mapped:
                field_idx[mapped] = col_idx

        records = []
        for row_num, row in enumerate(rows[1:], start=2):
            def get(field_name: str, default=""):
                idx = field_idx.get(field_name)
                if idx is None:
                    return default
                val = row[idx]
                return str(val).strip() if val is not None else default

            def get_bool(field_name: str) -> bool:
                """Read a truthy cell — accepts true/yes/1/y, case-insensitive."""
                idx = field_idx.get(field_name)
                if idx is None:
                    return False
                val = row[idx]
                if val is None:
                    return False
                if isinstance(val, bool):
                    return val
                return str(val).strip().lower() in {"true", "yes", "y", "1", "x"}

            def get_int(field_name: str, default: int = 1) -> int:
                idx = field_idx.get(field_name)
                if idx is None:
                    return default
                val = row[idx]
                if val is None:
                    return default
                try:
                    return max(1, int(float(val)))
                except (ValueError, TypeError):
                    return default

            name = get("name")
            if not name:
                continue

            records.append(InterfaceRecord(
                id=f"row_{row_num}",
                name=name,
                namespace=get("namespace"),
                software_component=get("software_component"),
                sender_system=get("sender_system"),
                receiver_system=get("receiver_system"),
                sender_adapter=normalise_adapter(get("sender_adapter", "HTTPS")),
                receiver_adapter=normalise_adapter(get("receiver_adapter", "HTTPS")),
                message_interface=get("message_interface"),
                mapping_program=get("mapping_program") or None,
                description=get("description"),
                has_bpm=get_bool("has_bpm"),
                has_multi_mapping=get_bool("has_multi_mapping"),
                channel_count=get_int("channel_count", 1),
                steps_spec=get("steps_spec"),
            ))

        logger.info("Loaded %d records from file.", len(records))
        return records


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_extractor(cfg: dict, pi_session: requests.Session = None):
    """Return the appropriate extractor based on config."""
    pi_cfg = cfg.get("pi", {})
    export_file = pi_cfg.get("export_file")

    if export_file and os.path.exists(export_file):
        logger.info("Using file-based extractor: %s", export_file)
        return PIFileExtractor(export_file)

    if pi_session is None:
        raise ValueError("A PI session is required when no export_file is configured.")

    base_url = pi_cfg.get("base_url", "")
    logger.info("Using REST extractor against %s", base_url)
    return PIRestExtractor(base_url, pi_session)
