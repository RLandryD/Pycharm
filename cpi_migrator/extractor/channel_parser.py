"""
extractor/channel_parser.py

Reads SAP PI/PO Communication Channel configurations directly from:
  1. PI/PO Integration Directory REST API (live)
  2. Exported channel XML files (offline)

Extracts ALL channel parameters — not just adapter type — so they
can be pre-filled into InterfaceConfig in the workbench.

PI/PO REST endpoints used:
  GET /CommunicationChannel/CommunicationChannel
  GET /CommunicationChannel/CommunicationChannel('{channel_id}')
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------

@dataclass
class ChannelConfig:
    """All extracted configuration from one PI/PO Communication Channel."""
    channel_id: str
    channel_name: str
    adapter_type: str
    direction: str                          # "Sender" or "Receiver"
    transport_protocol: str = ""
    message_protocol: str = ""

    # Connectivity
    address: str = ""
    path: str = ""
    port: int = 0
    protocol: str = "HTTPS"

    # Authentication
    auth_type: str = ""                     # Basic / X509 / OAuth / None
    credential_name: str = ""
    username: str = ""

    # Adapter-specific raw parameters
    parameters: dict[str, str] = field(default_factory=dict)

    # IDoc
    idoc_type: str = ""
    idoc_message_type: str = ""
    idoc_partner_number: str = ""

    # File/FTP/SFTP
    file_directory: str = ""
    file_pattern: str = ""
    file_poll_interval: str = ""
    file_post_processing: str = ""

    # SOAP/HTTP
    wsdl_url: str = ""
    service_name: str = ""
    endpoint_url: str = ""

    # RFC
    rfc_destination: str = ""
    function_module: str = ""

    # JDBC
    jdbc_driver: str = ""
    jdbc_url: str = ""
    jdbc_query: str = ""

    # Messaging
    queue_name: str = ""
    topic_name: str = ""

    raw_xml: str = ""


# ---------------------------------------------------------------------------
# REST channel parser
# ---------------------------------------------------------------------------

class PIChannelParser:
    """Fetches and parses Communication Channel configs from PI/PO REST API."""

    def __init__(self, base_url: str, session: requests.Session):
        self.base_url = base_url.rstrip("/")
        self.session  = session

    def fetch_all_channels(self) -> list[ChannelConfig]:
        url  = f"{self.base_url}/CommunicationChannel/CommunicationChannel"
        channels = []
        skip = 0

        while True:
            resp = self.session.get(
                url,
                params={"$format": "json", "$top": 100, "$skip": skip},
                timeout=30,
            )
            resp.raise_for_status()
            data    = resp.json()
            entries = data.get("d", {}).get("results", [])
            if not entries:
                break
            for entry in entries:
                try:
                    channel_id = entry.get("CommunicationChannelID", "")
                    detail     = self._fetch_channel_detail(channel_id)
                    if detail:
                        channels.append(detail)
                except Exception as exc:
                    logger.warning("Could not fetch channel %s: %s", channel_id, exc)
            skip += 100
            if len(entries) < 100:
                break

        logger.info("Fetched %d communication channels from PI/PO", len(channels))
        return channels

    def _fetch_channel_detail(self, channel_id: str) -> Optional[ChannelConfig]:
        url  = f"{self.base_url}/CommunicationChannel/CommunicationChannel('{channel_id}')"
        resp = self.session.get(url, params={"$format": "json"}, timeout=20)
        resp.raise_for_status()
        data = resp.json().get("d", {})
        return self._parse_channel_json(data)

    def _parse_channel_json(self, data: dict) -> Optional[ChannelConfig]:
        adapter = data.get("AdapterType", "")
        if not adapter:
            return None

        ch = ChannelConfig(
            channel_id=data.get("CommunicationChannelID", ""),
            channel_name=data.get("CommunicationChannelName", ""),
            adapter_type=adapter,
            direction=data.get("Direction", ""),
        )

        # Extract nested channel parameters
        params_raw = data.get("AdapterSpecificAttribute", {})
        if isinstance(params_raw, dict):
            results = params_raw.get("results", [])
            for p in results:
                k = p.get("Name", "")
                v = p.get("Value", "")
                ch.parameters[k] = v
                self._map_parameter(ch, k, v)

        return ch

    @staticmethod
    def _map_parameter(ch: ChannelConfig, key: str, value: str):
        """Map known PI/PO parameter names to ChannelConfig fields."""
        k = key.lower().replace("_", "").replace(" ", "")
        if not value:
            return

        mappings = {
            # Connectivity
            "targethost":          lambda: setattr(ch, "address", value),
            "host":                lambda: setattr(ch, "address", value),
            "serverport":          lambda: setattr(ch, "port", int(value) if value.isdigit() else 0),
            "port":                lambda: setattr(ch, "port", int(value) if value.isdigit() else 0),
            "urlpath":             lambda: setattr(ch, "path", value),
            "path":                lambda: setattr(ch, "path", value),
            "targeturl":           lambda: setattr(ch, "endpoint_url", value),
            "url":                 lambda: setattr(ch, "endpoint_url", value),
            # Auth
            "authenticationtype":  lambda: setattr(ch, "auth_type", value),
            "credentialname":      lambda: setattr(ch, "credential_name", value),
            "username":            lambda: setattr(ch, "username", value),
            # IDoc
            "idoctype":            lambda: setattr(ch, "idoc_type", value),
            "messagetype":         lambda: setattr(ch, "idoc_message_type", value),
            "partnernumber":       lambda: setattr(ch, "idoc_partner_number", value),
            # File
            "sourcedirectory":     lambda: setattr(ch, "file_directory", value),
            "filedirectory":       lambda: setattr(ch, "file_directory", value),
            "filepattern":         lambda: setattr(ch, "file_pattern", value),
            "pollinterval":        lambda: setattr(ch, "file_poll_interval", value),
            "postprocessing":      lambda: setattr(ch, "file_post_processing", value),
            # SOAP
            "wsdlurl":             lambda: setattr(ch, "wsdl_url", value),
            "servicename":         lambda: setattr(ch, "service_name", value),
            # RFC
            "rfcdestination":      lambda: setattr(ch, "rfc_destination", value),
            "functionmodule":      lambda: setattr(ch, "function_module", value),
            # JDBC
            "driverclass":         lambda: setattr(ch, "jdbc_driver", value),
            "connectionurl":       lambda: setattr(ch, "jdbc_url", value),
            "sqlstatement":        lambda: setattr(ch, "jdbc_query", value),
            # JMS/AMQP
            "queuename":           lambda: setattr(ch, "queue_name", value),
            "topicname":           lambda: setattr(ch, "topic_name", value),
        }
        fn = mappings.get(k)
        if fn:
            try:
                fn()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# XML file parser (offline / exported channels)
# ---------------------------------------------------------------------------

class XMLChannelParser:
    """
    Parses Communication Channel XML files exported from PI/PO.
    Accepts individual channel XML files or a directory of them.
    """

    def parse_file(self, xml_path: str) -> list[ChannelConfig]:
        path = Path(xml_path)
        if path.is_dir():
            channels = []
            for f in path.glob("*.xml"):
                channels.extend(self._parse_xml_file(f))
            return channels
        return self._parse_xml_file(path)

    def parse_bytes(self, xml_bytes: bytes) -> list[ChannelConfig]:
        try:
            root = ET.fromstring(xml_bytes)
            return self._extract_channels(root)
        except ET.ParseError as e:
            logger.error("XML parse error: %s", e)
            return []

    def _parse_xml_file(self, path: Path) -> list[ChannelConfig]:
        try:
            tree = ET.parse(path)
            return self._extract_channels(tree.getroot())
        except Exception as e:
            logger.warning("Could not parse %s: %s", path, e)
            return []

    def _extract_channels(self, root: ET.Element) -> list[ChannelConfig]:
        channels = []
        # Handle different PI/PO XML export formats
        ns = {
            "xi": "http://sap.com/xi/XI/Integrationflows",
            "com": "http://sap.com/xi/Communication",
        }

        # Try XI namespace first
        for ch_el in root.iter():
            tag = ch_el.tag.split("}")[-1] if "}" in ch_el.tag else ch_el.tag
            if tag in ("CommunicationChannel", "Channel"):
                ch = self._parse_channel_element(ch_el)
                if ch:
                    channels.append(ch)

        return channels

    def _parse_channel_element(self, el: ET.Element) -> Optional[ChannelConfig]:
        def find_text(tag: str) -> str:
            found = el.find(f".//{tag}")
            return found.text.strip() if found is not None and found.text else ""

        adapter = find_text("AdapterType") or find_text("TransportProtocol")
        if not adapter:
            return None

        ch = ChannelConfig(
            channel_id=find_text("ChannelID") or find_text("Name") or "",
            channel_name=find_text("ChannelName") or find_text("Name") or "",
            adapter_type=adapter,
            direction=find_text("Direction") or "",
            raw_xml=ET.tostring(el, encoding="unicode"),
        )

        # Extract all AdapterSpecificAttribute entries
        for attr in el.iter("AdapterSpecificAttribute"):
            name  = (attr.find("Name") or attr).text or ""
            value_el = attr.find("Value")
            value = value_el.text.strip() if value_el is not None and value_el.text else ""
            if name and value:
                ch.parameters[name] = value
                PIChannelParser._map_parameter(ch, name, value)

        return ch


# ---------------------------------------------------------------------------
# Converter: ChannelConfig → InterfaceConfig fields
# ---------------------------------------------------------------------------

def apply_channel_to_config(
    channel: ChannelConfig,
    cfg,                    # InterfaceConfig
    side: str = "receiver", # "sender" or "receiver"
):
    """
    Apply extracted channel parameters to an InterfaceConfig.
    Fills connectivity, auth, and adapter-specific fields.
    """
    from models.interface_config import AuthConfig, ConnectivityConfig

    conn = cfg.sender_connectivity if side == "sender" else cfg.receiver_connectivity
    auth = cfg.sender_auth         if side == "sender" else cfg.receiver_auth

    # Connectivity
    if channel.address:       conn.address  = channel.address
    if channel.path:          conn.path     = channel.path
    if channel.port:          conn.port     = channel.port
    if channel.endpoint_url:  conn.address  = channel.endpoint_url

    # Auth
    auth_map = {
        "basic":       "Basic",
        "basicauth":   "Basic",
        "x509":        "Certificate",
        "certificate": "Certificate",
        "oauth":       "OAuth2 Client Credentials",
        "oauth2":      "OAuth2 Client Credentials",
        "none":        "None",
        "":            "Basic",
    }
    auth.method          = auth_map.get(channel.auth_type.lower().replace(" ", ""), "Basic")
    auth.credential_name = channel.credential_name or auth.credential_name

    # Adapter-specific
    msg = cfg.message
    if channel.idoc_type:            msg.idoc_type           = channel.idoc_type
    if channel.idoc_message_type:    msg.idoc_message_type   = channel.idoc_message_type
    if channel.idoc_partner_number:  msg.idoc_partner_profile = channel.idoc_partner_number
    if channel.file_directory:       msg.file_directory      = channel.file_directory
    if channel.file_pattern:         msg.file_pattern        = channel.file_pattern
    if channel.file_post_processing: msg.file_post_processing = channel.file_post_processing
    if channel.wsdl_url:             cfg.message.__dict__["extra"] = \
                                        {**cfg.message.__dict__.get("extra", {}),
                                         "wsdl_url": channel.wsdl_url}
    if channel.jdbc_driver:          msg.jdbc_driver = channel.jdbc_driver
    if channel.jdbc_url:             msg.jdbc_jndi   = channel.jdbc_url
    if channel.jdbc_query:           msg.jdbc_query  = channel.jdbc_query
    if channel.rfc_destination:
        cfg.message.__dict__.setdefault("extra", {})["rfc_destination"] = channel.rfc_destination
    if channel.function_module:
        cfg.message.__dict__.setdefault("extra", {})["function_module"] = channel.function_module

    return cfg
