/*
 * migration_02_dynamic_receiver.groovy
 *
 * Pattern: Resolve the receiver endpoint dynamically from a routing table.
 * Core to consolidating many PI channels into fewer CPI flows — instead of
 * N channels, one flow with a Receiver adapter whose address is
 * ${header.DynamicAddress}, set here.
 *
 * Use case: PI had 20 SOAP receiver channels differing only by URL. In
 * CPI you build one flow; this script looks up the target URL from a
 * routing key in the payload or a property and sets the address header.
 *
 * Verified: STATIC ONLY. The address-header convention
 * (set a header, reference ${header.X} in the Receiver adapter address)
 * is standard CPI — verify the exact header name the adapter expects in
 * your tenant.
 *
 * Pitfall handled: setBody after read; fail-loud on unknown routing key
 * rather than sending to a null/empty address (which silently 404s).
 */

import com.sap.gateway.ip.core.customdev.util.Message
import groovy.xml.XmlSlurper

def Message processData(Message message) {

    def body = message.getBody(String) ?: ""
    message.setBody(body)

    // Routing key — from a property set upstream, or read from payload
    def routingKey = message.getProperty("RoutingKey")
    if (!routingKey && body.trim().startsWith("<")) {
        try {
            def root = new XmlSlurper().parseText(body)
            routingKey = root.ReceiverSystem?.text() ?: root.@target?.text() ?: ""
        } catch (Exception ex) {
            message.setProperty("RoutingResolveError", ex.message)
        }
    }

    // Routing table. In production, externalise this to a Value Mapping or
    // a Number Range / data store lookup. Inline here for the simple case.
    def routingTable = [
        "ARIBA"        : "https://ariba.example.com/api/v1/inbound",
        "SUCCESSFACTORS": "https://sf.example.com/odata/v2/inbound",
        "S4HANA"       : "https://s4.example.com/sap/opu/odata/inbound",
    ]

    def targetUrl = routingTable[routingKey?.toString()?.toUpperCase()]
    if (!targetUrl) {
        // Fail loud — never send to an empty address
        throw new IllegalStateException(
            "No receiver endpoint for routing key '${routingKey}'. " +
            "Known keys: ${routingTable.keySet()}")
    }

    // The Receiver adapter address field should be set to ${header.DynamicAddress}
    message.setHeader("DynamicAddress", targetUrl)
    message.setProperty("ResolvedReceiver", routingKey)
    return message
}
