"""tools/script_lint.py — the conversion linter.

Checks a Groovy script's API usage against the three-layer reference:
  (a) SAP CPI script API     reference/cpi_script_api.json (curated)
  (b) Groovy GDK             reference/groovy_gdk.json (extracted from
                             Apache source at GROOVY_2_4_21 / GROOVY_4_0_29)
  (c) Java stdlib            java./javax. imports pass as "stdlib"
                             (presence acknowledged, not method-checked)

Pinned matrix (2026-06-12): CPI runtime = Groovy 4.0.29; legacy scripts
authored for 2.4.x. The linter answers two questions per script:
  1. does every call exist SOMEWHERE in the reference?  (typo/AI-slop gate)
  2. does it run on BOTH versions, or is it 2.4-only / 4.0-only?
     (the migration question — e.g. groovy.util.XmlSlurper imports are
     REMOVED on the 4.0 runtime.)

Honest limits: Groovy is dynamic; we extract call NAMES, not types. A name
that exists on any receiver passes. This catches the killers (removed
classes, version-gated methods, hallucinated APIs) but is not a type check.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GDK_PATH = os.path.join(_HERE, "reference", "groovy_gdk.json")
SAP_PATH = os.path.join(_HERE, "reference", "cpi_script_api.json")

_IMPORT_RX = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+(?:\.\*)?)",
                        re.M)
_CALL_RX = re.compile(r"\.\s*(\w+)\s*\(")
_BARE_CALL_RX = re.compile(r"(?<![\w.])(\w+)\s*\(")

#: Groovy CORE-CLASS methods (XmlSlurper/JsonSlurper/builders) — these are
#: instance methods of groovy.* classes, not GDK extension methods, present
#: in both 2.4 and 4.0 (only the PACKAGE of the XML classes moved).
_CORE_CLASS_METHODS = {"parseText", "parseFile", "toJson", "prettyPrint",
                       "toPrettyString", "serialize", "breadthFirst",
                       "depthFirst", "children", "attributes",
                       "declareNamespace", "appendNode", "replaceNode",
                       "bind", "mkp", "yield"}

#: object-protocol + Groovy syntax names never worth flagging
_NOISE = {"if", "for", "while", "switch", "catch", "return", "new", "super",
          "this", "assert", "println", "print", "sprintf", "printf",
          "equals", "hashCode", "toString", "getClass", "size", "get", "put",
          "add", "remove", "contains", "containsKey", "containsValue",
          "keySet", "values", "entrySet", "length", "charAt", "substring",
          "indexOf", "lastIndexOf", "trim", "isEmpty", "iterator", "next",
          "hasNext", "close", "valueOf", "parse", "format", "append",
          "matcher", "matches", "group", "compile", "name", "text", "value",
          "key", "wait", "notify", "clone", "compareTo", "startsWith",
          "endsWith", "replace", "concat", "instanceof", "def", "byte",
          "getProperty", "setProperty", "getAt", "putAt", "call", "min",
          "max", "abs", "getBytes", "read", "write", "flush", "getName",
          "getMessage", "getTime", "before", "after", "getInstance",
          "getTimeInMillis", "set", "list", "string", "getSimpleName",
          "getCanonicalName", "currentTimeMillis", "processData",
          "customFunc"}

# java/javax/groovy core classes whose methods we acknowledge w/o checking
_STDLIB_PREFIXES = ("java.", "javax.", "org.w3c.", "org.xml.")


@dataclass
class LintFinding:
    kind: str          # removed_class | version_gap | unknown_call | info
    detail: str
    severity: str = "warn"     # error | warn | info


@dataclass
class LintReport:
    name: str
    calls_total: int = 0
    sap_api: list = field(default_factory=list)
    gdk_both: int = 0
    gdk_only_4: list = field(default_factory=list)
    gdk_only_24: list = field(default_factory=list)
    unknown: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    verdict: str = ""          # both | needs_4_runtime | breaks_on_4 | review


_REF_CACHE: dict = {}


def _load_refs() -> tuple:
    if "gdk" not in _REF_CACHE:
        with open(GDK_PATH) as fh:
            _REF_CACHE["gdk"] = json.load(fh)
        with open(SAP_PATH) as fh:
            _REF_CACHE["sap"] = json.load(fh)
    return _REF_CACHE["gdk"], _REF_CACHE["sap"]


def lint_script(text: str, name: str = "script") -> LintReport:
    gdk, sap = _load_refs()
    rep = LintReport(name=name)
    m24 = set(gdk["2.4.21"]["methods"])
    m40 = set(gdk["4.0.29"]["methods"])
    sap_methods = {m for c in sap["classes"].values()
                   for m in c["methods"]}
    sap_classes = {c.rsplit(".", 1)[-1] for c in sap["classes"]}

    # ── imports: removed-class detection (THE 2.4→4.0 killer) ───────────
    imports = _IMPORT_RX.findall(text)
    for imp in imports:
        if imp.startswith(("groovy.util.XmlSlurper",
                           "groovy.util.XmlParser")):
            rep.findings.append(LintFinding(
                "removed_class",
                f"{imp} — moved to groovy.xml.* in Groovy 3, REMOVED from "
                f"groovy.util in 4.x; this import fails on the 4.0.29 "
                f"runtime", "error"))
        elif imp.startswith("groovy.util.GroovyTestCase"):
            rep.findings.append(LintFinding(
                "removed_class", f"{imp} — moved to groovy.test in 4.x",
                "error"))

    # ── calls ────────────────────────────────────────────────────────────
    calls = set(_CALL_RX.findall(text)) | {
        c for c in _BARE_CALL_RX.findall(text)
        if c in ("processData", "customFunc")}
    calls -= _NOISE
    calls -= _CORE_CLASS_METHODS
    # locally defined functions aren't external API
    local_defs = set(re.findall(r"\bdef\s+(?:\w+\s+)?(\w+)\s*\(", text))
    calls -= local_defs
    rep.calls_total = len(calls)

    stdlib_ok = any(imp.startswith(_STDLIB_PREFIXES) for imp in imports)
    for c in sorted(calls):
        if c in sap_methods or c in sap_classes:
            rep.sap_api.append(c)
        elif c in m40 and c in m24:
            rep.gdk_both += 1
        elif c in m40:
            rep.gdk_only_4.append(c)
        elif c in m24:
            rep.gdk_only_24.append(c)
        else:
            # last resort: looks like a Java-class constructor or a
            # stdlib method on an imported type — acknowledge, don't flag,
            # when the script imports stdlib types; flag otherwise.
            if c[0].isupper() or stdlib_ok:
                continue
            rep.unknown.append(c)

    for c in rep.gdk_only_24:
        rep.findings.append(LintFinding(
            "version_gap", f"{c}() exists in 2.4 but NOT in the 4.0.29 GDK "
            f"surface — verify before running on the new runtime", "error"))
    for c in rep.gdk_only_4:
        rep.findings.append(LintFinding(
            "version_gap", f"{c}() is 4.x-only — fine on 4.0.29, breaks if "
            f"the script must also run on a 2.4 runtime", "info"))
    for c in rep.unknown:
        rep.findings.append(LintFinding(
            "unknown_call", f"{c}() not found in SAP API, GDK 2.4/4.0, or "
            f"stdlib heuristics — typo, custom helper, or hallucinated "
            f"API?", "warn"))

    if any(f.kind == "removed_class" or
           (f.kind == "version_gap" and f.severity == "error")
           for f in rep.findings):
        rep.verdict = "breaks_on_4" if any(
            f.kind == "removed_class" for f in rep.findings) else "review"
    elif rep.gdk_only_4:
        rep.verdict = "needs_4_runtime"
    elif rep.unknown:
        rep.verdict = "review"
    else:
        rep.verdict = "both"
    return rep


def lint_corpus(corpus: dict) -> dict:
    """Lint every .groovy/.gsh in a {key: text} corpus → summary dict."""
    out = {"scripts": 0, "verdicts": {}, "breaks_on_4": [],
           "needs_4": [], "unknown_calls": {}}
    for k, t in corpus.items():
        if not k.endswith((".groovy", ".gsh")):
            continue
        rep = lint_script(t, name=k)
        out["scripts"] += 1
        out["verdicts"][rep.verdict] = out["verdicts"].get(rep.verdict,
                                                           0) + 1
        if rep.verdict == "breaks_on_4":
            out["breaks_on_4"].append(k)
        if rep.verdict == "needs_4_runtime":
            out["needs_4"].append(k)
        for c in rep.unknown:
            out["unknown_calls"][c] = out["unknown_calls"].get(c, 0) + 1
    return out


def main() -> int:
    import argparse
    import sys
    sys.path.insert(0, _HERE)
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?",
                    help="a .groovy file, or omit to lint the library")
    ap.add_argument("--library", default="")
    args = ap.parse_args()
    if args.target and os.path.isfile(args.target):
        rep = lint_script(open(args.target).read(),
                          os.path.basename(args.target))
        print(f"{rep.name}: verdict={rep.verdict} | {rep.calls_total} "
              f"calls | SAP API: {sorted(set(rep.sap_api))}")
        for f in rep.findings:
            print(f"  [{f.severity}] {f.detail}")
        return 0
    from library_builder.library_store import LibraryStore
    lib_dir = args.library or "/tmp/libtest"
    summary = lint_corpus(LibraryStore(lib_dir).as_corpus())
    print(f"{summary['scripts']} scripts | verdicts: {summary['verdicts']}")
    if summary["breaks_on_4"]:
        print("BREAKS ON 4.0.29 RUNTIME:")
        for k in summary["breaks_on_4"]:
            print(f"  ✗ {k.rsplit('/', 1)[-1]}")
    if summary["unknown_calls"]:
        top = sorted(summary["unknown_calls"].items(),
                     key=lambda kv: -kv[1])[:10]
        print(f"top unknown calls: {top}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
