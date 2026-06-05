"""
models/interface_config.py
Full configuration model for one migrated CPI interface.
Populated by the Streamlit workbench Tab 4 (configurator) and
consumed by the scaffolder to generate ready-to-import iFlows.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


AUTH_METHODS   = ["Basic", "OAuth2 Client Credentials", "API Key", "Certificate", "None"]
MESSAGE_FORMATS = ["XML", "JSON", "IDoc", "CSV", "Binary", "Text"]
LOG_LEVELS     = ["None", "Header only", "Header + Body"]
ADAPTER_TYPES  = ["HTTPS", "HTTP", "SOAP", "OData", "REST", "IDoc", "RFC",
                  "File", "SFTP", "FTP", "JDBC", "JMS", "AMQP", "AS2", "AS4",
                  "SuccessFactors", "Mail", "ProcessDirect", "XI"]


@dataclass
class AuthConfig:
    method: str = "Basic"                    # one of AUTH_METHODS
    credential_name: str = ""                # CPI secure parameter alias
    token_url: str = ""                      # OAuth2 only
    client_id: str = ""                      # OAuth2 only (stored in session, not file)
    client_secret: str = ""                  # OAuth2 only
    api_key_header: str = "APIKey"           # API Key only
    api_key_value: str = ""                  # API Key only
    certificate_alias: str = ""              # Certificate only


@dataclass
class ConnectivityConfig:
    address: str = ""                        # host or full URL
    path: str = ""                           # URL path
    port: int = 0                            # 0 = use default for protocol
    protocol: str = "HTTPS"
    proxy_host: str = ""
    proxy_port: int = 0


@dataclass
class MessageConfig:
    is_async: bool = False
    format: str = "XML"                      # one of MESSAGE_FORMATS
    content_type: str = "application/xml"
    encoding: str = "UTF-8"
    mapping_program: str = ""
    xslt_program: str = ""
    namespace: str = ""
    # IDoc-specific
    idoc_type: str = ""
    idoc_message_type: str = ""
    idoc_partner_profile: str = ""
    # AS2/AS4-specific
    as2_partner_id: str = ""
    as2_signing_alg: str = "SHA-256"
    as2_encryption_alg: str = "AES128"
    as2_mdn_required: bool = False
    # JDBC-specific
    jdbc_driver: str = ""
    jdbc_jndi: str = ""
    jdbc_query: str = ""
    # File/SFTP-specific
    file_directory: str = ""
    file_pattern: str = "*.*"
    file_post_processing: str = "Delete"     # Delete / Move / Archive
    file_poll_interval_sec: int = 60
    file_archive_dir: str = ""


@dataclass
class ReliabilityConfig:
    retry_enabled: bool = True
    retry_max_attempts: int = 3
    retry_delay_sec: int = 60
    retry_exponential_backoff: bool = False
    dead_letter_enabled: bool = False
    dead_letter_queue: str = ""
    idempotency_enabled: bool = False
    idempotency_header: str = "MessageId"
    alert_on_failure: bool = True
    alert_address: str = ""
    log_level: str = "Header only"           # one of LOG_LEVELS
    store_message_on_failure: bool = True


@dataclass
class RuntimeConfig:
    timeout_sec: int = 300
    max_message_mb: int = 40
    scheduler_cron: str = ""                 # empty = not scheduled
    parallel_enabled: bool = False
    parallel_max_threads: int = 1
    quality_of_service: str = "Exactly Once" # Exactly Once / At Least Once / Best Effort


@dataclass
class InterfaceConfig:
    """Complete configuration for one PI/PO → CPI migration."""
    # Identity
    interface_name: str = ""
    target_id: str = "s4hana_cloud"
    std_iflow_id: str = ""                   # chosen standard iFlow ID
    std_iflow_package: str = ""

    # Sender (the system sending data INTO CPI)
    sender_adapter: str = "HTTPS"
    sender_connectivity: ConnectivityConfig  = field(default_factory=ConnectivityConfig)
    sender_auth: AuthConfig                  = field(default_factory=AuthConfig)

    # Receiver (the system CPI sends data TO)
    receiver_adapter: str = "HTTPS"
    receiver_connectivity: ConnectivityConfig = field(default_factory=ConnectivityConfig)
    receiver_auth: AuthConfig                 = field(default_factory=AuthConfig)

    # Message processing
    message: MessageConfig    = field(default_factory=MessageConfig)

    # Reliability / error handling
    reliability: ReliabilityConfig = field(default_factory=ReliabilityConfig)

    # Runtime
    runtime: RuntimeConfig    = field(default_factory=RuntimeConfig)

    # Post-processing notes — items the tool couldn't automate
    manual_steps: list[str]   = field(default_factory=list)

    def to_flat_dict(self) -> dict:
        """Flatten to a single dict for Jinja2 template rendering."""
        d = {}
        for section_name, section in [
            ("sender_conn",    self.sender_connectivity),
            ("sender_auth",    self.sender_auth),
            ("receiver_conn",  self.receiver_connectivity),
            ("receiver_auth",  self.receiver_auth),
            ("msg",            self.message),
            ("reliability",    self.reliability),
            ("runtime",        self.runtime),
        ]:
            for k, v in section.__dict__.items():
                d[f"{section_name}_{k}"] = v
        d["interface_name"]    = self.interface_name
        d["target_id"]         = self.target_id
        d["std_iflow_id"]      = self.std_iflow_id
        d["sender_adapter"]    = self.sender_adapter
        d["receiver_adapter"]  = self.receiver_adapter
        d["manual_steps"]      = self.manual_steps
        return d

    @classmethod
    def from_interface_record(cls, record, target_id: str = "s4hana_cloud") -> "InterfaceConfig":
        """Pre-populate from a PI/PO InterfaceRecord — user refines in UI."""
        from destinations.registry import DESTINATION_REGISTRY
        target = DESTINATION_REGISTRY.get(target_id)
        rec_adapter = target.adapter_mapping.get(
            record.receiver_adapter, record.receiver_adapter
        ) if target else record.receiver_adapter

        cfg = cls(
            interface_name=record.name,
            target_id=target_id,
            sender_adapter=record.sender_adapter,
            receiver_adapter=rec_adapter,
            manual_steps=[],
        )
        cfg.sender_connectivity.address = record.sender_system
        cfg.receiver_connectivity.address = record.receiver_system
        cfg.message.mapping_program = record.mapping_program or ""
        cfg.message.namespace = record.namespace
        cfg.message.is_async = record.has_bpm

        # IDoc pre-fill
        if record.sender_adapter == "IDoc" or record.receiver_adapter == "IDoc":
            cfg.message.idoc_message_type = record.message_interface

        # File adapter pre-fill
        if record.sender_adapter in ("File", "FTP", "SFTP"):
            cfg.message.file_poll_interval_sec = 60

        # Reliability defaults for high-volume adapters
        if record.sender_adapter in ("IDoc", "RFC", "JDBC"):
            cfg.reliability.retry_enabled = True
            cfg.reliability.store_message_on_failure = True

        return cfg
