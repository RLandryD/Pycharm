<?xml version='1.0' ?>
<xsl:stylesheet version="2.0"
	xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
	xpath-default-namespace="urn:sap-com:document:sap:idoc:soap:messages"
	xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:tns="http://sap.com/xi/APPL/Global2"
	xmlns:n1="http://sap.com/xi/SAPGlobal20/Global">
	<xsl:template match="/EQUIPMENT_CREATE02/IDOC">
		<tns:Tool_Out>
			<MessageHeader>
				<ID>
					<xsl:call-template name="generateId">
						<xsl:with-param name="idocNumber" select="EDI_DC40/DOCNUM"/>
					</xsl:call-template>
				</ID>
				<UUID>
					<xsl:call-template name="generateId">
						<xsl:with-param name="idocNumber" select="EDI_DC40/DOCNUM"/>
					</xsl:call-template>
				</UUID>
				<CreationDateTime>
					<xsl:value-of select="current-dateTime()"/>
				</CreationDateTime>
				<SenderBusinessSystemID>
					<xsl:value-of select="EDI_DC40/SNDPRN"/>
				</SenderBusinessSystemID>
			</MessageHeader>
			<equipment>
				<plant>
					<xsl:value-of select="E1EQUIPMENT_CREATE/E1BP_ITOB/MAINTPLANT" />
				</plant>
				<materialNumber>
					<xsl:value-of select="E1EQUIPMENT_CREATE/E1BP_ITOB_EQ_ONLY/MATERIAL_LONG" />
				</materialNumber>
				<equipmentNumber>
					<xsl:call-template name="trimEquipment">
						<xsl:with-param name="original"
							select="E1EQUIPMENT_CREATE/EXTERNAL_NUMBER" />
					</xsl:call-template>
				</equipmentNumber>
				<toolNumber>
					<xsl:call-template name="trimEquipment">
						<xsl:with-param name="original"
							select="E1EQUIPMENT_CREATE/EXTERNAL_NUMBER" />
					</xsl:call-template>
				</toolNumber>
				<serialNumber>
					<xsl:call-template name="removeLeadingZeros">
						<xsl:with-param name="original"
							select="E1EQUIPMENT_CREATE/E1BP_ITOB_EQ_ONLY/SERIALNO" />
					</xsl:call-template>
				</serialNumber>
				<quantity>1</quantity>
				<description>
					<xsl:value-of select="E1EQUIPMENT_CREATE/E1BP_ITOB/DESCRIPT" />
				</description>
				<location>
					<xsl:value-of select="E1EQUIPMENT_CREATE/E1BP_ITOB/MAINTLOC" />
				</location>
				<equipmentCategory>
					<xsl:value-of select="E1EQUIPMENT_CREATE/E1BP_ITOB_EQ_ONLY/EQUICATGRY" />
				</equipmentCategory>
			</equipment>
		</tns:Tool_Out>
	</xsl:template>
	<xsl:template name="removeLeadingZeros">
		<xsl:param name="original" />
		<xsl:variable name="originalString" select="normalize-space($original)"/>
		<xsl:variable name="originalNumber" select="string(number($original))"/>
		<xsl:choose>
			<xsl:when test="$originalNumber='NaN'">
				<xsl:value-of select="$originalString"/>
			</xsl:when>
			<xsl:otherwise>
				<xsl:value-of select="format-number(number($originalNumber), '#')" />
			</xsl:otherwise>
		</xsl:choose>
	</xsl:template>
		<xsl:template name="generateId">
		<!-- IDOC Number + TS -->
		<xsl:param name="idocNumber"/>
		<xsl:variable name="dateStr"
				select="format-dateTime(current-dateTime(),'[Y0001]-[M01]-[D01]-[H01].[m01].[s].[f]')"/>
		<xsl:variable name="dateStrNumOnly" select="replace($dateStr, '[^0-9]', '')"/>
		<xsl:value-of select="concat($idocNumber, '-', $dateStrNumOnly)"/>
	</xsl:template>
	<xsl:template name="trimEquipment">
		<xsl:param name="original" />
		<xsl:variable name="equipmentString" select="normalize-space($original)"/>
		<xsl:variable name="equipmentNumber" select="string(number($original))"/>
		<xsl:choose>
			<xsl:when test="$equipmentNumber='NaN'">
				<xsl:value-of select="$equipmentString"/>
			</xsl:when>
			<xsl:otherwise>
				<xsl:value-of select="format-number(number($equipmentString), '#')" />
			</xsl:otherwise>
		</xsl:choose>
	</xsl:template>
</xsl:stylesheet>
