<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="2.0" xpath-default-namespace="urn:sap-com:document:sap:idoc:soap:messages"
                xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
    <xsl:template match="/IORDER01/IDOC">
        <xsl:apply-templates select="E1ORHDR[1]"/>
    </xsl:template>

    <xsl:template match="/IORDER01/IDOC/E1ORHDR">

        <urn:BAPI_ALM_ORDER_GET_DETAIL xmlns:urn="urn:sap-com:document:sap:rfc:functions">
            <!-- Order ID for which details are required -->
            <NUMBER>
                <xsl:value-of select="AUFNR"/>
            </NUMBER>
        </urn:BAPI_ALM_ORDER_GET_DETAIL>
    </xsl:template>

</xsl:stylesheet>