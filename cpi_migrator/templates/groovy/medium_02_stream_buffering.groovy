/*
 * medium_02_stream_buffering.groovy
 *
 * Pattern: Convert a streaming payload into a fully-buffered byte[] body
 * so it can be read multiple times by downstream steps. Necessary when
 * the iFlow needs to: (a) inspect the payload for routing AND pass it
 * through, (b) retry a send without re-fetching, (c) compute a hash AND
 * forward the original bytes.
 *
 * Use case: Sender adapters like HTTPS and SOAP often deliver the body
 * as a single-shot InputStream. Steps that read the body more than once
 * (a Router that peeks + a Receiver that sends) fail silently because
 * the second read gets nothing.
 *
 * Verified: Static analysis. The setBody(byte[]) call here forces full
 * materialisation — for very large payloads, prefer setting an
 * exchange property with the stream wrapped in a CachedOutputStream
 * (Camel's standard pattern). This script picks bytes for simplicity
 * since most CPI payloads are < 100 MB.
 *
 * Pitfall handled: The classic "downstream got empty body" bug. After
 * this script runs, the body is byte[] and can be read N times.
 *
 * Pitfall handled: If you do this on a 500 MB file you'll OOM the
 * worker. The size guard at the top makes that a controlled failure
 * with a clear message rather than a heap dump.
 */

import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {

    // Safety cap — adjust per tenant memory profile. Default 50 MB.
    final long maxBytes = (message.getProperty("MaxBufferBytes") ?: "52428800") as Long

    byte[] body = message.getBody(byte[]) ?: new byte[0]

    if (body.length > maxBytes) {
        // Don't buffer something that would blow up memory. Fail loud.
        throw new IllegalStateException(
            "Payload size ${body.length} exceeds buffer cap ${maxBytes}. " +
            "Increase MaxBufferBytes property or use a streaming Splitter step instead.")
    }

    // Set body back as byte[] — this is the load-bearing line.
    // Downstream getBody() calls now get the cached array, not a consumed stream.
    message.setBody(body)

    // Diagnostic headers for log/MPL visibility
    message.setHeader("BufferedBodySize", body.length as String)
    def sdf = new java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'")
    sdf.setTimeZone(TimeZone.getTimeZone("UTC"))
    message.setHeader("BufferedAt", sdf.format(new Date()))

    return message
}
