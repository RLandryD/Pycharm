
import com.sap.gateway.ip.core.customdev.util.Message;
import java.util.HashMap;

    


def Message processData(Message message) {
	def messageLog = messageLogFactory.getMessageLog(message);
	def Id = message.getHeaders().get("TragetIflowID");
	
	def error = Id + " already exists in a different package.";
        if(messageLog != null){
                messageLog.addCustomHeaderProperty("Transport Failed", error);   
        }
        return message;
}