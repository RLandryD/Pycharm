import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {

    // Get the CSV data from the message body
    def body = message.getBody(java.lang.String) as String
    
    // Replace all occurrences of '&' with '&amp;'
    body = body.replaceAll("&", "&amp;")
    
    def lines = body.split("\n")
    def headers = lines[0].split(",").collect { it.trim().replaceAll("\\s+|\\.", "") }
    def xml = new StringBuilder("<JobRequisition>")

    // Process each line in the CSV
    for (int i = 1; i < lines.size(); i++) {
        def values = parseCSVLine(lines[i])
        xml.append("<JobRequisition>")
        for (int j = 0; j < headers.size(); j++) {
            def tagName
            switch (headers[j]) {
                case "RequisitionID":
                    tagName = "jobReqId"
                    break
                case "DocSignerFirstName":
                    tagName = "cust_DocumentSignerFirstName"
                    break
                case "DocSignerID":
                    tagName = "cust_DocumentSignerId"
                    break
                case "DocSignerLastName":
                    tagName = "cust_DocumentSignerLastName"
                    break
                case "DocSignerPositionTitle":
                    tagName = "cust_DocumentSignerPositionTittle"
                    break
                case "PositionState":
                    tagName = "cust_WLstateProvince"
                    break
                case "EmploymentAgreement":
                    tagName = "cust_employmentAgreement"
                    break
                default:
                    continue
            }

            /*if (tagName == "cust_employmentAgreement") {
                xml.append("<").append(tagName).append(">")
                xml.append("<PicklistOption><optionId>").append(values.size() > j ? values[j] : "").append("</optionId></PicklistOption>")
                xml.append("</").append(tagName).append(">")
            } else {*/
                xml.append("<").append(tagName).append(">")
                xml.append(values.size() > j ? values[j] : "")
                xml.append("</").append(tagName).append(">")
            //}
        }
        xml.append("</JobRequisition>")
    }
    xml.append("</JobRequisition>")

    // Set the transformed XML as the message body
    message.setBody(xml.toString())
    return message
}

// Function to parse a CSV line with quoted fields
def parseCSVLine(String line) {
    def values = []
    def matcher = line =~ /"([^"]*)"|([^,]+)/
    matcher.each { match ->
        if (match[1] != null) {
            values << match[1]
        } else {
            values << match[2]
        }
    }
    return values
}
