# CPI Migration Workbench — Session Handoff v4

## Current state
- **288/288 tests passing**
- One Streamlit app, **two programs** behind a mode switch (sidebar "Mode" radio)
- Project: ~/PycharmProjects/cpi_migrator/
- User: SAP CPI Consultant, PyCharm 2026.1, Python 3.12, Linux Mint

## Two-program architecture (mode switch)
The sidebar "Mode" radio toggles st.session_state["active_program"]:
- Migration (PI/PO to CPI) — the original 10 tabs (Profiles ... Client Tracker)
- API Management — 6 tabs (Landscape, API Proxies, Products, Applications, Policies, Deploy)

Implementation note: in APIM mode the migration `with tabN:` blocks still
execute but render into a hidden st.empty() sink that is cleared at the end
of the file (_migration_sink.empty()), so the migration UI never shows.
This avoids re-indenting 3000+ lines. If the app is ever refactored, the
clean approach is to extract each tab body into a function and call
conditionally.

## Program 2 — API Management (apim/ package)
- apim/model.py — APIProxy, APIProduct, Application, APIKey (issue/revoke/
  expire lifecycle), APIMLandscape with validate() referential integrity.
- apim/policy_library.py — 7 parameterised policies (VerifyAPIKey, Quota,
  SpikeArrest, CORS, SetHeader, JSONThreatProtection, OAuthVerify) +
  POLICY_BUILDERS registry + list_policies().
- apim/proxy_generator.py — generate_proxy() produces a full proxy bundle
  (descriptor + proxy/target endpoints + policy set, 5-7 files, all valid XML).
  proxy_from_iflow() bridges a migrated iFlow into a managed API.

## Held-off Program 1 features (now built)
- scaffolder/content_modifier_generator.py — ChannelConfig to Content
  Modifier BPMN step + descriptor. Derives headers/properties from adapter
  type (IDoc/RFC/File/JDBC), uses credential ALIAS not value.
- scaffolder/value_mapping_generator.py — value pairs to importable CPI
  Value Mapping artifact XML.

## Template libraries (standalone, NOT wired into UI yet by design)
- templates/xslt/ — 12 templates, all Saxon-verified except ex3 (needs
  tenant) and pitfall_c (doc-only). Includes 5 migration_* templates.
- templates/groovy/ — 17 templates, static-only verified (no CPI runtime).
  Includes 5 migration_* + pitfalls a-f.

## Testing infrastructure
- testing/xml_differ.py — structural XML diff with tolerance rules
- testing/xslt_executor.py — lxml XSLT 1.0 executor + SAP extension stubs
- testing/fixture_harness.py — per-interface shadow-test runner
- Saxon (saxonche pip pkg) available for real 2.0/3.0 XSLT verification

## Verification honesty
- XSLT templates: verified against real Saxon EE (same engine family as CPI)
- Groovy templates: STATIC ONLY (brace/paren balance, imports, conventions).
  No CPI runtime available. Data-store templates (idempotency, aggregation)
  are highest-risk to deploy untested.
- APIM XML: structural (well-formedness + round-trip), NOT tenant-imported.
- Mode switch: verified via Streamlit AppTest (both modes render, proxy
  creation flow works end-to-end).

## Run tests
cd ~/PycharmProjects/cpi_migrator && python -m pytest tests/test_all.py -q

## Key operating rules (from user)
1. Don't change the program every time — if it works for one input but not
   another, suspect the input, diagnose before patching.
2. Hold features out of the UI until there's a strong batch — build
   standalone tested components, integrate in one coherent pass.
3. Be honest about verification limits — don't claim things work untested.
