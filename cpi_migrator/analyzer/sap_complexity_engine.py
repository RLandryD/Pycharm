"""SAP-faithful two-axis complexity & effort engine.

Reproduces SAP Integration Suite Migration Assessment's model:

    interface signals  ->  fire weighted rules  ->  sum band weights
                       ->  total weight  ->  T-shirt size (S/M/L/XL)
                       ->  (size x worst-case category)  ->  effort hour range

All weights, size thresholds, the effort table, and the rule definitions are
SAP's own values, extracted from the live Migration Assessment OData service
(pimas) — not hand-tuned. See analyzer/data/:
    sap_ma_rule_catalog.json            98 rules / 450 variants / 1373 bands
    sap_ma_rule_definitions_index.json  what each rule inspects + match type
    sap_ma_weight_to_size.json          weight -> S/M/L/XL thresholds
    sap_ma_effort_table.json            (size x category) -> hour range

Two modes, one output shape (ComplexityResult):

  MODE 1 — TRUE MA (exact). When a real Migration Assessment result is present
  for the interface (weight + size + category already computed by SAP), use it
  directly. No guessing. This is what runs for real client MA exports.

  MODE 2 — SIGNAL APPROXIMATION. When only the artifact bundle is available
  (no SAP evaluation), extract structural signals (script/mapping/XSLT counts,
  orchestration steps, participants, adapters, BPM) and fire SAP's rules
  against them. Grounded in SAP's real weights, but an ESTIMATE — it is not a
  substitute for running SAP's tool on a live PI/PO system, and it has not been
  calibrated against a real evaluation (none was available in the trial tenant).

HONEST CAVEAT baked into every result: SAP's effort numbers are *technical
migration* effort (converting an existing interface), NOT *from-scratch build*
effort. A pluggable effort table lets a build-calibrated table be swapped in
for greenfield estimates once real build data points exist.
"""
from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("analyzer.sap_complexity_engine")

_DATA = Path(__file__).parent / "data"


# ---------------------------------------------------------------------------
# Load SAP's extracted model once at import
# ---------------------------------------------------------------------------

