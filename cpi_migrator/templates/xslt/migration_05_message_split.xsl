<?xml version="1.0" encoding="UTF-8"?>
<!--
 migration_05_message_split.xsl

 Pattern: Multi-mapping / message split. One source document becomes a
 wrapper containing N target documents (one per line item). A downstream
 General Splitter splits on <SplitMessage> to produce N separate messages.

 This is how PI's 1:N message split / multi-mapping migrates to CPI:
 XSLT produces the wrapper, the Splitter step fans it out.

 Verified: Saxon EE 9.8. 2 line items produce 2 <SplitMessage> elements,
 each carrying the shared OrderId header.

 Caveat: The ../../OrderId path assumes the structure below. Adjust the
 ancestor navigation to match your actual source schema depth.
-->
<xsl:stylesheet version="2.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
    <xsl:output method="xml" indent="yes"/>
    <xsl:template match="/PurchaseOrder">
        <Messages>
            <xsl:for-each select="Lines/Line">
                <SplitMessage>
                    <IndividualOrder>
                        <Header><xsl:value-of select="../../OrderId"/></Header>
                        <LineNum><xsl:value-of select="Num"/></LineNum>
                        <Product><xsl:value-of select="Product"/></Product>
                    </IndividualOrder>
                </SplitMessage>
            </xsl:for-each>
        </Messages>
    </xsl:template>
</xsl:stylesheet>
