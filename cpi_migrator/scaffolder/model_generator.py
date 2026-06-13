"""scaffolder/model_generator.py

The round-trip backbone: turn a parsed `IFlowModel` (from extractor.iflow_parser)
back into a deployable .iflw, from scratch. This is what makes full generation
real — the parser understands an existing iFlow, and this regenerates it with no
SAP-official bytes carried over.

Scope today: a single integration process whose steps are all in the generator's
supported vocabulary (the 14 kinds the coverage harness derives). Anything beyond
that — multi-process (ProcessCall/LIP), gateways, request-reply (ExternalCall),
exception subprocesses — raises UnsupportedConstruct so the caller can fall back
and so the coverage harness counts it honestly as not-yet-reproducible rather
than emitting a silent stub. Constructs are added here one at a time, each decoded
from the real corpus and each raising measured coverage.
"""
from __future__ import annotations

from scaffolder import minimal_iflow as _mi

# parser activityType  ->  generator's internal step token
PARSER_TO_GEN = {
    "Enricher":           "content_modifier",
    "Script":             "script",
    "Mapping":            "mapping",
    "Splitter":           "splitter",
    "Gather":             "gather",
    "Filter":             "filter",
    "XmlToJsonConverter": "xml_to_json",
    "JsonToXmlConverter": "json_to_xml",
    "XmlToCsvConverter":  "xml_to_csv",
    "CsvToXmlConverter":  "csv_to_xml",
    "ExternalCall":       "external_call",
    "ProcessCallElement": "process_call",
}
# single-step constructs that round-trip 1:1 (gen-kind == parser activityType)
for _k in ("Encoder", "Decoder", "DBstorage", "XMLDigitalSignMessage",
           "SimpleSignMessage", "Send", "Variables", "XmlModifier",
           "contentEnricherWithLookup", "Persist", "XmlValidator"):
    PARSER_TO_GEN[_k] = _k
BOUND_KINDS = {"StartEvent", "StartTimerEvent", "EndEvent"}


class UnsupportedConstruct(Exception):
    """Raised when the model contains something the generator can't emit yet.
    The message names the specific blocker so fallbacks and reports are honest."""


def model_to_mid_specs(model) -> list:
    """Translate a single-process model's ordered steps into the linear builder's
    mid-spec list, carrying the extracted config through so the regenerated step
    keeps its real reference/shape — not just its kind. Per-kind config the
    builders accept today: script (file + function), mapping (name), splitter/
    filter (xpath). Deeper config (CM property/header tables) is the next layer."""
    specs = []
    for sid in model.sequence:
        s = model.steps.get(sid)
        if s is None or s.kind in BOUND_KINDS:
            continue
        gen = PARSER_TO_GEN.get(s.kind)
        if gen is None:
            raise UnsupportedConstruct(s.kind)
        spec = {"kind": gen, "name": s.name or gen}
        cfg = s.config or {}
        if gen == "script":
            if cfg.get("script"):
                spec["script_file"] = cfg["script"]
            if cfg.get("scriptFunction"):
                spec["function"] = cfg["scriptFunction"]
        elif gen in ("mapping", "xslt_to_csv", "xslt_to_json"):
            if cfg.get("mappingname") or cfg.get("mappingName"):
                spec["mapping_name"] = cfg.get("mappingname") or cfg.get("mappingName")
        elif gen in ("splitter", "filter", "xml_to_csv"):
            if cfg.get("xpath"):
                spec["xpath"] = cfg["xpath"]
            # Carry the real step config verbatim when present, so the genuine
            # wrapContent/xpathType (e.g. /p2:SetDTE + Node) survive instead of
            # the synthetic /* + Nodelist default that trips CPI's content-type
            # check ("Filter may not pass XML message ... supports XML input only").
            if cfg and gen in ("filter", "splitter"):
                spec["config"] = cfg
        elif gen == "external_call":
            if cfg.get("receiver_name"):
                spec["receiver_name"] = cfg["receiver_name"]
            if cfg.get("address"):
                spec["address"] = cfg["address"]
            if cfg.get("mf_props"):
                spec["mf_props"] = cfg["mf_props"]
            if cfg.get("mf_name"):
                spec["mf_name"] = cfg["mf_name"]
        elif gen == "process_call":
            # Carry processId / cmdVariantUri / subActivityType verbatim so the
            # ProcessCall re-emits faithfully and targets the real local process.
            spec["config"] = cfg
        elif cfg and gen in _mi._PASSTHROUGH:
            # Re-emit the real step's captured properties verbatim (privateKeyAlias,
            # signatureAlgorithm, transformMethod, ...). Without this the step is a
            # hollow shell and CPI rejects it ("X is not specified").
            spec["config"] = cfg
        specs.append(spec)
    return specs


