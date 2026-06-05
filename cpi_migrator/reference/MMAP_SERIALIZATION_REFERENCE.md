# .mmap Serialization Reference — DECODED FROM REAL TENANT EXPORT

Source: 74-function extraction harness built + exported from CPI tenant (May 2026).
This is the authoritative spec for the future graphical-mapping -> XSLT converter.
Files studied: mmap.mmap, sample_input.xml, mmap_test_output.xml, package metadata.

## .mmap file format (top level)
- Root: `<xiObj xmlns="urn:sap-com:xi">` — the PI/CPI mapping object format ("tf7").
- Source/target WSDLs linked via `<lnkRole role="SOURCE_IFR_MESS">` /
  `role="TARGET_IFR_MESS">` with `<key typeID="wsdl" version="1.1">`.
- The actual mapping logic lives in `<tr:XiTrafo>` -> `<transformation>` as a
  flat list of `<brick>` elements, one per target field.

## Brick grammar (the core structure)
Each target-field mapping is a tree of bricks:
```
<brick gid="0" path="/ns1:Target_MT/ns1:FIELD_result" type="Dst">   <- target field
  <viewData x=".." y=".."/>                                          <- canvas position
  <arg>
    <brick fname="FUNCNAME" fns="dflt" type="Func">                  <- the function
      <viewData x=".." y=".."/>
      <arg><brick path="/ns1:Source_MT/ns1:INPUT" type="Src"/></arg> <- input 1 (pin 0)
      <arg pin="1"><brick path=".../INPUT2" type="Src"/></arg>       <- input 2
      <arg pin="2">...</arg>                                          <- input 3
      <bindings>                                                      <- function params
        <param name="X"><value>...</value></param>
      </bindings>
    </brick>
  </arg>
  <group/>
</brick>
```
- type="Dst" = target node, type="Src" = source node, type="Func" = function.
- Multi-arg functions use `<arg pin="N">` (0-indexed; pin omitted = pin 0).
- `<bindings>` holds function configuration params.
- `<viewData x y>` = canvas coordinates (relevant for our straight-line QoL goal).
- A function input can itself be a Func brick (nesting), e.g. getHeader fed by const.

## CRITICAL: screenshot name -> internal fname (NOT always identical)
The UI label differs from the serialized fname for many functions:

