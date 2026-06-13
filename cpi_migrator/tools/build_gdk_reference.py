"""tools/build_gdk_reference.py — extract the Groovy GDK surface from
Apache Groovy SOURCE at pinned tags (not scraped docs: exact, versioned,
reproducible).

CPI version matrix (user-pinned 2026-06-12): runtime Groovy 4.0.29;
legacy scripts authored for 2.4.x. GDK extension methods live in
*GroovyMethods classes as `public static RET name(SelfType self, ...)` —
the first parameter is the receiver type the method appears on.

Output: reference/groovy_gdk.json
  {"2.4.21": {"methods": {name: [{self, params, klass}]}, "classes": {...}},
   "4.0.29": {...},
   "class_locations": {...}}   # the package-move gotchas
"""
from __future__ import annotations

import json
import os
import re
import sys

SRC_DIR = "/tmp/gdk"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "reference", "groovy_gdk.json")

# public static [<T...>] ReturnType name(SelfType self, ...)
# first param under ANY name — 2.4 sources are inconsistent ('self',
# 'value', 'data'); these classes' public static surface IS the GDK.
_METHOD_RX = re.compile(
    r"public\s+static\s+(?:<[^>]+>\s+)?([\w.\[\]<>, ?]+?)\s+(\w+)\s*\("
    r"\s*(?:final\s+)?([\w.\[\]<>, ?]+?)\s+\w+\s*[,)]")

#: known class relocations between 2.4 and 4.x — the migration killers.
CLASS_LOCATIONS = {
    "XmlSlurper":  {"2.4": "groovy.util.XmlSlurper",
                    "4.0": "groovy.xml.XmlSlurper"},
    "XmlParser":   {"2.4": "groovy.util.XmlParser",
                    "4.0": "groovy.xml.XmlParser"},
    "GroovyTestCase": {"2.4": "groovy.util.GroovyTestCase",
                       "4.0": "groovy.test.GroovyTestCase"},
    "ConfigSlurper": {"2.4": "groovy.util.ConfigSlurper",
                      "4.0": "groovy.util.ConfigSlurper"},   # unchanged
    "JsonSlurper": {"2.4": "groovy.json.JsonSlurper",
                    "4.0": "groovy.json.JsonSlurper"},        # unchanged
    "JsonOutput":  {"2.4": "groovy.json.JsonOutput",
                    "4.0": "groovy.json.JsonOutput"},
    "MarkupBuilder": {"2.4": "groovy.xml.MarkupBuilder",
                      "4.0": "groovy.xml.MarkupBuilder"},
    "StreamingMarkupBuilder": {"2.4": "groovy.xml.StreamingMarkupBuilder",
                               "4.0": "groovy.xml.StreamingMarkupBuilder"},
}

FILES = {
    "4.0.29": [("v4_DefaultGroovyMethods.java", "DefaultGroovyMethods"),
               ("v4_StringGroovyMethods.java", "StringGroovyMethods"),
               ("v4_IOGroovyMethods.java", "IOGroovyMethods"),
               ("v4_EncodingGroovyMethods.java", "EncodingGroovyMethods"),
               ("v4_ResourceGroovyMethods.java", "ResourceGroovyMethods"),
               ("v4_DateUtilExtensions.java", "DateUtilExtensions"),
               ("v4_ProcessGroovyMethods.java", "ProcessGroovyMethods")],
    "2.4.21": [("v2_DefaultGroovyMethods.java", "DefaultGroovyMethods"),
               ("v2_StringGroovyMethods.java", "StringGroovyMethods"),
               ("v2_IOGroovyMethods.java", "IOGroovyMethods"),
               ("v2_EncodingGroovyMethods.java", "EncodingGroovyMethods"),
               ("v2_ResourceGroovyMethods.java", "ResourceGroovyMethods"),
               ("v2_ProcessGroovyMethods.java", "ProcessGroovyMethods"),
               ("v2_DateGroovyMethods.java", "DateGroovyMethods")],
}


def extract(src_dir: str = SRC_DIR) -> dict:
    ref = {"class_locations": CLASS_LOCATIONS}
    for version, files in FILES.items():
        methods: dict = {}
        per_class: dict = {}
        for fname, klass in files:
            path = os.path.join(src_dir, fname)
            if not os.path.exists(path):
                print(f"  ! {fname} missing — skipped", file=sys.stderr)
                continue
            text = open(path, encoding="utf-8", errors="replace").read()
            n = 0
            for m in _METHOD_RX.finditer(text):
                ret, name, self_t = (m.group(1).strip(), m.group(2),
                                     m.group(3).strip())
                methods.setdefault(name, []).append(
                    {"self": self_t, "ret": ret, "klass": klass})
                n += 1
            per_class[klass] = n
        ref[version] = {"methods": methods, "per_class": per_class,
                        "n_methods": len(methods)}
    return ref


def main() -> int:
    ref = extract()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(ref, fh, indent=0, sort_keys=True)
    for v in ("2.4.21", "4.0.29"):
        if v in ref:
            print(f"{v}: {ref[v]['n_methods']} distinct GDK method names "
                  f"({ref[v]['per_class']})")
    only4 = sorted(set(ref["4.0.29"]["methods"]) -
                   set(ref["2.4.21"]["methods"]))
    only2 = sorted(set(ref["2.4.21"]["methods"]) -
                   set(ref["4.0.29"]["methods"]))
    print(f"4.0-only methods: {len(only4)} (e.g. {only4[:8]})")
    print(f"2.4-only (removed by 4.0): {len(only2)} (e.g. {only2[:8]})")
    print(f"→ {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
