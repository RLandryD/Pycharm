# iFlow Structure Reference — Extracted from 97 Real SAP iFlows

Derived by structural analysis of 97 production iFlows across 55 SAP standard
packages. The spec for the iFlow-wiring generator. STRUCTURE/PATTERNS only —
not SAP business logic.

## Step kinds (activityType), by real frequency
| count | activityType | meaning |
|---|---|---|
| 406 | Enricher | Content Modifier (most common — set headers/properties) |
| 328 | Script | Groovy/JS script step |
| 188 | ExternalCall | request-reply to a receiver |
| 157 | ExclusiveGateway | router (if/else branch) |
| 97  | ProcessCallElement | call a local sub-process |
| 92  | Mapping | message mapping step |
| 86/70 | EndEvent / StartEvent | flow start/end |
| 77  | ErrorEventSubProcessTemplate | **standard exception subprocess** |
| 77  | StartErrorEvent | error handler entry |
| 43  | EndErrorEvent | error end |
| 29/13 | Json/Xml converters | format conversion |
| 19  | Splitter | split message |
| 17  | EscalationEndEvent | escalate |
| 15  | Filter | filter |
| 7   | Gather / StartTimerEvent | aggregate / scheduled start |
| 5   | Multicast | parallel branches |

## Adapters (ComponentType), by frequency
HTTP(131), ProcessDirect(60), SOAP(53), HTTPS(48), JMS(28), HCIOData/OData(15),
IDOC(14), AMQP(4).

## Structure stats
- avg 10.5 steps/iflow (range 2–83), avg 3.6 adapters
- 45% have explicit error-event handling; ~42% use the exception subprocess

## THE COMMON ENVELOPE (every iFlow shares this shell)
```
<bpmn2:definitions>
  <bpmn2:collaboration>
    <bpmn2:participant>          (sender system)
    <bpmn2:participant>          (the integration process — "Integration Process")
    <bpmn2:participant>          (receiver system)
    <bpmn2:messageFlow>          (sender -> process)   = sender adapter
    <bpmn2:messageFlow>          (process -> receiver) = receiver adapter
  </bpmn2:collaboration>
  <bpmn2:process>                (the main flow: start -> steps -> end)
    + optional <bpmn2:subProcess> ErrorEventSubProcessTemplate (exception handler)
  </bpmn2:process>
  <bpmndi:BPMNDiagram>           (visual layout)
</bpmn2:definitions>
```
- 1 definitions, 1 collaboration, 1 process, 3 participants, 2+ messageFlows.

## THE STANDARD EXCEPTION SUBPROCESS (folded into every generated iFlow)
Real structure (from "Get Sales Quote from SAP S4HANA"):
```
subProcess [ErrorEventSubProcessTemplate]
  StartErrorEvent ("Error Start")            <- catches any exception
  -> Enricher ("Get Exception Message")      <- capture error into a property
  -> Enricher ("Prepare Error Response")     <- build error payload/headers
  -> ExclusiveGateway (route by error type, e.g. 404 vs others)
  -> EndEvent / EndErrorEvent
```
Pattern: catch → capture message → prepare response → (optional route) → end.
This is what makes an iFlow "review-ready" — a reviewer expects it.

## RECURRING FLOW VARIANTS (selectable patterns)
1. **Linear** (most common): Start → Enricher → Mapping → ExternalCall(receiver) → End
2. **Router**: adds ExclusiveGateway after mapping to branch by content
3. **Splitter/Gather**: Splitter → per-item processing → Gather (batch/multi)
4. **Scheduled**: StartTimerEvent instead of message start (polling)
All variants get the exception subprocess folded in.

## GENERATOR IMPLICATIONS (Phase 1)
- Emit the common envelope (collaboration + 3 participants + process).
- Default to the linear variant; select others when detected (splitter if
  multi-mapping, router if conditional, timer if scheduled).
- ALWAYS fold in the standard exception subprocess.
- Most-used steps to template first: Enricher (CM), Script, Mapping,
  ExternalCall, ExclusiveGateway.
- Adapters to support first: HTTP/HTTPS, SOAP, OData, IDoc, ProcessDirect, JMS.

## Status: extracted. This is the iFlow-wiring spec.
