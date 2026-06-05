import com.sap.gateway.ip.core.customdev.util.Message;
import java.util.HashMap;

def Message processData(Message message) {	
	def body = message.getBody(java.lang.String) as String;
	def headers = message.getHeaders() as Map<String, Object>;
	def properties = message.getProperties() as Map<String, Object>;
	
	def propertiesAsString ="\n";
	properties.each{ it -> propertiesAsString = propertiesAsString + "${it}" + "\n" };
	
	def headersAsString ="\n";
	headers.each{ it -> headersAsString = headersAsString + "${it}" + "\n" };
	
	def messageLog = messageLogFactory.getMessageLog(message);
    if(messageLog != null && properties.get("enableLog") == "true"){
		messageLog.addAttachmentAsString("Log - Inbound Message before any processing",   "\n Properties \n ----------   \n" + propertiesAsString +
                                                            "\n Headers \n ----------   \n" + headersAsString +
		                                                    "\n Body \n ----------  \n\n" + body,
		                                                    "text/xml");
	}
	
	return message;
}