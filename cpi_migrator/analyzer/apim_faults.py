"""
analyzer/apim_faults.py

Static catalog of SAP API Management fault codes with diagnosis and
remediation guidance. Used at runtime debugging time (and by the future
Program 2 publish/consume tooling) to turn an opaque fault code into a
human-actionable next step.

Standalone reference module — no other code in the project imports it yet.
It exists now so Program 2 (P2-A6) can adopt it on day one and so the same
catalog is available immediately for any consultant debugging an APIM proxy.

Source: SAP Help portal fault-code reference + field experience. Curated for
the codes that account for the large majority of real proxy failures.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class APIMFault:
    code: str
    category: str        # "OAuth" / "API Key" / "Rate Limit" / "Routing" / ...
    http_status: int     # typical HTTP status the consumer sees
    meaning: str
    likely_cause: str
    remediation: str


# Catalog. Keys are the exact fault-code strings APIM emits.
CATALOG: dict[str, APIMFault] = {
    "steps.oauth.v2.FailedToResolveAPIKey": APIMFault(
        code="steps.oauth.v2.FailedToResolveAPIKey",
        category="OAuth",
        http_status=401,
        meaning="The VerifyAPIKey policy could not locate the API key in the request.",
        likely_cause="Key reference points at the wrong variable (e.g. request.queryparam.apikey "
                     "when the client sends a header), or the policy is attached before the source "
                     "variable is populated.",
        remediation="Confirm where the consumer sends the key (header vs query param) and update "
                    "the policy's APIKey ref. Recommend header-based delivery for production.",
    ),
    "oauth.v2.InvalidApiKey": APIMFault(
        code="oauth.v2.InvalidApiKey",
        category="OAuth",
        http_status=401,
        meaning="The API key was found but is not valid for this product.",
        likely_cause="Key revoked, expired, or belongs to an application that has not been "
                     "subscribed to the product that exposes this resource.",
        remediation="Check the application's subscriptions in the Developer Hub. Re-issue the "
                    "key or subscribe the app to the correct product.",
    ),
    "oauth.v2.InvalidAccessToken": APIMFault(
        code="oauth.v2.InvalidAccessToken",
        category="OAuth",
        http_status=401,
        meaning="The bearer token presented was not accepted by VerifyAccessToken.",
        likely_cause="Token expired, signed with a different issuer key, or issued for a "
                     "different audience.",
        remediation="Re-request the token from the OAuth provider configured in the policy. "
                    "Verify clock skew between APIM and the IdP.",
    ),
    "policies.ratelimit.SpikeArrestViolation": APIMFault(
        code="policies.ratelimit.SpikeArrestViolation",
        category="Rate Limit",
        http_status=429,
        meaning="Spike Arrest policy throttled the request — instantaneous rate exceeded.",
        likely_cause="A burst of requests arrived faster than the configured per-second rate. "
                     "Spike Arrest smooths bursts, it does not count quota.",
        remediation="Confirm the Spike Arrest rate matches realistic consumer behaviour. "
                    "If clients legitimately burst, raise the rate or switch to a Quota policy "
                    "with a longer interval.",
    ),
    "policies.ratelimit.QuotaViolation": APIMFault(
        code="policies.ratelimit.QuotaViolation",
        category="Rate Limit",
        http_status=429,
        meaning="Quota policy rejected the request — period allowance exhausted.",
        likely_cause="App has consumed its allotted calls for the current quota window.",
        remediation="Wait for the window to reset, or assign the app a product with a larger "
                    "quota. Surface quota headers (X-RateLimit-Remaining) to consumers.",
    ),
    "steps.routerules.NoRoutesMatched": APIMFault(
        code="steps.routerules.NoRoutesMatched",
        category="Routing",
        http_status=500,
        meaning="No Route Rule matched the request and no default was set.",
        likely_cause="A new HTTP verb or path arrived that the proxy's route rules don't cover. "
                     "Commonly an OPTIONS preflight when no CORS rule is configured.",
        remediation="Add a default route to NONE for OPTIONS (CORS preflight) and a fallback "
                    "rule that targets the real backend. Pattern: condition request.verb == "
                    "\"OPTIONS\" → Target Endpoint = NONE.",
    ),
    "messaging.adaptors.http.flow.ServiceUnavailable": APIMFault(
        code="messaging.adaptors.http.flow.ServiceUnavailable",
        category="Backend",
        http_status=503,
        meaning="The Target Endpoint could not reach the backend.",
        likely_cause="x-targetEndpoint URL is wrong, the CPI iFlow is undeployed, or the "
                     "backend is offline / firewalled.",
        remediation="Verify x-targetEndpoint matches the deployed CPI runtime URL. Confirm "
                    "the iFlow is Started. Re-test the backend directly.",
    ),
    "policies.security.threatprotection.ExecutionFailed": APIMFault(
        code="policies.security.threatprotection.ExecutionFailed",
        category="Security",
        http_status=400,
        meaning="A threat-protection policy (JSON/XML/Regex) rejected the payload.",
        likely_cause="Payload exceeded depth/length thresholds, or matched a blocked pattern.",
        remediation="Inspect the rejected payload against the policy thresholds. Tune the "
                    "policy or split the request if the payload is legitimately large.",
    ),
}


def lookup(code: str) -> APIMFault | None:
    """Return the catalog entry for a fault code, or None if unknown."""
    if not code:
        return None
    return CATALOG.get(code.strip())


def by_category(category: str) -> list[APIMFault]:
    """All catalog entries in a category (case-insensitive)."""
    target = (category or "").strip().lower()
    return [f for f in CATALOG.values() if f.category.lower() == target]


def search(query: str) -> list[APIMFault]:
    """Substring match across code, meaning, and likely cause."""
    q = (query or "").strip().lower()
    if not q:
        return []
    out = []
    for f in CATALOG.values():
        if q in f.code.lower() or q in f.meaning.lower() or q in f.likely_cause.lower():
            out.append(f)
    return out


def all_codes() -> list[str]:
    return sorted(CATALOG.keys())
