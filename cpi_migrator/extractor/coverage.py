"""extractor/coverage.py

Honest round-trip coverage measurement for the FULL-GENERATION pipeline.

We are committing to clean-room generation (no cloning of SAP-official bundles),
so the question that matters is: *of the real corpus iFlows, how many can our
generator reproduce from scratch, and which constructs block the rest?*

This module answers that WITHOUT guessing CPI activityType strings. It derives
the generator's supported vocabulary EMPIRICALLY — it builds a flow that
exercises every step kind the generator can emit, parses that output back with
our own parser, and takes the recovered kinds as the ground-truth "supported"
set. Then it scans the corpus and, per iFlow, decides reproducibility and tallies
which unsupported constructs block the most iFlows. That frequency ranking is the
build order: close the biggest gap first.

Reproducibility here is STRUCTURAL (kinds + topology), the honest floor. A step
kind being "supported" means the generator emits it and the parser recovers it;
it does not yet assert the per-step configuration is reproduced faithfully — that
is the next layer (config fidelity), measured separately as constructs are wired.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from scaffolder import minimal_iflow as _mi
from scaffolder.router_iflow import generate_router_iflow
from extractor.iflow_parser import parse_iflow

# Bounds the generator always emits (start/end events).
_BOUND_KINDS = {"StartEvent", "StartTimerEvent", "EndEvent"}


def generator_supported_kinds() -> set:
    """Empirically: build outputs exercising every kind the generator can emit,
    parse them back, and return the set of kinds our parser recovers. Self-
    consistent by construction — no hard-coded activityType strings."""
    supported: set = set(_BOUND_KINDS)

    # 1) every middle kind the linear builder knows
    mid = [{"kind": k} for k in sorted(_mi._MIDDLE_KINDS)]
    iflw, _files = _mi.build_flow_from_steps("COV", "Coverage", mid)
    for s in parse_iflow(iflw, "Coverage").steps.values():
        supported.add(s.kind)

    # 2) the router (ExclusiveGateway) — the one non-linear construct wired today
    r = generate_router_iflow("RouterCov", route_property="Type", set_value="A")
    rxml = r.iflw_xml if hasattr(r, "iflw_xml") else r
    for s in parse_iflow(rxml, "RouterCov").steps.values():
        supported.add(s.kind)

    return supported


@dataclass
class IFlowVerdict:
    name: str
    reproducible: bool
    n_steps: int
    n_processes: int
    unsupported_kinds: set = field(default_factory=set)
    reasons: list = field(default_factory=list)


def assess(model, supported: set) -> IFlowVerdict:
    """Structural reproducibility of one parsed IFlowModel against `supported`."""
    kinds = Counter(s.kind for s in model.steps.values())
    unsupported = {k for k in kinds if k not in supported}
    reasons = []
    n_proc = len(model.processes)
    # >1 process block = Local Integration Processes invoked via ProcessCall —
    # multi-process topology the linear/router builder cannot emit yet.
    multiproc = n_proc > 1
    if multiproc:
        reasons.append(f"{n_proc} process blocks (needs ProcessCall/LIP wiring)")
    if unsupported:
        reasons.append("unsupported kinds: " + ", ".join(sorted(unsupported)))
    repro = (not unsupported) and (not multiproc)
    return IFlowVerdict(
        name=getattr(model, "name", "?"), reproducible=repro,
        n_steps=len(model.steps), n_processes=n_proc,
        unsupported_kinds=unsupported, reasons=reasons)


@dataclass
class CoverageReport:
    total: int
    reproducible: int
    supported_kinds: set
    blocking_frequency: Counter            # unsupported kind -> # iFlows it blocks
    multiprocess_blocked: int              # iFlows blocked (also) by topology
    verdicts: list

    @property
    def pct(self) -> float:
        return round(100.0 * self.reproducible / self.total, 1) if self.total else 0.0


def measure_corpus(iflws, supported: set | None = None) -> CoverageReport:
    """iflws: iterable of (path, iflow_xml). Returns a CoverageReport with the
    reproducible count and a frequency-ranked list of blocking constructs."""
    if supported is None:
        supported = generator_supported_kinds()
    verdicts, blocking, multiproc_blocked, repro = [], Counter(), 0, 0
    for path, xml in iflws:
        try:
            model = parse_iflow(xml, path.rsplit("/", 1)[-1])
        except Exception:
            continue
        v = assess(model, supported)
        verdicts.append(v)
        if v.reproducible:
            repro += 1
        else:
            for k in v.unsupported_kinds:
                blocking[k] += 1
            if v.n_processes > 1:
                multiproc_blocked += 1
    return CoverageReport(
        total=len(verdicts), reproducible=repro, supported_kinds=supported,
        blocking_frequency=blocking, multiprocess_blocked=multiproc_blocked,
        verdicts=verdicts)


def format_report(rep: CoverageReport) -> str:
    lines = [
        "── FULL-GENERATION ROUND-TRIP COVERAGE ──",
        f"corpus iFlows measured : {rep.total}",
        f"reproducible today     : {rep.reproducible}/{rep.total}  ({rep.pct}%)",
        f"blocked by >1 process  : {rep.multiprocess_blocked} (ProcessCall/LIP topology)",
        "",
        f"generator supports ({len(rep.supported_kinds)} kinds): "
        + ", ".join(sorted(rep.supported_kinds)),
        "",
        "NEXT CONSTRUCTS, ranked by # of iFlows each unblocks:",
    ]
    for kind, n in rep.blocking_frequency.most_common():
        lines.append(f"  {n:3d}  {kind}")
    return "\n".join(lines)


def _blockers(v: IFlowVerdict) -> set:
    """The full set of features an iFlow needs before it is reproducible.
    Multi-process topology is folded into ProcessCallElement: you cannot emit >1
    process block without the ProcessCall construct that invokes them."""
    b = set(v.unsupported_kinds)
    if v.n_processes > 1:
        b.add("ProcessCallElement")
    return b


def greedy_unlock_order(report: CoverageReport):
    """Greedy: repeatedly pick the construct that makes the MOST currently-blocked
    iFlows fully reproducible, reporting the coverage curve. This is the true
    build priority — unlike raw frequency it accounts for co-occurrence."""
    remaining = [_blockers(v) for v in report.verdicts if not v.reproducible]
    supported_now: set = set()
    total = report.total
    curve = []
    while remaining:
        # how many iFlows does adding each still-needed feature FULLY unlock?
        solo = Counter()
        for b in remaining:
            need = b - supported_now
            if len(need) == 1:
                solo[next(iter(need))] += 1
        if solo:
            pick, gained = solo.most_common(1)[0]
        else:
            # nothing unlocks solo yet — pick the most-needed feature to chip away
            leftover = Counter()
            for b in remaining:
                for f in (b - supported_now):
                    leftover[f] += 1
            if not leftover:
                break
            pick, _ = leftover.most_common(1)[0]
            gained = 0
        supported_now.add(pick)
        remaining = [b for b in remaining if (b - supported_now)]
        now_repro = total - len(remaining)
        curve.append((pick, gained, round(100.0 * now_repro / total, 1)))
    return curve


def format_curve(report: CoverageReport) -> str:
    lines = ["", "GREEDY BUILD ORDER (coverage as each construct lands):",
             f"  start: {report.reproducible}/{report.total} ({report.pct}%)"]
    for pick, gained, pct in greedy_unlock_order(report):
        tag = f"+{gained:>2} iFlows" if gained else "enables combos"
        lines.append(f"  + {pick:<28} {tag:<14} → {pct}%")
    return "\n".join(lines)


def true_roundtrip(iflws):
    """The real test: for each corpus iFlow, parse → generate_from_model →
    reparse → compare the middle-step kind sequence. Returns (attempted, matched,
    mismatches, skipped); skipped = iFlows with constructs not yet emittable
    (counted honestly, not as a stub)."""
    from scaffolder.model_generator import (generate_from_model,
                                            UnsupportedConstruct)
    attempted = matched = skipped = 0
    mismatches = []
    mids = lambda m: [m.steps[s].kind for s in m.sequence
                      if m.steps[s].kind not in _BOUND_KINDS]
    for path, xml in iflws:
        nm = path.rsplit("/", 1)[-1]
        try:
            m1 = parse_iflow(xml, nm)
        except Exception:
            continue
        try:
            res = generate_from_model(m1, name=nm)
        except UnsupportedConstruct:
            skipped += 1
            continue
        except Exception as exc:
            attempted += 1
            mismatches.append((nm, f"generate error: {exc}"))
            continue
        attempted += 1
        try:
            m2 = parse_iflow(res.iflw_xml, nm)
        except Exception as exc:
            mismatches.append((nm, f"reparse error: {exc}"))
            continue
        if mids(m1) == mids(m2):
            matched += 1
        else:
            mismatches.append((nm, f"{mids(m1)} != {mids(m2)}"))
    return attempted, matched, mismatches, skipped


if __name__ == "__main__":   # pragma: no cover
    import pickle
    import sys
    pkl = sys.argv[1] if len(sys.argv) > 1 else "/tmp/learn/iflws.pkl"
    iflws = pickle.load(open(pkl, "rb"))
    rep = measure_corpus(iflws)
    print(format_report(rep))
    print(format_curve(rep))
    att, ok, mism, skip = true_roundtrip(iflws)
    print("\n── TRUE ROUND-TRIP (parse → generate → reparse → compare) ──")
    print(f"attempted (linear+supported): {att}")
    print(f"kind-sequence MATCHED        : {ok}/{att}")
    print(f"skipped (unsupported yet)    : {skip}")
    if mism:
        print(f"mismatches ({len(mism)}):")
        for nm, why in mism[:8]:
            print(f"   {nm}: {why}")
