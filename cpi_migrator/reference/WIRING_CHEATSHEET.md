# .mmap Function-Extraction Harness — Wiring Cheat-Sheet

Build a graphical Message Mapping in the test tenant with **FunctionSource_MT** as
source and **FunctionTarget_MT** as target. For each target field below, apply the
named standard function and connect the listed source field(s). Then export the
`.mmap`. That single export documents how every function serialises — the reference
for the future graphical-to-XSLT converter.

**Tips**
- Import both as WSDLs (NOT as .xsd — that was the earlier import error).
- Most fields are 1:1 (one function, simple inputs). A few (splitByValue, sort,
  useOneAsMany) map into the repeating **ResultItems/ResultItem** node — wire those
  with the target context on ResultItem.
- Statistic functions (sum/average/count/index) feed from the repeating
  **Items/Item** source node, so set the source context to Items/Item.
- Functions needing config (substring start/end, dateTrans formats, fixValues rows,
  formatNumber pattern) — set any sensible test values; the goal is to capture the
  function's serialisation, not realistic output.
- valueMapping needs a Value Mapping artifact (or test agency/scheme) to resolve.

## Arithmetic

| Target field | Apply function | Source input(s) |
|---|---|---|
| `add_result` | **add** | add_a, add_b |
| `subtract_result` | **subtract** | subtract_a, subtract_b |
| `multiply_result` | **multiply** | multiply_a, multiply_b |
| `divide_result` | **divide** | divide_a, divide_b |
| `equalsNumber_result` | **equals (number)** | equalsNumber_a, equalsNumber_b |
| `absolute_result` | **absolute** | absolute_in |
| `sqrt_result` | **sqrt** | sqrt_in |
| `square_result` | **square** | square_in |
| `sign_result` | **sign** | sign_in |
| `neg_result` | **neg** | neg_in |
| `inv_result` | **inv** | inv_in |
| `power_result` | **power** | power_base, power_exp |
| `lesser_result` | **lesser** | lesser_a, lesser_b |
| `greater_result` | **greater** | greater_a, greater_b |
| `max_result` | **max** | max_a, max_b |
| `min_result` | **min** | min_a, min_b |
| `ceil_result` | **ceil** | ceil_in |
| `floor_result` | **floor** | floor_in |
| `round_result` | **round** | round_in |
| `counter_result` | **counter** | (none — generates a counter) |
| `formatNumber_result` | **formatNumber** | formatNumber_in |

## Boolean

| Target field | Apply function | Source input(s) |
|---|---|---|
| `and_result` | **and** | and_a, and_b |
| `or_result` | **or** | or_a, or_b |
| `not_result` | **not** | not_in |
| `equalsBoolean_result` | **equals (boolean)** | equalsBoolean_a, equalsBoolean_b |
| `notEquals_result` | **notEquals** | notEquals_a, notEquals_b |
| `if_result` | **if** | if_cond, if_then, if_else |
| `ifS_result` | **ifS** | ifS_cond, ifS_then, ifS_else |
| `ifWithoutElse_result` | **ifWithoutElse** | ifWithoutElse_cond, ifWithoutElse_then |
| `ifSWithoutElse_result` | **ifSWithoutElse** | ifSWithoutElse_cond, ifSWithoutElse_then |
| `isNil_result` | **isNil** | isNil_in |

## Constants

| Target field | Apply function | Source input(s) |
|---|---|---|
| `constant_result` | **constant** | (none — type a constant value) |
| `copyValue_result` | **copyValue** | copyValue_in |
| `xsiNil_result` | **xsi:nil** | (none) |

## Conversions

| Target field | Apply function | Source input(s) |
|---|---|---|
| `fixValues_result` | **fixValues** | fixValues_in  (define a few key/value rows) |
| `valueMapping_result` | **valueMapping** | valueMapping_in  (needs a Value Mapping artifact or test agency/scheme) |

## Date

| Target field | Apply function | Source input(s) |
|---|---|---|
| `currentDate_result` | **currentDate** | (none) |
| `dateTrans_result` | **dateTrans** | dateTrans_in  (set in/out format) |
| `dateBefore_result` | **dateBefore** | dateBefore_a, dateBefore_b |
| `dateAfter_result` | **dateAfter** | dateAfter_a, dateAfter_b |
| `compareDates_result` | **compareDates** | compareDates_a, compareDates_b |

## Node Functions

| Target field | Apply function | Source input(s) |
|---|---|---|
| `createIf_result` | **createIf** | createIf_cond |
| `removeContexts_result` | **removeContexts** | removeContexts_in |
| `replaceValue_result` | **replaceValue** | replaceValue_in |
| `exists_result` | **exists** | exists_in |
| `getHeader_result` | **getHeader** | (none — type a header name, e.g. SAP_Sender) |
| `getProperty_result` | **getProperty** | (none — type a property name) |
| `splitByValue_result → ResultItems/ResultItem` | **splitByValue** | splitByValue_in  (NOTE: target is the repeating ResultItem) |
| `collapseContexts_result` | **collapseContexts** | collapseContexts_in |
| `useOneAsMany_result → ResultItems/ResultItem` | **useOneAsMany** | useOneAsMany_a, useOneAsMany_b, useOneAsMany_c  (NOTE: repeating target) |
| `sort_result → ResultItems/ResultItem` | **sort** | sort_in  (NOTE: repeating target) |
| `sortByKey_result` | **sortByKey** | sortByKey_in |
| `mapWithDefault_result` | **mapWithDefault** | mapWithDefault_in |
| `formatByExample_result` | **formatByExample** | formatByExample_in |

## Statistic (feed from Items/Item)

| Target field | Apply function | Source input(s) |
|---|---|---|
| `sum_result` | **sum** | Items/Item/numValue |
| `average_result` | **average** | Items/Item/numValue |
| `count_result` | **count** | Items/Item  (count occurrences) |
| `index_result` | **index** | Items/Item |

## Text

| Target field | Apply function | Source input(s) |
|---|---|---|
| `substring_result` | **substring** | substring_in  (set start/end) |
| `concat_result` | **concat** | concat_a, concat_b |
| `equalsString_result` | **equals (string)** | equalsString_a, equalsString_b |
| `indexOf2_result` | **indexOf (2-arg)** | indexOf2_text, indexOf2_search |
| `indexOf3_result` | **indexOf (3-arg)** | indexOf3_text, indexOf3_search, indexOf3_from |
| `lastIndexOf2_result` | **lastIndexOf (2-arg)** | lastIndexOf2_text, lastIndexOf2_search |
| `lastIndexOf3_result` | **lastIndexOf (3-arg)** | lastIndexOf3_text, lastIndexOf3_search, lastIndexOf3_from |
| `compare_result` | **compare** | compare_a, compare_b |
| `replaceString_result` | **replaceString** | replaceString_text, replaceString_from, replaceString_to |
| `length_result` | **length** | length_in |
| `endsWith_result` | **endsWith** | endsWith_text, endsWith_suffix |
| `startsWith2_result` | **startsWith (2-arg)** | startsWith2_text, startsWith2_prefix |
| `startsWith3_result` | **startsWith (3-arg)** | startsWith3_text, startsWith3_prefix, startsWith3_from |
| `toUpperCase_result` | **toUpperCase** | toUpperCase_in |
| `trim_result` | **trim** | trim_in |
| `toLowerCase_result` | **toLowerCase** | toLowerCase_in |

**Total functions captured: 74**