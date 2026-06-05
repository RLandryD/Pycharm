"""library_builder/mmap_generator.py

Generate a SAP message-mapping (.mmap) from a LOGICAL field-mapping spec.

Reverse-engineered from real mmaps (RCI093, SAP corpus). The spec is exactly
the shape of SAP's own "mapping definition" Excel sheet:

    source_message / source_file (wsdl|xsd) + root
    target_message / target_file (wsdl|xsd) + root
    rows of: target_path  <-  function( source_path, ... )

The mmap is a tree of <brick> elements:
    <brick path="/tgt" type="Dst">
      <arg>
        <brick fname="F" fns="dflt" type="Func">
          <arg><brick path="/src" type="Src"/></arg>
          <bindings>...constants...</bindings>
        </brick>
      </arg>
      <group/>
    </brick>

HONEST SCOPE / LIMITS
  * This emits the LOGICAL structure (bricks + schema bindings). It does NOT
    emit <viewData x y/> layout coordinates — that is the open tenant question
    (TEST 1). If the tenant requires layout, a layout pass must be added.
  * The function vocabulary mirrors SAP's standard functions (add, concat,
    iF, TransformDate, valuemap, sum, ...). Unknown functions are emitted as
    a Func brick with the given name (best-effort).
  * Correctness of WHICH field maps to WHICH (the spec rows) is the author's
    responsibility — this turns intent into a valid artifact, it does not
    decide the intent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as _field


# ---- spec model ------------------------------------------------------------
@dataclass
class MappingRow:
    target_path: str          # /ns1:Target/ns1:field
    expression: str           # add( /ns1:Src/a , /ns1:Src/b )  OR a bare src path


@dataclass
class MappingSpec:
    name: str = "generated.mmap"
    source_message: str = ""
    source_file: str = ""          # FunctionSource.wsdl / .xsd
    source_root: str = ""
    target_message: str = ""
    target_file: str = ""
    target_root: str = ""
    schema_type: str = "xsd"       # "xsd" | "wsdl"
    schema_dir: str = "src/main/resources/xsd"
    source_namespace: str = ""     # target namespace (wsdl bindings need it)
    target_namespace: str = ""
    rows: list = _field(default_factory=list)


# ---- expression parser -----------------------------------------------------
# turns  add( /a , /b )  into  (func="add", args=[expr, expr])
# a bare path  /ns1:Src/x  is a Src leaf; a quoted "k" is a constant.
_TOK = re.compile(r'\s*(?:([A-Za-z_][A-Za-z0-9_]*)\s*\(|(\))|(,)|"([^"]*)"|([^,()"]+))')


def _parse_expr(expr: str):
    """Parse a mapping expression into a nested dict tree.
    Node kinds: {'func':name,'args':[...]} | {'src':path} | {'const':value}."""
    pos = 0
    expr = expr.strip()

    def parse_atom():
        nonlocal pos
        m = _TOK.match(expr, pos)
        if not m:
            return None
        if m.group(1) is not None:          # FUNC(
            name = m.group(1)
            pos = m.end()
            args = []
            while True:
                # skip close?
                mm = _TOK.match(expr, pos)
                if mm and mm.group(2) is not None:    # )
                    pos = mm.end()
                    break
                arg = parse_atom()
                if arg is not None:
                    args.append(arg)
                mm = _TOK.match(expr, pos)
                if mm and mm.group(3) is not None:    # ,
                    pos = mm.end()
                    continue
                if mm and mm.group(2) is not None:    # )
                    pos = mm.end()
                    break
                if not mm:
                    break
            return {"func": name, "args": args}
        if m.group(4) is not None:          # "constant"
            pos = m.end()
            return {"const": m.group(4)}
        if m.group(5) is not None:          # bare path / token
            pos = m.end()
            val = m.group(5).strip()
            return {"src": val}
        return None

    node = parse_atom()
    return node if node is not None else {"src": expr}


# ---- brick emission --------------------------------------------------------
def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _vd(x: int, y: int) -> str:
    return f'<viewData x="{x}" y="{y}"/>'


# Canonical layout grid observed across real mmaps: data flows left->right,
# Src on the left, Func in the middle, Dst on the right. Coordinates only need
# to be present and role-appropriate (the tenant stores them verbatim; SAP's
# own values are auto-layout jitter around these anchors).
_X_SRC, _X_FUNC, _X_DST = 50, 135, 230


def _emit_node(node: dict, depth: int = 0, yctr: list = None) -> str:
    """Emit the brick XML for a parsed expression node, WITH canonical-grid
    viewData layout (Src left / Func middle / Dst right)."""
    if yctr is None:
        yctr = [40]
    if "src" in node:
        y = yctr[0]
        yctr[0] += 29   # step source rows down ~29px (observed spacing)
        return (f'<brick gid="0" path="{_esc(node["src"])}" type="Src">'
                f'{_vd(_X_SRC, y)}</brick>')
    if "const" in node:
        return (f'<brick fname="const" fns="dflt" type="Func">{_vd(_X_FUNC, 40)}'
                f'<bindings><param name="value"><value>{_esc(node["const"])}'
                f'</value></param></bindings></brick>')
    if "func" in node:
        name = node["func"]
        if name == "const" and len(node["args"]) == 1 \
                and "const" in node["args"][0]:
            return _emit_node(node["args"][0], depth, yctr)
        arg_children = []
        bindings = []
        const_i = 0
        arg_pos = 0
        # Functions whose constant arguments are nested <arg> child bricks
        # (a const Func brick), NOT <bindings> entries. e.g. formatByExample's
        # pattern, replaceValue's value. For these, constants take a pin slot.
        const_as_arg = name in _CONST_AS_ARG
        for a in node["args"]:
            if "const" in a and not const_as_arg:
                names = _PARAM_NAMES.get(name, [])
                pname = names[const_i] if const_i < len(names) \
                    else f"p{const_i + 1}"
                bindings.append(
                    f'<param name="{pname}"><value>{_esc(a["const"])}'
                    f'</value></param>')
                const_i += 1
            else:
                # every arg (source OR const-as-arg) takes a sequential pin:
                # first = no pin, then pin="1", pin="2", ...
                pin = f' pin="{arg_pos}"' if arg_pos > 0 else ""
                if "const" in a:
                    # emit the constant as a nested const Func brick
                    child = (f'<brick fname="const" fns="dflt" type="Func">'
                             f'{_vd(_X_FUNC, 40)}<bindings><param name="value">'
                             f'<value>{_esc(a["const"])}</value></param>'
                             f'</bindings></brick>')
                else:
                    child = _emit_node(a, depth + 1, yctr)
                arg_children.append(f"<arg{pin}>{child}</arg>")
                arg_pos += 1
        args_xml = "".join(arg_children)
        override = _structured_bindings(name)
        if override is not None:
            bind_xml = override
        else:
            bind_xml = (f"<bindings>{''.join(bindings)}</bindings>"
                        if bindings else "")
        # nested funcs shift left toward the source band
        fx = max(_X_SRC + 20, _X_FUNC - depth * 30)
        return (f'<brick fname="{_esc(_fname(name))}" fns="dflt" type="Func">'
                f'{_vd(fx, 40)}{args_xml}{bind_xml}</brick>')
    return '<brick type="Src"/>'


# Known function-specific binding parameter names (from real mmaps). Extend as
# more examples are analyzed; unknown functions fall back to positional p1,p2…
_PARAM_NAMES = {
    "counter": ["ini", "inc"],
    "const": ["value"],
    "constant": ["value"],
    "currentDate": ["oform", "calend"],
    "mapWithDefault": ["default_value"],
    "formatNumber": ["nformat", "separator"],
    "ifWithoutElse": ["keepss"],
    "ifSWithoutElse": ["keepss"],
    "CopyValue": ["nnumber"],
    "copyValue": ["nnumber"],
    "valuemap": ["srcns", "srctype", "dstns", "dsttype", "context",
                 "agency1", "schema1", "agency2", "schema2",
                 "vmstrategy", "vmdefault"],
    "FixValues": ["vmdefault", "vmstrategy", "table"],
    "TransformDate": ["iform", "oform", "calend"],
    "DateBefore": ["iform", "oform", "calend"],
    "DateAfter": ["iform", "oform", "calend"],
    "CompareDates": ["iform", "oform", "calend"],
    "replaceValue": ["value"],
    "SplitByValue": ["type"],
    "getHeader": ["value"],
    "getProperty": ["value"],
    "sort": ["comparator", "order"],
    "sortByKey": ["value", "comparator", "order"],
    "formatByExample": ["value"],
    "substring": ["start", "count"],
    "index": ["start", "inc", "type"],
}

# Excel/UI function names that differ from SAP's internal fname in the .mmap.
# (Discovered by diffing generated vs real accepted mmaps — only 'divide'->'div'
# so far; extend as more mismatches surface.)
_FUNC_ALIAS = {
    "divide": "div",
}


def _fname(name: str) -> str:
    return _FUNC_ALIAS.get(name, name)


# Functions whose constant arguments are nested <arg pin=N> const-bricks rather
# than <bindings> params (confirmed from the verified mmap). Most functions use
# bindings; only these take their constant as a pinned arg child.
_CONST_AS_ARG = {"formatByExample", "iF", "iFS"}


# Some functions require STRUCTURED xml inside <value> (not the flat display
# label the Excel shows). The editor reports "undefined could not be loaded"
# when it finds a label where it expects structure. These emit the correct
# default bindings for those functions. (Values are sensible defaults; the
# consultant tunes them in-editor — the point is a STRUCTURALLY valid mapping.)
_CALEND = ('<value><calend_props><fd>1</fd><md>1</md><le>true</le>'
           '</calend_props></value>')


def _structured_bindings(fname: str):
    """Exact structured <bindings> blocks for complex functions, copied verbatim
    from the verified tenant mmap (which opens cleanly with these). These use
    structured values (<calend_props>, <sort_comp>) and coded values that the
    Excel's display labels ('Sunday','case sensitive') cannot express."""
    f = fname
    cal = ('<param name="calend"><value><calend_props><fd>1</fd><md>1</md>'
           '<le>true</le></calend_props></value></param>')
    if f == "currentDate":
        return ('<bindings><param name="oform"><value>yyyy/MM/dd</value></param>'
                + cal + '</bindings>')
    if f in ("TransformDate", "DateBefore", "DateAfter", "CompareDates"):
        oform = "dd/MM/yyyy" if f == "TransformDate" else "yyyyMMdd"
        return (f'<bindings><param name="iform"><value>yyyyMMdd</value></param>'
                f'<param name="oform"><value>{oform}</value></param>'
                + cal + '</bindings>')
    if f == "SplitByValue":
        return '<bindings><param name="type"><value>0</value></param></bindings>'
    if f == "sort":
        return ('<bindings><param name="comparator">'
                '<value><sort_comp type="cs"/></value></param>'
                '<param name="order"><value><sort_order asc="true"/></value>'
                '</param></bindings>')
    if f == "index":
        return ('<bindings><param name="start"><value>1</value></param>'
                '<param name="inc"><value>1</value></param>'
                '<param name="type"><value>0</value></param></bindings>')
    if f == "formatNumber":
        return ('<bindings><param name="nformat"><value>00000.000</value></param>'
                '<param name="separator"><value>,</value></param></bindings>')
    if f == "valuemap":
        return ('<bindings><param name="srcns"><value>newInterfaceFunction</value>'
                '</param><param name="srctype"><value/></param>'
                '<param name="dstns"><value/></param>'
                '<param name="dsttype"><value/></param>'
                '<param name="context"><value>http://sap.com/xi/XI</value></param>'
                '<param name="agency1"><value>Sender Party</value></param>'
                '<param name="schema1"><value>Source Identifier</value></param>'
                '<param name="agency2"><value>Receiver Party</value></param>'
                '<param name="schema2"><value>Receiver Identifier</value></param>'
                '<param name="vmstrategy"><value>1</value></param>'
                '<param name="vmdefault"><value>Default Value</value></param>'
                '</bindings>')
    if f == "FixValues":
        return ('<bindings><param name="vmdefault"><value>Default value</value>'
                '</param><param name="vmstrategy"><value>1</value></param>'
                '<param name="table"><value><properties>'
                '<property name="key1">value1</property>'
                '<property name="key2">value2</property></properties></value>'
                '</param></bindings>')
    if f == "concat":
        return ('<bindings><param name="delimeter"><value> </value></param>'
                '</bindings>')
    if f == "sortByKey":
        return ('<bindings><param name="comparator">'
                '<value><sort_comp type="cs"/></value></param>'
                '<param name="order"><value><sort_order asc="true"/></value>'
                '</param></bindings>')
    return None


