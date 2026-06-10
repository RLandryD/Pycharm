import com.sap.gateway.ip.core.customdev.util.Message;
import java.util.HashMap;

import com.sap.it.api.ITApiFactory;
import com.sap.it.api.securestore.SecureStoreService;
import com.sap.it.api.securestore.UserCredential;

def Message processData(Message message) {

    def service = ITApiFactory.getApi(SecureStoreService.class, null);
        
    //Set Client Id and Secret
    String Client_Id = new String("598700112637-qr3evdaj7ctgckak67l09rqqvc8nj5mq.apps.googleusercontent.com");
 	String Client_Secret = new String("PXbv4vo0c-yULeALwAzKm1z4");
    
    message.setProperty("CLIENT_ID", Client_Id );
    message.setProperty("CLIENT_SECRET", Client_Secret);
    
 
    try {
    	credential = service.getUserCredential("GoogleAdWordsCode");
    	if (credential == null){
        	throw new IllegalStateException("No credential found for alias 'GoogleAdWordsCode'");
    	}else{
    	    def code = String.valueOf(credential.getPassword());
    	    message.setProperty("CODE", code);
    	    
    	    // check authorization code is already used
    	    def map = message.getProperties();
            def old_hash = map.get("CODE_HASHCODE");
            if (old_hash != null) {
                def current_hash = code.hashCode().toString();
                message.setProperty("CURRENT_HASHCODE",current_hash);
                if (!current_hash.equals(old_hash)) {
                    message.setProperty("REQUEST_TOKEN","true");
                    message.setProperty("CODE_HASHCODE", current_hash);
                }else{
                    message.setProperty("REQUEST_TOKEN","false");
                }
            }
    	}
    } catch (Exception ex) {
 		throw new IllegalStateException("No credential found for alias 'GoogleAdWordsCode'");
    }
    

	return message;
}

