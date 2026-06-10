import com.sap.gateway.ip.core.customdev.util.Message
import groovy.json.JsonSlurper

def Message processData(Message message) {
    Reader json = message.getBody(java.io.Reader)
    def input = new JsonSlurper().parse(json)
    message.setHeader('collectDataRecord', input)

    def urlExtend = new StringBuilder()
    if (input.type.equals('batch')) {
        urlExtend.append('productbatch/').append(input.batchId).append('/').append(input.manufacturerPartId).append('/').append(input.systemId)
        message.setHeader('genealogyFunction', urlExtend.toString())
    } else {
        urlExtend.append('serializedproduct/').append(input.partInstanceId).append('/').append(input.manufacturerPartId).append('/').append(input.systemId)
        message.setHeader('genealogyFunction', urlExtend.toString())
    }
    return message
}
