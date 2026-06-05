# MMAP Generation — Confirmed Patterns Contract

Everything in this file was CONFIRMED by tenant tests during the mmap-generator
build (the user runs all tenant tests; their results are ground truth). This is
the durable record so hard-won findings are never re-litigated. Each item notes
HOW it was confirmed.

## 0. The headline result

A `.mmap` generated from a logical field-mapping spec (the SAP "mapping
definition" Excel) was confirmed to:
  - import to the tenant,
  - open in the mapping editor with NO mapping errors,
  - match a verified tenant-authored mmap 100% on all entries (75/75 target
    fields: same sources, same pin order, same functions; 53/53 binding params).

Only display-only layout coordinates and per-instance GUIDs differ — both
proven irrelevant (see §3, §6).

## 1. Bundle wrapper — the ACCEPTED standalone-MessageMapping format

Confirmed by: user downloaded a mapping from a package, repacked, re-uploaded —
the tenant accepted exactly these 6 files (mmap.zip / mmap__1_.zip).

```
mmap.zip
├── src/main/resources/mapping/mmap.mmap
├── src/main/resources/wsdl/FunctionSource.wsdl   (SINGLE path — not doubled)
├── src/main/resources/wsdl/FunctionTarget.wsdl
├── .project
├── META-INF/MANIFEST.MF        (SAP-BundleType: MessageMapping)
└── metainfo.prop               (#Store metainfo properties\ndescription=)
```

- The MANIFEST is the MessageMapping type (see bundle_assembler
  build_messagemapping_manifest): SAP-BundleType: MessageMapping, NO
  SAP-RuntimeProfile, Provide-Capability: messagemapping.<symbolic>...,
  mapping-focused Import-Package.
- DOUBLED resource paths (src/main/resources/src/main/resources/...) cause the
  tenant error "The project must contain valid source folders." Confirmed: the
  ca236...content file had FunctionSource/Target at a doubled path and failed;
  files at the single path worked.
- The tenant regenerates MANIFEST bookkeeping (Origin-ModifiedDate etc.) on
  import — a re-uploaded mapping came back with fields added. So the MANIFEST
  need not be byte-perfect on those.

## 2. mmap envelope — the required skeleton

Confirmed by: comparing against the empty `isItSize.mmap` (a valid 2-binding
mapping with 0 bricks) and the verified `mmap.zip`.

Order (all confirmed present in valid files):
```
<xiObj xmlns="urn:sap-com:xi">
  <idInfo xmlns=""> ... </idInfo>
  <documentation xmlns=""> ... </documentation>
  <generic xmlns="">
    <admInf> ... </admInf>
    <lnks> SCHEMA BINDINGS </lnks>
    <textInfo><textObj id="..."><texts>
       <text label=""/><text label="GUID"></text>   (TWO text elements)
    </texts></textObj></textInfo>
  </generic>
  <AdditionalProperties xmlns="">  externalNameSpace/choiceOccurrence/
                                   groupsOccurrence = RESOLVED  </AdditionalProperties>
  <content xmlns="">
    <tr:XiTrafo xmlns:tr="urn:sap-com:xi:mapping:xitrafo">
      <tr:MetaData><mappingtool><project>
         <libstorage> ... usernamespace functionstorage scaffolding ... </libstorage>
         <transformation>
            BRICK TREES (one per target field)
            <namespaces>...</namespaces>     (see §5)
         </transformation>
         <testData><instances/></testData><ViewState/><pcont/>
      </project></mappingtool></tr:MetaData>
      <tr:ByteCodeJar/><tr:SourceStructure/><tr:TargetStructure/>
      <tr:Multiplicity>1:1</tr:Multiplicity>
      <tr:SourceParameters>...</tr:SourceParameters>
      <tr:TargetParameters>...</tr:TargetParameters>
    </tr:XiTrafo>
  </content>
</xiObj>
```

- `<libstorage>` must contain the user-namespace functionstorage scaffolding
  (NOT a bare `<libstorage/>`). Confirmed: isItSize.mmap has it; generated files
  without it were structurally incomplete.
- `<tr:SourceParameters>`/`<tr:TargetParameters>` present. Confirmed from
  isItSize.mmap.

## 3. Schema bindings (lnkRole)

Confirmed by: verified mmap + ca236 content.

```
<lnkRole kpos="1" role="TARGET_IFR_MESS"><lnk rMode="R"><key typeID="wsdl" version="1.1">
  <elem>FunctionTarget.wsdl</elem>        file
  <elem>src/main/resources/wsdl</elem>    dir (SINGLE path)
  <elem>FunctionTarget_MT</elem>          root message
  <elem>http://cpi.sap.com/demo</elem>    target namespace  (WSDL: 4 elems; xsd: 3)
</key></lnk></lnkRole>
```
Roles: SOURCE_IFR_MESS, TARGET_IFR_MESS. WSDL bindings carry the 4th namespace
element; xsd bindings use 3.

## 4. Brick tree — source → function → target wiring (THE CORE)

Confirmed by: v9 matched verified 75/75 on sources+pins+functions.

A target field is a Dst brick; its mapping is a nested tree under <arg>:
```
<brick gid="0" path="/ns1:Target/ns1:field" type="Dst">
  <arg><brick fname="FUNC" fns="dflt" type="Func">
     <arg><brick gid="0" path="/ns1:Source/ns1:a" type="Src"/></arg>
     <arg pin="1"><brick gid="0" path="/ns1:Source/ns1:b" type="Src"/></arg>
     <arg pin="2"><brick gid="0" path="/ns1:Source/ns1:c" type="Src"/></arg>
  </brick></arg>
  <group/>
</brick>
```

### 4a. THE PIN RULE (the big one — caused "incomplete mapping")
Confirmed by: user found multi-source functions showed only ONE source; editing
in-tenant to add the second fixed it. Then sequential-pin fix matched verified.

  - A function's arguments are sequentially numbered: 1st arg = NO pin,
    2nd = `pin="1"`, 3rd = `pin="2"`, ...
  - WITHOUT pins, the editor only recognizes the first source → "incomplete
    mapping" on every multi-arg function (add, sub, concat, useOneAsMany, ...).
  - WRONG: giving every extra arg `pin="1"` (cross-wires inputs — confirmed via
    useOneAsMany _c landing on slot 2, ifS _cond/_else swapped).
  - Numbering is PER FUNCTION (reset for each function's own arg list).

### 4b. const-as-ARG vs const-as-BINDING
Confirmed by: verified mmap inspection.
  - MOST functions: constant args go in `<bindings><param>` on the Func brick.
  - A FEW take their constant as a nested pinned `<arg>` const-brick instead:
    formatByExample, iF, iFS  (set `_CONST_AS_ARG`). For these the const occupies
    a pin slot (e.g. formatByExample: in=p0, const=pin1).
  - sortByKey is a hybrid: const-as-arg (the key) at p0, source at pin1, PLUS
    comparator/order bindings.

### 4c. if/ifS argument ORDER
Confirmed by: verified. iFS(then, cond, else) → physical order then(p0),
cond(pin1), else(pin2). The Excel writes them in that order; emit in that order.

## 5. <namespaces> block

Confirmed by: verified has it; v9 (with it) matched 100% and opened clean.
Placed at the END of <transformation>, after the last brick:
```
<namespaces><properties>
  <property name="http://cpi.sap.com/demo">ns1</property>
</properties></namespaces>
```
Declares the `ns1` prefix used in every field path. NOTE: NOT required to OPEN
(isItSize/v1 open without it), but present in clean verified files — include it.

## 6. Layout (viewData) — STORED VERBATIM, role-banded, display-only

Confirmed by: user dragged bricks to arbitrary positions (x=587, y=307) and the
tenant stored them exactly (mmap__2_.zip). Coordinates are never validated or
recomputed.

  - Each brick carries `<viewData x=".." y=".."/>`.
  - Canonical role grid (the values SAP's auto-layout jitters around):
    Src x≈50, Func x≈135, Dst x≈230. y small/local per row.
  - Deltas between bands ≈95-100 (NOT a fixed 62), variable ±13 — auto-layout
    jitter, no recoverable formula. The canonical grid is valid because any
    coords are accepted.
  - INDEPENDENT of the iflw process diagram (dc:Bounds, large canvas). The mmap
    viewData and the iflw CallActivity/Process bounds are separate coordinate
    systems — confirmed: no shared coords, different scales, iflw has no
    viewData, mmap has no dc:Bounds.

## 7. Function name aliases & binding param names

Confirmed by: diffing generated vs verified.
  - `divide` (Excel/UI) → `div` (internal fname). See `_FUNC_ALIAS`.
  - Each function's binding params have specific names (`_PARAM_NAMES`):
    counter→ini/inc, currentDate→oform/calend, formatNumber→nformat/separator,
    sort→comparator/order, substring→start/count, index→start/inc/type,
    valuemap→(11 params), FixValues→vmdefault/vmstrategy/table, etc.
  - Structured-value functions (`_structured_bindings`) emit XML inside <value>,
    copied verbatim from verified: <calend_props> (dates), <sort_comp>/
    <sort_order> (sort, sortByKey), numeric codes (SplitByValue type=0).

## 8. What was WRONG along the way (so we don't repeat it)

  - "Structured bindings break opening" — FALSE. Verified has them and opens;
    the real cause of early failures was the missing pin numbering + emitting
    flat Excel labels where structured XML was required.
  - "Namespaces break opening" — FALSE. Verified has namespaces and opens.
  - The lesson: change ONE variable per tenant test. Multi-variable versions
    (v3/v4/v5) made causes impossible to isolate and led to wrong conclusions.

## 9. Confirmed function coverage (all 75 target fields in the reference Excel)

Arithmetic/logic (bindings none, just pinned sources): add, sub, mul, div,
equalsA, abs, sqrt, sqr, sign, inv, power, etc. — CONFIRMED.
Multi-source (pinned): useOneAsMany(3), startWith3(3), replaceString(3),
lastIndexOf3(3), indexOf3(3) — CONFIRMED.
Conditionals (const-as-arg / order-sensitive): iF, iFS, ifWithoutElse,
ifSWithoutElse — CONFIRMED.
Structured-value: currentDate, TransformDate, DateBefore, DateAfter,
CompareDates, sort, sortByKey, SplitByValue, FixValues, index, formatNumber,
valuemap, formatByExample — CONFIRMED (matched verified).
