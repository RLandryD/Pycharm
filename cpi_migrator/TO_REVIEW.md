# TO_REVIEW

Items discussed during development that were **not** adopted in the latest
wire-up batch because they carry medium-to-high implementation risk against
the running codebase. Each item carries the risk that motivated deferral so
future sessions can decide deliberately whether to take it on.

Last updated: 2026-05-28 session.

---

## Medium risk — touches existing generators

### MR-1. `scaffolder/schema_bundler.py` wire-up

**What it does** Bundles WSDL/XSD dependencies into the iFlow output zip and
updates iFlow XML references to point at the bundled paths.

**Risk** Touches the iFlow generation pipeline. Changing how iFlow references
resolve could break previously-working generated iFlows in subtle ways
(broken references that only surface at CPI import time, not at zip creation
time). Needs a focused validation pass: generate iFlows with and without the
bundler, import both into a CPI tenant, confirm no behavior change for the
without-bundler case.

**When to adopt** After the next CPI tenant is available — must be
validated against actual import behavior, not just file existence on disk.

---

## Medium-high risk — touches templates or runtime behavior

### MHR-1. Exception subprocess wired into iFlow XML

**What it does** Adds `<exceptionSubprocess>` blocks to the iFlow template
that reference the generated Groovy exception handler scripts. Today the
scripts are generated but the iFlow XML doesn't link to them.

**Risk** Touches the load-bearing iFlow template that all other features
depend on. A malformed exception subprocess could cause iFlows to fail to
import or to mis-route errors silently. Needs careful structural validation
of the resulting `.iflw` against the BPMN schema CPI uses.

**Scope estimate** ~80 lines of template changes + structural tests.

**When to adopt** When committed to a real client engagement — the upside
(actually-routable error paths in generated iFlows) is worth the risk only
when there's a target to validate against.

---

### MHR-2. Content Modifier generation from channel data

**What it does** Reads sender/receiver-specific headers, properties, query
parameters from channel data (PI/PO ESR or client tenant Excel) and emits
`<callActivity>` Content Modifier steps wired into the iFlow's sequence
flow.

**Risk** Largest single change to the iFlow generation pipeline. Affects
every generated iFlow, not just ones with mappings. Bad header logic
silently corrupts payloads at runtime. Needs paired channel data
(impossible without a real PI/PO export or carefully constructed mock).

**Scope estimate** ~300 lines plus a full template restructuring.

**When to adopt** After the **tenant config Excel intake** (see HR-1) is
built, since that gives the data structure Content Modifiers consume.

---

### MHR-3. Tenant config Excel intake

**What it does** Multi-sheet Excel reader for client-supplied tenant
configuration: environments, source systems, destination systems,
per-interface settings. Wires the parsed data into iFlow template, Content
Modifier headers, and `parameters.prop`.

**Risk** Defines the data contract that several future features depend on
(MHR-2, MHR-4). Getting the schema wrong now means rework later. The format
also needs to be something a client can realistically fill in — too
detailed and they won't, too loose and it doesn't carry enough data.

**Scope estimate** ~half a session for the parser, plus integration time
proportional to how many generators consume it.

**When to adopt** Highest value of the deferred items if a real client
engagement is imminent. Should be designed *with* a real client's data in
hand to validate the schema is realistic.

---

### MHR-4. XSD-driven `.mmap` auto-mapper

**What it does** Reads source XSD + target XSD pair, generates a `.mmap`
file with auto-matched fields (case-insensitive + similarity), flags
non-matching fields for manual review.

**Risk** The `.mmap` format is binary-XML with brittle structure rules.
Generated mappings that look correct in code might fail to open in CPI's
graphical editor, or open but produce wrong output. The auto-match
algorithm also has high failure modes: false-positive matches between
similarly-named fields with semantically different content.

**Scope estimate** ~2 sessions for an MVP covering ~70% of standard
mapping function types. The other 30% (UDFs, complex graphical
constructs) are out of scope.

**When to adopt** When the consultant has a corpus of real `.mmap` files
to validate the generator output against — without that, building
this is testing against synthetic data that doesn't capture
real-world weirdness.

---

## High risk — new architecture surface

### HR-1. Shadow testing engine (4 phases)

**What it does** Validates migration fidelity by feeding identical input
through two execution paths (original PI mapping + new CPI iFlow) and
diffing the outputs structurally with tolerance rules.

**Risk** Significant new surface area: structural diff engine, expected
output fixture format, XSLT execution, mock backends, possibly a JVM-based
UDF runner. Multiple sessions of work, each with its own risk.

**Phases (from earlier session)**
- Phase 1: structural diff + expected-output fixture format (1 session)
- Phase 2: XSLT execution + mock backends (1 session)
- Phase 3: graphical mapping executor (3+ sessions)
- Phase 4: JVM-based UDF runner (optional, only if needed)

**When to adopt** Phase 1 alone is high-value and bounded; phases 2-4 should
wait until phase 1 has been used against a real client mapping corpus and
proven its worth.

---

### HR-2. Partner Directory pattern for bulk-similar interfaces

**What it does** Detects groups of LOW interfaces that share an iFlow shape
and differ only by endpoint/partner. Generates one parameterized iFlow + a
Partner Directory CSV that covers the whole group.

**Risk** Affects the relationship between interfaces and iFlows (today
1:1; this would make it N:1). Downstream code (TDD writer, security
inventory, proposal generator) assumes 1:1 and would need to handle the
grouped case carefully or report misleading numbers.

**Scope estimate** ~1.5 sessions including the downstream propagation.

**When to adopt** When dealing with a real 200+ interface project. Below
that, the time saved by collapsing similar interfaces doesn't justify the
refactor.

---

## Out of scope for the foreseeable future

### OOS-1. Hub credentials autoconnect file

**What it does** A small `~/.cpi_migrator/hub_credentials.json` file
(encrypted via existing AES-256 credential store) auto-loads the Hub API
key on startup.

**Risk** Low risk technically; deferred because the upside is small.
Catalog browsing works fine with manual paste; package zip download is
still gated through Integration Suite UI regardless of auth method.

---

### OOS-2. MCP/Joule readiness gates, API policy library, application/API key lifecycle modeling

**What they do** Various Program 2 features that could be reused in
Program 1.

**Risk** Each is fine; deferred because Program 2 itself is not yet
prioritized and these features need Program 2 context to slot into.
