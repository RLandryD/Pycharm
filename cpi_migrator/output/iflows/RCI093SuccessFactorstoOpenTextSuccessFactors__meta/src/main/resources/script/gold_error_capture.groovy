import com.sap.gateway.ip.core.customdev.util.Message

/* Gold-standard error capture (pattern: SAP Integration Flow Design
   Guidelines - Handle Errors Gracefully). Attaches full error context to the
   MPL and sets a custom status so monitoring flows and the Monitor UI can
   triage without opening traces. */
def Message processData(Message message) {
    def log = messageLogFactory.getMessageLog(message)
    def ex = message.getProperty('CamelExceptionCaught')
    def mplId = message.getProperty('SAP_MessageProcessingLogID') ?: 'n/a'

    // classify the error so subjects/bodies can say WHY (best-effort
    // heuristic on class+message; refine per client as patterns emerge)
    def blob = ((exClass ?: '') + ' ' + (exMsg ?: ''))
    def reason = 'Execution error'
    if (blob =~ /(?i)(connect|timed? ?out|unknownhost|no route|refused|ssl|handshake|certificat|50[234])/)
        reason = 'Endpoint error'
    else if (blob =~ /(?i)(saxpars|xml.*pars|unmarshal|jsonpars|validat|schema|malformed|typeconversion)/)
        reason = 'Incoming message error'
    else if (blob =~ /(?i)(xslt|mapping|transform)/)
        reason = 'Mapping error'
    else if (blob =~ /(?i)(status code [45]|odata|http operation failed|40[013478])/)
        reason = 'Outgoing message error'
    message.setProperty('ALERT_REASON', reason)
    message.setProperty('DateTime',
        new Date().format('yyyy-MM-dd HH:mm:ss', TimeZone.getTimeZone('UTC')))

    def sb = new StringBuilder()
    sb.append('Handling policy: Escalation End (MPL ESCALATED)\n')
    sb.append('MPL ID: ').append(mplId).append('\n')
    if (ex != null) {
        sb.append('Exception class: ')
          .append(ex.getClass().getCanonicalName()).append('\n')
        sb.append('Exception message: ')
          .append(ex.getMessage() ?: '').append('\n')
        def st = ex.getStackTrace()
        if (st != null && st.length > 0) {
            int n = Math.min(st.length, 15)
            sb.append('Stack trace (first ').append(n).append('):\n')
            for (int i = 0; i < n; i++) {
                sb.append('  at ').append(st[i].toString()).append('\n')
            }
        }
    } else {
        sb.append('No CamelExceptionCaught present.\n')
    }
    sb.append('Headers at failure (secrets redacted):\n')
    message.getHeaders().each { k, v ->
        if (!(k ==~ /(?i).*(auth|password|token|secret|cookie|apikey).*/)) {
            sb.append('  ').append(k).append(' = ')
              .append(String.valueOf(v).take(200)).append('\n')
        }
    }
    sb.append('Exchange property names:\n')
    message.getProperties().keySet().sort().each { k ->
        sb.append('  ').append(k).append('\n')
    }

    if (log != null) {
        log.addAttachmentAsString('ErrorContext', sb.toString(), 'text/plain')
        def body = ''
        try { body = (message.getBody(java.lang.String) ?: '') }
        catch (Exception ignore) { }
        if (body) {
            log.addAttachmentAsString('FailedPayload',
                body.take(100000), 'text/plain')
        }
        log.setStringProperty('ErrorType',
            ex != null ? ex.getClass().getSimpleName() : 'Unknown')
    }
    // visible in Monitor's Custom Status column (alphanumeric + _, max 40)
    def cs = 'Escalated_' + (ex != null ? ex.getClass().getSimpleName()
                                      : 'Unknown')
    message.setProperty('SAP_MessageProcessingLogCustomStatus',
        cs.replaceAll('[^A-Za-z0-9_]', '_').take(40))
    return message
}
