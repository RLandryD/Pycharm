"""
apim/policy_library.py

Parameterized SAP API Management policy templates. Policies are the XML
snippets attached to an API proxy's flow to enforce behaviour: rate limiting,
key verification, payload checks, header manipulation, CORS, etc.

This is the "API policy library, parameterized" item from the Program 2 plan.
Each builder returns ready-to-attach policy XML with the supplied parameters
substituted. The names match SAP APIM's policy types (Quota, VerifyAPIKey,
SpikeArrest, etc.) so they import cleanly.

Verified: structural (XML well-formedness). NOT deployed — policy element
schemas follow documented SAP APIM conventions but confirm by importing into
your API Management tenant.
"""

from __future__ import annotations

import html


def verify_api_key(policy_name: str = "Verify-API-Key",
                   key_location: str = "request.header.apikey") -> str:
    """VerifyAPIKey policy — rejects calls without a valid key."""
    return f"""<VerifyAPIKey async="false" continueOnError="false" enabled="true" name="{html.escape(policy_name)}">
    <DisplayName>{html.escape(policy_name)}</DisplayName>
    <APIKey ref="{html.escape(key_location)}"/>
</VerifyAPIKey>"""


def quota(policy_name: str = "Quota-1",
          allow_count: int = 1000,
          interval: int = 1,
          time_unit: str = "hour") -> str:
    """Quota policy — caps total requests over a time window."""
    return f"""<Quota async="false" continueOnError="false" enabled="true" name="{html.escape(policy_name)}" type="calendar">
    <DisplayName>{html.escape(policy_name)}</DisplayName>
    <Allow count="{allow_count}"/>
    <Interval>{interval}</Interval>
    <TimeUnit>{html.escape(time_unit)}</TimeUnit>
    <StartTime>2026-01-01 00:00:00</StartTime>
</Quota>"""


def spike_arrest(policy_name: str = "Spike-Arrest-1", rate: str = "100ps") -> str:
    """SpikeArrest policy — smooths traffic bursts (e.g. 100ps = 100/sec)."""
    return f"""<SpikeArrest async="false" continueOnError="false" enabled="true" name="{html.escape(policy_name)}">
    <DisplayName>{html.escape(policy_name)}</DisplayName>
    <Rate>{html.escape(rate)}</Rate>
</SpikeArrest>"""


def cors(policy_name: str = "CORS-1",
         allow_origins: str = "*",
         allow_methods: str = "GET, POST, PUT, DELETE, OPTIONS") -> str:
    """CORS policy — sets cross-origin headers on responses."""
    return f"""<CORS async="false" continueOnError="false" enabled="true" name="{html.escape(policy_name)}">
    <DisplayName>{html.escape(policy_name)}</DisplayName>
    <AllowOrigins>{html.escape(allow_origins)}</AllowOrigins>
    <AllowMethods>{html.escape(allow_methods)}</AllowMethods>
    <AllowHeaders>origin, accept, content-type, authorization</AllowHeaders>
    <GeneratePreflightResponse>true</GeneratePreflightResponse>
</CORS>"""


def assign_message_set_header(policy_name: str, header_name: str, header_value: str) -> str:
    """AssignMessage policy — sets a header on the request/response."""
    return f"""<AssignMessage async="false" continueOnError="false" enabled="true" name="{html.escape(policy_name)}">
    <DisplayName>{html.escape(policy_name)}</DisplayName>
    <Set>
        <Headers>
            <Header name="{html.escape(header_name)}">{html.escape(header_value)}</Header>
        </Headers>
    </Set>
    <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
    <AssignTo createNew="false" transport="http" type="request"/>
</AssignMessage>"""


def json_threat_protection(policy_name: str = "JSON-Threat-1",
                           max_depth: int = 10,
                           max_string_length: int = 5000) -> str:
    """JSONThreatProtection — guards against malicious oversized JSON."""
    return f"""<JSONThreatProtection async="false" continueOnError="false" enabled="true" name="{html.escape(policy_name)}">
    <DisplayName>{html.escape(policy_name)}</DisplayName>
    <ContainerDepth>{max_depth}</ContainerDepth>
    <StringValueLength>{max_string_length}</StringValueLength>
</JSONThreatProtection>"""


def oauth_verify(policy_name: str = "OAuth-Verify-1") -> str:
    """OAuthV2 VerifyAccessToken — validates inbound OAuth2 bearer tokens."""
    return f"""<OAuthV2 async="false" continueOnError="false" enabled="true" name="{html.escape(policy_name)}">
    <DisplayName>{html.escape(policy_name)}</DisplayName>
    <Operation>VerifyAccessToken</Operation>
</OAuthV2>"""


# Registry so the workbench / generator can enumerate available policies
POLICY_BUILDERS = {
    "VerifyAPIKey":          verify_api_key,
    "Quota":                 quota,
    "SpikeArrest":           spike_arrest,
    "CORS":                  cors,
    "SetHeader":             assign_message_set_header,
    "JSONThreatProtection":  json_threat_protection,
    "OAuthVerify":           oauth_verify,
}


def list_policies() -> list[str]:
    return sorted(POLICY_BUILDERS.keys())
