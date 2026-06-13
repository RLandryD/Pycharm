import com.sap.gateway.ip.core.customdev.util.Message
import javax.xml.parsers.DocumentBuilderFactory
import org.xml.sax.InputSource
import java.io.StringReader

def Message processData(Message message) {
    def body = message.getBody(java.lang.String)
    def messageLog = messageLogFactory.getMessageLog(message)

    if (body == null || messageLog == null) {
        messageLog.addAttachmentAsString("Empty body", "this message was empty", "text/plain")
    } else if (!body.contains("jobStartDate") && !body.contains("parentPosition")) {
        def xmlPayload = body.trim() // Remove leading and trailing whitespace

        // Log the XML payload before parsing
        if (messageLog != null) {
            messageLog.addAttachmentAsString("XML Payload Before Parsing", xmlPayload, "text/xml")
        }

        // Check if the XML payload is well-formed
        try {
            def dbFactory = DocumentBuilderFactory.newInstance()
            def dBuilder = dbFactory.newDocumentBuilder()
            def xmlInput = new InputSource(new StringReader(xmlPayload))
            def doc = dBuilder.parse(xmlInput)

            // Extract jobCodes and positionNumbers
            def jobCodes = doc.getElementsByTagName("JobCode")
            def formattedJobCodes = []
            for (int i = 0; i < jobCodes.getLength(); i++) {
                formattedJobCodes.add("'" + jobCodes.item(i).getTextContent() + "'")
            }
            def positionNumbers = doc.getElementsByTagName("PositionNumber")
            def formattedPositionNumbers = []
            for (int i = 0; i < positionNumbers.getLength(); i++) {
                formattedPositionNumbers.add("'" + positionNumbers.item(i).getTextContent() + "'")
            }

            // Log extracted values before setting properties
            if (messageLog != null) {
                messageLog.addAttachmentAsString("Extracted jobCodes", formattedJobCodes.join(','), "text/plain")
                messageLog.addAttachmentAsString("Extracted positionNumbers", formattedPositionNumbers.join(','), "text/plain")
            }

            // Set properties
            message.setHeader("jobCodes", formattedJobCodes.join(','))
            message.setHeader("positionNumbers", formattedPositionNumbers.join(','))

            // Log properties after setting them
            if (messageLog != null) {
                messageLog.addAttachmentAsString("Set jobCodes Property", formattedJobCodes.join(','), "text/plain")
                messageLog.addAttachmentAsString("Set positionNumbers Property", formattedPositionNumbers.join(','), "text/plain")
            }

            // Log the original body
            if (messageLog != null) {
                messageLog.addAttachmentAsString("JobRequisition", body, "text/xml")
            }
        } catch (Exception e) {
            throw new Exception("Error parsing XML: " + e.message, e)
        }
    } else if (body.contains("firstName")) {
        messageLog.addAttachmentAsString("User info", body, "text/xml")
    } else if (body.contains("EmpJob")) {
        messageLog.addAttachmentAsString("EmpJob", body, "text/xml")
    } else if (messageLog != null) {
        messageLog.addAttachmentAsString("FOJobCode", body, "text/xml")
    } else if (body.contains("<managerId/>") || body.contains("<userId/>") || body.contains("<localeLabel/>")) {
        messageLog.addAttachmentAsString("EmpJob empty", body, "text/xml")
    } else {
        if (body.contains("<localeLabel>Director/GM/NAM</localeLabel>") || 
            body.contains("<localeLabel>VIP/Non-Exec</localeLabel>") || 
            body.contains("<localeLabel>VP/Officer</localeLabel>")) {
            messageLog.addAttachmentAsString("Filter 2", body, "text/xml")
        }
    }

    return message
}
