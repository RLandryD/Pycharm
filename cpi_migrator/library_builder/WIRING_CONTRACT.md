# SAP Integration Suite Bundle — Wiring Contract

Derived empirically from analysis of all available bundles (157 iFlows across
client-authored RCI093/CPQ and SAP-standard packages_part1 + others), plus the
tenant deploy tests. This is the spec the parameter manager / wiring validator
/ clone-and-adapt modifiers must honor.

## 1. Bundle structure

```
<bundle>.zip
  ├── <guid>_content            one nested zip per iFlow
  │     ├── .project            Eclipse descriptor; only <name>=symbolic varies
  │     ├── META-INF/MANIFEST.MF
  │     └── src/main/resources/ ALL content lives here (the only layout in
  │           ├── scenarioflows/integrationflow/*.iflw      real exports)
  │           ├── mapping/*.mmap, *.xslt, *.xsl
  │           ├── script/*.groovy, *.js
  │           ├── xsd/*.xsd, wsdl/*.wsdl, edmx/*.edmx
  │           ├── parameters.prop      externalized VALUES (key=value)
  │           └── parameters.propdef   externalized field DEFINITIONS (xml)
  ├── resources.cnt             tenant-regenerated (can be blank)
  ├── contentmetadata.md        tenant-regenerated (can be blank)
  ├── hash                      tenant-regenerated (can be blank)
  └── ExportInformation.info    tenant-regenerated (can be blank)
```
Pre-bundle working forms also exist (bare dir, `main/resources/` layout) but the
deployable export always uses `src/main/resources/`.

## 2. What the tenant REQUIRES vs REGENERATES (tenant-tested)

REGENERATED on import — may be blank: hash, resources.cnt, contentmetadata.md,
ExportInformation.info. (Confirmed: blank versions imported; re-export valid.)

REQUIRED — we must generate correctly: MANIFEST.MF (cannot be blank),
.project, and the content files.

## 3. MANIFEST.MF — required field set (from known-deployed bundles)

These 11 fields are common to EVERY manifest we deployed; generating exactly
these is sufficient for import:
  Manifest-Version, Bundle-ManifestVersion, Bundle-Name, Bundle-SymbolicName,
  Bundle-Version, SAP-BundleType (=IntegrationFlow), SAP-NodeType (=IFLMAP),
  SAP-RuntimeProfile (=iflmap), Import-Package, Origin-Bundle-Name,
  Origin-Bundle-SymbolicName.

Optional fields seen in the corpus (NOT required to deploy; do not block):
  Origin-Bundle-Version, Origin-ModifiedDate, Require-Capability (marked
  resolution:=optional — declares dependent message-mappings/script-collections/
  adapters), Import-Service, SAP-ContentMode, SAP-ArtifactTrait,
  Provide-Capability, WorkspaceProfile, Bundle-ClassPath, Bundle-Activator.

IMPORTANT — Import-Package is NOT one fixed constant. 26 distinct variants
exist across the corpus; the list reflects what the iFlow uses (adapters,
scripting, jdbc, mapping libs). The 1761-char variant is the most common (112
manifests) and is what our assembler emits; it deployed successfully. A more
correct approach is to derive Import-Package from content, OR use a known-good
common variant. For now the common variant is proven to deploy.

Bundle-SymbolicName may carry an OSGi directive: `Name; singleton:=true`.
Name rules (tenant-observed): must NOT end with a period (400 error); valid
chars for symbolic name are [A-Za-z0-9._-].

## 4. Parameter wiring (the core of "good and wired")

Endpoints/credentials are NOT hardcoded in the .iflw — they are `{{ParamName}}`
placeholders. The VALUES live in parameters.prop; the FIELD DEFINITIONS live in
parameters.propdef.

Consistency rules (zero violations across RCI093 + SAP ERP):
  - Every `{{param}}` referenced in the .iflw MUST have an entry in BOTH
    parameters.prop AND parameters.propdef. (referenced ⊆ declared)
  - prop and propdef always carry the SAME key set; they move together.
  - prop/propdef MAY declare MORE keys than the iflw uses (harmless extras).

Values can be blank or placeholder and the bundle still IMPORTS — SAP ships its
own flows with connectivity values blank (e.g. `SAP CPQ Host=`, `S4_Material_
address=`). The consultant fills real values in the tenant. So:
  - To "personalize" connectivity = edit parameters.prop only. No .iflw edits.
  - Params are OPTIONAL entirely: 4 iflws in the corpus have ZERO {{params}}
    and parameters.prop can even be a 0-byte file.

