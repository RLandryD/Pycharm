"""
testing/fixture_harness.py

Per-interface shadow-test harness. Reads expected-output fixture files,
runs the transformation under test (XSLT today; mapping engine and full
iFlow simulator in later phases), diffs against expected, returns
pass/fail with detailed evidence.

Fixture directory layout (one folder per interface):

    fixtures/
      MY_INTERFACE/
        config.yaml                 # tolerance rules + transform path
        transform.xsl               # the XSLT under test
        request_001.input.xml       # one test case: input payload
        request_001.expected.xml    # one test case: expected output
        request_002.input.xml       # ...
        request_002.expected.xml
        request_003.input.xml
        request_003.expected.xml

``config.yaml`` is optional. When present:

    transform: transform.xsl        # relative to fixture dir
    type: xslt                       # xslt | groovy | mapping | iflow
    diff:                            # any DiffConfig field
      ignore_element_order: false
      path_rules:
        /Order/Timestamp: ignore
    params:                          # XSLT params (optional)
      target_system: S4HANA
    extensions:                      # SAP extension stubs
      value_maps:
        ERP:Country:DE: Germany

When ``config.yaml`` is absent, defaults are used: type=xslt,
transform=transform.xsl, strict-but-practical diff rules.

This harness deliberately does NOT need a CPI tenant. The consultant writes
expected outputs once (either from a real PI/PO message capture during
discovery, or by hand for known test cases), then every iFlow regeneration
validates against the same fixtures.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from testing.xml_differ import DiffConfig, DiffResult, XmlDiffer
from testing.xslt_executor import SAPExtensions, XsltExecutionResult, XsltExecutor

logger = logging.getLogger(__name__)


@dataclass
class TestCaseResult:
    """Outcome of one input→expected pair."""
    case_name: str                          # e.g. "request_001"
    passed: bool
    diff_result: Optional[DiffResult] = None
    execution_result: Optional[XsltExecutionResult] = None
    error_message: str = ""

    @property
    def summary(self) -> str:
        if self.error_message:
            return f"ERROR — {self.error_message}"
        if not self.execution_result or not self.execution_result.success:
            return "FAIL — transformation did not produce output"
        if self.diff_result is None:
            return "FAIL — no diff result"
        return self.diff_result.summary()


@dataclass
class InterfaceTestResult:
    """All test cases for one interface."""
    interface_name: str
    fixture_dir: Path
    transform_path: Path
    transform_type: str
    cases: list[TestCaseResult] = field(default_factory=list)
    config_warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.cases) and all(c.passed for c in self.cases)

    @property
    def pass_count(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def fail_count(self) -> int:
        return sum(1 for c in self.cases if not c.passed)

    def summary(self) -> str:
        if not self.cases:
            return f"{self.interface_name}: NO TEST CASES"
        return (f"{self.interface_name}: {self.pass_count}/{len(self.cases)} cases passed "
                f"({'PASS' if self.passed else 'FAIL'})")


def _load_config(fixture_dir: Path) -> dict:
    """Read config.yaml if present. Returns empty dict if absent."""
    cfg_path = fixture_dir / "config.yaml"
    if not cfg_path.exists():
        return {}
    try:
        import yaml
        with cfg_path.open() as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        logger.warning("pyyaml not installed — fixture config ignored")
        return {}
    except Exception as exc:
        logger.warning("Failed to read %s: %s — using defaults", cfg_path, exc)
        return {}


def _build_diff_config(cfg: dict) -> DiffConfig:
    """Construct a DiffConfig from the 'diff' section of a fixture config."""
    diff_cfg = cfg.get("diff", {})
    return DiffConfig(
        ignore_whitespace=diff_cfg.get("ignore_whitespace", True),
        ignore_attribute_order=diff_cfg.get("ignore_attribute_order", True),
        ignore_namespace_prefix=diff_cfg.get("ignore_namespace_prefix", True),
        ignore_comments=diff_cfg.get("ignore_comments", True),
        ignore_processing_instructions=diff_cfg.get("ignore_processing_instructions", True),
        normalize_numbers=diff_cfg.get("normalize_numbers", True),
        normalize_dates=diff_cfg.get("normalize_dates", True),
        normalize_booleans=diff_cfg.get("normalize_booleans", True),
        ignore_element_order=diff_cfg.get("ignore_element_order", False),
        optional_paths=diff_cfg.get("optional_paths", []),
        path_rules=diff_cfg.get("path_rules", {}),
    )


def _build_extensions(cfg: dict) -> SAPExtensions:
    """Build an SAPExtensions object from a fixture config's 'extensions'."""
    ext_cfg = cfg.get("extensions", {})
    return SAPExtensions(
        value_maps=ext_cfg.get("value_maps", {}),
        properties=ext_cfg.get("properties", {}),
        headers=ext_cfg.get("headers", {}),
    )


def discover_test_cases(fixture_dir: Path) -> list[tuple[str, Path, Path]]:
    """Find ``*.input.xml`` + matching ``*.expected.xml`` pairs.

    Returns list of (case_name, input_path, expected_path). Cases without
    a matching expected file are skipped with a warning.
    """
    cases = []
    for input_path in sorted(fixture_dir.glob("*.input.xml")):
        base = input_path.name[:-len(".input.xml")]
        expected_path = fixture_dir / f"{base}.expected.xml"
        if not expected_path.exists():
            logger.warning("No expected.xml for %s — skipping", input_path.name)
            continue
        cases.append((base, input_path, expected_path))
    return cases


