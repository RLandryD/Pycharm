"""
analyzer/domains.py

Canonical SAP ISA-M deployment-domain vocabulary plus a derivation function
from a (source_location, target_location) pair. The existing questionnaire
captures location at the questionnaire level (Q4), but per-interface domain
classification was never centralised — recommendation_engine, reporters, and
the future Interface Request artifact each invented their own labels.

This module fixes that by exposing one vocabulary and one derivation function.
It does NOT modify the recommendation engine or change any existing scoring;
it only provides a vocabulary that future features (P1-1 ISA-M artifact, the
TDD writer, the proposal generator) can adopt.

Domains follow SAP's published ISA-M nomenclature.
"""
from __future__ import annotations

from dataclasses import dataclass

# Canonical ISA-M domain values.
CLOUD_TO_CLOUD       = "Cloud2Cloud"
CLOUD_TO_ON_PREMISE  = "Cloud2OnPremise"
ON_PREMISE_TO_ON_PREMISE = "OnPremise2OnPremise"
ON_PREMISE_TO_CLOUD  = "OnPremise2Cloud"
EDGE_LOCAL           = "EdgeLocal"          # processing stays at the edge cell
HYBRID               = "Hybrid"             # multi-leg flows spanning ≥2 domains

ALL_DOMAINS = (
    CLOUD_TO_CLOUD, CLOUD_TO_ON_PREMISE,
    ON_PREMISE_TO_ON_PREMISE, ON_PREMISE_TO_CLOUD,
    EDGE_LOCAL, HYBRID,
)

# Map common location tokens (as used in the questionnaire Q4 + free-text
# system names) to a coarse {cloud, on_premise, edge} bucket.
_LOCATION_BUCKETS = {
    "cloud":       "cloud",
    "saas":        "cloud",
    "btp":         "cloud",
    "s4hana_cloud":"cloud",
    "ariba":       "cloud",
    "successfactors":"cloud",
    "aws":         "cloud",
    "azure":       "cloud",
    "gcp":         "cloud",

    "onpremise":   "on_premise",
    "on_premise":  "on_premise",
    "on-prem":     "on_premise",
    "ecc":         "on_premise",
    "s4hana_op":   "on_premise",
    "s4_op":       "on_premise",
    "pi":          "on_premise",
    "po":          "on_premise",

    "edge":        "edge",
    "eic":         "edge",
    "local":       "edge",
}


@dataclass
class DomainClassification:
    domain: str
    source_bucket: str          # "cloud" | "on_premise" | "edge" | "unknown"
    target_bucket: str
    confidence: str             # "high" | "medium" | "low"


def _bucket(location: str) -> str:
    if not location:
        return "unknown"
    key = location.strip().lower().replace(" ", "_").replace("/", "_")
    if key in _LOCATION_BUCKETS:
        return _LOCATION_BUCKETS[key]
    for token, bucket in _LOCATION_BUCKETS.items():
        if token in key:
            return bucket
    return "unknown"


def derive_domain(source_location: str, target_location: str) -> DomainClassification:
    """Classify a source→target pair into one of the canonical ISA-M domains.

    Inputs are free-text location labels (system names, target IDs from
    destinations.registry, or questionnaire answers). Unknown buckets default
    to cloud-side with low confidence — the consultant should review.
    """
    src = _bucket(source_location)
    tgt = _bucket(target_location)
    confidence = "high" if "unknown" not in (src, tgt) else "low"

    if "edge" in (src, tgt) and src == tgt:
        domain = EDGE_LOCAL
    elif src == "cloud" and tgt == "cloud":
        domain = CLOUD_TO_CLOUD
    elif src == "on_premise" and tgt == "on_premise":
        domain = ON_PREMISE_TO_ON_PREMISE
    elif src == "cloud" and tgt == "on_premise":
        domain = CLOUD_TO_ON_PREMISE
    elif src == "on_premise" and tgt == "cloud":
        domain = ON_PREMISE_TO_CLOUD
    elif "edge" in (src, tgt):
        domain = HYBRID
        confidence = "medium" if confidence == "high" else confidence
    elif "unknown" in (src, tgt):
        # Conservative default: assume cloud-to-cloud (the most common today)
        # but mark low confidence so the UI shows it as advisory only.
        domain = CLOUD_TO_CLOUD
    else:
        domain = HYBRID

    return DomainClassification(domain=domain, source_bucket=src,
                                target_bucket=tgt, confidence=confidence)


def audit_domain_coverage(values: list[str]) -> dict:
    """Compare a caller-supplied set of domain labels to the canonical set.

    Used by tests and by any future migration of the recommendation engine
    onto this vocabulary. Returns a report dict with missing/unknown lists.
    """
    canonical = set(ALL_DOMAINS)
    supplied = set(values or [])
    return {
        "canonical": sorted(canonical),
        "supplied":  sorted(supplied),
        "missing":   sorted(canonical - supplied),   # in canonical, not supplied
        "unknown":   sorted(supplied - canonical),   # in supplied, not canonical
        "covered":   canonical.issubset(supplied),
    }
