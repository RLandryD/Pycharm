# CPI Migrator — Session Handoff (current; supersedes HANDOFF_prev_v4.md)

This is the orientation doc for continuing the project in a fresh chat. Read
this first, then the referenced files. (Prior handoff archived as
HANDOFF_prev_v4.md.)

═══════════════════════════════════════════════════════════════════════════
## 0. WHO / HOW (the working agreement — please honor these)
═══════════════════════════════════════════════════════════════════════════
- **User:** Ricardo (landry), SAP CPI consultant. Linux Mint, PyCharm, Python
  3.12. Project at `~/PycharmProjects/cpi_migrator/`. He runs ALL tenant tests
  himself — **his tenant logs are ground truth.** The assistant's sandbox
  CANNOT reach SAP/tenant hosts.
- **Run process:** replace folder (keep the venv), Ctrl+C + restart
  `streamlit run workbench.py`. Library scripts run `python -m
  library_builder.module` from project root. Paths relative (no leading slash).

## 0.1 HOW WE WORK TOGETHER (read this — it's why the project works)
This was built over many sessions with a specific, proven collaboration style.
The next assistant should step into THIS, not reinvent it:

- **Be the honest gate, not the eager yes-man.** The user has said explicitly he
  values honesty over confident-but-wrong, and praises re-checking over trusting
  theories. When something can't be done, or a claim isn't proven, SAY SO
  plainly. Don't dress up "should work" as "works." Don't claim SAP-certainty
  before his tenant confirms. This honesty is the foundation of the trust.
- **SUSPECT THE INPUT FIRST.** Most bugs were bad/ misunderstood input, not bad
  logic. Every rigor pass that found a real bug (schema lowercase-name= collapse,
  groovy exception.message false-positive, xslt portable-vs-version conflation)
  came from auditing the OUTPUT against reality and distrusting the first
  plausible result. ALWAYS do this audit before calling something done.
- **One variable per tenant test.** Multi-variable changes made mmap root causes
  un-isolatable and cost many days. Change ONE thing, have him test, learn, repeat.
- **Understand first, then build, then audit.** Analyse real specimens (+ docs)
  to UNDERSTAND how a thing works → encode that → VALIDATE against the specimens.
  Docs give the model; specimens + tenant are ground truth; reconcile, never
  assume docs are complete.
- **HOLD features until "build"/"generate".** Discuss/design deeply first. The
  user drives WHEN to build. He thinks out loud and refines requirements across
  several turns — engage with each refinement; don't jump ahead to code.
- **Communication style:** SHORT non-code answers, detail in code. Token-
  efficient. He often refines an idea over 2-4 messages before it's ready —
  reflect each refinement back precisely so he can confirm you've got it. He
  has corrected the design several times and was RIGHT each time (envelope
  substitution, universal-op binding, dropping the runtime, the EVALUATE layer,
  schema identity = structure-not-name) — take his domain instincts seriously.
- **Rigor pass is mandatory per artifact type.** After building an extractor,
  audit its output against reality (manual spot-checks + blind-spot hunts +
  cross-check against an executor where possible) BEFORE declaring done. groovy
  was first shipped without this and the user correctly asked for the pass,
  which found 3 real bugs. Do it every time now.
- **He's aware switching chats loses the rapport** — that's why this section
  exists. Honor the methodology above and you'll re-enter the rhythm fast.

═══════════════════════════════════════════════════════════════════════════
## 1. CURRENT STATE
═══════════════════════════════════════════════════════════════════════════
- **Tests: 472 passing.** Run:
  `cd <project> && . <venv>/bin/activate && python -m pytest tests/test_all.py -q`
- mmap generator + parser + capability catalog are the mature, proven core.
- PI/PO migration source material is BLOCKED (see §5) — an access wall, not a
  capability gap. Don't re-chase without new access.

═══════════════════════════════════════════════════════════════════════════
## 2. THE BIG WIN — mmap is SOLVED end-to-end
═══════════════════════════════════════════════════════════════════════════
**Generation:** `library_builder/mmap_generator.py` makes a tenant-valid,
editor-clean `.mmap` from a logical spec. Proven: generated mmap matched a
verified tenant mmap **100% on all entries** (75/75 fields: same sources, pin
order, functions; 53/53 binding params); user confirmed it opens cleanly and
delivers identical results.
- `generate_mmap(spec, validate=False)`. `validate=True` = pre-deploy pattern
  gate (raises ValueError on structural violations).
