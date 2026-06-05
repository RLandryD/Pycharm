<?xml version="1.0" encoding="UTF-8"?>
<!--
 pitfall_b_strip_empties.xsl

 Pitfall: Graphical mappings auto-suppress <Notes></Notes> empty
 tags. XSLT identity transforms copy them through. Older IDoc/RFC
 receivers reject empty elements with validation faults.

 Fix: Identity-transform template plus an empty-element match that
 produces no output. The empty-match template wins by template
 priority because it's more specific than the wildcard.

 Verified: lxml + Saxon. Input <Order><A>x</A><B></B><C>y</C></Order>
 → output <Order><A>x</A><C>y</C></Order>.

 Note: removes empty elements with no children AND no attributes.
 If you need to preserve empties that have attributes (e.g.
 <link href="..."/>), the current predicate (not(@*)) already
 handles that — they're kept.
-->
<xsl:stylesheet version="2.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
    <xsl:output method="xml" indent="yes"/>

    <!-- Identity transform: copy everything through unchanged. -->
    <xsl:template match="@*|node()">
        <xsl:copy>
            <xsl:apply-templates select="@*|node()"/>
        </xsl:copy>
    </xsl:template>

    <!-- Override: suppress empty elements (no children, no attributes). -->
    <xsl:template match="*[not(node()) and not(@*)]"/>
</xsl:stylesheet>
