import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {
    // Get the input XML from the message body
    def inputXml = message.getBody(java.lang.String) as String

    // Parse the input XML
    def inputXmlParsed = new XmlSlurper().parseText(inputXml)

    // Initialize the new XML structure
    def newXml = new StringBuilder("<?xml version='1.0' encoding='UTF-8'?><JobRequisition>")

    // Iterate over each JobRequisition entry
    inputXmlParsed.JobRequisition.each { jobReq ->
        def jobReqId = jobReq.jobReqId.text()

        // Append the new JobRequisition entry with only jobReqId populated
        newXml.append("""
        <JobRequisition>
            <jobReqId>${jobReqId}</jobReqId>
            <cust_DocumentSignerId> </cust_DocumentSignerId>
            <cust_DocumentSignerFirstName> </cust_DocumentSignerFirstName>
            <cust_DocumentSignerLastName> </cust_DocumentSignerLastName>
            <cust_DocumentSignerPositionTittle> </cust_DocumentSignerPositionTittle>
            <cust_WLstateProvince> </cust_WLstateProvince>
            <cust_employmentAgreement> </cust_employmentAgreement>
        </JobRequisition>
        """)
    }

    // Close the root JobRequisition tag
    newXml.append("</JobRequisition>")

    // Set the new XML as the message body
    message.setBody(newXml.toString())
    return message
}
