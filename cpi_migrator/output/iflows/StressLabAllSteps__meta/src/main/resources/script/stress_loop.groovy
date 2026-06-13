import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {
    def n = (message.getProperty('P_LOOPCOUNT') ?: '0') as Integer
    message.setProperty('P_LOOPCOUNT', String.valueOf(n + 1))
    def log = messageLogFactory.getMessageLog(message)
    if (log != null) log.setStringProperty('LoopIteration', String.valueOf(n + 1))
    return message
}
