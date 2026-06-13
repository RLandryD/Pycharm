import com.sap.gateway.ip.core.customdev.util.Message;

def Message processData(Message message) {
    // Get the body of the incoming message
    def body = message.getBody(String);
    
    // Replace '> <' with '><'
    def modifiedBody = body.replaceAll("> <", "><")
     modifiedBody = modifiedBody.replaceAll(' \\(.*\\)', '')
    message.setProperty("updated body", modifiedBody.toString())
    
    // Parse the XML manually using XmlSlurper
    def root = new XmlSlurper().parseText(modifiedBody)
    
    // Find and remove duplicate entries
    def jobRequisitions = root.JobRequisition
    def uniqueEntries = []
    def duplicates = []
    
    jobRequisitions.each { jobReq ->
        def jobReqId = jobReq.jobReqId.text()
        def isDuplicate = uniqueEntries.find { it.jobReqId.text() == jobReqId }
        
        if (isDuplicate) {
            // Check if the current jobReq has missing information
            def hasMissingInfo = jobReq.'**'.findAll { it.text().trim().isEmpty() }.size() > 0
            
            if (hasMissingInfo) {
                duplicates << jobReq
            } else {
                // Remove the previous duplicate and keep the current one
                uniqueEntries.remove(isDuplicate)
                uniqueEntries << jobReq
            }
        } else {
            uniqueEntries << jobReq
        }
    }
    
    // Remove duplicates from the root
    duplicates.each { root.remove(it) }
    
    // Convert the modified XML back to a string
    def resultXml = groovy.xml.XmlUtil.serialize(root)
    
    // Set the modified body back to the message
    message.setBody(resultXml)
    
    return message
}
