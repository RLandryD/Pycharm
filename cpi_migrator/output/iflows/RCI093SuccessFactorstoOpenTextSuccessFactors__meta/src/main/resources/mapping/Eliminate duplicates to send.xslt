<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
    <xsl:output method="text" encoding="UTF-8"/>

    <!-- Template to match the root element and process JobRequisition -->
    <xsl:template match="/">
        <!-- Output CSV header -->
        <xsl:text>Requisition ID,SignerID,Signer First Name,Signer Last Name,Signer Position,Employment Agreement&#10;</xsl:text>
        <!-- Process each JobRequisition element -->
        <xsl:apply-templates select="//JobRequisition/JobRequisition"/>
    </xsl:template>

    <!-- Template to process nested JobRequisition elements -->
    <xsl:template match="JobRequisition/JobRequisition">
        <xsl:for-each select="Position/EmpJob">
            <!-- Check if localeLabel matches the specified values -->
            <xsl:if test="customString10Nav/PicklistOption/localeLabel='Director/GM/NAM' or customString10Nav/PicklistOption/localeLabel='VIP/Non-Exec' or customString10Nav/PicklistOption/localeLabel='VP/Officer'">
                <xsl:value-of select="../../jobReqId"/><xsl:text>,</xsl:text>
                <xsl:value-of select="userId"/><xsl:text>,</xsl:text>
                <!-- Navigate to the User element to get firstName and lastName -->
                <xsl:value-of select="User/firstName"/><xsl:text>,</xsl:text>
                <xsl:value-of select="User/lastName"/><xsl:text>,</xsl:text>
                <xsl:value-of select="customString10Nav/PicklistOption/localeLabel"/><xsl:text>,</xsl:text>
                <xsl:value-of select="../../FOJobCode/cust_employmentAgreementNav/PickListValueV2/label_en_US"/><xsl:text>&#10;</xsl:text>
            </xsl:if>
        </xsl:for-each>
    </xsl:template>

</xsl:stylesheet>
