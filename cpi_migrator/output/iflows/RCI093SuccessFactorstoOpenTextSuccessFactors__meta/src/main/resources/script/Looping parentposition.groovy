import com.sap.gateway.ip.core.customdev.util.Message;

def Message processData(Message message) {
    def body = message.getBody(java.lang.String);
    def xml = new XmlParser().parseText(body);
    def messageLog = messageLogFactory.getMessageLog(message);
    def pMap = message.getProperties();
    def Manual_Run = pMap.get("Manual_Run");
    def jobReqsManualRun = pMap.get("jobReqsManualRun");
        
    if (body.contains("parentPosition")) {
        
        StringBuffer manPos = new StringBuffer();
		def pmp = xml.JobRequisition*.Position*.parentPosition*.Position*.Position*.parentPosition*.Position*.code*.text()
        def fpmp = pmp.collect { "'${it}'" }.join(',')
        manPos.append("&\$filter = position in " + fpmp.toString() + "");
        message.setProperty("EmpPosition", manPos.toString());

    
    } 

    return message;
}
