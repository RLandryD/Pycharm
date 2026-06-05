/*
 * pitfall_e_variable_scope.groovy
 *
 * Confusion between headers, properties, and global/local variables — a
 * top source of subtle CPI bugs where a value "disappears" between steps.
 *
 * The four storage scopes in CPI and how long they live:
 *
 *   Headers     - travel with the message, CAN leak to receiver adapters
 *                 (become HTTP headers!). Cleared by some adapter steps.
 *   Properties  - exchange-scoped, live for the whole iFlow execution,
 *                 NEVER leak to the receiver. Safest for internal state.
 *   Global vars - persisted ACROSS iFlow executions (Write Variables step).
 *                 Survive restarts. For cross-message state.
 *   Local vars  - Groovy method scope only — gone when the script returns.
 *
 * ❌ BROKEN PATTERN — using a header for internal state
 *
 *     message.setHeader("InternalApiKey", secretKey)
 *     // BUG: if the next step is an HTTP Receiver, InternalApiKey gets
 *     // sent as an HTTP header to the partner. Secret leak.
 *
 * ❌ BROKEN PATTERN — expecting a local var to persist
 *
 *     def counter = 0          // resets to 0 every message
 *     counter++
 *     // BUG: this is NOT a running total across messages. Each message
 *     // gets a fresh execution with counter=0.
 *
 * ✅ CORRECT USAGE
 */

import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {

    def body = message.getBody(String) ?: ""
    message.setBody(body)

    // Internal state that must NOT reach the receiver -> property
    message.setProperty("InternalApiKey", "use-secure-store-really")
    message.setProperty("ProcessingStage", "validated")

    // Value the receiver SHOULD get (e.g. a real HTTP header) -> header
    message.setHeader("X-Correlation-Id",
        message.getProperty("SAP_MessageProcessingLogID") ?: "n/a")

    // Cross-message running state -> would need a Write Variables step
    // (global variable), NOT a local var. A local like below resets each run:
    def localOnly = 0
    localOnly++   // always ends at 1 — correct only for within-this-execution use

    message.setProperty("LocalCounterDemo", localOnly as String)
    return message
}

/*
 * Quick reference — pick the scope by lifetime + visibility:
 *
 *   Need it only inside this script?         -> local variable
 *   Need it later in THIS message's flow?    -> property (setProperty)
 *   Need the receiver to receive it?         -> header (setHeader) — but
 *                                               beware it leaks to HTTP/SOAP
 *   Need it ACROSS messages / after restart? -> global variable (Write
 *                                               Variables step, not Groovy)
 *
 * Rule of thumb: default to PROPERTY for internal state. Only use a header
 * when you specifically want the receiver to see the value.
 */
