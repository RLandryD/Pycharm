import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {
    def body = message.getBody(java.lang.String) as String
    def log = messageLogFactory.getMessageLog(message)
    if (log != null) {
        log.setStringProperty('StressLab', 'seeded')
        log.addAttachmentAsString('SeededPayload',
            body ?: '', 'text/xml')
    }
    message.setProperty('P_RUNSTAMP', String.valueOf(System.currentTimeMillis()))
    message.setProperty('P_ORDERCOUNT',
        String.valueOf((body =~ /<Order /).size()))
    return message
}
