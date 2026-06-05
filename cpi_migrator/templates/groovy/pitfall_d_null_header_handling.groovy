/*
 * pitfall_d_null_header_handling.groovy
 *
 * Common NPE pattern: assuming a header is present when a sender adapter
 * may or may not provide it. Causes intermittent message failures that
 * are hard to reproduce because they depend on which client sent the
 * message.
 *
 * Why it happens: Different HTTP clients send different header sets.
 * curl might send "User-Agent" lowercase; a Postman test sends it
 * Capitalised; a Java client might omit it entirely. CPI preserves
 * case but lookup is case-sensitive in Groovy.
 *
 * ❌ BROKEN
 *
 *     def Message processData(Message message) {
 *         def userAgent = message.getHeader("User-Agent", String)
 *         if (userAgent.contains("Mobile")) { ... }
 *         // BUG: NPE if header missing OR if cased differently.
 *     }
 *
 * ✅ FIXES — three layers of defense
 */

import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {

    def body = message.getBody(String) ?: ""
    message.setBody(body)

    // FIX 1: Elvis operator — never assume non-null
    def userAgent = (message.getHeader("User-Agent", String) ?: "").toLowerCase()
    boolean isMobile = userAgent.contains("mobile") || userAgent.contains("android") || userAgent.contains("iphone")

    // FIX 2: Case-insensitive lookup via header map scan, since some
    // adapters normalise case differently. Get the full header map
    // ONCE and scan, rather than guessing every possible casing.
    def headers = message.getHeaders() ?: [:]
    def contentType = findHeaderCaseInsensitive(headers, "Content-Type") ?: ""

    // FIX 3: For properties (vs headers), same pattern but on getProperties()
    def runtimeProperties = message.getProperties() ?: [:]
    def customRouting = runtimeProperties.get("CustomRouting") ?: "DEFAULT"

    message.setHeader("DetectedDevice",  isMobile ? "MOBILE" : "DESKTOP")
    message.setHeader("DetectedContent", contentType)
    message.setProperty("ResolvedRoute", customRouting)

    return message
}

private String findHeaderCaseInsensitive(Map headers, String name) {
    def lowerName = name.toLowerCase()
    def entry = headers.find { k, v -> k?.toString()?.toLowerCase() == lowerName }
    return entry?.value?.toString()
}

/*
 * Bonus pitfall: getHeader(String name) returns Object, not String.
 * If the adapter set it as an Integer/List/byte[], your .contains()
 * call on it will fail in surprising ways. Always pass the type
 * explicitly: getHeader("X-Count", Integer) — Groovy will coerce
 * or return null cleanly.
 */
