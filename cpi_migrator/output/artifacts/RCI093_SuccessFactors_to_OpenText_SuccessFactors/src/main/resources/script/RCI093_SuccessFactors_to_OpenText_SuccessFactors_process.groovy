import com.sap.gateway.ip.core.customdev.util.Message
import java.util.HashMap

// Generic processing + logging script (starting point — complete as needed).
def Message processData(Message message) {
    def body = message.getBody(java.lang.String) as String

    // Log for traceability (visible in MPL)
    def messageLog = messageLogFactory.getMessageLog(message)
    if (messageLog != null) {
        messageLog.setStringProperty("ProcessedBy", "MigrationTool")
        messageLog.addAttachmentAsString("IncomingPayload", body ?: "", "text/plain")
    }

    // TODO: add interface-specific processing here.
    message.setBody(body)
    return message
}
