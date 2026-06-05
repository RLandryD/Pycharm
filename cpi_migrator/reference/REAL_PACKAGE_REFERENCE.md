# Real Production Package — Decoded Reference

Source: RCI093_SuccessFactors_to_OpenText (real Cintas/Deloitte production export,
Feb 2025). The authoritative spec for our iFlow-wiring, externalization, and
package-assembly features. This is REAL production structure, not a demo.

## Package export format (multi-artifact)
A package export (vs single-artifact) is a zip of:
```
ExportInformation.info        Name= ... / Date= ...
contentmetadata.md            base64-encoded properties (Org, Environment, versions)
hash                          integrity
resources.cnt                 resource manifest
<guid1>_content               nested ZIP = artifact 1 (an iFlow project)
<guid2>_content               nested ZIP = artifact 2 (an iFlow project)
```
Each `<guid>_content` is itself a zip with the single-artifact layout below.

## Single artifact (iFlow project) layout
```
META-INF/MANIFEST.MF
.project
src/main/resources/scenarioflows/integrationflow/<name>.iflw
src/main/resources/parameters.prop        <- externalized param VALUES
src/main/resources/parameters.propdef     <- param DEFINITIONS + adapter bindings
src/main/resources/script/*.groovy
src/main/resources/mapping/*.mmap *.xslt *.xsl
src/main/resources/xsd/*.xsd
src/main/resources/edmx/*.edmx             <- OData metadata
```

## *** EXTERNALIZED PARAMETERS — THE KEY FINDING ***

### parameters.prop  (name=value, one per line, .properties format)
```
a=https\://api4preview.sapsf.com
c=SF_REL
cred_QA=SF_QA
fsqa=https\://api4.successfactors.com
Completed_Mail_To=rlandrydelgado@deloitte.com
Mail_From=donotreplycpi@cintas.com
```
- Colons escaped as `\:` (Java properties format).
- Empty values allowed (Manual_Run=, ExcludeReqs=).

### parameters.propdef  (XML: definitions + adapter bindings)
Two sections:
1. `<parameter>` blocks — one per param: name, type(xsd:string), isRequired,
   constraint, description, additionalMetadata.
2. `<param_references>` — THE CRITICAL PART. Binds each param to a specific
   adapter attribute:
```
<reference
  attribute_category="Receiver.Receiver.System"
  attribute_id="ctype::AdapterVariant/cname::SuccessFactors/tp::HTTP/mp::OData V2/direction::Receiver/version::1.21.0/attrId::address"
  attribute_uilabel="Address"
  param_key="fsqa"/>
```
- attribute_category: where in the iFlow (Receiver/Receiver2/Sender + section).
- attribute_id: fully-qualified adapter attribute path (ctype/cname/tp/mp/direction/version/attrId).
- param_key: which .prop entry supplies the value.

### How the iFlow references them: inline {{paramName}}
- In adapter config: `address = {{fsqa}}`, `alias = {{cred_QA}}`
- In Content Modifier values: `<cell id='Value'>{{ExcludeReqs}}</cell>`
- Plain double-brace substitution everywhere a value can vary.

=> OUR EXTERNALIZATION FEATURE must produce all three: {{name}} in the .iflw,
   name=value in .prop, and the <parameter>+<param_references> in .propdef.

## iFlow step serialization (.iflw is BPMN2 XML)

### callActivity (processing steps) — activityType values seen:
| Step | activityType | componentVersion |
|---|---|---|
| Groovy Script | Script | 1.1 |
| Content Modifier | **Enricher** | 1.6 |
| General Splitter | Splitter | 1.6 |
(confirms our content_modifier_generator used Enricher/1.6 correctly.)

### Content Modifier internal structure — CORRECTION TO OUR GENERATOR
Property keys: bodyType, propertyTable, headerTable, wrapContent,
componentVersion, activityType, cmdVariantUri.

propertyTable/headerTable rows use **cell id=** format (HTML-escaped):
```
<row>
  <cell id='Action'>Create</cell>
  <cell id='Type'>constant</cell>
  <cell id='Value'>{{ExcludeReqs}}</cell>
  <cell id='Default'></cell>
  <cell id='Name'>ExcludeReqs</cell>
  <cell id='Datatype'></cell>
</row>
```
*** OUR GENERATOR USED THE WRONG FORMAT *** — we emitted <row><id>..<Name>..
Real format is <row><cell id='Action'/><cell id='Type'/><cell id='Value'/>
<cell id='Default'/><cell id='Name'/><cell id='Datatype'/>. MUST FIX when wiring.
- bodyType=expression when body is set; Value can contain {{param}}.

### Adapter (messageFlow) serialization
Receiver SuccessFactors OData V2 adapter, key properties:
```
address = {{fsqa}}                 <- externalized endpoint
alias = {{cred_QA}}                <- externalized credential ALIAS (not secret!)
authenticationMethod = OAuth2SAMLBearer
operation = Query(GET)
resourcePath = JobRequisition
MessageProtocol = OData V2
TransportProtocol = HTTP
direction = Receiver
ComponentType = SuccessFactors
componentVersion = 1.21
queryOptions = $select=...${property.JobReqs}...   <- dynamic via ${property.X}
cmdVariantUri = ctype::AdapterVariant/cname::sap:SuccessFactors/tp::...
```
- Note TWO substitution syntaxes: {{param}} = externalized config param;
  ${property.X} = runtime exchange property (set earlier in flow). Different things!

## Real mappings/scripts present (study material for converter + groovy libs)
- 2 .mmap (MM_Manager_Changes, First_Reestructure) — real graphical mappings to
  cross-check against our serialization reference.
- 7 XSLT/XSL: JobCode&Position extractor (v1+v2), Eliminate duplicates,
  Convert XML to CSV, Non-benefitClass, OpenText CSV_to_XML, Sort looped.
- 11 Groovy: PayloadCheck, GET_TENANT_ID, LogAttachment, format_manager_and_User,
  CSVtoXML, Remove duplicates, Looping parentposition, De-populate fields, script1.
- 4 XSD + 2 large EDMX (SuccessFactors OData metadata, 6.7MB each).
=> These are REAL production examples to validate our XSLT/Groovy template libs
   and the .mmap converter against. Highest-value corpus we have.

## Confirmations & corrections summary
CONFIRMED CORRECT in our build:
- Content Modifier = activityType Enricher, componentVersion 1.6
- Credential stored as ALIAS ({{cred_QA}}), never the secret
- Package resource layout (src/main/resources/...)

MUST FIX in our generators when we wire:
- Content Modifier table format: <cell id='X'> not <id>/<Name> (see above)
- Add the .propdef <param_references> generation (binds param->adapter attribute)
- Two substitution syntaxes: {{config}} vs ${property.runtime}

## Status: decoded. Combined with the .mmap reference, the iFlow-wiring +
## externalization + package-assembly features are now fully specced.
