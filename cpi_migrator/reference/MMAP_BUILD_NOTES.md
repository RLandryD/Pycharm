# .mmap Build Notes — Tenant Session (pre-export)

Captured from the consultant's live mapping build. These document the exact
function configurations used, so when the .mmap is exported we know what each
serialized form corresponds to. Reference for the future graphical→XSLT converter.

## Function configuration choices made during build

### Boolean — Keep/Suppress option
- **ifWithoutElse**: "Keep Suppress" left INACTIVE.
- **ifSWithoutElse**: "Keep Suppress" ACTIVATED.
- (Deliberate contrast so the .mmap shows both serializations of the flag.)

### Conversions
- **fixValues**: mode set to "Default value"; default value = "Default value".
  Other available modes noted: "Use Key", "Throw exception". "Advanced" button
  adds key/value rows.
- **valueMapping** (via Advanced):
  - Source Agency  = Sender Party
  - Source Identifier = (Target Identifier — per consultant note; verify on export)
  - Target Agency  = Receiver Party
  - Target Identifier = Receiver Identifier
  - On Failure = Throw exception
  - Default Value = Default Value

### Date functions
- **currentDate**: Date Format left default. Advanced defaults noted:
  First Weekday = Sunday, Min Days = 1, Lenient = true.
- **dateTrans, dateBefore, dateAfter, compareDates**: everything left default.

### Node functions
- **replaceValue**: "Replace by" value = "Replaced By".
- **getProperty, getHeader, sortByKey**: fed with a constant (placeholder value).
- **mapWithDefault**: default set to a single space " "
  (per consultant's Deloitte senior's convention — common real-world practice).
- **formatByExample**: required a pattern; used the alphabet in order as the
  example pattern.

## Target structure / context notes
- sum_result, average_result, count_result, index_result were placed OUTSIDE
  Items/Item in the target (statistic results collapse the context to a single
  value — correct placement). This also tests context handling.
- For the repeating Items/Item-driven results, the consultant deliberately
  changed the CONTEXT between the 3 expected results to demonstrate how
  important correct context setting is. The exported .mmap will show different
  context configurations across these — intentional, not an error.

## Suspected cause of slow/stuck test (10%, ~5 min)
Consultant hypotheses to investigate (NOT yet confirmed):
1. Context handling on the repeating node may be stalling the mapping test.
2. Multiple target items with the SAME element name inside Items/Item
   (splitByValue_result, sort_result, useOneAsMany_result all live in the
   repeating ResultItem) may be causing ambiguity.
- ACTION when .mmap arrives: inspect how these repeating/same-context fields
  serialize; may inform whether the harness target structure needs adjusting
  for future extraction runs.

## QoL convention for our generated .mmaps (future feature)
- The consultant aligns mapping lines to be STRAIGHT and spaces objects evenly
  for readability (demonstrated on replaceString).
- DESIGN GOAL: when our workbench generates .mmap files, lay out nodes with
  straight connector lines and consistent spacing between objects, to match
  how consultants read graphical message mappings. Readability is a feature.

## Status
- Awaiting .mmap export from tenant. Build notes above to be cross-referenced
  against the serialized output once received.
