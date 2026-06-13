import com.sap.gateway.ip.core.customdev.util.Message;

def Message processData(Message message) {
    def body = message.getBody(java.lang.String);
    def xml = new XmlParser().parseText(body);
    def messageLog = messageLogFactory.getMessageLog(message);
    def pMap = message.getProperties();
    def Manual_Run = pMap.get("Manual_Run");
    def jobReqsManualRun = pMap.get("jobReqsManualRun");

    if (body.contains("Director/GM/NAM") || body.contains("VIP/Non-Exec") || body.contains("VIP/Officer")) {
        StringBuffer uID = new StringBuffer();
        StringBuffer finalPosition = new StringBuffer();
        uID.append("&\$filter = (status in 't','f')")
        
        if (Manual_Run && jobReqsManualRun){
        def userId = xml.JobRequisition*.Position*.EmpJob*.userId*.text()
        def formatteduserId = userId.collect { "'${it}'" }.join(',');
        uID.append(" and userId in " + formatteduserId.toString() );
        
        } 
        message.setProperty("userId", uID.toString());
            
        
        
        
        def fpos = xml.JobRequisition*.Position*.code*.text()
        def formattedfpos = fpos.collect { "'${it}'" }.join(',');
        finalPosition.append("&\$filter = code in " + formattedfpos.toString() );
        message.setProperty("finalPosition", finalPosition.toString());
        
        StringBuffer posManPos = new StringBuffer();
        def pManagerP = xml.JobRequisition*.Position*.EmpJob*.position*.text()
        def formattedmanPos = pManagerP.collect { "'${it}'" }.join(',');
        posManPos.append("&\$filter = code in " + formattedmanPos.toString() + "");
        message.setProperty("PosManagerPos", posManPos.toString());
        
        def parentPosition = xml.JobRequisition*.Position*.parentPosition*.Position*.code*.text()
        def formattedpPosition = parentPosition.collect { "'${it}'" }.join(',');
        
        def parentPMessage = "&\$filter = code in " + formattedpPosition.toString()
        parentPMessage = parentPMessage.replaceAll('\\[', '').replaceAll('\\]', '')
        message.setProperty("MessagePosition", formattedpPosition.toString());
        
        
        def EmpPosition = "&\$filter = position in " + formattedpPosition.toString()
        EmpPosition = EmpPosition.replaceAll('\\[', '').replaceAll('\\]', '')
        message.setProperty("EmpPosition", EmpPosition.toString());
        //messageLog.addAttachmentAsString("User info", body, "text/xml")
    } else if (body.contains("parentPosition")) {
        
        def parentPosition = xml.JobRequisition*.Position*.parentPosition*.Position*.code*.text()
        def formattedpPosition = parentPosition.collect { "'${it}'" }.join(',');
        
        def parentPMessage = "&\$filter = code in " + formattedpPosition.toString()
        parentPMessage = parentPMessage.replaceAll('\\[', '').replaceAll('\\]', '')
        message.setProperty("MessagePosition", parentPMessage.toString());
        message.setProperty("userId", "");
        
        def EmpPosition = "&\$filter = position in " + formattedpPosition.toString()
        EmpPosition = EmpPosition.replaceAll('\\[', '').replaceAll('\\]', '')
        message.setProperty("EmpPosition", EmpPosition.toString());
        
        if (Manual_Run && jobReqsManualRun){
            StringBuffer manPos = new StringBuffer();
            def pmp = xml.JobRequisition*.Position*.parentPosition*.Position*.code*.text()
            def fpmp = pmp.collect { "'${it}'" }.join(',')
            manPos.append("&\$filter = position in " + fpmp.toString() + "");
            message.setProperty("ManagerPos", manPos.toString());
        }
    
    } else if (body.contains("EmpJob")) {
        if (messageLog != null) {
            message.setProperty("MessagePosition", "");
            message.setProperty("EmpPosition", "");
            //messageLog.addAttachmentAsString("Employee Information", body, "text/xml");
        }
    } else {
        if (messageLog != null && Manual_Run && jobReqsManualRun) {
            StringBuffer Jcode = new StringBuffer();
            StringBuffer posnum = new StringBuffer();
            StringBuffer manPos = new StringBuffer();
            
            def positionNumbers = xml.JobRequisition*.positionNumber*.text()
            def formattedPosition = positionNumbers.collect { "'${it}'" }.join(',')
            
            def pmp = xml.JobRequisition*.Position*.parentPosition*.Position*.code*.text()
            def fpmp = pmp.collect { "'${it}'" }.join(',')
            
            
            posnum.append("&\$filter = code in " + formattedPosition.toString() + "");
            manPos.append("&\$filter = position in " + fpmp.toString() + "");
            
            def jobCode = xml.JobRequisition*.jobCode*.text()
            def formattedjobCode = jobCode.collect { "'${it}'" }.join(',');
            formattedjobCode = formattedjobCode.replaceAll('\\[', '').replaceAll('\\]', '')
            Jcode.append("&\$filter = externalCode in " + formattedjobCode.toString() + "");
            message.setProperty("MessagePosition", "");
            message.setProperty("EmpPosition", "");
            message.setProperty("positionNumber", posnum.toString());
            message.setProperty("jobCode", Jcode.toString());
            message.setProperty("ManagerPos", manPos.toString());
            
            
        } else {
            message.setProperty("positionNumber", "");
            message.setProperty("jobCode", "");
            message.setProperty("ManagerPos","");
        }
    }

    return message;
}
