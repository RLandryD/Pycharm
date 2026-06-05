<?xml version="1.0" encoding="UTF-8"?>
<!--  ***********************************************************************************************************
    *    (c) Copyright 2018 Commondo LLC 
    *    ALL RIGHTS RESERVED
    *
    * ***********************************************************************************************************
    * BuildRequest_jiraIssues_update
    * =========================================
    *
	*	Map SAP data to Jira Data for Jira REST api call
	*	REST method: PUT
    *	API method: /rest/api/2/issue/{issueIdOrKey} (https://docs.atlassian.com/software/jira/docs/api/REST/7.6.1/#api/2/issue-editIssue)
    *
    *	Description: Create multiple requests for Jira rest api method '/rest/api/2/issue/{issueIdOrKey}' based on data from SAP PS Project. SAP PS data is mapped to Jira Issues - this
    *   transformation check which of the data already exists in the Jira system and only updates existing Issues. For the creation of missing (new) Jira Issues based on SAP data,
    *   please refer to the 'BuildRequest_jiraIssues_create' transformation file
    *   The Jira REST api method can only update one issue at a time, as such, multiple REST calls are created (one for each Issue)
    *
    *   note: Together, the two files BuildRequest_jiraIssues_create and BuildRequest_jiraIssues_update form the main transformation for mapping data from SAP PS to Jira
    *
    *   note: the actuall key or id of the Jira Issue is not passed as a parameter but rather as part of the REST api method url
    *
    * *********************************************************************************************************** -->
<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
    xmlns:xs="http://www.w3.org/2001/XMLSchema"
    xmlns:saxon="http://saxon.sf.net/"
    xmlns:comm="http://www.commondo.eu"
    xmlns:json="http://www.w3.org/2005/xpath-functions"
    xmlns:cPr="urn:cProjects"
    xmlns:rfc="urn:sap-com:document:sap:rfc:functions"
    exclude-result-prefixes="xs saxon comm json"
    version="3.0">
    
    <xsl:output method="xml" version="1.0" encoding="UTF-8" indent="yes" omit-xml-declaration="yes"/>
    
    <!-- INPUT PARAMS -->
    <!-- ****************************************************************************** -->
    <xsl:param name="JIRA_IssueTypes_raw"/>
    <xsl:variable name="jiraIssueTypes" select="json-to-xml($JIRA_IssueTypes_raw)"/>
    
    <xsl:param name="JIRA_customFields_raw"/>
    <xsl:variable name="jiraCustomFields" select="json-to-xml($JIRA_customFields_raw)"/>
    
    <xsl:param name="JIRA_readProject_raw"/>
    <xsl:variable name="jiraProject" select="json-to-xml($JIRA_readProject_raw)"/>
    
    <xsl:param name="JIRA_readIssues_raw"/>
    <xsl:variable name="jiraIssues" select="json-to-xml($JIRA_readIssues_raw)"/>
    
    <xsl:param name="SAP_Project_Raw"/>
    <xsl:variable name="SAPProject_mid" select="parse-xml($SAP_Project_Raw)"/>
    <xsl:variable name="SAPProject" select="parse-xml($SAPProject_mid/rfc:ZDPR_GET_PROJECT_XML.Response/EV_XML)"/>
    
    <xsl:param name="sapTaskGuid"/>
    <xsl:variable name="sapTaskGUID">
        <xsl:value-of select="parse-xml($sapTaskGuid)"/>  
    </xsl:variable>
    
    <!-- Global Stylesheet variables -->
    <!-- ****************************************************************************** -->
    <!-- <xsl:variable name="SAPProject" select="/"/> -->
    <xsl:variable name="jiraProjectId" select="$jiraProject/*:map[*:string[@*:key='id']]/*:string[@*:key = 'id']"/>
    <xsl:variable name="jiraIssueTypeId_Epic" select="$jiraIssueTypes//*:map[*:string[@*:key = 'name' and . = 'Epic']]/*:string[@*:key = 'id']"/>
    <xsl:variable name="jiraCustomFieldId_EpicName" select="$jiraCustomFields//*:map[*:string[@*:key = 'name' and . = 'Epic Name']]/*:string[@*:key = 'id']"/>
    <xsl:variable name="jiraCustomFieldId_SAPID" select="$jiraCustomFields//*:map[*:string[@*:key = 'name' and . = 'SAP ID 1']]/*:string[@*:key = 'id']"/>    
    
    <!-- XSLT search/lookup keys -->
    <!-- ****************************************************************************** --> 
    <xsl:key name="jiraIssue_bySAPID" match="*:map[parent::*:array[@*:key='issues']]" use="*:map[@*:key='fields']/*[@*:key=$jiraCustomFieldId_SAPID]"/>
    
    <!-- Build Stylesheet Output -->
    <!-- ****************************************************************************** -->
    <xsl:template match="/">
        <ROOT>
            <xsl:apply-templates select="$SAPProject//TaskData[(count(key('jiraIssue_bySAPID', EXTERNAL_ID, $jiraIssues)) = 1) and ((GUID = $sapTaskGUID) or (NUMBER = $sapTaskGUID))]" mode="issue"/>
        </ROOT>
    </xsl:template>    
    
    <!-- Templates -->
    <!-- ****************************************************************************** -->
    <xsl:template match="TaskData" mode="issue">
        <xsl:variable name="sapActivityId" select="EXTERNAL_ID"/>
        <xsl:variable name="jiraIssue">
            <xsl:copy-of select="key('jiraIssue_bySAPID', $sapActivityId, $jiraIssues)"/>
        </xsl:variable>
        <xsl:variable name="jiraIssueId" select="$jiraIssue/*:map/*:string[@*:key='id']"/>
        <CALL>
            <xsl:call-template name="REQUEST">
                <xsl:with-param name="jiraIssueId" select="$jiraIssue/*:map/*:string[@*:key='id']"/>
            </xsl:call-template>
        </CALL>
    </xsl:template>
    
    <xsl:template name="REQUEST">
        <xsl:param name="jiraIssueId"/>
        <xsl:variable name="output">
            <map xmlns="http://www.w3.org/2005/xpath-functions">
                <map key="fields">
                    <string key="summary">
                        <xsl:value-of select="DESCRIPTION"/>
                    </string>
                    
                </map>                  
            </map>
        </xsl:variable>
        <ID>
            <xsl:value-of select="$jiraIssueId"/>
        </ID>
        <BODY>
            <xsl:value-of select="xml-to-json($output)"/>
        </BODY>
    </xsl:template>  
</xsl:stylesheet>