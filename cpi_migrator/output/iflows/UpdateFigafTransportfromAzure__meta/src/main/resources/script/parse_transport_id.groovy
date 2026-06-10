import com.sap.gateway.ip.core.customdev.util.Message
import groovy.json.JsonSlurper

Message processData(Message message) {
    // Get the incoming JSON payload
    String jsonPayloadString = message.getBody(java.lang.String)

    if (jsonPayloadString == null || jsonPayloadString.isEmpty()) {
        message.setProperty("ScriptError_PayloadMissing", "Incoming JSON payload (transport array) was null or empty.")
        throw new Exception("Incoming JSON payload (transport array) was null or empty.")
    }

    def slurper = new JsonSlurper()
    def parsedPayload 

    try {
        parsedPayload = slurper.parseText(jsonPayloadString)
    } catch (Exception e) {
        message.setProperty("ScriptError_JsonParsingFailed", "Failed to parse incoming JSON (transport array): " + e.getMessage())
        throw new Exception("Failed to parse incoming JSON (transport array): " + e.getMessage(), e)
    }

    // Check if the parsed payload is a List and is not empty
    if (parsedPayload instanceof List && !parsedPayload.isEmpty()) {
        // Get the first object (transport) from the list
        def firstTransportObject = parsedPayload[0] // Or parsedPayload.getAt(0)

        if (firstTransportObject != null && firstTransportObject instanceof Map) {
            // Extract the technicalTransportId from the first transport object
            String technicalTransportIdValue = firstTransportObject.technicalTransportId

            if (technicalTransportIdValue != null && technicalTransportIdValue instanceof String) {
                message.setProperty("technicalTransportId", technicalTransportIdValue)
            } else {
                message.setProperty("ScriptWarning_TechnicalTransportIdMissingOrInvalid", "'technicalTransportId' field was not found or not a string in the first transport object.")
                throw new Exception("technicalTransportId field was not found or not a string in the first transport object.")
            }
        } else {
            message.setProperty("ScriptWarning_FirstTransportObjectInvalid", "The first element in the JSON array is not a valid object.")
            throw new Exception("The first element in the JSON array is not a valid object.")
        }
    } else {
        message.setProperty("ScriptWarning_PayloadNotListOrEmpty", "Parsed JSON payload is not a list or is empty.")
        message.setProperty("shouldUpdateTransport", "False")
        throw new Exception("Parsed JSON payload is not a list or is empty.")
    }

    return message
}