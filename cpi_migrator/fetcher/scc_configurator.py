"""
fetcher/scc_configurator.py

Auto-configures connectivity settings based on deployment topology:
  - On-Premise / Private Cloud  → injects SAP Cloud Connector (SCC) proxy
  - Public Cloud (SaaS)         → defaults to direct OAuth2 / HTTPS routing
  - Hybrid                      → SCC for sender, OAuth for receiver (or vice versa)

Called automatically in Tab 4 when the user selects a destination target,
and can be re-applied any time the adapter or target changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from models.interface_config import InterfaceConfig, AuthConfig, ConnectivityConfig


# ---------------------------------------------------------------------------
# SCC default settings (user can override in workbench)
# ---------------------------------------------------------------------------

@dataclass
class SCCProfile:
    """Represents one SCC location configured in BTP."""
    location_id: str          # SCC Location ID (configured in BTP cockpit)
    virtual_host: str         # Virtual host mapped in SCC
    virtual_port: int         # Virtual port mapped in SCC
    protocol: str = "HTTPS"   # HTTPS or HTTP


# Deployment topology → routing strategy
TOPOLOGY_CLOUD_CONNECTOR = {"onpremise", "private_cloud", "hybrid_sender"}
TOPOLOGY_DIRECT_OAUTH    = {"cloud", "saas", "paas", "hybrid_receiver"}

# Destination target variant → topology
TARGET_TOPOLOGY = {
    "s4hana_cloud":    "saas",
    "s4hana_op":       "onpremise",
    "ariba":           "saas",
    "successfactors":  "saas",
    "btp":             "paas",
    "fieldglass":      "saas",
    "concur":          "saas",
}

# Default OAuth token URL patterns per target
DEFAULT_TOKEN_URLS = {
    "s4hana_cloud":   "https://{tenant}.authentication.{region}.hana.ondemand.com/oauth/token",
    "ariba":          "https://api.ariba.com/v2/oauth/token",
    "successfactors": "https://{dc}.successfactors.com/oauth/token",
    "btp":            "https://{tenant}.authentication.{region}.hana.ondemand.com/oauth/token",
    "fieldglass":     "https://api.fieldglass.net/oauth2/v2.0/token",
    "concur":         "https://us.api.concursolutions.com/oauth2/v0/token",
}

# Adapters that always need Cloud Connector regardless of target
ALWAYS_NEEDS_SCC = {"RFC", "JDBC", "IDoc"}

# Adapters that never need Cloud Connector
NEVER_NEEDS_SCC  = {"HTTPS", "HTTP", "SOAP", "OData", "REST", "AS2", "AS4",
                    "SuccessFactors", "AMQP", "MQTT"}


class SCCConfigurator:
    """
    Auto-applies connectivity and auth settings to an InterfaceConfig
    based on the selected destination target and adapter types.
    """

    def __init__(self, scc_profile: Optional[SCCProfile] = None):
        self.scc_profile = scc_profile or SCCProfile(
            location_id="",
            virtual_host="",
            virtual_port=443,
        )

    def configure(
        self,
        cfg: InterfaceConfig,
        target_id: str,
        tenant: str = "",
        region: str = "eu10",
    ) -> tuple[InterfaceConfig, list[str]]:
        """
        Apply auto-configuration to cfg in-place.
        Returns (updated_cfg, list_of_applied_changes).
        """
        changes = []
        topology = TARGET_TOPOLOGY.get(target_id, "cloud")

        # ── Sender side ───────────────────────────────────────────────
        sender_needs_scc = self._needs_scc(cfg.sender_adapter, topology, side="sender")
        if sender_needs_scc:
            changes += self._apply_scc(cfg.sender_connectivity, cfg.sender_auth,
                                       cfg.sender_adapter, side="Sender")
        else:
            changes += self._apply_direct(cfg.sender_connectivity, cfg.sender_auth,
                                          cfg.sender_adapter, side="Sender")

        # ── Receiver side ─────────────────────────────────────────────
        receiver_needs_scc = self._needs_scc(cfg.receiver_adapter, topology, side="receiver")
        if receiver_needs_scc:
            changes += self._apply_scc(cfg.receiver_connectivity, cfg.receiver_auth,
                                       cfg.receiver_adapter, side="Receiver")
        else:
            changes += self._apply_direct(cfg.receiver_connectivity, cfg.receiver_auth,
                                          cfg.receiver_adapter, side="Receiver",
                                          target_id=target_id, tenant=tenant, region=region)

        # ── Quality of service ────────────────────────────────────────
        if cfg.sender_adapter in ("IDoc", "JMS", "AMQP"):
            cfg.runtime.quality_of_service = "Exactly Once"
            changes.append("QoS set to 'Exactly Once' (IDoc/JMS/AMQP requires guaranteed delivery)")
        elif topology == "saas":
            cfg.runtime.quality_of_service = "At Least Once"
            changes.append("QoS set to 'At Least Once' (SaaS target)")

        # ── Retry defaults for on-premise ─────────────────────────────
        if topology == "onpremise":
            cfg.reliability.retry_enabled     = True
            cfg.reliability.retry_max_attempts = 3
            cfg.reliability.retry_delay_sec    = 60
            cfg.reliability.store_message_on_failure = True
            changes.append("Retry enabled (3×60s) — recommended for on-premise connectivity")

        return cfg, changes

    def _needs_scc(self, adapter: str, topology: str, side: str) -> bool:
        if adapter in ALWAYS_NEEDS_SCC:
            return True
        if adapter in NEVER_NEEDS_SCC:
            return False
        # For hybrid: sender side uses SCC, receiver uses direct
        if topology == "onpremise":
            return True
        if topology in ("saas", "paas", "cloud"):
            return False
        if topology == "hybrid_sender" and side == "sender":
            return True
        return False

    def _apply_scc(
        self,
        conn: ConnectivityConfig,
        auth: AuthConfig,
        adapter: str,
        side: str,
    ) -> list[str]:
        changes = []
        conn.protocol = "HTTPS"
        if self.scc_profile.virtual_host:
            conn.address = self.scc_profile.virtual_host
            conn.port    = self.scc_profile.virtual_port
            changes.append(f"{side}: address set to SCC virtual host "
                           f"{self.scc_profile.virtual_host}:{self.scc_profile.virtual_port}")
        if self.scc_profile.location_id:
            auth.credential_name = auth.credential_name or f"SCC_{side}_Credentials"
            changes.append(f"{side}: SCC Location ID = '{self.scc_profile.location_id}'")
        else:
            changes.append(f"{side}: ⚠ SCC required — set Location ID in sidebar settings")

        if adapter in ("RFC", "JDBC"):
            auth.method = "Basic"
            changes.append(f"{side}: auth method set to Basic (required for {adapter} via SCC)")
        return changes

    def _apply_direct(
        self,
        conn: ConnectivityConfig,
        auth: AuthConfig,
        adapter: str,
        side: str,
        target_id: str = "",
        tenant: str = "",
        region: str = "eu10",
    ) -> list[str]:
        changes = []
        conn.protocol = "HTTPS"

        # Set default OAuth for cloud targets
        if target_id and auth.method in ("Basic", ""):
            token_url_tpl = DEFAULT_TOKEN_URLS.get(target_id, "")
            if token_url_tpl and adapter not in ("IDoc", "File", "FTP", "SFTP"):
                auth.method    = "OAuth2 Client Credentials"
                auth.token_url = token_url_tpl.format(tenant=tenant or "YOUR_TENANT",
                                                       region=region, dc="api")
                auth.credential_name = auth.credential_name or f"{target_id.upper()}_OAuth"
                changes.append(f"{side}: auth defaulted to OAuth2 "
                               f"(public cloud target '{target_id}')")

        if not conn.address and target_id:
            changes.append(f"{side}: ⚠ endpoint address not set — fill in Tab 4 Connectivity")

        return changes


# ---------------------------------------------------------------------------
# Convenience function used by workbench.py
# ---------------------------------------------------------------------------

def auto_configure(
    cfg: InterfaceConfig,
    target_id: str,
    scc_location_id: str = "",
    scc_virtual_host: str = "",
    scc_virtual_port: int = 443,
    tenant: str = "",
    region: str = "eu10",
) -> tuple[InterfaceConfig, list[str]]:
    """
    One-call helper for the Streamlit workbench.
    Returns (updated_config, human-readable list of what was changed).
    """
    profile = SCCProfile(
        location_id=scc_location_id,
        virtual_host=scc_virtual_host,
        virtual_port=scc_virtual_port,
    )
    configurator = SCCConfigurator(scc_profile=profile)
    return configurator.configure(cfg, target_id, tenant=tenant, region=region)
