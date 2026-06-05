"""
fetcher/odata_suggester.py

For an RFC/BAPI-based PI/PO interface, suggest the S/4HANA OData (or SOAP) API
that replaces it on the target — because RFC/BAPI calls are not Clean-Core and
must be re-platformed onto released APIs.

Two layers:
  1. A curated static map of the most common BAPI/RFC function modules to their
     released S/4HANA API_* counterparts (works fully offline).
  2. Live Hub catalog search (via the existing HubCatalogClient) by BAPI name,
     returning the top matching packages/APIs.

Returns up to N ApiSuggestion records, static matches first (highest
confidence), then Hub search results.

This module only READS from HubCatalogClient; it does not modify it.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Curated BAPI / RFC → S/4HANA released API map
# Keys are matched case-insensitively against the function module name.
# Values: (api_name, api_path, note)
# ---------------------------------------------------------------------------

BAPI_TO_ODATA: dict[str, tuple[str, str, str]] = {
    "BAPI_SALESORDER_CREATEFROMDAT2": (
        "API_SALES_ORDER_SRV", "/sap/opu/odata/sap/API_SALES_ORDER_SRV",
        "Released Sales Order (A2X) OData V2 API."),
    "BAPI_SALESORDER_CHANGE": (
        "API_SALES_ORDER_SRV", "/sap/opu/odata/sap/API_SALES_ORDER_SRV",
        "Use PATCH on A_SalesOrder for changes."),
    "BAPI_PO_CREATE1": (
        "API_PURCHASEORDER_PROCESS_SRV", "/sap/opu/odata/sap/API_PURCHASEORDER_PROCESS_SRV",
        "Released Purchase Order OData API."),
    "BAPI_PO_CHANGE": (
        "API_PURCHASEORDER_PROCESS_SRV", "/sap/opu/odata/sap/API_PURCHASEORDER_PROCESS_SRV",
        "Released Purchase Order OData API."),
    "BAPI_OUTB_DELIVERY_CREATE_SLS": (
        "API_OUTBOUND_DELIVERY_SRV", "/sap/opu/odata/sap/API_OUTBOUND_DELIVERY_SRV",
        "Outbound Delivery API."),
    "BAPI_BILLINGDOC_CREATEMULTIPLE": (
        "API_BILLING_DOCUMENT_SRV", "/sap/opu/odata/sap/API_BILLING_DOCUMENT_SRV",
        "Billing Document API."),
    "BAPI_MATERIAL_SAVEDATA": (
        "API_PRODUCT_SRV", "/sap/opu/odata/sap/API_PRODUCT_SRV",
        "Product Master (replaces material BAPI)."),
    "BAPI_BUPA_CREATE_FROM_DATA": (
        "API_BUSINESS_PARTNER", "/sap/opu/odata/sap/API_BUSINESS_PARTNER",
        "Business Partner API (BP replaces customer/vendor)."),
    "BAPI_CUSTOMER_CREATEFROMDATA1": (
        "API_BUSINESS_PARTNER", "/sap/opu/odata/sap/API_BUSINESS_PARTNER",
        "Customer is modelled as Business Partner in S/4."),
    "BAPI_ACC_DOCUMENT_POST": (
        "API_JOURNALENTRY_SRV", "/sap/opu/odata/sap/API_JOURNALENTRY_SRV",
        "Journal Entry API for FI postings."),
    "BAPI_GOODSMVT_CREATE": (
        "API_MATERIAL_DOCUMENT_SRV", "/sap/opu/odata/sap/API_MATERIAL_DOCUMENT_SRV",
        "Material Document (goods movement) API."),
    "BAPI_EQUI_CREATE": (
        "API_EQUIPMENT", "/sap/opu/odata/sap/API_EQUIPMENT",
        "Equipment master API."),
    "BAPI_PRODORD_CREATE": (
        "API_PRODUCTION_ORDER_2_SRV", "/sap/opu/odata/sap/API_PRODUCTION_ORDER_2_SRV",
        "Production Order API."),
}

# Keyword fallbacks when no exact BAPI name match — maps a topic word in the
# function module / interface name to a likely API family.
KEYWORD_TO_API: dict[str, tuple[str, str, str]] = {
    "salesorder":  BAPI_TO_ODATA["BAPI_SALESORDER_CREATEFROMDAT2"],
    "sales":       BAPI_TO_ODATA["BAPI_SALESORDER_CREATEFROMDAT2"],
    "purchaseorder": BAPI_TO_ODATA["BAPI_PO_CREATE1"],
    "purchase":    BAPI_TO_ODATA["BAPI_PO_CREATE1"],
    "delivery":    BAPI_TO_ODATA["BAPI_OUTB_DELIVERY_CREATE_SLS"],
    "billing":     BAPI_TO_ODATA["BAPI_BILLINGDOC_CREATEMULTIPLE"],
    "invoice":     BAPI_TO_ODATA["BAPI_BILLINGDOC_CREATEMULTIPLE"],
    "material":    BAPI_TO_ODATA["BAPI_MATERIAL_SAVEDATA"],
    "product":     BAPI_TO_ODATA["BAPI_MATERIAL_SAVEDATA"],
    "customer":    BAPI_TO_ODATA["BAPI_CUSTOMER_CREATEFROMDATA1"],
    "vendor":      BAPI_TO_ODATA["BAPI_BUPA_CREATE_FROM_DATA"],
    "partner":     BAPI_TO_ODATA["BAPI_BUPA_CREATE_FROM_DATA"],
    "accounting":  BAPI_TO_ODATA["BAPI_ACC_DOCUMENT_POST"],
    "goods":       BAPI_TO_ODATA["BAPI_GOODSMVT_CREATE"],
    "equipment":   BAPI_TO_ODATA["BAPI_EQUI_CREATE"],
    "production":  BAPI_TO_ODATA["BAPI_PRODORD_CREATE"],
}


@dataclass
class ApiSuggestion:
    api_name: str
    api_path: str
    confidence: str            # "high" (exact BAPI), "medium" (keyword), "low" (hub search)
    source: str                # "static" | "hub"
    note: str = ""
    package_id: str = ""

    def __repr__(self) -> str:
        return f"ApiSuggestion({self.api_name}, {self.confidence}, {self.source})"


class ODataSuggester:
    """Suggest replacement OData APIs for RFC/BAPI interfaces."""

    def __init__(self, hub_client=None):
        # hub_client: optional HubCatalogClient for live search
        self.hub = hub_client

    @staticmethod
    def _norm(name: str) -> str:
        return re.sub(r"[^A-Z0-9_]", "", (name or "").upper())

    def suggest_for_bapi(self, function_module: str, top: int = 3) -> list[ApiSuggestion]:
        """Return suggestions for a single BAPI/RFC function module name."""
        suggestions: list[ApiSuggestion] = []
        fm = self._norm(function_module)
        if not fm:
            return suggestions

        # 1. Exact static match
        if fm in BAPI_TO_ODATA:
            api, path, note = BAPI_TO_ODATA[fm]
            suggestions.append(ApiSuggestion(api, path, "high", "static", note))

        # 2. Keyword static fallback
        if not suggestions:
            lowered = fm.lower()
            for kw, (api, path, note) in KEYWORD_TO_API.items():
                if kw in lowered:
                    suggestions.append(ApiSuggestion(api, path, "medium", "static",
                                                     f"Matched on '{kw}'. {note}"))
                    break

        # 3. Live Hub search to enrich / fill remaining slots
        if self.hub is not None and len(suggestions) < top:
            try:
                hits = self.hub.search_packages(query=function_module, top=top)
                seen = {s.api_name for s in suggestions}
                for pkg in hits:
                    if pkg.name in seen:
                        continue
                    suggestions.append(ApiSuggestion(
                        api_name=pkg.name, api_path=pkg.url, confidence="low",
                        source="hub", note=pkg.short_text, package_id=pkg.id))
                    if len(suggestions) >= top:
                        break
            except Exception as exc:  # pragma: no cover - network defensive
                logger.warning("Hub search for %s failed: %s", function_module, exc)

        return suggestions[:top]

    def suggest_for_interface(self, interface_name: str,
                              function_module: str = "",
                              top: int = 3) -> list[ApiSuggestion]:
        """Suggest APIs using the function module if known, else the interface name."""
        result = self.suggest_for_bapi(function_module, top=top) if function_module else []
        if not result:
            # try keyword match on the interface name itself
            lowered = self._norm(interface_name).lower()
            for kw, (api, path, note) in KEYWORD_TO_API.items():
                if kw in lowered:
                    result.append(ApiSuggestion(api, path, "medium", "static",
                                                f"Inferred from interface name ('{kw}'). {note}"))
                    break
        if not result and self.hub is not None:
            try:
                hits = self.hub.search_packages(query=interface_name, top=top)
                for pkg in hits[:top]:
                    result.append(ApiSuggestion(pkg.name, pkg.url, "low", "hub",
                                                pkg.short_text, pkg.id))
            except Exception as exc:  # pragma: no cover
                logger.warning("Hub search failed: %s", exc)
        return result[:top]
