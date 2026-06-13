"""tools/pattern_mining.py — topology-level pattern mining over the corpus.

Classifies every corpus iFlow into a named structural family (the
"conversion vocabulary"): which architectural shapes exist in the wild, at
what frequency, with which orthogonal traits (exception handling, signing,
persistence). Output drives two things:

  1. PI/PO converter priorities — crack the most frequent shapes first.
  2. The client slide — "your landscape decomposes into N shapes".

This is a different altitude from the wiring/capability mining the corpus
pipeline already does (which answers "how do I rebuild THIS flow"); the
miner answers "what shapes exist ACROSS flows".

Usage: python3 tools/pattern_mining.py [--corpus /tmp/learn/iflws.pkl]
                                       [--json out.json] [--verbose]
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_EVENT_KINDS = {"StartEvent", "EndEvent", "StartTimerEvent", "TerminateEvent",
                "StartErrorEvent", "EndErrorEvent", "EscalationEndEvent"}

# Orthogonal traits — counted independently of the family
_TRAITS = {
    "exception_subprocess": ("ErrorEventSubProcessTemplate",),
    "digital_signing": ("XMLDigitalSignMessage", "XMLDigitalVerifyMessage",
                        "SignMessage", "VerifyMessage"),
    "persistence": ("DBstorage", "Persist", "WriteVariables", "Variables"),
    "format_conversion": ("JsonToXmlConverter", "XmlToJsonConverter",
                          "Encoder", "Decoder", "XmlModifier", "Filter"),
    "timer_driven": ("StartTimerEvent",),
}


def classify(model) -> tuple:
    """(family, fingerprint, traits) for one parsed iFlow model."""
    kinds = collections.Counter(s.kind for s in model.steps.values())
    body = {k: c for k, c in kinds.items() if k not in _EVENT_KINDS}
    n_proc = len(getattr(model, "processes", []) or [])

    traits = sorted(t for t, marks in _TRAITS.items()
                    if any(kinds.get(m) for m in marks))

    def n(*ks):
        return sum(kinds.get(k, 0) for k in ks)

    if not body:
        family = "Pure passthrough / proxy"
    elif n("Splitter") and n("Gather", "Join"):
        family = "Splitter–Gather orchestration"
    elif n("Splitter"):
        family = "Splitter fan-out"
    elif n("Multicast", "SequentialMulticast"):
        family = "Multicast fan-out"
    elif n("ExclusiveGateway") >= 2 and n("ProcessCallElement") >= 2:
        family = "Routed multi-process orchestration"
    elif n("ExclusiveGateway"):
        family = "Content-based routing"
    elif n("ProcessCallElement") >= 3 or n_proc >= 4:
        family = "Multi-process orchestration"
    elif n("Mapping") and n("ExternalCall"):
        family = "Map, call & deliver"
    elif n("Mapping"):
        family = "Map & deliver"
    elif n("ExternalCall"):
        family = "Sync relay / API proxy"
    elif n("Script", "Enricher"):
        family = "Script/enrich pipeline"
    else:
        family = "Other / mixed"

    fp_src = json.dumps({"k": sorted(body.items()), "p": n_proc},
                        sort_keys=True)
    fingerprint = hashlib.sha256(fp_src.encode()).hexdigest()[:16]
    return family, fingerprint, traits


def mine(corpus: list, verbose: bool = False) -> dict:
    from extractor.iflow_parser import parse_iflow
    fam = collections.Counter()
    fam_examples = collections.defaultdict(list)
    fam_traits = collections.defaultdict(collections.Counter)
    fingerprints = collections.defaultdict(set)
    trait_totals = collections.Counter()
    errors = 0
    for path, xml in corpus:
        try:
            m = parse_iflow(xml, path.rsplit("/", 1)[-1])
        except Exception:
            errors += 1
            continue
        family, fp, traits = classify(m)
        fam[family] += 1
        fingerprints[family].add(fp)
        trait_totals.update(traits)
        fam_traits[family].update(traits)
        if len(fam_examples[family]) < 3:
            fam_examples[family].append(path)
    total = sum(fam.values())
    report = {
        "total_flows": total,
        "parse_errors": errors,
        "families": [
            {"family": f, "count": c, "pct": round(100 * c / total, 1),
             "distinct_shapes": len(fingerprints[f]),
             "traits": dict(fam_traits[f].most_common()),
             "examples": fam_examples[f]}
            for f, c in fam.most_common()],
        "trait_totals": dict(trait_totals.most_common()),
    }
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="/tmp/learn/iflws.pkl")
    ap.add_argument("--json", default="")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    if not os.path.exists(args.corpus):
        print(f"corpus not found: {args.corpus}")
        return 2
    with open(args.corpus, "rb") as fh:
        corpus = pickle.load(fh)
    report = mine(corpus, verbose=args.verbose)
    print(f"{report['total_flows']} flows → "
          f"{len(report['families'])} families "
          f"({report['parse_errors']} parse errors)\n")
    for f in report["families"]:
        print(f"  {f['count']:>4}  {f['pct']:>5}%  {f['family']:36} "
              f"({f['distinct_shapes']} distinct shapes)")
    print("\ntraits across all flows:")
    for t, c in report["trait_totals"].items():
        print(f"  {c:>4}  {t}")
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=1)
        print(f"\nreport → {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
