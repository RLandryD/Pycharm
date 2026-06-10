import com.sap.gateway.ip.core.customdev.util.Message;
import groovy.xml.XmlUtil;

def Message processData(Message message) {
	
    def payload = message.getBody(java.lang.String.class)toString();
    def payloadParsed = new XmlSlurper().parseText( payload );
       
    //--------------------------------------------------------------------------
    // Analyze the incoming payloads to determine which type of operation to perform
    //--------------------------------------------------------------------------
    def userListMethod = payloadParsed.name();
    def userListOperator = payloadParsed.operations.operator.toString();;
    def userListType = payloadParsed.operations.operand.listType.toString();
  
    if ( userListMethod.equalsIgnoreCase("mutate") )  {
            message.setProperty("UserListOperation", "Mutate");
    }
    
    if ( userListMethod.equalsIgnoreCase("mutateMembers") ) {
            message.setProperty("UserListOperation", "MutateMembers");
    }
    
    if ( userListMethod.equalsIgnoreCase("get") ) {
            message.setProperty("UserListOperation", "Get");
    }

	return message;
}
