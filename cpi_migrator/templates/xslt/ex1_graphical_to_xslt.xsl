<?xml version="1.0" encoding="UTF-8"?>
<!--
 ex1_graphical_to_xslt.xsl

 Pattern: Graphical Message Mapping equivalent in XSLT.
   - Root-level wrapping (SRC_Employee → TGT_UserHub)
   - apply-templates to push subtree processing into named templates
   - concat() for joining fields (FirstName + ' ' + LastName)
   - if-then-else for conditional flag mapping (Status → IsActive 0/1)

 Verified: Saxon EE 9.8 (CPI XSLT 1.2 step). Compatible with XSLT 2.0+.
 Source: Figaf-style example.
-->
<xsl:stylesheet version="2.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
    <xsl:output method="xml" indent="yes" encoding="UTF-8"/>

    <xsl:template match="/SRC_Employee">
        <TGT_UserHub>
            <xsl:apply-templates select="PersonalData"/>
        </TGT_UserHub>
    </xsl:template>

    <xsl:template match="PersonalData">
        <UserRecord>
            <FullName>
                <xsl:value-of select="concat(FirstName, ' ', LastName)"/>
            </FullName>
            <IsActive>
                <xsl:value-of select="if (Status = 'Active') then '1' else '0'"/>
            </IsActive>
        </UserRecord>
    </xsl:template>
</xsl:stylesheet>
