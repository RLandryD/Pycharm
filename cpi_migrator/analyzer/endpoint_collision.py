"""
analyzer/endpoint_collision.py

Detects sender endpoint address collisions that would break deployment to a
single CPI tenant. CPI requires every HTTPS/SOAP/OData sender to expose a
unique address path; two iFlows on the same tenant cannot share /Customer/v1.

Inputs: a list of ChannelConfig objects (the same objects channel_parser.py
emits). Output: a list of CollisionFinding records, one per address that has
more than one sender claiming it.

Read-only. Does not modify channels or any other model. Pre-flight uses these
findings as additional checklist items.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Sender-adapter types that publish an HTTP-style path on the tenant
HTTP_LIKE_SENDERS = {"HTTPS", "HTTP", "SOAP", "REST", "ODATA", "AS2", "AS4"}


@dataclass
class CollisionFinding:
    """One conflicting endpoint path with the channels claiming it."""
    address_path: str
    channels: list[str] = field(default_factory=list)         # channel names
    adapter_types: list[str] = field(default_factory=list)
    recommendation: str = ""

    @property
    def severity(self) -> str:
        # Two same-adapter senders on identical path = deployment failure.
        # Two different-adapter senders on same path = ambiguous but may work.
        return "HIGH" if len(set(self.adapter_types)) == 1 else "MEDIUM"


def _normalise_path(address: str, path: str) -> str:
    """Combine address + path into a comparable endpoint key.

    PI/PO channels store the path in either field depending on adapter and
    export source, so we coalesce them and strip protocol/host noise.
    """
    raw = (path or address or "").strip()
    if not raw:
        return ""
    # strip protocol + host if a full URL leaked in
    raw = re.sub(r"^[a-zA-Z]+://[^/]+", "", raw)
    # collapse repeated slashes, strip trailing slash, lower-case
    raw = re.sub(r"/+", "/", raw).rstrip("/").lower()
    if not raw.startswith("/"):
        raw = "/" + raw
    return raw


def detect_collisions(channels: list) -> list[CollisionFinding]:
    """Scan channels and return one CollisionFinding per colliding path.

    Only sender-side HTTP-family channels are checked. A channel with no
    direction set is treated as a candidate (offline exports often omit it).
    """
    if not channels:
        return []

    by_path: dict[str, list] = defaultdict(list)
    for ch in channels:
        direction = (getattr(ch, "direction", "") or "").lower()
        if direction and direction != "sender":
            continue
        adapter = (getattr(ch, "adapter_type", "") or "").upper()
        if adapter not in HTTP_LIKE_SENDERS:
            continue
        key = _normalise_path(getattr(ch, "address", ""), getattr(ch, "path", ""))
        if not key or key == "/":
            # Empty/root paths are pre-flight problems in their own right but
            # are not collision findings — we surface those separately.
            continue
        by_path[key].append(ch)

    findings: list[CollisionFinding] = []
    for path, chs in by_path.items():
        if len(chs) < 2:
            continue
        names = [getattr(c, "channel_name", "") or getattr(c, "channel_id", "?") for c in chs]
        adapters = [(getattr(c, "adapter_type", "") or "").upper() for c in chs]
        findings.append(CollisionFinding(
            address_path=path,
            channels=names,
            adapter_types=adapters,
            recommendation=(
                f"Assign a unique sender address per iFlow. Suggested: "
                f"{path}/{names[0].lower().replace(' ', '_')} for the first, "
                f"{path}/{names[1].lower().replace(' ', '_')} for the second."
            ),
        ))

    findings.sort(key=lambda f: f.address_path)
    return findings


def collisions_to_preflight_items(findings: list[CollisionFinding]) -> list:
    """Convert findings to PreflightItem objects for the preflight checklist.

    Imported lazily so this module has no hard dependency on reporter/.
    """
    if not findings:
        return []
    from reporter.preflight_generator import PreflightItem

    items = []
    for f in findings:
        items.append(PreflightItem(
            category="Sender Endpoints",
            task=f"Resolve sender address collision on {f.address_path}",
            detail=(
                f"{len(f.channels)} sender channels claim {f.address_path}: "
                f"{', '.join(f.channels)}. CPI requires a unique address per "
                f"sender on a tenant. {f.recommendation}"
            ),
            responsible="Consultant",
            mandatory=(f.severity == "HIGH"),
            triggered_by=f"{len(f.channels)} sender channels on {f.address_path}",
        ))
    return items