def _emit_row(row: MappingRow) -> str:
    tree = _parse_expr(row.expression)
    yctr = [40]                      # local y space per mapping row
    inner = _emit_node(tree, depth=0, yctr=yctr)
    return (f'<brick gid="0" path="{_esc(row.target_path)}" type="Dst">'
            f'{_vd(_X_DST, 40)}<arg>{inner}</arg><group/></brick>')


def _schema_binding(role: str, file: str, sdir: str, root: str,
                    schema_type: str, namespace: str = "") -> str:
    # WSDL bindings carry a 4th <elem> = target namespace (seen in real mmaps);
    # xsd bindings use 3 elements. Include namespace when provided.
    ns_elem = f"<elem>{_esc(namespace)}</elem>" if namespace else ""
    return (f'<lnkRole kpos="1" role="{role}"><lnk rMode="R">'
            f'<key typeID="{schema_type}" version="1.1">'
            f'<elem>{_esc(file)}</elem><elem>{_esc(sdir)}</elem>'
            f'<elem>{_esc(root)}</elem>{ns_elem}</key></lnk></lnkRole>')


# AdditionalProperties block seen in real mmaps (resolved schema flags)
_ADDITIONAL_PROPS = (
    '<AdditionalProperties xmlns="">'
    '<Property Applicable="BOTH"><PropertyName>externalNameSpace</PropertyName>'
    '<PropertyValue>RESOLVED</PropertyValue></Property>'
    '<Property Applicable="BOTH"><PropertyName>choiceOccurrence</PropertyName>'
    '<PropertyValue>RESOLVED</PropertyValue></Property>'
    '<Property Applicable="BOTH"><PropertyName>groupsOccurrence</PropertyName>'
    '<PropertyValue>RESOLVED</PropertyValue></Property>'
    '</AdditionalProperties>'
)


