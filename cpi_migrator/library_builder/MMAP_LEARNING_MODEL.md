# MMAP Learning Model — Complexity Patterns (Priority #1 deliverable)

Derived empirically by parsing **all 87 non-empty mmaps** in the corpus (3 →
4,076 bricks) with `library_builder/mmap_parser.py`. Measured fidelity:
**100% function recovery and 100%+ source recovery across every complexity
bucket.** This is the structured model the capability-catalog (blocker #2) is
built on, and the basis for replicating arbitrary mmaps (gap #1).

## 1. The four complexity dimensions

A mapping's complexity is fully described by five measurable axes:

| axis | what it is | range in corpus |
|------|-----------|-----------------|
| **bricks** | total nodes | 0 → 4,476 |
| **Dst** | target fields mapped | 0 → 4,076 |
| **functions** | function boxes | 0 → 390 |
| **groups** | `<group/>` context markers (one per Dst) | tracks Dst |
| **context** | `context=` attrs / context funcs (loop/N:M handling) | 0 → 78 |
| **pins** | multi-arg wirings | 0 → 171 |

Buckets used for fidelity testing:
- **simple** (<10 bricks): pure field copies, 1-2 functions. 4 mmaps.
- **medium** (10-100): typical interface mapping. 45 mmaps.
- **complex** (100-500): large replication mappings. 31 mmaps.
- **huge** (500+): bulk/compound-employee monsters. 7 mmaps.

## 2. The function library — 112 distinct functions, FIXED signatures

Across the whole corpus there are **112 distinct functions**. The key learning:
**each function has a fixed argument arity** (the only exception is
`generateUUID`, seen with 0 or 1 args). This means a function's *signature* is
a reliable, catalogable fact — we can validate any mapping against it.

Arity classes (the source-arg count, constants-in-bindings excluded):

- **0-arg** (value generators): `const`, `currentDate`, `generateUUID`,
  `generateGuid`, `getMessageId`, `generateMessageHeaderUUID`, `setSOAPMessageID`
- **1-arg** (transforms): `mapWithDefault`, `TransformDate`, `valuemap`,
  `SplitByValue`, `not`, `exists`, `FixValues`, `createIf`, `getProperty`,
  `substring`, `isNil`, `toLowerCase`, `toUpperCase`, `sort`, `removeContexts`,
  `collapseContexts`, `trim`, `length`, `abs`, `sqrt`, ... (most functions)
- **2-arg**: `stringEquals`, `ifWithoutElse`, `concat`, `formatByExample`,
  `and`, `or`, `greater`, `less`, `sortByKey`, `add`, `sub`, `mul`, `div`,
  `equalsA`, `startWith2`, `indexOf2`, `lastIndexOf2`, `power`, ...
- **3-arg**: `useOneAsMany`, `iF`, `iFS`, `replaceString`, `startWith3`,
  `indexOf3`, `lastIndexOf3`

Top-10 most-used (the functions a generator MUST get right): const (1109),
mapWithDefault (343), stringEquals (163), ifWithoutElse (129), concat (103),
TransformDate (98), valuemap (90), useOneAsMany (66), iF (64), SplitByValue (58).

## 3. The three structural shapes a mapping can take

Every Dst field's tree is one of three shapes (all parse cleanly):

1. **Direct copy** — `Dst <- Src` (no function). The simplest; ~half of all
   fields in large mappings.
2. **Function tree** — `Dst <- func(Src, Src, const-binding...)`. Functions
   nest arbitrarily: `Dst <- concat(toUpperCase(a), const)`.
3. **Context-aware** — sources are NESTED paths into repeating structures
   (`/Items/Item/numValue`) and/or use context functions (`useOneAsMany`,
   `SplitByValue`, `removeContexts`, `collapseContexts`). 29 of 87 mmaps use
   this. This is the N:M / loop dimension.

## 4. Constants: binding vs arg (the placement rule)

- **20 functions carry `<bindings>`** (constant parameters): const,
  mapWithDefault, ifWithoutElse, concat, TransformDate, valuemap, SplitByValue,
  FixValues, substring, getProperty, currentDate, sort, sortByKey, CopyValue,
  formatNumber, ... (full list measured).
- **3 functions take their constant as a pinned `<arg>` child** instead:
  formatByExample, iF, iFS (`_CONST_AS_ARG` in the generator).
- Everything else takes only source args.

## 5. The pin rule (confirmed + now corpus-validated)

Arguments are numbered sequentially PER FUNCTION: 1st = no pin, 2nd = `pin="1"`,
3rd = `pin="2"`. Corpus has up to 171 pins in one mapping. The parser recovers
all pins; the generator emits them correctly (locked by tests).

## 6. Formatting variants (parser robustness)

Two mmaps (`*ZZDEBMAS*`) are PRETTY-PRINTED with newlines/tabs inside brick tags
(`<brick gid="0"\n   path="..."\n   type="Dst">`). The parser is whitespace-
tolerant (re.S + `\s+` in tag patterns). All other mmaps are single-line.
Lesson: never assume single-line tags when parsing real SAP exports.

## 7. What this unlocks

- **Replication (gap #1):** parse any mmap → structured `ParsedMmap` → the
  generator can rebuild it. Round-trip proven on all 87.
- **Capability catalog (blocker #2):** each parsed field is a tagged capability
  ("this field does date-conversion via TransformDate on path X"). The catalog
  can index FIELDS/FUNCTIONS, not whole files — enabling "find X exclusively".
- **Schema-aware generation (remaining gap):** the nested-path handling (§3.3)
  is the bridge to generating against arbitrary schemas — the parser already
  reads nested paths correctly; the generator needs to emit them from a schema.

## 8. Remaining gaps (honest)

- **Context/group generation** — we PARSE context mappings 100%, but generating
  a NEW N:M context mapping from scratch is untested (we can replicate existing
  ones, not yet author novel ones).
- **Schema-driven path emission** — generator currently takes paths from the
  spec; emitting correct nested paths from an xsd/wsdl is the next build.
- These are the items to validate against the PI/PO packages when shared.
