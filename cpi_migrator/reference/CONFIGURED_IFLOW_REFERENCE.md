# Configured iFlow — Verified Serialization Reference

Learned by diffing a real iFlow before/after adding a sender adapter +
externalized parameters in CPI, then exporting. Every detail below is
CONFIRMED from a real CPI export (user's own content). This is the spec the
generator must match to produce CPI-valid, importable iFlows.

## Confirmed: per-artifact bundle format is accepted + lossless
A single-artifact bundle (META-INF/MANIFEST.MF + .project + src/main/resources/)
uploads, imports, renders, and round-trips losslessly. The package-wrapper
format (resources.cnt + hash) is NOT needed for per-artifact upload and was
correctly abandoned (its hash algorithm is IS-internal, not reproducible).

## What a sender adapter (message flow) adds — EXACT serialization
Adding an OData sender connecting Participant → StartEvent produced:

1. A <bpmn2:messageFlow> in the collaboration:
   - sourceRef = the sender Participant id, targetRef = the StartEvent id
   - ComponentType (e.g. ODataSender), ComponentNS=sap, direction=Sender
   - cmdVariantUri: ctype::AdapterVariant/cname::sap:ODataSender/tp::HTTP/
     mp::OData V2/direction::Sender/version::1.3.1   (adapter-specific!)
   - adapter-specific attrs (entitySet, edmxPath, operation=GET_FEED,
     authentication=basic, userRole=ESBMessaging.send, etc.)
   - externalized values use {{param_name}} inline

2. A <bpmndi:BPMNEdge> in the diagram:
   - bpmnElement=MessageFlow_id, sourceElement=BPMNShape_<participant>,
     targetElement=BPMNShape_<startevent>, two di:waypoint x/y points

So a connected adapter = messageFlow (collaboration) + BPMNEdge (diagram),
BOTH required. Counts went 0→2 messageFlows, +2 shapes, +6 edges.

## Externalized parameters — EXACT mechanism (two files)
Adding externalized params created TWO files:

1. parameters.prop (the VALUES, Java-properties; spaces escaped as `\ `):
   ```
   #<date>
   test=
   test_entity\ set=
   ```
   (empty value = to be filled at config time)

2. parameters.propdef (the DEFINITIONS + bindings):
   - <parameter> block per param: name, type=xsd:string, isRequired, etc.
   - <param_references>: binds each param to an adapter attribute:
     <reference attribute_category="Sender"
                attribute_id="/attrId::entitySet"
                param_key="test_entity set"/>
   The attribute_id (e.g. attrId::entitySet, attrId::edmxPath) links the
   {{param}} in the message flow to its definition.

## The rule for {{param}}
A value shown as {{name}} in the messageFlow MUST have:
  - an entry in parameters.prop (name=value), AND
  - a <parameter> def + a <param_references><reference> binding in .propdef
Otherwise CPI shows it as unconfigured / invalid.

## Adapter validation (learned from the errors)
An ODataSender requires mandatory attrs 'Entity Set' and 'EDMX'. If left blank
(even externalized but empty), CPI flags: "Attribute 'Entity Set' is mandatory".
=> Generated adapters must either set real values or externalize them with a
   default, and the consultant fills them. Mandatory attrs per adapter type
   must be known (varies by adapter).

## Manifest (from earlier analysis, confirmed required)
MANIFEST.MF needs the full OSGi block: Import-Package (fixed boilerplate,
identical across all iFlows), Import-Service, Bundle-SymbolicName; singleton,
SAP-BundleType: IntegrationFlow, SAP-NodeType: IFLMAP, SAP-RuntimeProfile: iflmap.

## Diagram section (confirmed required)
Every shape needs <bpmndi:BPMNShape> with <dc:Bounds> (x/y/w/h); every
connection a <bpmndi:BPMNEdge> with waypoints. Missing diagram = import fails.

## Generator target (the spec)
To produce a CPI-valid iFlow the generator must emit, together:
  1. Full OSGi MANIFEST (boilerplate Import-Package + bundle headers)
  2. .project
  3. The .iflw with: collaboration props, participants, process (steps with
     cmdVariantUri each), messageFlows (adapter config + cmdVariantUri),
     AND a complete BPMNDiagram (shapes + edges + bounds/waypoints)
  4. parameters.prop + parameters.propdef for externalized values
  5. Bundle all in the per-artifact zip; upload via IntegrationDesigntimeArtifacts
     into a pre-created package.

## Status: serialization fully decoded + verified. Ready to spec the generator.