_ENVELOPE_HEAD = (
    '<xiObj xmlns="urn:sap-com:xi"><idInfo xmlns="" VID="01">'
    '<vc caption="LOCAL" sp="-1" swcGuid="00000000000000000000000000000000" '
    'vcType="S"><clCxt consider="A"/></vc>'
    '<key typeID="XI_TRAFO" version=""/><version>1.0</version></idInfo>'
    '<documentation xmlns=""><description/></documentation>'
    '<generic xmlns=""><admInf>'
    '<modifBy></modifBy><modifAt></modifAt><owner/></admInf><lnks>'
)
def _guid() -> str:
    """The textObj id. v3 (all-zeros) opens fine, so we keep the stable
    all-zeros id rather than a random one (changing it was not what fixed/broke
    opening — verified via isolation tests)."""
    return "00000000000000000000000000000000"


# after the schema bindings: textInfo, close generic (single empty text label —
# matches the v3 envelope that opens correctly)
def _envelope_after_lnks() -> str:
    import uuid
    lbl = uuid.uuid4().hex
    return ('</lnks><textInfo loadedL="EN"><textObj id="'
            '00000000000000000000000000000000" masterL="EN" type="0">'
            f'<texts lang="EN"><text label=""/><text label="{lbl}"></text>'
            '</texts></textObj>'
            '</textInfo></generic>')
