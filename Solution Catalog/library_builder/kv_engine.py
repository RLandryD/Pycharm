"""library_builder/kv_engine.py

Analyze key-value config artifacts (prop, propdef, properties) and JSON.

Dedup decision (locked with the user): two config files with the SAME KEY SET
are the SAME solution — the key structure is what's reusable; the values are
environment-specific *examples* of that solution. So we fingerprint on the key
set, but we KEEP example value-sets on the entry (a few real samples) rather
than discarding them. Collapsing same-key/different-value files is therefore
INTENDED, not a suspect over-collapse.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from library_builder.extractor import Solution, _sha


_PROP_LINE = re.compile(r"^\s*([^#!=:\s][^=:]*)\s*[=:]\s*(.*)$", re.M)

_META_JSON_KEYS = {
    "products", "keywords", "supportedplatforms", "countries", "industries",
    "lineofbusiness", "partnercontent", "updateavailable", "exportinformation",
}


def analyze_keyvalue(text: str, ext: str
                     ) -> tuple[Optional[Solution], Optional[str]]:
    if not text or not text.strip():
        return None, "empty"
    if ext == "json":
        return _analyze_json(text)
    return _analyze_props(text, ext)


def _analyze_props(text: str, ext: str):
    pairs = [(k.strip(), v.strip()) for k, v in _PROP_LINE.findall(text)
             if k.strip()]
    keys = sorted({k for k, _v in pairs})
    if not keys:
        return None, "no keys found"
    fingerprint = _sha(ext + "|" + "|".join(keys))
    example = {k: v for k, v in pairs}
    code = json.dumps({"keys": keys}, indent=2)
    sol = Solution(
        fingerprint=fingerprint,
        type=("propdef" if ext == "propdef" else "properties"),
        category="EXTERNALIZED_PARAMETERS",
        operations=[f"key:{k}" for k in keys[:40]],
        requires={"key_count": len(keys), "intended_value_dedup": True},
        produces={"example_values": [example]},
        code=code,
    )
    return sol, None


def _analyze_json(text: str):
    try:
        data = json.loads(text)
    except Exception as exc:
        return None, f"json parse error: {str(exc)[:60]}"
    if not isinstance(data, dict):
        return Solution(
            fingerprint=_sha("json|" + type(data).__name__),
            type="json", category="JSON_CONFIG",
            requires={"top_level": type(data).__name__,
                      "intended_value_dedup": True},
            code=type(data).__name__,
        ), None
    keys = sorted(data.keys())
    low = {k.lower() for k in keys}
    if low & _META_JSON_KEYS and len(low & _META_JSON_KEYS) >= 2:
        return None, "metadata json (export/manifest shape)"
    fingerprint = _sha("json|" + "|".join(keys))
    example = {}
    for k in keys[:40]:
        v = data[k]
        if isinstance(v, (dict, list)):
            example[k] = json.dumps(v)[:120]
        else:
            example[k] = str(v)[:120]
    code = json.dumps({"keys": keys}, indent=2)
    sol = Solution(
        fingerprint=fingerprint,
        type="json", category="JSON_CONFIG",
        operations=[f"key:{k}" for k in keys[:40]],
        requires={"key_count": len(keys), "intended_value_dedup": True},
        produces={"example_values": [example]},
        code=code,
    )
    return sol, None