def _load(name: str) -> dict | list:
    try:
        return json.load(open(_DATA / name, encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - data files ship with package
        logger.error("Could not load %s: %s", name, exc)
        return {}


_RULE_CATALOG = _load("sap_ma_rule_catalog.json")          # {rule: [bands]}
_RULE_DEFS = _load("sap_ma_rule_definitions_index.json")   # {rule: {desc,factset,rule_type}}
_WEIGHT_TO_SIZE = _load("sap_ma_weight_to_size.json")      # [{Size,FromWeight,ToWeight}]
_EFFORT_TABLE = _load("sap_ma_effort_table.json")          # {table:{S:{Migrate:[lo,hi]}}}

# SAP MigrationStatus label normalisation. The catalog uses the long labels
# (Ready to migrate / Adjustments required / Evaluation required); the effort
# table and API use the short ones (Migrate / Adapt / Evaluate). Map both ways.
_CAT_LONG_TO_SHORT = {
    "Ready to migrate": "Migrate",
    "Adjustments required": "Adapt",
    "Evaluation required": "Evaluate",
    # already-short pass through
    "Migrate": "Migrate", "Adapt": "Adapt", "Evaluate": "Evaluate",
}
# Worst-case precedence: any Evaluate dominates; else any Adapt; else Migrate.
_CAT_RANK = {"Migrate": 0, "Adapt": 1, "Evaluate": 2}
_RANK_CAT = {0: "Migrate", 1: "Adapt", 2: "Evaluate"}


def _short_cat(label: str) -> str:
    return _CAT_LONG_TO_SHORT.get((label or "").strip(), "Migrate")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class FiredRule:
    rule: str
    characteristic: str
    matched_value: str
    category: str          # short: Migrate / Adapt / Evaluate
    weight: int
    signal_note: str = ""  # why it fired (which signal / count)


@dataclass
class ComplexityResult:
    """Canonical two-axis output consumed across the app."""
    interface_name: str
    total_weight: int
    size: str              # S / M / L / XL
    category: str          # Migrate / Adapt / Evaluate (worst-case)
    effort_hours_low: float
    effort_hours_high: float
    fired_rules: list = field(default_factory=list)   # list[FiredRule]
    mode: str = "signal"   # "true_ma" | "signal"
    caveats: list = field(default_factory=list)
    signals: dict = field(default_factory=dict)

    # ---- compatibility helpers (bridge to the legacy LOW/MEDIUM/HIGH world) --
    @property
    def legacy_complexity(self) -> str:
        """Derive a LOW/MEDIUM/HIGH label from the SAP size, so existing
        consumers that still read a 3-band string keep working during the
        repoint. S->LOW, M->MEDIUM, L/XL->HIGH."""
        return {"S": "LOW", "M": "MEDIUM", "L": "HIGH", "XL": "HIGH"}.get(self.size, "MEDIUM")

    @property
    def effort_hours_avg(self) -> float:
        return round((self.effort_hours_low + self.effort_hours_high) / 2, 1)

    @property
    def effort_days_avg(self) -> float:
        """Average effort expressed in 8-hour days (for legacy day-based
        consumers). Not SAP-native — SAP speaks hours — but provided for the
        transition."""
        return round(self.effort_hours_avg / 8.0, 2)


# ---------------------------------------------------------------------------
# Band parsing
# ---------------------------------------------------------------------------

def _parse_range(match_value: str) -> Optional[tuple[int, int]]:
    """'1,10' -> (1,10); '201,99999' -> (201,99999). Returns None if not a
    numeric range (e.g. a ValueMatch token like 'Java')."""
    if not match_value or "," not in match_value:
        return None
    parts = match_value.split(",")
    try:
        lo = int(parts[0].strip())
        hi = int(parts[-1].strip())
        return lo, hi
    except (ValueError, IndexError):
        return None


def _band_for_count(rule_name: str, count: int,
                    characteristic: str = "RECEIVER_IF") -> Optional[dict]:
    """Find the catalog band whose numeric range contains `count`, for the
    given characteristic. Returns the band dict or None."""
    full = rule_name if rule_name in _RULE_CATALOG else f"MAIN_{rule_name}"
    bands = _RULE_CATALOG.get(full, [])
    best = None
    for b in bands:
        if characteristic and b.get("characteristic") and b["characteristic"] != characteristic:
            continue
        rng = _parse_range(b.get("match_value", ""))
        if rng and rng[0] <= count <= rng[1]:
            best = b
            break
    return best


def _band_for_value(rule_name: str, value: str,
                    characteristic: str = "RECEIVER_IF") -> Optional[dict]:
    """Find a ValueMatch band whose match_value equals `value`, OR — for rules
    whose match_value is a comma-separated list of tokens (e.g. adapter-type
    rules: 'FTP,HTTP,SOAP,XI') — whose list contains `value`."""
    full = rule_name if rule_name in _RULE_CATALOG else f"MAIN_{rule_name}"
    val = str(value).strip().lower()
    for b in _RULE_CATALOG.get(full, []):
        if characteristic and b.get("characteristic") and b["characteristic"] != characteristic:
            continue
        mv = b.get("match_value", "").strip()
        tokens = [t.strip().lower() for t in mv.split(",")] if "," in mv else [mv.lower()]
        if val == mv.lower() or val in tokens:
            return b
    return None


# ---------------------------------------------------------------------------
# Signal extraction from a real artifact bundle (Mode 2 input)
# ---------------------------------------------------------------------------

def extract_signals(bundle_zip: bytes) -> dict:
    """Extract structural complexity signals from a single iFlow bundle zip.

    Returns counts SAP's rules key on. All derived from the bundle's files +
    the iflw BPMN XML — proven extractable on real packages (RCI093).
    """
    sig = {
        "groovy_scripts": 0, "js_scripts": 0, "message_mappings": 0,
        "xslt": 0, "xsd": 0, "edmx": 0, "wsdl": 0, "value_mappings": 0,
        "participants": 0, "message_flows": 0, "routers": 0,
        "call_activities": 0, "service_tasks": 0, "script_tasks": 0,
        "rfc_lookups": 0, "jdbc_lookups": 0,
        "has_bpm": False, "sender_adapter": "", "receiver_adapter": "",
        "mapping_types": set(),
    }
    try:
        z = zipfile.ZipFile(io.BytesIO(bundle_zip))
        names = z.namelist()
    except Exception:
        return sig

    low = [n.lower() for n in names]
    sig["groovy_scripts"] = sum(1 for n in low if n.endswith(".groovy") or n.endswith(".gsh"))
    sig["js_scripts"] = sum(1 for n in low if n.endswith(".js"))
    sig["message_mappings"] = sum(1 for n in low if n.endswith(".mmap"))
    sig["xslt"] = sum(1 for n in low if n.endswith(".xsl") or n.endswith(".xslt"))
    sig["xsd"] = sum(1 for n in low if n.endswith(".xsd"))
    sig["edmx"] = sum(1 for n in low if n.endswith(".edmx"))
    sig["wsdl"] = sum(1 for n in low if n.endswith(".wsdl"))
    sig["value_mappings"] = sum(1 for n in low if "valuemap" in n or n.endswith(".vmap"))

    if sig["message_mappings"]:
        sig["mapping_types"].add("GMM")  # graphical message mapping
    if sig["xslt"]:
        sig["mapping_types"].add("XSL")

    # Parse the iflw BPMN XML for orchestration structure.
    iflw = next((n for n in names if n.lower().endswith(".iflw")), None)
    if iflw:
        try:
            xml = z.read(iflw).decode("utf-8", "replace")
        except Exception:
            xml = ""
        sig["call_activities"] = len(re.findall(r"<bpmn2:callActivity", xml))
        sig["service_tasks"] = len(re.findall(r"<bpmn2:serviceTask", xml))
        sig["script_tasks"] = len(re.findall(r"activityType[^>]*Script", xml))
        sig["participants"] = len(re.findall(r"<bpmn2:participant", xml))
        sig["message_flows"] = len(re.findall(r"<bpmn2:messageFlow", xml))
        sig["routers"] = len(re.findall(
            r"<bpmn2:exclusiveGateway|<bpmn2:parallelGateway", xml))
        if re.search(r"processdirect|integrationprocess|ccbpm", xml, re.I):
            # a local integration process / multi-step orchestration hint
            pass
        # Adapter type hints from messageFlow ComponentType properties.
        adapters = re.findall(r"<key>ComponentType</key>\s*<value>([^<]+)</value>", xml)
        if adapters:
            sig["sender_adapter"] = adapters[0]
            sig["receiver_adapter"] = adapters[-1]
        # Java mapping reference?
        if re.search(r"\.jar|javaMapping|com\.sap\.aii", xml):
            sig["mapping_types"].add("Java")

    # Look inside groovy scripts for RFC/JDBC lookups (fires lookup-count rules).
    for n in names:
        if n.lower().endswith(".groovy") or n.lower().endswith(".gsh"):
            try:
                body = z.read(n).decode("utf-8", "replace")
            except Exception:
                continue
            if re.search(r"rfc|RfcDestination|executeRfc", body):
                sig["rfc_lookups"] += 1
            if re.search(r"jdbc|DataSource|sql", body, re.I):
                sig["jdbc_lookups"] += 1

    # NOTE: we deliberately do NOT infer ccBPM/BPM from structure (step or
    # router counts). SAP's ccBPM rule is a boolean source-side fact; inferring
    # it from a migrated bundle's complexity over-fires (corpus testing showed
    # it wrongly flagged ~38% of iFlows as Evaluate). BPM is only honoured when
    # an explicit signal (is_ccbpm / is_javabpm) is set from real source/MA data.
    sig["is_ccbpm"] = False
    sig["is_javabpm"] = False

    sig["mapping_types"] = sorted(sig["mapping_types"])
    return sig


# ---------------------------------------------------------------------------
# Rule firing (Mode 2)
# ---------------------------------------------------------------------------

def signals_from_interface(record) -> dict:
    """Approximate the engine's signal dict from an InterfaceRecord (Tab-1, when
    no bundle exists yet) by counting structural cues in the description plus the
    record's flags/adapters. Same keys as extract_signals, so it feeds fire_rules
    identically — giving a true MA weight/size/effort per interface, scaling with
    how much the interface actually does (so a 'monster' outweighs an 'XL')."""
    desc = (getattr(record, "description", "") or "").lower()

    def cnt(*words):
        return sum(desc.count(w) for w in words)

    groovy = cnt("groovy")
    xslt = cnt("xslt", ".xsl", "xsl transform", "xsl mapping")
    js = cnt("javascript", " js ", "js script")
    vmap = cnt("value mapping", "value-mapping", "valuemap")
    mmaps = 1 if (getattr(record, "mapping_program", "") or "message mapping" in desc
                  or "mmap" in desc or "graphical mapping" in desc) else 0
    if getattr(record, "has_multi_mapping", False) or "multi-mapping" in desc \
            or "two message mappings" in desc or "multi mapping" in desc:
        mmaps += 1
    # Flow-step constructs → operation-mapping step-count proxy (each adds work).
    steps = cnt("splitter", "split ", "router", "route ", "multicast",
                "aggregat", "gather", "enrich", "filter", "content modifier",
                "converter", "convert ", "digest", "base64", "exception")
    channels = max(int(getattr(record, "channel_count", 1) or 1), 1)

    sig = {
        "groovy_scripts": groovy, "js_scripts": js, "xslt": xslt,
        "value_mappings": vmap, "message_mappings": mmaps,
        "call_activities": steps, "service_tasks": steps // 2,
        "participants": channels, "message_flows": channels,
        "rfc_lookups": cnt("rfc lookup"), "jdbc_lookups": cnt("jdbc lookup"),
        "sender_adapter": getattr(record, "sender_adapter", "") or "",
        "receiver_adapter": getattr(record, "receiver_adapter", "") or "",
        "has_bpm": bool(getattr(record, "has_bpm", False)),
        "mapping_types": set(),
    }
    if mmaps:
        sig["mapping_types"].add("GMM")
    if xslt:
        sig["mapping_types"].add("XSL")
    if "java mapping" in desc or "javamapping" in desc or "bytecode" in desc:
        sig["mapping_types"].add("Java")
    return sig


# Map an extracted signal to the SAP rule + how to evaluate it. Each entry:
#   (rule_name, kind, signal_key)
_SIGNAL_RULES = [
    ("GMMCustomUDFUsageCount",      "count", "groovy_scripts"),   # distinct custom UDFs ~ scripts
    ("GMMCustomFuncLibUsageCount",  "count", "js_scripts"),       # function-lib refs (rough)
    ("XSLTDependenciesCount",       "count", "xslt"),
    ("OMStepCount",                 "count", "call_activities"),  # steps in operation mapping
    ("OMParametersCount",           "count", "service_tasks"),
    ("ICOReceivers",                "count", "participants"),
    ("ICOOperationCount",           "count", "message_flows"),
    ("GMMRFCLookupCount",           "count", "rfc_lookups"),
    ("GMMJDBCLookUpCount",          "count", "jdbc_lookups"),
    ("GMMValueMapping",             "count", "value_mappings"),
]


def fire_rules(signals: dict) -> list[FiredRule]:
    """Fire SAP rules against extracted signals, returning the bands that
    matched (with their real weights and categories).

    Faithful to SAP's model: count-based rules are evaluated against BOTH the
    RECEIVER_IF and EXT_RCV_DET characteristics (SAP fires both — they are
    separate fact sets), so a rule can legitimately contribute twice.
    """
    fired: list[FiredRule] = []

    for rule_name, kind, key in _SIGNAL_RULES:
        val = signals.get(key, 0)
        if kind == "count":
            if not val or val <= 0:
                continue
            # SAP evaluates count rules per characteristic; fire each that has
            # a matching band. Rules with no characteristic fall back to "".
            chars = ["RECEIVER_IF", "EXT_RCV_DET"]
            full = rule_name if rule_name in _RULE_CATALOG else f"MAIN_{rule_name}"
            has_char = any(b.get("characteristic") for b in _RULE_CATALOG.get(full, []))
            if not has_char:
                chars = [""]
            for ch in chars:
                band = _band_for_count(rule_name, int(val), characteristic=ch)
                if band:
                    fired.append(FiredRule(
                        rule=rule_name, characteristic=band.get("characteristic", ""),
                        matched_value=band.get("match_value", ""),
                        category=_short_cat(band.get("category", "")),
                        weight=int(band.get("weight", 0)),
                        signal_note=f"{key}={val}"))

    # MappingType — ValueMatch, one band per detected mapping kind, both chars.
    for mt in signals.get("mapping_types", []):
        for ch in ("RECEIVER_IF", "EXT_RCV_DET"):
            band = _band_for_value("MappingType", mt, characteristic=ch)
            if band:
                fired.append(FiredRule(
                    rule="MappingType", characteristic=band.get("characteristic", ""),
                    matched_value=mt, category=_short_cat(band.get("category", "")),
                    weight=int(band.get("weight", 0)),
                    signal_note=f"mapping_type={mt}"))

    # Adapter-type rules — ValueMatch (no characteristic). The match_value is a
    # comma-separated adapter list; _band_for_value handles membership.
    for rule_name, key in (("SenderAdapterType", "sender_adapter"),
                           ("ReceiverAdapterType", "receiver_adapter")):
        adapter = _normalize_adapter(signals.get(key, ""))
        if adapter:
            band = _band_for_value(rule_name, adapter, characteristic="")
            if band:
                fired.append(FiredRule(
                    rule=rule_name, characteristic="",
                    matched_value=band.get("match_value", ""),
                    category=_short_cat(band.get("category", "")),
                    weight=int(band.get("weight", 0)),
                    signal_note=f"{key}={adapter}"))

    # BPM — SAP's ccBPM/JavaBPM rules are a boolean check for whether the
    # SOURCE used ccBPM (classical) or Java BPM. That is a PI/PO-side concept;
    # from a migrated CPI bundle it cannot be reliably detected (by the time
    # it's an iFlow it has already been redesigned). We therefore do NOT infer
    # ccBPM from flow complexity (step/router counts) — doing so wrongly forced
    # a third of real iFlows to "Evaluate" in corpus testing. ccBPM fires only
    # when an explicit BPM signal is present (set upstream from real source
    # metadata or a parsed MA result), never guessed from structure.
    if signals.get("is_ccbpm") or signals.get("is_javabpm"):
        rule = "ccBPM" if signals.get("is_ccbpm") else "JavaBPM"
        bands = _RULE_CATALOG.get(f"MAIN_{rule}", [])
        if bands:
            b = max(bands, key=lambda x: x.get("weight", 0))
            fired.append(FiredRule(
                rule=rule, characteristic=b.get("characteristic", ""),
                matched_value=b.get("match_value", ""),
                category=_short_cat(b.get("category", "")),
                weight=int(b.get("weight", 0)),
                signal_note="explicit BPM signal from source/MA"))

    return fired


# CPI ComponentType / adapter-name hints -> SAP MA adapter tokens.
_ADAPTER_NORMALISE = {
    "sfsf": "SFSF", "successfactors": "SFSF",
    "http": "HTTP", "https": "HTTP", "httpaae": "HTTP_AAE",
    "soap": "SOAP", "ws": "WS_AAE", "xi": "XI",
    "idoc": "IDoc", "rfc": "RFC", "jdbc": "JDBC", "jms": "JMS",
    "mail": "Mail", "smtp": "Mail", "file": "File", "ftp": "FTP",
    "sftp": "SFTP", "as2": "AS2", "as4": "AS4", "rest": "REST",
    "odata": "ODATA", "odatav2": "ODATA", "sfdc": "SFDC",
    "salesforce": "SFDC", "ariba": "Marketplace", "edi": "EDISeparator",
    "ebics": "EBICS", "oftp": "OFTP", "x400": "X400",
}


def _normalize_adapter(raw: str) -> str:
    """Map a CPI/iflw adapter or ComponentType string to a SAP MA adapter
    token. Best-effort substring match; returns '' if unrecognised."""
    if not raw:
        return ""
    s = re.sub(r"[^a-z0-9]", "", str(raw).lower())
    for key, tok in _ADAPTER_NORMALISE.items():
        if key in s:
            return tok
    return ""


# ---------------------------------------------------------------------------
# Weight -> size -> effort
# ---------------------------------------------------------------------------

def weight_to_size(total_weight: int) -> str:
    for row in _WEIGHT_TO_SIZE:
        if row["FromWeight"] <= total_weight <= row["ToWeight"]:
            return row["Size"]
    # Above the top band -> XL.
    return "XL"


# Approximation-scale calibration. The interface-list approximation fires far
# fewer rules than a real SAP MA (which inspects every PI/PO object), so its
# weights run ~10-100x smaller than real-MA weights. Feeding them into the
# real-MA size bands (S:1-150 …) squashes everything to S/M and the size-bucket
# effort table hands back a few hours. So the approximation gets its OWN bands
# and a weight-driven effort (scales with weight, no bucket, no cap). When a
# real MA export is imported (Mode 1, assess_true_ma) the SAP bands + SAP effort
# table apply instead. All values here are heuristic and configurable.
_APPROX_SIZE_BANDS = [("S", 0, 40), ("M", 41, 100), ("L", 101, 170)]  # else XL
_APPROX_EFFORT_HRS_PER_WEIGHT = (0.5, 0.85)   # (low, high) hours per weight pt


def approx_weight_to_size(weight: int) -> str:
    for size, lo, hi in _APPROX_SIZE_BANDS:
        if lo <= weight <= hi:
            return size
    return "XL"


def approx_effort_hours(weight: int) -> tuple[float, float]:
    """Weight-driven effort range (hours). Linear in weight so a heavier
    interface always costs more — the 'not pre-set' behaviour."""
    lo_rate, hi_rate = _APPROX_EFFORT_HRS_PER_WEIGHT
    return round(weight * lo_rate, 1), round(weight * hi_rate, 1)


def effort_for(size: str, category: str) -> tuple[float, float]:
    table = _EFFORT_TABLE.get("table", {})
    cell = table.get(size, {}).get(category)
    if cell and len(cell) == 2:
        return float(cell[0]), float(cell[1])
    return 0.0, 0.0


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

# Default migration-effort table is SAP's (loaded above). A build-effort table
# can be registered later for greenfield/new-project estimates.
_EFFORT_PROFILES: dict[str, dict] = {"migration": _EFFORT_TABLE}


def register_effort_profile(name: str, table: dict) -> None:
    """Register an alternative (size x category)->hours table, e.g. a
    build-calibrated profile for new projects. `table` must have the same
    shape as sap_ma_effort_table.json: {'table': {size: {cat: [lo,hi]}}}."""
    _EFFORT_PROFILES[name] = table


class SAPComplexityEngine:
    """Faithful two-axis complexity & effort estimator.

    Usage:
        eng = SAPComplexityEngine()
        # Mode 2 (signal approximation from a bundle):
        res = eng.assess_bundle("My_IFlow", bundle_zip_bytes)
        # Mode 1 (exact, from a real MA result):
        res = eng.assess_true_ma("My_IFlow", weight=380, size="L",
                                 category="Evaluate")
    """

    MIGRATION_CAVEAT = (
        "SAP effort = technical *migration* effort (converting an existing "
        "interface), not from-scratch build effort.")
    APPROX_CAVEAT = (
        "Estimate from artifact structure — not a substitute for running SAP's "
        "Migration Assessment on the live PI/PO system; uncalibrated against a "
        "real evaluation.")

    def __init__(self, effort_profile: str = "migration"):
        self.effort_profile = effort_profile

    def _effort(self, size: str, category: str) -> tuple[float, float]:
        prof = _EFFORT_PROFILES.get(self.effort_profile, _EFFORT_TABLE)
        cell = prof.get("table", {}).get(size, {}).get(category)
        if cell and len(cell) == 2:
            return float(cell[0]), float(cell[1])
        return effort_for(size, category)

    # -- Mode 1: exact, from a real SAP MA result -------------------------
    def assess_true_ma(self, interface_name: str, weight: int, size: str = "",
                       category: str = "") -> ComplexityResult:
        cat = _short_cat(category)
        sz = (size or weight_to_size(int(weight or 0))).upper()
        lo, hi = self._effort(sz, cat)
        return ComplexityResult(
            interface_name=interface_name, total_weight=int(weight or 0),
            size=sz, category=cat, effort_hours_low=lo, effort_hours_high=hi,
            mode="true_ma", caveats=[self.MIGRATION_CAVEAT])

    # -- Mode 2: approximation, from a bundle -----------------------------
    def assess_bundle(self, interface_name: str,
                      bundle_zip: bytes) -> ComplexityResult:
        signals = extract_signals(bundle_zip)
        return self.assess_signals(interface_name, signals)

    def assess_interface(self, record) -> ComplexityResult:
        """MODE 2b — approximation from an InterfaceRecord (no bundle, no real
        MA). Derives signals from the interface, fires the real SAP rules to get
        a scaling weight, then sizes + estimates effort on the APPROXIMATION
        calibration (its own bands + weight-driven hours). This is the primary
        S/M/L/XL + weight + effort shown in the workbench before a bundle or a
        real MA export exists. Effort scales with weight — no bucket, no cap."""
        signals = signals_from_interface(record)
        fired = fire_rules(signals)
        total = sum(f.weight for f in fired)
        rank = max((_CAT_RANK[f.category] for f in fired), default=0)
        category = _RANK_CAT[rank]
        size = approx_weight_to_size(total)
        lo, hi = approx_effort_hours(total)
        return ComplexityResult(
            interface_name=getattr(record, "name", ""), total_weight=total,
            size=size, category=category, effort_hours_low=lo,
            effort_hours_high=hi, fired_rules=fired, mode="signal",
            caveats=[self.APPROX_CAVEAT, self.MIGRATION_CAVEAT],
            signals={k: v for k, v in signals.items()
                     if not isinstance(v, set)})

    def assess_signals(self, interface_name: str,
                       signals: dict) -> ComplexityResult:
        fired = fire_rules(signals)
        total = sum(f.weight for f in fired)
        # Worst-case category across fired rules.
        rank = max((_CAT_RANK[f.category] for f in fired), default=0)
        category = _RANK_CAT[rank]
        size = weight_to_size(total)
        lo, hi = self._effort(size, category)
        return ComplexityResult(
            interface_name=interface_name, total_weight=total, size=size,
            category=category, effort_hours_low=lo, effort_hours_high=hi,
            fired_rules=fired, mode="signal",
            caveats=[self.APPROX_CAVEAT, self.MIGRATION_CAVEAT],
            signals={k: v for k, v in signals.items()
                     if not isinstance(v, set)})