Caveats (flag, don't auto-solve):
  - Some params are isRequired=true or constrained (propdef <constraint>,
    <isCombobox>). These need valid-shaped values before the flow will RUN.
  - Credentials ({{...Credential...}}) reference tenant Security Material that
    must exist in the target tenant.
  - ProcessDirect internal addresses are sometimes HARDCODED literals
    (e.g. /S4/CPQ/...), not externalized — keep consistent across paired flows.
  - A bundle with blank endpoints IMPORTS and saves, but won't RUN until values
    are set. Honest automation target = "deployed, configurable, ready to fill".

propdef parameter element shape:
  <parameter><key/><name>NAME</name><type>xsd:string</type>
    <isRequired>false</isRequired><constraint/><description/>
    <additionalMetadata>[<isCombobox>true</isCombobox>]</additionalMetadata>
  </parameter>

## 5. Mapping (.mmap) binding — the consultant ceiling

A .mmap binds to its source/target schemas by filename+path+root-element:
  <lnkRole role="SOURCE_IFR_MESS"><lnk><key typeID="xsd">
     <elem>Input First parent Position.xsd</elem>
     <elem>src/main/resources/xsd</elem><elem>JobRequisition</elem></key></lnk>
Roles: SOURCE_IFR_MESS, TARGET_IFR_MESS. The mapping's internal field paths
reference the schema structure.

Implication:
  - REUSE a mapping as-is: works when the new interface uses the SAME schemas
    (ship the same .xsd/.wsdl in the bundle).
  - ADAPT to DIFFERENT schemas: field paths must be re-authored — this is the
    consultant's work, not automatable. The system can match-and-reuse from the
    2k+ mmap catalog, or FLAG when no schema-compatible mapping fits.

## 6. metainfo.prop (package-level, optional)

Seen in PI/PO-migrated minimal flows: `description=Migrated from PI/PO`.
Carries the package description. Optional.

## 7. What this means for the build

Personalizing an artifact reduces to:
  1. parameter manager — read/set/write parameters.prop, keep propdef in sync,
     support blank/placeholder/real values.
  2. wiring validator — enforce referenced ⊆ declared; flag required/
     constrained/credential params and hardcoded ProcessDirect addresses.
  3. rename modifier — set bundle/iflw identity, validator-safe.
  4. mapping selector — match schema-compatible mmap from catalog; flag if none.
The .iflw itself is NOT edited for connectivity (it is param-driven).

## 8. Mappings appear in TWO forms (corrected)

CPI lets you create a mapping inside an iFlow OR as a standalone "exposed"
MessageMapping next to interfaces. A package can contain both as sibling
<guid>_content units.

(a) IN-INTERFACE mapping: src/main/resources/mapping/*.mmap inside an iFlow's
    _content. Used only by that iFlow.

(b) EXPOSED MessageMapping: its OWN _content unit. Distinct contract:
    - structure: mapping/*.mmap + wsdl|xsd schemas + .project + MANIFEST
      (no .iflw, no parameters.prop)
    - MANIFEST: SAP-BundleType: MessageMapping (NOT IntegrationFlow);
      NO SAP-RuntimeProfile; a smaller mapping-focused Import-Package
      (com.sap.aii.mapping.*, com.sap.aii.mappingtool.*, com.sap.it.api.mapping,
      com.sap.xi.mapping.camel); and
      Provide-Capability: messagemapping.<symbolic>;version:Version="1.0.0"
      (this is what "exposed" means — published so iFlows can reference it).
    - an iFlow that USES an exposed mapping declares Require-Capability:
      messagemapping.<name>;resolution:=optional (optional resolution).

The exposed MessageMapping is the CLEANEST generation target (self-contained,
no iflw/params to wire). Assembler support: build_messagemapping_manifest() /
build_messagemapping_content().

## 9. mmap schema binding — WSDL needs 4 elements

<lnkRole role="SOURCE_IFR_MESS"><lnk><key typeID="wsdl">
  <elem>FunctionSource.wsdl</elem>      file
  <elem>src/main/resources/wsdl</elem>  dir (single, NOT doubled)
  <elem>FunctionSource_MT</elem>        root message
  <elem>http://namespace</elem>         target namespace (WSDL only; xsd omits)
</key></lnk></lnkRole>
Doubled paths (src/main/resources/src/main/resources/...) cause the tenant
"project must contain valid source folders" import error.

mmap also carries an <AdditionalProperties> block (externalNameSpace=RESOLVED,
choiceOccurrence=RESOLVED, groupsOccurrence=RESOLVED) between </generic> and
<content>.
