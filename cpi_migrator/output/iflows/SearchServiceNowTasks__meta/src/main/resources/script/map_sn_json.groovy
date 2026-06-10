import com.sap.gateway.ip.core.customdev.util.Message;
import groovy.json.JsonSlurper;
import groovy.json.JsonOutput;

def Message processData(Message message) {
    def body = message.getBody(String)
    def json = new JsonSlurper().parseText(body)

    def snUrl = message.getProperty("ServiceNow_URL")

    def stripHtml = { html ->
        html?.replaceAll(/<[^>]+>/, '')
            ?.replaceAll('&nbsp;', ' ')
            ?.replaceAll(/&#(\d+);/) { m ->
                Character.toChars(m[1].toInteger()).toString()
            }?.trim()
    }

    def result = json.result.collect { ticket ->
        [
            ID          : ticket.number,
            URL         : "${snUrl}/nav_to.do?uri=vtb_task.do?sys_id=${ticket.sys_id}",
            Title       : ticket.short_description,
            Description : stripHtml(ticket.description),
            Status      : ticket.state
        ]
    }

    message.setBody(JsonOutput.toJson(result))
    return message
}
