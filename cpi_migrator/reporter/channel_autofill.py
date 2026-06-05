"""
reporter/channel_autofill.py

Resolves real PI/PO Communication Channel values into the fields the
infrastructure guide (WE20 / WE21 / SM59 / JDBC) would otherwise leave as
[fill] placeholders.

The infrastructure_guide generator already reads from InterfaceConfig.message,
but many concrete values (partner numbers, RFC destinations, JDBC URLs, file
directories) live on the ChannelConfig produced by extractor/channel_parser.py
and never reach the sheet. This module bridges that gap WITHOUT modifying the
channel parser or the config model.

Usage:
    autofill = ChannelAutofill(channels)          # list[ChannelConfig]
    values   = autofill.for_interface("MY_IFACE") # -> InfraAutofill
    # then pass `channels=...` to InfrastructureGuideGenerator.generate()

The matcher links a channel to an interface by name overlap and by adapter
type, since PI/PO channel names rarely equal interface names exactly.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Resolved value bundle (one per interface)
# ---------------------------------------------------------------------------

@dataclass
class InfraAutofill:
    """Concrete infrastructure values resolved from channel data.

    Any empty string means "channel did not provide it" — the guide should
    then fall back to its existing placeholder behaviour.
    """
    interface_name: str = ""

    # IDoc (WE20 / WE21)
    idoc_type: str = ""
    idoc_message_type: str = ""
    idoc_partner_number: str = ""

    # RFC (SM59)
    rfc_destination: str = ""
    rfc_target_host: str = ""
    rfc_function_module: str = ""

    # JDBC
    jdbc_driver: str = ""
    jdbc_url: str = ""
    jdbc_query: str = ""

    # File / SFTP
    file_directory: str = ""
    file_pattern: str = ""

    # Connectivity / auth (useful for SM59 + WE21 target URLs)
    address: str = ""
    path: str = ""
    port: int = 0
    auth_type: str = ""
    credential_name: str = ""

    # The raw channel parameters that backed this resolution (audit trail)
    source_parameters: dict = field(default_factory=dict)

    @property
    def has_any(self) -> bool:
        return any([
            self.idoc_type, self.idoc_message_type, self.idoc_partner_number,
            self.rfc_destination, self.rfc_target_host, self.rfc_function_module,
            self.jdbc_driver, self.jdbc_url, self.jdbc_query,
            self.file_directory, self.address, self.credential_name,
        ])


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------

def _tokens(text: str) -> set[str]:
    return {t.lower() for t in re.split(r"[_\-\s/.:]", text or "") if len(t) > 2}


class ChannelAutofill:
    """Indexes a set of ChannelConfig objects and resolves per-interface values."""

    def __init__(self, channels: list):
        # channels: list[ChannelConfig] — duck-typed to avoid hard import
        self.channels = channels or []

    def for_interface(self, interface_name: str,
                      sender_adapter: str = "",
                      receiver_adapter: str = "") -> InfraAutofill:
        """Find the best-matching channel(s) and merge their values.

        Prefers a name-token overlap; falls back to adapter-type match.
        Merges across all matching channels (sender + receiver) so an
        interface with two channels gets both sides filled.
        """
        result = InfraAutofill(interface_name=interface_name)
        iface_tokens = _tokens(interface_name)
        wanted_adapters = {a.lower() for a in (sender_adapter, receiver_adapter) if a}

        matches: list[tuple[int, object]] = []
        for ch in self.channels:
            score = 0
            ch_tokens = _tokens(getattr(ch, "channel_name", "")) | _tokens(getattr(ch, "channel_id", ""))
            overlap = iface_tokens & ch_tokens
            score += 3 * len(overlap)
            if wanted_adapters and getattr(ch, "adapter_type", "").lower() in wanted_adapters:
                score += 2
            if score > 0:
                matches.append((score, ch))

        # If nothing matched by name/adapter, and there is exactly one channel,
        # use it (common for single-interface offline exports).
        if not matches and len(self.channels) == 1:
            matches = [(1, self.channels[0])]

        matches.sort(key=lambda x: x[0], reverse=True)

        for _, ch in matches:
            self._merge(result, ch)

        return result

    @staticmethod
    def _merge(result: InfraAutofill, ch) -> None:
        """Copy non-empty channel values into result without overwriting
        values already resolved by a higher-scoring channel."""
        def take(attr_dst: str, val):
            if val and not getattr(result, attr_dst):
                setattr(result, attr_dst, val)

        take("idoc_type", getattr(ch, "idoc_type", ""))
        take("idoc_message_type", getattr(ch, "idoc_message_type", ""))
        take("idoc_partner_number", getattr(ch, "idoc_partner_number", ""))
        take("rfc_destination", getattr(ch, "rfc_destination", ""))
        take("rfc_function_module", getattr(ch, "function_module", ""))
        take("jdbc_driver", getattr(ch, "jdbc_driver", ""))
        take("jdbc_url", getattr(ch, "jdbc_url", ""))
        take("jdbc_query", getattr(ch, "jdbc_query", ""))
        take("file_directory", getattr(ch, "file_directory", ""))
        take("file_pattern", getattr(ch, "file_pattern", ""))
        take("address", getattr(ch, "address", "") or getattr(ch, "endpoint_url", ""))
        take("path", getattr(ch, "path", ""))
        take("auth_type", getattr(ch, "auth_type", ""))
        take("credential_name", getattr(ch, "credential_name", ""))
        if not result.rfc_target_host and getattr(ch, "address", ""):
            result.rfc_target_host = ch.address
        if not result.port and getattr(ch, "port", 0):
            result.port = ch.port
        params = getattr(ch, "parameters", None)
        if params:
            result.source_parameters.update(params)

    def index_by_interface(self, interface_names: list[str]) -> dict[str, InfraAutofill]:
        """Resolve autofill for a list of interface names at once."""
        return {name: self.for_interface(name) for name in interface_names}
