# Real Production Corpus — Reference Summary

Source: real SF/HR integration project files (EC→ADP/Sailpoint/WorkCare/Egencia,
2022-2023). ~120 files. This is the richest reference corpus we have. Used to
VALIDATE our template libraries and the .mmap converter against real production.

## Inventory
- 26 XSLT/XSL (real production transforms)
- 16 .mmap graphical mappings (incl. huge ones: MM_CompoundEmployee 363KB,
  MM_CompoundEmployee_FutureTerms 464KB, EC_to_Sailpoint 73KB)
- 45 XSD (real SF OData schemas, target schemas; some huge: SF_Schema 1MB+)
- 49 Groovy/gsh scripts (real production)
- groovy-4.0.23.jar + groovy-templates-4.0.23.jar  <- THE BIG UNLOCK

## *** MAJOR CAPABILITY UNLOCK: local Groovy execution ***
groovy-4.0.23.jar lets us RUN CPI Groovy scripts locally for the first time
(previously static-check only). Built testing/groovy_runner/ with a Message stub.
Immediately caught 2 real bugs in our own templates that static analysis missed:
- medium_02_stream_buffering: invalid Date.format(String,TimeZone) -> fixed to
  SimpleDateFormat (the idiom real production uses, confirmed in FormatLastModifiedDate.groovy)
- migration_01_edi_flatfile_parser: lines.length on a List -> .size(). Fixed.
Both now execute correctly. 288 tests still pass.

## Production XSLT calibration (real-world signal)
- Versions: 15x v1.0, 24x v2.0, 2x v3.0. (Mostly 1.0/2.0 — matches our library.)
- 7 use method="text" for XML->CSV (CSV output is common in HR integrations).
- NOTABLE: ZERO use of for-each-group or xsl:key/Muenchian grouping.
  => Real consultants avoid grouping functions. Our ex4_for_each_group and the
     grouping emphasis may be less relevant than assumed. Keep but de-prioritise.
- 23/26 compile clean in Saxon. The 3 "fails" use CPI extension funcs/runtime
  params (not bugs) — confirms they're real working production references.

## Real Groovy patterns observed (validate our templates against these)
- Date handling: java.text.SimpleDateFormat (NOT Date.format()).
- Body read: message.getBody(java.lang.String) as String  (explicit cast idiom).
- MessageLog: messageLogFactory.getMessageLog(message) — the IMPLICIT binding
  form (our pitfall_c warns this can NPE; real code uses it anyway, often inside
  try/catch). Confirms pitfall_c is a real concern but also that implicit form is
  common in practice.
- Heavy use of HashMap for lookups (CountryHashmap, Position_Hashmap, hashmap1).
- Real scripts: BuildWhereClause/QueryBuilder (dynamic OData query building),
  computeTimezoneDates, MandatoryFields checks, RemoveExtraNodes, GetAddress.
  These are richer than our templates — candidate patterns for library expansion.

## .mmap corpus (for converter validation)
16 real graphical mappings beyond our 74-function harness. The huge ones
(CompoundEmployee 363-464KB) show real-world complexity: deep nesting, many
functions, real context handling. PRIMARY validation set for the future
graphical->XSLT converter — far better than synthetic examples.

## XSD corpus (for shadow-test fixtures + schema understanding)
45 real schemas: SF OData GET entities (PerPerson, EmpJob, FOLocation, etc.),
target schemas (ADP, Sailpoint, WorkCare, Egencia). Real input/output shapes
for testing mappings and the converter.

## Confidentiality note
Contains real client identifiers (Cintas/Deloitte, emails, system names). Used
ONLY to learn STRUCTURE/PATTERNS, not to copy business logic. Our tool generates
its own content in these formats. Do not redistribute client business logic.

## Status: corpus catalogued. Groovy execution capability is now part of the
## project (testing/groovy_runner/). 2 template bugs fixed via real execution.
