"""Non-IFlow package artifact parsers.

Decoded from real exports (2026-06-11 big batch: 55 ValueMappings across
C4C/EC standard content, ScriptCollections from Data Space + EC packages,
3 standalone MessageMapping artifacts).

ValueMapping bundle = {.project, MANIFEST.MF, value_mapping.xml} where the
XML is:  <vm version="2.0"><group id=..><entry><agency/><schema/><value/>
</entry>...</group>...</vm>
Each group is one logical mapping row across N agency/schema spaces — the
same model as PI's value mapping tables, which makes this parser the direct
conversion target for PI VM content.

ScriptCollection bundle = {.project, MANIFEST.MF, src/main/resources/script/*}
— scripts referenced cross-artifact from iFlows.
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass, field

logger = logging.getLogger("extractor.package_artifacts")


@dataclass
class ValueMappingEntry:
    agency: str
    schema: str
    value: str


@dataclass
class ValueMappingGroup:
    group_id: str
    entries: list = field(default_factory=list)


@dataclass
class ParsedValueMapping:
    name: str = ""
    groups: list = field(default_factory=list)

    @property
    def agencies(self) -> list:
        seen = []
        for g in self.groups:
            for e in g.entries:
                key = (e.agency, e.schema)
                if key not in seen:
                    seen.append(key)
        return seen


def parse_value_mapping(bundle_bytes: bytes, name: str = "") -> ParsedValueMapping:
    """Parse a ValueMapping artifact bundle (zip) into groups/entries."""
    pm = ParsedValueMapping(name=name)
    z = zipfile.ZipFile(io.BytesIO(bundle_bytes))
    vm_files = [n for n in z.namelist() if n.endswith("value_mapping.xml")]
    if not vm_files:
        logger.warning("ValueMapping bundle %s has no value_mapping.xml", name)
        return pm
    text = z.read(vm_files[0]).decode("utf-8", "replace")
    for gm in re.finditer(r'<group id="([^"]*)">(.*?)</group>', text, re.S):
        grp = ValueMappingGroup(group_id=gm.group(1))
        for em in re.finditer(
                r"<entry>\s*<agency>(.*?)</agency>\s*<schema>(.*?)</schema>"
                r"\s*<value>(.*?)</value>\s*</entry>", gm.group(2), re.S):
            grp.entries.append(ValueMappingEntry(*map(str.strip, em.groups())))
        pm.groups.append(grp)
    return pm


@dataclass
class ParsedScriptCollection:
    name: str = ""
    scripts: dict = field(default_factory=dict)   # {filename: source text}

    @property
    def script_names(self) -> list:
        return sorted(self.scripts)


def parse_script_collection(bundle_bytes: bytes,
                            name: str = "") -> ParsedScriptCollection:
    """Parse a ScriptCollection artifact bundle into {script name: source}."""
    sc = ParsedScriptCollection(name=name)
    z = zipfile.ZipFile(io.BytesIO(bundle_bytes))
    for n in z.namelist():
        if n.endswith((".groovy", ".gsh", ".js")) and "/script/" in n:
            sc.scripts[n.rsplit("/", 1)[-1]] = z.read(n).decode(
                "utf-8", "replace")
    return sc


# Artifact types observed in real package exports, with what we do for each.
KNOWN_ARTIFACT_TYPES = {
    "IFlow":           "parse + reproduce",
    "ValueMapping":    "parse_value_mapping",
    "ScriptCollection": "parse_script_collection",
    "MessageMapping":  "bundle of .mmap — mmap_parser per file",
    "ContentPackage":  "package descriptor",
    "Url":             "link-only, no payload",
    "File":            "opaque document",
    "PartnerLogo":     "cosmetic",
    "thumbnail":       "cosmetic",
}
