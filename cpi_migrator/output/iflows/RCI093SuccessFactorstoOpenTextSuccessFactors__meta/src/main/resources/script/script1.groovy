import com.sap.gateway.ip.core.customdev.util.Message;

def Message processData(Message message) 
{
    def body = message.getBody(java.lang.String);
    def messageLog = messageLogFactory.getMessageLog(message);
    def pMap = message.getProperties();
    StringBuffer str = new StringBuffer();
    
    // Fetch properties
    def jobReqsManualRun = pMap.get("jobReqsManualRun");
    def ExcludeReqs = pMap.get("ExcludeReqs");
    def Manual_Run = pMap.get("Manual_Run");
    str.append("&\$filter = (status/id in '37901','37904') and (deleted eq '0')")
    
    if (Manual_Run && jobReqsManualRun) {
        def formattedJobReqs = jobReqsManualRun.trim().replace(",", "','");
        
        str.append(" and (jobReqId in '" + formattedJobReqs.toString() + "')");
    } else if (ExcludeReqs) {
        def excludedr = ExcludeReqs.trim().split(",");
        def exclusionFilter = excludedr.collect { "jobReqId ne '${it}'" }.join(" and ");
        
        str.append(" and " + exclusionFilter);
    }
    
    //tr.append("&\$orderby=jobReqId&\$top=2500&\$skip=2400");
    
    message.setProperty("JobReqs", str.toString());
    
    if (messageLog) {
        messageLog.addAttachmentAsString("JobReqs Property", message.getProperty("JobReqs"), "text/plain");
    }

    // Return the message object
    return message;
}
