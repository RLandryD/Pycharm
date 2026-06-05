<?xml version="1.0" encoding="UTF-8"?>
<!-- ============================================================================================= -->
<!-- This script is used for deleting leaf elements, which does not habe any values. -->
<!-- Furthermore, it set the missing attributes in the target IDOC payload, if these are missing -->
<!-- History: -->
<!-- 2025-01-16 [SAP-GS] - Fixing for attribute processing (one unnecessary template match deleted) -->
<!-- 2024-02-05 [SAP-GS] - Fix so that empty nested groups won't be written into the output. -->
<!-- 2024-01-31 [SAP-GS] - Extended with keep entry group nodes feature. -->
<!-- 2023-08-26 [SAP-GS] - Increased the priority of the template match for keeping emtpy nodes, if it is #NIL# or #KEEP#  -->
<!-- 2023-08-21 [SAP-GS] - Optimized for Base-Overlay Approach -->
<!-- 2023-01-24 [SAP-GS] - Insertion of IDOC attribues added -->
<!-- 2022-08-04 [SAP-GS] - XSLT initially created -->
<!-- ============================================================================================= -->

<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
    xmlns:xs="http://www.w3.org/2001/XMLSchema" exclude-result-prefixes="xs" version="2.0">
    <xsl:output method="xml" version="1.0" encoding="utf-8" indent="yes"/>

    <xsl:param name="SAP_EDI_REC_Document_Standard"/>

    <!-- ============================================================================================= -->
    <!-- Template: Process leaf nodes and delete empty leaf nodes -->
    <!-- ============================================================================================= -->
    <xsl:template match="*[*]" priority="0.0">
        <xsl:if test="string-length(string-join(descendant::*[not(*)])) > 0 and string-join(descendant::*[not(*)]) != ' '">
            <xsl:copy>
                <xsl:apply-templates select="@* | node()"/>
            </xsl:copy>
        </xsl:if>
    </xsl:template>

    <xsl:template match="*[not(*)]" priority="0.0">
        <xsl:if test="string-length(string-join(current())) > 0">
            <xsl:copy>
                <xsl:apply-templates select="@* | node()"/>
            </xsl:copy>
        </xsl:if>
    </xsl:template>

    <!-- ============================================================================================= -->
    <!-- Template: Keep empty elements -->
    <!-- ============================================================================================= -->
    <xsl:template match="node()[text() = ('#KEEP#', '#NIL#')]" priority="0.2">
        <xsl:copy> </xsl:copy>
    </xsl:template>


    <!-- ============================================================================================= -->
    <!-- Template: Keep empty elements -->
    <!-- ============================================================================================= -->
    <xsl:template match="node()[text() = ('#KEEP_EMPTY_GROUP#')]" priority="0.2"> </xsl:template>

    <!-- ============================================================================================= -->
    <!-- Template: Set attribute value of @BEGIN or @SEGMENT to 1, if document standard is SAP_IDOC  -->
    <!-- ============================================================================================= -->
    <xsl:template match="@BEGIN | @SEGMENT">
        <xsl:if test="$SAP_EDI_REC_Document_Standard = 'SAP_IDoc'">
            <xsl:attribute name="{local-name()}" select="'1'"/>
        </xsl:if>
    </xsl:template>

    <!-- ============================================================================================= -->
    <!-- Template: Process remaining attribues and delete attributes -->
    <!-- ============================================================================================= -->
    <xsl:template match="@*">
        <xsl:if test="normalize-space(string(.)) != ''">
            <xsl:copy>
                <xsl:apply-templates select="@*"/>
            </xsl:copy>
        </xsl:if>
    </xsl:template>

</xsl:stylesheet>
