<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform" xmlns:xs="http://www.w3.org/2001/XMLSchema" exclude-result-prefixes="xs">
  <xsl:output method="xml" encoding="UTF-8" indent="yes" omit-xml-declaration="yes"/>
  <xsl:template match="/">
    <xsl:variable name="var1_initial" select="."/>
    <root>
 <xsl:for-each select="root/targetType">
        <xsl:variable name="var2_cur" select="."/>
        <element>
        <targetTypeId><xsl:value-of select="targetTypeId"/></targetTypeId>
        <valuemapId></valuemapId>
        <ExistsinCommission></ExistsinCommission>
        <Existsinvaluemap></Existsinvaluemap>
        </element>
</xsl:for-each>
    </root>
  </xsl:template>
</xsl:stylesheet>