def _input_schema_ref(model):
    """First input-schema reference a flow declares (schemaResourceUri / xsd /
    wsdl). Returns None for pure passthroughs that declare no schema."""
    for s in getattr(model, "steps", {}).values():
        cfg = getattr(s, "config", None) or {}
        for k in ("schemaResourceUri", "xsdName", "messageSchema", "wsdlURL"):
            if cfg.get(k):
                return cfg[k]
    return None


def _real_body_from_model(model):
    """If the source flow itself carries a real, literal payload body (a
    constant Content Modifier body — an actual message instance, not a ${...}
    expression or a path), reuse THAT instead of synthesizing. Real beats
    synthetic whenever the corpus actually has it."""
    for s in getattr(model, "steps", {}).values():
        cfg = getattr(s, "config", None) or {}
        b = (cfg.get("wrapContent") or "").strip()
        if b and "${" not in b and (b.startswith("<") or b.startswith("{")) \
                and len(b) > 30:
            return b
    return None


def mock_specs_from_model(model, corpus: dict | None = None) -> list:
    """Build the deployable mock-I/O scaffold: a content modifier (sample
    payload) + a request-reply to the flow's real receiver (address kept and
    externalized so it can be bound for a real run). The sender is replaced by
    the generator's default timer start, so the flow SELF-TRIGGERS and can be
    deployed/tested on the tenant without the client's inbound system.

    Payload priority — real beats synthetic: (1) a real literal body the flow
    already carries; (2) a schema-derived sample, if the flow declares a schema
    and a corpus is supplied to resolve it; (3) a generic stub. Synthesis is
    only the fallback where the corpus has no real instance to reuse."""
    cm = {"kind": "content_modifier", "name": "Set Sample Payload"}
    body = _real_body_from_model(model)
    if not body:
        ref = _input_schema_ref(model)
        if ref and corpus:
            try:
                from scaffolder.resource_resolver import resolve
                from scaffolder.sample_payload import sample_payload_from_xsd
                res = resolve(ref, corpus, kind="schema",
                              package=getattr(model, "source_package", None))
                if res.ok and res.content:
                    body = sample_payload_from_xsd(res.content)
            except Exception:
                pass
    if body:
        cm["body"] = body
    specs = [cm]
    recv = [e for e in getattr(model, "endpoints", [])
            if getattr(e, "direction", "") == "receiver"]
    if recv:
        r = recv[0]
        specs.append({"kind": "external_call", "name": "Request Reply",
                      "receiver_name": getattr(r, "name", None) or "Receiver",
                      "address": getattr(r, "address", None) or "{{MOCK_ENDPOINT}}"})
    else:
        specs.append({"kind": "external_call",
                      "name": "Request Reply (mock receiver)",
                      "receiver_name": "MockReceiver", "address": "{{MOCK_ENDPOINT}}"})
    return specs


def generate_mock_from_model(model, iflow_id: str = "", name: str = "",
                             corpus: dict | None = None):
    """Deployable mock SUBSTITUTION (timer -> content modifier -> request-reply)
    for flows whose real sender/receiver can't be faithfully reproduced (their
    binding lives in the client tenant, not the artifact).

    This is NOT a faithful reproduction — it's a deliberate, self-triggering
    'signature' scaffold that makes the flow testable. Callers MUST track these
    separately from faithful reproductions; never count a mock as a faithful
    round-trip."""
    name = name or model.name
    iflow_id = _mi._sanitize_id(iflow_id or name)
    specs = mock_specs_from_model(model, corpus=corpus)
    iflw, extra_files = _mi.build_flow_from_steps(iflow_id, name, specs)
    manifest = _mi.build_manifest(iflow_id, name)
    project = _mi.build_project(iflow_id)
    files = {
        "META-INF/MANIFEST.MF": manifest,
        ".project": project,
        f"src/main/resources/scenarioflows/integrationflow/{iflow_id}.iflw": iflw,
    }
    files.update(extra_files or {})
    return _mi.MinimalIFlowResult(
        iflow_id=iflow_id, name=name, iflw_xml=iflw,
        manifest=manifest, project_xml=project, files=files)


