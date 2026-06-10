import com.sap.gateway.ip.core.customdev.util.Message
import groovy.json.JsonSlurper

def Message processData(Message message) {
    // Read body
    def body = message.getBody(String)
    def json = new JsonSlurper().parseText(body)
    
    // Get property to match
    def issueKey = message.getProperty("ServiceNowIssueKeyToUpdate")
    
    // Find matching sys_id
    def match = json.result.find { it.number == issueKey }
    if (match) {
        message.setProperty("ServiceNowTaskSysID", match.sys_id)
    } else {
        message.setProperty("ServiceNowTaskSysID", null)
    }
    
    return message
}