# the libstorage scaffolding present in real mmaps (empty user-namespace fn store)
_LIBSTORAGE = (
    '<libstorage><entry name="usernamespace"><functionstorage version="XI7.1">'
    '<key><key typeID=""><elem></elem><elem></elem></key></key>'
    '<classname></classname><package></package><imports/>'
    '<globals><javaText/></globals><init><functionmodel>'
    '<signature cacheType="0"/><name></name><key></key><tab></tab>'
    '<title></title><uiTitle></uiTitle>'
    '<implementation type="udf"><javaText/></implementation>'
    '</functionmodel></init><cleanup><javaText/></cleanup><usedjars/>'
    '</functionstorage></entry></libstorage>'
)
# then content/transformation begins (with libstorage)
_ENVELOPE_MID_AFTER_PROPS = (
    '<content xmlns="">'
    '<tr:XiTrafo xmlns:tr="urn:sap-com:xi:mapping:xitrafo"><tr:MetaData>'
    '<mappingtool version="XI7.1"><project version="XI7.1">'
    + _LIBSTORAGE +
    '<transformation>'
)
# the multiplicity + source/target parameter declarations (from real mmaps)
_PARAM_DECLS = (
    '<tr:SourceParameters><tr:Parameter><tr:Position>1</tr:Position>'
    '<tr:Minoccurs>1</tr:Minoccurs><tr:Maxoccurs>1</tr:Maxoccurs>'
    '</tr:Parameter></tr:SourceParameters>'
    '<tr:TargetParameters><tr:Parameter><tr:Position>1</tr:Position>'
    '<tr:Minoccurs>1</tr:Minoccurs><tr:Maxoccurs>1</tr:Maxoccurs>'
    '</tr:Parameter></tr:TargetParameters>'
)
_ENVELOPE_TAIL = (
    '</transformation><testData><instances/></testData>'
    '<ViewState></ViewState><pcont/></project></mappingtool></tr:MetaData>'
    '<tr:ByteCodeJar/><tr:SourceStructure/><tr:TargetStructure/>'
    '<tr:Multiplicity>1:1</tr:Multiplicity>'
    + _PARAM_DECLS +
    '</tr:XiTrafo></content></xiObj>'
)


