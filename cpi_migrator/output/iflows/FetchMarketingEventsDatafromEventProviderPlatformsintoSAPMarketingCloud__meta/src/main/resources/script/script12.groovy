import com.sap.gateway.ip.core.customdev.util.Message;

def Message processData(Message message) {
                
                // get a map of iflow properties
                def map = message.getProperties();
                def header = message.getHeaders();
                def ID = header.get("TragetIflowID");
                
               
                                                
                                    def response= "Iflow update failed";   
                                    // save the http error response as a message attachment 
                                                def messageLog = messageLogFactory.getMessageLog(message);
                                               messageLog.setStringProperty(ID,response);

                return message;
}