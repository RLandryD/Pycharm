# SAP CPI — Expression Language & Artifact Reference

Research compiled from (a) 166 real iFlows reverse-engineered from the corpus and
(b) SAP's official Integration Suite documentation. Frequencies in parentheses
are real occurrence counts in the corpus, so they reflect what's actually used in
production, not just what's possible.

---

## PART 1 — The `${...}` Expression Language

CPI runs on **Apache Camel**, and `${...}` is Camel's **Simple expression
language**. Each `${...}` is resolved at runtime against the message *Exchange*
(body + headers + properties). It is NOT a CPI invention — anything true of Camel
Simple is true here.

### 1.1 The expression families (corpus frequency)

| Expression | Count | What it reads | Scope / notes |
|---|---|---|---|
| `${property.NAME}` / `${exchangeProperty.NAME}` | 810 | An exchange **property** | Flow-scoped. **Not** sent to the receiver. The workhorse for intermediate state. |
| `${in.body}` / `${body}` | 151 | The **current message body** | Whatever the previous step produced. |
| `${header.NAME}` | 110 | A message **header** | **Propagated to the receiver** (e.g. becomes an HTTP header). Use for small, transport-facing values. |
| `${date:now:FORMAT}` | 25 | Current date/time | `FORMAT` is Java SimpleDateFormat. Offsets allowed: `${date:now-720h:yyyy-MM-dd}` (30 days ago). |
| `${exception.message}` / `${exception.stacktrace}` | 24 | The caught exception | **Only meaningful inside an exception subprocess / error handler.** Empty elsewhere. |
| `${bodyAs(Type)}` e.g. `${bodyAs(String)}` | 8 | Body converted to a Java type | Forces a type conversion (e.g. stream → String). |
| `${file:name}` / `${header.CamelFileName}` | 19 | File metadata | Set by SFTP/file adapters. |
| `${camelId}` | 4 | The Camel context id | Rarely needed. |
| `${header.CamelSplitIndex}` | — | Index of the current split | Auto-set by the Splitter; counts splits. |

### 1.2 The Expression-vs-Constant rule (the one that bit us)

SAP's rule, verbatim in intent:

> If the value/body **contains** `${...}`, the Source Type **must be Expression**.
> If it's a **literal** with no `${...}`, use **Constant** (recommended,
> especially for large bodies — Constant skips expression parsing, so it's
> faster).

Consequences we hit on the tenant:

- A literal body tagged `Expression` → **"Expression Text does not have any
  expression parameters, but specified as 'Expression'."** (Fixed: the generator
  now picks `bodyType=constant` for literal bodies, `expression` only when
  `${...}` is present.)
- **Constant header/property values cannot contain `{}` or `[]`** — those are
  reserved. If you need them literally, you're in Expression territory.

### 1.3 The three containers (and who sees them)

| Container | Lifetime | Sent to receiver? | Typical use |
|---|---|---|---|
| **Message Header** | Whole flow | **Yes** (becomes outbound header) | Small transport data: HTTP headers, file names, correlation ids. |
| **Exchange Property** | Whole flow | **No** (flow-internal) | Larger / intermediate data you'll read later in the flow. |
| **Message Body** | Until next step overwrites it | Yes (it *is* the payload) | The actual message. Empty body in a Content Modifier = "leave unchanged". |

### 1.4 Source Types available for header/property

`Constant`, `Expression`, `Header`, `XPath`, `Local Variable`, `Global Variable`,
`Number Range`, `External Parameter`. (Body supports only Constant or Expression.)
`Data Type` column applies only to `XPath` and `Expression`.

### 1.5 Canonical patterns (all real)

```
# capture the inbound body before an external call, to restore/compare later
property.savedBody   = ${in.body}                         (Expression)

# stamp the execution time
property.runTimestamp = ${date:now:yyyy-MM-dd'T'HH:mm:ss}  (Expression)

# assemble an envelope from parts already in headers/properties
body =
  <Envelope>
    <CorrelationId>${header.CID}</CorrelationId>
    <ProcessedAt>${property.runTimestamp}</ProcessedAt>
    <Payload>${in.body}</Payload>
  </Envelope>                                              (Expression)

# error-handler body (exception subprocess only)
body = <Fault><msg>${exception.message}</msg></Fault>      (Expression)

# a fixed routing constant
header.TargetSystem = ARIBA                                (Constant)
```

---

## PART 2 — Artifact / Step Catalog

The 33 distinct `activityType`s found in the corpus, with what each does, how it's
referenced in the `.iflw`, and current build status in the workbench.

### Legend
- ✅ **built + tenant-verified** (deploys and runs COMPLETED)
- 🟡 **decoded, not yet built** (schema known; needs a wirer + tenant verify)
- ⚪ **niche / lower priority**