def generate_mmap(spec: MappingSpec, validate: bool = False) -> str:
    """Produce the .mmap XML text from a logical spec (no layout coords).

    If validate=True, the output is checked against the confirmed patterns
    (validate_generated_mmap) and a ValueError is raised on any violation —
    a pre-deploy gate. Defaults to False to preserve existing behavior."""
    sdir = spec.schema_dir
    if spec.schema_type == "wsdl" and sdir.endswith("/xsd"):
        sdir = sdir[:-4] + "/wsdl"     # default dir follows schema type
    tgt_bind = _schema_binding("TARGET_IFR_MESS", spec.target_file,
                               sdir, spec.target_root,
                               spec.schema_type, spec.target_namespace)
    src_bind = _schema_binding("SOURCE_IFR_MESS", spec.source_file,
                               sdir, spec.source_root,
                               spec.schema_type, spec.source_namespace)
    bricks = "".join(_emit_row(r) for r in spec.rows)
    # The <namespaces> block declares the prefix used in all field paths
    # (ns1 -> the message namespace). Without it the tenant cannot resolve
    # ns1: in the paths -> "incomplete mapping" / unresolved target fields.
    ns = spec.target_namespace or spec.source_namespace
    namespaces = ""
    if ns:
        namespaces = (f'<namespaces><properties>'
                      f'<property name="{_esc(ns)}">ns1</property>'
                      f'</properties></namespaces>')
    result = (_ENVELOPE_HEAD + tgt_bind + src_bind
              + _envelope_after_lnks() + _ADDITIONAL_PROPS
              + _ENVELOPE_MID_AFTER_PROPS
              + bricks + namespaces + _ENVELOPE_TAIL)
    if validate:
        problems = validate_generated_mmap(result)
        # the namespaces/viewData "recommended" notes are not hard failures;
        # only fail on structural problems
        hard = [p for p in problems if "recommended" not in p]
        if hard:
            raise ValueError("generated mmap violates confirmed patterns: "
                             + "; ".join(hard))
    return result


