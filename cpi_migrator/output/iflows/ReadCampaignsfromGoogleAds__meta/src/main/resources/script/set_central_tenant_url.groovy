import com.sap.gateway.ip.core.customdev.util.Message;
import groovy.xml.XmlUtil;

def Message processData(Message message) {
	
    def props = message.getProperties();


    //Check if use backup central tenant is set to true
    def useBackup = props.get("USE_BACKUP_TENANT").toString();
    def tenantHost = "l250279-iflmap.hcisbp.us3.hana.ondemand.com";
    if ("TRUE".equalsIgnoreCase(useBackup)){
        tenantHost = "l4057-iflmap.hcisbp.eu1.hana.ondemand.com";
    }

    def servicetUrl = "https://" + tenantHost + "/http/googleads/forwardToAPI";
    message.setHeader("serviceEndpoint",servicetUrl )
    return message;
}