| activityType (corpus count) | Function | iFlow signature (load-bearing) | Resource file | Status |
|---|---|---|---|---|
| **Enricher / Content Modifier** (618) | Set body/headers/properties | `cname::Enricher/1.5`; `bodyType` (constant\|expression), `wrapContent`, `headerTable`, `propertyTable` | — | ✅ |
| **Script** (436) | Groovy/JS logic | `ctype::FlowstepVariant/cname::GroovyScript`; `script=<f>.groovy`, `scriptFunction` | `script/<f>.groovy` | ✅ |
| **ProcessCallElement** (281) | Call a Local Integration Process | `cname::NonLoopingProcess`; references a `LocalIntegrationProcess` | — | 🟡 |
| **ExternalCall** (222) | Request-Reply to a receiver | `serviceTask` + Receiver participant + messageFlow + adapter | — (adapter cfg) | 🟡 |
| **ExclusiveGateway** (210) | Router (conditional branch) | `bpmn2:exclusiveGateway` + `GatewayRoute` conditions (Camel Simple/XPath) | — | 🟡 |
| **Mapping** (204) | XSLT or graphical mapping | XSLT: `cname::XSLTMapping/1.2.0`, `mappingSource=mappingSrcIflow`, `mappinguri=dir://mapping/xslt/...<n>.xsl`. mmap: `cname::MessageMapping`, `dir://mmap/...` | `mapping/<n>.xsl` (XSLT) / `<n>.mmap` (graphical) | ✅ XSLT / 🟡 mmap |
| **EndEvent / StartEvent** (196 / 172) | Flow terminals | `MessageEndEvent` / start | — | ✅ |
| **ErrorEventSubProcessTemplate** (143) | Exception subprocess | separate process pool, `StartErrorEvent`→…→`EndErrorEvent`; body uses `${exception.*}` | — | 🟡 |
| **Filter** (67) | Keep an XPath node-set | `cname::Filter/1.1.0`, `xpathType=Nodelist`, `wrapContent=//xpath` | — | ✅ |
| **DBstorage / Persist** (29 / 1) | Data Store / persist message | `cname::DataStoreOperations` etc. | — | ⚪ |
| **Splitter** (26) | Split a collection | `cname::GeneralSplitter/1.5.1`, `splitExprValue=/root/Record`, Streaming, threads | — | ✅ |
| **Send** (26) | Fire-and-forget to receiver | `serviceTask` + Receiver participant + messageFlow | — (adapter cfg) | 🟡 |
| **XMLDigitalSignMessage / SimpleSignMessage** (25/2) | Sign payload | signer variant | keystore | ⚪ |
| **Encoder / Decoder** (24 / 9) | Base64/MIME/etc. | encoder/decoder variant | — | ⚪ |
| **JsonToXmlConverter / XmlToJson / CsvToXml / XmlToCsv** (19/7/2/1) | Format converters | `cname::JsonToXmlConverter` etc. | — | 🟡 (easy) |
| **contentEnricherWithLookup** (18) | Enrich via external lookup | content-enricher variant + receiver | — | 🟡 |
| **Gather** (13) | Re-aggregate splits | `cname::Gather/1.2.0`, `aggregationAlgorithm=...` | — | ✅ |
| **StartTimerEvent** (13) | Scheduled start | `cname::TimerStartEvent/1.3.0`, `fireNow` | — | ✅ |
| **Variables** (12) | Write local/global variable | `cname::Variables` | — | ⚪ |
| **XmlModifier** (10) | Tweak XML decl/namespaces | `cname::XmlModifier` | — | ⚪ |
| **Multicast / SequentialMulticast** (9 / 7) | Send to multiple branches | `bpmn2` parallel/sequential multicast | — | 🟡 |
| **Join** (5) | Join multicast branches | `cname::Join` | — | 🟡 |
| **XmlValidator** (2) | Validate vs XSD | validator variant + schema | `xsd/<n>.xsd` | ⚪ |

### Adapters (ExternalCall/Send `ComponentType`, corpus count)

`SOAP` (110), `HTTP` (109), `ProcessDirect` (86), `HTTPS` (64),
`SuccessFactors` (37), `Mail` (19), `SFTP` (19), `JMS` (18), `IDOC` (2),
`Ariba` (2), `OData` (1). Each is a `ctype::AdapterVariant/cname::<Adapter>/vendor::SAP`
on a message flow between the process and a Receiver participant
(`ifl:type="EndpointRecevier"` — note SAP's own typo, copy it verbatim).

---

## PART 3 — Resource File Conventions (consolidated)

| Type | Step reference (in `.iflw`) | Bundle path |
|---|---|---|
| Groovy | `script=<n>.groovy` | `src/main/resources/script/<n>.groovy` |
| XSLT | `mappinguri=dir://mapping/xslt/src/main/resources/mapping/<n>.xsl` + `mappingpath` (no ext) + `mappingSrcIflow` | `src/main/resources/mapping/<n>.xsl` |
| Graphical mmap | `mappinguri=dir://mmap/src/main/resources/mapping/<n>` + `mappingType=MessageMapping` | `src/main/resources/mapping/<n>.mmap` |
| XSD / WSDL / EDMX | *not* step-referenced — referenced **inside** mappings/adapters | `src/main/resources/{xsd,wsdl,edmx}/<n>.<ext>` |

Every referenced file must be inside the uploaded bundle, or CPI fails with
"Mapping file not found" / "Script not found". (This was the bug fixed by
persisting resources to the meta dir and auto-including
`meta/src/main/resources/**` in the package.)

---

## PART 4 — Implications for the Workbench (where `${...}` earns its keep)

1. **Content Modifier property/header tables with Expression values.** The
   generator already supports `propertyTable`/`headerTable`; wiring in real
   Expression values (e.g. a `runTimestamp = ${date:now:...}` property, or
   `savedBody = ${in.body}` before a future external call) makes generated flows
   behave like hand-built ones. The Constant-vs-Expression switch is already
   correct.
2. **Endpoint-triggered flows.** Once Request-Reply/Send is built, the XSLT input
   no longer needs the seeded `<root><Record/></root>` constant — the real
   inbound body (`${in.body}`) becomes the mapping input.
3. **Router (ExclusiveGateway).** Branch conditions are Camel Simple/XPath
   expressions (`${header.docType} = 'INV'`), so the expression layer is a
   prerequisite for routing.
4. **Error subprocess.** The exception body uses `${exception.message}` /
   `${exception.stacktrace}` — the only place those resolve.
5. **Easy wins next:** the format converters (Json/Xml/Csv) are simple,
   file-less variants — low-risk additions to the palette after the current
   endpoint tier.
