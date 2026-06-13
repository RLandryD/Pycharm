<?xml version="1.0" encoding="UTF-8"?>

<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:sap="http://sap.com/it/sp/xslt">

    <xsl:output method="text" encoding="UTF-8"/>

    <!-- Template to match the root element and process JobRequisition -->
    <xsl:template match="/">
        <!-- Initialize a variable to count complete JobRequisition entries -->
        <xsl:variable name="completeCount" select="count(//JobRequisition/JobRequisition[
            Position/EmpJob/User/firstName and
            Position/EmpJob/User/lastName and
            Position/EmpJob/User/custom10 and
            Position/locationNav/FOLocation/customString4 and
            FOJobCode/cust_employmentAgreementNav/PickListValueV2/label_en_US
        ])"/>

        <!-- Set the CompleteEntries property -->
        <sap:set-property name="CompleteEntries" value="{$completeCount}"/>

        <!-- Output CSV header -->
        <xsl:text>Requisition ID,Doc. SignerID,Doc. Signer First Name,Doc. Signer Last Name,Doc. Signer Position Title,Position State,Employment Agreement&#10;</xsl:text>

        <!-- Process each JobRequisition element -->
        <xsl:apply-templates select="//JobRequisition/JobRequisition"/>
    </xsl:template>

    <!-- Template to process nested JobRequisition elements -->
    <xsl:template match="JobRequisition/JobRequisition">
        <xsl:for-each select="Position/EmpJob">
            <!-- Check if localeLabel matches the specified values and all required fields are present -->
            <xsl:if test="(customString10Nav/PicklistOption/localeLabel='Director/GM/NAM' or customString10Nav/PicklistOption/localeLabel='VIP/Non-Exec' or customString10Nav/PicklistOption/localeLabel='VP/Officer') and
                           User/firstName and
                           User/lastName and
                           User/custom10 and
                           ../../Position/locationNav/FOLocation/customString4 and
                           ../../FOJobCode/cust_employmentAgreementNav/PickListValueV2/label_en_US">
                <xsl:value-of select="translate(../../jobReqId, ',', '')"/><xsl:text>,</xsl:text>
                <xsl:value-of select="translate(userId, ',', '')"/><xsl:text>,</xsl:text>
                <!-- Navigate to the User element to get firstName and lastName -->
                <xsl:value-of select="translate(User/firstName, ',', '')"/><xsl:text>,</xsl:text>
                <xsl:value-of select="translate(User/lastName, ',', '')"/><xsl:text>,</xsl:text>
                <xsl:value-of select="translate(User/custom10, ',', '')"/><xsl:text>,</xsl:text>
                <!-- Navigate to the nested Position element to get customString4 -->
                <xsl:value-of select="translate(../../Position/locationNav/FOLocation/customString4, ',', '')"/><xsl:text>,</xsl:text>
                <xsl:value-of select="translate(../../FOJobCode/cust_employmentAgreementNav/PickListValueV2/label_en_US, ',', '')"/><xsl:text>&#10;</xsl:text>
            </xsl:if>
        </xsl:for-each>
    </xsl:template>

</xsl:stylesheet>
