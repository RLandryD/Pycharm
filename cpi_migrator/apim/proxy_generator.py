"""
apim/proxy_generator.py

Generates an SAP API Management proxy bundle (the API proxy definition XML
plus attached policies) from an APIProxy spec. Optionally derives a proxy
that fronts a migrated CPI iFlow, tying Program 1 output to Program 2.

A real APIM proxy bundle is a zip of several files (APIProxy descriptor,
proxy endpoints, target endpoints, policies). This generator produces the
core descriptor + a policy set + a manifest dict; the workbench can zip them.

Verified: structural (XML well-formedness + round-trip). NOT deployed —
descriptor layout follows documented SAP APIM proxy structure; confirm by
importing into the API Management tenant.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field

from apim.model import APIProxy, ProxyAuthType
from apim import policy_library


@dataclass
class GeneratedProxy:
    name: str
    descriptor_xml: str
    policies_xml: dict          # policy_name -> xml
    proxy_endpoint_xml: str
    target_endpoint_xml: str
    manifest: dict

    def all_files(self) -> dict:
        """Return filename -> content for the whole bundle."""
        files = {
            f"APIProxy/{self.name}.xml": self.descriptor_xml,
            f"APIProxy/proxies/default.xml": self.proxy_endpoint_xml,
            f"APIProxy/targets/default.xml": self.target_endpoint_xml,
        }
        for pname, pxml in self.policies_xml.items():
            files[f"APIProxy/policies/{pname}.xml"] = pxml
        return files


def _default_policies_for_auth(auth: ProxyAuthType) -> dict:
    """Pick a sensible default policy set based on the proxy's auth type."""
    policies = {}
    # Always add spike arrest + a quota as baseline protection
    policies["Spike-Arrest"] = policy_library.spike_arrest()
    policies["Quota-Default"] = policy_library.quota()

    if auth == ProxyAuthType.API_KEY:
        policies["Verify-API-Key"] = policy_library.verify_api_key()
    elif auth == ProxyAuthType.OAUTH2:
        policies["OAuth-Verify"] = policy_library.oauth_verify()

    # JSON threat protection is good hygiene for any JSON API
    policies["JSON-Threat"] = policy_library.json_threat_protection()
    return policies


def generate_proxy(proxy: APIProxy, extra_policies: dict | None = None) -> GeneratedProxy:
    """Generate a full proxy bundle from an APIProxy spec."""
    policies = _default_policies_for_auth(proxy.auth_type)
    if extra_policies:
        policies.update(extra_policies)
    # Any policy names explicitly listed on the proxy but not yet built:
    # leave a note in the manifest (don't silently drop).
    unbuilt = [p for p in proxy.policies if p not in policies]

    descriptor = f"""<?xml version="1.0" encoding="UTF-8"?>
<APIProxy revision="1" name="{html.escape(proxy.name)}">
    <DisplayName>{html.escape(proxy.name)}</DisplayName>
    <Description>{html.escape(proxy.description)}</Description>
    <BasePaths>{html.escape(proxy.base_path)}</BasePaths>
    <Policies>
{chr(10).join("        <Policy>" + html.escape(p) + "</Policy>" for p in policies)}
    </Policies>
    <ProxyEndpoints>
        <ProxyEndpoint>default</ProxyEndpoint>
    </ProxyEndpoints>
    <TargetEndpoints>
        <TargetEndpoint>default</TargetEndpoint>
    </TargetEndpoints>
</APIProxy>"""

    # Build the request/response policy attachment order
    request_steps = "".join(
        f"            <Step><Name>{html.escape(p)}</Name></Step>\n" for p in policies)

    proxy_endpoint = f"""<?xml version="1.0" encoding="UTF-8"?>
<ProxyEndpoint name="default">
    <PreFlow name="PreFlow">
        <Request>
{request_steps}        </Request>
        <Response/>
    </PreFlow>
    <HTTPProxyConnection>
        <BasePath>{html.escape(proxy.base_path)}</BasePath>
    </HTTPProxyConnection>
    <RouteRule name="default">
        <TargetEndpoint>default</TargetEndpoint>
    </RouteRule>
</ProxyEndpoint>"""

    target_endpoint = f"""<?xml version="1.0" encoding="UTF-8"?>
<TargetEndpoint name="default">
    <HTTPTargetConnection>
        <URL>{html.escape(proxy.target_url)}</URL>
    </HTTPTargetConnection>
</TargetEndpoint>"""

    manifest = {
        "name": proxy.name,
        "base_path": proxy.base_path,
        "target_url": proxy.target_url,
        "auth_type": proxy.auth_type.value,
        "policies": list(policies.keys()),
        "unbuilt_policies": unbuilt,
        "source_iflow": proxy.source_iflow,
    }

    return GeneratedProxy(
        name=proxy.name,
        descriptor_xml=descriptor,
        policies_xml=policies,
        proxy_endpoint_xml=proxy_endpoint,
        target_endpoint_xml=target_endpoint,
        manifest=manifest,
    )


def proxy_from_iflow(iflow_name: str, base_path: str, target_url: str,
                     auth_type: ProxyAuthType = ProxyAuthType.API_KEY) -> APIProxy:
    """Convenience: build an APIProxy that fronts a migrated CPI iFlow.

    This is the Program-1-to-Program-2 bridge: after migrating an interface
    to an iFlow, expose it as a managed API in one step.
    """
    return APIProxy(
        name=f"{iflow_name}_API".replace(" ", "_"),
        base_path=base_path,
        target_url=target_url,
        auth_type=auth_type,
        description=f"Managed API fronting migrated iFlow '{iflow_name}'",
        source_iflow=iflow_name,
    )
