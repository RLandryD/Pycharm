"""
testing/xml_differ.py

Structural XML diff engine with configurable tolerance rules. Compares two
XML documents and decides if they're "equivalent" under a set of rules that
ignore cosmetic differences (whitespace, attribute order, namespace prefixes,
date formats) while flagging real ones (different values, missing elements,
wrong structure).

This is the load-bearing piece for off-tenant shadow testing: the
``payload_replayer``, ``xslt_executor``, and ``fixture_harness`` all end
up calling ``XmlDiffer.diff()`` to decide pass/fail.

Design choices:

- Default rules are "strict but practical" — they catch real bugs without
  failing on harmless serialiser differences. The exact ruleset can be
  tuned per-interface via ``DiffConfig``.
- The output is structured (``DiffResult`` with typed reasons) so a UI
  can present "5 cosmetic differences, 0 real differences → PASS" without
  the user having to read a diff.
- Date format tolerance handles the common SAP cases: ISO 8601, German
  ``dd.MM.yyyy``, US ``MM/dd/yyyy``, and the bare ``yyyyMMdd`` IDoc form.
  Two values are equal if they parse to the same datetime, regardless of
  surface format.
- Numeric tolerance handles "100" vs "100.00" vs "100.0" as equal.
  Doesn't handle locale-specific decimal separators yet — flag for later.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Union

from lxml import etree

logger = logging.getLogger(__name__)


class DiffSeverity(Enum):
    """How serious is this difference."""
    REAL = "real"          # Different values, missing fields — fails the test
    COSMETIC = "cosmetic"  # Whitespace, format, order — reported but not failed


@dataclass
class DiffEntry:
    """One difference found between the two documents."""
    path: str                       # XPath-like location, e.g. /Order/Line[2]/Amount
    kind: str                       # value_mismatch, missing_element, extra_element, ...
    severity: DiffSeverity
    expected: str = ""
    actual: str = ""
    note: str = ""


@dataclass
class DiffResult:
    """Outcome of a comparison. ``passed`` is True when there are no REAL diffs."""
    passed: bool
    entries: list[DiffEntry] = field(default_factory=list)
    expected_doc: str = ""
    actual_doc: str = ""

    @property
    def real_diffs(self) -> list[DiffEntry]:
        return [e for e in self.entries if e.severity == DiffSeverity.REAL]

    @property
    def cosmetic_diffs(self) -> list[DiffEntry]:
        return [e for e in self.entries if e.severity == DiffSeverity.COSMETIC]

    def summary(self) -> str:
        if self.passed:
            return (f"PASS — {len(self.cosmetic_diffs)} cosmetic differences "
                    f"(no real differences)")
        return (f"FAIL — {len(self.real_diffs)} real differences, "
                f"{len(self.cosmetic_diffs)} cosmetic")


@dataclass
class DiffConfig:
    """Tolerance rules for a diff. Edit per interface as needed.

    The defaults pass common harmless variations (whitespace, attr order,
    trailing zeros on numbers, equivalent date formats) and fail real
    semantic differences. Tighten or loosen per interface depending on
    what your receiver actually consumes.
    """
    ignore_whitespace:        bool = True   # leading/trailing/inter-element whitespace
    ignore_attribute_order:   bool = True   # XML attribute order is undefined
    ignore_namespace_prefix:  bool = True   # ns1:Foo == myns:Foo if same URI
    ignore_comments:          bool = True   # XML comments
    ignore_processing_instructions: bool = True

    # Value comparison
    normalize_numbers:        bool = True   # "100" == "100.00" == "1.0e2"
    normalize_dates:          bool = True   # ISO/German/US/IDoc forms equal if same instant
    normalize_booleans:       bool = True   # "true" == "True" == "1", "false" == "0"

    # Structural tolerance
    ignore_element_order:     bool = False  # treat <a/><b/> same as <b/><a/>? (off by default)
    optional_paths: list[str] = field(default_factory=list)  # XPaths allowed to be missing

    # Per-XPath value overrides — e.g. {"/Order/Timestamp": "ignore"} skips comparison.
    # Values: "ignore", "presence_only" (just check it exists).
    path_rules: dict[str, str] = field(default_factory=dict)


# ────────────────────────────────────────────────────────────────────────
# Value normalisation
# ────────────────────────────────────────────────────────────────────────

_DATE_PATTERNS = [
    # (regex, strptime format) — order matters, more specific first
    (re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"), "%Y-%m-%dT%H:%M:%S"),
    (re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"), "%Y-%m-%d %H:%M:%S"),
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"),                  "%Y-%m-%d"),
    (re.compile(r"^\d{2}\.\d{2}\.\d{4}$"),                "%d.%m.%Y"),  # German
    (re.compile(r"^\d{2}/\d{2}/\d{4}$"),                  "%m/%d/%Y"),  # US
    (re.compile(r"^\d{8}$"),                              "%Y%m%d"),     # IDoc
]

_BOOL_TRUE  = {"true",  "yes", "y", "1", "x"}
_BOOL_FALSE = {"false", "no",  "n", "0", ""}


def _try_parse_date(text: str) -> Optional[datetime]:
    """Return a datetime if ``text`` matches any known date format, else None."""
    s = text.strip()
    for pattern, fmt in _DATE_PATTERNS:
        if pattern.match(s):
            try:
                # Truncate to the format's expected length to handle trailing tz
                return datetime.strptime(s[:len(fmt) + 10], fmt)
            except ValueError:
                continue
    return None


def _try_parse_number(text: str) -> Optional[float]:
    """Return a float if ``text`` looks like a number, else None.

    Handles "100", "100.00", "1.5e2", "+0.5", "-100", but not locale-specific
    decimal separators like "100,50" (we'd need to know the locale)."""
    s = text.strip().replace(" ", "")
    if not s:
        return None
    if not re.match(r"^[+\-]?(\d+\.?\d*|\.\d+)([eE][+\-]?\d+)?$", s):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _values_equal(a: str, b: str, cfg: DiffConfig) -> tuple[bool, str]:
    """Compare two string values under the config's tolerance rules.

    Returns ``(equal, comparison_kind)`` where comparison_kind is one of
    "exact", "whitespace", "number", "date", "boolean" — useful for
    explaining why two values matched.
    """
    if a == b:
        return True, "exact"

    # Whitespace-tolerant compare
    if cfg.ignore_whitespace and " ".join(a.split()) == " ".join(b.split()):
        return True, "whitespace"

    a_str, b_str = a.strip(), b.strip()

    # Numeric tolerance — "100" == "100.00" == "1e2"
    if cfg.normalize_numbers:
        na, nb = _try_parse_number(a_str), _try_parse_number(b_str)
        if na is not None and nb is not None and na == nb:
            return True, "number"

    # Date tolerance — different formats representing same instant
    if cfg.normalize_dates:
        da, db = _try_parse_date(a_str), _try_parse_date(b_str)
        if da is not None and db is not None and da == db:
            return True, "date"

    # Boolean tolerance — "true"/"1"/"yes" all equal, "false"/"0"/"no" all equal
    if cfg.normalize_booleans:
        a_low, b_low = a_str.lower(), b_str.lower()
        if a_low in _BOOL_TRUE and b_low in _BOOL_TRUE:
            return True, "boolean"
        if a_low in _BOOL_FALSE and b_low in _BOOL_FALSE:
            return True, "boolean"

    return False, "differs"


# ────────────────────────────────────────────────────────────────────────
# Tree walking
# ────────────────────────────────────────────────────────────────────────

def _local_name(tag: str) -> str:
    """Strip namespace prefix from an etree tag for prefix-tolerant compare."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _build_path(element: etree._Element, root: etree._Element) -> str:
    """Build an XPath-like locator string with positional predicates."""
    parts = []
    cur = element
    while cur is not None and cur is not root.getparent():
        parent = cur.getparent()
        if parent is None:
            parts.append(_local_name(cur.tag))
            break
        # Position among same-named siblings
        same = [s for s in parent if _local_name(s.tag) == _local_name(cur.tag)]
        if len(same) == 1:
            parts.append(_local_name(cur.tag))
        else:
            idx = same.index(cur) + 1
            parts.append(f"{_local_name(cur.tag)}[{idx}]")
        cur = parent
    return "/" + "/".join(reversed(parts))


# ────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────

class XmlDiffer:
    """Structural XML differ with configurable tolerance rules.

    Usage:

        differ = XmlDiffer()  # default config — strict but practical
        result = differ.diff(expected_xml_str, actual_xml_str)
        print(result.summary())
        if not result.passed:
            for entry in result.real_diffs:
                print(f"  {entry.path}: {entry.note}")
    """

    def __init__(self, config: Optional[DiffConfig] = None):
        self.config = config or DiffConfig()

    def diff(
        self,
        expected: Union[str, bytes, Path],
        actual:   Union[str, bytes, Path],
    ) -> DiffResult:
        """Compare two XML documents. Inputs can be strings, bytes, or paths."""
        expected_tree = self._parse(expected)
        actual_tree   = self._parse(actual)

        entries: list[DiffEntry] = []
        self._compare_elements(expected_tree, actual_tree, expected_tree, entries)

        result = DiffResult(
            passed=not any(e.severity == DiffSeverity.REAL for e in entries),
            entries=entries,
            expected_doc=etree.tostring(expected_tree, pretty_print=True).decode(),
            actual_doc=etree.tostring(actual_tree, pretty_print=True).decode(),
        )
        return result

    def _parse(self, src: Union[str, bytes, Path]) -> etree._Element:
        """Parse XML from str/bytes/Path. Whitespace handling per config."""
        if isinstance(src, Path):
            src = src.read_bytes()
        elif isinstance(src, str):
            src = src.encode("utf-8") if not src.lstrip().startswith("<") else src
            if isinstance(src, str):
                src = src.encode("utf-8")
        parser = etree.XMLParser(
            remove_blank_text=self.config.ignore_whitespace,
            remove_comments=self.config.ignore_comments,
            remove_pis=self.config.ignore_processing_instructions,
        )
        return etree.fromstring(src, parser=parser)

    def _compare_elements(
        self,
        expected_el: etree._Element,
        actual_el:   etree._Element,
        root:        etree._Element,
        entries:     list[DiffEntry],
    ) -> None:
        """Recursive comparison. Appends DiffEntry as differences are found."""
        cfg = self.config
        path = _build_path(expected_el, root)

        # Apply per-path overrides
        rule = cfg.path_rules.get(path)
        if rule == "ignore":
            return  # skip this subtree entirely
        # presence_only: skip value/child comparison, just confirm existence
        if rule == "presence_only":
            return

        # Tag comparison
        exp_tag = _local_name(expected_el.tag) if cfg.ignore_namespace_prefix else expected_el.tag
        act_tag = _local_name(actual_el.tag)   if cfg.ignore_namespace_prefix else actual_el.tag
        if exp_tag != act_tag:
            entries.append(DiffEntry(
                path=path, kind="tag_mismatch", severity=DiffSeverity.REAL,
                expected=exp_tag, actual=act_tag,
                note=f"Element tag differs: <{exp_tag}> vs <{act_tag}>",
            ))
            return

        # Attribute comparison
        self._compare_attributes(expected_el, actual_el, path, entries)

        # Text content comparison
        exp_text = (expected_el.text or "").strip()
        act_text = (actual_el.text or "").strip()
        if exp_text or act_text:
            equal, kind = _values_equal(exp_text, act_text, cfg)
            if not equal:
                entries.append(DiffEntry(
                    path=path, kind="value_mismatch", severity=DiffSeverity.REAL,
                    expected=exp_text, actual=act_text,
                    note=f"Value differs: '{exp_text}' vs '{act_text}'",
                ))
            elif kind != "exact":
                entries.append(DiffEntry(
                    path=path, kind=f"value_normalised_{kind}",
                    severity=DiffSeverity.COSMETIC,
                    expected=exp_text, actual=act_text,
                    note=f"Same value, different surface form ({kind})",
                ))

        # Children comparison
        self._compare_children(expected_el, actual_el, root, entries, path)

    def _compare_attributes(
        self,
        expected_el: etree._Element,
        actual_el:   etree._Element,
        path:        str,
        entries:     list[DiffEntry],
    ) -> None:
        """Compare attributes element-wise. Order ignored if configured."""
        exp_attrs = dict(expected_el.attrib)
        act_attrs = dict(actual_el.attrib)

        if not self.config.ignore_attribute_order:
            # If order matters, compare key sequences (rare in real-world)
            if list(exp_attrs.keys()) != list(act_attrs.keys()):
                entries.append(DiffEntry(
                    path=path, kind="attribute_order",
                    severity=DiffSeverity.REAL,
                    expected=str(list(exp_attrs.keys())),
                    actual=str(list(act_attrs.keys())),
                    note="Attribute order differs",
                ))
                return

        for key, exp_val in exp_attrs.items():
            if key not in act_attrs:
                entries.append(DiffEntry(
                    path=path, kind="missing_attribute",
                    severity=DiffSeverity.REAL,
                    expected=f"{key}={exp_val}", actual="(missing)",
                    note=f"Attribute '{key}' missing from actual",
                ))
                continue
            equal, kind = _values_equal(exp_val, act_attrs[key], self.config)
            if not equal:
                entries.append(DiffEntry(
                    path=path, kind="attribute_value_mismatch",
                    severity=DiffSeverity.REAL,
                    expected=f"{key}={exp_val}",
                    actual=f"{key}={act_attrs[key]}",
                    note=f"Attribute '{key}' differs",
                ))

        for key in act_attrs:
            if key not in exp_attrs:
                entries.append(DiffEntry(
                    path=path, kind="extra_attribute",
                    severity=DiffSeverity.REAL,
                    expected="(missing)", actual=f"{key}={act_attrs[key]}",
                    note=f"Attribute '{key}' not expected",
                ))

    def _compare_children(
        self,
        expected_el: etree._Element,
        actual_el:   etree._Element,
        root:        etree._Element,
        entries:     list[DiffEntry],
        path:        str,
    ) -> None:
        """Compare children. Matching strategy: by tag+position, or by tag
        only if ignore_element_order is set."""
        exp_kids = list(expected_el)
        act_kids = list(actual_el)

        if self.config.ignore_element_order:
            # Match by local name (best-effort — repeated tags use index)
            self._compare_unordered(exp_kids, act_kids, root, entries, path)
            return

        # Positional comparison
        for i, exp_kid in enumerate(exp_kids):
            if i >= len(act_kids):
                kid_path = f"{path}/{_local_name(exp_kid.tag)}"
                # Check whether this missing child is in optional_paths
                if kid_path in self.config.optional_paths:
                    continue
                entries.append(DiffEntry(
                    path=kid_path, kind="missing_element",
                    severity=DiffSeverity.REAL,
                    expected=f"<{_local_name(exp_kid.tag)}/>", actual="(missing)",
                    note="Element present in expected but missing in actual",
                ))
                continue
            self._compare_elements(exp_kid, act_kids[i], root, entries)

        for j in range(len(exp_kids), len(act_kids)):
            extra = act_kids[j]
            entries.append(DiffEntry(
                path=f"{path}/{_local_name(extra.tag)}",
                kind="extra_element", severity=DiffSeverity.REAL,
                expected="(missing)", actual=f"<{_local_name(extra.tag)}/>",
                note="Extra element in actual not present in expected",
            ))

    def _compare_unordered(
        self,
        exp_kids: list[etree._Element],
        act_kids: list[etree._Element],
        root:     etree._Element,
        entries:  list[DiffEntry],
        path:     str,
    ) -> None:
        """Match children by tag name, ignoring sibling order. Used when
        ``ignore_element_order`` is set."""
        # Bucket by local-name
        from collections import defaultdict
        exp_by_name: dict[str, list[etree._Element]] = defaultdict(list)
        act_by_name: dict[str, list[etree._Element]] = defaultdict(list)
        for k in exp_kids:
            exp_by_name[_local_name(k.tag)].append(k)
        for k in act_kids:
            act_by_name[_local_name(k.tag)].append(k)

        all_names = set(exp_by_name) | set(act_by_name)
        for name in all_names:
            exp_group = exp_by_name.get(name, [])
            act_group = act_by_name.get(name, [])
            for i in range(max(len(exp_group), len(act_group))):
                if i >= len(exp_group):
                    entries.append(DiffEntry(
                        path=f"{path}/{name}[{i+1}]",
                        kind="extra_element", severity=DiffSeverity.REAL,
                        actual=f"<{name}/>",
                        note="Extra element (unordered mode)",
                    ))
                elif i >= len(act_group):
                    if f"{path}/{name}" in self.config.optional_paths:
                        continue
                    entries.append(DiffEntry(
                        path=f"{path}/{name}[{i+1}]",
                        kind="missing_element", severity=DiffSeverity.REAL,
                        expected=f"<{name}/>",
                        note="Missing element (unordered mode)",
                    ))
                else:
                    self._compare_elements(exp_group[i], act_group[i], root, entries)
