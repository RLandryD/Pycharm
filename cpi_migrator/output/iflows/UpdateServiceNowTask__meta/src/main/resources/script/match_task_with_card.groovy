import com.sap.gateway.ip.core.customdev.util.Message
import groovy.json.JsonSlurper

def Message processData(Message message) {
    def body = message.getBody(String)
    def json = new JsonSlurper().parseText(body)
    
    def taskSysId = message.getProperty("ServiceNowTaskSysID")
    
    // Find object where task.value matches ServiceNowTaskSysID
    def match = json.result.find { it.task?.value == taskSysId }
    
    if (match) {
        message.setProperty("ServiceNowCardSysID", match.sys_id)
    } else {
        message.setProperty("ServiceNowCardSysID", null)
    }
    
    return message
}