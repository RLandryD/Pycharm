import groovy.json.*;
import com.sap.it.api.pd.PartnerDirectoryService;
import com.sap.it.api.ITApiFactory;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import org.osgi.framework.FrameworkUtil;
import com.sap.gateway.ip.core.customdev.util.Message;
import com.sap.it.op.b2b.monitor.api.*;
import com.sap.it.op.b2b.monitor.api.events.*;
import java.nio.charset.Charset;
import java.nio.charset.StandardCharsets;
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import com.sap.it.api.pd.BinaryData;
import groovy.util.XmlSlurper
import javax.xml.xpath.XPathFactory
import javax.xml.xpath.XPathConstants
import org.w3c.dom.Document
import javax.xml.parsers.DocumentBuilderFactory
import org.xml.sax.InputSource
import javax.xml.namespace.NamespaceContext
import javax.xml.xpath.XPathExpressionException
import com.sap.it.api.nrc.NumberRangeConfigurationService

// Identifier name, sender type system and scheme code are all matched
def findGroupIdentifier(String singleIdentifier, String schemeCode, String sndTypeSystem, PartnerDirectoryService service) {
    if (singleIdentifier == "") {
        return [:]
    }
    
    if (sndTypeSystem == "UN-EDIFACT") {
        sndTypeSystem = "UNEDIFACT"
    } else if (sndTypeSystem == "ASC-X12") {
        sndTypeSystem = "ASC_X12"
    } else if (sndTypeSystem == "GS1-XML") {
        sndTypeSystem = "GS1_XML"
    }

    if (schemeCode == "") {
        schemeCode = "N/A"
    }

    def jsonSlurper = new JsonSlurper()
    // extend the id of identifier group to the new format <Identifier Name>__<Type System>__<Scheme Code>
    // '/' is not allowed in Pid / Id, so use "NA" instead
    def schemeInkey = schemeCode
    if (schemeInkey == "N/A") {
        schemeInkey = "NA"
    }
    // if the new format of Id is available, will use the related info to calculate runtime Pid. In this case, the previous format of identification name will never be checked.
    String key = String.format("%s__%s__%s", singleIdentifier, sndTypeSystem, schemeInkey)
    def content = service.getParameter(key, "SAP_TPM_GROUP_ID_LIST" , String.class)
    // if not available, replace subtype with UNEDIFACT for the second try
    if (content == null && sndTypeSystem in ["EANCOM", "ODETTE_EDIFACT", "VDA_EDIFACT"]) {
        sndTypeSystem = "UNEDIFACT";
        key = String.format("%s__%s__%s", singleIdentifier, sndTypeSystem, schemeInkey)
        content = service.getParameter(key, "SAP_TPM_GROUP_ID_LIST" , String.class)
    }
    // if the new format is not available, try with the previous format of identification name
    if (content == null) {
        content = service.getParameter(singleIdentifier, "SAP_TPM_GROUP_ID_LIST" , String.class)
    }
    if (content == null) {
        return [:]
    }
    def json = jsonSlurper.parseText(content)
    if (json.TypeSystemId != sndTypeSystem || json.SchemeCode != schemeCode) {
        return [:]
    }
    return ["GroupId": json.GroupId, "Subsidiary": json.Subsidiary]
}


String calcHashedPid(String plainPid) {
    try {
        def matchStringLowerCase = plainPid.toLowerCase()
        MessageDigest digest = MessageDigest.getInstance("MD5")
        digest.update(matchStringLowerCase.bytes)
        return "SAP_TPM_" + new BigInteger(1, digest.digest()).toString(16).padLeft(32, '0')
    } catch (NoSuchAlgorithmException e) {
        log.error('Error creating MD5 hash', e)
        throw new IllegalStateException("PartnerID hash can't be generated.")
    }
}

// return null if the corresponding hashed pis is not available from Partner Directory
String getValidHashedPid(String plainPid, PartnerDirectoryService service) {
    partnerID = calcHashedPid(plainPid)
    def recDocumentStandard = service.getParameter("SAP_EDI_REC_Document_Standard", partnerID , String.class)
    if (recDocumentStandard == null) {
        return null
    }
    return partnerID
}

def calcRuntimePid(String pidTemplate, String pidTemplateForEDISubset, String sndId, String sndScheme, String recId, String recScheme, String sndTypeSystem, PartnerDirectoryService service, messageLog, seq, Message message) {
    
    def defaultPid = String.format(pidTemplate, sndId, recId)
    // single + single
    def plainPid = defaultPid
    def hashedPid = getValidHashedPid(plainPid, service)
    def isUNEDIFACT = "false";
    if (hashedPid == null && sndTypeSystem in ["EANCOM", "ODETTE_EDIFACT", "VDA_EDIFACT"]) {
        plainPid = String.format(pidTemplateForEDISubset, sndId, recId)
        hashedPid = getValidHashedPid(plainPid, service)
        if (hashedPid != null) isUNEDIFACT = "true";
    }
    if (hashedPid != null) {
        if (messageLog != null) {
            messageLog.setStringProperty(String.format("Evaluate Pid (%s)", seq), "Partner ID " + calcHashedPid(plainPid) + " is found.\nPartner Directory Lookup Keys: " + plainPid + "\n")
        }
        return ["hashedPid":hashedPid, "plainPid": plainPid, "isUNEDIFACT": isUNEDIFACT]
    }

    // group + single
    def sndMap = findGroupIdentifier(sndId, sndScheme, sndTypeSystem, service)
    def actualSndId = sndMap.GroupId
    if (actualSndId != null) {
        plainPid = String.format(pidTemplate, actualSndId, recId)
        hashedPid = getValidHashedPid(plainPid, service)
        if (hashedPid == null && sndTypeSystem in ["EANCOM", "ODETTE_EDIFACT", "VDA_EDIFACT"]) {
            plainPid = String.format(pidTemplateForEDISubset, actualSndId, recId)
            hashedPid = getValidHashedPid(plainPid, service)
            if (hashedPid != null) isUNEDIFACT = "true";
        }
        if (hashedPid != null) {
            if (messageLog != null) {
                messageLog.setStringProperty(String.format("Evaluate Pid (%s)", seq), "Partner ID " + calcHashedPid(plainPid) + " is found.\nPartner Directory Lookup Keys: " + plainPid + "\n")
            }
            return ["hashedPid":hashedPid, "plainPid": plainPid, "subsidiary": sndMap.Subsidiary, "isUNEDIFACT": isUNEDIFACT]
        }
    }

    // single + group
    def recMap = findGroupIdentifier(recId, recScheme, sndTypeSystem, service)
    def actualRecId = recMap.GroupId
    if (actualRecId != null) {
        plainPid = String.format(pidTemplate, sndId, actualRecId)
        hashedPid = getValidHashedPid(plainPid, service)
        if (hashedPid == null && sndTypeSystem in ["EANCOM", "ODETTE_EDIFACT", "VDA_EDIFACT"]) {
            plainPid = String.format(pidTemplateForEDISubset, sndId, actualRecId)
            hashedPid = getValidHashedPid(plainPid, service)
            if (hashedPid != null) isUNEDIFACT = "true";
        }
        if (hashedPid != null) {
            if (messageLog != null) {
                messageLog.setStringProperty(String.format("Evaluate Pid (%s)", seq), "Partner ID " + calcHashedPid(plainPid) + " is found.\nPartner Directory Lookup Keys: " + plainPid + "\n")
            }
            return ["hashedPid": hashedPid, "plainPid": plainPid, "isUNEDIFACT": isUNEDIFACT]
        }
    }

    // group + group
    if (actualSndId != null && actualRecId != null) {
        plainPid = String.format(pidTemplate, actualSndId, actualRecId)
        hashedPid = getValidHashedPid(plainPid, service)
        if (hashedPid == null && sndTypeSystem in ["EANCOM", "ODETTE_EDIFACT", "VDA_EDIFACT"]) {
            plainPid = String.format(pidTemplateForEDISubset, actualSndId, actualRecId)
            hashedPid = getValidHashedPid(plainPid, service)
            if (hashedPid != null) isUNEDIFACT = "true";
        }
        if (hashedPid != null) {
            if (messageLog != null) {
                messageLog.setStringProperty(String.format("Evaluate Pid (%s)", seq), "Partner ID " + calcHashedPid(plainPid) + " is found.\nPartner Directory Lookup Keys: " + plainPid + "\n")
            }
            return ["hashedPid":hashedPid, "plainPid": plainPid, "subsidiary": sndMap.Subsidiary, "isUNEDIFACT": isUNEDIFACT]
        }
    }
    
    if (messageLog != null) {
        messageLog.setStringProperty(String.format("Evaluate Pid (%s)", seq), "Partner ID " + calcHashedPid(defaultPid) + " is not available.\nPartner Directory Lookup Keys: " + defaultPid + "\n")
    }
    
    // if not found PID in partner directory, still need set plainPid in header
    message.setHeader("SAP_Partner_ID_MATCH_String", plainPid);

    return null
}

def processEdifactSenderFunctionalAck(String sndAckAS2CreateAck, String sndAckAS2InterchangeNumber, String sndAckAS2UniqueInterchangeNumber, String sndAckAS2NumberRange, Message message) {
    if (sndAckAS2CreateAck == "checkEDIEnvelop" || sndAckAS2CreateAck == "required") {
        if (sndAckAS2InterchangeNumber == null ) {
            sndAckAS2InterchangeNumber = "useFromEDIMessage";
        }
        message.setProperty("SAP_EDISPLITTER_EDIFACT_INTERCHANGE_NUMBER", sndAckAS2InterchangeNumber);
        message.setProperty("SAP_EDISPLITTER_EDIFACT_UNIQUE_INTERCHANGE_NUMBER", "required");
        if (sndAckAS2InterchangeNumber == "numberRange") {
            if (sndAckAS2UniqueInterchangeNumber == null) {
                sndAckAS2UniqueInterchangeNumber = "required";
            }
            message.setProperty("SAP_EDISPLITTER_EDIFACT_UNIQUE_INTERCHANGE_NUMBER", sndAckAS2UniqueInterchangeNumber);
            if (sndAckAS2NumberRange == null) {
                throw new IllegalStateException("Number Range for Intercange Number when creating Functional Acknowledgement is not available");
            }
            message.setProperty("SAP_EDISPLITTER_EDIFACT_NUMBER_RANGE", sndAckAS2NumberRange);
        }
    }
}

def processX12SenderFunctionalAck(String sndAckAS2CreateAck, String sndAckAS2InterchangeNumber, String sndAckAS2UniqueInterchangeNumber, String sndAckAS2NumberRange, Message message) {
    if (sndAckAS2CreateAck == "checkEDIEnvelop" || sndAckAS2CreateAck == "required") {
        if (sndAckAS2InterchangeNumber == null ) {
            sndAckAS2InterchangeNumber = "useFromEDIMessage";
        }
        message.setProperty("SAP_EDISPLITTER_X12_INTERCHANGE_NUMBER", sndAckAS2InterchangeNumber);
        message.setProperty("SAP_EDISPLITTER_X12_UNIQUE_INTERCHANGE_NUMBER", "required");
        if (sndAckAS2InterchangeNumber == "numberRange") {
            if (sndAckAS2UniqueInterchangeNumber == null) {
                sndAckAS2UniqueInterchangeNumber = "required";
            }
            message.setProperty("SAP_EDISPLITTER_X12_UNIQUE_INTERCHANGE_NUMBER", sndAckAS2UniqueInterchangeNumber);
            if (sndAckAS2NumberRange == null) {
                throw new IllegalStateException("Number Range for Intercange Number when creating Functional Acknowledgement is not available");
            }
            message.setProperty("SAP_EDISPLITTER_X12_NUMBER_RANGE", sndAckAS2NumberRange);
        }
    }
}

def Message handleSourceEncoding(Message message) {
    def properties = message.getProperties();
    def sourceEncoding = properties.get("SAP_TPM_SND_Source_Encoding");
    def targetEncoding = properties.get("SAP_TPM_REC_Target_Encoding");
    
    if (null == sourceEncoding || "" == sourceEncoding) {
        return message; // No need to handle encoding
    }
    
    // unifined place to set encoding for b2b payload display
    message.setProperty("SAP_TPM_SND_B2B_Payload_Source_Encoding", sourceEncoding);
    
    if (isSourceXml(message) && isTargetXml(message)) {
        // Will handle conversion in target code page if needed
        return message;
    }
    
    if (isSourceXml(message) && isTargetEdi(message)) {
        // Will handle conversion in target code page if needed
        return message;
    }
    
    if (isSourceEdi(message) && isTargetXml(message)) {
        // convert to UTF-8 for unifined handling
        convertEncoding(message, sourceEncoding, 'UTF-8');
    } 
    
    if (isSourceEdi(message) && isTargetEdi(message)) {
        convertEncoding(message, sourceEncoding, 'UTF-8');
    }
    
    return message;
}

def void convertEncoding(Message message, String sourceEncoding, String targetEncoding) {
    validateCharset(sourceEncoding);
    validateCharset(targetEncoding);
    
    byte[] rawBytes = message.getBody(byte[].class);
    
    // Step 1: Decode bytes using  to get Java String
    String decoded = new String(rawBytes, Charset.forName(sourceEncoding));
    
    // Step 2: Re-encode to target bytes
    byte[] targetBytes = decoded.getBytes(Charset.forName(targetEncoding));

    message.setBody(targetBytes);
}

def void validateCharset(String encoding) {
    try {
        Charset.forName(encoding) // Attempt to resolve the charset
    } catch (Exception e) {
        throw new IllegalStateException("Invalid encoding value: ${encoding}")
    }
}

def boolean isSourceEdi(Message message) {
    def headers = message.getHeaders();
    def payloadFormat = headers.get("SAP_EDI_Payload_Format") as String;
    return 'EDI_FLAT' == payloadFormat; 
}

def boolean isSourceXml(Message message) {
    return !isSourceEdi(message);
}

def boolean isTargetEdi(Message message) {
    def payloadFormat = message.getProperty("SAP_EDI_REC_Payload_Format");
    return 'EDI_FLAT' == payloadFormat; 
}

def boolean isTargetXml(Message message) {
    return !isTargetEdi(message);
}

def translateToBoolean(String input) {
    if (input == "0") {
        return "false"
    } else {
        return "true"
    }
}

def translateAuthentication(String input) {
    if (input == "user_password") {
        return "user"
    } else if (input == "public_key") {
        return "key"
    } else if (input == "dual") {
        return "dual"
    } else {
        throw new IllegalStateException("Unknown authentication method")
    }
}

def translateProxyType(String input) {
    if (input == "sapcc") {
        return "onPremise"
    } else if (input == "none") {
        return "internet"
    } else {
        throw new IllegalStateException("Unknown proxy type")
    }
}

