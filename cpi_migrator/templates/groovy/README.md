# Groovy Template Library

Curated Groovy scripts for SAP CPI / Integration Suite migrations.
Each file documents pattern, verification status, and known caveats.

## Verification status

**Important:** All templates pass static analysis (syntax, brace/paren
balance, required imports, CPI Message API surface). None have been
executed inside a CPI tenant because the build environment doesn't have
JVM-based Groovy + the CPI runtime libraries. Before using any of these
in production, **deploy to a CPI dev tenant and verify against your
specific scenario**.

The patterns are based on documented CPI/Camel API conventions and
SAP Community-validated practices. Where a CPI-specific API path has
varied between tenant versions, the comment in the file calls it out.

## Templates

### Simple (single concern, straightforward CPI API usage)

| File | Pattern |
|---|---|
| `simple_01_dynamic_routing_headers.groovy` | Read payload, set routing headers for a downstream Router step |
| `simple_02_payload_truncation.groovy` | Cap large payloads, preserve head+tail with marker, record original length |

### Medium (multi-step logic, real-world parsing/IO)

| File | Pattern |
|---|---|
| `medium_01_multipart_form_data_parser.groovy` | Parse multipart/form-data manually since CPI doesn't natively |
| `medium_02_stream_buffering.groovy` | Materialise streaming body into bytes for multi-read scenarios |

### Complex (security-sensitive, multiple integrations)

| File | Pattern |
|---|---|
| `complex_01_custom_cryptography.groovy` | AES-256-GCM encrypt/decrypt via Secure Parameter store |
| `complex_02_oauth2_token_validation.groovy` | Validate inbound Bearer tokens via RFC 7662 introspection |

### Pitfalls (anti-patterns + the correct fix)

| File | Pattern shown wrong, then right |
|---|---|
| `pitfall_a_body_stream_consumed.groovy` | The #1 CPI Groovy bug — read without setBody → downstream gets empty |
| `pitfall_b_exception_swallowed.groovy` | Silent `catch (Exception ignored)` and three correct alternatives |
| `pitfall_c_messagelog_api.groovy` | Implicit `messageLogFactory` binding vs explicit `ITApiFactory` |
| `pitfall_d_null_header_handling.groovy` | NPE from assuming headers are present + case-insensitive header lookup |

## Common patterns across all templates

All templates use:
- `import com.sap.gateway.ip.core.customdev.util.Message` (CPI's Message interface)
- A single `def Message processData(Message message)` entrypoint
- Defensive `?:` Elvis fallbacks on `getBody()` and `getHeader()` to avoid NPEs
- `setBody()` immediately after any `getBody()` to preserve the stream

The cryptography and OAuth templates additionally use:
- `com.sap.it.api.ITApiFactory` for service discovery
- `com.sap.it.api.securestore.SecureStoreService` for credential retrieval

## What's NOT here

These are NOT included in the library, on purpose:

- **JMS / SFTP / S3 client code** — those are CPI adapter responsibilities;
  Groovy shouldn't reach past the adapter abstraction
- **Database calls** — same reasoning; use the JDBC adapter
- **Mail sending** — use the Mail adapter step
- **Scripts that mutate global state** — Groovy steps in CPI should be
  pure functions of (message in) → (message out)

## Adding more templates

If you add a new template, follow the same comment header structure:
1. Pattern description (what it does, in 2-3 lines)
2. Use case (when a consultant would reach for this)
3. Verification status (locally tested vs static-only)
4. Pitfalls handled (referencing the pitfall_* templates by name where applicable)

Run the brace/paren verifier in `tests/test_all.py::TestGroovyTemplates`
before committing.


## Migration-specific templates (added later)

All STATIC-ONLY verified (no CPI runtime in build env). The two data-store
templates (idempotency, aggregation) are the highest-risk to deploy
untested — verify the data store accumulation/retrieval cycle in a tenant.

| File | Pattern |
|---|---|
| `migration_01_edi_flatfile_parser.groovy` | Delimited flat file -> XML when CPI converters too rigid |
| `migration_02_dynamic_receiver.groovy` | Resolve receiver endpoint from routing table (channel consolidation) |
| `migration_03_idempotency_check.groovy` | Duplicate detection via data store (rebuilds EOIO/exactly-once) |
| `migration_04_mpl_masking.groovy` | Log to MPL with PII masking (GDPR/compliance) |
| `migration_05_aggregation_batch.groovy` | Collect N messages, emit one (ccBPM collect pattern) |
| `pitfall_e_variable_scope.groovy` | Header vs property vs global vs local scope confusion |
| `pitfall_f_charset_mismatch.groovy` | Charset corruption from implicit getBody(String) decoding |
