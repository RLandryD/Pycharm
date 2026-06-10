import com.sap.gateway.ip.core.customdev.util.Message
import groovy.json.JsonSlurper
import groovy.json.JsonBuilder

def resolveTemplate(String template, def data) {
    if (template == null || data == null) {
        return template
    }
    return template.replaceAll(/\[\[(.+?)\]\]/) { fullMatch, placeholderPath ->
        try {
            def value = placeholderPath.tokenize('.').inject(data) { current, part ->
                if (current == null) return null
                // Handle array access like webhookTicketDtoList[0] or simpleArray[1]
                if (part.contains('[') && part.endsWith(']')) {
                    def arrayName = part.substring(0, part.indexOf('['))
                    def indexStr = part.substring(part.indexOf('[') + 1, part.length() - 1)
                    if (indexStr.isNumber()) {
                        def index = indexStr.toInteger()
                        if (arrayName.isEmpty() && current instanceof List) { // e.g. {{[0].name}} if data is a list
                             return current.getAt(index)
                        } else if (!arrayName.isEmpty() && current."$arrayName" instanceof List) { // e.g. {{webhookTicketDtoList[0].name}}
                            return current."$arrayName"?.getAt(index)
                        } else {
                            return null // Array or index not valid
                        }
                    } else {
                         return null // Index not a number
                    }
                }
                return current."$part" // Standard property access
            }
            return value != null ? value.toString() : fullMatch // If value is null, return the original placeholder
        } catch (Exception e) {
            // If placeholder resolution fails, return the original placeholder
            // For debugging: message.setProperty("TemplateError_${placeholderPath}", e.toString())
            return fullMatch
        }
    }
}

def Message processData(Message message) {

    def props = message.getProperties()
    def slurper = new JsonSlurper()

    // 1. Get Configuration JSON from MESSAGE BODY
    String configJsonString = message.getBody(java.lang.String)
    if (configJsonString == null || configJsonString.isEmpty()) {
        message.setProperty("ProcessingError", "Configuration JSON in message body is null or empty.")
        message.setProperty("ShouldProcess", false)
        // throw new IllegalStateException("Configuration JSON in message body is null or empty.")
        return message
    }
    def config = slurper.parseText(configJsonString) // Parse the configuration JSON
    
    // 2. Get Figaf Event Type and Entity Type from incoming payload
    // Get Incoming Figaf Payload from Exchange Property
    String figafJsonStringFromProperty = props.get("incomingFigafJsonBody") // Name of your property
    if (figafJsonStringFromProperty == null || figafJsonStringFromProperty.isEmpty()) {
        message.setProperty("ProcessingError", "Exchange property 'incomingFigafJsonBody' (Figaf payload) is not set or is empty.")
        message.setProperty("ShouldProcess", false)
        // throw new IllegalStateException("Exchange property 'incomingFigafJsonBody' (Figaf payload) is not set or is empty.")
        return message
    }
    def figafPayload = slurper.parseText(figafJsonStringFromProperty) // Parse incoming Figaf message

    //Get Figaf Event Type and Entity Type from Figaf payload   
    String figafEventType = figafPayload.eventType
    String figafEntityType = figafPayload.entityType
    message.setProperty("FigafEventType", figafEventType)
    message.setProperty("FigafEntityType", figafEntityType)
    message.setProperty("FigafEntityID", figafPayload.figafEntityId) // For logging/tracing

    // 3. Find Event-Specific Configuration
    def eventConfig = config.eventMappings[figafEventType]
    if (eventConfig == null) {
        message.setProperty("ProcessingError", "No configuration found in 'configJSON' for eventType: ${figafEventType}")
        message.setProperty("ShouldProcess", false)
        throw new IllegalStateException("Externalized parameter 'configJSON' is not configured or is empty.")
        return message
    }

    boolean isEnabled = eventConfig.enabled instanceof Boolean ? eventConfig.enabled : (eventConfig.enabled.toString().toLowerCase() == 'true')
    //message.setProperty("IsEnabled", isEnabled)
    if (!isEnabled) {
        message.setProperty("ShouldProcess", false)
        log.info("Processing skipped for eventType '${figafEventType}' as it is disabled in configuration.")
        return message
    }
    message.setProperty("ShouldProcess", true) // Indicates that processing should continue

    String actionType = eventConfig.actionType
    message.setProperty("ServiceNowActionType", actionType)

    // 4. Extract ServiceNow Issue Key to Update (from incoming Figaf payload)
    String servicenowIssueKeyToUpdate = null
    if (figafEntityType == "TICKET") {
        servicenowIssueKeyToUpdate = figafPayload.externalTicketId
    } else if (figafEntityType == "TRANSPORT") {
        if (figafPayload.webhookTicketDtoList && !figafPayload.webhookTicketDtoList.isEmpty()) {
            // Assuming the first ticket in the list is the relevant one
            servicenowIssueKeyToUpdate = figafPayload.webhookTicketDtoList[0].externalTicketId
        }
    }

    // Check if issue key is needed based on action type and if it was found
    if (servicenowIssueKeyToUpdate == null || servicenowIssueKeyToUpdate.isEmpty()) {
        message.setProperty("ProcessingError", "Could not determine ServiceNow Issue Key from Figaf payload for entity ${figafPayload.figafEntityId}. Required for actionType '${actionType}'.")
        message.setProperty("ShouldProcess", false)
        return message
    }
    
    message.setProperty("ServiceNowIssueKeyToUpdate", servicenowIssueKeyToUpdate)

    //-----------------------------------------------------------------------------------------------------------------------------
    //  Prepare Payloads for Api Calls
    //-----------------------------------------------------------------------------------------------------------------------------

    // 5. Prepare ServiceNow Transition Payload (if applicable)
    String servicenowTransitionPayloadJson = null
    if (eventConfig.transitionDetails && eventConfig.transitionDetails.id) {
        servicenowTransitionPayloadJson = eventConfig.transitionDetails.id.toString()
    }
    message.setProperty("ServiceNowTransitionPayload", servicenowTransitionPayloadJson)


    // 6. Prepare ServiceNow Comment Payload (if applicable)
    String servicenowCommentPayloadJson = null
    
    if (eventConfig.comment && eventConfig.comment instanceof String) {
        String rawCommentTemplate = eventConfig.comment
        String resolvedCommentText = resolveTemplate(rawCommentTemplate, figafPayload)
        
        // Optionally prepend global default header from configJSON
        String defaultHeader = config.globalServiceNowConfig?.defaultCommentHeader ?: ""
        if (!defaultHeader.isEmpty() && !resolvedCommentText.startsWith(defaultHeader)) {
            resolvedCommentText = defaultHeader + " " + resolvedCommentText
        }

        servicenowCommentPayloadJson = resolvedCommentText
    }
    message.setProperty("ServiceNowCommentPayload", servicenowCommentPayloadJson)

    // 6. Prepare ServiceNow Payload (if applicable)
    def builder = new JsonBuilder()

    if (actionType == "transitionAndComment") {
        builder {
            lane     servicenowTransitionPayloadJson
            comments servicenowCommentPayloadJson
        }
    } else if (actionType == "commentOnly") {
        builder {
            comments servicenowCommentPayloadJson
        }
    }

    message.setProperty("ServiceNowPatchBody", builder.toString())
    return message;
}