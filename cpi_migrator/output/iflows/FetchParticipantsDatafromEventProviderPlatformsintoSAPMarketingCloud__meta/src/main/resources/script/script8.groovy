import com.sap.gateway.ip.core.customdev.util.Message
import groovy.json.JsonBuilder

def Message processData(Message message) {
    def collectDataRecord = message.getHeaders().get('collectDataRecord')
    collectDataRecord.type = 'assemblyPartRelationship'
    message.setHeader('assemblyPartRelation', 'N')
    def edcjson = new JsonBuilder(collectDataRecord)
    message.setBody(edcjson.toPrettyString())
    return message
}
