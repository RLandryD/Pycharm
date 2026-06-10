"""scaffolder/regenerate.py

The CPI → intermediate-model → CPI regeneration path. This is the honest engine
behind "upload a CPI package and get a clean-room regeneration": for each .iflw
it parses the real structure + config, regenerates from scratch, and reports
exactly what it could and could NOT reproduce — never a silent stub.

It is deliberately separate from the PI/PO assessment pipeline (which sizes
effort from interface metadata). That pipeline never carried the source iFlow, so
it could only emit a placeholder. This path takes the real .iflw as input.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from extractor.iflow_parser import parse_iflow
from scaffolder.model_generator import generate_from_model, UnsupportedConstruct

_BOUNDS = {"StartEvent", "StartTimerEvent", "EndEvent"}
_IFLW_SUFFIX = ".iflw"


@dataclass
class RegenResult:
    name: str
    reproduced: bool                 # generated AND re-parses to the same kinds
    n_steps: int = 0                 # middle steps in the source
    result = None                    # MinimalIFlowResult when reproduced
    blockers: list = field(default_factory=list)   # why not (honest)
    note: str = ""


def regenerate_iflow_xml(xml: str, name: str = "iflow",
                         resources: dict | None = None,
                         package: str | None = None) -> RegenResult:
    """Parse → generate_from_model → reparse → verify. Honest on failure.

    `resources`/`package` are forwarded to the generator so a deploy bundle
    carries the real referenced files; the measurement path leaves them None
    (reproduce is structural and resource-independent)."""
    try:
        m1 = parse_iflow(xml, name)
    except Exception as exc:
        return RegenResult(name, False, note=f"parse error: {exc}")
    mids = lambda m: [m.steps[s].kind for s in m.sequence
                      if m.steps[s].kind not in _BOUNDS]
    n_steps = len(mids(m1))
    # bundle-scope hint: locate THIS source iflw inside the corpus so its OWN
    # parameter files (real configured values) can be shipped — package-level
    # scoping alone is ambiguous (one parameters.prop per iFlow bundle).
    if resources:
        stripped = xml.strip()
        for _p, _c in resources.items():
            if _p.endswith(".iflw") and isinstance(_c, str) \
                    and _c.strip() == stripped:
                m1.source_bundle = _p.split("::", 1)[0]
                break
    try:
        res = generate_from_model(m1, name=name, resources=resources,
                                  package=package)
    except UnsupportedConstruct as exc:
        return RegenResult(name, False, n_steps=n_steps, blockers=[str(exc)],
                           note="not yet emittable")
    except Exception as exc:
        return RegenResult(name, False, n_steps=n_steps,
                           note=f"generate error: {exc}")
    try:
        m2 = parse_iflow(res.iflw_xml, name)
    except Exception as exc:
        return RegenResult(name, False, n_steps=n_steps,
                           note=f"reparse error: {exc}")
    ok = mids(m1) == mids(m2)
    # A 0-middle-step flow whose real content is its sender/receiver endpoints
    # regenerates as a bare timer with those endpoints dropped — that is an
    # EMPTY flow, not a faithful reproduction. Report it honestly instead of
    # claiming success on what deploys as Start→End.
    if ok and n_steps == 0:
        src_eps = [e for e in m1.endpoints if e.direction in ("sender", "receiver")]
        gen_eps = [e for e in m2.endpoints if e.direction in ("sender", "receiver")]
        if src_eps and not gen_eps:
            return RegenResult(
                name, False, n_steps=n_steps,
                blockers=["sender/receiver passthrough (main endpoint "
                          "reproduction not yet supported)"],
                note="source is an endpoint-only passthrough; not yet emittable")
    r = RegenResult(name, ok, n_steps=n_steps,
                    note="" if ok else f"{mids(m1)} != {mids(m2)}")
    if ok:
        r.result = res
    return r


def regenerate_package(files: dict) -> list:
    """files: {path: text} (e.g. from walk_corpus_bytes). Regenerate every .iflw
    found; return one RegenResult each."""
    out = []
    for path, text in (files or {}).items():
        if path.endswith(_IFLW_SUFFIX) and isinstance(text, str) \
                and "<bpmn2:" in text:
            out.append(regenerate_iflow_xml(text, path.rsplit("/", 1)[-1]))
    return out


def summarize(results: list) -> str:
    n = len(results)
    ok = sum(1 for r in results if r.reproduced)
    lines = [f"regenerated {ok}/{n} iFlow(s) fully from scratch:"]
    for r in results:
        if r.reproduced:
            lines.append(f"  ✓ {r.name}  ({r.n_steps} steps)")
        else:
            why = ", ".join(r.blockers) or r.note or "unknown"
            lines.append(f"  ✗ {r.name}  — blocked by: {why}")
    return "\n".join(lines)
