/*
 * pitfall_f_charset_mismatch.groovy
 *
 * Charset corruption: getBody(String) decodes bytes using a charset, and
 * if you don't control which one, non-ASCII characters (accents, umlauts,
 * Cyrillic, Chinese) get mangled. Shows as ? or mojibake in the output.
 *
 * Why it happens: getBody(String) may use the message's Content-Type
 * charset, or fall back to a platform default. On different CPI workers
 * the default can differ, so the SAME script produces correct output in
 * test and corrupted output in prod — maddening to debug.
 *
 * Symptom: "Müller" becomes "MÃ¼ller" or "M?ller". JSON with emoji breaks.
 * Chinese product names turn into question marks.
 *
 * ❌ BROKEN
 *
 *     def text = message.getBody(String)        // charset = whatever
 *     def upper = text.toUpperCase()
 *     message.setBody(upper)                     // re-encoded with default
 *     // BUG: double charset assumption — decode AND encode both unguarded
 *
 * ✅ FIX — always control charset explicitly on BOTH read and write
 */

import com.sap.gateway.ip.core.customdev.util.Message
import java.nio.charset.StandardCharsets

def Message processData(Message message) {

    // Read as bytes, decode with EXPLICIT charset
    byte[] raw = message.getBody(byte[]) ?: new byte[0]
    String text = new String(raw, StandardCharsets.UTF_8)

    // ... do work on text ...
    String result = text   // (transformation would go here)

    // Write back with EXPLICIT charset — and tell downstream what it is
    message.setBody(result.getBytes(StandardCharsets.UTF_8))
    message.setHeader("Content-Type", "application/xml; charset=UTF-8")

    return message
}

/*
 * Three rules to never get bitten by charset:
 *
 * 1. Read bytes + decode explicitly: new String(getBody(byte[]), UTF_8).
 *    Don't trust getBody(String) for non-ASCII payloads.
 *
 * 2. Write bytes + encode explicitly: setBody(text.getBytes(UTF_8)).
 *    Don't setBody(String) and hope the encoder picks UTF-8.
 *
 * 3. Set the Content-Type charset header so the receiver decodes correctly.
 *
 * If the source is genuinely a different charset (e.g. ISO-8859-1 from a
 * legacy mainframe), decode with THAT charset on read, then re-encode as
 * UTF-8 on write. The key is: always be explicit, never let the platform
 * default decide.
 */