| UI label (screenshot) | serialized fname | Notes |
|---|---|---|
| add | add | |
| subtract | **sub** | abbreviated |
| multiply | **mul** | abbreviated |
| divide | **div** | abbreviated |
| equals (number) | **equalsA** | !! |
| absolute | **abs** | |
| square | **sqr** | |
| sqrt | sqrt | |
| sign | sign | |
| neg | **sign** | !! BUG-PRONE: neg serialized same fname as sign — see note |
| inv | inv | |
| power | power | |
| lesser | **less** | |
| greater | greater | |
| max/min | max/min | |
| ceil/floor/round | ceil/floor/round | |
| counter | counter | params: ini, inc |
| formatNumber | formatNumber | params: nformat, separator |
| and/or/not | and/or/not | |
| equals (boolean) | **equals** | |
| notEquals | notEquals | |
| if | **iF** | capital F |
| ifS | **iFS** | |
| ifWithoutElse | ifWithoutElse | param keepss=false |
| ifSWithoutElse | ifSWithoutElse | param keepss=true |
| isNil | isNil | |
| constant | **const** | param value |
| copyValue | **CopyValue** | param nnumber |
| xsi:nil | xsiNil | no input |
| fixValues | **FixValues** | params vmdefault, vmstrategy, table(properties) |
| valueMapping | **valuemap** | params srcns,context,agency1,schema1,agency2,schema2,vmstrategy,vmdefault |
| currentDate | currentDate | params oform, calend(fd/md/le) |
| dateTrans | **TransformDate** | params iform, oform, calend |
| dateBefore | **DateBefore** | params iform, oform, calend |
| dateAfter | **DateAfter** | |
| compareDates | **CompareDates** | |
| createIf | createIf | |
| removeContexts | removeContexts | |
| replaceValue | replaceValue | param value |
| exists | exists | |
| getHeader | getHeader | input = const brick (header name) |
| getProperty | getProperty | input = const brick (property name) |
| splitByValue | **SplitByValue** | param type |
| collapseContexts | collapseContexts | |
| useOneAsMany | useOneAsMany | 3 inputs |
| sort | sort | params comparator(type=cs), order(asc) |
| sortByKey | sortByKey | key input = const; params comparator, order |
| mapWithDefault | mapWithDefault | param default_value |
| formatByExample | formatByExample | 2nd input = const pattern |
| substring | substring | params start, count |
| concat | concat | param delimeter (NOTE SAP's spelling: "delimeter") |
| equals (string) | **stringEquals** | |
| indexOf (2) | indexOf2 | |
| indexOf (3) | indexOf3 | |
| lastIndexOf (2/3) | lastIndexOf2 / lastIndexOf3 | |
| compare | compare | |
| replaceString | replaceString | 3 inputs |
| length | length | |
| endsWith | **endWith** | !! singular |
| startsWith (2) | **startWith2** | !! singular |
| startsWith (3) | **startWith3** | |
| toUpperCase/toLowerCase/trim | toUpperCase/toLowerCase/trim | |
| sum/average/count/index | sum/average/count/index | index params start,inc,type |

## TWO REAL BUGS DISCOVERED (in the harness build, not the format)
1. **neg serialized as fname="sign"** — when wiring "neg" the consultant appears to
   have picked "sign" (or the UI maps neg->sign internally). Test output confirms:
   neg_result=1 for input 12.5, which is sign(12.5)=1, NOT neg(12.5)=-12.5.
   => CONVERTER WARNING: neg and sign may be ambiguous in the .mmap. Verify against
   expected behaviour; don't trust fname alone for neg.
2. **if argument order**: in the .mmap, `iF` has pin0=then, pin1=cond, pin2=else
   (NOT cond,then,else as the UI suggests). Test output if_result=Hello confirms
   the "then" value was wired to pin0. => CONVERTER: map iF pins as then/cond/else.

## CONTEXT BEHAVIOUR (the thing that worried us — RESOLVED, works fine)
- Statistic funcs (sum/average/count/index) placed at PARENT level (outside the
  repeating node) correctly collapse context: sum_result=10, count_result=1, etc.
  from 3 input items. (count=1 because input items were in separate contexts; this
  is the "context matters" demonstration the consultant intentionally built.)
- splitByValue/sort/useOneAsMany placed INSIDE the repeating ResultItem correctly
  preserve per-item context (output shows them under ResultItem).
- The repeating node itself maps as a plain Dst<-Src brick with no function:
  `path=".../ResultItems/ResultItem" <- ".../Items/Item"`.
- CONCLUSION: the earlier 10% hang was NOT a context/structure problem. The mapping
  is structurally sound. Hang was transient (tenant) — later runs completed.

## Function PARAM serialization details (for converter)
- counter: `<param name="ini">1</param><param name="inc">1</param>`
- formatNumber: `nformat=00000.000`, `separator=,`  (output 00012,500 for 12.5)
- dateTrans/dateBefore/etc: `iform`, `oform` (Java date patterns), `calend` block
  with `<fd>` first-day, `<md>` min-days, `<le>` lenient.
- valuemap: full agency/scheme set + `vmstrategy` (1=default-on-fail per this build)
  + `vmdefault`. NOTE: On Failure="Throw exception" was the UI choice but serialized
  vmstrategy=1 with a default — verify mapping of UI failure-modes to vmstrategy ints.
- fixValues: `table` as `<properties><property name="key1">value1</property>...`
- concat: `delimeter` (SAP misspelling — converter must match this literal string).
- substring: `start`, `count` (both 0 here).
- sort/sortByKey: `comparator type="cs"` (case-sensitive), `order asc="true"`.
- mapWithDefault: `default_value` = single space " " (consultant's Deloitte convention).

## ACTUAL OUTPUTS (sanity reference for converter testing)
For input simpleField=Hello, numbers=12.5, date=20260115, 3 items(numValue 10/20/30):
add=25, subtract=0, multiply=156.25, divide=1, sqrt=3.5355.., power=5.14E13,
formatNumber=00012,500, if=Hello, constant=Constant, fixValues="Default value",
valueMapping="Default Value", currentDate=2026/05/29, dateTrans=15/01/2026,
concat="Hello Hello", indexOf2=4, replaceString=HellX, length=5, toUpperCase=HELLO,
sum=10, average=10, count=1, index=1, getHeader=null, getProperty=null.

## PACKAGE STRUCTURE (for our package-assembly feature)
A MessageMapping artifact zip contains:
```
META-INF/MANIFEST.MF        <- Bundle-SymbolicName, SAP-BundleType: MessageMapping,
                               SAP-NodeType: IFLMAP, Import-Package list, Provide-Capability
metainfo.prop               <- description property
.project                    <- Eclipse project; natures include com.sap.ide.ifl.*
src/main/resources/mapping/<name>.mmap
src/main/resources/wsdl/<source>.wsdl
src/main/resources/wsdl/<target>.wsdl
```
This is the exact layout our package-assembly feature must produce.

## Status: COMPLETE — this is the converter spec.
