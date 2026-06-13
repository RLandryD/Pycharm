<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
  <xsl:output method="xml" indent="yes"/>
  
  <xsl:template match="/rows">
    <Requisitions>
      <xsl:apply-templates select="row"/>
    </Requisitions>
  </xsl:template>
  
  <xsl:template match="row">
    <Requisition>
      <RequisitionID><xsl:value-of select="RequisitionID"/></RequisitionID>
      <DocSignerID><xsl:value-of select="DocSignerID"/></DocSignerID>
      <DocSignerFirstName><xsl:value-of select="DocSignerFirstName"/></DocSignerFirstName>
      <DocSignerLastName><xsl:value-of select="DocSignerLastName"/></DocSignerLastName>
      <SignerCountry><xsl:value-of select="SignerCountry"/></SignerCountry>
      <SignerState><xsl:value-of select="SignerState"/></SignerState>
      <DocSignerPositionTitle><xsl:value-of select="DocSignerPositionTitle"/></DocSignerPositionTitle>
      <EmploymentAgreement><xsl:value-of select="EmploymentAgreement"/></EmploymentAgreement>
    </Requisition>
  </xsl:template>
</xsl:stylesheet>