def Message processData(Message message) {
    def service = ITApiFactory.getApi(PartnerDirectoryService.class, null);
    if(service == null){
        throw new IllegalStateException("Partner Directory Service not found");
    }

    def headers                      =   message.getHeaders();
    def sndAdapterType               =   headers.get("SAP_COM_SND_Adapter_Type");
    def sndDocumentStandard          =   headers.get("SAP_EDI_Document_Standard");
    def sndInterchangeControlNr      =   headers.get("SAP_EDI_Interchange_Control_Number");
    // change to SAP_EDI_Message_Control_Number
    def sndMessageControlNr          =   headers.get("SAP_EDI_Message_Control_Number");
    def sndMsgControllingAgency      =   headers.get("SAP_EDI_Message_Controlling_Agency");
    def sndMessageNamespace          =   headers.get("SAP_EDI_Message_Namespace");
    def sndMessageRelease            =   headers.get("SAP_EDI_Message_Release");
    def sndMessageType               =   headers.get("SAP_EDI_Message_Type");
    def sndMessageVersion            =   headers.get("SAP_EDI_Message_Version");
    def sndPayloadFormat             =   headers.get("SAP_EDI_Payload_Format");
    def sndReceiverId                =   headers.get("SAP_EDI_Receiver_ID");
    def sndReceiverIdQualifier       =   headers.get("SAP_EDI_Receiver_ID_Qualifier");
    def sndReceiverPartnerType       =   headers.get("SAP_EDI_Receiver_Partner_Type");
    def sndReceiverSystemId          =   headers.get("SAP_EDI_GS_Receiver_ID");
    def sndReceiverSystemIdQualifier =   headers.get("SAP_EDI_GS_Receiver_ID_Qualifier");
    def sndSenderId                  =   headers.get("SAP_EDI_Sender_ID");
    def sndSenderIdQualifier         =   headers.get("SAP_EDI_Sender_ID_Qualifier");
    def sndSenderPartnerType         =   headers.get("SAP_EDI_Sender_Partner_Type");
    def sndSenderSystemId            =   headers.get("SAP_EDI_GS_Sender_ID");
    def sndSenderSystemIdQualifier   =   headers.get("SAP_EDI_GS_Sender_ID_Qualifier");
    def sndSyntaxId                  =   headers.get("SAP_EDI_Syntax_Identifier");
    def sndSyntaxVersionId           =   headers.get("SAP_EDI_Syntax_Version");
    def sndTestIndicator             =   headers.get("SAP_EDI_Usage_Indicator");
    def sndMDNPayload                =   headers.get("SAP_TPM_Sender_MDN_Payload");
    def sndMDNType                   =   headers.get("SAP_TPM_Sender_MDN_Type");
    def partnerID                    =   "";

    if (sndAdapterType == null){
        throw new IllegalStateException("Mandatory value sndAdapterType not provided.");
    }
    else{
        sndAdapterType = sndAdapterType.trim();
    }

    if (sndDocumentStandard == null){
        throw new IllegalStateException("Mandatory value sndDocumentStandard not provided.");
    }
    else{
        sndDocumentStandard = sndDocumentStandard.trim();
    }

    if (sndMessageNamespace == null){
        sndMessageNamespace = "";
    }
    else{
        sndMessageNamespace = sndMessageNamespace.trim();
    }

    if (sndMessageVersion == null){
        sndMessageVersion = "";
    }
    else{
        sndMessageVersion = sndMessageVersion.trim();
    }

    if (sndMessageRelease == null){
        sndMessageRelease = "";
    }
    else{
        sndMessageRelease = sndMessageRelease.trim();
    }

    if (sndMessageType == null){
        throw new IllegalStateException("Mandatory value sndMessageType not provided.");
    }
    else{
        sndMessageType = sndMessageType.trim();
    }

    if (sndReceiverId == null){
        sndReceiverId = ""
        // throw new IllegalStateException("Mandatory value sndReceiverId not provided.");
    }
    else{
        sndReceiverId = sndReceiverId.trim();
    }

    if (sndReceiverIdQualifier == null){
        sndReceiverIdQualifier = "";
    }
    else{
        sndReceiverIdQualifier = sndReceiverIdQualifier.trim();
    }

    if (sndReceiverPartnerType == null){
        sndReceiverPartnerType = "";
    }
    else{
        sndReceiverPartnerType = sndReceiverPartnerType.trim();
    }

    if (sndReceiverSystemId == null){
        sndReceiverSystemId = "";
    }
    else{
        sndReceiverSystemId = sndReceiverSystemId.trim();
    }

    if (sndReceiverSystemIdQualifier == null){
        sndReceiverSystemIdQualifier = "";
    }
    else{
        sndReceiverSystemIdQualifier = sndReceiverSystemIdQualifier.trim();
    }

    if (sndSenderId == null){
        sndSenderId = ""
        // throw new IllegalStateException("Mandatory value sndSenderId not provided.");
    }
    else{
        sndSenderId = sndSenderId.trim();
    }

    if (sndSenderIdQualifier == null){
        sndSenderIdQualifier = "";
    }
    else{
        sndSenderIdQualifier = sndSenderIdQualifier.trim();
    }

    if (sndSenderPartnerType == null){
        sndSenderPartnerType = "";
    }
    else{
        sndSenderPartnerType = sndSenderPartnerType.trim();
    }

    if (sndSenderSystemId == null){
        sndSenderSystemId = "";
    }
    else{
        sndSenderSystemId = sndSenderSystemId.trim();
    }

    if (sndSenderSystemIdQualifier == null){
        sndSenderSystemIdQualifier = "";
    }
    else{
        sndSenderSystemIdQualifier = sndSenderSystemIdQualifier.trim();
    }
    sndSenderSystemId = "";
    sndReceiverSystemId = "";

//    def matchString          = sndAdapterType + "-" + sndDocumentStandard + "-" + sndMessageNamespace + "-" + sndMessageVersion + "-" + sndSenderId + "-" + sndSenderIdQualifier + "-" + sndSenderPartnerType + "-" + sndSenderSystemId + "-" + sndSenderSystemIdQualifier + "-" + sndReceiverId + "-" + sndReceiverIdQualifier + "-" + sndReceiverPartnerType + "-" + sndReceiverSystemId + "-" + sndReceiverSystemIdQualifier + "-" + sndMessageType
    def PidStringTemplate = sndAdapterType + "-" + sndDocumentStandard + "-" + sndMessageNamespace + "-" + sndMessageVersion + "-%s-" + sndSenderIdQualifier + "-" + sndSenderPartnerType + "-" + sndSenderSystemId + "-" + sndSenderSystemIdQualifier + "-%s-" + sndReceiverIdQualifier + "-" + sndReceiverPartnerType + "-" + sndReceiverSystemId + "-" + sndReceiverSystemIdQualifier + "-" + sndMessageType
    def PidStringTemplateForEDISubset = sndAdapterType + "-UN-EDIFACT-" + sndMessageNamespace + "-" + sndMessageVersion + "-%s-" + sndSenderIdQualifier + "-" + sndSenderPartnerType + "-" + sndSenderSystemId + "-" + sndSenderSystemIdQualifier + "-%s-" + sndReceiverIdQualifier + "-" + sndReceiverPartnerType + "-" + sndReceiverSystemId + "-" + sndReceiverSystemIdQualifier + "-" + sndMessageType
    def idMap = null
    def messageLog = messageLogFactory.getMessageLog(message)
    // support Custom Keys
    def customKeyId = sndDocumentStandard
    if (customKeyId == "UN-EDIFACT") {
        customKeyId = "UNEDIFACT"
    } else if (customKeyId == "ASC-X12") {
        customKeyId = "ASC_X12"
    } else if (customKeyId == "GS1-XML") {
        customKeyId = "GS1_XML"
    }
    def rulesetSeqData = service.getParameter(customKeyId, "SAP_TPM_Agreement_Matching_Rules", BinaryData.class);
    if (rulesetSeqData == null) {
        messageLog.setStringProperty("CustomKey Setting", "NA")
    } else {
        def rulesetSeqStr = new String(rulesetSeqData.getData());
        messageLog.setStringProperty("CustomKey Setting", rulesetSeqStr)
        def xpath = XPathFactory.newInstance().newXPath()
        def xmlBody = message.getBody(java.lang.String) as String;
        DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance()
        // support Namespace
        factory.setNamespaceAware(true)
        def nsRegex = /xmlns:(\w+)="([^"]+)"/
        def nsMap = [:]
        xmlBody.eachMatch(nsRegex) { match ->
            def prefix = match[1]
            def uri = match[2]
            nsMap[prefix] = uri
        }
        messageLog.setStringProperty("XML Namespace", nsMap.toString())
        xpath.setNamespaceContext(new NamespaceContext() {
            @Override
            public String getNamespaceURI(String prefix) {
                return nsMap.get(prefix, null);
            }
        
            @Override
            public String getPrefix(String namespaceURI) {
                throw new UnsupportedOperationException();
            }
        
            @Override
            public Iterator<String> getPrefixes(String namespaceURI) {
                throw new UnsupportedOperationException();
            }
        })
        // end Namespace
        Document xmlDoc = factory.newDocumentBuilder().parse(new InputSource(new StringReader(xmlBody)));
        def rulesetSeq = new JsonSlurper().parseText(rulesetSeqStr)
        def rulesetSize = rulesetSeq.size()
        StringBuilder sb = new StringBuilder()
        for (int i=1; i<=rulesetSize; i++) {
            def rulesetId = rulesetSeq.getAt(i.toString())
            def rulesetData = service.getParameter(rulesetId, "SAP_TPM_Agreement_Matching_Rules", BinaryData.class);
            if (rulesetData == null) {
                messageLog.setStringProperty(String.format("Ruleset Content (%d)", i), rulesetId + " is not available")
                continue
            }
            def rulesetDataStr = new String(rulesetData.getData())
            messageLog.setStringProperty(String.format("Ruleset Content (%d)", i), rulesetDataStr)
            def ruleset = new JsonSlurper().parseText(rulesetDataStr)
            if (ruleset.typeSystemVersion.isEmpty() || ruleset.messageType.isEmpty()) {
                continue
            }
            if (!(ruleset.typeSystemVersion[0] == "All" || sndMessageVersion in ruleset.typeSystemVersion)) {
                continue
            }
            if (!(ruleset.messageType[0] == "All" || sndMessageType in ruleset.messageType)) {
                continue
            }
            def sndSenderIdinRuleset = sndSenderId
            if (ruleset.standardKeys?.TPM_SenderIdentifier) {
                try {
                    sndSenderIdinRuleset = xpath.evaluate(ruleset.standardKeys.TPM_SenderIdentifier, xmlDoc, XPathConstants.STRING)
                } catch (XPathExpressionException e) {
                    messageLog.setStringProperty("Invalid XPath in " + ruleset.name, ruleset.standardKeys.TPM_SenderIdentifier + ": " + e.getMessage())
                    continue
                }
            }
            if (sndSenderIdinRuleset == "") {
                continue
            }
            def sndReceiverIdinRuleset = sndReceiverId
            if (ruleset.standardKeys?.TPM_ReceiverIdentifier) {
                try {
                    sndReceiverIdinRuleset = xpath.evaluate(ruleset.standardKeys.TPM_ReceiverIdentifier, xmlDoc, XPathConstants.STRING)
                } catch (XPathExpressionException e) {
                    messageLog.setStringProperty("Invalid XPath in " + ruleset.name, ruleset.standardKeys.TPM_ReceiverIdentifier + ": " + e.getMessage())
                    continue
                }
            }
            if (sndReceiverIdinRuleset == "") {
                continue
            }
            def customKeys = ruleset.customKeys
            def keySize = customKeys.size()
            sb.setLength(0)
            def missing = false
            for (int j=1; j<=keySize; j++) {
                def rule = customKeys.getAt(j.toString())
                if (rule == null) {
                    missing = true
                    break
                }
                try {
                    def value = xpath.evaluate(rule.expression, xmlDoc, XPathConstants.STRING)
                    if (value) {
                        sb.append(String.format("-%s-%s", rule.alias, value))
                    } else {
                        missing = true
                        break
                    }
                } catch (XPathExpressionException e) {
                    messageLog.setStringProperty("Invalid XPath in " + ruleset.name, rule.expression)
                    missing = true
                    break
                }
            }
            if (missing) {
                continue
            }
            extendPidStringTemplate = PidStringTemplate + sb.toString()
            extendPidStringTemplateForEDISubset = PidStringTemplateForEDISubset + sb.toString()
            idMap = calcRuntimePid(extendPidStringTemplate, extendPidStringTemplateForEDISubset, sndSenderIdinRuleset, sndSenderIdQualifier, sndReceiverIdinRuleset, sndReceiverIdQualifier, sndDocumentStandard, service, messageLog, i.toString(), message)
            if (idMap != null) {
                message.setHeader("SAP_EDI_Sender_ID", sndSenderIdinRuleset)
                message.setHeader("SAP_EDI_Receiver_ID", sndReceiverIdinRuleset)
                break
            }
        }
    }
    // support group identifier
    if (idMap == null) {
        idMap = calcRuntimePid(PidStringTemplate, PidStringTemplateForEDISubset, sndSenderId, sndSenderIdQualifier, sndReceiverId, sndReceiverIdQualifier, sndDocumentStandard, service, messageLog, "default", message)
    }
    if (idMap == null) {
        def defaultPid = String.format(PidStringTemplate, sndSenderId, sndReceiverId)
        // no pid available
        throw new IllegalStateException("Partner ID " + calcHashedPid(defaultPid) + " is not available.\nPartner Directory Lookup Keys: " + defaultPid + "\n")
    }
    partnerID = idMap.hashedPid
    def matchString = idMap.plainPid
    def subsidiary = idMap.subsidiary
    def isUNEDIFACT = idMap.isUNEDIFACT;
    
    if (isUNEDIFACT == "true") {
        message.setHeader("SAP_EDI_SND_Type_System_UNEDIFACT", "true");
    }


