/*
 * pitfall_b_exception_swallowed.groovy
 *
 * Second most common bug: catching all exceptions to "prevent the iFlow
 * from failing" then continuing with a corrupted message. The iFlow
 * completes "successfully" but produced garbage. Worse, monitoring
 * shows no error — the broken message gets delivered to the receiver
 * and the corruption surfaces hours later in downstream systems.
 *
 * Why it happens: CPI's default behaviour when a script throws is to
 * fail the message (which the consultant sees as red in MPL monitor).
 * Tempting to catch everything to make MPL green. Don't.
 *
 * ❌ BROKEN PATTERN
 *
 *     def Message processData(Message message) {
 *         try {
 *             def parsed = new XmlSlurper().parseText(message.getBody(String))
 *             message.setProperty("OrderId", parsed.Header.Id.text())
 *         } catch (Exception ignored) {
 *             // BUG: parse failed, OrderId is now missing, but iFlow
 *             // continues as if everything is fine.
 *         }
 *         return message
 *     }
 *
 * ✅ FIX — Three acceptable patterns depending on intent:
 */

import com.sap.gateway.ip.core.customdev.util.Message
import groovy.xml.XmlSlurper

// PATTERN 1: Let it throw. The iFlow's Exception Subprocess catches it,
// logs the full stack trace to MPL, and the consultant sees a clear
// red bar with the actual error. This is the CORRECT default.
def Message processData_letItThrow(Message message) {
    def body   = message.getBody(String) ?: ""
    message.setBody(body)
    def parsed = new XmlSlurper().parseText(body)  // throws on malformed XML — good
    message.setProperty("OrderId", parsed.Header.Id.text())
    return message
}

// PATTERN 2: Catch, route via property. Use when the iFlow has a Router
// downstream that branches on success/failure. Surface the reason as a
// property the Router can read.
def Message processData_routeOnError(Message message) {
    def body = message.getBody(String) ?: ""
    message.setBody(body)
    try {
        def parsed = new XmlSlurper().parseText(body)
        message.setProperty("OrderId",    parsed.Header.Id.text())
        message.setProperty("ParseStatus", "OK")
    } catch (Exception ex) {
        // Capture WHY, not just THAT.
        message.setProperty("ParseStatus", "FAILED")
        message.setProperty("ParseError",  ex.class.simpleName + ": " + ex.message)
        // Don't set OrderId — downstream must check ParseStatus before using it.
    }
    return message
}

// PATTERN 3: Catch and rethrow with context. Use when the original
// exception's message is unhelpful and you want to enrich it for
// monitoring. Always rethrow — never just rethrow nothing.
def Message processData_enrich(Message message) {
    def body = message.getBody(String) ?: ""
    message.setBody(body)
    try {
        def parsed = new XmlSlurper().parseText(body)
        message.setProperty("OrderId", parsed.Header.Id.text())
        return message
    } catch (Exception ex) {
        // Wrap with interface-specific context so MPL log is useful
        def correlationId = message.getHeader("SAP_MessageProcessingLogID", String) ?: "unknown"
        throw new RuntimeException(
            "Failed to parse Order payload for MPL ${correlationId}: ${ex.message}", ex)
    }
}

/*
 * The pattern to NEVER use:
 *
 *   } catch (Exception ignored) {}        // silent swallow
 *   } catch (Exception ex) { log.info ex }  // logged but execution continues with bad data
 *
 * If you don't want the message to fail, design the iFlow to handle the
 * failure (Pattern 2). Don't silently corrupt and continue.
 */
