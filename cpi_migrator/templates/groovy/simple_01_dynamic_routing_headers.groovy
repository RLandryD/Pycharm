/*
 * simple_01_dynamic_routing_headers.groovy
 *
 * Pattern: Set message headers used by downstream Router steps to choose
 * a branch based on payload content.
 *
 * Use case: An iFlow has a Router that branches on header CamelHttpMethod
 * or a custom header like RoutingTarget. This script reads a field from
 * the payload and sets the routing header BEFORE the Router executes.
 *
 * Verified: Static analysis only — syntax + CPI API surface. Not executed
 * against a tenant. Test in a CPI dev tenant before production use.
 *
 * Pitfall handled: Reading body twice is the #1 source of "empty payload"
 * bugs in CPI Groovy. We read the body as String ONCE, store it back, so
 * downstream steps still get the payload.
 */

import com.sap.gateway.ip.core.customdev.util.Message
import groovy.xml.XmlSlurper

def Message processData(Message message) {

    // Read body ONCE. setBody() puts it back so downstream steps still see it.
    // Without setBody, the body stream is consumed and downstream gets null.
    def bodyText = message.getBody(String) ?: ""
    message.setBody(bodyText)

    // Default routing target — overridden below if payload parses successfully
    def routingTarget = "DEFAULT"
    def priority      = "NORMAL"

    if (bodyText.trim().startsWith("<")) {
        try {
            def root = new XmlSlurper().parseText(bodyText)
            // Field names follow the example invoice schema — adapt per interface
            def country = root.@country?.text() ?: root.Country?.text() ?: ""
            def amount  = (root.Amount?.text() ?: "0") as BigDecimal

            // Routing rules — straightforward business logic
            routingTarget = country in ["DE", "FR", "IT", "ES"] ? "EU_REGION" : "GLOBAL"
            priority      = amount > 10000 ? "HIGH" : "NORMAL"
        } catch (Exception ex) {
            // Parse failure must not crash the iFlow — fall back to defaults
            // and surface the reason via header for monitoring.
            message.setHeader("RoutingParseError", ex.message)
        }
    }

    message.setHeader("RoutingTarget", routingTarget)
    message.setHeader("Priority",      priority)

    return message
}
