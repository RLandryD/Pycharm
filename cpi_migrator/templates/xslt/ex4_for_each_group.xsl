<?xml version="1.0" encoding="UTF-8"?>
<!--
 ex4_for_each_group.xsl

 Pattern: XSLT 2.0+ grouping with xsl:for-each-group.
 Replaces the legacy Muenchian Method (xsl:key) from XSLT 1.0.

 Use case: bucket flat item lists by a shared attribute (Supplier
 code here), aggregating per group with current-group() / sum().

 Verified: Saxon EE 9.8. Output preserves group order from input.
 Source: Figaf-style example.
-->
<xsl:stylesheet version="2.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
    <xsl:output method="xml" indent="yes"/>

    <xsl:template match="/Items">
        <GroupedOrders>
            <xsl:for-each-group select="Item" group-by="Supplier">
                <PurchaseOrder>
                    <VendorIdentifier>
                        <xsl:value-of select="current-grouping-key()"/>
                    </VendorIdentifier>
                    <LineItems>
                        <xsl:for-each select="current-group()">
                            <PartNum><xsl:value-of select="Part"/></PartNum>
                        </xsl:for-each>
                    </LineItems>
                    <TotalGroupCost>
                        <xsl:value-of select="sum(current-group()/Cost)"/>
                    </TotalGroupCost>
                </PurchaseOrder>
            </xsl:for-each-group>
        </GroupedOrders>
    </xsl:template>
</xsl:stylesheet>
