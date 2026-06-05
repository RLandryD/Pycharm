"""
fetcher/groovy_library.py

Catalog of community-verified Groovy scripts for SAP CPI.
Organized by use case. Scripts sourced from:
  - SAP Community blogs
  - SAP GitHub apibusinesshub-integration-recipes
  - SAP Help Portal Script Collections

Usage:
  lib = GroovyLibrary()
  scripts = lib.search("IDoc XML transform")
  scripts = lib.get_by_adapter("IDoc", "SOAP")
  scripts = lib.get_by_category("value_mapping")
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class GroovyTemplate:
    id: str
    title: str
    category: str           # xml_transform / json / idoc / error / header / value_map / utility
    adapters: list[str]     # relevant adapter types
    description: str
    code: str
    source: str = "SAP Community"
    tags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Script catalog
# ---------------------------------------------------------------------------

GROOVY_CATALOG: list[GroovyTemplate] = [

    # ── XML Transform ────────────────────────────────────────────────
    GroovyTemplate(
        id="xml_basic_transform",
        title="Basic XML transformation (XmlSlurper + MarkupBuilder)",
        category="xml_transform",
        adapters=["SOAP","HTTPS","HTTP","OData"],
        description="Parse source XML and build target XML. Standard pattern for most field mapping scenarios.",
        tags=["xml","transform","mapping","xmlslurper","markupbuilder"],
        source="SAP Community",
        code='''import com.sap.gateway.ip.core.customdev.util.Message
import groovy.xml.XmlSlurper
import groovy.xml.StreamingMarkupBuilder
import groovy.xml.XmlUtil

def Message processData(Message message) {
    def body     = message.getBody(String.class)
    def sourceXml = new XmlSlurper().parseText(body)

    def builder = new StreamingMarkupBuilder()
    builder.encoding = "UTF-8"

    def targetXml = builder.bind {
        mkp.xmlDeclaration()
        // TODO: Update namespace and root element
        "TargetNamespace"."RootElement" {
            // TODO: Map fields — example:
            "TargetField1"( sourceXml.SourceField1.text() )
            "TargetField2"( sourceXml.SourceField2.text() )
        }
    }

    message.setBody(XmlUtil.serialize(targetXml))
    return message
}''',
    ),

    GroovyTemplate(
        id="xml_idoc_to_soap",
        title="IDoc XML to SOAP/XML transformation",
        category="xml_transform",
        adapters=["IDoc","SOAP"],
        description="Extract IDoc segment fields and map to SOAP/XML target structure.",
        tags=["idoc","soap","transform","e1","segment"],
        source="SAP Community",
        code='''import com.sap.gateway.ip.core.customdev.util.Message
import groovy.xml.XmlSlurper
import groovy.xml.StreamingMarkupBuilder
import groovy.xml.XmlUtil

def Message processData(Message message) {
    def body     = message.getBody(String.class)
    def idoc     = new XmlSlurper().parseText(body)

    // Extract control record fields
    def docNum   = idoc.IDOC.EDI_DC40.DOCNUM.text()
    def mesType  = idoc.IDOC.EDI_DC40.MESTYP.text()
    def sndPrn   = idoc.IDOC.EDI_DC40.SNDPRN.text()

    // Extract E1 segment fields — TODO: Update segment names
    def headerSeg = idoc.IDOC.E1_HEADER   // replace with actual segment
    def itemsSeg  = idoc.IDOC.E1_ITEM     // replace with actual segment

    def builder = new StreamingMarkupBuilder()
    def result = builder.bind {
        mkp.xmlDeclaration()
        // TODO: Update namespace
        "http://company.com/target"."Request" {
            "Header" {
                "DocumentNumber"( docNum )
                "MessageType"( mesType )
                "SenderSystem"( sndPrn )
            }
            "Items" {
                itemsSeg.each { item ->
                    "Item" {
                        // TODO: Map item fields
                        "Field1"( item.FIELD1.text() )
                    }
                }
            }
        }
    }

    message.setBody(groovy.xml.XmlUtil.serialize(result))
    return message
}''',
    ),

    GroovyTemplate(
        id="xml_change_root_node",
        title="Change XML root node name",
        category="xml_transform",
        adapters=["SOAP","HTTPS","OData"],
        description="Rename the root element of an XML payload while preserving all children.",
        tags=["xml","root","rename","namespace"],
        source="SAP Community",
        code='''import com.sap.gateway.ip.core.customdev.util.Message
import groovy.xml.StreamingMarkupBuilder
import groovy.xml.XmlUtil

def Message processData(Message message) {
    def body = message.getBody(String.class)
    def xml  = new XmlSlurper().parseText(body)

    def newRootName = "NewRootElement"  // TODO: Set target root name

    def builder = new StreamingMarkupBuilder()
    def result  = builder.bind {
        "${newRootName}" {
            xml.children().each { child ->
                mkp.yield child
            }
        }
    }.toString()

    message.setBody(result)
    return message
}''',
    ),

    GroovyTemplate(
        id="xml_sort_segments",
        title="Sort XML/IDoc segments in required order",
        category="xml_transform",
        adapters=["IDoc","SOAP"],
        description="Reorder XML elements/IDoc segments to match a target system's expected sequence.",
        tags=["idoc","sort","order","segments","xml"],
        source="SAP Community",
        code='''import com.sap.gateway.ip.core.customdev.util.Message
import groovy.xml.StreamingMarkupBuilder

def Message processData(Message message) {
    def body = message.getBody(String.class)
    def xml  = new XmlSlurper().parseText(body)

    // TODO: Update with required segment order
    def desiredOrder = [
        "EDI_DC40", "E1HEADER", "E1ITEM", "E1ACCOUNT"
    ]

    def idocNode      = xml.IDOC[0]
    def sortedChildren = idocNode.children().sort { a, b ->
        desiredOrder.indexOf(a.name()) <=>
        desiredOrder.indexOf(b.name())
    }

    def builder = new StreamingMarkupBuilder()
    def result  = builder.bind {
        // TODO: Update root element name
        ORDERS05 {
            IDOC(BEGIN: "1") {
                sortedChildren.each { child -> mkp.yield child }
            }
        }
    }.toString()

    message.setBody(result)
    return message
}''',
    ),

    # ── JSON ─────────────────────────────────────────────────────────
    GroovyTemplate(
        id="json_to_xml",
        title="Convert JSON payload to XML",
        category="json",
        adapters=["HTTPS","REST","OData"],
        description="Parse JSON input and generate XML output using JsonSlurper and MarkupBuilder.",
        tags=["json","xml","convert","jsonslurper"],
        source="SAP Community",
        code='''import com.sap.gateway.ip.core.customdev.util.Message
import groovy.json.JsonSlurper
import groovy.xml.StreamingMarkupBuilder
import groovy.xml.XmlUtil

def Message processData(Message message) {
    def body = message.getBody(String.class)
    def json = new JsonSlurper().parseText(body)

    def builder = new StreamingMarkupBuilder()
    def result  = builder.bind {
        mkp.xmlDeclaration()
        // TODO: Update root element
        "Request" {
            // TODO: Map JSON fields — example:
            "Field1"( json.field1 ?: "" )
            "Field2"( json.nested?.field2 ?: "" )
            if (json.items) {
                "Items" {
                    json.items.each { item ->
                        "Item" {
                            "Id"( item.id ?: "" )
                            "Value"( item.value ?: "" )
                        }
                    }
                }
            }
        }
    }

    message.setBody(XmlUtil.serialize(result))
    message.setHeader("Content-Type", "application/xml")
    return message
}''',
    ),

    GroovyTemplate(
        id="xml_to_json",
        title="Convert XML payload to JSON",
        category="json",
        adapters=["HTTPS","REST","OData"],
        description="Parse XML input and produce JSON output.",
        tags=["xml","json","convert","groovy.json"],
        source="SAP Community",
        code='''import com.sap.gateway.ip.core.customdev.util.Message
import groovy.json.JsonOutput

def Message processData(Message message) {
    def body = message.getBody(String.class)
    def xml  = new XmlSlurper().parseText(body)

    // TODO: Map XML fields to JSON structure
    def result = [
        field1: xml.Field1.text(),
        field2: xml.Field2.text(),
        items: xml.Items.Item.collect { item ->
            [
                id:    item.Id.text(),
                value: item.Value.text(),
            ]
        }
    ]

    message.setBody(JsonOutput.prettyPrint(JsonOutput.toJson(result)))
    message.setHeader("Content-Type", "application/json")
    return message
}''',
    ),

    # ── Header & Property ────────────────────────────────────────────
    GroovyTemplate(
        id="header_set_dynamic",
        title="Set dynamic headers from payload content",
        category="header",
        adapters=["HTTPS","SOAP","IDoc","OData"],
        description="Extract values from the payload and set them as message headers for downstream routing.",
        tags=["header","property","dynamic","routing"],
        source="SAP Community",
        code='''import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {
    def body = message.getBody(String.class)
    def xml  = new XmlSlurper().parseText(body)

    // Extract routing-relevant fields and set as headers
    // TODO: Update field paths
    def senderId    = xml.Header.SenderID.text()
    def receiverId  = xml.Header.ReceiverID.text()
    def messageType = xml.Header.MessageType.text()
    def docNumber   = xml.Header.DocumentNumber.text()

    message.setHeader("SenderID",     senderId)
    message.setHeader("ReceiverID",   receiverId)
    message.setHeader("MessageType",  messageType)
    message.setHeader("DocumentNo",   docNumber)
    message.setHeader("ProcessingTimestamp",
        new Date().format("yyyy-MM-dd'T'HH:mm:ss"))

    // Log to MPL
    def msgLog = messageLogFactory.getMessageLog(message)
    if (msgLog != null) {
        msgLog.addCustomHeaderProperty("SenderID",   senderId)
        msgLog.addCustomHeaderProperty("DocumentNo", docNumber)
    }

    return message
}''',
    ),

    GroovyTemplate(
        id="property_store_restore",
        title="Store and restore exchange properties across split/aggregate",
        category="header",
        adapters=["HTTPS","SOAP","JMS"],
        description="Save properties before a Splitter step and restore them after Aggregator.",
        tags=["property","split","aggregate","correlation"],
        source="SAP Community",
        code='''import com.sap.gateway.ip.core.customdev.util.Message

// Use in a Script step BEFORE Splitter to save context:
def Message saveContext(Message message) {
    def xml = new XmlSlurper().parseText(message.getBody(String.class))
    message.setProperty("OriginalDocNumber", xml.Header.DocumentNumber.text())
    message.setProperty("OriginalSender",    xml.Header.Sender.text())
    message.setProperty("SplitTimestamp",    new Date().format("yyyyMMddHHmmss"))
    return message
}

// Use in a Script step AFTER Aggregator to restore context:
def Message restoreContext(Message message) {
    def docNo  = message.getProperty("OriginalDocNumber")
    def sender = message.getProperty("OriginalSender")
    message.setHeader("DocumentNumber", docNo)
    message.setHeader("Sender",         sender)
    return message
}

// Entry point — CPI calls processData:
def Message processData(Message message) {
    // TODO: Choose saveContext or restoreContext based on placement
    return saveContext(message)
}''',
    ),

    # ── Value Mapping ────────────────────────────────────────────────
    GroovyTemplate(
        id="value_map_inline",
        title="Inline value mapping table",
        category="value_map",
        adapters=["SOAP","HTTPS","IDoc","OData"],
        description="Map source code values to target values using an inline map. "
                    "Use for small, stable mappings. For large tables use CPI Value Mapping artifact.",
        tags=["value mapping","code conversion","lookup","translation"],
        source="SAP Community",
        code='''import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {
    def body = message.getBody(String.class)
    def xml  = new XmlSlurper().parseText(body)

    // TODO: Replace with actual source→target value mappings
    def statusMap = [
        "01": "CREATED",
        "02": "IN_PROCESS",
        "03": "COMPLETED",
        "04": "CANCELLED",
        "05": "ERROR",
    ]

    def unitMap = [
        "ST": "EA",  // Each
        "KG": "KGM",
        "L":  "LTR",
        "M":  "MTR",
    ]

    // Apply mappings — TODO: update field paths
    def sourceStatus = xml.Header.Status.text()
    def sourceUnit   = xml.Item.Unit.text()

    def targetStatus = statusMap.get(sourceStatus, sourceStatus)
    def targetUnit   = unitMap.get(sourceUnit, sourceUnit)

    // Update payload
    def result = body
        .replace("<Status>${sourceStatus}</Status>",
                 "<Status>${targetStatus}</Status>")
        .replace("<Unit>${sourceUnit}</Unit>",
                 "<Unit>${targetUnit}</Unit>")

    message.setBody(result)
    return message
}''',
    ),

    # ── Error Handling ────────────────────────────────────────────────
    GroovyTemplate(
        id="error_handler_standard",
        title="Standard exception handler with MPL logging",
        category="error",
        adapters=["SOAP","HTTPS","IDoc","RFC","JDBC","AS2"],
        description="Full exception handler for the Exception Sub-Process. "
                    "Logs error to MPL, builds error response XML, sets alert header.",
        tags=["error","exception","mpl","logging","alert"],
        source="SAP Community",
        code='''import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {
    def exception = message.getProperty("CamelExceptionCaught")
    def headers   = message.getHeaders()

    def errorMsg   = exception?.getMessage() ?: "Unknown error"
    def errorClass = exception?.getClass()?.getSimpleName() ?: "Exception"
    def msgId      = headers.get("SAP_MessageProcessingLogID", "UNKNOWN")
    def sender     = headers.get("SenderID",   "UNKNOWN")
    def receiver   = headers.get("ReceiverID", "UNKNOWN")

    // Log to MPL
    def msgLog = messageLogFactory.getMessageLog(message)
    if (msgLog != null) {
        msgLog.setStringProperty("ErrorType",    errorClass)
        msgLog.setStringProperty("ErrorMessage", errorMsg)
        msgLog.setStringProperty("SenderID",     sender)
        msgLog.setStringProperty("ReceiverID",   receiver)
        msgLog.addCustomHeaderProperty("ProcessingStatus", "FAILED")
    }

    // Build error response
    def errorPayload = """<?xml version="1.0" encoding="UTF-8"?>
