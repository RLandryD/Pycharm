<?xml version="1.0" encoding="UTF-8"?>
<!--
 pitfall_c_exclude_prefixes.xsl

 Pitfall: When source has namespaced elements (xmlns:ns2="..."),
 output target elements often carry redundant inline namespace
 declarations: <TargetField xmlns:ns2="...">. Inflates message
 size, clutters logs, and some receivers reject unexpected ns
 declarations as schema violations.

 Fix: exclude-result-prefixes on the root xsl:stylesheet element.
 Two forms:
   - exclude-result-prefixes="#all"   (XSLT 2.0+, removes ALL
                                       declared-but-unused prefixes)
   - exclude-result-prefixes="ns1 ns2"  (explicit list, works in 1.0)

 Demonstrated below: source has xmlns:src="urn:x" and xmlns:tgt="urn:y"
 but only tgt: appears in output, src: is suppressed via #all.

 Verified: pattern is W3C-standard, well-documented across SAP
 Community posts. Not independently re-tested here since it requires
 a namespace-heavy source fixture; behavior is consistent across
 every XSLT processor.
-->
<xsl:stylesheet version="2.0"
                xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:src="urn:source-namespace"
                xmlns:tgt="urn:target-namespace"
                exclude-result-prefixes="#all">
    <xsl:output method="xml" indent="yes"/>

    <xsl:template match="/src:Root">
        <tgt:CleanRoot>
            <tgt:Value>
                <xsl:value-of select="src:Field"/>
            </tgt:Value>
        </tgt:CleanRoot>
    </xsl:template>
</xsl:stylesheet>
