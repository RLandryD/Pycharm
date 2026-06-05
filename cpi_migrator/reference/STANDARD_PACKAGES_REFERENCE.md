# SAP Standard Integration Packages — Structural Reference

Source: 55 real SAP Discover standard integration packages (master data
replication, Sales/Service Cloud, IBP, CPQ, Logistics). Used STRICTLY to learn
structure and wiring patterns for the iFlow-wiring feature — NOT to copy SAP's
business logic, mappings, or scripts into tool output or client deliverables.

## Confidentiality line (important)
These are SAP's copyrighted content. We extract:
  - HOW a configured iFlow is wired (step types, adapter config, param refs)
  - Naming conventions, package structure, externalization patterns
We do NOT:
  - Reproduce SAP's mappings/scripts/business logic as generated output
  - Redistribute these packages
The tool generates ITS OWN content in these structural forms.

## Confirmed: a real configured iFlow (the template-quality target)
Example "replicate business partner from sap erp":
  - 6 <bpmn2:callActivity> processing steps (vs our generator's ZERO)
  - 3 <bpmn2:messageFlow> adapters (sender + receivers)
  - parameters.prop + parameters.propdef (externalization, as decoded before)
  - .mmap graphical mapping + 2 Groovy scripts + WSDL + JSON schema
  - META-INF/MANIFEST.MF (confirms the upload-packaging fix is correct)

Package contents pattern:
```
META-INF/MANIFEST.MF
metainfo.prop
src/main/resources/
  scenarioflows/integrationflow/<name>.iflw   <- configured, multi-step
  parameters.prop / parameters.propdef         <- externalized config
  mapping/*.mmap                                <- graphical mappings
  script/*.groovy                               <- helper scripts
  wsdl/*.wsdl, json/*.json, xsd/*.xsd           <- schemas
publicsign.crt                                  <- SAP signature (their content)
```

## Breadth available (every common pattern covered)
- Master data replication: business partner, customer, product, employee,
  equipment, org unit, region, UoM, pricing, many code lists (~40 packages)
- Cloud suites: Sales Cloud V2, Service Cloud V2, IBP, CPQ, Logistics, Omnichannel
- Adapters seen across set: OData, IDoc, SOAP, plus value-mapping collection
- Sync + async patterns, splitters, content modifiers, scripts

## Use for the iFlow-wiring build
This corpus is the validation set: the wiring feature should produce iFlows
whose STRUCTURE matches these (step types, adapter config, param externalization)
— validated by comparing generated output shape against these references.
No more downloads needed; this covers the full pattern space.

## Status: catalogued. Sufficient reference for iFlow-wiring. Do not expand.
