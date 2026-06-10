import com.sap.gateway.ip.core.customdev.util.Message;
import groovy.xml.XmlUtil;

def Message processData(Message message) {
	
    def props = message.getProperties();
    def payload = message.getBody(java.lang.String.class)toString();
    def payloadParsed = new XmlSlurper().parseText( payload );
    
		
    //TargetPartialFailure
    def partialFailure = payloadParsed.operationsHeader.partialFailure.toString(); 
    if (partialFailure != null) {
    	message.setProperty("TargetPartialFailure", partialFailure );
    }else{
        message.setProperty("TargetPartialFailure", "false");
    }
    
    //TargetValidateOnly
    def validateOnly = payloadParsed.operationsHeader.validateOnly.toString(); 
    if (validateOnly != null) {
    	message.setProperty("TargetValidateOnly", validateOnly );
    }else{
        message.setProperty("TargetValidateOnly", "false");
    }
    
    //TargetClientCustomerId
    def customerId = message.getProperty("CUSTOMER_ID").replaceAll("-","");
    
    def targetClientCustomerId = payloadParsed.operationsHeader.clientCustomerId.toString().replaceAll("-","");
    if (targetClientCustomerId == null  || targetClientCustomerId == '' ){
        targetClientCustomerId = customerId;
    }
    
    message.setProperty("CUSTOMER_ID", customerId.trim());
    message.setProperty("TargetClientCustomerId", targetClientCustomerId.trim());
    
    		
	return message;
}
