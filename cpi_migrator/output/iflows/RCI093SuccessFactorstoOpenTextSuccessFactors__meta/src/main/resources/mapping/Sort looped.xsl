<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
    <xsl:output method="xml" indent="yes"/>

    <!-- Identity template to copy all nodes and attributes -->
    <xsl:template match="@*|node()">
        <xsl:copy>
            <xsl:apply-templates select="@*|node()"/>
        </xsl:copy>
    </xsl:template>

    <!-- Template to process Position elements -->
    <xsl:template match="Position">
        <xsl:copy>
            <xsl:apply-templates select="@*|node()[not(self::EmpJob)]"/>
            <!-- Process only the newest EmpJob element within the Position -->
            <xsl:for-each select="EmpJob">
                <xsl:sort select="positionEntryDate" order="descending" data-type="text"/>
                <xsl:if test="position() = 1">
                    <xsl:copy>
                        <xsl:apply-templates select="@*|node()"/>
                    </xsl:copy>
                </xsl:if>
            </xsl:for-each>
        </xsl:copy>
    </xsl:template>

</xsl:stylesheet>