- `spec_from_excel(path)` reads SAP "mapping definition" Excel.
- **READ `library_builder/MMAP_PATTERNS_CONFIRMED.md` before touching
  generation.** Critical: the **pin rule** (args numbered per-function: 1st no
  pin, 2nd pin="1", 3rd pin="2"); const-as-arg vs const-as-binding
  (`_CONST_AS_ARG`={formatByExample,iF,iFS}); structured bindings
  (`<calend_props>`,`<sort_comp>`) verbatim from verified; `<namespaces>` block;
  `divide`→`div`.
- **Myths that were FALSE (don't repeat):** "structured bindings break opening",
  "namespaces break opening". Real early-failure cause = missing pin numbering +
  flat Excel labels where structured XML was required.

**Parsing (inverse):** `library_builder/mmap_parser.py` → `ParsedMmap` with
per-field expression trees. **100% function + 100%+ source recovery across all
120 non-empty mmaps** (3 → 4,476 bricks; handles pretty-printed multiline tags).

**Learning model:** `library_builder/MMAP_LEARNING_MODEL.md` — 112 functions
with FIXED arities; 3 shapes (direct / function-tree / context-aware); corpus
profile; 29/87 use context (N:M).

═══════════════════════════════════════════════════════════════════════════
## 3. CAPABILITY CATALOGS — architecture PROVEN on mmap AND groovy
═══════════════════════════════════════════════════════════════════════════
**mmap** — `library_builder/mmap_capabilities.py` — decomposes an mmap into
tagged capability units (one per target field): category, functions, sources,
constants, matchable `signature()`, SAP-aligned weight. **39,334 capabilities
across 120 mmaps, zero failures.** Solves blocker #2.

**groovy** — `library_builder/groovy_capabilities.py` — the CODE-capability
extractor, built on the model worked out with the user (see §6.5):
- script = **ENVELOPE** (`processData(Message)` wrapper — swappable template)
  + **UNIVERSAL OPERATIONS** (READ_*/WRITE_*/EMIT_* + portable PARSE_JSON,
  TRANSFORM_DATE, REGEX…) + **BINDING TABLE** (universal op → SAP API call,
  e.g. READ_PROPERTY → message.getProperty).
- SAP API vocabulary **discovered empirically** via `discover_bindings(corpus)`
  — NOT hard-coded.
- Each capability carries the **five adapt-facets**: purpose | needs |
  what_varies | shape | when_to_use, + honest `intent_confidence`.
- `build_catalog(corpus)` → capabilities + binding_vocabulary + signature index.
- **Proven: 498/498 real groovy specimens, zero failures, 163 signatures.**
  FETCH-by-need works (e.g. "parse JSON" → 84 caps).
**FIELD-SPEC LAYER (part 2)** — `library_builder/field_spec.py` + workbench
`_render_solution_fields`. From a solver solution (carrying source_target_slots)
+ matched capabilities, derives EDITABLE setup fields: variable-N source/target
(pre-filled from the requirement), externalized {{params}} (surfaced "set via
parameter, not hardcoded"), config. `build_field_spec`, `merge_edits`,
`apply_user_value`. KEY: `merge_edits` preserves user hand-edits across solver
re-runs — the "requirements change mid-call" safety (re-propose defaults WITHOUT
clobbering edited values). Logic fully sandbox-tested (5 tests incl. the
edit-preservation scenario). Wired into the Capability Solver panel as editable
Streamlit text inputs in session state. **UX is USER-TESTED visually** (Claude
can't render Streamlit) — the agreed division: Claude proves the logic, user
proves the UX.

**CORPUS SOURCING BUGS — FOUND + FIXED (user caught via 26KB groovy library vs
~10k files)** — two compounding bugs that silently shrank the capability catalog:
- **Bug 1: workbench never read the Final/ harvest.** It built only from Tab-1
  `uploaded_packages` (zip uploads); the 34k-file disk harvest at
  `~/PycharmProjects/cpi_migrator/Final/<ext-folders>/` was never touched. FIX:
  AI Solver panel now has a "Capability source folder" input → `build_corpus(
  path=...)` over the whole directory (recursive, cached via
  `_load_capability_corpus_dir`). Point it at Final/ to build from everything.
- **Bug 2: basename collision.** Both `walk_corpus` (dir) and `_read_zip` (zip)
  keyed files by BASENAME, so the many same-named files (e.g. hundreds of
  `script.groovy` across packages) collapsed to ONE (first-seen wins) — the
  direct cause of the tiny library. FIX: key by PATH-QUALIFIED name (relative
  path for dirs, full zip-internal path for zips). Verified 150 files→150 (was
  51); 20 same-named→20 (was 1). Extension detection still works on path keys.
  8 tests lock it (TestNoBasenameCollision + updated TestCorpusPipeline).
- The Hub 400 (external api.sap.com Business Accelerator Hub) is UNRELATED and
  correctly ignored — we use our own learned catalog + the destinations
  built-in static catalog.

**GAP FIXES (queued, this session)** — after the engines + bridge + recommender:
- **Gap 1 DONE** — part-1 bridge wired into the workbench AI Solver: a "Solve
  from a parsed interface" picker runs `solve_for` on a parsed InterfaceRecord,
  deriving the requirement + matching capabilities + carrying source/target slots
  so the field-spec layer pre-fills. Free-text path remains the fallback.
- **Gap 3 DONE** — fixed stale "not yet covered" label (claimed iflw/prop/js
  unbuilt — all 8 ARE built). Now shows covered types + "Non-capability files"
  (images/PDFs/JSON/jars) honestly; updated panel docstring + no-artifacts msg.
- **Gap 2 DONE** — strengthened capability-mode mappings with a schema-drafted
  tier: when no real .mmap matches, draft direct field mappings from learned
  schemas' element names (`_draft_mapping_from_schemas`), instead of a blank
  placeholder. RIGOR: no schemas → None; no field overlap → None (never
  fabricates non-matching fields). Fixed a wrong-key bug (schema catalog uses
  `identities`, not `capabilities`). 3 tests.
- **Gap 5 DONE (report-only)** — `TAB_OPTIMIZATION_AUDIT.md`: 10-tab analysis,
  merge candidates (Clean Core+Verify→Validate), the Match-vs-Solver overlap
  (complementary — recommend relabel not merge), move Profiles to settings,
  pipeline fast-path. NO tabs changed — recommendations for user decision.
- CAPABILITY-MODE NOW LIVE IN GENERATE ALL — the Generate tab loads the corpus
  once (cached, template fallback if absent) and passes it to `generate_bundle`,
  so real learned artifacts are produced; results show which came from real
  capabilities vs templates.
- Gap 4 (recommender rules vs real tenant errors) is NOT a code task — closes as
  the user feeds real tenant error bodies; engine degrades gracefully (unknown +
  raw msg) meanwhile.

**ERROR→RECOMMENDATION ENGINE (Tier 1)** — `fetcher/error_recommender.py` +
wired into `fetcher/cpi_uploader.py` + surfaced in the workbench. Takes a tenant
failure (stage + HTTP status + OData error body) → structured `Recommendation`
(cause, concrete fix, `fix_class`, `auto_fixable`, untruncated `raw_error`).
`parse_odata_error` robustly extracts the real message from CPI's varied error
shapes (nested JSON / string / XML / plain / malformed) and keeps it WHOLE
(fixing the uploader's prior 300-char truncation that lost detail). Rule base
covers upload (structural: missing resource, symbolic-name, invalid bpmn,
package-not-found, CSRF), deploy (semantic: groovy compile; config: unresolved
params, missing credential; substitution: unsupported function), and auth
(401/403). Rules are concepts (AND across) of synonyms (OR within).
THE SAFETY MODEL IS IN THE DATA: structural→auto_fixable=True (self-verifiable,
no side effects); semantic logic errors→auto_fixable=False (recommend only — won't
auto-strip behavior); unsupported-fn→substitution (bounded). This is the
foundation for the recommend→suggest→bounded-auto-fix tiers; a future loop may
only touch what's marked auto_fixable.
- WIRING: `_report_failure` (upload) + `deploy_iflow`/`fetch_deploy_error_detail`
  (deploy, incl. the deep IntegrationRuntimeArtifacts/ErrorInformation fetch) run
  the recommender; `UploadResult.recommendation` carries it. The workbench upload
  loop captures deploy-failed status + recommendation and renders a "🔎 Failure
  diagnosis" panel (cause + fix + fix_class badge + raw tenant message).
- RIGOR-AUDITED: caught synonym-matching AND/OR bug (Groovy-compile + unsupported
  -fn cases were missing) → fixed (concepts AND, synonyms OR); OData parser robust
  across all shapes. 8 engine tests + uploader wiring tests.
- HONEST LIMITS: rules validated against documented/sample CPI error bodies;
  Claude CANNOT test the live tenant — user confirms real responses. Unmatched
  patterns return fix_class=unknown + raw message (degrades gracefully, shows what
  rule to add). Recommend-ONLY (Tier 1) — surfaces, changes nothing. Tiers 2
  (suggest+approve) and 3 (bounded structural auto-fix / correctness-gated
  semantic) are designed but NOT built.

**INPUT→CAPABILITY BRIDGE (part 1)** — `library_builder/requirement_bridge.py`
— closes the gap where parsed inputs stopped at config shapes and never reached
the capability catalogs. `to_requirement(obj)` converts any input —
RequirementResult (requirement files), InterfaceRecord (MA Excel / PI inventory),
or PiCapability (a read PI mapping) — into a `CapabilityRequirement` with a
solver-ready `requirement_text` + structured sender/receiver slots.
`solve_for(obj, corpus)` runs the full input→EVALUATE→FETCH→SELECT and returns
the solution PLUS `source_target_slots` for the field-spec UI (part 2). Honest:
translates only the fields present (no fabricated senders for scheduled flows).
Proven end-to-end (requirement "orders as JSON → IDOC to S4" matched an iFlow
pattern + a mapping). RIGOR-AUDITED: all 3 input paths + scheduled-flow + unknown
-object error + the "mapping mapping" redundancy fix. NEXT: part 2 = field-spec
layer (editable workbench fields for externalized params + variable N
source/target, pre-filled requirement→capability→blank, user-editable, edits
preserved across re-runs) — user will test the rendering visually.

**pi (PI/PO → CPI) — `library_builder/pi_capabilities.py`** — NEW: a real PI
capability EXTRACTOR + PI→CPI TRANSLATOR, grounded in REAL specimens fetched
from public GitHub (we went from N=0 to real PI input this session). Per intent:
we never PRODUCE PI — we READ a PI mapping artifact, understand it, and translate
to "what to build in Integration Suite."
  * EXTRACT: classifies java_mapping (AbstractTransformation, transform(
    TransformationInput/Output)) vs udf_library (ESR @LibraryMethod/@Argument,
    tf7 runtime, DynamicConfiguration); pulls operations (READ/WRITE_BODY,
    DYNAMIC_CONFIG, LOOKUP/RFC/DB) + udf_methods.
  * TRANSLATE (`translate_to_cpi`): java_mapping → CPI Groovy/Java
    processData(Message) step; udf_library → CPI Groovy UDF in a graphical
    mapping (tf7 runtime is IDENTICAL — UDF logic transfers); DYNAMIC_CONFIG →
    CPI header/property; lookups → CPI external call. Emits `migration_specs`.
  Registered in facade + solver (needs_binding=True — migration → tenant build).
- RIGOR-AUDITED: fixed LOOKUP false-positive (was tagged from the
  `mapping.lookup` IMPORT, not real usage — now requires usage in the body, an
  import-stripped scan). Regression-tested.
- **HONEST LIMITS:** (1) built on a SMALL real sample (1 Java mapping + 1 UDF
  library) + documented PI contract + SAP modernization rules — genuinely
  grounded, NOT broadly corpus-validated; widen as more PI specimens arrive.
  (2) The `.tpz`/ESR-XML PACKAGE READER (parsing a full PI export into these
  capabilities) is STILL A HANDOFF item — needs a real `.tpz` (the access wall
  holds). What exists now reads PI mapping CODE artifacts; the package unwrapper
  is the next PI step. (3) Real specimens saved at /tmp/pi_corpus (Java mapping +
  UDF lib) — re-fetchable from GitHub (SriniBlog, js1972 gists).

**iflw (THE CAPSTONE)** — `library_builder/iflw_capabilities.py` — integration-
flow anatomy for CLONE-AND-ADAPT reuse. An iFlow's capability is its shape:
  * ADAPTERS — sender + receiver, from <messageFlow> ComponentType, direction
    inferred from source/target Participant refs (HTTP/SOAP/SFTP/IDOC/Mail/JMS/
    OData/ProcessDirect/SuccessFactors/RFC...).
  * STEPS — ordered activityType sequence (Enricher/Script/Mapping/Gateway/
    Splitter/Filter/Converter/DBstorage/ExternalCall/Send...) = what it DOES.
  * CONFIG — adapter/step properties + {{externalized}} params = what-varies.
Identity = sender + step-sequence + receiver. Purpose reads as a pattern
("SFTP → enrich, external-call, route, script → HTTP"). Registered in facade +
solver; always flags needs_binding (iFlows deploy to tenant).
**Proven: 164/164 specimens, zero failures, 133 distinct anatomies.** Search by
adapter-pattern finds whole iFlows to clone (e.g. "SFTP HTTP enrich" → the real
Concur/Fieldglass replication flows).
- RIGOR-AUDITED: verified adapter direction (0 failures — 49 genuinely sender-
  less, 6 no-adapter sub-process/templates, correctly represented); added a
  `trigger` dimension so timer/scheduled flows read honestly ("timer → ...")
  instead of a misleading "?". Regression-tested.

**props (prop/propdef)** — `library_builder/props_capabilities.py` — the iFlow
CONFIGURATION SURFACE (structural, not behavior — fresh model, not the groovy
template). `.propdef` = XML CONTRACT (parameter name/type/isRequired); `.prop` =
INI key=value VALUES. Identity = the SET of parameter names; locked principle
(from the retired kv_engine, confirmed on specimens): same key set = same config
solution, values are environment-specific EXAMPLES. `pair_configs` recognizes
prop↔propdef pairs (same key set) and merges values into the contract.
Registered in facade + solver (searchable by parameter name).
- RIGOR-AUDITED: confirmed values-with-`=` preserved (URLs/base64), comment-only
  → 0 params, empty `<name/>` skipped, same-keyset dedup. SCOPE (honest): targets
  iFlow .prop/.propdef; generic OSGi build.properties is OUT of scope (different
  artifact); real .prop are flat key=value (no line-continuation), so parser is
  deliberately simple — extend only if a real iFlow .prop needs it.

**js** — `library_builder/js_capabilities.py` — the CPI JS model, which MIRRORS
groovy (same envelope `function processData(message){...return message}`, same
SAP Message API bindings, same universal-op/portable-op split) in JS/Rhino
syntax. Captures envelope (declaration/expression/arrow forms), operations,
portable ops (JSON.parse/stringify, map/filter, string ops), body_read_as,
is_library, op_keywords. Registered in the facade + solver.
- RIGOR-AUDITED: fixed envelope missing the function-EXPRESSION form
  (`var processData = function(message)`); confirmed the exception.message
  false-positive is avoided (groovy lesson carried over). Regression-tested.
- **HONEST LIMIT: corpus has only 1 real .js specimen.** Unlike groovy(498)/
  xslt(131), this is NOT corpus-validated at scale — it's built on the validated
  groovy model + that 1 specimen + the documented CPI JS contract. Structurally
  sound; breadth of real JS idioms seen is narrow. Treat outputs as reasoned
  until more JS specimens confirm. (The extractor nailed the 1 real specimen:
  "parse JSON, build JSON, restructure + write-back", correct for a
  Nested→Flat JSON transform.)

**xslt (xslt/xsl)** — `library_builder/xslt_capabilities.py` — same model as
groovy, for transforms: PORTABLE TRANSFORM CORE (standard W3C XSLT) + BINDING
LAYER of SAP/java EXTENSION FUNCTIONS (cpi:setProperty/setHeader, error:throw,
ica_fn:* — discovered via `discover_extensions`). Identity = output method +
match patterns. Five facets like groovy. `build_catalog(corpus, verify=True)`
optionally CONFIRMS runnability by really compiling via lxml. **Proven: 131/131
specimens, zero failures; 109 vendor-neutral; 55 verified lxml-runnable.**
- RIGOR-AUDITED: caught conflation of "portable (no SAP)" with "lxml-runnable" —
  they're SEPARATE dims (lxml runs only XSLT 1.0; corpus is 62×1.0/37×2.0/32×3.0).
  Now tracks `portable`, `xslt_version`, `sandbox_runnable` distinctly, and
  `verify_runnable()` proves it by real compilation. Regression-tested.

**UNIFIED FACADE** — `library_builder/capability_catalog.py` — one entry point
over all four extractors: `catalog_for(kind, corpus)`, `type_for_ext(ext)`,
`build_all({type: corpus})`, `TYPES`. Register new types here as they're built.
This is the FETCH layer's single door for the solver.

**REASONING LAYER (B)** — `library_builder/solver.py` — the problem-solver:
`normalize(catalog, ctype)` maps any catalog into one `NormalizedCapability`
shape; then EVALUATE→FETCH→SELECT→ADAPT→COMPOSE (`evaluate`, `fetch`, `select`,
`adapt`, `solve`, `solution_summary`). FETCH uses corpus-derived IDF weighting
so rare specific terms discriminate. Honest boundary: proves fetch/adapt/compose
in sandbox; SELECT "best pick" + SAP execution need the user/tenant — every
solution is `confidence="reasoned"`, SAP-binding steps auto-flag
`needs_tenant_test`. Validated end-to-end on 887 normalized capabilities.
- RIGOR-AUDITED: caught "look up" (spaced) missing the lookup intent (fixed);
  found capabilities under-discoverable when their function wasn't in canned
  phrases → added corpus-grounded `op_keywords` to groovy (vocab 900→1202, MORE
  findable, no bias); dropped noise fragments; added IDF. Regression-tested.

**RETIRED + DELETED this session (the old parallel system):** extractor.py,
code_engine, function_engine, xslt_engine, mmap_engine, xml_engine, query.py,
matcher.py, run_extractor, run_matcher — all superseded by the capability
extractors + solver. They were a shallower, separate library/Solution catalog
that NOTHING live used (the workbench imports zero library_builder modules; it
uses its own GroovyLibrary + engine.claude_solver). `Solution`/`_sha` extracted
to `solution_types.py` so the kept engines stand alone. NET: library_builder
went 26→16 modules, one capability/solver pipeline, no duplicate engines.
KEPT (no new replacement yet): kv_engine (prop/propdef), iflw_engine (iflw),
requirement_reader (feeds EVALUATE), bundle_assembler/validator (deploy path),
reference_tables. These retire as their capability extractors get built.

**CORPUS PIPELINE** — `library_builder/corpus_pipeline.py` — the clean
orchestrator that REPLACED extractor.py/run_extractor. `build_corpus(path=...)`
or `build_corpus(files={name:text})` → walks a dir/zip/nested-zips/*_content,
classifies by type (facade), builds per-type catalogs, normalizes into the
solver view. Returns a `Corpus` with `.report()`, `.solve(requirement)`,
`.search(term)`. **Proven end-to-end on the real packages: 1,660 files →
40,223 capabilities across 4 types; solve + search both work.** The `.report()`
`classify.unknown` honestly shows what's NOT yet covered (167 iflw, 3 prop/
propdef, 1 js) = the next engines. Rigor-audited (dedup, empty, single-file,
xslt verify passthrough all pass). Honest limit (documented): walk keys by
basename, so same-named files across packages collide — true content-dupes are
handled downstream (schema catalog). This is the single entry point every future
engine (js/props/opmap/PI) flows through unchanged.

**schema (xsd/wsdl/edmx)** — `library_builder/schema_catalog.py` — the
"identity" type: NEVER generated, only REUSED. Catalogued WHOLE by the
STRUCTURE they define (analysed from the real corpus — identity is structure,
NOT the top name: 17 different XSDs all name their root "root"). Evidence-based
per-type identity rule:
  - xsd  = the SET of element + complex/simpleType names defined
  - wsdl = targetNamespace + that element/type set (ns scopes identity)
  - edmx = Schema Namespace + the SET of EntityType names (the OData tables)
Jobs: IDENTITY + DEDUPE (same structure = duplicate; personalized/trimmed
versions of the same source table collapse together; CANONICAL = well-formed +
LARGEST, so the fuller file wins, never a trimmed one) + VALIDITY (parses as
XML — tags open/close, comments intact; damaged schemas flagged + excluded from
reuse) + SUBSET families (one schema's defines ⊂ another's = candidate
personalized cut; superset preferred) + INDEX (by namespace / defined-name).
`build_catalog`, `find_schema(catalog, defines=…, namespace=…)`.
**Proven: 258 schemas (174 xsd, 71 wsdl, 13 edmx) → 210 distinct by true
structural identity, 30 real dup groups (e.g. B1_Sales_Order_XSD ≡
B1_Sales_Order_f; MATMAS05 dot-vs-underscore variants), zero failures.** Fully
standalone — pure W3C, no SAP, no tenant step.
NOTE: a fingerprint bug (lowercase-only name= match) once falsely merged 15
unrelated EDMX/UBL files into one group — caught by "suspect the input",
fixed (per-type identity + content fallback), regression-tested.

═══════════════════════════════════════════════════════════════════════════
## 4. SAP MIGRATION METHODOLOGY captured (effort/complexity calibration)
═══════════════════════════════════════════════════════════════════════════
`reporter/SAP_MIGRATION_ASSESSMENT_ALIGNMENT.md` — read-only from SAP's PIMAS
rule engine (all `CreatedBy: SAP`). Authoritative:
- **Effort sizing:** weight→size S=1-150, M=151-350, L=351-500, XL=501+.
- **Complexity taxonomy:** 98 rules (ValueMatch 81 / Range 11 / RangeSum 6);
  families: Sender/Receiver adapters (48, the #1 driver), GMM graphical mapping
  (20: UDFs/lookups/value-maps), Java mapping (7), ICO (9), OM (5), XSLT, BPM.
- **16 modernization rules** (source→target equivalence).
- Clean-room: align to it; don't republish as product.

═══════════════════════════════════════════════════════════════════════════
## 5. PI/PO MIGRATION — honest status (BLOCKED; documented for when unblocked)
═══════════════════════════════════════════════════════════════════════════
- **The trial cannot yield PI/PO mapping material.** Exhaustively confirmed:
  download portal=entitlement wall; "Add PO System"=needs live PO + Cloud
  Connector; PO runtime profiles uActivatable (licensing); Integration Advisor
  MIG/MAG not provisioned; PIMAS=assessment metadata only, no mapping logic.
- **Intel for when access comes:** `.tpz` = tar.gz of ESR XML (trivial to open).
  CPI mapping engine IS the PO tf7 engine (confirmed via groovy imports
  `com.sap.aii.mappingtool.tf7`). SAP's own migration tooling converts PI→CPI
  and OUTPUTS THE CPI MMAP FORMAT WE ALREADY MASTERED. So our migration value =
  orchestration/cataloging/adaptation, not re-doing SAP's converter.
- **Unlock:** a real PO system via the "Add PO System" screen — arrives with a
  real migration PROJECT. Then crack `.tpz` like we cracked mmap (examples, one
  variable at a time).

═══════════════════════════════════════════════════════════════════════════
## 6. PRIORITY PLAN (user-agreed) — what to do next
═══════════════════════════════════════════════════════════════════════════
3 kinds of learning per object type:

| Object | Goal | Output | Status |
|--------|------|--------|--------|
| mmap | functional | generate from spec | DONE (proven + capability catalog) |
| groovy | functional grammar | capability catalog (5 facets) | DONE (498 specimens, 476 tests) |
| JS | functional grammar | capability catalog | DONE (1 specimen — see note) |
| xslt/xsl | functional grammar | capability catalog (core+ext) | DONE (131 specimens) |
| opmap / propdef | structural | replicate + parameterize | propdef DONE (in props) |
| prop / propdef | structural config | config-surface capability | DONE (prop↔propdef pairing) |
| xsd/wsdl/edmx | identity | dedupe + index + reuse | DONE (258 schemas, 480 tests) |
| iflw | anatomical | clone-and-adapt | DONE (164 specimens) — ALL 7 TYPES COMPLETE |

**NEXT — Groovy functional-grammar extraction (unblocked, no tenant needed).**
Per the user's framing: NOT replicate code 100%, but extract HOW each function
works — imports/dependencies, I/O contract (reads message/headers/properties,
what it returns), data types, how operations/regex/transforms are written — so
the program can fetch a **raw functionality and adapt it to a real problem.**
Build a capability catalog of adaptable functionalities (like
mmap_capabilities.py, for code). 180+ groovy specimens in the official packages.
Then JS, then XSLT (same approach; model hardens each time).

Then: opmap/propdef (structural), schema dedup/index (easy, tenant-independent),
finally iflw (anatomical clone-and-adapt).

**UI (deferred):** a "Generated Items list" to select downloads for self-upload,
NOT per-object generate buttons. Build after ≥2 generators exist.

═══════════════════════════════════════════════════════════════════════════
## 6.5 THE SOLVER ARCHITECTURE (designed with the user — build toward this)
═══════════════════════════════════════════════════════════════════════════
The end goal is a PROBLEM-SOLVER, not a snippet library. It formalizes what a
consultant (or this assistant) does when asked "solve this in CPI":

```
        ┌─ UNDERSTANDING (analysis + each type's DOCUMENTATION, validated
        │   against the real specimens) feeds EVERY layer below ─┐
        ↓          ↓           ↓          ↓            ↓          │
   EXTRACTOR →  CATALOG  →  EVALUATE → FETCH → SELECT+ADAPT → COMPOSE
   (mechanics) (vocabulary  (decompose (match  (fit+adapt    (coherent
               +what-varies) problem)  equiv.) the varying)   A+B+C)
                                                                ↓
                            "should work in SAP, reasoned justification"
                                                                ↓
                        [LAST] SAP binding + iflw wiring → TENANT confirms
                               (same loop as mmap: build → user deploys → tenant truth)
```

Key design decisions reached with the user (honor these):
- **Capabilities are real, self-contained skills**, each validated ON ITS OWN
  that it correctly does its own operation in its own native way. NO substitute
  runtime, NO shared data-shape contract, NO inter-capability handoff (SAP does
  data flow between steps at runtime). Each artifact reads its input its own way.
- **The SAP layer = a thin envelope + a swappable binding table.** Almost
  nothing is irreducibly SAP: getProperty/getHeader/setBody/addAttachmentAsString
  are universal operations (READ/WRITE/EMIT) with SAP bindings. Even MPL logging
  = EMIT to a sink; only the sink is SAP. The binding table is discovered
  empirically from the corpus.
- **Extraction target = the FIVE FACETS** (what the assistant uses to ADAPT, not
  copy): purpose / needs / what-varies / shape / when-to-use. "what-varies" is
  what makes a capability adaptable; "when-to-use" drives SELECT.
- **Understanding-first method** (how mmap actually succeeded): understand each
  type deeply (analysis + its official documentation) → encode into extractor +
  catalog + the reasoning layers → VALIDATE against real specimens. Docs give
  the model; the specimens (and ultimately the tenant) are ground truth —
  reconcile, don't assume docs are complete (this is the mmap lesson).
- **Sandbox proves logic; tenant proves SAP.** Extractor/reader/catalog/fetcher/
  solver AND portable-logic execution are all sandbox-testable. Only the final
  SAP binding/wiring needs the user's tenant. The user WILL test each against
  the tenant — that's a shared step, same as mmap.
- **Certainty boundary (hold honestly):** understanding makes a solution correct-
  by-construction and well-justified ("should work, here's why"); the TENANT
  makes it certain. Never claim SAP-certainty before the tenant confirms.

## REPLICATION RECIPE (how to build each remaining type's extractor)
Follow the proven groovy/mmap pattern, per type:
1. Collect ALL real specimens of the type from the packages (walk the zips).
2. ANALYZE empirically first: entry shapes, the SAP-coupling vocabulary (the
   binding table — discover it, don't assume), the portable-core vocabulary
   (libraries/elements used). Read the type's official docs for mechanics.
3. Build the extractor: envelope/identity + universal operations + binding
   table + the five facets + signature. Honest `intent_confidence`.
4. `build_catalog(corpus)` → capabilities + binding_vocabulary + signature index.
5. TEST in sandbox: 100% extraction (zero failures), FETCH-by-need works, and
   EXECUTE portable cores where possible (lxml for XSLT, schema parse for
   xsd/wsdl/edmx — these are FULLY standalone, no SAP at all).
6. Lock with tests in tests/test_all.py. Then the user tenant-tests the binding.

Type-specific notes (from this session's coupling analysis):
- **xsd / wsdl / edmx**: NO SAP coupling — pure W3C. Fully standalone; extractor
  is identity+dedupe+index; executable/validatable entirely in sandbox.
- **xslt / xsl**: THIN coupling — standard W3C XSLT + 1-2 optional SAP/java
  extension namespaces (the binding layer). Transform logic is portable;
  runnable via lxml in sandbox.
- **js**: thin envelope, like groovy.
- **prop / propdef / opmap**: structurally general (plain kv/XML, no SAP code)
  but semantically SAP-config — parseable standalone, meaningful only when wired.
- **groovy**: DONE — use groovy_capabilities.py as the template.

## NEXT REASONING LAYER (after ≥2-3 catalogs exist)
Wire the catalogs into the solver: EVALUATE (decompose a requirement into needed
capability signatures — extends requirement_reader), FETCH (match needs to
catalog signatures), SELECT+ADAPT (pick best fit, fill what-varies), COMPOSE.
Also wire capability search into query.py + matcher (capabilities are extracted
but not yet searchable through the query spine).

═══════════════════════════════════════════════════════════════════════════
## 7. KEY FILES (in the project zip)
═══════════════════════════════════════════════════════════════════════════
- `library_builder/mmap_generator.py`, `mmap_parser.py`, `mmap_capabilities.py`
- `library_builder/MMAP_PATTERNS_CONFIRMED.md`, `MMAP_LEARNING_MODEL.md`
- `reporter/SAP_MIGRATION_ASSESSMENT_ALIGNMENT.md`
- `tests/test_all.py` (472 tests)
- `HANDOFF.md` (this), `HANDOFF_prev_v4.md` (archive)
For groovy work: bring the official packages (180+ .groovy) + part1/part2.

═══════════════════════════════════════════════════════════════════════════
## 8. ENV REFERENCE
═══════════════════════════════════════════════════════════════════════════
BTP trial: US East (VA) AWS; Global/Subaccount/Org 0140aa99trial; Space dev.
Runtime `0140aa99trial.it-cpitrial05...`; Design `0140aa99trial.
integrationsuite-trial...`; PIMAS `0140aa99trial.pimas-cpitrial06...`.
PENDING (user-side): rotate tenant client secret (BTP → subaccount → Instances
→ Process Integration Runtime → service keys → recreate → update local store).

═══════════════════════════════════════════════════════════════════════════
## 9. CLEAN-ROOM / IP
═══════════════════════════════════════════════════════════════════════════
Learn patterns; don't republish SAP's shipped files or PIMAS rule catalog as
product. Format knowledge is legitimately derived from the user's own tenant
exports. No upload of the full project anywhere; only deliberate test-bundle
outputs.
