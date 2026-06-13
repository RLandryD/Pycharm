import com.sap.gateway.ip.core.customdev.util.Message;
import java.util.HashMap;
import groovy.xml.XmlUtil;
import groovy.util.XmlParser;
import groovy.xml.MarkupBuilder;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

def Message processData(Message message) {
   
    
    try{
    
    //logging body 
	def body = message.getBody(java.lang.String);
      
    def messageLog = messageLogFactory.getMessageLog(message)
        if(messageLog != null)
            {                              
                messageLog.addAttachmentAsString("ExceptionLog", body, "text/xml");
            }
       
       return message;
       
    }
    catch(Exception e)
    {
        //logging exception
        def messageLog = messageLogFactory.getMessageLog(message);
        if(messageLog != null)
        {
            messageLog.addAttachmentAsString("ExceptionLog", e.getMessage(), "plain/text");
        }
    }
}