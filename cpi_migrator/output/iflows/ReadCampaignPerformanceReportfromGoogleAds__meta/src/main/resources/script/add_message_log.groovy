
import com.sap.gateway.ip.core.customdev.util.Message;
import java.util.HashMap;
def Message processData(Message message) {
        // do nothing
       return message;
}

// not in use
def Message logRequest(Message message){
    //Log request info: headers and body
    logHeaders(message);
    logRequestBody(message);
}

// not in use
// add log for headers with sensitive data masked
def Message logHeaders(Message message) {
       //Log headers related to central tenant processing
       def map = message.getHeaders();
       def messageLog = messageLogFactory.getMessageLog(message);
       def value = "";
       
       def headerValue = map.get("AuthorizationToken");
       if(headerValue!=null){
           //mask sensitive value
           value = "AuthorizationToken:" + "\t" + getMaskedString(headerValue) +"\n";
       }

       headerValue = map.get("TargetURL");
       if(headerValue!=null){
           value = value + "TargetURL:" + "\t" + headerValue +"\n";
       }       

       // save value to the attach file in message log
       if(value!=null){
        messageLog.addAttachmentAsString("Request_Headers", value , "text/plain");
       }

    return message; 

}

def String getMaskedString(String data){
    // provide masked string of related data
    StringBuilder sb = new StringBuilder();
    for(int i=0; i<data.length(); i++){
        if(i<2)
            sb.append(data[i]);
        else
            sb.append('*');
    }
    return sb.toString();
}

// not in use
def Message logMessageBody(Message message) {
    //Log current message body
    if (message!=null && message.getBody()!=null ) {

        def body_bytes = message.getBody(byte[].class);
       // save value to the attach file in message log
        def messageLog = messageLogFactory.getMessageLog(message);
        messageLog.addAttachmentAsString("Message_Body", new String(body_bytes) , "text/plain");
        }
    return message;
}

// add log for propery RequestBody, with file name Request_Body
// contains info of the original request sent to central tenant
def Message logRequestBody(Message message){
       //Log Property: RequestBody
       //- original request obody is stored as property
       def map = message.getProperties();
       def messageLog = messageLogFactory.getMessageLog(message);
       
       def value = map.get("RequestBody").toString();
       // save value to the attach file in message log
       if(value!=null){
        messageLog.addAttachmentAsString("Request_Body", value , "text/plain");
       }

    return message;    
}


// add log for exception details
def Message logException(Message message) {
    // get a map of properties
    def map = message.getProperties();
    
    // get an exception java class instance
    def ex = map.get("CamelExceptionCaught");
    if (ex!=null) {
        
        def messageLog = messageLogFactory.getMessageLog(message);
        // an http adapter throws an instance of org.apache.camel.component.ahc.AhcOperationFailedException
        if (ex.getClass().getCanonicalName().equals("org.apache.camel.component.ahc.AhcOperationFailedException")) {
            // save the http error response as a message attachment
            messageLog.addAttachmentAsString("Camel_Exception_Body", ex.getResponseBody(), "text/plain");
        }else{
            messageLog.addAttachmentAsString("Error_Response_info", ex.toString(), "text/plain");
        }
    }
    return message;
}

def Message logContext(Message message) {
    logRequestBody(message);
    logException(message);
    return message;
}