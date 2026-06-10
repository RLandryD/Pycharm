import com.sap.gateway.ip.core.customdev.util.Message;
    import java.util.HashMap;
    
    def Message processData(Message message) {

        //Headers 
        def map = message.getHeaders();
        def ID = map.get("PKID");
        def param="";
        param= "/IntegrationPackages('"+ID+"')/IntegrationDesigntimeArtifacts";
        message.setHeader("Param", param);
        
        return message;
        }
    