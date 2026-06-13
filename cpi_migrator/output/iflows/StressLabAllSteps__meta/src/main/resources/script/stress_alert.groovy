import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {
    def ex = message.getProperty('CamelExceptionCaught')
    def log = messageLogFactory.getMessageLog(message)
    if (log != null) {
        log.setStringProperty('StressLabError',
            ex != null ? ex.toString() : 'unknown')
    }
    return message
}
