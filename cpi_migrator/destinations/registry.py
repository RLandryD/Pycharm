"""
destinations/registry.py

Defines every supported migration target. Each DestinationTarget describes
where to fetch live metadata from (SAP Business Accelerator Hub and/or
other public APIs), what adapters it supports, and how to map PI/PO
sender adapters to the correct CPI receiver adapter for that target.

Adding a new destination = add one entry to DESTINATION_REGISTRY.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class HubSource:
    """A data source on api.sap.com / SAP Business Accelerator Hub."""
    # Package ID on the Hub (used to build the fetch URL)
    package_id: str
    # Human label shown in reports
    label: str
    # REST endpoint to fetch package content list
    # {package_id} is interpolated at runtime
    api_path: str = "/content/packages/{package_id}/artifacts"
    # Optional: fetch an OpenAPI / EDMX catalog for this target
    catalog_url: Optional[str] = None


@dataclass
class DestinationTarget:
    id: str                          # machine key, e.g. "s4hana_cloud"
    label: str                       # human label, e.g. "SAP S/4HANA Cloud"
    variant: str                     # "cloud" | "onpremise" | "saas" | "paas"
    description: str
    hub_sources: list[HubSource]     # ≥1 Hub packages that cover this target
    # Supported CPI receiver adapters for this target (in priority order)
    supported_adapters: list[str]
    # Map: PI/PO sender adapter → recommended CPI receiver adapter for this target
    adapter_mapping: dict[str, str]
    # Extra notes injected into iFlow stubs and reports
    migration_hints: list[str] = field(default_factory=list)
    # Cache TTL in seconds (default 24 h)
    cache_ttl_seconds: int = 86_400


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

SAP_HUB_BASE = "https://api.sap.com/api"
SAP_HUB_CONTENT = "https://api.sap.com/odata/1.0/catalog.svc"
S4_ODATA_CATALOG = "https://api.sap.com/odata/1.0/catalog.svc/Packages?$filter=packageId+eq+'{pid}'&$format=json"

DESTINATION_REGISTRY: dict[str, DestinationTarget] = {

    # ── S/4HANA Cloud (Public Edition) ───────────────────────────────
    "s4hana_cloud": DestinationTarget(
        id="s4hana_cloud",
        label="SAP S/4HANA Cloud (Public Edition)",
        variant="cloud",
        description="SAP-managed SaaS ERP. Integrations use standard OData/SOAP APIs "
                    "published on the SAP Business Accelerator Hub.",
        hub_sources=[
            HubSource(
                package_id="SAPS4HANACloud",
                label="S/4HANA Cloud APIs",
                catalog_url="https://api.sap.com/odata/1.0/catalog.svc/Packages"
                            "?$filter=packageId+eq+'SAPS4HANACloud'&$format=json",
            ),
            HubSource(
                package_id="SAPIntegrationSuiteS4HANACloud",
                label="Integration Suite — S/4HANA Cloud content",
            ),
        ],
        supported_adapters=["OData", "SOAP", "HTTPS", "IDoc", "AS2"],
        adapter_mapping={
            "RFC":   "OData",       # BAPI → OData equivalent
            "IDOC":  "IDoc",
            "IDoc":  "IDoc",
            "SOAP":  "SOAP",
            "FILE":  "HTTPS",       # no file system; use HTTP inbound
            "File":  "HTTPS",
            "FTP":   "HTTPS",
            "SFTP":  "HTTPS",
            "JDBC":  "OData",       # DB queries → OData API calls
            "JMS":   "AMQP",        # via Advanced Event Mesh
            "REST":  "HTTPS",
            "HTTPS": "HTTPS",
            "HTTP":  "HTTPS",
        },
        migration_hints=[
            "S/4HANA Cloud enforces Clean Core — all integrations must use published APIs (Tier-1).",
            "Custom RFC/BAPI calls are not supported; map to equivalent OData V4 API on the Hub.",
            "IDoc over HTTPS (IDoc adapter) is supported; configure communication arrangement in S/4.",
            "Check SAP Note 3085212 for the current supported API list before scaffolding.",
        ],
    ),

    # ── S/4HANA On-Premise ────────────────────────────────────────────
    "s4hana_op": DestinationTarget(
        id="s4hana_op",
        label="SAP S/4HANA On-Premise",
        variant="onpremise",
        description="Customer-managed S/4HANA. Supports RFC, IDoc, SOAP, and OData "
                    "adapters. More flexibility than Cloud edition.",
        hub_sources=[
            HubSource(
                package_id="SAPS4HANAOnPremise",
                label="S/4HANA On-Premise APIs",
                catalog_url="https://api.sap.com/odata/1.0/catalog.svc/Packages"
                            "?$filter=packageId+eq+'SAPS4HANAOnPremise'&$format=json",
            ),
        ],
        supported_adapters=["RFC", "IDoc", "SOAP", "OData", "HTTPS", "JDBC", "JMS"],
        adapter_mapping={
            "RFC":   "RFC",
            "IDOC":  "IDoc",
            "IDoc":  "IDoc",
            "SOAP":  "SOAP",
            "FILE":  "SFTP",
            "File":  "SFTP",
            "FTP":   "FTP",
            "SFTP":  "SFTP",
            "JDBC":  "JDBC",
            "JMS":   "JMS",
            "REST":  "HTTPS",
            "HTTPS": "HTTPS",
            "HTTP":  "HTTP",
        },
        migration_hints=[
            "RFC adapter is supported — but consider OData for new interfaces (future-proof).",
            "Ensure CPI Cloud Connector is configured for on-premise connectivity.",
            "IDoc: configure partner profiles in SM59 / WE20 on the S/4 side.",
            "JDBC requires DB driver upload to CPI keystore and firewall rule.",
        ],
    ),

    # ── SAP Ariba ─────────────────────────────────────────────────────
    "ariba": DestinationTarget(
        id="ariba",
        label="SAP Ariba",
        variant="saas",
        description="SAP Ariba procurement and supply chain SaaS. Integrations use "
                    "Ariba Network APIs and cXML/SOAP.",
        hub_sources=[
            HubSource(
                package_id="SAPAriba",
                label="SAP Ariba APIs",
                catalog_url="https://api.sap.com/odata/1.0/catalog.svc/Packages"
                            "?$filter=packageId+eq+'SAPAriba'&$format=json",
            ),
            HubSource(
                package_id="SAPAribaNetworkIntegration",
                label="Ariba Network Integration content",
            ),
        ],
        supported_adapters=["HTTPS", "SOAP", "AS2", "OData"],
        adapter_mapping={
            "RFC":   "HTTPS",
            "IDOC":  "HTTPS",
            "IDoc":  "HTTPS",
            "SOAP":  "SOAP",
            "FILE":  "HTTPS",
            "File":  "HTTPS",
            "FTP":   "HTTPS",
            "SFTP":  "HTTPS",
            "REST":  "HTTPS",
            "HTTPS": "HTTPS",
            "HTTP":  "HTTPS",
            "JDBC":  "HTTPS",
        },
        migration_hints=[
            "Ariba uses OAuth 2.0 (client credentials) — store credentials in CPI secure parameters.",
            "cXML documents are exchanged over HTTPS; use HTTPS adapter with cXML content modifier.",
            "Ariba Network integration packages are available on the Hub — prefer standard content.",
            "Check the Ariba API Explorer at developer.ariba.com for current endpoint schemas.",
        ],
    ),

    # ── SAP SuccessFactors ────────────────────────────────────────────
    "successfactors": DestinationTarget(
        id="successfactors",
        label="SAP SuccessFactors (HCM)",
        variant="saas",
        description="SAP SuccessFactors HCM suite. Integrations use the SF OData API "
                    "and the dedicated SuccessFactors adapter in CPI.",
        hub_sources=[
            HubSource(
                package_id="SAPSuccessFactors",
                label="SuccessFactors APIs",
                catalog_url="https://api.sap.com/odata/1.0/catalog.svc/Packages"
                            "?$filter=packageId+eq+'SAPSuccessFactors'&$format=json",
            ),
            HubSource(
                package_id="SAPSuccessFactorsEmployeeCentral",
                label="SF Employee Central integration content",
            ),
        ],
        supported_adapters=["SuccessFactors", "OData", "HTTPS", "SOAP"],
        adapter_mapping={
            "RFC":   "SuccessFactors",
            "IDOC":  "SuccessFactors",
            "IDoc":  "SuccessFactors",
            "SOAP":  "SOAP",
            "FILE":  "HTTPS",
            "File":  "HTTPS",
            "FTP":   "HTTPS",
            "SFTP":  "HTTPS",
            "REST":  "HTTPS",
            "HTTPS": "HTTPS",
            "HTTP":  "HTTPS",
            "JDBC":  "SuccessFactors",
        },
        migration_hints=[
            "Use the dedicated SuccessFactors adapter (not generic OData) for compound employee APIs.",
            "SF API versions change with each release cycle — validate against current SFAPI version.",
            "Delta loading via SF compound employee API requires upsert handling in CPI.",
            "Check the Integration Center in SF for existing pre-built integrations before custom-building.",
        ],
    ),

    # ── SAP BTP Services ──────────────────────────────────────────────
    "btp": DestinationTarget(
        id="btp",
        label="SAP BTP (Business Technology Platform)",
        variant="paas",
        description="SAP BTP platform services: workflow, event mesh, data intelligence, "
                    "HANA Cloud, and extension apps.",
        hub_sources=[
            HubSource(
                package_id="SAPBTPIntegration",
                label="SAP BTP Integration content",
                catalog_url="https://api.sap.com/odata/1.0/catalog.svc/Packages"
                            "?$filter=packageId+eq+'SAPBTPIntegration'&$format=json",
            ),
            HubSource(
                package_id="SAPAdvancedEventMesh",
                label="Advanced Event Mesh content",
            ),
        ],
        supported_adapters=["HTTPS", "OData", "AMQP", "MQTT", "JMS"],
        adapter_mapping={
            "RFC":   "HTTPS",
            "IDOC":  "AMQP",
            "IDoc":  "AMQP",
            "SOAP":  "HTTPS",
            "FILE":  "HTTPS",
            "File":  "HTTPS",
            "FTP":   "HTTPS",
            "SFTP":  "HTTPS",
            "JMS":   "AMQP",
            "REST":  "HTTPS",
            "HTTPS": "HTTPS",
            "HTTP":  "HTTPS",
            "JDBC":  "HTTPS",
        },
        migration_hints=[
            "BTP extension apps expose REST/OData APIs — use HTTPS adapter with OAuth2 client credentials.",
            "For event-driven patterns, use Advanced Event Mesh (AMQP/MQTT adapter).",
            "SAP Workflow Service: trigger via REST API from CPI using HTTPS adapter.",
            "HANA Cloud: use JDBC adapter or expose data via CAP OData service.",
        ],
    ),

    # ── SAP Fieldglass ────────────────────────────────────────────────
    "fieldglass": DestinationTarget(
        id="fieldglass",
        label="SAP Fieldglass",
        variant="saas",
        description="SAP Fieldglass vendor management system. REST/SOAP APIs for "
                    "contingent workforce and services procurement.",
        hub_sources=[
            HubSource(
                package_id="SAPFieldglass",
                label="SAP Fieldglass APIs",
            ),
        ],
        supported_adapters=["HTTPS", "SOAP"],
        adapter_mapping={
            "RFC":   "HTTPS",
            "IDOC":  "HTTPS",
            "IDoc":  "HTTPS",
            "SOAP":  "SOAP",
            "FILE":  "HTTPS",
            "File":  "HTTPS",
            "FTP":   "HTTPS",
            "SFTP":  "HTTPS",
            "REST":  "HTTPS",
            "HTTPS": "HTTPS",
            "HTTP":  "HTTPS",
        },
        migration_hints=[
            "Fieldglass uses API key authentication — store in CPI secure parameters.",
            "Standard integration content available on Hub for common P2P scenarios.",
        ],
    ),

    # ── SAP Concur ────────────────────────────────────────────────────
    "concur": DestinationTarget(
        id="concur",
        label="SAP Concur (T&E)",
        variant="saas",
        description="SAP Concur travel and expense management. REST APIs for expense "
                    "reports, travel requests, and invoice processing.",
        hub_sources=[
            HubSource(
                package_id="SAPConcur",
                label="SAP Concur APIs",
            ),
        ],
        supported_adapters=["HTTPS", "OData"],
        adapter_mapping={
            "RFC":   "HTTPS",
            "IDOC":  "HTTPS",
            "IDoc":  "HTTPS",
            "SOAP":  "HTTPS",
            "FILE":  "HTTPS",
            "File":  "HTTPS",
            "FTP":   "HTTPS",
            "SFTP":  "HTTPS",
            "REST":  "HTTPS",
            "HTTPS": "HTTPS",
            "HTTP":  "HTTPS",
        },
        migration_hints=[
            "Concur uses OAuth 2.0 — geolocation-aware token URLs differ by data centre.",
            "Use SAP pre-built Concur packages from Hub where available.",
        ],
    ),
}


def get_target(target_id: str) -> DestinationTarget:
    """Return a DestinationTarget by id, raising KeyError if not found."""
    try:
        return DESTINATION_REGISTRY[target_id]
    except KeyError:
        available = ", ".join(DESTINATION_REGISTRY.keys())
        raise KeyError(f"Unknown destination '{target_id}'. Available: {available}")


def list_targets() -> list[DestinationTarget]:
    return list(DESTINATION_REGISTRY.values())


# ── AWS ───────────────────────────────────────────────────────────────────────
DESTINATION_REGISTRY["aws_s3"] = DestinationTarget(
    id="aws_s3", label="Amazon S3", variant="cloud",
    description="AWS S3 object storage. Use HTTPS adapter with AWS SigV4 auth.",
    hub_sources=[HubSource(package_id="AWSIntegration", label="AWS S3 Integration")],
    supported_adapters=["HTTPS", "HTTP"],
    adapter_mapping={k: "HTTPS" for k in ["RFC","IDoc","SOAP","FILE","File","FTP",
                     "SFTP","JDBC","JMS","REST","HTTPS","HTTP","OData","AS2","AS4"]},
    migration_hints=[
        "Use HTTPS adapter with AWS SigV4 authentication (access key + secret).",
        "Store AWS credentials in CPI secure parameters.",
        "S3 REST API: PUT /bucket/key for upload, GET /bucket/key for download.",
        "Use Content Modifier to set x-amz-* headers required by S3.",
    ],
)

DESTINATION_REGISTRY["aws_sqs"] = DestinationTarget(
    id="aws_sqs", label="Amazon SQS", variant="cloud",
    description="AWS SQS message queue. Use HTTPS adapter with SigV4.",
    hub_sources=[HubSource(package_id="AWSIntegration", label="AWS SQS Integration")],
    supported_adapters=["HTTPS", "AMQP"],
    adapter_mapping={k: "HTTPS" for k in ["RFC","IDoc","SOAP","FILE","File","FTP",
                     "SFTP","JDBC","JMS","REST","HTTPS","HTTP","OData"]},
    migration_hints=[
        "SQS REST API uses query-string auth or SigV4 — store in CPI secure params.",
        "Use HTTPS adapter POST to SQS endpoint with Action=SendMessage.",
        "For high-volume: consider Advanced Event Mesh as intermediary.",
    ],
)

# ── Azure ─────────────────────────────────────────────────────────────────────
DESTINATION_REGISTRY["azure_servicebus"] = DestinationTarget(
    id="azure_servicebus", label="Azure Service Bus", variant="cloud",
    description="Azure Service Bus messaging. HTTPS adapter with SAS token or Entra OAuth.",
    hub_sources=[HubSource(package_id="AzureIntegration", label="Azure Service Bus Integration")],
    supported_adapters=["HTTPS", "AMQP"],
    adapter_mapping={k: "HTTPS" for k in ["RFC","IDoc","SOAP","FILE","File","FTP",
                     "SFTP","JDBC","JMS","REST","HTTPS","HTTP","OData","AS2","AS4"]},
    migration_hints=[
        "Use SAS token in Authorization header: SharedAccessSignature sr=...",
        "Or use Entra OAuth2 (client credentials) — preferred for production.",
        "REST endpoint: https://{namespace}.servicebus.windows.net/{queue}/messages",
        "Store SAS key or client secret in CPI secure parameters.",
    ],
)

DESTINATION_REGISTRY["azure_blob"] = DestinationTarget(
    id="azure_blob", label="Azure Blob Storage", variant="cloud",
    description="Azure Blob Storage. HTTPS adapter with SAS or Entra OAuth.",
    hub_sources=[HubSource(package_id="AzureIntegration", label="Azure Blob Integration")],
    supported_adapters=["HTTPS"],
    adapter_mapping={k: "HTTPS" for k in ["RFC","IDoc","SOAP","FILE","File","FTP",
                     "SFTP","JDBC","REST","HTTPS","HTTP","OData"]},
    migration_hints=[
        "REST: PUT https://{account}.blob.core.windows.net/{container}/{blob}",
        "Use Entra OAuth2 or SAS token for authentication.",
        "Set Content-Type and x-ms-blob-type: BlockBlob headers in Content Modifier.",
    ],
)

# ── GCP ───────────────────────────────────────────────────────────────────────
DESTINATION_REGISTRY["gcp_pubsub"] = DestinationTarget(
    id="gcp_pubsub", label="Google Cloud Pub/Sub", variant="cloud",
    description="GCP Pub/Sub messaging. HTTPS adapter with service account JWT.",
    hub_sources=[HubSource(package_id="GCPIntegration", label="GCP Pub/Sub Integration")],
    supported_adapters=["HTTPS"],
    adapter_mapping={k: "HTTPS" for k in ["RFC","IDoc","SOAP","FILE","File","FTP",
                     "SFTP","JDBC","JMS","REST","HTTPS","HTTP","OData"]},
    migration_hints=[
        "Use OAuth2 with GCP service account — generate JWT from private key.",
        "Token URL: https://oauth2.googleapis.com/token",
        "Pub/Sub REST: POST https://pubsub.googleapis.com/v1/projects/{project}/topics/{topic}:publish",
        "Store service account JSON key in CPI secure parameters.",
    ],
)

DESTINATION_REGISTRY["gcp_gcs"] = DestinationTarget(
    id="gcp_gcs", label="Google Cloud Storage", variant="cloud",
    description="GCP Cloud Storage (GCS). HTTPS adapter with service account OAuth2.",
    hub_sources=[HubSource(package_id="GCPIntegration", label="GCP GCS Integration")],
    supported_adapters=["HTTPS"],
    adapter_mapping={k: "HTTPS" for k in ["RFC","IDoc","SOAP","FILE","File","FTP",
                     "SFTP","JDBC","REST","HTTPS","HTTP","OData"]},
    migration_hints=[
        "Upload: POST https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o",
        "Use OAuth2 service account credentials stored in CPI secure params.",
        "Set Content-Type header matching the file being uploaded.",
    ],
)
