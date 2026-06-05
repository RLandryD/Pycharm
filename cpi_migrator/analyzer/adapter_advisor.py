"""
analyzer/adapter_advisor.py

Adapter-specific migration advisories. For each adapter type involved in an
interface, returns concrete guidance on what changes when migrating PI/PO →
Cloud Integration: which CPI adapter replaces it, known gaps, config notes,
and gotchas.

This encodes the practical knowledge a consultant applies per adapter, so the
generated migration guidance is specific ("your IDoc sender becomes the IDoc
adapter, but you'll need…") rather than generic.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AdapterAdvisory:
    pi_adapter: str
    cpi_adapter: str
    direction: str               # "sender" | "receiver" | "both"
    severity: str                # "info" | "caution" | "warning"
    notes: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)


# Knowledge base — PI/PO adapter -> CPI guidance. Patterns, not client data.
_ADVISORY_KB = {
    "IDOC": {
        "cpi": "IDoc adapter (or SOAP with IDoc message protocol)",
        "severity": "caution",
        "notes": [
            "IDoc adapter on CPI uses the SOAP/HTTP transport, not the ABAP RFC stack.",
            "Partner profiles and ports are configured differently — re-map on the SAP side.",
            "Use the IDoc XSD from WE60 for the message type.",
        ],
        "gaps": [
            "No direct tRFC/qRFC queue equivalent — use EOIO via JMS/data store for ordering.",
        ],
    },
    "RFC": {
        "cpi": "RFC adapter (requires Cloud Connector for on-prem)",
        "severity": "warning",
        "notes": [
            "RFC adapter needs the SAP Cloud Connector to reach on-prem systems.",
            "Synchronous RFC lookups become request-reply with the RFC receiver.",
        ],
        "gaps": [
            "No stateful tRFC — design idempotency explicitly.",
        ],
    },
    "FILE": {
        "cpi": "SFTP / File adapter",
        "severity": "info",
        "notes": [
            "Local NFS file access usually migrates to SFTP.",
            "File content conversion → use a converter step or Groovy.",
        ],
        "gaps": [],
    },
    "SFTP": {
        "cpi": "SFTP adapter",
        "severity": "info",
        "notes": ["Known-hosts and credential aliases configured in Security Material."],
        "gaps": [],
    },
    "JDBC": {
        "cpi": "JDBC adapter (requires Cloud Connector for on-prem DBs)",
        "severity": "warning",
        "notes": [
            "JDBC needs Cloud Connector + a JDBC data source configured on the tenant.",
            "SQL operations are supported but stored-procedure patterns may need rework.",
        ],
        "gaps": ["No native DB polling like PI's sender JDBC — use a scheduler + select."],
    },
    "SOAP": {
        "cpi": "SOAP adapter (SOAP 1.x)",
        "severity": "info",
        "notes": ["WS-Security configured via Security Material.",
                  "SOAP Axis adapter → migrate to standard SOAP."],
        "gaps": [],
    },
    "HTTP": {
        "cpi": "HTTP / HTTPS adapter",
        "severity": "info",
        "notes": ["Plain HTTP sender → HTTPS adapter with the iFlow endpoint."],
        "gaps": [],
    },
    "HTTPS": {
        "cpi": "HTTPS adapter",
        "severity": "info",
        "notes": ["Endpoint path becomes the iFlow's address; externalize it."],
        "gaps": [],
    },
    "MAIL": {
        "cpi": "Mail adapter",
        "severity": "info",
        "notes": ["SMTP/IMAP/POP3 supported; credentials via Security Material."],
        "gaps": [],
    },
    "REST": {
        "cpi": "HTTP adapter (REST style) or OData where applicable",
        "severity": "info",
        "notes": ["Map REST verbs/paths to the HTTP receiver configuration."],
        "gaps": [],
    },
    "AS2": {
        "cpi": "AS2 adapter (B2B; needs Trading Partner Management)",
        "severity": "warning",
        "notes": ["AS2 requires the B2B/TPM add-on and partner certificate setup."],
        "gaps": ["Confirm TPM entitlement on the tenant before promising AS2."],
    },
    "ODATA": {
        "cpi": "OData adapter (V2/V4)",
        "severity": "info",
        "notes": ["Provide the EDMX metadata; query options externalized."],
        "gaps": [],
    },
}


def advise_for_adapter(adapter: str, direction: str = "both") -> AdapterAdvisory:
    key = (adapter or "").strip().upper()
    kb = _ADVISORY_KB.get(key)
    if not kb:
        return AdapterAdvisory(
            pi_adapter=adapter or "Unknown", cpi_adapter="(manual mapping needed)",
            direction=direction, severity="caution",
            notes=[f"No standard advisory for adapter '{adapter}'. Verify the "
                   "CPI equivalent manually."])
    return AdapterAdvisory(
        pi_adapter=adapter, cpi_adapter=kb["cpi"], direction=direction,
        severity=kb["severity"], notes=list(kb["notes"]), gaps=list(kb["gaps"]))


def advise_for_interface(iface) -> list[AdapterAdvisory]:
    """Return advisories for both adapters of an interface."""
    out = []
    s = getattr(iface, "sender_adapter", "")
    r = getattr(iface, "receiver_adapter", "")
    if s:
        out.append(advise_for_adapter(s, "sender"))
    if r:
        out.append(advise_for_adapter(r, "receiver"))
    return out


def advise_all(interfaces: list) -> dict:
    """Aggregate advisories across interfaces, deduplicated by adapter+direction."""
    seen = {}
    for iface in interfaces:
        for adv in advise_for_interface(iface):
            k = (adv.pi_adapter.upper(), adv.direction)
            if k not in seen:
                seen[k] = adv
    advisories = list(seen.values())
    return {
        "advisories": advisories,
        "warnings": [a for a in advisories if a.severity == "warning"],
        "total": len(advisories),
    }
