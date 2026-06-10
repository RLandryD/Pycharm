"""transform_diff.py — feed a mock XML payload through the ORIGINAL mapping and
through the REPRODUCED mapping, and compare outputs. Divergence pinpoints where
the resolver shipped the wrong (or a synthetic) file; a match proves the
reproduced mapping behaves like the original for that input.

Drives "better schema-resolvers": a resolver is only correct if the file it
resolves transforms a sample the same way the original does. XSLT mappings are
executable here (lxml, XSLT 1.0); message mappings (.mmap) are CPI-specific and
not locally executable — reported as not-comparable rather than guessed.
"""
from __future__ import annotations

from dataclasses import dataclass

try:
    from lxml import etree
    _HAVE_LXML = True
except Exception:                       # pragma: no cover
    _HAVE_LXML = False


@dataclass
class DiffResult:
    comparable: bool                    # could both be executed?
    match: bool = False                 # identical output for the sample input?
    out_original: str = ""
    out_reproduced: str = ""
    note: str = ""


def _canon(xml_text: str) -> str:
    """Canonical form so cosmetic whitespace/indent/attr-order differences don't
    show as false divergence (e.g. xsl:output indent='yes')."""
    try:
        parser = etree.XMLParser(remove_blank_text=True)
        return etree.tostring(
            etree.fromstring(xml_text.encode(), parser), method="c14n").decode()
    except Exception:
        return " ".join(xml_text.split())


def run_xslt(payload_xml: str, xsl: str) -> str:
    transform = etree.XSLT(etree.fromstring(xsl.encode()))
    return str(transform(etree.fromstring(payload_xml.encode())))


def compare_mappings(payload_xml: str, original: str, reproduced: str,
                     kind: str = "xsl") -> DiffResult:
    """Run `payload_xml` through both mappings and compare. `kind` is the
    resource type; only xsl/xslt are executable locally."""
    if kind not in ("xsl", "xslt"):
        return DiffResult(comparable=False,
                          note=f"{kind} not locally executable (CPI-specific)")
    if not _HAVE_LXML:
        return DiffResult(comparable=False, note="lxml not available")
    try:
        a = run_xslt(payload_xml, original)
    except Exception as e:
        return DiffResult(comparable=False, note=f"original failed: {e}")
    try:
        b = run_xslt(payload_xml, reproduced)
    except Exception as e:
        return DiffResult(comparable=True, match=False, out_original=a,
                          note=f"reproduced failed to run: {e}")
    return DiffResult(comparable=True, match=_canon(a) == _canon(b),
                      out_original=a, out_reproduced=b)


def diff_from_schema(xsd: str, original: str, reproduced: str,
                     kind: str = "xsl") -> DiffResult:
    """Generate a mock payload from the input schema, then diff the two
    mappings against it — the full 'mock-body tests the schema processing' loop."""
    from scaffolder.sample_payload import sample_payload_from_xsd
    return compare_mappings(sample_payload_from_xsd(xsd), original, reproduced,
                            kind=kind)
