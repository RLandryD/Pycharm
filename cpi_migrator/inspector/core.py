"""inspector/core.py — format-agnostic payload analyzer for safe client
troubleshooting (backlog: payload_inspector).

Pipeline per payload: detect → parse → PROFILE (structure + value shapes) →
REDACT (type-preserving: every value masked, every structural token kept —
element/attribute names, JSON keys, CSV headers, EDI segment ids and envelope
service characters survive so the shape stays diagnosable while the client's
data never leaves the room) → VALIDATE against requirements (XSD via
xmlschema when installed, JSON Schema via jsonschema, CSV column rules, EDI
envelope arithmetic) with error paths that localize the problem.

Formats: xml · json · csv · edi (X12/EDIFACT) · flat (fallback).
Everything degrades gracefully: a missing optional lib reports
"validator unavailable" instead of crashing.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field


# ── report ───────────────────────────────────────────────────────────────────
@dataclass
class Finding:
    level: str          # PASS | FAIL | WARN | INFO
    check: str
    detail: str
    path: str = ""


@dataclass
class InspectionReport:
    fmt: str = "unknown"
    parse_ok: bool = False
    parse_error: str = ""
    profile: dict = field(default_factory=dict)
    redacted: str = ""
    findings: list = field(default_factory=list)

    def add(self, level, check, detail, path=""):
        self.findings.append(Finding(level, check, detail, path))


# ── shared type-preserving redaction ─────────────────────────────────────────
_DEF_KEEP = frozenset()


def redact_value(v: str, keep: frozenset = _DEF_KEEP) -> str:
    """Mask a VALUE while preserving its shape: digits→9, letters→X/x,
    punctuation/length/decimal points/date separators untouched
    ('2026-01-15'→'9999-99-99', 'Müller & Söhne'→'Xxxxxx & Xxxxx',
    'A-46'→'X-99'). Values in `keep` (exact match) pass through — for
    e.g. routing-relevant enum codes the client clears for sharing."""
    if v is None:
        return v
    s = str(v)
    if s in keep or s.strip() in keep:
        return s
    out = []
    for ch in s:
        if ch.isdigit():
            out.append("9")
        elif ch.isalpha():
            out.append("X" if ch.isupper() else "x")
        else:
            out.append(ch)
    return "".join(out)


def _shape(v: str) -> str:
    s = (v or "").strip()
    if not s:
        return "empty"
    if re.fullmatch(r"-?\d+", s):
        return "integer"
    if re.fullmatch(r"-?\d+[.,]\d+", s):
        return "decimal"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}([T ].*)?", s):
        return "date"
    if s.lower() in ("true", "false"):
        return "boolean"
    return "text"


# ── detection ────────────────────────────────────────────────────────────────
def detect_format(data: str) -> str:
    s = data.lstrip("\ufeff \t\r\n")
    if s.startswith("-----BEGIN PGP"):
        return "pgp"
    if s.startswith("<"):
        return "xml"
    if s[:1] in "{[":
        return "json"
    head = s[:3]
    if head == "ISA" or head == "UNA" or s.startswith("UNB"):
        return "edi"
    first = s.splitlines()[0] if s else ""
    if first.count(",") >= 1 or first.count(";") >= 1 or "\t" in first:
        return "csv"
    return "flat"


# ── XML handler ──────────────────────────────────────────────────────────────
def _xml_inspect(data: str, rep: InspectionReport, keep: frozenset):
    from lxml import etree
    try:
        root = etree.fromstring(data.encode("utf-8"))
        rep.parse_ok = True
    except etree.XMLSyntaxError as exc:
        rep.parse_error = str(exc)
        rep.add("FAIL", "well-formed XML", str(exc),
                f"line {exc.lineno}" if getattr(exc, "lineno", 0) else "")
        return
    paths: dict = {}
    shapes: dict = {}

    def walk(el, p):
        tag = etree.QName(el).localname
        cp = f"{p}/{tag}"
        d = paths.setdefault(cp, {"count": 0, "attrs": set()})
        d["count"] += 1
        for k, v in el.attrib.items():
            d["attrs"].add(etree.QName(k).localname)
            shapes.setdefault(f"{cp}/@{etree.QName(k).localname}",
                              set()).add(_shape(v))
            el.set(k, redact_value(v, keep))
        if el.text and el.text.strip():
            shapes.setdefault(cp, set()).add(_shape(el.text))
            el.text = redact_value(el.text, keep)
        if el.tail and el.tail.strip():
            el.tail = redact_value(el.tail, keep)
        for c in el:
            if isinstance(c.tag, str):
                walk(c, cp)
            elif c.tail and c.tail.strip():
                c.tail = redact_value(c.tail, keep)
    walk(root, "")
    ns = sorted({etree.QName(e).namespace for e in root.iter()
                 if isinstance(e.tag, str) and etree.QName(e).namespace})
    rep.profile = {
        "root": etree.QName(root).localname,
        "namespaces": ns,
        "elements": {p: {"count": d["count"],
                         "attrs": sorted(d["attrs"]),
                         "value_shapes": sorted(shapes.get(p, ()))}
                     for p, d in sorted(paths.items())},
    }
    rep.redacted = etree.tostring(root, pretty_print=True,
                                  encoding="unicode")


def _xsd_validate(data: str, xsd_text: str, rep: InspectionReport):
    try:
        import xmlschema
    except ImportError:
        rep.add("WARN", "XSD validation",
                "xmlschema not installed (pip install xmlschema) — skipped")
        return
    try:
        schema = xmlschema.XMLSchema(io.StringIO(xsd_text))
    except Exception as exc:
        rep.add("WARN", "XSD validation", f"schema unreadable: {exc}")
        return
    errs = list(schema.iter_errors(io.StringIO(data)))
    if not errs:
        rep.add("PASS", "XSD validation", "payload valid against schema")
    for e in errs[:20]:
        rep.add("FAIL", "XSD validation", e.reason or str(e),
                e.path or "")
    if len(errs) > 20:
        rep.add("INFO", "XSD validation", f"... {len(errs) - 20} more errors")


# ── JSON handler ─────────────────────────────────────────────────────────────
def _json_inspect(data: str, rep: InspectionReport, keep: frozenset):
    try:
        obj = json.loads(data)
        rep.parse_ok = True
    except json.JSONDecodeError as exc:
        rep.parse_error = str(exc)
        rep.add("FAIL", "valid JSON", exc.msg,
                f"line {exc.lineno} col {exc.colno}")
        return
    paths: dict = {}

    def red(o, p):
        if isinstance(o, dict):
            return {k: red(v, f"{p}.{k}") for k, v in o.items()}
        if isinstance(o, list):
            d = paths.setdefault(p + "[]", {"count": 0, "shapes": set()})
            d["count"] += len(o)
            return [red(v, p + "[]") for v in o]
        d = paths.setdefault(p, {"count": 0, "shapes": set()})
        d["count"] += 1
        if isinstance(o, bool) or o is None:
            d["shapes"].add("boolean" if isinstance(o, bool) else "null")
            return o
        if isinstance(o, (int, float)):
            d["shapes"].add("number")
            return 9 if isinstance(o, int) else 9.9
        d["shapes"].add(_shape(o))
        return redact_value(o, keep)
    redacted = red(obj, "$")
    rep.profile = {"paths": {p: {"count": d["count"],
                                 "value_shapes": sorted(d["shapes"])}
                             for p, d in sorted(paths.items())}}
    rep.redacted = json.dumps(redacted, indent=2, ensure_ascii=False)


def _jsonschema_validate(data: str, schema_text: str, rep: InspectionReport):
    try:
        import jsonschema
    except ImportError:
        rep.add("WARN", "JSON Schema validation",
                "jsonschema not installed — skipped")
        return
    try:
        schema = json.loads(schema_text)
        validator = jsonschema.Draft7Validator(schema)
    except Exception as exc:
        rep.add("WARN", "JSON Schema validation", f"schema unreadable: {exc}")
        return
    errs = sorted(validator.iter_errors(json.loads(data)),
                  key=lambda e: list(e.absolute_path))
    if not errs:
        rep.add("PASS", "JSON Schema validation", "payload valid")
    for e in errs[:20]:
        rep.add("FAIL", "JSON Schema validation", e.message,
                "$." + ".".join(str(x) for x in e.absolute_path))


# ── CSV handler ──────────────────────────────────────────────────────────────
def _csv_inspect(data: str, rep: InspectionReport, keep: frozenset,
                 has_header: bool = True):
    sample = data[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        class dialect:                       # noqa: N801 (csv convention)
            delimiter = ","
            quotechar = '"'
    rows = list(csv.reader(io.StringIO(data), delimiter=dialect.delimiter,
                           quotechar=getattr(dialect, "quotechar", '"')))
    if not rows:
        rep.parse_error = "empty file"
        rep.add("FAIL", "CSV parse", "no rows")
        return
    rep.parse_ok = True
    header = rows[0] if has_header else [f"col{i + 1}"
                                         for i in range(len(rows[0]))]
    body = rows[1:] if has_header else rows
    widths = {len(r) for r in body}
    cols = {}
    for i, h in enumerate(header):
        vals = [r[i] for r in body if i < len(r)]
        cols[h or f"col{i + 1}"] = {
            "value_shapes": sorted({_shape(v) for v in vals} or {"empty"}),
            "max_len": max((len(v) for v in vals), default=0),
            "empty": sum(1 for v in vals if not v.strip()),
        }
    rep.profile = {"delimiter": dialect.delimiter, "columns": len(header),
                   "header": header if has_header else None,
                   "rows": len(body), "row_widths": sorted(widths),
                   "column_profile": cols}
    if len(widths) > 1:
        rep.add("WARN", "CSV consistency",
                f"inconsistent column counts per row: {sorted(widths)}")
    out = io.StringIO()
    w = csv.writer(out, delimiter=dialect.delimiter,
                   quotechar=getattr(dialect, "quotechar", '"'),
                   lineterminator="\n")
    if has_header:
        w.writerow(header)                  # headers are structure — kept
    for r in body:
        w.writerow([redact_value(v, keep) for v in r])
    rep.redacted = out.getvalue()


def _csv_validate(rep: InspectionReport, expected_headers=None,
                  expected_columns=None):
    prof = rep.profile or {}
    if expected_columns is not None:
        got = prof.get("columns")
        lvl = "PASS" if got == expected_columns else "FAIL"
        rep.add(lvl, "CSV column count",
                f"expected {expected_columns}, got {got}")
    if expected_headers:
        have = [h.strip() for h in (prof.get("header") or [])]
        missing = [h for h in expected_headers if h not in have]
        if missing:
            rep.add("FAIL", "CSV headers", f"missing: {missing}")
        else:
            rep.add("PASS", "CSV headers", "all expected headers present")


# ── EDI handler (X12 + EDIFACT) ──────────────────────────────────────────────
def _edi_inspect(data: str, rep: InspectionReport, keep: frozenset):
    s = data.replace("\r\n", "\n").strip()
    if s.startswith("ISA"):
        if len(s) < 106:
            rep.parse_error = "ISA segment shorter than 106 chars"
            rep.add("FAIL", "X12 envelope", rep.parse_error)
            return
        elem = s[3]
        term = s[105]
        comp = s[104]
        segs = [x.strip("\n") for x in s.split(term) if x.strip()]
        rep.parse_ok = True
        counts: dict = {}
        for g in segs:
            counts[g.split(elem)[0].strip()] = \
                counts.get(g.split(elem)[0].strip(), 0) + 1
        rep.profile = {"standard": "X12", "element_sep": elem,
                       "segment_term": term, "component_sep": comp,
                       "segments": counts, "total_segments": len(segs)}
        _x12_envelope_checks(segs, elem, rep)
        red = []
        for g in segs:
            parts = g.split(elem)
            tag = parts[0].strip()
            if tag == "ISA":
                red.append(g)               # fixed-width service segment kept
                continue
            red.append(elem.join([parts[0]] +
                                 [redact_value(p, keep) for p in parts[1:]]))
        rep.redacted = (term + "\n").join(red) + term
    elif s.startswith(("UNB", "UNA")):
        rep.parse_ok = True
        term = "'"
        segs = [x.strip() for x in s.split(term) if x.strip()]
        counts: dict = {}
        for g in segs:
            counts[g.split("+")[0]] = counts.get(g.split("+")[0], 0) + 1
        rep.profile = {"standard": "EDIFACT", "segments": counts,
                       "total_segments": len(segs)}
        red = [("+".join([g.split("+")[0]]
                         + [redact_value(p, keep)
                            for p in g.split("+")[1:]])) for g in segs]
        rep.redacted = (term + "\n").join(red) + term
    else:
        rep.parse_error = "no ISA/UNB envelope"
        rep.add("FAIL", "EDI envelope", rep.parse_error)


def _x12_envelope_checks(segs, elem, rep):
    by = {}
    for g in segs:
        by.setdefault(g.split(elem)[0].strip(), []).append(g.split(elem))
    isa = by.get("ISA", [[]])[0]
    iea = by.get("IEA", [[]])[0]
    st = by.get("ST", [])
    se = by.get("SE", [])
    gs = by.get("GS", [])
    ge = by.get("GE", [])
    if len(st) != len(se):
        rep.add("FAIL", "X12 ST/SE pairing",
                f"{len(st)} ST vs {len(se)} SE")
    for sti, sei in zip(st, se):
        # SE01 = segment count ST..SE inclusive
        start = segs.index(elem.join(sti))
        end = segs.index(elem.join(sei))
        expect = end - start + 1
        got = sei[1] if len(sei) > 1 else "?"
        lvl = "PASS" if str(expect) == got else "FAIL"
        rep.add(lvl, "X12 SE01 segment count",
                f"declared {got}, actual {expect}",
                f"ST*{sti[1] if len(sti) > 1 else '?'}")
        if len(sti) > 2 and len(sei) > 2 and sti[2] != sei[2]:
            rep.add("FAIL", "X12 ST02/SE02 control number",
                    f"{sti[2]} vs {sei[2]}")
    if ge and len(ge[0]) > 1:
        lvl = "PASS" if str(len(st)) == ge[0][1] else "FAIL"
        rep.add(lvl, "X12 GE01 transaction count",
                f"declared {ge[0][1]}, actual {len(st)}")
    if iea and len(iea) > 1:
        lvl = "PASS" if str(len(gs)) == iea[1] else "FAIL"
        rep.add(lvl, "X12 IEA01 group count",
                f"declared {iea[1]}, actual {len(gs)}")
    if isa and len(isa) > 13 and iea and len(iea) > 2:
        lvl = "PASS" if isa[13] == iea[2] else "FAIL"
        rep.add(lvl, "X12 ISA13/IEA02 control number",
                f"{isa[13]} vs {iea[2]}")


# ── PGP keyring handler (report-only) ────────────────────────────────────────
def _pgp_inspect(data: str, rep: InspectionReport, keep: frozenset):
    """Keyrings get METADATA inspection only — key ids, user ids,
    algorithms — for the re-key worksheet. No redacted copy is produced:
    a keyring is a secret, not a payload to share."""
    from inspector.pgp_inspect import inspect_keyring
    try:
        r = inspect_keyring(data)
    except Exception as exc:
        rep.parse_error = str(exc)
        rep.add("FAIL", "PGP keyring parse", str(exc))
        return
    if not r["keys"]:
        rep.parse_error = "no OpenPGP key packets found"
        rep.add("FAIL", "PGP keyring parse", rep.parse_error)
        return
    rep.parse_ok = True
    rep.profile = {
        "keyring_kind": r["kind"],
        "keys": [{"role": "primary" if k.primary else "subkey",
                  "kind": k.kind, "algorithm": f"{k.algorithm}-{k.bits}",
                  "key_id": k.key_id, "fingerprint": k.fingerprint,
                  "created": k.created, "user_ids": k.user_ids}
                 for k in r["keys"]],
    }
    rep.add("PASS", "PGP keyring parse",
            f"{r['kind']} keyring · "
            f"{sum(1 for k in r['keys'] if k.primary)} primary key(s), "
            f"{sum(1 for k in r['keys'] if not k.primary)} subkey(s)")
    if r["kind"] in ("secret", "mixed"):
        rep.add("INFO", "PGP keyring handling",
                "contains SECRET keys — upload to CPI Security Material "
                "(UI) as 'PGP Secret Keyring'; never share this file")
    for w in r["warnings"]:
        rep.add("WARN", "PGP compatibility", w)
    rep.redacted = ""        # deliberately no shareable copy of a keyring


# ── flat fallback ────────────────────────────────────────────────────────────
def _flat_inspect(data: str, rep: InspectionReport, keep: frozenset):
    lines = data.splitlines()
    rep.parse_ok = True
    widths = {}
    for ln in lines:
        widths[len(ln)] = widths.get(len(ln), 0) + 1
    rep.profile = {"lines": len(lines),
                   "line_width_histogram": dict(sorted(widths.items()))}
    if len(widths) <= 3 and lines:
        rep.add("INFO", "flat structure",
                "few distinct line widths — likely fixed-width records")
    rep.redacted = "\n".join(redact_value(ln, keep) for ln in lines)


# ── public API ───────────────────────────────────────────────────────────────
def inspect_payload(data: "str | bytes", fmt: str | None = None,
                    schema: str | None = None,
                    schema_kind: str | None = None,
                    keep_values=(),
                    csv_expected_headers=None,
                    csv_expected_columns=None) -> InspectionReport:
    """One-call pipeline. `schema` (text) + `schema_kind` ('xsd'|'json') run
    validation; CSV expectations via the csv_* args."""
    if isinstance(data, bytes):
        data = data.decode("utf-8", "replace")
    keep = frozenset(keep_values or ())
    rep = InspectionReport(fmt=fmt or detect_format(data))
    handler = {"xml": _xml_inspect, "json": _json_inspect,
               "csv": _csv_inspect, "edi": _edi_inspect,
               "pgp": _pgp_inspect,
               "flat": _flat_inspect}.get(rep.fmt, _flat_inspect)
    handler(data, rep, keep)
    if rep.parse_ok:
        rep.add("PASS", f"{rep.fmt} parse", "payload parsed cleanly")
    if schema and rep.parse_ok:
        if (schema_kind or "").lower() == "xsd" or \
                (schema_kind is None and rep.fmt == "xml"):
            _xsd_validate(data, schema, rep)
        elif (schema_kind or "").lower() in ("json", "jsonschema") or \
                (schema_kind is None and rep.fmt == "json"):
            _jsonschema_validate(data, schema, rep)
    if rep.fmt == "csv" and rep.parse_ok and \
            (csv_expected_headers or csv_expected_columns is not None):
        _csv_validate(rep, csv_expected_headers, csv_expected_columns)
    return rep