def run_interface_tests(
    fixture_dir: Path,
    transform_override: Optional[Path] = None,
) -> InterfaceTestResult:
    """Run all test cases in one fixture directory.

    ``transform_override`` lets the caller supply a freshly-generated
    XSLT path instead of using ``transform.xsl`` inside the fixture dir.
    This is how the workbench shadow-tests a generated iFlow's mapping
    against fixtures that were captured before the iFlow existed.
    """
    cfg = _load_config(fixture_dir)
    transform_type = cfg.get("type", "xslt")
    transform_path = transform_override or (fixture_dir / cfg.get("transform", "transform.xsl"))

    result = InterfaceTestResult(
        interface_name=fixture_dir.name,
        fixture_dir=fixture_dir,
        transform_path=transform_path,
        transform_type=transform_type,
    )

    if transform_type != "xslt":
        result.config_warnings.append(
            f"transform type '{transform_type}' not yet supported (XSLT only). "
            f"Skipping all cases.")
        return result

    if not transform_path.exists():
        result.config_warnings.append(f"Transform file not found: {transform_path}")
        return result

    diff_config = _build_diff_config(cfg)
    extensions  = _build_extensions(cfg)
    params      = cfg.get("params", {})

    executor = XsltExecutor(extensions=extensions)
    differ   = XmlDiffer(config=diff_config)

    cases = discover_test_cases(fixture_dir)
    if not cases:
        result.config_warnings.append("No *.input.xml / *.expected.xml pairs found.")
        return result

    for case_name, input_path, expected_path in cases:
        try:
            exec_result = executor.run(transform_path, input_path, params=params)
            if not exec_result.success:
                result.cases.append(TestCaseResult(
                    case_name=case_name,
                    passed=False,
                    execution_result=exec_result,
                    error_message=exec_result.error_message,
                ))
                continue

            diff_result = differ.diff(expected_path, exec_result.output_xml)
            result.cases.append(TestCaseResult(
                case_name=case_name,
                passed=diff_result.passed,
                diff_result=diff_result,
                execution_result=exec_result,
            ))
        except Exception as exc:
            logger.exception("Test case crashed: %s", case_name)
            result.cases.append(TestCaseResult(
                case_name=case_name,
                passed=False,
                error_message=f"Crashed: {exc}",
            ))

    return result


def run_all_fixtures(fixtures_root: Path) -> list[InterfaceTestResult]:
    """Walk a directory of fixture folders and run all of them.

    Each subfolder of ``fixtures_root`` is treated as one interface's
    fixture set, named after the folder.
    """
    results = []
    if not fixtures_root.exists():
        logger.warning("Fixtures root does not exist: %s", fixtures_root)
        return results
    for entry in sorted(fixtures_root.iterdir()):
        if not entry.is_dir():
            continue
        results.append(run_interface_tests(entry))
    return results


def create_fixture_skeleton(fixture_dir: Path, interface_name: str) -> None:
    """Create an empty fixture directory with a sample config + placeholder files.

    Used to scaffold new fixture sets during onboarding. Consultants edit
    the placeholder XML to match real client payloads.
    """
    fixture_dir.mkdir(parents=True, exist_ok=True)

    (fixture_dir / "config.yaml").write_text(f"""# Fixture configuration for {interface_name}
# Uncomment and edit as needed. Defaults below match strict-but-practical comparison.

transform: transform.xsl   # path to the XSLT under test, relative to this dir
type: xslt                  # xslt | groovy | mapping | iflow (only xslt today)

diff:
  ignore_whitespace: true
  ignore_attribute_order: true
  ignore_namespace_prefix: true
  normalize_numbers: true     # "100" == "100.00"
  normalize_dates: true       # "2026-01-15" == "15.01.2026"
  normalize_booleans: true    # "true" == "1"
  ignore_element_order: false # set true if your receiver doesn't care about order
  # path_rules:
  #   /Order/Timestamp: ignore        # skip this path entirely
  #   /Order/CorrelationId: presence_only  # just check it exists
  # optional_paths:
  #   - /Order/Notes                  # allowed to be missing in actual

# params:                       # XSL parameters, if your stylesheet declares any
#   target_system: S4HANA

# extensions:                   # SAP CPI extension stubs
#   value_maps:
#     ERP:Country:DE: Germany
#   properties:
#     SenderId: ECC_PROD
#   headers:
#     SAP-MessageId: test-001
""")

    (fixture_dir / "transform.xsl").write_text(f"""<?xml version="1.0"?>
<!-- Place the XSLT for {interface_name} here. The fixture harness will
     run this against each *.input.xml file and diff the output against
     the matching *.expected.xml file. -->
<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">
  <xsl:output method="xml" indent="yes"/>
  <xsl:template match="/">
    <!-- TODO: add your transformation here -->
    <xsl:copy-of select="."/>
  </xsl:template>
</xsl:stylesheet>
""")

    (fixture_dir / "request_001.input.xml").write_text(
        '<?xml version="1.0"?>\n<!-- Example input payload — replace with real client payload -->\n<Order/>\n')
    (fixture_dir / "request_001.expected.xml").write_text(
        '<?xml version="1.0"?>\n<!-- Expected output for request_001.input.xml — the PI/PO equivalent of what CPI should produce -->\n<Order/>\n')
