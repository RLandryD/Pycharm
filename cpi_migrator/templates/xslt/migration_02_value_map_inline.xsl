<?xml version="1.0" encoding="UTF-8"?>
<!--
 migration_02_value_map_inline.xsl

 Pattern: Inline value-mapping lookup table. Use when there's no CPI Value
 Mapping artifact and you don't want to create one for a handful of static
 entries (very common in real migrations).

 For runtime Value Mapping artifacts, use the valuemap:get extension
 instead (see ex3_cpi_extensions.xsl). This template is for the embedded-
 table case.

 Verified: Saxon EE 9.8. Input CountryCode=DE resolves to Germany;
 unknown codes return UNKNOWN:<code> so misses are visible, not silent.

 Caveat: Inline tables don't scale — past ~30 entries, create a real
 Value Mapping artifact for maintainability.
-->
<xsl:stylesheet version="2.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
    <xsl:output method="xml" indent="yes"/>
    <xsl:variable name="countryMap">
        <entry key="DE" value="Germany"/>
        <entry key="FR" value="France"/>
        <entry key="US" value="United States"/>
    </xsl:variable>
    <xsl:template match="/Order">
        <Result>
            <xsl:variable name="code" select="CountryCode"/>
            <CountryName>
                <xsl:variable name="match" select="$countryMap/entry[@key=$code]/@value"/>
                <xsl:value-of select="if ($match != '') then $match else concat('UNKNOWN:', $code)"/>
            </CountryName>
        </Result>
    </xsl:template>
</xsl:stylesheet>