<ErrorResponse>
    <MessageId>${msgId}</MessageId>
    <ErrorType>${errorClass}</ErrorType>
    <ErrorMessage><![CDATA[${errorMsg}]]></ErrorMessage>
    <Timestamp>${new Date().format("yyyy-MM-dd\'T\'HH:mm:ss")}</Timestamp>
</ErrorResponse>"""

    message.setBody(errorPayload)
    message.setHeader("Content-Type",  "application/xml")
    message.setHeader("ErrorOccurred", "true")

    return message
}''',
    ),

    GroovyTemplate(
        id="retry_check",
        title="Check retry count and route to DLQ after max attempts",
        category="error",
        adapters=["JMS","HTTPS","SOAP"],
        description="Read the SAP_RetryCount header and route to dead letter handling after N attempts.",
        tags=["retry","dlq","dead letter","jms","error handling"],
        source="SAP Community",
        code='''import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {
    def headers      = message.getHeaders()
    def retryCount   = headers.get("SAP_RetryCount", "0") as int
    def maxRetries   = 3  // TODO: match your iFlow retry config

    message.setProperty("RetryCount",       retryCount)
    message.setProperty("MaxRetriesHit",    retryCount >= maxRetries)
    message.setProperty("ShouldSendToDLQ",  retryCount >= maxRetries)

    // Log retry status
    def msgLog = messageLogFactory.getMessageLog(message)
    if (msgLog != null) {
        msgLog.addCustomHeaderProperty("RetryCount",     retryCount.toString())
        msgLog.addCustomHeaderProperty("MaxRetriesHit",  (retryCount >= maxRetries).toString())
    }

    return message
}''',
    ),

    # ── Utility ──────────────────────────────────────────────────────
    GroovyTemplate(
        id="compare_xml_payloads",
        title="Compare two XML payloads and extract differences",
        category="utility",
        adapters=["HTTPS","SOAP"],
        description="Delta detection — compare two XML structures and return only changed elements. "
                    "Useful for master data replication (send only changes).",
        tags=["compare","diff","delta","master data","change detection"],
        source="SAP Community",
        code='''import com.sap.gateway.ip.core.customdev.util.Message
import groovy.xml.MarkupBuilder

def Message processData(Message message) {
    def input1 = message.getProperty("SourcePayload")   // previous version
    def input2 = message.getBody(String.class)          // current version

    if (!input1) {
        // No previous version — this is a create, pass through
        message.setProperty("IsCreate", true)
        return message
    }

    def xml1 = new XmlSlurper().parseText(input1)
    def xml2 = new XmlSlurper().parseText(input2)

    def changedFields = []

    // TODO: Update with actual element names to compare
    xml2.children().each { element ->
        def name = element.name()
        def oldVal = xml1."${name}".text()
        def newVal = element.text()
        if (oldVal != newVal) {
            changedFields << [name: name, old: oldVal, new: newVal]
        }
    }

    message.setProperty("ChangedFields", changedFields)
    message.setProperty("HasChanges",    !changedFields.isEmpty())
    message.setProperty("IsCreate",      false)

    return message
}''',
    ),

    GroovyTemplate(
        id="uuid_generator",
        title="Generate unique message ID / correlation ID",
        category="utility",
        adapters=["HTTPS","SOAP","IDoc","JMS"],
        description="Generate a UUID for message correlation, deduplication, or as a document number.",
        tags=["uuid","correlation","id","dedup","idempotency"],
        source="SAP Community",
        code='''import com.sap.gateway.ip.core.customdev.util.Message
import java.util.UUID

def Message processData(Message message) {
    // Generate unique IDs
    def msgUUID    = UUID.randomUUID().toString()
    def shortId    = msgUUID.replace("-", "").substring(0, 16).toUpperCase()
    def timestamp  = new Date().format("yyyyMMddHHmmssSSS")
    def correlId   = "CPI-${timestamp}-${shortId}"

    // Set as headers for downstream use
    message.setHeader("SAP_MessageId",    msgUUID)
    message.setHeader("CorrelationId",    correlId)
    message.setHeader("ProcessingDate",   new Date().format("yyyy-MM-dd"))
    message.setHeader("ProcessingTime",   new Date().format("HH:mm:ss"))

    // Log to MPL
    def msgLog = messageLogFactory.getMessageLog(message)
    if (msgLog != null) {
        msgLog.addCustomHeaderProperty("CorrelationId", correlId)
    }

    return message
}''',
    ),

    GroovyTemplate(
        id="payload_logger",
        title="Log payload to MPL for debugging",
        category="utility",
        adapters=["HTTPS","SOAP","IDoc","RFC","JDBC","AS2","JMS"],
        description="Attach the full message body to the MPL log entry. "
                    "Remove or disable in production — enable only for debugging.",
        tags=["logging","mpl","debug","payload","trace"],
        source="SAP Community",
        code='''import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {
    def body     = message.getBody(String.class)
    def headers  = message.getHeaders()

    def msgLog = messageLogFactory.getMessageLog(message)
    if (msgLog != null) {
        // Attach payload (truncated for large messages)
        def truncated = body?.length() > 10000 ?
            body.substring(0, 10000) + "... [TRUNCATED]" : body

        msgLog.addAttachmentAsString(
            "RequestPayload",
            truncated ?: "(empty)",
            "text/xml"
        )

        // Log key headers
        ["SenderID","ReceiverID","MessageType","DocumentNo"].each { hdr ->
            def val = headers.get(hdr)
            if (val) msgLog.addCustomHeaderProperty(hdr, val.toString())
        }
    }

    // NOTE: Disable this script step in production
    // Keep in iFlow but set step to inactive
    return message
}''',
    ),

    GroovyTemplate(
        id="content_filter",
        title="Filter messages based on payload content",
        category="utility",
        adapters=["HTTPS","SOAP","IDoc","JMS"],
        description="Route or drop messages based on field values in the payload. "
                    "Sets a property that a Router step can evaluate.",
        tags=["filter","route","content","conditional","router"],
        source="SAP Community",
        code='''import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {
    def body = message.getBody(String.class)
    def xml  = new XmlSlurper().parseText(body)

    // TODO: Update field paths and filter conditions
    def docType    = xml.Header.DocumentType.text()
    def compCode   = xml.Header.CompanyCode.text()
    def amount     = xml.Header.Amount.text()?.toBigDecimal() ?: 0

    // Set routing properties
    message.setProperty("DocType",      docType)
    message.setProperty("CompanyCode",  compCode)
    message.setProperty("Amount",       amount)

    // Compound routing decision
    message.setProperty("RouteToFinance",
        docType in ["INVOICE","CREDIT"] && amount > 1000)
    message.setProperty("RouteToApproval",
        amount > 50000)
    message.setProperty("SkipProcessing",
        docType == "TEST" || compCode == "TEST")

    return message
}''',
    ),
]


# ---------------------------------------------------------------------------
# Library class
# ---------------------------------------------------------------------------

class GroovyLibrary:

    def __init__(self):
        self._catalog = GROOVY_CATALOG

    def search(self, query: str, top_n: int = 5) -> list[GroovyTemplate]:
        """Search by keyword across title, description, tags."""
        q = query.lower()
        scored = []
        for t in self._catalog:
            text  = (t.title + " " + t.description + " " +
                     " ".join(t.tags) + " " + t.category).lower()
            score = sum(2 for word in q.split() if word in text)
            if score > 0:
                scored.append((score, t))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored[:top_n]]

    def get_by_adapter(
        self,
        sender_adapter: str,
        receiver_adapter: str,
        top_n: int = 3,
    ) -> list[GroovyTemplate]:
        """Get most relevant scripts for an adapter combination."""
        results = []
        for t in self._catalog:
            if sender_adapter in t.adapters or receiver_adapter in t.adapters:
                results.append(t)
        return results[:top_n]

    def get_by_category(self, category: str) -> list[GroovyTemplate]:
        return [t for t in self._catalog if t.category == category]

    def get_by_id(self, script_id: str) -> Optional[GroovyTemplate]:
        return next((t for t in self._catalog if t.id == script_id), None)

    def list_categories(self) -> list[str]:
        return sorted(set(t.category for t in self._catalog))

    def suggest_for_interface(
        self,
        interface_name: str,
        sender_adapter: str,
        receiver_adapter: str,
        has_mapping: bool = False,
    ) -> list[GroovyTemplate]:
        """Smart suggestion combining name keywords + adapter match."""
        import re
        keywords = set(
            w.lower() for w in re.split(r"[_\-\s/]", interface_name)
            if len(w) > 3
        )
        scored = []
        for t in self._catalog:
            score = 0
            text  = (t.title + " " + " ".join(t.tags) + " " + t.category).lower()
            score += sum(2 for kw in keywords if kw in text)
            if sender_adapter in t.adapters or receiver_adapter in t.adapters:
                score += 3
            if has_mapping and t.category in ("xml_transform", "json", "value_map"):
                score += 2
            if t.category == "error":
                score += 1   # always suggest error handler
            if score > 0:
                scored.append((score, t))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored[:4]]
