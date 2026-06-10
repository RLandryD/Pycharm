
import com.sap.gateway.ip.core.customdev.util.Message;



Message checkUpsertResult(Message message) {
    def idx = message.getProperty("CamelSplitIndex")
    

    def body = message.getBody(String.class)
    if (body.isNumber()){
        message.setProperty("allRecordsSync", message.getProperty("currentPageCount"+idx) <= Integer.parseInt(body))
        message.setProperty("hasError", false)
    } else {
        message.setProperty("allRecordsSync", false)
        message.setProperty("hasError", true)
        
    }

   
    return message
}

Message countProcessed(Message message){
    def body = message.getBody(String.class)
    def xml = new XmlSlurper().parseText(body)
    

    int sum = 0
    xml.Processed?.each{
        p -> sum = sum + Integer.parseInt(p.text())
    }

    message.setBody(sum)
    return message
}