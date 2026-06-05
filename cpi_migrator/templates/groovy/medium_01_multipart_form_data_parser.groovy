/*
 * medium_01_multipart_form_data_parser.groovy
 *
 * Pattern: Parse a multipart/form-data request body, extract each part as
 * a named field, and either keep them as exchange properties (for small
 * fields) or as data-store entries (for file uploads).
 *
 * Use case: A REST/HTTPS sender adapter receives a multipart upload
 * (form fields + file attachments). Standard CPI doesn't parse multipart
 * natively — this script extracts parts so downstream steps can route
 * on form fields or persist files separately.
 *
 * Verified: Static analysis. Logic follows RFC 7578 (multipart/form-data).
 * Boundary detection is by Content-Type header — test against your
 * specific client's multipart serialiser since some omit the leading
 * "--" or use non-standard quoting.
 *
 * Pitfall handled: Reading multipart with naive split("\n") fails because
 * binary parts contain \n bytes. We split on the boundary delimiter
 * INCLUDING its leading "--" sentinel.
 *
 * Pitfall handled: Don't use String for the body when binary parts are
 * involved — getBody(byte[]) preserves byte integrity. We do String here
 * only after extracting each part because field values are typically
 * text; binary file parts get base64-encoded for downstream.
 */

import com.sap.gateway.ip.core.customdev.util.Message
import java.util.Base64

def Message processData(Message message) {

    def contentType = (message.getHeader("Content-Type", String) ?: "").toLowerCase()
    if (!contentType.contains("multipart/")) {
        message.setProperty("MultipartParseSkipped", "not multipart")
        return message
    }

    // Boundary parameter — formats vary: boundary=xxx, boundary="xxx"
    def boundaryMatch = contentType =~ /boundary="?([^";]+)"?/
    if (!boundaryMatch.find()) {
        message.setProperty("MultipartParseError", "boundary missing from Content-Type")
        return message
    }
    def boundary = "--" + boundaryMatch.group(1)
    def boundaryBytes = boundary.getBytes("UTF-8")

    // Read body as bytes — preserves binary content
    byte[] body = message.getBody(byte[]) ?: new byte[0]

    // Split on boundary. We use ByteArrayOutputStream-based splitter since
    // groovy's String.split won't handle arbitrary byte sequences.
    def parts = splitByBoundary(body, boundaryBytes)

    int partIndex = 0
    parts.each { partBytes ->
        if (partBytes.length < 4) return  // skip prologue/epilogue/empty

        // Each part has headers \r\n\r\n body
        int headerEnd = indexOfSeparator(partBytes, "\r\n\r\n".getBytes("UTF-8"))
        if (headerEnd < 0) return

        def headerSection = new String(partBytes, 0, headerEnd, "UTF-8")
        byte[] valueBytes = partBytes[(headerEnd + 4)..-1] as byte[]
        // Trim trailing \r\n that precedes the next boundary
        if (valueBytes.length >= 2
                && valueBytes[valueBytes.length - 2] == (byte)0x0D
                && valueBytes[valueBytes.length - 1] == (byte)0x0A) {
            valueBytes = valueBytes[0..valueBytes.length - 3] as byte[]
        }

        // Extract field name + filename from Content-Disposition
        def dispositionMatch = headerSection =~ /name="([^"]+)"(?:; *filename="([^"]+)")?/
        if (!dispositionMatch.find()) return
        def fieldName = dispositionMatch.group(1)
        def fileName  = dispositionMatch.group(2)

        if (fileName) {
            // Binary file part — base64 for downstream + filename property
            message.setProperty("MultipartFile_${fieldName}_name",   fileName)
            message.setProperty("MultipartFile_${fieldName}_base64", Base64.encoder.encodeToString(valueBytes))
            message.setProperty("MultipartFile_${fieldName}_size",   valueBytes.length as String)
        } else {
            // Text field — UTF-8 decode and store as property
            message.setProperty("MultipartField_${fieldName}", new String(valueBytes, "UTF-8"))
        }
        partIndex++
    }
    message.setProperty("MultipartPartCount", partIndex as String)
    return message
}

// Split a byte array on a boundary byte sequence. Returns list of parts
// between boundaries (excluding the boundary itself).
private List splitByBoundary(byte[] data, byte[] boundary) {
    def parts = []
    int pos = 0
    while (pos < data.length) {
        int idx = indexOfSeparator(data, boundary, pos)
        if (idx < 0) {
            // Last segment — typically the epilogue, often empty
            if (pos < data.length) parts << (data[pos..-1] as byte[])
            break
        }
        if (idx > pos) parts << (data[pos..idx - 1] as byte[])
        pos = idx + boundary.length
        // Skip the \r\n that follows a boundary
        if (pos < data.length - 1 && data[pos] == (byte)0x0D && data[pos + 1] == (byte)0x0A) {
            pos += 2
        }
    }
    return parts
}

// Find first index of `needle` in `haystack` starting at `from`.
private int indexOfSeparator(byte[] haystack, byte[] needle, int from = 0) {
    if (needle.length == 0) return from
    outer:
    for (int i = from; i <= haystack.length - needle.length; i++) {
        for (int j = 0; j < needle.length; j++) {
            if (haystack[i + j] != needle[j]) continue outer
        }
        return i
    }
    return -1
}
