<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="2.0">
  <xsl:output method="xml" indent="no"/>
  <xsl:template match="@*|node()">
    <xsl:copy><xsl:apply-templates select="@*|node()"/></xsl:copy>
  </xsl:template>
  <!-- annotate every order with a computed total -->
  <xsl:template match="Order">
    <xsl:copy>
      <xsl:apply-templates select="@*"/>
      <xsl:attribute name="lineTotalQty">
        <xsl:value-of select="sum(lines/line/@qty)"/>
      </xsl:attribute>
      <xsl:apply-templates select="node()"/>
    </xsl:copy>
  </xsl:template>
</xsl:stylesheet>
