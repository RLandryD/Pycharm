import com.sap.gateway.ip.core.customdev.util.Message
import groovy.json.JsonSlurper

Message processData(Message message) {
    def body = message.getBody(String)
    if (!body) {
        message.setProperty("ScriptError_PayloadMissing", "Payload is empty")
        throw new Exception("Empty JSON payload")
    }

    def json
    try {
        json = new JsonSlurper().parseText(body)
    } catch (Exception e) {
        message.setProperty("ScriptError_JsonParsingFailed", "JSON parsing error: " + e.message)
        throw new Exception("Failed to parse JSON: " + e.message, e)
    }
    
    // Extract 'number' field 
    def ticketNumber = json?.number
    
    if (ticketNumber != null) {
        message.setProperty("externalTicketIds", ticketNumber.toString())
    } else {
        message.setProperty("ScriptWarning_NumberMissing", "Missing 'number' in payload.")
    }

    return message
}
