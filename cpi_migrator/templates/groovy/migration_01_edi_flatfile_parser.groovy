/*
 * migration_01_edi_flatfile_parser.groovy
 *
 * Pattern: Parse a delimited flat file (CSV / fixed-position / custom
 * delimiter) into XML when CPI's built-in converters are too rigid for
 * the source format. B2B migrations frequently hit irregular files that
 * the standard converter rejects.
 *
 * Use case: A partner sends a pipe-delimited or ragged CSV that the CPI
 * "Converter > CSV to XML" step can't handle (variable columns, embedded
 * delimiters, multi-record-type files). This Groovy fallback gives full
 * control.
 *
 * Verified: STATIC ONLY (no CPI runtime here). Logic is plain Groovy
 * string/collection handling — low risk, but test against your real
 * file in a dev tenant.
 *
 * Pitfall handled: setBody after read (pitfall_a). Charset made explicit
 * (pitfall_f) — getBody(String) can use platform default and corrupt
 * non-ASCII.
 */

import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {

    // Explicit UTF-8 — never rely on platform default charset
    byte[] raw = message.getBody(byte[]) ?: new byte[0]
    String text = new String(raw, "UTF-8")
    message.setBody(text)   // preserve for downstream

    // Configurable via properties (Configure tab)
    String delimiter = message.getProperty("FlatFileDelimiter") ?: "|"
    boolean hasHeader = (message.getProperty("FlatFileHasHeader") ?: "true") == "true"

    def lines = text.split(/\r?\n/).findAll { it.trim().length() > 0 }
    if (lines.size() == 0) {
        message.setBody("<Records/>")
        return message
    }

    List<String> columnNames
    int startIdx = 0
    if (hasHeader) {
        columnNames = splitRow(lines[0], delimiter)
        startIdx = 1
    } else {
        // Synthesize Col1..ColN from the first data row's width
        int width = splitRow(lines[0], delimiter).size()
        columnNames = (1..width).collect { "Col${it}".toString() }
    }

    def sb = new StringBuilder()
    sb.append("<Records>")
    for (int i = startIdx; i < lines.size(); i++) {
        def cells = splitRow(lines[i], delimiter)
        sb.append("<Record>")
        columnNames.eachWithIndex { col, idx ->
            def value = idx < cells.size() ? cells[idx] : ""
            sb.append("<").append(sanitizeTag(col)).append(">")
            sb.append(escapeXml(value))
            sb.append("</").append(sanitizeTag(col)).append(">")
        }
        sb.append("</Record>")
    }
    sb.append("</Records>")

    message.setBody(sb.toString())
    return message
}

private List<String> splitRow(String row, String delimiter) {
    // Literal split — for quoted-delimiter handling, extend here
    return row.split(java.util.regex.Pattern.quote(delimiter), -1).collect { it.trim() }
}

private String sanitizeTag(String name) {
    // XML element names can't contain spaces/special chars
    def cleaned = name.replaceAll(/[^A-Za-z0-9_]/, "_")
    return cleaned ==~ /^[0-9].*/ ? "_" + cleaned : cleaned
}

private String escapeXml(String v) {
    return v.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")
}
