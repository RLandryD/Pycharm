<?xml version="1.0" encoding="UTF-8"?>

<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:sap="http://sap.com/it/sp/xslt">

    <xsl:output method="text" encoding="UTF-8"/>

    <!-- Template to match the root element and process JobRequisition -->
    <xsl:template match="/">
        <!-- Initialize a variable to count incomplete JobRequisition entries -->
        <xsl:variable name="incompleteCount" select="count(//JobRequisition/JobRequisition[
            not(Position/EmpJob/userId) or
            not(Position/EmpJob/User/firstName) or
            not(Position/EmpJob/User/lastName) or
            not(Position/EmpJob/User/custom10) or
            not(Position/locationNav/FOLocation/customString4) or
            not(FOJobCode/cust_employmentAgreementNav/PickListValueV2/label_en_US)
        ])"/>

        <!-- Set the IncompleteEmployees property -->
        <sap:set-property name="IncompleteEmployees" value="{$incompleteCount}"/>

        <!-- Output CSV header -->
        <xsl:text>Type,Requisition ID,Error Reason&#10;</xsl:text>

        <!-- Process each JobRequisition element -->
        <xsl:apply-templates select="//JobRequisition/JobRequisition[
            not(Position/EmpJob/userId) or
            not(Position/EmpJob/User/firstName) or
            not(Position/EmpJob/User/lastName) or
            not(Position/EmpJob/User/custom10) or
            not(Position/locationNav/FOLocation/customString4) or
            not(FOJobCode/cust_employmentAgreementNav/PickListValueV2/label_en_US)
        ]"/>
    </xsl:template>

    <!-- Template to process nested JobRequisition elements -->
    <xsl:template match="JobRequisition/JobRequisition">
        <!-- Initialize variables to hold missing fields -->
        <xsl:variable name="missingSignerID" select="not(Position/EmpJob/userId)"/>
        <xsl:variable name="missingFirstName" select="not(Position/EmpJob/User/firstName)"/>
        <xsl:variable name="missingLastName" select="not(Position/EmpJob/User/lastName)"/>
        <xsl:variable name="missingCustom10" select="not(Position/EmpJob/User/custom10)"/>
        <xsl:variable name="missingState" select="not(Position/locationNav/FOLocation/customString4)"/>
        <xsl:variable name="missingAgreement" select="not(FOJobCode/cust_employmentAgreementNav/PickListValueV2/label_en_US)"/>

        <!-- Check if there are any missing fields and output CSV rows -->
        <xsl:if test="$missingSignerID">
            <xsl:text>E,</xsl:text>
            <xsl:value-of select="jobReqId"/>
            <xsl:text>,Document Signer ID not found&#10;</xsl:text>
        </xsl:if>
        <xsl:if test="$missingFirstName">
            <xsl:text>W,</xsl:text>
            <xsl:value-of select="jobReqId"/>
            <xsl:text>,First Name of Document signer is blank&#10;</xsl:text>
        </xsl:if>
        <xsl:if test="$missingLastName">
            <xsl:text>W,</xsl:text>
            <xsl:value-of select="jobReqId"/>
            <xsl:text>,Last Name of Document signer is blank&#10;</xsl:text>
        </xsl:if>
        <xsl:if test="$missingCustom10">
            <xsl:text>W,</xsl:text>
            <xsl:value-of select="jobReqId"/>
            <xsl:text>,Position Title of Document signer is blank&#10;</xsl:text>
        </xsl:if>
        <xsl:if test="$missingState">
            <xsl:text>W,</xsl:text>
            <xsl:value-of select="jobReqId"/>
            <xsl:text>,Position State is blank&#10;</xsl:text>
        </xsl:if>
        <xsl:if test="$missingAgreement">
            <xsl:text>E,</xsl:text>
            <xsl:value-of select="jobReqId"/>
            <xsl:text>,Employment Agreement is blank&#10;</xsl:text>
        </xsl:if>
    </xsl:template>

</xsl:stylesheet>
