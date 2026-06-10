#!/usr/bin/env python3
"""repro_groups.py — measure structural reproduce rate across a FROZEN,
stratified train/holdout split, so generator changes can be checked for
generalization instead of overfitting.

Method
------
- base-build       = the development set. Inspect these failures, build the fix.
- comparison-1/2/3 = held out. Measure only. A *real* (generalizing) change
  lifts all four groups similarly; a change that lifts base-build but not the
  comparison groups is overfitting.

The split is STRATIFIED by each flow's primary blocker (so every group has the
same construct mix) and then FROZEN to tools/repro_split.json — once written, a
flow never changes group, even as the generator improves. Delete the json to
re-draw the split (only do this deliberately; it invalidates past comparisons).

Usage:  python3 tools/repro_groups.py [--corpus /path/to/iflws.pkl] [--verbose]
"""
from __future__ import annotations
import argparse
import json
import os
import pickle
import sys
from collections import Counter, defaultdict

GROUPS = ["base-build", "comparison-1", "comparison-2", "comparison-3"]
# Primary-blocker priority: a flow is stratified by its single most-blocking
# construct, so the dominant driver (multi-process) is spread evenly.
_PRIORITY = [
    ("multi-process", ("multi-process", "ProcessCall", "Local Integration")),
    ("gateway", ("ExclusiveGateway", "Gateway", "routing")),
    ("exception-subprocess", ("ErrorEventSubProcess", "exception")),
    ("endpoints", ("sender/receiver", "passthrough", "main")),
]


def _stratum(reproduced: bool, blockers) -> str:
    if reproduced:
        return "reproduced"
    text = " ; ".join(str(b) for b in (blockers or []))
    for name, needles in _PRIORITY:
        if any(n.lower() in text.lower() for n in needles):
            return name
    return "other"


def _load(corpus_path: str):
    with open(corpus_path, "rb") as fh:
        return pickle.load(fh)


def _split(iflws, split_path: str):
    """Return {path: group}. Load frozen split if present; else stratify,
    round-robin within each stratum, and freeze."""
    if os.path.exists(split_path):
        with open(split_path) as fh:
            return json.load(fh)
    # need current blockers to stratify
    from extractor.iflow_parser import parse_iflow  # noqa
    from scaffolder.regenerate import regenerate_iflow_xml
    strata = defaultdict(list)
    for path, xml in iflws:
        r = regenerate_iflow_xml(xml, path.rsplit("/", 1)[-1])
        strata[_stratum(r.reproduced, getattr(r, "blockers", []))].append(path)
    assign = {}
    for stratum, paths in strata.items():
        for i, p in enumerate(sorted(paths)):       # deterministic round-robin
            assign[p] = GROUPS[i % 4]
    with open(split_path, "w") as fh:
        json.dump(assign, fh, indent=0, sort_keys=True)
    return assign


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="/tmp/learn/iflws.pkl")
    ap.add_argument("--split", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "repro_split.json"))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    if not os.path.exists(args.corpus):
        print(f"corpus not found: {args.corpus}")
        return 2
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scaffolder.regenerate import regenerate_iflow_xml

    iflws = _load(args.corpus)
    assign = _split(iflws, args.split)
    froze = "loaded frozen" if os.path.exists(args.split) else "created"

    per = {g: {"n": 0, "repro": 0, "mock": 0, "blk": Counter()} for g in GROUPS}
    for path, xml in iflws:
        g = assign.get(path)
        if g is None:                 # new flow not in frozen split
            continue
        r = regenerate_iflow_xml(xml, path.rsplit("/", 1)[-1])
        per[g]["n"] += 1
        if r.reproduced:
            per[g]["repro"] += 1
        else:
            blks = getattr(r, "blockers", []) or ["(unknown)"]
            for b in blks:
                per[g]["blk"][str(b).split("(")[0].strip()[:30]] += 1
            # endpoint-only flows can't be faithfully reproduced, but a mock
            # scaffold (timer->CM->request-reply) makes them deployable/testable.
            # Counted SEPARATELY — never folded into faithful reproduction.
            if any("endpoint" in str(b).lower() or "passthrough" in str(b).lower()
                   for b in blks):
                try:
                    from extractor.iflow_parser import parse_iflow
                    from scaffolder.model_generator import generate_mock_from_model
                    res = generate_mock_from_model(
                        parse_iflow(xml, path.rsplit("/", 1)[-1]))
                    if "timerEventDefinition" in res.iflw_xml:
                        per[g]["mock"] += 1
                except Exception:
                    pass

    print(f"split: {froze} ({args.split})\n")
    print(f"{'group':14} {'n':>4} {'faithful':>9} {'+mock':>6} {'handled':>8}   top blockers")
    tn = tr = tm = 0
    for g in GROUPS:
        d = per[g]
        tn += d["n"]; tr += d["repro"]; tm += d["mock"]
        handled = d["repro"] + d["mock"]
        rate = handled / d["n"] * 100 if d["n"] else 0
        top = ", ".join(f"{k}×{v}" for k, v in d["blk"].most_common(3))
        print(f"{g:14} {d['n']:>4} {d['repro']:>9} {d['mock']:>6} "
              f"{handled:>5} {rate:>4.0f}%   {top}")
    print(f"{'TOTAL':14} {tn:>4} {tr:>9} {tm:>6} {tr+tm:>5} "
          f"{(tr+tm)/tn*100:>4.0f}%")
    print(f"\nfaithful reproduction: {tr}/{tn} ({tr/tn*100:.1f}%)  |  "
          f"+ mock-deployable (endpoint-only, testable, NOT faithful): {tm}  |  "
          f"handled: {tr+tm}/{tn} ({(tr+tm)/tn*100:.1f}%)")
    if args.verbose:
        print("\nper-group blocker detail:")
        for g in GROUPS:
            print(f"  {g}: " + ", ".join(
                f"{k}×{v}" for k, v in per[g]["blk"].most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