# ---- spec loader: SAP "mapping definition" Excel --------------------------
def spec_from_excel(path: str) -> MappingSpec:
    """Read a SAP mapping-definition sheet (.xls/.xlsx) into a MappingSpec.

    Recognizes the OVERVIEW block (Name/Source/Target) and the DEFINITION
    table (TARGET | MAPPING | TYPE)."""
    rows_raw = _read_sheet(path)
    spec = MappingSpec()
    in_def = False
    for r in rows_raw:
        cells = [str(c).strip() if c is not None else "" for c in r]
        if not any(cells):
            continue
        key = cells[0]
        val = cells[1] if len(cells) > 1 else ""
        low = key.lower()
        if low.startswith("name of mapping"):
            spec.name = val or spec.name
        elif low.startswith("name of the source message"):
            spec.source_message = val.strip("[]")
        elif low.startswith("name of the source file"):
            spec.source_file = val.strip("[]")
            spec.schema_type = "wsdl" if val.lower().endswith("wsdl]") \
                or val.lower().endswith("wsdl") else "xsd"
        elif low.startswith("name of the target message"):
            spec.target_message = val.strip("[]")
        elif low.startswith("name of the target file"):
            spec.target_file = val.strip("[]")
        elif key == "TARGET" and val.upper() == "MAPPING":
            in_def = True
        elif in_def and key.startswith("/"):
            spec.rows.append(MappingRow(target_path=key,
                                        expression=val))
    # derive roots from message names if not set
    spec.source_root = spec.source_root or spec.source_message
    spec.target_root = spec.target_root or spec.target_message
    return spec


def _read_sheet(path: str) -> list:
    low = path.lower()
    if low.endswith(".xls"):
        import xlrd
        wb = xlrd.open_workbook(path)
        sh = wb.sheets()[0]
        return [[sh.cell_value(r, c) for c in range(sh.ncols)]
                for r in range(sh.nrows)]
    else:
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb.worksheets[0]
        return [list(row) for row in ws.iter_rows(values_only=True)]


# ── Self-validation against confirmed patterns ─────────────────────────────
def validate_generated_mmap(mmap_text: str) -> list[str]:
    """Check a generated mmap against the tenant-CONFIRMED patterns
    (MMAP_PATTERNS_CONFIRMED.md). Returns a list of problem strings; empty =
    conforms to every confirmed pattern. Use as a pre-deploy gate."""
    import re as _re
    problems = []

    # §2 envelope required blocks
    for needed, why in [
        ("functionstorage", "libstorage scaffolding missing (§2)"),
        ("tr:SourceParameters", "SourceParameters missing (§2)"),
        ("tr:TargetParameters", "TargetParameters missing (§2)"),
        ("AdditionalProperties", "AdditionalProperties missing (§2)"),
        ("<transformation>", "transformation block missing (§2)"),
    ]:
        if needed not in mmap_text:
            problems.append(why)

    # §3 schema bindings
    if 'role="SOURCE_IFR_MESS"' not in mmap_text:
        problems.append("SOURCE_IFR_MESS binding missing (§3)")
    if 'role="TARGET_IFR_MESS"' not in mmap_text:
        problems.append("TARGET_IFR_MESS binding missing (§3)")

    # §4a pin rule: within each Func brick, args must be 0,1,2... not repeated.
    # Heuristic: no function should have two args BOTH lacking a pin beyond the
    # first, and pins should never repeat consecutively as all "1".
    # Detect the classic bug: <arg pin="1">...<arg pin="1"> inside one func.
    for fm in _re.finditer(r'<brick fname="[^"]+"[^>]*>(.*?)</brick>',
                           mmap_text):
        body = fm.group(1)
        # only direct args (rough): count pin values among direct-ish args
        pins = _re.findall(r'<arg pin="(\d+)">', body[:800])
        if pins and pins.count("1") > 1:
            problems.append("repeated pin=\"1\" in a function — multi-source "
                            "wiring bug (§4a)")
            break

    # §5 namespaces block (present in clean verified files)
    if "<namespaces>" not in mmap_text:
        problems.append("namespaces block missing (§5) — recommended")

    # §6 layout present
    if "<viewData" not in mmap_text:
        problems.append("no viewData layout (§6) — recommended")

    # well-formedness
    try:
        import xml.dom.minidom as _M
        _M.parseString(mmap_text)
    except Exception as e:
        problems.append(f"not well-formed XML: {e}")

    return problems
