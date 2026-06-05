<?xml version="1.0" encoding="UTF-8"?>
<!--
 pitfall_a_json_array_force.xsl

 Pitfall: CPI's XML-to-JSON converter (and naive XSLT) collapses
 single-entry arrays into single objects. If <Items> has one <Item>,
 the output becomes "Items": {...} instead of "Items": [{...}],
 crashing downstream JS/JSON consumers that iterate.

 Fix: Use XSLT 3.0 map+array constructors. The array{} wrapper
 enforces structural array output even for 0 or 1 entries.

 Verified: Saxon EE 9.8. Output for single/multiple/zero items:
   single:   "LineItems": [ "A" ]
   multiple: "LineItems": [ "A", "B" ]
   zero:     "LineItems": [  ]

 Source: Figaf-style pattern, original snippet was a fragment —
 wrapper template added here to make it runnable. When embedding
 the fix in a larger stylesheet, only the xsl:map-entry line is
 the load-bearing piece. The 'array { for $x in ... }' construct
 is what forces correct array serialization.
-->
<xsl:stylesheet version="3.0"
                xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:xs="http://www.w3.org/2001/XMLSchema"
                xmlns:fn="http://www.w3.org/2005/xpath-functions">
    <xsl:output method="text" encoding="UTF-8"/>

    <xsl:template match="/Invoice">
        <xsl:variable name="json-structure" as="map(*)">
            <xsl:map>
                <xsl:map-entry key="'invoiceNumber'" select="string(Header/ID)"/>
                <!-- Load-bearing pattern: array{} keeps brackets even for 0/1 items -->
                <xsl:map-entry key="'LineItems'"
                               select="array { for $x in Items/Item return string($x/ID) }"/>
            </xsl:map>
        </xsl:variable>
        <xsl:value-of select="serialize($json-structure,
                                         map{'method':'json','indent':true()})"/>
    </xsl:template>
</xsl:stylesheet>
