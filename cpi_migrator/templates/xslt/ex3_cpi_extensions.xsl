<?xml version="1.0" encoding="UTF-8"?>
<!--
 ex3_cpi_extensions.xsl

 Pattern: Direct calls to SAP CPI runtime services from XSLT.
 Avoids needing wrapper Groovy scripts before/after the mapping step.

 ⚠ STATUS: UNTESTED LOCALLY. Uses CPI-specific extension functions
 (sapcpi:getExchangeProperty, sapcpi:getMappedValue,
 sapcpi:setExchangeProperty) that only exist in CPI runtime. Cannot
 validate without a tenant.

 BEFORE USING:
   1. Verify sapcpi:getMappedValue parameter order matches the
      version of CPI you target. SAP has shifted argument signatures
      between releases (some use 4 args, some use 5, with different
      orderings between source/target agency/scheme).
   2. The "void" variable pattern relies on Saxon evaluating unused
      variables. Saxon may lazily skip it — if setExchangeProperty
      doesn't fire, switch to <xsl:value-of select="..."/> inside
      a suppressed container.
   3. sum(Items/Item/Price) returns NaN if any Price is missing.
      Consider wrapping in number() with 0 fallback for production.

 Source: Figaf-style example. Test thoroughly in dev tenant first.
-->
<xsl:stylesheet version="2.0"
                xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:sapcpi="http://sap.com/it/"
                exclude-result-prefixes="sapcpi">
    <xsl:param name="exchange"/>

    <xsl:template match="/Order">
        <TargetOrder>
            <InterfaceID>
                <xsl:value-of select="sapcpi:getExchangeProperty($exchange, 'SAP_MessageProcessingLogID')"/>
            </InterfaceID>
            <TargetCountryCode>
                <xsl:value-of select="sapcpi:getMappedValue('ERP', 'CountryName', BillingCountry, 'ISO', 'TwoLetterCode')"/>
            </TargetCountryCode>

            <xsl:variable name="totalAmount" select="sum(Items/Item/Price)"/>
            <xsl:variable name="void" select="sapcpi:setExchangeProperty($exchange, 'TotalOrderValue', $totalAmount)"/>

            <Amount><xsl:value-of select="$totalAmount"/></Amount>
        </TargetOrder>
    </xsl:template>
</xsl:stylesheet>
