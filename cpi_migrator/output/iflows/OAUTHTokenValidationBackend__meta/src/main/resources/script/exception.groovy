import com.sap.gateway.ip.core.customdev.util.Message;
import java.util.HashMap;

 

def Message processData(Message message) {
  
   def map = message.getProperties();
   def ex = map.get("CamelExceptionCaught");
   
   
    if (ex!=null) {
def body = message.getBody();
message.setBody(ex.getResponseBody());

 

//Headers
message.setHeader("STATUS_CODE", ex.getStatusCode());
message.setHeader("STATUS_TEXT", ex.getStatusText());

    }

       
return message;

}