<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
    <xsl:output method="xml" indent="yes"/>
    
    <!-- Root template -->
    <xsl:template match="/">
        <ExtractedValues>
            <userId>
                <xsl:for-each select="//JobRequisition/JobRequisition/EmpJob/userId">
                    <userId>
                        <xsl:value-of select="."/>
                    </userId>
                </xsl:for-each>
            </userId>
            <managerId>
                <xsl:for-each select="//JobRequisition/JobRequisition/EmpJob/managerId">
                    <managerId>
                        <xsl:value-of select="."/>
                    </managerId>
                </xsl:for-each>
            </managerId>
        </ExtractedValues>
    </xsl:template>
</xsl:stylesheet>
