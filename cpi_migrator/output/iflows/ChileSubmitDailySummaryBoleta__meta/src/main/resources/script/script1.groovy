import com.sap.gateway.ip.core.customdev.util.Message
import groovy.json.JsonBuilder
import groovy.json.JsonSlurper

def Message processData(Message message) {
    def payload = message.getHeaders().get('payload')
    Reader json = message.getBody(java.io.Reader)
    def input = new JsonSlurper().parse(json)
    if (input.Messages.Message1) {
        if (input.Messages.Message1.ReceiveEvents) {
            payload['n0:MaterialTraceabilityEventNotificationMessage'].EventPackage.ReceiveEvents = input.Messages.Message1.ReceiveEvents
        }else if (input.Messages.Message1.ReceiveSerialNumberEvents) {
            payload['n0:MaterialTraceabilityEventNotificationMessage'].EventPackage.ReceiveSerialNumberEvents = input.Messages.Message1.ReceiveSerialNumberEvents
        }
    }

    // Store updated Payload
    message.setHeader('payload', payload)
    def aasJson = new JsonBuilder(payload)
    message.setBody(aasJson.toPrettyString())
    return message
}
