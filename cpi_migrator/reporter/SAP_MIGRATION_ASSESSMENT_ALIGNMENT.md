# SAP Migration-Assessment Alignment (PIMAS) — calibration reference

Source: SAP's PIMAS (Integration Suite Migration Assessment) rule engine
configuration, observed read-only via `/pimas/api/v1/` on the user's own trial
(all records `CreatedBy: SAP`). This is SAP's *methodology* — how SAP itself
scores PI/PO interface migration complexity and converts that to effort.

**Clean-room note:** we LEARN from this to calibrate our own `effort_model.py`
and `sap_complexity_engine.py` so they speak SAP's language and align to SAP's
thresholds. We do NOT republish SAP's rule catalog verbatim as our product.
This file records the *structure and thresholds* for alignment, not a copy of
SAP's data rows.

## 1. SAP's effort sizing (the headline calibration)

PIMAS converts a summed complexity **Weight** into a t-shirt **Size**:

| Size | Weight range |
|------|-------------|
| S    | 1 – 150     |
| M    | 151 – 350   |
| L    | 351 – 500   |
| XL   | 501 – 99999 |

**Use:** our effort model's size bands should align to these thresholds so our
estimates map onto SAP's own sizing. (Our Option-X model already uses size →
hours; this gives the authoritative weight→size cut points.)

## 2. SAP's rule-engine shape (3 rule types)

PIMAS scores an interface with 98 rules → 450 variants → 1000+ parameters.
Three rule types (mirror these in our complexity engine):

- **ValueMatchRule** (81 rules) — does a configured value/feature appear?
  (binary/categorical presence → weight). e.g. "uses a custom adapter module".
- **RangeRule** (11) — does a single count fall in a band? e.g. OMStepCount.
- **RangeSumRule** (6) — does a SUM of counts fall in a band? e.g.
  GMMCustomUDFUsageCount across the interface.

Each parameter row: `RuleMatchValue` (e.g. "5,99999") → `Weight` (1,5,10,15,20,
25,30,50,70…) → `MigrationStatus` (1=supported / 2=partial / 3=manual effort;
observed distribution ~751/54/195 → most features migrate cleanly, a long tail
needs manual work).

## 3. What SAP measures = the complexity-driver taxonomy (98 rules)

Grouped by family (this is the authoritative list of what drives migration
complexity — directly maps onto what OUR complexity engine should score):

**Sender adapter/protocol (24)** & **Receiver adapter/protocol (24)** — the
biggest families. SAP scores per-side: adapter type, custom adapter
type/module, QoS, transport protocol, file content-conversion, OS commands,
JDBC driver/isolation/batch, MAIL/JMS/REST/SOAP specifics, IDOC metadata.
→ *Our anatomical iflw learning must capture adapter type + custom modules; these are SAP's #1 complexity driver.*

**GMM — Graphical Message Mapping (20)** — UDF usage count, custom function-
library usage, RFC/JDBC/SOAP lookups, value mapping, dynamic configuration,
trace/system/fileOS access from UDFs.
→ *Directly relevant to OUR mmap work: SAP treats custom UDFs, lookups, and
value-mappings as the mapping-complexity signals. Our mmap parser already
extracts functions; we can flag these specific high-weight constructs.*

**JAVAM — Java Mapping (7)** — dynamic config, SOAP lookup count, value
mapping, lookup service, trace, system, fileOS. (Java mappings = high migration
effort; IMR010 recommends Java→Graphical/XSLT/Groovy.)

**ICO — Integrated Configuration Object (9)** — receiver/inbound interface
counts, operation count, alert rules, extended/arbitrary receiver
determination, schema validation, ordered-at-runtime.

**OM — Operation Mapping (5)** — step count, parameter count/type, multi-
message, XOP includes.

**XSLT (2)** — dependencies count, Java extension usage.
**Content-based routing (2)**, **MappingType (1)**, **FaultMessage (1)**,
**JavaBPM (1)**, **ccBPM (1)** — BPM constructs flagged (high effort).

## 4. SAP's modernization recommendations (16 rules, IMR001–IMR019)

Source-pattern → recommended-target (the adapter/style equivalence catalog):

- **Protocol:** File/SFTP/FTP → SOAP/OData/REST; IDOC → SOAP/OData/REST (or
  Business Event); RFC → SOAP/OData/REST; SMTP/POP3/IMAP → REST; JDBC → REST
- **Integration Style:** Multiple-receiver-async → Business Event; IDOC →
  Business Event; Polling → Push
- **Mapping:** Java mapping & ABAP → Graphical / XSLT / Groovy Script
- **Security:** HTTP → HTTPS; Basic → Certificate/OAuth; FTP → SFTP
- **Clean Core:** IDOC → Business Event / SOAP-OData API; RFC(BAPI) → SOAP-OData
- **Monitoring:** Alert rules → SAP Cloud ALM

→ *This is the PO→CPI equivalence seed for the eventual iflw clone-and-adapt
work: when adapting a migrated iflow, these are SAP's recommended target
patterns per legacy construct.*

## 5. How this calibrates OUR tool

1. **effort_model.py** — adopt SAP's weight→size cut points (§1) so our sizing
   aligns with SAP's. Our multiplier model stays; the size bands gain an
   authoritative anchor.
2. **sap_complexity_engine.py** — structure scoring as ValueMatch / Range /
   RangeSum rules (§2); use SAP's families (§3) as the checklist of what to
   score. Especially: adapter type + custom modules (sender/receiver), GMM
   custom UDFs / lookups / value-maps, Java mappings, ICO receiver/op counts,
   BPM presence.
3. **mmap analysis** — flag the GMM-family signals our parser can already see:
   custom UDF usage, value mapping, lookups → these are SAP's mapping-complexity
   drivers, so our per-mmap complexity should weight them.
4. **migration adapter-equivalence** — keep §4 as the source→target seed for
   iflw adaptation.

## 6. Honest limits

- This is assessment *methodology*, NOT mapping/transformation logic. It does
  not advance the PI mapping-format reverse-engineering (still gated on real
  PO-system access).
- `RuleParameter` was paginated (first 1000 of N rows seen); we have the
  structure and weight/status distribution, not every row — sufficient for
  alignment, not for exact replication of SAP's scoring (which we don't need).
- `MigrationStatus` semantics (1/2/3) inferred as supported/partial/manual from
  distribution + context; treat as directional until confirmed.
