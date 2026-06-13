import com.sap.gateway.ip.core.customdev.util.Message;
import java.util.HashMap;
import java.io.*;

def Message processData(Message message){
      
      //Retrieve Body 
      def body = message.getBody(java.lang.String);
      body = body.replaceAll("&","&amp;");
      
      //Function to fetch application url of HCI Tenant
      String appUrl = System.getenv("HC_APPLICATION_URL");
      
      //Logic to retrieve only Tenant Name      
      String TMN_ShortName = appUrl.substring(8,15);
      
      //Set the Tenant name to Property
      message.setProperty("P_Tenant_Name",TMN_ShortName);
            
      message.setBody(body);
            
      return message;
}