/*
 * pitfall_a_body_stream_consumed.groovy
 *
 * THE #1 most common CPI Groovy bug: reading the body without setting it
 * back, which leaves downstream steps with an empty payload.
 *
 * Why it happens: getBody(String) and getBody(byte[]) consume the
 * underlying InputStream. The body field becomes empty after the read.
 * No exception, no warning — just silent data loss.
 *
 * Symptom: Steps after your Groovy script see an empty body. Mapping
 * step crashes with "no source XML". Receiver sends an empty POST.
 * Logs show your script's output but the next step's input is gone.
 *
 * ❌ BROKEN PATTERN
 *
 *     def Message processData(Message message) {
 *         def body = message.getBody(String)
 *         // do something with body — peek, log, route...
 *         message.setHeader("BodyLength", body.length() as String)
 *         return message
 *         // BUG: body was read but never set back. Next step gets empty payload.
 *     }
 *
 * ✅ FIX
 */

import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {

    def body = message.getBody(String) ?: ""

    // Set it back IMMEDIATELY after reading, before any other work.
    // This is the load-bearing line — without it, downstream gets nothing.
    message.setBody(body)

    // Now safe to inspect, derive headers from it, etc.
    message.setHeader("BodyLength", body.length() as String)
    message.setHeader("BodyHash",   body.hashCode() as String)

    return message
}

/*
 * Alternative pattern if you need to read the body multiple times in the
 * same script: assign it to a variable once, work from the variable.
 * Don't call getBody() repeatedly — each call after the first returns
 * the cached value but adds overhead and is bug-prone if someone later
 * adds a setBody() in between.
 */
