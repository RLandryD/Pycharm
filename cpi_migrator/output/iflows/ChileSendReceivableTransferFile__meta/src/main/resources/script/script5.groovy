import com.sap.gateway.ip.core.customdev.util.Message
import groovy.json.JsonBuilder
import groovy.json.JsonSlurper

def Message processData(Message message) {
    def origEvent = message.getProperties().get('receiveEvents')
    def input = new JsonSlurper().parseText(origEvent)
    def globalAssetId = UUID.randomUUID().toString()

    // Handle Receive Events
    if (input.ReceiveEvents) {
        appendKeyAssignment(input.ReceiveEvents, 'CATENA_X_BATCH', globalAssetId)
    }
    if (input.ReceiveSerialNumberEvents) {
        appendKeyAssignment(input.ReceiveSerialNumberEvents.SerialNumbers, 'CATENA_X_VENDOR_PART', globalAssetId)
    }

    def aasJson = new JsonBuilder(input)
    message.setBody(aasJson.toPrettyString())
    return message
}

def appendKeyAssignment(def event, def qualifier, def value) {
    if (event.KeyAssignments instanceof List) {
        event.KeyAssignments << createKeyAssignmentMap(qualifier, value)
    } else {
        event.KeyAssignments = createKeyAssignmentMap(qualifier, value)
    }
}

def createKeyAssignmentMap(def qualifier, def value) {
    def keyAssignmentMap = [:]
    keyAssignmentMap.Qualifier = qualifier
    keyAssignmentMap.Value = value
    return keyAssignmentMap
}
