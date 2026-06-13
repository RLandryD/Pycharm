"""library_builder/mmap_parser.py

Parse a .mmap back into a logical MappingSpec (the inverse of mmap_generator).

This is the round-trip foundation:
    real mmap --parse--> spec --generate--> mmap'   (should match real mmap)

A clean round-trip proves two things at once:
  1. we can READ any mmap into our structured model (what the capability
     catalog needs), and
  2. our model is COMPLETE enough to write it back faithfully.

Where round-trip differs from the original, the difference IS the learning:
it tells us which structural feature our model doesn't yet capture.

The parser walks the <transformation> brick trees and reconstructs, per target
field, the function/source/const expression tree, plus the schema bindings and
namespace from the envelope.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as _field


@dataclass
class ParsedNode:
    """A node in a parsed mapping expression tree."""
    kind: str                      # "src" | "const" | "func"
    value: str = ""                # path (src), literal (const), or fname (func)
    pin: str = ""                  # pin attribute on the <arg> wrapping this node
    args: list = _field(default_factory=list)   # child ParsedNodes (for func)
    bindings: list = _field(default_factory=list)  # [(param_name, value_xml)]


@dataclass
class ParsedField:
    target_path: str
    tree: "ParsedNode | None"
    raw: str = ""                  # the raw Dst brick (for fidelity checks)


@dataclass
class ParsedMmap:
    source_message: str = ""
    source_file: str = ""
    source_root: str = ""
    source_namespace: str = ""
    target_message: str = ""
    target_file: str = ""
    target_root: str = ""
    target_namespace: str = ""
    schema_type: str = "xsd"
    namespace_prefix_uri: str = ""
    fields: list = _field(default_factory=list)   # ParsedField


# ---- low-level brick-tree tokenizer ---------------------------------------
# We need a real tree walk because bricks nest arbitrarily. Tokenize the tags
# we care about and build a stack.
_TAG = re.compile(
    r'<brick\b([^>]*?)(/?)>'      # 1=attrs 2=selfclose
    r'|</brick>'
    r'|<arg\b([^>]*?)>'           # 3=arg attrs
    r'|</arg>'
    r'|<bindings>(.*?)</bindings>'  # 4=bindings inner (non-greedy, no nested bindings)
    r'|<group\s*/>'
    , re.S)


def _attr(attrs: str, name: str) -> str:
    m = re.search(rf'{name}="([^"]*)"', attrs)
    return m.group(1) if m else ""


def _parse_bindings(inner: str):
    out = []
    for m in re.finditer(r'<param name="([^"]+)">(.*?)</param>', inner, re.S):
        out.append((m.group(1), m.group(2)))
    return out


def _parse_brick_tree(segment: str):
    """Parse one Dst brick's content into a ParsedNode tree.
    `segment` is the full Dst <brick ...>...</brick> string."""
    pos = 0
    # stack entries: ('brick', node) | ('arg', pin)
    stack = []
    root = None
    pending_pin = ""
    for m in _TAG.finditer(segment):
        tok = m.group(0)
        if tok.startswith("<brick"):
            attrs, selfclose = m.group(1), m.group(2)
            typ = _attr(attrs, "type")
            if typ == "Src":
                node = ParsedNode("src", _attr(attrs, "path"), pin=pending_pin)
            elif typ == "Func":
                node = ParsedNode("func", _attr(attrs, "fname"), pin=pending_pin)
            elif typ == "Dst":
                node = ParsedNode("func", "__DST__", pin="")  # placeholder root
                node.value = "__DST__"
            else:
                node = ParsedNode("src", _attr(attrs, "path"), pin=pending_pin)
            pending_pin = ""
            # attach to parent func if any
            for entry in reversed(stack):
                if entry[0] == "brick" and entry[1].kind == "func":
                    entry[1].args.append(node)
                    break
            if root is None:
                root = node
            if not selfclose:
                stack.append(("brick", node))
        elif tok == "</brick>":
            # pop the latest brick
            for i in range(len(stack) - 1, -1, -1):
                if stack[i][0] == "brick":
                    stack.pop(i)
                    break
        elif tok.startswith("<arg"):
            pending_pin = _attr(m.group(3), "pin")
        elif tok == "</arg>":
            pending_pin = ""
        elif tok.startswith("<bindings>"):
            # attach to nearest open func brick
            for entry in reversed(stack):
                if entry[0] == "brick" and entry[1].kind == "func":
                    entry[1].bindings = _parse_bindings(m.group(4))
                    break
    return root


# ---- field extraction ------------------------------------------------------
def _split_dst_bricks(transformation: str):
    """Yield each top-level Dst brick segment from the transformation body."""
    # top-level Dst bricks start with <brick gid="0" path="/...Target..." type="Dst">
    # find their spans by locating each Dst start and slicing to the next one.
    # Attribute order varies by authoring tool: CPI's editor writes
    # gid/path/type; PI-authored exports (observed in Figaf's PI content,
    # CRLF pretty-printed) write type/gid/path. Match order-agnostically.
    starts = [m.start() for m in re.finditer(
        r'<brick\b(?=[^>]*\btype="Dst")(?=[^>]*\bgid="0")[^>]*>',
        transformation, re.S)]
    # also include Dst bricks that may be nested-target parents; we take all
    # top-level by tracking only those not inside another (approx: all at the
    # positions found, sliced consecutively — adequate for flat field lists).
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(transformation)
        yield transformation[s:e]


def parse_mmap(text: str) -> ParsedMmap:
    pm = ParsedMmap()
    # schema bindings
    for role in ("SOURCE_IFR_MESS", "TARGET_IFR_MESS"):
        m = re.search(
            rf'role="{role}">\s*<lnk[^>]*>\s*<key typeID="([^"]+)"[^>]*>'
            rf'(.*?)</key>', text, re.S)
        if not m:
            continue
        stype = m.group(1)
        elems = re.findall(r"<elem>([^<]*)</elem>", m.group(2))
        if role.startswith("SOURCE"):
            pm.schema_type = stype
            if len(elems) >= 3:
                pm.source_file, _, pm.source_root = elems[0], elems[1], elems[2]
            if len(elems) >= 4:
                pm.source_namespace = elems[3]
        else:
            if len(elems) >= 3:
                pm.target_file, _, pm.target_root = elems[0], elems[1], elems[2]
            if len(elems) >= 4:
                pm.target_namespace = elems[3]
    # namespace prefix
    nm = re.search(r'<namespaces>\s*<properties>\s*<property '
                   r'name="([^"]+)">\s*(\w+)\s*</property>', text)
    if nm:
        pm.namespace_prefix_uri = nm.group(1)
    # transformation body
    tm = re.search(r"<transformation>(.*)</transformation>", text, re.S)
    body = tm.group(1) if tm else ""
    for seg in _split_dst_bricks(body):
        tp = _attr(seg[:200], "path")
        tree = _parse_brick_tree(seg)
        pm.fields.append(ParsedField(target_path=tp, tree=tree, raw=seg))
    return pm
