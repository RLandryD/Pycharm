import com.sap.gateway.ip.core.customdev.util.Message;

def Message processData(Message message) {
    try {
        def body = message.getBody(java.lang.String);
        def messageLog = messageLogFactory.getMessageLog(message)
        if (messageLog != null) {
            messageLog.addAttachmentAsString("ExceptionLog", body, "text/plain");
        }
        return message;
    } catch (Exception e) {
        def messageLog = messageLogFactory.getMessageLog(message);
        if (messageLog != null) {
            messageLog.addAttachmentAsString("ExceptionLog", e.getMessage(), "text/plain");
        }
        return message;
    }
}
