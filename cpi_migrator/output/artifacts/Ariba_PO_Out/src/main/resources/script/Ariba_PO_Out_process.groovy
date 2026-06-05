/* Refer the link below to learn more about the use cases of script.
https://help.sap.com/viewer/368c481cd6954bdfa5d0435479fd4eaf/Cloud/en-US/148851bf8192412cba1f9d2c17f4bd25.html

If you want to know more about the SCRIPT APIs, refer the link below
https://help.sap.com/doc/a56f52e1a58e4e2bac7f7adbf45b2e26/Cloud/en-US/index.html */
import com.sap.gateway.ip.core.customdev.util.Message;
import java.util.HashMap;
import com.sap.it.api.pd.PartnerDirectoryService
import com.sap.it.api.ITApiFactory
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import com.sap.it.api.pd.BinaryData
import groovy.json.JsonSlurper

def Message processData(Message message) {
    
    def service = ITApiFactory.getApi(PartnerDirectoryService.class, null)
    if (service == null) {
        throw new IllegalStateException("Partner Directory Service not found")
    }

    def headers =   message.getHeaders()
    def partnerID = headers.get("SAP_TPM_ACTIVITYPARTNER_ID")
    
    // dynamic PD entries for idoc receiver
    def idocRecProxyType = service.getParameter("SAP_IDocProxyType", partnerID , String.class);
    def idocRecContentType = service.getParameter("SAP_IDocContentType", partnerID, String.class);
    def idocRecAuthenType = service.getParameter("SAP_IDocAuthMethod", partnerID , String.class);
    def idocRecLocationId = service.getParameter("SAP_IDOC_REC_Location_ID", partnerID , String.class);

    
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
    def recProxyType               = service.getParameter("SAP_AS2_Outbound_Proxy_Type", partnerID , String.class);
    def recAuthenType              = service.getParameter("SAP_AS2_Outbound_Authentication_Type", partnerID , String.class);
    def recLocationId              = service.getParameter("SAP_AS2_REC_Location_ID", partnerID , String.class);
    def recAuthenPrivateKeyAlias   = service.getParameter("SAP_AS2_REC_Private_Key_Alias", partnerID , String.class);
    def recContentTransferEncoding = service.getParameter("SAP_AS2_Outbound_Content_Transfer_Encoding", partnerID , String.class);
    def recMdnType                 = service.getParameter("SAP_AS2_Outbound_Mdn_Type", partnerID , String.class);
    def recMdnTargetUrlFromActivityPD = service.getParameter("SAP_AS2_Outbound_Mdn_Target_URL", partnerID , String.class);
    def recMdnPublicKeyAlias       = service.getParameter("SAP_AS2_Outbound_Mdn_Public_Key_Alias", partnerID , String.class);
    def recMdnRequestSig           = service.getParameter("SAP_AS2_Outbound_Mdn_Request_Signing", partnerID , String.class);
    def recMdnSigAlg               = service.getParameter("SAP_AS2_Outbound_Mdn_Signing_Algorithm", partnerID , String.class);
    def recMdnVerifySig            = service.getParameter("SAP_AS2_Outbound_Mdn_Verify_Signature", partnerID , String.class);
    def recMdnRequestMic           = service.getParameter("SAP_AS2_Outbound_Mdn_Request_Mic", partnerID , String.class);
    def recMdnVerifyMic            = service.getParameter("SAP_AS2_Outbound_Mdn_Verify_Mic", partnerID , String.class);
    def recMdnFailNegMdn           = service.getParameter("SAP_AS2_Outbound_Fail_Message_On_Negative_MDN", partnerID , String.class);
    def recProcessDirectAddress    = service.getParameter("SAP_PROCESS_DIRECT_REC_Address", partnerID , String.class);
    def step3ReryCount = service.getParameter("SAP_TPM_IFLOW_STEP3_RETRY_COUNT", "SAP_TPM_Global_Parameters" , String.class);
    def step3DeadLetterQ = service.getParameter("SAP_TPM_ENABLE_STEP3_DEAD_LETTER_Q", "SAP_TPM_Global_Parameters" , String.class);
    def recMdnTargetUrl = service.getParameter("SAP_AS2_Outbound_Mdn_Target_URL", "SAP_TPM_Global_Parameters" , String.class);
    def techOverdue = service.getParameter("SAP_TPM_Technical_Acknowledgement_Overdue", "SAP_TPM_Global_Parameters" , String.class);
    def funcOverdue = service.getParameter("SAP_TPM_Functional_Acknowledgement_Overdue", "SAP_TPM_Global_Parameters" , String.class);
    
    
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
    message.setProperty("SAP_PROCESS_DIRECT_REC_Address", recProcessDirectAddress);
    
    if (null == step3ReryCount) {
        step3ReryCount = "3";
    }
    message.setProperty("SAP_TPM_IFLOW_STEP3_RETRY_COUNT", Integer.parseInt(step3ReryCount));
    message.setProperty("SAP_TPM_ENABLE_STEP3_DEAD_LETTER_Q", step3DeadLetterQ);
    if (recMdnTargetUrl != null) {
        message.setProperty("SAP_AS2_Outbound_Mdn_Target_URL", recMdnTargetUrl);
    }
    
    if (null == techOverdue || "null".equalsIgnoreCase(techOverdue)) {
        techOverdue = "15";
    }
    message.setProperty("SAP_TPM_Technical_Acknowledgement_Overdue", Long.parseLong(techOverdue));
 
    if (null == funcOverdue || "null".equalsIgnoreCase(funcOverdue)) {
        funcOverdue = "30";
    }
    message.setProperty("SAP_TPM_Functional_Acknowledgement_Overdue", Long.parseLong(funcOverdue));
    
    message.setProperty("SAP_COM_REC_Adapter_Type", recAdapterType)
    
    // dynamic parameters for custom activity
    def customActivityParams = service.getParameter("SAP_TPM_CustomActivityParams", partnerID, BinaryData.class);
    if (customActivityParams != null){
        def customActivityParamsStr = new String(customActivityParams.getData());
        def customActivityParamsJson = new JsonSlurper().parseText(customActivityParamsStr);
        customActivityParamsJson.each{
            paramKey, paramValue -> message.setProperty(paramKey, paramValue);
        }
    }
    
    return message
}