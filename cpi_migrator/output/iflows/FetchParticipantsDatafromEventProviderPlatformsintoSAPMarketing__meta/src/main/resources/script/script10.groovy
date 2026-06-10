import com.sap.gateway.ip.core.customdev.util.Message;
    import java.util.HashMap;
    
    def Message processData(Message message) {
        println "You can print and see the result in the console!"

        //Headers 
        def map = message.getHeaders();
        def ID = map.get("TragetIflowID");
        def param="";
        param= "/IntegrationDesigntimeArtifacts(Id='"+ID+"',Version='active')";
        message.setHeader("Param", param);
        
        return message;
        }
    