def _package_files(iflow_id, name, iflw):
    manifest = _mi.build_manifest(iflow_id, name)
    project = _mi.build_project(iflow_id)
    files = {
        "META-INF/MANIFEST.MF": manifest,
        ".project": project,
        f"src/main/resources/scenarioflows/integrationflow/{iflow_id}.iflw": iflw,
    }
    return manifest, project, files


def generate_multiprocess(model, iflow_id: str = "", name: str = ""):
    """Emit a multi-process flow honestly: the MAIN process PLUS the called Local
    Integration Processes with their real steps. The main process is built with
    the linear builder when it is linear+emittable (proven path), otherwise with
    the full-graph emitter (gateway in main, or a kind the linear path can't
    emit) so a branching main also round-trips."""
    name = name or model.name
    iflow_id = _mi._sanitize_id(iflow_id or name)
    main = [p for p in model.processes if getattr(p, "is_main", False)]
    main_ids = set(main[0].step_ids) if main else set(model.sequence)
    main_has_route = any(
        getattr(rt, "source", "") in main_ids or getattr(rt, "target", "") in main_ids
        for rt in (getattr(model, "routes", []) or []))

    iflw = extra_files = None
    if not main_has_route and not model.endpoints:
        try:
            specs = model_to_mid_specs(model)        # raises on unemittable main kind
            iflw, extra_files = _mi.build_flow_from_steps(iflow_id, name, specs)
            iflw = _mi.apply_collab_config(iflw,
                                           getattr(model, "collab_config", None))
            iflw = _mi.apply_timer_start(iflw, model)
        except UnsupportedConstruct:
            iflw = None
    if iflw is None:
        # gateway in main, an unemittable kind, OR endpoints to carry → full graph
        iflw, extra_files = _mi.build_gateway_flow(iflow_id, name, model)

    iflw = _mi.inject_local_processes(iflw, model)
    manifest, project, files = _package_files(iflow_id, name, iflw)
    files.update(extra_files or {})
    return _mi.MinimalIFlowResult(
        iflow_id=iflow_id, name=name, iflw_xml=iflw,
        manifest=manifest, project_xml=project, files=files)


def generate_gateway(model, iflow_id: str = "", name: str = ""):
    """Single-process flow containing an ExclusiveGateway: emit the full
    step+flow graph verbatim (branches, conditions) so it round-trips."""
    name = name or model.name
    iflow_id = _mi._sanitize_id(iflow_id or name)
    iflw, extra_files = _mi.build_gateway_flow(iflow_id, name, model)
    manifest, project, files = _package_files(iflow_id, name, iflw)
    files.update(extra_files or {})
    return _mi.MinimalIFlowResult(
        iflow_id=iflow_id, name=name, iflw_xml=iflw,
        manifest=manifest, project_xml=project, files=files)


