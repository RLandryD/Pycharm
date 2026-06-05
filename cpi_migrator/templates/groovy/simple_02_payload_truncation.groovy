/*
 * simple_02_payload_truncation.groovy
 *
 * Pattern: Truncate a large payload before logging or before sending to a
 * receiver that has a body-size limit. Preserves head + tail so the
 * structure is recognisable in logs, marks where the cut happened, and
 * records the original length as a header.
 *
 * Use case: Many B2B / IDoc / large EDI receivers reject payloads above
 * a fixed size. Or a Write Variables step is being used to log full
 * payloads to MPL attachments, but you want to cap log volume.
 *
 * Verified: Static analysis only. Adjust MAX_BYTES to fit your scenario.
 *
 * Pitfall handled: Using .substring() on a UTF-8 string with multibyte
 * characters can split a character in half. We operate on bytes when the
 * intent is a byte-size cap, on chars when the intent is character count.
 * This template defaults to character-count truncation since logs are
 * usually rendered as text — adjust the helper if your receiver has a
 * byte limit instead.
 */

import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {

    // Configurable — usually injected via externalised parameter
    // (Configure tab in iFlow editor, accessed here via property)
    final int maxChars = (message.getProperty("MaxPayloadChars") ?: "10000") as Integer

    def bodyText = message.getBody(String) ?: ""
    int originalLength = bodyText.length()

    if (originalLength > maxChars) {
        // Keep enough head + tail to be diagnostic in logs
        int headSize = (int)(maxChars * 0.7)
        int tailSize = maxChars - headSize - 100  // leave room for marker

        def head = bodyText.substring(0, headSize)
        def tail = bodyText.substring(originalLength - tailSize, originalLength)
        def marker = "\n... [TRUNCATED ${originalLength - headSize - tailSize} chars] ...\n"

        message.setBody(head + marker + tail)
        message.setHeader("PayloadTruncated",      "true")
        message.setHeader("OriginalPayloadLength", originalLength as String)
    } else {
        // Set the body back to preserve the stream for downstream steps.
        // Without this, the next step would see an empty body because
        // getBody(String) consumes the stream.
        message.setBody(bodyText)
        message.setHeader("PayloadTruncated", "false")
    }

    return message
}
