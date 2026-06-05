<?xml version="1.0" encoding="UTF-8"?>

<!--
Author: Alex Radu, Buyer Integrator / SAP/Ariba
Author email: alex.radu@sap.com
Please send bugs/defects and enhancements requests to author via email.
-->

<!--
HHandles the scenario where buyer sends multiple POs in the same CSV file
We are grouping all rows specific to a unique PO number so that we can later use this as input to a CPI splitter step
-->
<xsl:stylesheet exclude-result-prefixes="#all" version="2.0"
    xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
    <xsl:output encoding="UTF-8" indent="yes" method="xml" omit-xml-declaration="no"/>
    <xsl:template match="/root">
        <batch>
            <xsl:for-each-group group-by="row/OrderID" select=".">
                    <xsl:for-each select="current-group()">
                        <root>
                            <xsl:copy-of select="./row[OrderID = current-grouping-key()]"/>
                        </root>
                    </xsl:for-each>
            </xsl:for-each-group>
        </batch>
    </xsl:template>
</xsl:stylesheet>
