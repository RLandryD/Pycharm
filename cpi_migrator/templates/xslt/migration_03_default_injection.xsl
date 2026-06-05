<?xml version="1.0" encoding="UTF-8"?>
<!--
 migration_03_default_injection.xsl

 Pattern: "If source node exists and non-empty, map it; else use a
 constant." This is PI's node-presence default pattern, distinct from
 value-based conditionals (see ex2_choose_when.xsl).

 Two idioms shown:
   - Full xsl:choose for when you need the node-exists AND non-empty test
   - Shorthand if() for simple non-empty defaults

 Verified: Saxon EE 9.8. Empty Currency defaults to EUR; populated
 Priority passes through as HIGH.
-->
<xsl:stylesheet version="2.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
    <xsl:output method="xml" indent="yes"/>
    <xsl:template match="/Order">
        <Target>
            <Currency>
                <xsl:choose>
                    <xsl:when test="Currency and string-length(Currency) &gt; 0">
                        <xsl:value-of select="Currency"/>
                    </xsl:when>
                    <xsl:otherwise>EUR</xsl:otherwise>
                </xsl:choose>
            </Currency>
            <Priority>
                <xsl:value-of select="if (Priority != '') then Priority else 'STANDARD'"/>
            </Priority>
        </Target>
    </xsl:template>
</xsl:stylesheet>
