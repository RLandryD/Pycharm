"""
testing/xslt_executor.py

Local XSLT execution harness using lxml. Runs a stylesheet against an input
XML payload and returns the output — no CPI tenant required. Used by the
fixture harness to validate "the XSLT we generated produces the expected
result" without ever deploying.

Why lxml: CPI's XSLT runtime is also XSLT 1.0 (with a few extensions for
SAP-specific lookups). lxml uses libxslt, which is the same engine many
SAP-adjacent tools use. For standard transformations the output matches
what CPI would produce. For SAP-specific extensions (e.g. ``valuemap:get``)
those need stubbing — see ``SAPExtensions``.

Limitations to be honest about:

- XSLT 2.0+ features are NOT supported (lxml uses libxslt which is 1.0 only).
  Most PI/PO XSLT is 1.0 so this matches reality, but if a consultant wrote
  XSLT 2.0 for CPI, the harness will fail on it. Flag this in the result.
- SAP-specific extension functions (``valuemap``, ``MappingTrace``, etc.)
  are stubbed with sensible defaults. Real values need to be supplied via
  the ``extensions`` argument when those lookups must be exercised.
- The harness does NOT execute Java/Groovy steps invoked from XSLT. If the
  stylesheet calls out to ``java:`` namespace functions, the call fails and
  is surfaced as a real test error — which is correct behaviour, since CPI
  wouldn't run that XSLT either.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from lxml import etree

logger = logging.getLogger(__name__)


@dataclass
class XsltExecutionResult:
    """Outcome of running an XSLT against an input payload."""
    success: bool
    output_xml: str = ""
    error_message: str = ""
    warnings: list[str] = field(default_factory=list)
    # Carry the parsed result tree for direct comparison without re-parsing
    output_tree: Optional[etree._ElementTree] = None


# ────────────────────────────────────────────────────────────────────────
# SAP-specific extension stubs
# ────────────────────────────────────────────────────────────────────────

class SAPExtensions:
    """Stub implementations of SAP CPI-specific XSLT extension functions.

    The default implementations return safe values (empty strings, the
    input value unchanged, etc.) so transformations don't crash. For tests
    that exercise these extensions, supply real values via the constructor.

    Common extension namespaces:
      - ``http://sap.com/it/ValueMapping``  -> valuemap:get(...)
      - ``http://sap.com/xi/XI/Mapping``    -> XIMapping:* helpers
      - ``http://sap.com/xi/XI/SystemLocal`` -> message header access
    """

    def __init__(
        self,
        value_maps: Optional[dict] = None,
        properties: Optional[dict] = None,
        headers:    Optional[dict] = None,
    ):
        # value_maps: {"agency:scheme:source_value": "target_value"}
        self.value_maps = value_maps or {}
        self.properties = properties or {}
        self.headers    = headers or {}

    @staticmethod
    def _to_str(arg) -> str:
        """Coerce an XSLT-passed value to a plain string.

        lxml passes node-set arguments as either a list of Element objects
        (when the XPath selects nodes) or strings (when the XPath is a
        literal). Both forms need to come out as the textual value the
        SAP extension would have seen at runtime.
        """
        if isinstance(arg, list):
            if not arg:
                return ""
            first = arg[0]
            if hasattr(first, "text"):
                return (first.text or "").strip()
            return str(first)
        if hasattr(arg, "text"):
            return (arg.text or "").strip()
        return str(arg) if arg is not None else ""

    def value_map_get(self, _, src_agency, src_scheme, tgt_agency, tgt_scheme, src_value):
        """``<xsl:value-of select="valuemap:get(...)" />``"""
        src_value_str = self._to_str(src_value)
        key = f"{self._to_str(src_agency)}:{self._to_str(src_scheme)}:{src_value_str}"
        if key not in self.value_maps:
            logger.warning("Value map lookup missed: %s — returning source value", key)
            return src_value_str
        return str(self.value_maps[key])

    def get_property(self, _, name):
        return str(self.properties.get(self._to_str(name), ""))

    def get_header(self, _, name):
        return str(self.headers.get(self._to_str(name), ""))


# ────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────

class XsltExecutor:
    """Runs an XSLT stylesheet against an input XML payload.

    Usage:

        executor = XsltExecutor()
        result = executor.run(stylesheet_path, input_xml)
        if result.success:
            print(result.output_xml)
        else:
            print(f"Failed: {result.error_message}")

    With SAP extension support:

        ext = SAPExtensions(value_maps={"ERP:Country:DE": "Germany"})
        executor = XsltExecutor(extensions=ext)
    """

    def __init__(self, extensions: Optional[SAPExtensions] = None):
        self.extensions = extensions or SAPExtensions()
        # Build the lxml extension function registration table
        self._ext_funcs = self._build_extension_map()

    def _build_extension_map(self) -> dict:
        """Map (namespace, local-name) -> callable for lxml XSLT extensions."""
        ext = self.extensions
        return {
            ("http://sap.com/it/ValueMapping", "get"): ext.value_map_get,
            # Aliases — different SAP versions use different namespaces
            ("http://sap.com/xi/XI/Mapping",   "get"): ext.value_map_get,
            ("http://sap.com/xi/XI/SystemLocal", "getProperty"): ext.get_property,
            ("http://sap.com/xi/XI/SystemLocal", "getHeader"):   ext.get_header,
        }

    def run(
        self,
        stylesheet: Union[str, bytes, Path],
        input_xml:  Union[str, bytes, Path],
        params:     Optional[dict] = None,
    ) -> XsltExecutionResult:
        """Apply ``stylesheet`` to ``input_xml`` and return the result.

        ``params`` are XSL parameters passed as ``key=value`` to lxml.
        Values are auto-quoted as strings; if you need a node-set or
        number param, build the lxml object yourself.
        """
        warnings_list: list[str] = []

        try:
            xslt_tree = self._parse(stylesheet)
        except etree.XMLSyntaxError as exc:
            return XsltExecutionResult(
                success=False,
                error_message=f"Stylesheet is not valid XML: {exc}",
            )
        except Exception as exc:
            return XsltExecutionResult(
                success=False,
                error_message=f"Could not read stylesheet: {exc}",
            )

        # Detect XSLT 2.0+ features that lxml/libxslt won't handle
        version = xslt_tree.get("version", "1.0")
        if version not in ("1.0", "1"):
            warnings_list.append(
                f"Stylesheet declares version={version!r}. lxml only supports "
                f"XSLT 1.0 — this transformation may fail or behave differently "
                f"than CPI. If CPI itself supports 2.0, validate manually.")

        try:
            transform = etree.XSLT(xslt_tree, extensions=self._ext_funcs)
        except etree.XSLTParseError as exc:
            return XsltExecutionResult(
                success=False,
                error_message=f"Stylesheet parse error: {exc}",
                warnings=warnings_list,
            )

        try:
            input_tree = self._parse(input_xml)
        except etree.XMLSyntaxError as exc:
            return XsltExecutionResult(
                success=False,
                error_message=f"Input payload is not valid XML: {exc}",
                warnings=warnings_list,
            )

        # Format params for lxml — strings need XPath-quoting
        xslt_params = {}
        for k, v in (params or {}).items():
            if isinstance(v, str):
                xslt_params[k] = etree.XSLT.strparam(v)
            else:
                xslt_params[k] = v

        try:
            result_tree = transform(input_tree, **xslt_params)
        except etree.XSLTApplyError as exc:
            return XsltExecutionResult(
                success=False,
                error_message=f"Transformation runtime error: {exc}",
                warnings=warnings_list,
            )

        # Any messages from <xsl:message> end up in transform.error_log
        for entry in transform.error_log:
            warnings_list.append(f"{entry.level_name}: {entry.message}")

        return XsltExecutionResult(
            success=True,
            output_xml=str(result_tree),
            warnings=warnings_list,
            output_tree=result_tree,
        )

    @staticmethod
    def _parse(src: Union[str, bytes, Path]) -> etree._Element:
        """Parse XML from str/bytes/Path."""
        if isinstance(src, Path):
            return etree.parse(str(src)).getroot()
        if isinstance(src, str):
            src = src.encode("utf-8")
        return etree.fromstring(src)
