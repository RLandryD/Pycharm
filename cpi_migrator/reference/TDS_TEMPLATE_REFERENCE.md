# Real TDS (Technical Design Specification) — Structure Reference

Source: PYR140_TDS_Payroll_Audit_Report.docx + RCI093 TDS (real Cintas/Deloitte
deliverables, 2024). The authoritative structure for our doc-generator (TDD/TDS).

## Document structure (real consultant TDS sections, in order)
1. Title block: "SAP SuccessFactors Integrations / Technical Design
   Specification / <Interface ID> <Name>"
2. Table of Contents
3. Revision History (table: Version | Date | Author(s) | Description)
4. Reviewer/Approver and Key Contacts
5. Open Items (often "NA" initially)
6. General Data
7. Introduction
   - Objective
   - Bulleted scope list ("X consists of the following: ...")
8. High Level Integration Landscape Diagram (image placeholder; "NA" if none)
9. Overview & Trigger (Requirements)
10. Integration Design
11. Technical Process Design
    - Technical Design Steps (numbered, e.g. 6.2)
    - Processing logic (INITIALIZATION, AT SELECTION SCREEN, START OF SELECTION,
      GET PERAS, per-record steps)
    - Field population per mapping sheet
12. Mapping sheets (tables: source field -> target field)

## Tables used (7 in the sample)
- Revision History
- Reviewers/Approvers
- Field mapping sheets (the core technical content)

## Implications for our doc-generator
Our TDDGenerator should produce THIS structure, not a generic one. Key sections
consultants expect: Revision History + Approvers + Open Items up front,
Objective + scope bullets, Landscape diagram slot, Requirements/Trigger,
Integration Design, Technical Process Design with numbered steps, and mapping
sheets as tables. The mapping sheet tables are the deliverable's core — they
should be auto-populated from our parsed interface + mapping data.

## Note
This is an ABAP-side TDS (payroll audit report program) — shows consultants
document both the SAP-backend logic AND the integration. Our generator focuses
on the integration/iFlow side but the section skeleton is the same.