//    def matchStringLowerCase = matchString.toLowerCase();
//
//    // Great hash from the match string
//    try {
//        MessageDigest digest = MessageDigest.getInstance("MD5");
//        digest.update(matchStringLowerCase.bytes);
//        partnerID = "SAP_TPM_" + new BigInteger(1, digest.digest()).toString(16).padLeft(32, '0');
//        message.setProperty("SAP_Partner_ID_String", matchString);
//        message.setProperty("SAP_Partner_ID_Hash", partnerID);
//    }
//    catch (NoSuchAlgorithmException e) {
//        log.error('Error creating MD5 hash', e);
//        throw new IllegalStateException("PartnerID hash can't be generated.");
//    }

    message.setProperty("SAP_Partner_ID_String", matchString);
    message.setProperty("SAP_Partner_ID_Hash", partnerID);
    message.setHeader("ACTUAL_PARTNER_ID", partnerID);
    message.setHeader("SAP_TPM_ACTIVITYPARTNER_ID", partnerID);
    message.setHeader("SAP_Partner_ID_MATCH_String", matchString);
    if (subsidiary != null) {
        message.setHeader("SAP_TPM_REC_Sender_Name", subsidiary)
    }
    // Set binary parameters
    // message.setHeader("PREPROC_XSLT", "pd:" + partnerID + ":PREPROC_XSLT:Binary");
    // message.setHeader("MAPPING_XSLT", "pd:" + partnerID + ":MAPPING_XSLT:Binary");
    // message.setHeader("POSTPROC_XSLT", "pd:" + partnerID + ":POSTPROC_XSLT:Binary");
    // message.setHeader("ASSEMBLY_XSLT", "pd:" + partnerID + ":ASSEMBLY_XSLT:Binary"); // Script for filling headers/trailers and assembly of the interchange
    // message.setHeader("SND_CONVERSION_XSD", "pd:" + partnerID + ":SND_CONVERSION_XSD:Binary"); // For syntax validation and/or edi to xml conversion at sender side
    // message.setHeader("REC_CONVERSION_XSD", "pd:" + partnerID + ":REC_CONVERSION_XSD:Binary"); // For syntax validation and/or edi to xml conversion at receiver side
    // message.setHeader("SND_VALIDATION_XSD", "pd:" + partnerID + ":SND_VALIDATION_XSD:Binary"); // For payload validation at sender side
    // message.setHeader("REC_VALIDATION_XSD", "pd:" + partnerID + ":REC_VALIDATION_XSD:Binary"); // For payload validation at receiver side

    // proposal from IA team
    message.setHeader("PREPROC_XSLT", "pd:" + partnerID + ":SOURCE_MIG_PRE_PROC:Binary");
    message.setHeader("MAPPING_XSLT", "pd:" + partnerID + ":MAPPING_XSLT:Binary");
    message.setHeader("POSTPROC_XSLT", "pd:" + partnerID + ":TARGET_MIG_POST_PROC:Binary");
    message.setHeader("ASSEMBLY_XSLT", "pd:" + partnerID + ":ASSEMBLY_XSLT:Binary"); // Script for filling headers/trailers and assembly of the interchange
    message.setHeader("SND_CONVERSION_XSD", "pd:" + partnerID + ":SOURCE_MSG_XSD:Binary"); // For syntax validation and/or edi to xml conversion at sender side
    message.setHeader("REC_CONVERSION_XSD", "pd:" + partnerID + ":TARGET_MSG_XSD:Binary"); // For syntax validation and/or edi to xml conversion at receiver side
    message.setHeader("SND_VALIDATION_XSD", "pd:" + partnerID + ":SOURCE_MIG_XSD:Binary"); // For payload validation at sender side
    message.setHeader("REC_VALIDATION_XSD", "pd:" + partnerID + ":TARGET_MIG_XSD:Binary"); // For payload validation at receiver side
    message.setHeader("EXTENDED_POSTPROC_XSLT", "pd:" + partnerID + ":EXTENDED_POSTPROC_XSLT:Binary"); // For extended postprocessing
    message.setHeader("EXTENDED_PREPROC_XSLT", "pd:" + partnerID + ":EXTENDED_PREPROC_XSLT:Binary"); // For extended preprocessing

    // sequential mapping conversion
    def seqMappingId = service.getParameter("SAP_TPM_Sequential_Mapping_ID", partnerID , String.class)
    def seqMapping = null
    if (seqMappingId != null) {
        seqMapping = service.getParameter(seqMappingId, "SAP_TPM_Sequential_Mapping_List" , BinaryData.class)
        if (seqMapping != null) {
            def sndConversionXSD = service.getParameter("SAP_TPM_Source_Custom_Conversion_XSD", partnerID , String.class)
            if (sndConversionXSD != null) {
                message.setHeader("SND_CONVERSION_XSD", sndConversionXSD)
            }
            def recConversionXSD = service.getParameter("SAP_TPM_Target_Custom_Conversion_XSD", partnerID , String.class)
            if (recConversionXSD != null) {
                message.setHeader("REC_CONVERSION_XSD", recConversionXSD)
            }
        }
    }


    // get communication parameters for receiver
    def recAdapterType             = service.getParameter("SAP_COM_REC_Adapter_Type", partnerID , String.class);
    def recA2ReceiverURL           = service.getParameter("SAP_AS2_REC_Receiver_URL", partnerID , String.class);
    def recAS2CredentialName       = service.getParameter("SAP_AS2_REC_Credential_Name", partnerID , String.class);
    def recAS2FileName             = service.getParameter("SAP_AS2_REC_File_Name", partnerID , String.class);
    def recAS2AppendTimestamp      = service.getParameter("SAP_AS2_REC_APPEND_TIMESTAMP", partnerID , String.class);
    def recIncludeMillisecond      = service.getParameter("SAP_AS2_REC_APPEND_TIMESTAMP_INCLUDE_MILLISECOND", partnerID , String.class);
    def recAS2MsgIdLeftPart        = service.getParameter("SAP_AS2_REC_Message_ID_Left_Part", partnerID , String.class);
    def recAS2MsgIdRightPart       = service.getParameter("SAP_AS2_REC_Message_ID_Right_Part", partnerID , String.class);
    def recAS2OwnAS2Id             = service.getParameter("SAP_AS2_REC_Own_AS2_ID", partnerID , String.class);
    def recAS2PartnerAS2Id         = service.getParameter("SAP_AS2_REC_Partner_AS2_ID", partnerID , String.class);
    def recAS2MessageSubject       = service.getParameter("SAP_AS2_REC_Message_Subject", partnerID , String.class);
    def recAS2OwnEmailAddress      = service.getParameter("SAP_AS2_REC_Own_Email_Address", partnerID , String.class);
    def recAS2ContentType          = service.getParameter("SAP_AS2_REC_Content_Type", partnerID , String.class);
    def recAS2OutboundCompressMsg  = service.getParameter("SAP_AS2_Outbound_Compress_Message", partnerID , String.class);
    def recAS2OutboundSignMsg      = service.getParameter("SAP_AS2_Outbound_Sign_Message", partnerID , String.class);
    def recAS2OutboundSignAlgthm   = service.getParameter("SAP_AS2_Outbound_Signing_Algorithm", partnerID , String.class);
    def recAS2OutboundPrivateKey   = service.getParameter("SAP_AS2_Outbound_Signing_Private_Key_Alias", partnerID , String.class);
    def recAS2OutboundEncryptMsg   = service.getParameter("SAP_AS2_Outbound_Encrypt_Message", partnerID , String.class);
    def recAS2OutboundEncryptAlg   = service.getParameter("SAP_AS2_Outbound_Encryption_Algorithm", partnerID , String.class);
    def recAS2OutboundPublicKey    = service.getParameter("SAP_AS2_Outbound_Public_Key_Alias", partnerID , String.class);
    def recAS2OutboundEncryptKeyL  = service.getParameter("SAP_AS2_Outbound_Encryption_Key_Length", partnerID , String.class);
    def recSOAPAddress             = service.getParameter("SAP_SOAP_REC_Address", partnerID , String.class);
    def recSOAPCredentialName      = service.getParameter("SAP_SOAP_REC_Credential_Name", partnerID , String.class);
    def recSMTPFrom                = service.getParameter("SAP_SMTP_REC_From", partnerID , String.class);
    def recSMTPTo                  = service.getParameter("SAP_SMTP_REC_To", partnerID , String.class);
    def recSMTPSubject             = service.getParameter("SAP_SMTP_REC_Subject", partnerID , String.class);
    def recIDOCAddress             = service.getParameter("SAP_IDOC_REC_Address", partnerID , String.class);
    def recIDOCCredentialName      = service.getParameter("SAP_IDOC_REC_Credential_Name", partnerID , String.class);
    // sftp receiver
    def recSFTPDirectory           = service.getParameter("SAP_TPM_REC_SFTP_Directory", partnerID , String.class)
    def recSFTPFilename            = service.getParameter("SAP_TPM_REC_SFTP_FileName", partnerID , String.class)
    def recSFTPAddress             = service.getParameter("SAP_TPM_REC_SFTP_Address", partnerID , String.class)
    def recSFTPProxyType           = service.getParameter("SAP_FtpProxyType", partnerID , String.class)
    def recSFTPLocationId          = service.getParameter("SAP_TPM_REC_SFTP_LocationID", partnerID , String.class)
    def recSFTPAuthMethod          = service.getParameter("SAP_FtpAuthMethod", partnerID , String.class)
    def recSFTPCredentialName      = service.getParameter("SAP_TPM_REC_SFTP_CredentialName", partnerID , String.class)
    def recSFTPUsername            = service.getParameter("SAP_TPM_REC_SFTP_UserName", partnerID , String.class)
    def recSFTPPrivateKeyAlias     = service.getParameter("SAP_TPM_REC_SFTP_PrivateKeyAlias", partnerID , String.class)
    def recSFTPTimeout             = service.getParameter("SAP_FtpTimeout", partnerID , String.class)
    def recSFTPmaxReconnect        = service.getParameter("SAP_FtpMaxReconnect", partnerID , String.class)
    def recSFTPmaxReconDelay       = service.getParameter("SAP_FtpMaxReconDelay", partnerID , String.class)
    def recSFTPDisconnect          = service.getParameter("SAP_FtpDisconnect", partnerID , String.class)
    def recSFTPStepwise            = service.getParameter("SAP_FtpStepwise", partnerID , String.class)
    def recSFTPCreateDir           = service.getParameter("SAP_FtpCreateDir", partnerID , String.class)
    def recSFTPFlattenFilename     = service.getParameter("SAP_FtpFlattenFileName", partnerID , String.class)
    def recSFTPFastExistsCheck     = service.getParameter("SAP_FtpFastExistsCheck", partnerID , String.class)
    def recSFTPAfterProc           = service.getParameter("SAP_FtpAfterProc", partnerID , String.class)
    

    // get communication parameters for sender. This is necessary for functional acknowledgements
    def sndAckAdapterType             = service.getParameter("SAP_COM_SND_Adapter_Type", partnerID , String.class);
    def sndAckA2ReceiverURL           = service.getParameter("SAP_AS2_SND_Receiver_URL", partnerID , String.class);
    def sndAckAS2CredentialName       = service.getParameter("SAP_AS2_SND_Credential_Name", partnerID , String.class);
    def sndAckAS2FileName             = service.getParameter("SAP_AS2_SND_File_Name", partnerID , String.class);
    def sndAS2AppendTimestamp         = service.getParameter("SAP_AS2_SND_APPEND_TIMESTAMP", partnerID , String.class);
    def sndIncludeMillisecond         = service.getParameter("SAP_AS2_SND_APPEND_TIMESTAMP_INCLUDE_MILLISECOND", partnerID , String.class);
    def sndAckAS2MsgIdLeftPart        = service.getParameter("SAP_AS2_SND_Message_ID_Left_Part", partnerID , String.class);
    def sndAckAS2MsgIdRightPart       = service.getParameter("SAP_AS2_SND_Message_ID_Right_Part", partnerID , String.class);
    def sndAckAS2OwnAS2Id             = service.getParameter("SAP_AS2_SND_Own_AS2_ID", partnerID , String.class);
    def sndAckAS2PartnerAS2Id         = service.getParameter("SAP_AS2_SND_Partner_AS2_ID", partnerID , String.class);
    def sndAckAS2MessageSubject       = service.getParameter("SAP_AS2_SND_Message_Subject", partnerID , String.class);
    def sndAckAS2OwnEmailAddress      = service.getParameter("SAP_AS2_SND_Own_Email_Address", partnerID , String.class);
    def sndAckAS2ContentType          = service.getParameter("SAP_AS2_SND_Content_Type", partnerID , String.class);
    def sndAS2OutboundCompressMsg  = service.getParameter("SAP_AS2_SND_Outbound_Compress_Message", partnerID , String.class);
    def sndAS2OutboundSignMsg      = service.getParameter("SAP_AS2_SND_Outbound_Sign_Message", partnerID , String.class);
    def sndAS2OutboundSignAlgthm   = service.getParameter("SAP_AS2_SND_Outbound_Signing_Algorithm", partnerID , String.class);
    def sndAS2OutboundPrivateKey   = service.getParameter("SAP_AS2_SND_Outbound_Signing_Private_Key_Alias", partnerID , String.class);
    def sndAS2OutboundEncryptMsg   = service.getParameter("SAP_AS2_SND_Outbound_Encrypt_Message", partnerID , String.class);
    def sndAS2OutboundEncryptAlg   = service.getParameter("SAP_AS2_SND_Outbound_Encryption_Algorithm", partnerID , String.class);
    def sndAS2OutboundPublicKey    = service.getParameter("SAP_AS2_SND_Outbound_Public_Key_Alias", partnerID , String.class);
    def sndAS2OutboundEncryptKeyL  = service.getParameter("SAP_AS2_SND_Outbound_Encryption_Key_Length", partnerID , String.class);
    def sndAckSOAPAddress             = service.getParameter("SAP_SOAP_SND_Address", partnerID , String.class);
    def sndAckSOAPCredentialName      = service.getParameter("SAP_SOAP_SND_Credential_Name", partnerID , String.class);
    def sndAckSMTPFrom                = service.getParameter("SAP_SMTP_SND_From", partnerID , String.class);
    def sndAckSMTPTo                  = service.getParameter("SAP_SMTP_SND_To", partnerID , String.class);
    def sndAckSMTPSubject             = service.getParameter("SAP_SMTP_SND_Subject", partnerID , String.class);
    def sndAckAS2CreateAck            = service.getParameter("SAP_EDISPLITTER_EDIFACT_CREATE_ACK", partnerID, String.class);
    def splitter997GroupControlNumber         = service.getParameter("SAP_EDISPLITTER_997_GROUP_CONTROL_NUMBER", partnerID, String.class);
    def splitter997UniqueGroupControlNumber   = service.getParameter("SAP_EDISPLITTER_997_UNIQUE_GROUP_CONTROL_NUMBER", partnerID, String.class);
    def splitter997GroupNumberRange           = service.getParameter("SAP_EDISPLITTER_997_GROUP_NUMBER_RANGE", partnerID, String.class);
    def splitter997TransactionSetNumber       = service.getParameter("SAP_EDISPLITTER_997_TRANSACTION_SET_NUMBER", partnerID, String.class);
    def splitter997UniqueTransactionSetNumber = service.getParameter("SAP_EDISPLITTER_997_UNIQUE_TRANSACTION_SET_NUMBER", partnerID, String.class);
    def splitter997TransactionSetNumberRange  = service.getParameter("SAP_EDISPLITTER_997_TS_NUMBER_RANGE", partnerID, String.class);
    def sendTestInterchangeToTargetSystem  = service.getParameter("SAP_TPM_SND_SendTestInterchangeToTargetSystem", partnerID, String.class);

    // get payload validation parameters for sender and receiver.
    def sndSourcePayloadValidation = service.getParameter("SAP_EDI_SND_Payload_Validation", partnerID , String.class);
    def recTargetPayloadValidation = service.getParameter("SAP_EDI_REC_Payload_Validation", partnerID , String.class);
    def senStopProcessingWhenPayloadValidationFails = service.getParameter("SAP_TPM_SEN_Stop_Processing_When_Payload_Validation_Fails", partnerID, String.class);
    def recStopProcessingWhenPayloadValidationFails = service.getParameter("SAP_TPM_REC_Stop_Processing_When_Payload_Validation_Fails", partnerID, String.class);

    // get target decimal character for EDI to XML Converter and EDI Splitter.
    def ediTargetDecimalCharacter = service.getParameter("SAP_EDI_TARGET_DECIMAL_CHARACTER", partnerID, String.class);

    // get envelope and document standards parameters for receiver.
    def recAcknowledgementRequest = service.getParameter("SAP_EDI_REC_Acknowledgement_Request", partnerID , String.class);
    def recArchivingIndicator = service.getParameter("SAP_EDI_REC_Archiving_Indicator", partnerID , String.class);
    def recAuthorizationIDScheme = service.getParameter("SAP_ISA_REC_Auth_Information_Qualifier", partnerID , String.class);
    def recClient = service.getParameter("SAP_EDI_REC_Client", partnerID , String.class);
    def recCommonAccessReference = service.getParameter("SAP_EDI_REC_Common_Access_Reference", partnerID , String.class);
    def recComponentDataElementSeparator = service.getParameter("SAP_EDI_REC_Component_Data_Element_Separator", partnerID , String.class);
    def recControlVersionID = service.getParameter("SAP_EDI_REC_Control_Version", partnerID , String.class);
    def recCustomerExtension = service.getParameter("SAP_EDI_REC_Customer_Extension", partnerID , String.class);
    def recDirection = service.getParameter("SAP_EDI_REC_Direction", partnerID , String.class);
    def recDocumentStandard = service.getParameter("SAP_EDI_REC_Document_Standard", partnerID , String.class);
    def recGroupControllingAgency = service.getParameter("SAP_EDI_REC_Functional_Group_Controlling_Agency", partnerID , String.class);
    def recIdentifierCode = service.getParameter("SAP_EDI_REC_Functional_Identifier_Code", partnerID , String.class);
    def recIdocType = service.getParameter("SAP_EDI_REC_Idoc_Type", partnerID , String.class);
    def recInterchangeAgreementID = service.getParameter("SAP_EDI_REC_Interchange_Agreement_ID", partnerID , String.class);
    def recInterchangeControlNumber = service.getParameter("SAP_EDI_REC_Interchange_Control_Number", partnerID , String.class);
    def recInterchangeControlNumberType = service.getParameter("SAP_EDI_REC_Interchange_Control_Number_Type", partnerID , String.class);
    def recMsgAssociationAssignCode = service.getParameter("SAP_EDI_REC_Message_Association_Assign_Code", partnerID , String.class);
    def recMsgCode = service.getParameter("SAP_EDI_REC_Message_Code", partnerID , String.class);
    def recMsgControllingAgency = service.getParameter("SAP_EDI_REC_Message_Controlling_Agency", partnerID , String.class);
    def recMsgFunction = service.getParameter("SAP_EDI_REC_Message_Function", partnerID , String.class);
    def recMsgRelease = service.getParameter("SAP_EDI_REC_Message_Release", partnerID , String.class);
    def recMsgNumber = service.getParameter("SAP_EDI_REC_Message_Number", partnerID , String.class);
    def recMsgType = service.getParameter("SAP_EDI_REC_Message_Type", partnerID , String.class);
    def recMsgVersion = service.getParameter("SAP_EDI_REC_Message_Version", partnerID , String.class);
    def recOutputMode = service.getParameter("SAP_EDI_REC_Output_Mode", partnerID , String.class);
    def recPayloadFormat = service.getParameter("SAP_EDI_REC_Payload_Format", partnerID , String.class);
    def recProcessingTypeCode = service.getParameter("SAP_EDI_REC_Processing_Priority_Code", partnerID , String.class);
    def recRecLogicalAddress = service.getParameter("SAP_EDI_REC_Receiver_Logical_Address", partnerID , String.class);
    def recRecPartnerFunction = service.getParameter("SAP_EDI_REC_Receiver_Partner_Function", partnerID , String.class);
    def recRecPartnerType = service.getParameter("SAP_EDI_REC_Receiver_Partner_Type", partnerID , String.class);
    def recRecReferenceID = service.getParameter("SAP_EDI_REC_Receiver_ID", partnerID , String.class);
    def recRecReferenceIDScheme = service.getParameter("SAP_EDI_REC_Receiver_ID_Qualifier", partnerID , String.class);
    def recRecRoutingAddress = service.getParameter("SAP_EDI_REC_Receiver_Routing_Address", partnerID , String.class);
    def recRecSystemID = service.getParameter("SAP_EDI_REC_Receiver_System_ID", partnerID , String.class);
    def recRecSystemIDQualifier = service.getParameter("SAP_EDI_REC_Receiver_System_ID_Qualifier", partnerID , String.class);
    def recRepetitionReferenceSeparator = service.getParameter("SAP_EDI_REC_Repetition_Separator", partnerID , String.class);
    def recSecurityInformation = service.getParameter("SAP_ISA_REC_Security_Information", partnerID , String.class);
    def recSecurityInformationQualifier = service.getParameter("SAP_EDI_REC_Security_Information_Qualifier", partnerID , String.class);
    def recSecurityInformationScheme = service.getParameter("SAP_ISA_REC_Security_Information_Qualifier", partnerID , String.class);
    def recSerialization = service.getParameter("SAP_EDI_REC_Serialization", partnerID , String.class);
    def recSndGroupReferenceNumber = service.getParameter("SAP_EDI_REC_Sender_Group_Reference_Number", partnerID , String.class);
    def recSndLogicalAddress = service.getParameter("SAP_EDI_REC_Sender_Logical_Addess", partnerID , String.class);
    def recSndMsgReferenceNumber = service.getParameter("SAP_EDI_REC_Sender_Message_Reference_Number", partnerID , String.class);
    def recSndPartnerFunction = service.getParameter("SAP_EDI_REC_Sender_Partner_Function", partnerID , String.class);
    def recSndPartnerType = service.getParameter("SAP_EDI_REC_Sender_Partner_Type", partnerID , String.class);
    def recSndReferenceID = service.getParameter("SAP_EDI_REC_Sender_ID", partnerID , String.class);
    def recSndReferenceIDScheme = service.getParameter("SAP_EDI_REC_Sender_ID_Qualifier", partnerID , String.class);
    def recSndRoutingAddress = service.getParameter("SAP_EDI_REC_Sender_Routing_Address", partnerID , String.class);
    def recSndSystemID = service.getParameter("SAP_EDI_REC_Sender_System_ID", partnerID , String.class);
    def recSndSystemIDQualifier = service.getParameter("SAP_EDI_REC_Sender_System_ID_Qualifier", partnerID , String.class);
    def recSndTransmissionFile = service.getParameter("SAP_EDI_REC_Sender_Interchange_Reference_Number", partnerID , String.class);
    def recStandardFlag = service.getParameter("SAP_EDI_REC_Standard_Flag", partnerID , String.class);
    def recStandardVersion = service.getParameter("SAP_EDI_REC_Standard_Version", partnerID , String.class);
    def recStatus = service.getParameter("SAP_EDI_REC_Status", partnerID , String.class);
    def recStdMsgType = service.getParameter("SAP_EDI_REC_Standard_Message_Type", partnerID , String.class);
    def recSyntaxID = service.getParameter("SAP_EDI_REC_Syntax_ID", partnerID , String.class);
    def recSyntaxVersionID = service.getParameter("SAP_EDI_REC_Syntax_Version", partnerID , String.class);
    def recUsageIndicator = service.getParameter("SAP_EDI_REC_Usage_Indicator", partnerID , String.class);
    def recExtendedPostprocessing = service.getParameter("SAP_EDI_REC_Extended_Postprocessing", partnerID , String.class);
    def recExtendedPreprocessing = service.getParameter("SAP_EDI_REC_Extended_Preprocessing", partnerID , String.class);
    def recSenderShortName = service.getParameter("SAP_EDI_REC_Sender_Name", partnerID , String.class);
    def recReceiverShortName = service.getParameter("SAP_EDI_REC_Receiver_Name", partnerID , String.class);
    // dynamic support in AS2 Receiver
    def recProxyType = service.getParameter("SAP_AS2_Outbound_Proxy_Type", partnerID , String.class);
    def recAuthenType = service.getParameter("SAP_AS2_Outbound_Authentication_Type", partnerID , String.class);
    def recLocationId = service.getParameter("SAP_AS2_REC_Location_ID", partnerID , String.class);
    def recAuthenPrivateKeyAlias = service.getParameter("SAP_AS2_REC_Private_Key_Alias", partnerID , String.class);
    def recContentTransferEncoding = service.getParameter("SAP_AS2_Outbound_Content_Transfer_Encoding", partnerID , String.class);
    def recMdnType = service.getParameter("SAP_AS2_Outbound_Mdn_Type", partnerID , String.class);
    def recMdnTargetUrlFromActivityPD = service.getParameter("SAP_AS2_Outbound_Mdn_Target_URL", partnerID , String.class);
    def recMdnPublicKeyAlias = service.getParameter("SAP_AS2_Outbound_Mdn_Public_Key_Alias", partnerID , String.class);
    def recMdnRequestSig = service.getParameter("SAP_AS2_Outbound_Mdn_Request_Signing", partnerID , String.class);
    def recMdnSigAlg = service.getParameter("SAP_AS2_Outbound_Mdn_Signing_Algorithm", partnerID , String.class);
    def recMdnVerifySig = service.getParameter("SAP_AS2_Outbound_Mdn_Verify_Signature", partnerID , String.class);
    def recMdnRequestMic = service.getParameter("SAP_AS2_Outbound_Mdn_Request_Mic", partnerID , String.class);
    def recMdnVerifyMic = service.getParameter("SAP_AS2_Outbound_Mdn_Verify_Mic", partnerID , String.class);
    def recMdnFailNegMdn = service.getParameter("SAP_AS2_Outbound_Fail_Message_On_Negative_MDN", partnerID , String.class);

    // contact person
    def sndContactPersonFirstName = service.getParameter("SAP_TPA_SND_Trading_Partner_FirstName", partnerID , String.class);
    def sndContactPersonLastName = service.getParameter("SAP_TPA_SND_Trading_Partner_LastName", partnerID , String.class);
    def sndContactPersonEmail = service.getParameter("SAP_EDI_REC_Sender_EMail_Address", partnerID , String.class);
    def sndContactPersonTelephone = service.getParameter("SAP_EDI_REC_Sender_Telephone_Number", partnerID , String.class);
    def recContactPersonFirstName = service.getParameter("SAP_TPA_REC_Trading_Partner_FirstName", partnerID , String.class);
    def recContactPersonLastName = service.getParameter("SAP_TPA_REC_Trading_Partner_LastName", partnerID , String.class);
    def recContactPersonEmail = service.getParameter("SAP_EDI_REC_Receiver_EMail_Address", partnerID , String.class);
    def recContactPersonTelephone = service.getParameter("SAP_EDI_REC_Receiver_Telephone_Number", partnerID , String.class);

    // get general trading partner agreement parameters for monitoring.
    def tpaAgreementName           = service.getParameter("SAP_TPA_Name", partnerID , String.class);
    def tpaBTName                  = service.getParameter("SAP_TPA_BT_Name", partnerID , String.class);
    def tpaBTTypeName              = service.getParameter("SAP_TPA_BT_Type", partnerID , String.class);
    def tpaBTActivityName          = service.getParameter("SAP_TPA_BTA_Name", partnerID , String.class);
    def tpaBTActivityDirection     = service.getParameter("SAP_TPA_BTA_Direction", partnerID , String.class);
    def tpaBTActivityRefId         = service.getParameter("SAP_BA_REF_ID", partnerID , String.class);
    def tpaSenderTpId              = service.getParameter("SAP_TPA_SND_Trading_Partner_ID", partnerID , String.class);
    def tpaSenderTpName            = service.getParameter("SAP_TPA_SND_Trading_Partner_Name", partnerID , String.class);
    def tpaReceiverTpId            = service.getParameter("SAP_TPA_REC_Trading_Partner_ID", partnerID , String.class);
    def tpaReceiverTpName          = service.getParameter("SAP_TPA_REC_Trading_Partner_Name", partnerID , String.class);
    // new parameter for agreement name
    def tpmAgreementName           = service.getParameter("SAP_TPM_Agreement_Name", partnerID, String.class);
    if (tpmAgreementName != null) {
        tpaAgreementName = tpmAgreementName;
    }
    def tpmAgreementId             = service.getParameter("SAP_TPA_ID", partnerID, String.class);
    // get tenant host name
    def tenantHost = service.getParameter("SAP_TPM_TenantHost", "SAP_TPM_Global_Parameters" , String.class);

    // set ASSEMBLY file mapping
    def assemblyXslt = "";
    if ("SAP_IDoc" == recDocumentStandard) {
        assemblyXslt = "/mapping/idoc_assembly.xsl";
    } else if ("IDOC" == recDocumentStandard) {
        assemblyXslt = "/mapping/idoc_assembly.xsl";
    } else if ("SOAP" == recDocumentStandard) {
        assemblyXslt = "/mapping/soap_assembly.xsl";
    } else if ("SAP_S4HANA_OnPremise_SOA" == recDocumentStandard) {
        assemblyXslt = "/mapping/soap_assembly.xsl";
    } else if ("SAP_S4HANA_Cloud_SOA" == recDocumentStandard) {
        assemblyXslt = "/mapping/soap_assembly.xsl";
    } else if ("ASC_X12" == recDocumentStandard) {
        assemblyXslt = "/mapping/ascx12_assembly.xsl";
    } else if ("UNEDIFACT" == recDocumentStandard) {
        assemblyXslt = "/mapping/unedifact_assembly.xsl";
    } else if ("GS1_XML" == recDocumentStandard) {
        assemblyXslt = "/mapping/gs1xml_assembly.xsl";
    } else if ("TRADACOMS" == recDocumentStandard) {
        assemblyXslt = "/mapping/tradacoms_assembly.xsl";
    } else if ("EANCOM" == recDocumentStandard) {
        assemblyXslt = "/mapping/unedifact_assembly.xsl";
    } else if ("ODETTE_EDIFACT" == recDocumentStandard) {
        assemblyXslt = "/mapping/unedifact_assembly.xsl";
    } else if ("VDA_EDIFACT" == recDocumentStandard) {
        assemblyXslt = "/mapping/unedifact_assembly.xsl";
    }
    message.setHeader("SAP_TPM_ASSEMBLY_XSLT", assemblyXslt);
    
    // get edi splitter edifact validate message.
    if(sndDocumentStandard in ["UN-EDIFACT", "EANCOM", "ODETTE_EDIFACT", "VDA_EDIFACT"]){
        def ediSplitterEdifactVlidate  = service.getParameter("SAP_EDISPLITTER_EDIFACT_VALIDATE_MESSAGE",partnerID,String.class);
        message.setHeader("SAP_EDISPLITTER_EDIFACT_VALIDATE_MESSAGE", ediSplitterEdifactVlidate);
    }
    // get edi splitter x12 validate message option.
    if(sndDocumentStandard == "ASC-X12"){
        def ediSplitterX12Vlidate      = service.getParameter("SAP_EDISPLITTER_X12_VALIDATE_MESSAGE_OPTION",partnerID,String.class);
        message.setHeader("SAP_EDISPLITTER_X12_VALIDATE_MESSAGE_OPTION",ediSplitterX12Vlidate);
    }
    
    // get custom process direct
    def jsonSlurper = new JsonSlurper();
    def preProc = service.getParameter("SAP_EDI_REC_Custom_PreProcessing", partnerID , String.class);
    if (preProc != null) {
        def preProcJson = jsonSlurper.parseText(preProc);
        message.setProperty("SAP_EDI_REC_Custom_PreProcessing_Enabled", "true");
        message.setProperty("SAP_EDI_REC_Custom_PreProcessing_Address", preProcJson.TargetAddress);
    } else {
        message.setProperty("SAP_EDI_REC_Custom_PreProcessing_Enabled", "false")
    }
    
    //sequential mapping
    if (seqMapping != null) {
        def seqMappingJson = new JsonSlurper().parseText(new String(seqMapping.getData()))
        message.setProperty("SAP_TPM_Sequential_Mapping_Enabled", "true")
        message.setProperty("SAP_TPM_Sequential_Mapping_JSON", seqMappingJson)
        message.setProperty("SAP_TPM_Sequential_Mapping_Next_Index", 1)
        def sourceValdiationXSD = service.getParameter("SAP_TPM_Source_Custom_Payload_Validation_XSD", partnerID , String.class)
        def targetValdiationXSD = service.getParameter("SAP_TPM_Target_Custom_Payload_Validation_XSD", partnerID , String.class)
        if (sourceValdiationXSD != null) {
            message.setProperty("SAP_TPM_Sequential_Mapping_SND_Payload_Validation", "true")
            message.setHeader("SAP_TPM_Source_Custom_Payload_Validation_XSD", sourceValdiationXSD)
        }
        if (targetValdiationXSD != null) {
            message.setProperty("SAP_TPM_Sequential_Mapping_REC_Payload_Validation", "true")
            message.setHeader("SAP_TPM_Target_Custom_Payload_Validation_XSD", targetValdiationXSD)
        }
    }

    def mainProc = service.getParameter("SAP_EDI_REC_Custom_Main_Mapping", partnerID , String.class);
    if (mainProc != null) {
        def mainProcJson = jsonSlurper.parseText(mainProc);
        message.setProperty("SAP_EDI_REC_Custom_Main_Mapping_Enabled", "true");
        message.setProperty("SAP_EDI_REC_Custom_Main_Mapping_Address", mainProcJson.TargetAddress);
    } else {
        message.setProperty("SAP_EDI_REC_Custom_Main_Mapping_Enabled", "false");
    }

    def postProc = service.getParameter("SAP_EDI_REC_Custom_PostProcessing", partnerID , String.class);
    if (postProc != null) {
        def postProcJson = jsonSlurper.parseText(postProc);
        message.setProperty("SAP_EDI_REC_Custom_PostProcessing_Enabled", "true");
        message.setProperty("SAP_EDI_REC_Custom_PostProcessing_Address", postProcJson.TargetAddress);
    } else {
        message.setProperty("SAP_EDI_REC_Custom_PostProcessing_Enabled", "false");
    }


    // For beta: Set receiver type system related parameters.
    // Remark for GA: The parameters recPayloadFormat and recInterchangeControlNumberType must be provided by PD.
    if (recDocumentStandard == "UNEDIFACT"){
        recPayloadFormat    = "EDI_FLAT";
        if (recInterchangeControlNumberType == null) recInterchangeControlNumberType = "ICN_UN_EDIFACT";
    }
    else if (recDocumentStandard == "ASC_X12"){
        recPayloadFormat    = "EDI_FLAT";
        if (recInterchangeControlNumberType == null) recInterchangeControlNumberType = "ICN_ASC_X12";
    }
    else if (recDocumentStandard == "SAP_IDoc"){
        recPayloadFormat    = "XML";
        if (recInterchangeControlNumberType == null) recInterchangeControlNumberType = "ICN_SAP_IDOC";
    }
    else if (recDocumentStandard == "SAP_S4HANA_Cloud_SOA" || recDocumentStandard == "SAP_S4HANA_OnPremise_SOA"){
        recPayloadFormat    = "XML";
        if (recInterchangeControlNumberType == null) recInterchangeControlNumberType = "ICN_SAP_SOAP";
    }
    else if (recDocumentStandard == "GS1_XML") {
        recPayloadFormat    = "XML";
        if (recInterchangeControlNumberType == null) recInterchangeControlNumberType = "ICN_GS1_XML";
    }
    else if (recDocumentStandard == "TRADACOMS"){
        recPayloadFormat    = "EDI_FLAT";
        if (recInterchangeControlNumberType == null) recInterchangeControlNumberType = "ICN_TRADACOMS";
    }
    else if (recDocumentStandard == "EANCOM"){
        recPayloadFormat    = "EDI_FLAT";
        if (recInterchangeControlNumberType == null) recInterchangeControlNumberType = "ICN_EANCOM";
    }
    else if (recDocumentStandard == "ODETTE_EDIFACT"){
        recPayloadFormat    = "EDI_FLAT";
        if (recInterchangeControlNumberType == null) recInterchangeControlNumberType = "ICN_ODETTE_EDIFACT";
    }
    else if (recDocumentStandard == "VDA_EDIFACT"){
        recPayloadFormat    = "EDI_FLAT";
        if (recInterchangeControlNumberType == null) recInterchangeControlNumberType = "ICN_VDA_EDIFACT";
    }
    else if (recDocumentStandard == "cXML") {
        recPayloadFormat    = "XML";
    }
    else if (recDocumentStandard == null) {
        throw new IllegalStateException("Parameter recDocumentStandard not found for\n Partner ID: " + partnerID + "\nPartner Directory Lookup Keys: " + matchString + "\n");
    }
    else {
        throw new IllegalStateException("Wrong value " + recDocumentStandard + " for parameter recDocumentStandard for partnerID " + partnerID);
    }

    // dynamic PD entries for idoc receiver
    def idocRecProxyType = service.getParameter("SAP_IDocProxyType", partnerID , String.class);
    def idocRecContentType = service.getParameter("SAP_IDocContentType", partnerID, String.class);
    def idocRecAuthenType = service.getParameter("SAP_IDocAuthMethod", partnerID , String.class);
    def idocRecLocationId = service.getParameter("SAP_IDOC_REC_Location_ID", partnerID , String.class);
    // dynamic PD entries for soap receiver
    def soapRecProxyType = service.getParameter("SAP_SOAP_REC_Proxy_Type", partnerID , String.class);
    def soapRecAuthenType = service.getParameter("SAP_SOAP_REC_Authentication", partnerID , String.class);
    def soapRecLocationId = service.getParameter("SAP_SOAP_REC_Location_ID", partnerID , String.class);
    // dynamic PD entries for edifact custom separator
    def edifactUseCustomSeparator = service.getParameter("SAP_TPM_EDIFACT_Use_Custom_Separator", partnerID , String.class);
    def edifactSegmentTerminator = service.getParameter("SAP_TPM_EDIFACT_Segment_Terminator", partnerID , String.class);
    def edifactCompositeSeparator = service.getParameter("SAP_TPM_EDIFACT_Composite_Separator", partnerID , String.class);
    def edifactDataElementSeparator = service.getParameter("SAP_TPM_EDIFACT_Data_Element_Separator", partnerID , String.class);
    def edifactEscapeCharacter = service.getParameter("SAP_TPM_EDIFACT_Escape_Character", partnerID , String.class);
    // dynamic PD entries for x12 custom separator
    def x12UseCustomSeparator = service.getParameter("SAP_TPM_X12_Use_Custom_Separator", partnerID , String.class);
    def x12SegmentTerminator = service.getParameter("SAP_TPM_X12_Segment_Terminator", partnerID , String.class);
    def x12CompositeSeparator = service.getParameter("SAP_TPM_X12_Composite_Separator", partnerID , String.class);
    def x12DataElementSeparator = service.getParameter("SAP_TPM_X12_Data_Element_Separator", partnerID , String.class);
    def x12RepetitionSeparator = service.getParameter("SAP_TPM_X12_Repetition_Separator", partnerID , String.class);

    //Process Direct
    def recProcessDirectAddress = service.getParameter("SAP_PROCESS_DIRECT_REC_Address", partnerID , String.class);
    def sndAckProcessDirectAddress = service.getParameter("SAP_PROCESS_DIRECT_SND_Address", partnerID , String.class);
    
    // Edifact Target Encoding in XMLtoEDI converter
    def edifactTargetEncoding = service.getParameter("SAP_XMLTOEDI_EDIFACT_TARGET_ENCODING", partnerID , String.class)
    
    // Edifact Target Syntax Version in XMLtoEDI converter
    def edifactSyntaxVersion = service.getParameter("SAP_TPM_EDIFACT_Syntax_Version", partnerID , String.class)
    // EdiSplitter control message version 
    def edifactControlMsgVersion = service.getParameter("SAP_EDISPLITTER_EDIFACT_CONTRL_MSG_VERSION", partnerID , String.class)

    // VAN information
    def tpaCommunicationPartnerName = service.getParameter("SAP_TPA_Communication_Partner_Name", partnerID, String.class);

    // SAP_TPM_Target_MIG_HasEnvelope
    def sapTPMTargetMigHasEnvelope = service.getParameter("SAP_TPM_Target_MIG_HasEnvelope", partnerID, String.class);
    // SAP_TPM_Source_MIG_HasEnvelope
    def sapTPMSourceMigHasEnvelope = service.getParameter("SAP_TPM_Source_MIG_HasEnvelope", partnerID, String.class);
    // SAP_TPM_Pass_Through
    def sapTPMPassThrough = service.getParameter("SAP_TPM_Pass_Through", partnerID, String.class);
    
    // Partially Accepted Functional Acknowledgement
    def partialAckTargetStatus = service.getParameter("SAP_TPM_REC_PartialAck_TargetStatus", partnerID , String.class);
    
    // Code Page
    def tpmSourceEncoding = service.getParameter("SAP_TPM_SND_Source_Encoding", partnerID , String.class);
    def tpmTargetEncoding = service.getParameter("SAP_TPM_REC_Target_Encoding", partnerID , String.class);
    
    // Get system name from PD
    def sndSystemInstanceName = service.getParameter("SAP_TPM_SND_SYSTEM_INSTANCE_NAME", partnerID, String.class);
    def recSystemInstanceName = service.getParameter("SAP_TPM_REC_SYSTEM_INSTANCE_NAME", partnerID, String.class);
    // Helper: extract value after "::"
    def extractSystemNameFromInstance = { instanceName ->
        if (!instanceName) return null;
        def parts = instanceName.split("::");
        return parts.size() > 1 ? parts[-1] : parts[0];
    }
    def extractedSndSystemInstanceName = extractSystemNameFromInstance(sndSystemInstanceName);
    def extractedRecSystemInstanceName = extractSystemNameFromInstance(recSystemInstanceName);
    
    
    // set communication properties for receiver
    message.setProperty("SAP_COM_REC_Adapter_Type", recAdapterType);
    message.setProperty("SAP_AS2_REC_Receiver_URL", recA2ReceiverURL);
    message.setProperty("SAP_AS2_REC_Credential_Name", recAS2CredentialName);
    if (recAS2AppendTimestamp == "true") {
        if (recIncludeMillisecond == "true") {
            recAS2FileName += LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMddHHmmssSSS"));
        } else {
            recAS2FileName += LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMddHHmmss"));
        }
    }
    message.setProperty("SAP_AS2_REC_File_Name", recAS2FileName);
    message.setProperty("SAP_AS2_REC_Message_ID_Left_Part", recAS2MsgIdLeftPart);
    message.setProperty("SAP_AS2_REC_Message_ID_Right_Part", recAS2MsgIdRightPart);
    message.setProperty("SAP_AS2_REC_Own_AS2_ID", recAS2OwnAS2Id);
    message.setProperty("SAP_AS2_REC_Partner_AS2_ID", recAS2PartnerAS2Id);
    message.setProperty("SAP_AS2_REC_Message_Subject", recAS2MessageSubject);
    message.setProperty("SAP_AS2_REC_Own_Email_Address", recAS2OwnEmailAddress);
    message.setProperty("SAP_AS2_REC_Content_Type", recAS2ContentType);
    message.setProperty("SAP_SOAP_REC_Address", recSOAPAddress);
    message.setProperty("SAP_SOAP_REC_Credential_Name", recSOAPCredentialName);
    message.setProperty("SAP_SMTP_REC_From", recSMTPFrom);
    message.setProperty("SAP_SMTP_REC_To", recSMTPTo);
    message.setProperty("SAP_SMTP_REC_Subject", recSMTPSubject);
    message.setProperty("SAP_IDOC_REC_Address", recIDOCAddress);
    message.setProperty("SAP_IDOC_REC_Credential_Name", recIDOCCredentialName);
    //sftp receiver properties
    if (recSFTPAddress != null) {
        message.setProperty("SAP_TPM_REC_SFTP_Directory", recSFTPDirectory)
        message.setProperty("SAP_TPM_REC_SFTP_FileName", recSFTPFilename)
        message.setProperty("SAP_TPM_REC_SFTP_Address", recSFTPAddress)
        message.setProperty("SAP_FtpProxyType", translateProxyType(recSFTPProxyType))
        message.setProperty("SAP_TPM_REC_SFTP_LocationID", recSFTPLocationId)
        message.setProperty("SAP_FtpAuthMethod", translateAuthentication(recSFTPAuthMethod))
        message.setProperty("SAP_TPM_REC_SFTP_CredentialName", recSFTPCredentialName)
        message.setProperty("SAP_TPM_REC_SFTP_UserName", recSFTPUsername)
        message.setProperty("SAP_TPM_REC_SFTP_PrivateKeyAlias", recSFTPPrivateKeyAlias)
        message.setProperty("SAP_FtpTimeout", recSFTPTimeout)
        message.setProperty("SAP_FtpMaxReconnect", recSFTPmaxReconnect)
        message.setProperty("SAP_FtpMaxReconDelay", recSFTPmaxReconDelay)
        message.setProperty("SAP_FtpDisconnect", translateToBoolean(recSFTPDisconnect))
        message.setProperty("SAP_FtpStepwise", translateToBoolean(recSFTPStepwise))
        message.setProperty("SAP_FtpCreateDir", translateToBoolean(recSFTPCreateDir))
        message.setProperty("SAP_FtpFlattenFileName", translateToBoolean(recSFTPFlattenFilename))
        message.setProperty("SAP_FtpFastExistsCheck", translateToBoolean(recSFTPFastExistsCheck))
        message.setProperty("SAP_FtpAfterProc", recSFTPAfterProc)
    }
    
    if (recAS2OutboundCompressMsg != null) {
        message.setHeader("SAP_AS2_Outbound_Compress_Message", recAS2OutboundCompressMsg);
    }
    if (recAS2OutboundSignMsg != null) {
        message.setHeader("SAP_AS2_Outbound_Sign_Message", recAS2OutboundSignMsg);
    }
    if (recAS2OutboundSignAlgthm != null) {
        message.setHeader("SAP_AS2_Outbound_Signing_Algorithm", recAS2OutboundSignAlgthm);
    }
    if (recAS2OutboundPrivateKey != null) {
        message.setHeader("SAP_AS2_Outbound_Signing_Private_Key_Alias", recAS2OutboundPrivateKey);
    }
    if (recAS2OutboundEncryptMsg != null) {
        message.setHeader("SAP_AS2_Outbound_Encrypt_Message", recAS2OutboundEncryptMsg);
    } else {
        message.setHeader("SAP_AS2_Outbound_Encrypt_Message", false);
    }
    if (recAS2OutboundEncryptAlg != null) {
        message.setHeader("SAP_AS2_Outbound_Encryption_Algorithm", recAS2OutboundEncryptAlg);
    }
    if (recAS2OutboundPublicKey != null) {
        message.setProperty("SAP_AS2_Outbound_Public_Key_Alias", recAS2OutboundPublicKey);
    }
    if (recAS2OutboundEncryptKeyL != null) {
        message.setHeader("SAP_AS2_Outbound_Encryption_Key_Length", recAS2OutboundEncryptKeyL);
    }

    // set target decimal character for EDI to XML Converter and EDI Splitter.
    if (ediTargetDecimalCharacter != null) {
        message.setHeader("SAP_EDITOXML_TARGET_DECIMAL_CHARACTER", ediTargetDecimalCharacter);
        message.setHeader("SAP_EDISPLITTER_DECIMAL_CHARACTER", ediTargetDecimalCharacter);
    } else {
        message.setHeader("SAP_EDITOXML_TARGET_DECIMAL_CHARACTER", "dot");
        message.setHeader("SAP_EDISPLITTER_DECIMAL_CHARACTER", "dot");
    }
    
    // set Sender Empty Segment
    if (sndDocumentStandard in ["UN-EDIFACT", "EANCOM", "ODETTE_EDIFACT", "VDA_EDIFACT"]) {
        def ediToXmlEmptySegment = service.getParameter("SAP_EDITOXML_EDIFACT_EMPTY_SEGMENT", partnerID , String.class);
        if (ediToXmlEmptySegment == null || ediToXmlEmptySegment.isEmpty()) {
            ediToXmlEmptySegment = "Exclude";
        }
        message.setHeader("SAP_EDITOXML_EDIFACT_EMPTY_SEGMENT", ediToXmlEmptySegment);
    } else if (sndDocumentStandard == "ASC-X12") {
        def ediToXmlEmptySegment = service.getParameter("SAP_EDITOXML_X12_EMPTY_SEGMENT", partnerID , String.class);
        if (ediToXmlEmptySegment == null || ediToXmlEmptySegment.isEmpty()) {
            ediToXmlEmptySegment = "Exclude";
        }
        message.setHeader("SAP_EDITOXML_X12_EMPTY_SEGMENT", ediToXmlEmptySegment);
    } else if (sndDocumentStandard == "TRADACOMS") {
        def ediToXmlEmptySegment = service.getParameter("SAP_EDITOXML_TRADACOMS_EMPTY_SEGMENT", partnerID , String.class);
        if (ediToXmlEmptySegment == null || ediToXmlEmptySegment.isEmpty()) {
            ediToXmlEmptySegment = "Exclude";
        }
        message.setHeader("SAP_EDITOXML_TRADACOMS_EMPTY_SEGMENT", ediToXmlEmptySegment);
    }
    // set Receiver Empty Segment
    if (recDocumentStandard in ["UNEDIFACT", "EANCOM", "ODETTE_EDIFACT", "VDA_EDIFACT"]) {
        def xmlToEdiEmptySegment = service.getParameter("SAP_XMLTOEDI_EDIFACT_EMPTY_SEGMENT", partnerID , String.class);
        if (xmlToEdiEmptySegment == null || xmlToEdiEmptySegment.isEmpty()) {
            xmlToEdiEmptySegment = "Exclude";
        }
        message.setHeader("SAP_XMLTOEDI_EDIFACT_EMPTY_SEGMENT", xmlToEdiEmptySegment);
    } else if (recDocumentStandard == "ASC_X12") {
        def xmlToEdiEmptySegment = service.getParameter("SAP_XMLTOEDI_X12_EMPTY_SEGMENT", partnerID , String.class);
        if (xmlToEdiEmptySegment == null || xmlToEdiEmptySegment.isEmpty()) {
            xmlToEdiEmptySegment = "Exclude";
        }
        message.setHeader("SAP_XMLTOEDI_X12_EMPTY_SEGMENT", xmlToEdiEmptySegment);
    } else if (recDocumentStandard == "TRADACOMS") {
        def xmlToEdiEmptySegment = service.getParameter("SAP_XMLTOEDI_TRADACOMS_EMPTY_SEGMENT", partnerID , String.class);
        if (xmlToEdiEmptySegment == null || xmlToEdiEmptySegment.isEmpty()) {
            xmlToEdiEmptySegment = "Exclude";
        }
        message.setHeader("SAP_XMLTOEDI_TRADACOMS_EMPTY_SEGMENT", xmlToEdiEmptySegment);
    }
    
    //Set Transaction Mode
    def edifactTransactionMode = service.getParameter("SAP_EDISPLITTER_EDIFACT_TRANSACTION_MODE", partnerID, String.class);
    if(edifactTransactionMode != null){
        message.setHeader("SAP_EDISPLITTER_EDIFACT_TRANSACTION_MODE", edifactTransactionMode);
    }
    def x12TransactionMode = service.getParameter("SAP_EDISPLITTER_X12_TRANSACTION_MODE", partnerID, String.class);
    if(x12TransactionMode != null){
        message.setHeader("SAP_EDISPLITTER_X12_TRANSACTION_MODE", x12TransactionMode);
    }
    
    // set communication properties for sender. This is necessary for functional acknowledgements
    message.setProperty("SAP_COM_SND_Adapter_Type", sndAckAdapterType);
    message.setProperty("SAP_AS2_SND_Receiver_URL", sndAckA2ReceiverURL);
    message.setProperty("SAP_AS2_SND_Credential_Name", sndAckAS2CredentialName);
    if (sndAS2AppendTimestamp == "true") {
        if (sndIncludeMillisecond == "true") {
            sndAckAS2FileName += LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMddHHmmssSSS"));
        } else {
            sndAckAS2FileName += LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMddHHmmss"));
        }
    }
    message.setProperty("SAP_AS2_SND_File_Name", sndAckAS2FileName);
    message.setProperty("SAP_AS2_SND_Message_ID_Left_Part", sndAckAS2MsgIdLeftPart);
    message.setProperty("SAP_AS2_SND_Message_ID_Right_Part", sndAckAS2MsgIdRightPart);
    message.setProperty("SAP_AS2_SND_Own_AS2_ID", sndAckAS2OwnAS2Id);
    message.setProperty("SAP_AS2_SND_Partner_AS2_ID", sndAckAS2PartnerAS2Id);
    message.setProperty("SAP_AS2_SND_Message_Subject", sndAckAS2MessageSubject);
    message.setProperty("SAP_AS2_SND_Own_Email_Address", sndAckAS2OwnEmailAddress);
    message.setProperty("SAP_AS2_SND_Content_Type", sndAckAS2ContentType);
    message.setProperty("SAP_SOAP_SND_Address", sndAckSOAPAddress);
    message.setProperty("SAP_SOAP_SND_Credential_Name", sndAckSOAPCredentialName);
    message.setProperty("SAP_SMTP_SND_From", sndAckSMTPFrom);
    message.setProperty("SAP_SMTP_SND_To", sndAckSMTPTo);
    message.setProperty("SAP_SMTP_SND_Subject", sndAckSMTPSubject);
    if (sndAckAS2CreateAck != null ) {
        message.setHeader("SAP_EDISPLITTER_EDIFACT_CREATE_ACK", sndAckAS2CreateAck);
        message.setHeader("SAP_EDISPLITTER_X12_CREATE_ACK", sndAckAS2CreateAck);
    } else {
        // default value for Create Functional Acknowledgement in SPLIT step (Check EDI Envelope)
        message.setHeader("SAP_EDISPLITTER_EDIFACT_CREATE_ACK", "checkEDIEnvelop");
        message.setHeader("SAP_EDISPLITTER_X12_CREATE_ACK", "checkEDIEnvelop");
        message.setHeader("SAP_EDISPLITTER_EDIFACT_INTERCHANGE_NUMBER", "useFromEDIMessage");
        message.setHeader("SAP_EDISPLITTER_X12_INTERCHANGE_NUMBER", "useFromEDIMessage");
    }
    // Process Sender Functional Acknowledgement Configuration
    if (sndDocumentStandard in ["UN-EDIFACT", "EANCOM", "ODETTE_EDIFACT", "VDA_EDIFACT"]) {
        message.setProperty("SAP_EDISPLITTER_X12_INTERCHANGE_NUMBER", "useFromEDIMessage");
        def sndAckAS2InterchangeNumber       = service.getParameter("SAP_EDISPLITTER_EDIFACT_INTERCHANGE_NUMBER", partnerID, String.class);
        def sndAckAS2UniqueInterchangeNumber = service.getParameter("SAP_EDISPLITTER_EDIFACT_UNIQUE_INTERCHANGE_NUMBER", partnerID, String.class);
        def sndAckAS2NumberRange             = service.getParameter("SAP_EDISPLITTER_EDIFACT_NUMBER_RANGE", partnerID, String.class);
        processEdifactSenderFunctionalAck(sndAckAS2CreateAck, sndAckAS2InterchangeNumber, sndAckAS2UniqueInterchangeNumber, sndAckAS2NumberRange, message);
    } else if (sndDocumentStandard == "ASC-X12") {
        message.setProperty("SAP_EDISPLITTER_EDIFACT_INTERCHANGE_NUMBER", "useFromEDIMessage");
        def sndAckAS2InterchangeNumber       = service.getParameter("SAP_EDISPLITTER_X12_INTERCHANGE_NUMBER", partnerID, String.class);
        def sndAckAS2UniqueInterchangeNumber = service.getParameter("SAP_EDISPLITTER_X12_UNIQUE_INTERCHANGE_NUMBER", partnerID, String.class);
        def sndAckAS2NumberRange             = service.getParameter("SAP_EDISPLITTER_X12_NUMBER_RANGE", partnerID, String.class);
        processX12SenderFunctionalAck(sndAckAS2CreateAck, sndAckAS2InterchangeNumber, sndAckAS2UniqueInterchangeNumber, sndAckAS2NumberRange, message);
    }
    // Group Number
    if (splitter997GroupControlNumber == null) {
        splitter997GroupControlNumber = "predefined";
    }
    message.setProperty("SAP_EDISPLITTER_997_GROUP_CONTROL_NUMBER", splitter997GroupControlNumber);
    if (splitter997GroupControlNumber == "numberRange") {
        if (splitter997UniqueGroupControlNumber == null) {
            throw new IllegalStateException("Unique Group Number is not set for Group Number");
        }
        if (splitter997GroupNumberRange == null) {
            throw new IllegalStateException("Group Number Range is not set for Group Number");
        }
        message.setProperty("SAP_EDISPLITTER_997_UNIQUE_GROUP_CONTROL_NUMBER", splitter997UniqueGroupControlNumber);
        message.setProperty("SAP_EDISPLITTER_997_GROUP_NUMBER_RANGE", splitter997GroupNumberRange);
    }
    // Transaction Set Number
    if (splitter997TransactionSetNumber == null) {
        splitter997TransactionSetNumber = "predefined";
    }
    message.setProperty("SAP_EDISPLITTER_997_TRANSACTION_SET_NUMBER", splitter997TransactionSetNumber);
    if (splitter997TransactionSetNumber == "numberRange") {
        if (splitter997UniqueTransactionSetNumber == null) {
            throw new IllegalStateException("Unique Transaction Set Number is not set for Transaction Set Number");
        }
        if (splitter997TransactionSetNumberRange == null) {
            throw new IllegalStateException("Transaction Set Number Range is not set for Transaction Set Number");
        }
        message.setProperty("SAP_EDISPLITTER_997_UNIQUE_TRANSACTION_SET_NUMBER", splitter997UniqueTransactionSetNumber);
        message.setProperty("SAP_EDISPLITTER_997_TS_NUMBER_RANGE", splitter997TransactionSetNumberRange);
    }
    // Usage Indicator
    message.setProperty("SAP_TPM_SND_SendTestInterchangeToTargetSystem", sendTestInterchangeToTargetSystem);
    // not resend the func ack in retry if it has been sent successfully before 
    if (headers.get("SAP_TPM_SND_FUNC_ACK_Sent") == "true") {
        message.setHeader("SAP_EDISPLITTER_EDIFACT_CREATE_ACK", "notRequired");
        message.setHeader("SAP_EDISPLITTER_X12_CREATE_ACK", "notRequired");
    }
    if (sndAS2OutboundCompressMsg != null) {
        message.setProperty("SAP_AS2_SND_Outbound_Compress_Message", sndAS2OutboundCompressMsg);
    }
    if (sndAS2OutboundSignMsg != null) {
        message.setProperty("SAP_AS2_SND_Outbound_Sign_Message", sndAS2OutboundSignMsg);
    }
    if (sndAS2OutboundSignAlgthm != null) {
        message.setProperty("SAP_AS2_SND_Outbound_Signing_Algorithm", sndAS2OutboundSignAlgthm);
    }
    if (sndAS2OutboundPrivateKey != null) {
        message.setProperty("SAP_AS2_SND_Outbound_Signing_Private_Key_Alias", sndAS2OutboundPrivateKey);
    }
    if (sndAS2OutboundEncryptMsg != null) {
        message.setProperty("SAP_AS2_SND_Outbound_Encrypt_Message", sndAS2OutboundEncryptMsg);
    } else {
        message.setProperty("SAP_AS2_SND_Outbound_Encrypt_Message", false);
    }
    if (sndAS2OutboundEncryptAlg != null) {
        message.setProperty("SAP_AS2_SND_Outbound_Encryption_Algorithm", sndAS2OutboundEncryptAlg);
    }
    if (sndAS2OutboundPublicKey != null) {
        message.setProperty("SAP_AS2_SND_Outbound_Public_Key_Alias", sndAS2OutboundPublicKey);
    }
    if (sndAS2OutboundEncryptKeyL != null) {
        message.setProperty("SAP_AS2_SND_Outbound_Encryption_Key_Length", sndAS2OutboundEncryptKeyL);
    }


    // set payload validation properties for sender and receiver
    message.setProperty("SAP_EDI_SND_Payload_Validation", sndSourcePayloadValidation);
    message.setProperty("SAP_EDI_REC_Payload_Validation", recTargetPayloadValidation);
    message.setProperty("SAP_TPM_SEN_Stop_Processing_When_Payload_Validation_Fails", senStopProcessingWhenPayloadValidationFails);
    message.setProperty("SAP_TPM_REC_Stop_Processing_When_Payload_Validation_Fails", recStopProcessingWhenPayloadValidationFails);

    // set envelope and document standards properties for receiver.
    message.setProperty("SAP_EDI_REC_Acknowledgement_Request", recAcknowledgementRequest);
    message.setProperty("SAP_EDI_REC_Archiving_Indicator", recArchivingIndicator);
    message.setProperty("SAP_ISA_REC_Auth_Information_Qualifier", recAuthorizationIDScheme);
    message.setProperty("SAP_EDI_REC_Client", recClient);
    message.setProperty("SAP_EDI_REC_Common_Access_Reference", recCommonAccessReference);
    message.setProperty("SAP_EDI_REC_Component_Data_Element_Separator", recComponentDataElementSeparator);
    message.setProperty("SAP_EDI_REC_Control_Version", recControlVersionID);
    message.setProperty("SAP_EDI_REC_Customer_Extension", recCustomerExtension);
    message.setProperty("SAP_EDI_REC_Direction", recDirection);
    message.setProperty("SAP_EDI_REC_Document_Standard", recDocumentStandard);
    message.setProperty("SAP_EDI_REC_Functional_Group_Controlling_Agency", recGroupControllingAgency);
    message.setProperty("SAP_EDI_REC_Functional_Identifier_Code", recIdentifierCode);
    message.setProperty("SAP_EDI_REC_Idoc_Type", recIdocType);
    message.setProperty("SAP_EDI_REC_Interchange_Agreement_ID", recInterchangeAgreementID);
    message.setProperty("SAP_EDI_REC_Interchange_Control_Number_Type", recInterchangeControlNumberType);
    message.setProperty("SAP_EDI_REC_Interchange_Control_Number", recInterchangeControlNumber);
    message.setProperty("SAP_EDI_REC_Message_Association_Assign_Code", recMsgAssociationAssignCode);
    message.setProperty("SAP_EDI_REC_Message_Code", recMsgCode);
    message.setProperty("SAP_EDI_REC_Message_Controlling_Agency", recMsgControllingAgency);
    message.setProperty("SAP_EDI_REC_Message_Function", recMsgFunction);
    message.setProperty("SAP_EDI_REC_Message_Release", recMsgRelease);
    message.setProperty("SAP_EDI_REC_Message_Type", recMsgType);
    message.setProperty("SAP_EDI_REC_Message_Number", recMsgNumber);
    message.setProperty("SAP_EDI_REC_Message_Version", recMsgVersion);
    message.setProperty("SAP_EDI_REC_Output_Mode", recOutputMode);
    message.setProperty("SAP_EDI_REC_Payload_Format", recPayloadFormat);
    message.setProperty("SAP_EDI_REC_Processing_Priority_Code", recProcessingTypeCode);
    message.setProperty("SAP_EDI_REC_Receiver_Logical_Address", recRecLogicalAddress);
    message.setProperty("SAP_EDI_REC_Receiver_Partner_Function", recRecPartnerFunction);
    message.setProperty("SAP_EDI_REC_Receiver_Partner_Type", recRecPartnerType);
    message.setProperty("SAP_EDI_REC_Receiver_ID", recRecReferenceID);
    message.setProperty("SAP_EDI_REC_Receiver_ID_Qualifier", recRecReferenceIDScheme);
    message.setProperty("SAP_EDI_REC_Receiver_Routing_Address", recRecRoutingAddress);
    message.setProperty("SAP_EDI_REC_Receiver_System_ID", recRecSystemID);
    message.setProperty("SAP_EDI_REC_Receiver_System_ID_Qualifier", recRecSystemIDQualifier);
    message.setProperty("SAP_EDI_REC_Repetition_Separator", recRepetitionReferenceSeparator);
    message.setProperty("SAP_ISA_REC_Security_Information", recSecurityInformation);
    message.setProperty("SAP_EDI_REC_Security_Information_Qualifier", recSecurityInformationQualifier);
    message.setProperty("SAP_ISA_REC_Security_Information_Qualifier", recSecurityInformationScheme);
    message.setProperty("SAP_EDI_REC_Serialization", recSerialization);
    message.setProperty("SAP_EDI_REC_Sender_Group_Reference_Number", recSndGroupReferenceNumber);
    message.setProperty("SAP_EDI_REC_Sender_Logical_Addess", recSndLogicalAddress);
    message.setProperty("SAP_EDI_REC_Sender_Message_Reference_Number", recSndMsgReferenceNumber);
    message.setProperty("SAP_EDI_REC_Sender_Partner_Function", recSndPartnerFunction);
    message.setProperty("SAP_EDI_REC_Sender_Partner_Type", recSndPartnerType);
    message.setProperty("SAP_EDI_REC_Sender_ID", recSndReferenceID);
    message.setProperty("SAP_EDI_REC_Sender_ID_Qualifier", recSndReferenceIDScheme);
    message.setProperty("SAP_EDI_REC_Sender_Routing_Address", recSndRoutingAddress);
    message.setProperty("SAP_EDI_REC_Sender_System_ID", recSndSystemID);
    message.setProperty("SAP_EDI_REC_Sender_System_ID_Qualifier", recSndSystemIDQualifier);
    message.setProperty("SAP_EDI_REC_Sender_Interchange_Reference_Number", recSndTransmissionFile);
    message.setProperty("SAP_EDI_REC_Standard_Flag", recStandardFlag);
    message.setProperty("SAP_EDI_REC_Standard_Version", recStandardVersion);
    message.setProperty("SAP_EDI_REC_Status", recStatus);
    message.setProperty("SAP_EDI_REC_Standard_Message_Type", recStdMsgType);
    message.setProperty("SAP_EDI_REC_Syntax_ID", recSyntaxID);
    message.setProperty("SAP_EDI_REC_Syntax_Version", recSyntaxVersionID);
    message.setProperty("SAP_EDI_REC_Usage_Indicator", recUsageIndicator);
    message.setProperty("SAP_EDI_REC_Extended_Postprocessing", recExtendedPostprocessing);
    message.setProperty("SAP_EDI_REC_Extended_Preprocessing", recExtendedPreprocessing);
    message.setProperty("SAP_EDI_REC_Sender_Name", recSenderShortName);
    message.setProperty("SAP_EDI_REC_Receiver_Name", recReceiverShortName);

    // set contact person to exchange properties
    message.setProperty("SAP_TPA_SND_Trading_Partner_FirstName", sndContactPersonFirstName);
    message.setProperty("SAP_TPA_SND_Trading_Partner_LastName", sndContactPersonLastName);
    message.setProperty("SAP_EDI_REC_Sender_EMail_Address", sndContactPersonEmail);
    message.setProperty("SAP_EDI_REC_Sender_Telephone_Number", sndContactPersonTelephone);
    message.setProperty("SAP_TPA_REC_Trading_Partner_FirstName", recContactPersonFirstName);
    message.setProperty("SAP_TPA_REC_Trading_Partner_LastName", recContactPersonLastName);
    message.setProperty("SAP_EDI_REC_Receiver_EMail_Address", recContactPersonEmail);
    message.setProperty("SAP_EDI_REC_Receiver_Telephone_Number", recContactPersonTelephone);

    // set REC Sender/Receiver Identifier to headers
    // message.setHeader("SAP_EDI_CH_REC_Sender_ID", recSndReferenceID);
    // message.setHeader("SAP_EDI_CH_REC_Receiver_ID", recRecReferenceID);
        
    // AS2 receiver dynamic properties
    if (recProxyType == null || recProxyType.isEmpty()) {
        recProxyType = "default";
    }
    message.setHeader("SAP_AS2_Outbound_Proxy_Type", recProxyType);
    if (recAuthenType == null || recAuthenType.isEmpty()) {
        recAuthenType = "BasicAuthentication";
    }
    message.setHeader("SAP_AS2_Outbound_Authentication_Type", recAuthenType);
    message.setProperty("SAP_AS2_REC_Location_ID", recLocationId);
    message.setProperty("SAP_AS2_REC_Private_Key_Alias", recAuthenPrivateKeyAlias);
    if (recContentTransferEncoding == null || recContentTransferEncoding.isEmpty()) {
        recContentTransferEncoding = "binary";
    }
    message.setHeader("SAP_AS2_Outbound_Content_Transfer_Encoding", recContentTransferEncoding);
    if (recMdnType == null || recMdnType.isEmpty()) {
        recMdnType = "None";
    }
    message.setHeader("SAP_AS2_Outbound_Mdn_Type", recMdnType);
    if (recMdnTargetUrlFromActivityPD != null) {
        // global parameters will set mdn url, and if custom input url in communication, will Override
        message.setProperty("SAP_AS2_Outbound_Mdn_Target_URL", recMdnTargetUrlFromActivityPD);
    }
    message.setProperty("SAP_AS2_Outbound_Mdn_Public_Key_Alias", recMdnPublicKeyAlias);
    message.setHeader("SAP_AS2_Outbound_Mdn_Request_Signing", recMdnRequestSig);
    message.setHeader("SAP_AS2_Outbound_Mdn_Signing_Algorithm", recMdnSigAlg);
    message.setHeader("SAP_AS2_Outbound_Mdn_Verify_Signature", recMdnVerifySig);
    message.setHeader("SAP_AS2_Outbound_Mdn_Request_Mic", recMdnRequestMic);
    message.setHeader("SAP_AS2_Outbound_Mdn_Verify_Mic", recMdnVerifyMic);
    message.setHeader("SAP_AS2_Outbound_Fail_Message_On_Negative_MDN", recMdnFailNegMdn);

    // set general trading partner agreement properties for monitoring.
    message.setProperty("SAP_TPA_Name", tpaAgreementName);
    message.setProperty("SAP_TPM_Agreement_Name", tpmAgreementName);
    message.setProperty("SAP_TPA_BT_Name", tpaBTName);
    message.setProperty("SAP_TPA_BT_Type", tpaBTTypeName);
    message.setProperty("SAP_TPA_BTA_Name", tpaBTActivityName);
    message.setProperty("SAP_TPA_BTA_Direction", tpaBTActivityDirection);
    message.setProperty("SAP_BA_REF_ID", tpaBTActivityRefId);
    message.setProperty("SAP_TPA_SND_Trading_Partner_ID", tpaSenderTpId);
    message.setProperty("SAP_TPA_SND_Trading_Partner_Name", tpaSenderTpName);
    message.setProperty("SAP_TPA_REC_Trading_Partner_ID", tpaReceiverTpId);
    message.setProperty("SAP_TPA_REC_Trading_Partner_Name", tpaReceiverTpName);
    
    // set SAP_TPM_Target_MIG_HasEnvelope
    message.setProperty("SAP_TPM_Target_MIG_HasEnvelope", sapTPMTargetMigHasEnvelope);
    // set SAP_TPM_Source_MIG_HasEnvelope
    message.setProperty("SAP_TPM_Source_MIG_HasEnvelope", sapTPMSourceMigHasEnvelope);
    // set pass through
    message.setProperty("SAP_TPM_Pass_Through", sapTPMPassThrough);
    
    // set Partially Accepted Functional Acknowledgement
    if (null == partialAckTargetStatus) {
        partialAckTargetStatus = "Failed";
    }
    message.setProperty("SAP_TPM_REC_PartialAck_TargetStatus", partialAckTargetStatus);
    
    // Code Page
    message.setProperty("SAP_TPM_SND_Source_Encoding", tpmSourceEncoding);
    message.setProperty("SAP_TPM_REC_Target_Encoding", tpmTargetEncoding);


    // dynamic properties for idoc adapter
    if (idocRecProxyType == null || idocRecProxyType.isEmpty()) {
        idocRecProxyType = "default";
    }
    if (idocRecAuthenType == null || idocRecAuthenType.isEmpty()) {
        idocRecAuthenType = "Basic";
    }
    if(idocRecContentType == null || idocRecContentType.isEmpty()) {
        idocRecContentType = "Application/x-sap.idoc";
    }
    message.setProperty("SAP_IDocContentType", idocRecContentType);
    message.setProperty("SAP_IDocProxyType", idocRecProxyType);
    message.setProperty("SAP_IDocAuthMethod", idocRecAuthenType);
    message.setProperty("SAP_IDOC_REC_Location_ID", idocRecLocationId);
    // dynamic properties for soap adapter
    if (soapRecProxyType == null || soapRecProxyType.isEmpty()) {
        soapRecProxyType = "default";
    }
    if (soapRecAuthenType  == null || soapRecAuthenType .isEmpty()) {
        soapRecAuthenType  = "Basic";
    }
    message.setHeader("SAP_SOAP_REC_Proxy_Type", soapRecProxyType);
    message.setHeader("SAP_SOAP_REC_Authentication", soapRecAuthenType);
    message.setProperty("SAP_SOAP_REC_Location_ID", soapRecLocationId );
    // dynamic PD entries for edifact custom separator
    if (edifactUseCustomSeparator == null || edifactUseCustomSeparator.isEmpty()) {
        edifactUseCustomSeparator = "false";
    }
    message.setProperty("SAP_TPM_EDIFACT_Use_Custom_Separator", edifactUseCustomSeparator);
    if ("true".equals(edifactUseCustomSeparator)) {
        message.setProperty("SAP_TPM_EDIFACT_Segment_Terminator", edifactSegmentTerminator );
        message.setProperty("SAP_TPM_EDIFACT_Composite_Separator", edifactCompositeSeparator );
        message.setProperty("SAP_TPM_EDIFACT_Data_Element_Separator", edifactDataElementSeparator );
        message.setProperty("SAP_TPM_EDIFACT_Escape_Character", edifactEscapeCharacter );
    }
    // dynamic PD entries for x12 custom separator
    if (x12UseCustomSeparator == null || x12UseCustomSeparator.isEmpty()) {
        x12UseCustomSeparator = "false";
    }
    message.setProperty("SAP_TPM_X12_Use_Custom_Separator", x12UseCustomSeparator);
    if ("true".equals(x12UseCustomSeparator)) {
        message.setProperty("SAP_TPM_X12_Segment_Terminator", x12SegmentTerminator );
        message.setProperty("SAP_TPM_X12_Composite_Separator", x12CompositeSeparator );
        message.setProperty("SAP_TPM_X12_Data_Element_Separator", x12DataElementSeparator );
        message.setProperty("SAP_TPM_X12_Repetition_Separator", x12RepetitionSeparator );
    }

    //process direct receiver Demo
    message.setProperty("SAP_PROCESS_DIRECT_REC_Address", recProcessDirectAddress);
    message.setProperty("SAP_PROCESS_DIRECT_SND_Address", sndAckProcessDirectAddress)
    
    // Edifact Target Encoding in XMLtoEDI converter
    if (edifactTargetEncoding == null) {
        edifactTargetEncoding = "UTF-8"
    } 
    message.setHeader("SAP_XMLTOEDI_EDIFACT_TARGET_ENCODING", edifactTargetEncoding)
    charsetToSyntaxIdMapping = [
        "ISO-8859-1": "UNOC",
        "ISO-8859-2": "UNOD",
        "ISO-8859-3": "UNOG",
        "ISO-8859-4": "UNOH",
        "ISO-8859-5": "UNOE",
        "ISO-8859-6": "UNOI",
        "ISO-8859-7": "UNOF",
        "ISO-8859-8": "UNOJ",
        "ISO-8859-9": "UNOK",
        "UTF-8"     : "UNOY"
        ]
    message.setProperty("SAP_EDI_REC_Syntax_ID", charsetToSyntaxIdMapping[edifactTargetEncoding])
    
    // Edifact Target Syntax Version in XMLtoEDI converter
    if (edifactSyntaxVersion == null){
        edifactSyntaxVersion = "3"
    }
    message.setHeader("SAP_TPM_EDIFACT_Syntax_Version", edifactSyntaxVersion )

    // EdiSplitter control message version 
    if (edifactControlMsgVersion == null){
        edifactControlMsgVersion = "defaultVersion"
    }
    message.setHeader("SAP_EDISPLITTER_EDIFACT_CONTRL_MSG_VERSION", edifactControlMsgVersion )

    // payload archive
    def documentArchiveValue = null;
    def senderArchivePayload = service.getParameter("SAP_TPM_Sender_Archive_Payload", partnerID , String.class);
    if (senderArchivePayload != null && "true".equalsIgnoreCase(senderArchivePayload)) {
        documentArchiveValue = "ARCHIVING_PENDING";
        message.setProperty("SAP_TPM_Sender_Archive_Payload", senderArchivePayload);
    } else {
        message.setProperty("SAP_TPM_Sender_Archive_Payload", "false")
    }
    def receiverArchivePayload = service.getParameter("SAP_TPM_Receiver_Archive_Payload", partnerID , String.class);
    if (receiverArchivePayload != null && "true".equalsIgnoreCase(receiverArchivePayload)) {
        documentArchiveValue = "ARCHIVING_PENDING";
        message.setProperty("SAP_TPM_Receiver_Archive_Payload", receiverArchivePayload);
    } else {
        message.setProperty("SAP_TPM_Receiver_Archive_Payload", "false")
    }
    
    // control number
    def NRCS = ITApiFactory.getApi(NumberRangeConfigurationService.class, null)
    def properties                      =   message.getProperties()
 	def recGroupControlNumberType       =   properties.get("SAP_EDI_REC_Group_Control_Number_Type")
 	def recMessageNumberType            =   properties.get("SAP_EDI_REC_Message_Number_Type")
 	def recGroupControlNumber       = ""
    def recMessageNumber            = ""
    
    if (recInterchangeControlNumberType == null && sndDocumentStandard == "cXML"){
        // do nothing
    } else {
        if (recInterchangeControlNumberType == null){
            recInterchangeControlNumberType = "ICN_DEFAULT";
        }
        try {
     	    recInterchangeControlNumber = NRCS.getNextValuefromNumberRange(recInterchangeControlNumberType, null);
     	   	recGroupControlNumber       = recInterchangeControlNumber;
     	   	recMessageNumber            = recInterchangeControlNumber;
     	  
     	    if (recGroupControlNumberType != null){
     		    recGroupControlNumber = NRCS.getNextValuefromNumberRange(recGroupControlNumberType, null);
     	    }
     		
     	    if (recMessageNumberType != null){
     		    recMessageNumber = NRCS.getNextValuefromNumberRange(recMessageNumberType, null);
     	    }
        } catch(Exception e){
            throw new IllegalStateException("Error reading number range " + recInterchangeControlNumberType);
        }
        message.setProperty("SAP_EDI_REC_Interchange_Control_Number", recInterchangeControlNumber);
        message.setProperty("SAP_EDI_REC_Group_Control_Number", recGroupControlNumber);
        message.setProperty("SAP_EDI_REC_Message_Number", recMessageNumber)
    }
    
    // system name
    message.setProperty("SAP_TPM_SND_SYSTEM_INSTANCE_NAME", sndSystemInstanceName);
    message.setProperty("SAP_TPM_REC_SYSTEM_INSTANCE_NAME", recSystemInstanceName);


    // dynamic parameters for custom activity
    def customActivityParams = service.getParameter("SAP_TPM_CustomActivityParams", partnerID, BinaryData.class);
    if (customActivityParams != null){
        def customActivityParamsStr = new String(customActivityParams.getData());
        def customActivityParamsJson = new JsonSlurper().parseText(customActivityParamsStr);
        customActivityParamsJson.each{
            paramKey, paramValue -> message.setProperty(paramKey, paramValue);
        }
    }
    
    // exchange names
    def needExchange = "false";
    if (tpaBTActivityRefId == "RESPONSE-FLOW") {
        needExchange = "true";
    }
    if ("true".equalsIgnoreCase(needExchange)) {
        def temp = tpaSenderTpName;
        tpaSenderTpName = tpaReceiverTpName;
        tpaReceiverTpName = temp;
    }


    // Create event entry in monitoring queue
    def bundleContext = FrameworkUtil.getBundle(Class.forName("com.sap.gateway.ip.core.customdev.util.Message")).getBundleContext();
    def serviceRef = bundleContext.getServiceReference(Class.forName("com.sap.it.op.b2b.monitor.api.B2BMonitoringApi"));
    B2BMonitoringApi api = (B2BMonitoringApi) bundleContext.getService(serviceRef);

    BusinessDocumentCreateEvent documentCreateEvent = api.createBusinessDocumentCreateEvent();
    // no builder or "with..." pattern
    // for manually written mappings, the attributes are then nicely aligned underneath each other
    documentCreateEvent.setMonitoringReference(headers.get("SAP_MessageProcessingLogID"));
    documentCreateEvent.setMonitoringReferenceType(MonitoringReferenceType.MPL);
    
    def b2bInterchangeId = "";
    def b2bRestart = false;
    if (headers.get("Document_ID") != null) {
        b2bInterchangeId = headers.get("Document_ID");
        b2bRestart = true;
    } else {
        try {
            MessageDigest digest = MessageDigest.getInstance("MD5");
            digest.update(message.getHeaders().get("SAP_MessageProcessingLogID").toString().bytes);
            b2bInterchangeId = new BigInteger(1, digest.digest()).toString(16).padLeft(32, '0');
        }
        catch (NoSuchAlgorithmException e) {
            log.error('Error creating MD5 hash', e);
            throw new IllegalStateException("PartnerID hash can't be generated.");
        }    
    }

    // Get original body before change encoding
    def originalBodyBytes = message.getBody((byte[]).class);
    // Note: handle source encoding and set encoding for B2B monitor display
    handleSourceEncoding(message);
    def b2bPayloadSourceEncoding = message.getProperty("SAP_TPM_SND_B2B_Payload_Source_Encoding");

    message.setProperty("Document_ID", b2bInterchangeId);
    BusinessDocument document = null;
    if (headers.get("SAPJMSRetries") == null && !b2bRestart) {
        document = documentCreateEvent.createBusinessDocument();
        document.setId(b2bInterchangeId);
        document.setExtendedProperty("agreement_name", tpaAgreementName);
        if (tenantHost != null) {
            document.setExtendedProperty("agreement_name_hyperlink", "https://" + tenantHost + "/shell/tpm/agreements/" + tpmAgreementId + "?tabName=overview&mode=display");
        }
        // sender as2 message id
        if (sndAdapterType == "AS2") {
            document.setExtendedProperty("as2_message_id", headers.get("SAP_AS2MessageID"));
        }

        // for TPM we would expect a rather generic transfer of the exchange headers
        // this could be achieved by maintaining a map of function pointers and property or header
        // names and then iterating over the entries and call something like
        // key.accept(exchange.getProperty(value)

        document.setSenderDocumentStandard(sndDocumentStandard);
        document.setReceiverDocumentStandard(recDocumentStandard);
        document.setSenderMessageType(sndMessageType);
        document.setReceiverMessageType(recMsgType);
        document.setSenderAdapterType(sndAdapterType);
        document.setReceiverAdapterType(recAdapterType);
        document.setSenderInterchangeControlNumber(sndInterchangeControlNr);
        document.setReceiverInterchangeControlNumber(recInterchangeControlNumber);
        document.setSenderMessageNumber(sndMessageControlNr);
        document.setReceiverMessageNumber(recMsgNumber);
        if (subsidiary != null) {
            document.setSenderTradingPartnerName(subsidiary);
        } else {
            document.setSenderTradingPartnerName(tpaSenderTpName);
        }
        document.setReceiverTradingPartnerName(tpaReceiverTpName);
        document.setAgreedSenderIdAtSender(sndSenderId);
        document.setAgreedSenderIdQualifierAtSender(sndSenderIdQualifier);
        document.setAgreedReceiverIdAtSender(sndReceiverId);
        document.setAgreedReceiverIdQualifierAtSender(sndReceiverIdQualifier);
        document.setAgreedSenderIdAtReceiver(recSndReferenceID);
        document.setAgreedSenderIdQualifierAtReceiver(recSndReferenceIDScheme);
        document.setAgreedReceiverIdAtReceiver(recRecReferenceID);
        document.setAgreedReceiverIdQualifierAtReceiver(recRecReferenceIDScheme);
        document.setTransactionDocumentType(BusinessTransactionDocumentType.REQUEST);
        document.setProcessingStatus(ProcessingStatus.PROCESSING);
        document.setSenderFunctionalAckStatus(FunctionalAcknowledgementStatus.EXPECTED);
        document.setReceiverFunctionalAckStatus(FunctionalAcknowledgementStatus.NOT_EXPECTED);
        document.setDocumentCreationTimestamp(System.currentTimeMillis());
        if (sapTPMPassThrough == "true") {
            document.setTransactionActivityType(TransactionActivityType.PASS_THROUGH)
        } else {
            document.setTransactionActivityType(TransactionActivityType.STANDARD)
        }
    
        // achive
        if (null != documentArchiveValue && "ARCHIVING_PENDING".equals(documentArchiveValue)) {
            document.setArchivingStatus(ArchivingStatus.ARCHIVING_PENDING);
        }
        // name and direction and communicationPartnerName
        document.setInterchangeName(message.getProperty("SAP_TPA_BTA_Name"));
        def btaDirection = message.getProperty("SAP_TPA_BTA_Direction");
        if ("INBOUND".equalsIgnoreCase(btaDirection)) {
            document.setInterchangeDirection(InterchangeDirection.IN);
            document.setSenderCommunicationPartnerName(tpaCommunicationPartnerName);
        } else if ("OUTBOUND".equalsIgnoreCase(btaDirection)) {
            document.setInterchangeDirection(InterchangeDirection.OUT);
            document.setReceiverCommunicationPartnerName(tpaCommunicationPartnerName);
        }
        
        // system name
        document.setSenderSystemId(extractedSndSystemInstanceName);
        document.setReceiverSystemId(extractedRecSystemInstanceName);
    
        // hierarchy is created bottom to top, which might be counter intuitive,
        // however the document is the main object for the BusinessDocument related events
        // for Beta, there is no transaction entry to be made, so we would only use the
        // name as part of the document, but then we could keep the API stable for GA
    
        BusinessTransaction transaction = document.createBusinessTransaction();
        def transactionId = transaction.getId();
        message.setProperty("Transaction_ID", transactionId);
        transaction.setAgreementSequence(1);
        transaction.setTypeName(tpaBTTypeName);
        transaction.setInitiatorTradingPartnerName(tpaSenderTpName);
        transaction.setResponderTradingPartnerName(tpaReceiverTpName);
        transaction.setStatus(BusinessTransactionStatus.OPEN);
    
        TradingPartnerAgreement agreement = transaction.createAgreement();
        def agreementId = transaction.getId();
        message.setProperty("Agreement_ID", agreementId);
        agreement.setInitiatorTradingPartnerName(tpaSenderTpName);
        agreement.setResponderTradingPartnerName(tpaReceiverTpName);
        agreement.setStatus(TradingPartnerAgreementStatus.OPEN);
    
        BusinessDocumentPayload inboundPayload = document.createPayload();
        def latestTpaBTActivityDirection = message.getProperty("SAP_TPA_BTA_Direction");
        if ("INBOUND".equalsIgnoreCase(latestTpaBTActivityDirection)) {
            inboundPayload.setDirection(BusinessDocumentDirection.INBOUND);
        } else if ("OUTBOUND".equalsIgnoreCase(latestTpaBTActivityDirection)) {
            inboundPayload.setDirection(BusinessDocumentDirection.OUTBOUND);
        }
        // achive
        inboundPayload.setArchivingRelevant(Boolean.valueOf(message.getProperty("SAP_TPM_Sender_Archive_Payload")));
    
    
        inboundPayload.setProcessingPhase(ProcessingPhase.PAYLOAD_PLAIN);
        
        // Note: Process Direct Step 1b already converts payload to UTF-8 
        if (!isSourceXml(message) && null != b2bPayloadSourceEncoding && "" != b2bPayloadSourceEncoding) {
            inboundPayload.setPayloadContentType("text/plain; charset=${b2bPayloadSourceEncoding}");
        }
        inboundPayload.setPayload(originalBodyBytes);
    
        documentCreateEvent.submit();
    } else {
        // during retry, need set Transaction_ID and Agreement_ID
        document = documentCreateEvent.createUpdateBusinessDocument(b2bInterchangeId);
        if (document.getBusinessTransaction() == null) {
            // need create BusinessTransaction
            if (subsidiary != null) {
                document.setSenderTradingPartnerName(subsidiary);
            } else {
                document.setSenderTradingPartnerName(tpaSenderTpName);
            }
            // archive
            if (null != documentArchiveValue && "ARCHIVING_PENDING".equals(documentArchiveValue)) {
                document.setArchivingStatus(ArchivingStatus.ARCHIVING_PENDING);
            }
            // name and direction and communicationPartnerName
            document.setInterchangeName(message.getProperty("SAP_TPA_BTA_Name"));
            def btaDirection = message.getProperty("SAP_TPA_BTA_Direction");
            if ("INBOUND".equalsIgnoreCase(btaDirection)) {
                document.setInterchangeDirection(InterchangeDirection.IN);
                document.setSenderCommunicationPartnerName(tpaCommunicationPartnerName);
            } else if ("OUTBOUND".equalsIgnoreCase(btaDirection)) {
                document.setInterchangeDirection(InterchangeDirection.OUT);
                document.setReceiverCommunicationPartnerName(tpaCommunicationPartnerName);
            }
            
            // system name
            document.setSenderSystemId(extractedSndSystemInstanceName);
            document.setReceiverSystemId(extractedRecSystemInstanceName);
        
            // hierarchy is created bottom to top, which might be counter intuitive,
            // however the document is the main object for the BusinessDocument related events
            // for Beta, there is no transaction entry to be made, so we would only use the
            // name as part of the document, but then we could keep the API stable for GA
        
            BusinessTransaction transaction = document.createBusinessTransaction();
            def transactionId = transaction.getId();
            message.setProperty("Transaction_ID", transactionId);
            transaction.setAgreementSequence(1);
            transaction.setTypeName(tpaBTTypeName);
            transaction.setInitiatorTradingPartnerName(tpaSenderTpName);
            transaction.setResponderTradingPartnerName(tpaReceiverTpName);
            transaction.setStatus(BusinessTransactionStatus.OPEN);
        
            TradingPartnerAgreement agreement = transaction.createAgreement();
            def agreementId = transaction.getId();
            message.setProperty("Agreement_ID", agreementId);
            agreement.setInitiatorTradingPartnerName(tpaSenderTpName);
            agreement.setResponderTradingPartnerName(tpaReceiverTpName);
            agreement.setStatus(TradingPartnerAgreementStatus.OPEN);
        
            BusinessDocumentPayload inboundPayload = document.createPayload();
            def latestTpaBTActivityDirection = message.getProperty("SAP_TPA_BTA_Direction");
            if ("INBOUND".equalsIgnoreCase(latestTpaBTActivityDirection)) {
                inboundPayload.setDirection(BusinessDocumentDirection.INBOUND);
            } else if ("OUTBOUND".equalsIgnoreCase(latestTpaBTActivityDirection)) {
                inboundPayload.setDirection(BusinessDocumentDirection.OUTBOUND);
            }
            // archive
            inboundPayload.setArchivingRelevant(Boolean.valueOf(message.getProperty("SAP_TPM_Sender_Archive_Payload")));
        
        
            inboundPayload.setProcessingPhase(ProcessingPhase.PAYLOAD_PLAIN);
            
            // Note: Process Direct Step 1b already converts payload to UTF-8 
            if (!isSourceXml(message) && null != b2bPayloadSourceEncoding && "" != b2bPayloadSourceEncoding) {
                inboundPayload.setPayloadContentType("text/plain; charset=${b2bPayloadSourceEncoding}");
            }
            inboundPayload.setPayload(originalBodyBytes);
        
            document.setProcessingStatus(ProcessingStatus.PROCESSING);
            documentCreateEvent.submit();
        } else {
            // not need create BusinessTransaction
            BusinessTransaction transaction = document.getBusinessTransaction();
            def transactionId = transaction.getId();
            message.setProperty("Transaction_ID", transactionId);
            
            TradingPartnerAgreement agreement = transaction.getAgreement();
            def agreementId = transaction.getId();
            message.setProperty("Agreement_ID", agreementId);
        }

    }

    message.setProperty("SAP_TPM_Sender_MDN_Payload", sndMDNPayload);
    message.setProperty("SAP_TPM_Sender_MDN_Type", sndMDNType);
    headers.remove("SAP_TPM_Sender_MDN_Payload");
    headers.remove("SAP_TPM_Sender_MDN_Type");

    return message;
}
