import com.sap.gateway.ip.core.customdev.util.Message

// Generated pass-through script — complete the transform as needed.
def Message processData(Message message) {
    def body = message.getBody(java.lang.String) as String
    // create an exchange property (readable by later steps)
    message.setProperty('ScriptProcessedAt', new Date().toString())
    // log to the Message Processing Log for monitoring visibility
    def messageLog = messageLogFactory.getMessageLog(message)
    if (messageLog != null) {
        messageLog.setStringProperty('ScriptStep', 'executed')
    }
    message.setBody(body)
    return message
}