def generate_from_model(model, iflow_id: str = "", name: str = "",
                        resources: dict | None = None,
                        package: str | None = None):
    """IFlowModel -> MinimalIFlowResult (bundle files + iflw_xml), built clean.
    Raises UnsupportedConstruct for topologies/constructs not yet handled.

    If `resources` ({corpus_path: content}, e.g. from walk_corpus over the
    original package exports) is supplied, the real files each step references
    (scripts, mappings) are resolved and shipped in the bundle, and the result
    carries a `.resource_report`. Without it the bundle is unchanged."""
    if len(model.processes) > 1:
        result = generate_multiprocess(model, iflow_id, name)
    elif getattr(model, "routes", None):
        result = generate_gateway(model, iflow_id, name)
    elif any(getattr(s, "parent_subprocess", "") for s in model.steps.values()):
        # a real exception/event subprocess nests handler children → reconstruct
        # the full graph (which emits the subProcess with its children).
        result = generate_gateway(model, iflow_id, name)
    elif model.endpoints:
        # any single-process flow with sender/receiver endpoints: the graph
        # emitter ships the real participants + message flows (adapter config),
        # so the bundle is deployable, not just structurally faithful. (Linear
        # flows with no endpoints keep the proven linear builder below.)
        result = generate_gateway(model, iflow_id, name)
    else:
        name2 = name or model.name
        iflow_id2 = _mi._sanitize_id(iflow_id or name2)
        specs = model_to_mid_specs(model)        # raises on a genuinely unemittable kind
        iflw, extra_files = _mi.build_flow_from_steps(iflow_id2, name2, specs)
        iflw = _mi.apply_collab_config(iflw, getattr(model, "collab_config", None))
        iflw = _mi.apply_timer_start(iflw, model)
        manifest, project, files = _package_files(iflow_id2, name2, iflw)
        files.update(extra_files or {})
        result = _mi.MinimalIFlowResult(
            iflow_id=iflow_id2, name=name2, iflw_xml=iflw,
            manifest=manifest, project_xml=project, files=files)

    # iFlow-level carry: externalized-parameter files (real exports always ship
    # the pair; entries derived from the flow's {{param}} set) + metainfo.prop
    # (real exports ship it at bundle root).
    result.files.update(_mi.emit_parameter_files(
        getattr(model, "parameters", None)))
    result.iflw_xml = _mi.repair_flow_di(result.iflw_xml)
    result.files.setdefault("metainfo.prop",
                            "#Store metainfo properties\ndescription=\n")
    if resources:
        from scaffolder.resource_attach import attach_resources
        rep = attach_resources(model, resources, package=package)
        result.files.update(rep.shipped)
        result.resource_report = rep
        # the ORIGINAL parameter files (with the configured values) beat the
        # synthesized empty-value pair when the source package provides them.
        # Prefer the source iflw's OWN bundle (every iFlow bundle has its own
        # parameters.prop, so package scoping alone is ambiguous); fall back to
        # package-scoped resolution.
        from scaffolder.resource_resolver import resolve
        bundle = getattr(model, "source_bundle", None)
        for fname, bpath in (("parameters.prop",
                              "src/main/resources/parameters.prop"),
                             ("parameters.propdef",
                              "src/main/resources/parameters.propdef")):
            if bundle:
                direct = resources.get(f"{bundle}::{bpath}")
                if direct is None:
                    direct = resources.get(f"{bundle}::{fname}")
                if direct is not None:
                    result.files[bpath] = direct
                    continue
            r = resolve(fname, resources, package=package)
            if r.ok and not r.ambiguous:
                result.files[bpath] = r.content
        # References-tab parity: when the source iflw's OWN bundle is known,
        # ship its ENTIRE src/main/resources sibling set (scripts, mappings,
        # schemas) — not only the step-referenced files. Real bundles carry
        # helper files no step config names (utility groovy, superseded XSLT,
        # mmap-referenced XSDs), and the editor's References tab lists the
        # folder contents; reference-driven attach alone leaves those gaps
        # (seen on RCI093: 18 shipped vs 23 in the original). Step-resolved
        # content keeps precedence; the iflw itself and the parameter pair are
        # handled above.
        if bundle:
            _skip_tail = ("/parameters.prop", "/parameters.propdef")
            _res_prefix = f"{bundle}::src/main/resources/"
            added = 0
            for ckey, content in resources.items():
                if not ckey.startswith(_res_prefix):
                    continue
                rel = ckey.split("::", 1)[1]
                if rel.endswith(_skip_tail) or rel.endswith(".iflw") \
                        or "/scenarioflows/" in rel:
                    continue
                # the bundle's OWN copy overrides a basename match resolved
                # from a sibling bundle (same package, two bundles, same EDMX
                # basename, different content — decoded on RCI093)
                if rel not in result.files:
                    added += 1
                result.files[rel] = content
            if added and result.resource_report is not None:
                result.resource_report.resolved.append(
                    ("(bundle)", f"+{added} sibling resource file(s)", bundle))
    return result
