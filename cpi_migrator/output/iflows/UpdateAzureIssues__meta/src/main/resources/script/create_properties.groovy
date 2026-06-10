import com.sap.gateway.ip.core.customdev.util.Message
import groovy.json.JsonSlurper

Message processData(Message message) {
    
    String figafJsonPayloadString = message.getBody(java.lang.String)

    if (figafJsonPayloadString == null || figafJsonPayloadString.isEmpty()) {
        message.setProperty("ScriptError_FigafPayloadMissing", "Figaf JSON payload was null or empty.")
        throw new Exception("Figaf JSON payload was null or empty.")
        return message 
    }

    def slurper = new JsonSlurper()
    def figafPayload

    try {
        figafPayload = slurper.parseText(figafJsonPayloadString)
    } catch (Exception e) {
        message.setProperty("ScriptError_JsonParsingFailed", "Failed to parse Figaf JSON: " + e.getMessage())
        
        throw new Exception("Failed to parse Figaf JSON: " + e.getMessage(), e)
        return message
    }

    // Extract eventType
    String eventType = figafPayload.eventType
    if (eventType != null) {
        message.setProperty("eventType", eventType)
    } else {
        message.setProperty("ScriptWarning_EventTypeMissing", "eventType field was not found in Figaf payload.")
    }

    // Extract entityType
    String entityType = figafPayload.entityType
    if (entityType != null) {
        message.setProperty("entityType", entityType)
    } else {
        message.setProperty("ScriptWarning_EntityTypeMissing", "entityType field was not found in Figaf payload.")
    }

    return message
}