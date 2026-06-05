/*
 * pitfall_c_messagelog_api.groovy
 *
 * Confusion between the implicit MessageLog binding and the explicit API.
 * Symptoms range from "no log appears in MPL attachments" to NullPointerException.
 *
 * Why it happens: CPI's documentation has shown two different APIs over
 * the years. Older blog posts show the implicit form which only works
 * in certain step contexts. The explicit ITApiFactory form works
 * everywhere but requires the import.
 *
 * ❌ BROKEN — works in some contexts, NPE in others
 *
 *     def Message processData(Message message) {
 *         def messageLog = messageLogFactory.getMessageLog(message)
 *         messageLog.addAttachmentAsString("Body", message.getBody(String), "text/xml")
 *         return message
 *         // BUG: 'messageLogFactory' isn't always bound. Works in Groovy Script
 *         // steps, NPE in Local Integration Processes called from a script step.
 *     }
 *
 * ✅ FIX — Explicit, works in every step type
 */

import com.sap.gateway.ip.core.customdev.util.Message
import com.sap.it.api.ITApiFactory
import com.sap.it.api.mapping.MappingContext
import com.sap.it.api.msglog.MessageLog
import com.sap.it.api.msglog.MessageLogFactory

def Message processData(Message message) {

    def body = message.getBody(String) ?: ""
    message.setBody(body)  // Pitfall A — always set back after read

    // Explicit factory lookup. Works in script steps, Local Integration
    // Processes, exception subprocesses, everywhere.
    MessageLogFactory factory = ITApiFactory.getService(MessageLogFactory, null)
    if (factory != null) {
        MessageLog log = factory.getMessageLog(message)
        if (log != null) {
            // Attach the body for MPL inspection — gated on size to avoid
            // bloating storage for every message.
            if (body.length() < 100_000) {
                log.addAttachmentAsString("PayloadSnapshot", body, "text/xml")
            }
            // String-valued log properties show in the MPL header
            log.setStringProperty("ProcessedBy", "MyGroovyScript")
            log.setStringProperty("BodyLength",  body.length() as String)
        }
    }
    // factory == null happens outside the CPI runtime — fine to skip.
    // Don't throw — your script shouldn't break unit-test runs either.

    return message
}

/*
 * Three things to know about MessageLog:
 *
 * 1. addAttachmentAsString stores the value in MPL attachment storage,
 *    which is durable and visible in monitoring UI for ~30 days. Use
 *    sparingly for large payloads — it counts against tenant quota.
 *
 * 2. setStringProperty is for short metadata (run IDs, counts, status).
 *    Visible in MPL "custom header attributes" column. No size limit
 *    enforced but stay under ~200 chars per value.
 *
 * 3. There's also log.addCustomHeaderProperty (deprecated alias for
 *    setStringProperty in newer tenants). Use setStringProperty — it's
 *    the current API name.
 */
