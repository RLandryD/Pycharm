# Local Groovy Execution Runner

MAJOR CAPABILITY: lets us actually RUN CPI Groovy scripts locally (previously
static-check only). Enabled by groovy-4.0.23.jar (obtained from a real package).

## Why this matters
Our 17 Groovy templates were static-verified only (brace balance, imports,
conventions) because no JVM Groovy + CPI runtime was available. With the Groovy
jar we can now EXECUTE them against a stubbed Message API and catch real runtime
bugs that static analysis misses.

## What it caught immediately (bugs static analysis missed)
1. medium_02_stream_buffering: Date.format(String, TimeZone) is invalid in
   Groovy 4 / JDK 21 — fixed to use java.text.SimpleDateFormat (the idiom real
   production scripts use).
2. migration_01_edi_flatfile_parser: lines.length on a split() result (a List)
   — must be .size(). Fixed.

## Files
- CPIMessageStub.groovy — stub of com.sap.gateway.ip.core.customdev.util.Message
  (body/headers/properties/attachments, getBody type coercion). NOT a full
  runtime — no ITApiFactory services (DataStore/SecureStore/MessageLog), no
  Camel exchange. Scripts using those still need those stubbed.
- run_cpi_script.groovy — loads a CPI script, swaps the CPI Message import for
  the stub, runs processData, prints body/headers/properties.

## Usage
```
JAR=path/to/groovy-4.0.23.jar
java -cp "$JAR" org.codehaus.groovy.tools.FileSystemCompiler -d classes CPIMessageStub.groovy
java -cp "$JAR:classes" groovy.ui.GroovyMain run_cpi_script.groovy <script.groovy> "body text"
```

## Limits (honest)
- Groovy 4.0.23 may differ slightly from the tenant's Groovy version — most
  core idioms match, but verify version-sensitive things on tenant.
- Scripts needing CPI services (DataStore for idempotency/aggregation,
  SecureStore for crypto/oauth, MessageLogFactory) won't run without extended
  stubs — those remain partially verified.
- This validates LOGIC and SYNTAX in a real JVM. It does NOT validate
  tenant-specific runtime behaviour.
