<?xml version="1.0" encoding="UTF-8"?>
<!--
 migration_04_namespace_strip.xsl

 Pattern: Strip ALL namespaces from a payload. Frequent first step in
 migrated flows where the source PI payload is heavily namespaced but the
 CPI target (or a downstream receiver) wants namespace-free XML.

 Distinct from pitfall_c (which only cleans up redundant prefix
 declarations on output) — this fully removes namespaces from every
 element and attribute.

 Verified: Saxon EE 9.8. <ns2:Order ns2:id="1"><ns2:Item>A</ns2:Item></ns2:Order>
 becomes <Order id="1"><Item>A</Item></Order>.

 Caveat: If two different namespaces have same local-name elements that
 mean different things, stripping collapses them — verify the target
 schema tolerates this before using.
-->
<xsl:stylesheet version="2.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
    <xsl:output method="xml" indent="yes"/>
    <xsl:template match="*">
        <xsl:element name="{local-name()}">
            <xsl:apply-templates select="@*|node()"/>
        </xsl:element>
    </xsl:template>
    <xsl:template match="@*">
        <xsl:attribute name="{local-name()}">
            <xsl:value-of select="."/>
        </xsl:attribute>
    </xsl:template>
    <xsl:template match="text()|comment()">
        <xsl:copy/>
    </xsl:template>
</xsl:stylesheet>
