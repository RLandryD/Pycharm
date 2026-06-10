import com.sap.gateway.ip.core.customdev.util.Message;
import org.apache.commons.lang3.StringUtils;
import groovy.json.JsonSlurper;
import groovy.json.JsonBuilder;

def Message parse_response_20(Message message) {
    
    def jsonbody = message.getBody(java.lang.String) as String;
    def jsonSlurper = new JsonSlurper();
    def jsonResponse = jsonSlurper.parseText(jsonbody);
    def offlineUserDataJobsResourceName = jsonResponse.resourceName;
    message.setProperty("OfflineUserDataJobsResourceName",offlineUserDataJobsResourceName);
    
		
	return message;
}
