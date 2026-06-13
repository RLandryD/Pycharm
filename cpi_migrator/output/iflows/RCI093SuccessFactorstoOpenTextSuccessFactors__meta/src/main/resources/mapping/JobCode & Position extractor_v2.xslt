<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
    <xsl:output method="xml" indent="yes"/>

    <!-- Define keys to identify unique elements -->
    <xsl:key name="uniqueEmpJob" match="EmpJob" use="concat(customString10Nav/PicklistOption/localeLabel, '|', jobCode, '|', managerId, '|', position, '|', userId)"/>
    <xsl:key name="uniquePosition" match="Position" use="code"/>
    <xsl:key name="uniqueFOJobCode" match="FOJobCode" use="externalCode"/>

    <!-- Identity template to copy all nodes and attributes -->
    <xsl:template match="@*|node()">
        <xsl:copy>
            <xsl:apply-templates select="@*|node()"/>
        </xsl:copy>
    </xsl:template>

    <!-- Template to process the root JobRequisition -->
    <xsl:template match="/JobRequisition">
        <xsl:copy>
            <xsl:apply-templates select="JobRequisition"/>
        </xsl:copy>
    </xsl:template>

    <!-- Template to process unique FOJobCode elements, including subnodes -->
    <xsl:template match="FOJobCode">
        <xsl:if test="generate-id() = generate-id(key('uniqueFOJobCode', externalCode)[1])">
            <xsl:copy>
                <xsl:apply-templates select="@*|node()"/>
            </xsl:copy>
        </xsl:if>
    </xsl:template>

    <!-- Template to process Position elements -->
    <xsl:template match="Position">
        <xsl:if test="generate-id() = generate-id(key('uniquePosition', code)[1])">
            <xsl:copy>
                <xsl:apply-templates select="parentPosition"/>
                <xsl:apply-templates select="code"/>
                <!-- Process the newest EmpJob element within the Position -->
                <xsl:for-each select="EmpJob">
                    <xsl:sort select="positionEntryDate" order="descending" data-type="text"/>
                    <xsl:if test="position() = 1">
                        <xsl:copy>
                            <xsl:apply-templates select="@*|node()"/>
                        </xsl:copy>
                    </xsl:if>
                </xsl:for-each>
                <!-- Ensure to process locationNav and its children -->
                <xsl:apply-templates select="locationNav"/>
            </xsl:copy>
        </xsl:if>
    </xsl:template>

    <!-- Template to process unique EmpJob elements -->
    <xsl:template match="EmpJob">
        <xsl:if test="generate-id() = generate-id(key('uniqueEmpJob', concat(customString10Nav/PicklistOption/localeLabel, '|', jobCode, '|', managerId, '|', position, '|', userId))[1])">
            <xsl:copy>
                <xsl:apply-templates select="@*|node()"/>
            </xsl:copy>
        </xsl:if>
    </xsl:template>

    <!-- Template to process locationNav and its children -->
    <xsl:template match="locationNav">
        <xsl:copy>
            <xsl:apply-templates select="@*|node()"/>
        </xsl:copy>
    </xsl:template>

    <!-- Template to process FOLocation and its children -->
    <xsl:template match="FOLocation">
        <xsl:copy>
            <xsl:apply-templates select="@*|node()"/>
        </xsl:copy>
    </xsl:template>

    <!-- Template to process customString4 -->
    <xsl:template match="customString4">
        <xsl:copy>
            <xsl:apply-templates select="@*|node()"/>
        </xsl:copy>
    </xsl:template>

</xsl:stylesheet>
