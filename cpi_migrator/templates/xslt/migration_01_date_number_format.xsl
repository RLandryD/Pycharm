<?xml version="1.0" encoding="UTF-8"?>
<!--
 migration_01_date_number_format.xsl

 Pattern: SAP date/number format conversions. Covers the function family
 from PI graphical mappings (DateTrans, FormatNum) that appears in nearly
 every real migration.

 Conversions shown:
   - SAP date YYYYMMDD <-> ISO YYYY-MM-DD
   - German decimal (1.234,56) -> US decimal (1234.56)
   - Leading-zero padding (SAP material number style, 10 chars)
   - Leading-zero stripping

 Verified: Saxon EE 9.8. Input 20260115 -> 2026-01-15, 1.234,56 -> 1234.56,
 0000012345 -> 12345 (stripped) and back to 0000012345 (padded).

 Caveat: translate()-based decimal swap assumes German grouping. If source
 locale varies per message, branch on a locale header first.
-->
<xsl:stylesheet version="2.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:xs="http://www.w3.org/2001/XMLSchema">
    <xsl:output method="xml" indent="yes"/>
    <xsl:template match="/Record">
        <Converted>
            <IsoDate>
                <xsl:value-of select="concat(substring(SapDate,1,4),'-',substring(SapDate,5,2),'-',substring(SapDate,7,2))"/>
            </IsoDate>
            <SapDate>
                <xsl:value-of select="translate(IsoDate,'-','')"/>
            </SapDate>
            <UsAmount>
                <xsl:value-of select="translate(translate(GermanAmount,'.',''),',','.')"/>
            </UsAmount>
            <PaddedMatNr>
                <xsl:value-of select="format-number(number(MatNr),'0000000000')"/>
            </PaddedMatNr>
            <UnpaddedMatNr>
                <xsl:value-of select="number(MatNr)"/>
            </UnpaddedMatNr>
        </Converted>
    </xsl:template>
</xsl:stylesheet>
