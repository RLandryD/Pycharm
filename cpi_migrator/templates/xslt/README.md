# XSLT Template Library

Curated, real-world XSLT templates for SAP CPI migrations. Each file
documents pattern, verification status, and any known caveats.

| File | Pattern | XSLT | Verified |
|---|---|---|---|
| `ex1_graphical_to_xslt.xsl` | Graphical mapping equivalent (concat + if-then-else) | 2.0 | ✅ Saxon |
| `ex2_choose_when.xsl` | Conditional business rules (choose/when, castable as) | 2.0 | ✅ Saxon, 5 cases |
| `ex3_cpi_extensions.xsl` | CPI runtime calls (getMappedValue, setExchangeProperty) | 2.0 | ⚠ Needs tenant |
| `ex4_for_each_group.xsl` | XSLT 2.0+ grouping with current-group()/sum() | 2.0 | ✅ Saxon |
| `pitfall_a_json_array_force.xsl` | Force single-entry JSON arrays not to collapse | 3.0 | ✅ Saxon, 3 cases |
| `pitfall_b_strip_empties.xsl` | Identity transform + suppress empty nodes | 2.0 | ✅ lxml |
| `pitfall_c_exclude_prefixes.xsl` | Namespace prefix cleanup on output | 2.0 | Documentation pattern |

## Why each is here

- **Ex1-4**: Cover the core patterns consultants reach for every day —
  field rename, conditional routing, runtime data access, grouping.
- **Pitfalls A-C**: Address well-documented CPI behaviors that catch
  out new consultants and downstream receivers.

## What's NOT here yet

- Value Mapping artifact generation (separate concern, iFlow-level)
- Format converters (XML↔JSON↔CSV — these are iFlow steps, not XSLT)
- Real graphical `.mmap` content (needs client samples to validate)

## Adding more templates

If a new pattern is verified working in Saxon (and lxml where 1.0-compatible),
add it as `<category>_<short_name>.xsl` with the same header block:
- Pattern description
- Verification status + engine
- Known caveats
- Source attribution


## Migration-specific templates (added later)

| File | Pattern | XSLT | Verified |
|---|---|---|---|
| `migration_01_date_number_format.xsl` | SAP date/number conversions (DateTrans, FormatNum family) | 2.0 | ✅ Saxon |
| `migration_02_value_map_inline.xsl` | Inline value-map lookup table with miss-visible fallback | 2.0 | ✅ Saxon |
| `migration_03_default_injection.xsl` | Node-presence default/constant injection | 2.0 | ✅ Saxon |
| `migration_04_namespace_strip.xsl` | Strip ALL namespaces (full, vs pitfall_c prefix cleanup) | 2.0 | ✅ Saxon |
| `migration_05_message_split.xsl` | 1:N message split for downstream General Splitter | 2.0 | ✅ Saxon |
