<?xml version="1.0" encoding="UTF-8"?>
<!--
 ex2_choose_when.xsl

 Pattern: Conditional business rules via xsl:choose / xsl:when.
 Equivalent to nested ifElse blocks in graphical mappings.

 BUG FIX vs original Figaf snippet:
   Original:  not(xs:integer(Quantity) castable as xs:integer)
   Fixed:     not(Quantity castable as xs:integer)

 Why: xs:integer(Quantity) is a HARD cast — it throws FORG0001
 ("cannot convert") immediately on invalid input, so the castable
 test never executes. castable as exists precisely because it
 returns boolean without throwing — wrapping it inside a hard cast
 defeats its purpose.

 Verified: Saxon EE 9.8, 5 cases including invalid + empty quantity.
-->
<xsl:stylesheet version="2.0"
                xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:xs="http://www.w3.org/2001/XMLSchema">
    <xsl:output method="xml" indent="yes"/>

    <xsl:template match="/Item">
        <TargetItem>
            <ItemID><xsl:value-of select="ID"/></ItemID>
            <ShippingPriority>
                <xsl:choose>
                    <xsl:when test="Type = 'Express' and Price &gt; 1000">
                        <xsl:text>CRITICAL_AIR</xsl:text>
                    </xsl:when>
                    <xsl:when test="Type = 'Express' or Country = 'MX'">
                        <xsl:text>HIGH_GROUND</xsl:text>
                    </xsl:when>
                    <xsl:when test="not(Quantity castable as xs:integer)">
                        <xsl:text>ERROR_INVALID_QTY</xsl:text>
                    </xsl:when>
                    <xsl:otherwise>
                        <xsl:text>STANDARD_ECONOMY</xsl:text>
                    </xsl:otherwise>
                </xsl:choose>
            </ShippingPriority>
        </TargetItem>
    </xsl:template>
</xsl:stylesheet>
