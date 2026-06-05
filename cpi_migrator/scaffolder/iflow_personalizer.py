#!/usr/bin/env python3
"""
iflow_personalizer.py  --  Clone-and-adapt: turn a real, known-good iFlow
inner-bundle into a PERSONALIZED one (new identity + optional script / mapping /
parameter overrides) while keeping every internal reference valid.

WHY THIS IS THE SAFE PATH
    Fully-synthesized iFlows fail the tenant's import validation (the frontier).
    A package built with THIS recipe — a real iFlow re-skinned, scripts/transforms
    swapped, data-contract files left intact — was accepted by the tenant via the
    API (hash never consulted) and is the proven way to produce a personalized,
    deployable artifact.

THE RECIPE (validated)
    - Rename ONLY the .iflw file; update MANIFEST.MF symbolic/display names and
      .project. Every other filename is preserved, so the iFlow's step references
      (script=..., mapping=...) keep resolving.
    - Replace .groovy CONTENTS (same filenames) with your logic.
    - Personalize .xslt/.xsl text; optionally tweak .mmap <description>.
    - Append to parameters.prop.
    - Leave the data contract intact: .mmap message types, .xsd, .edmx. Changing
      those changes what the flow expects and is what tends to break OPEN.

USAGE
    from scaffolder.iflow_personalizer import PersonalizationSpec, personalize_bundle
    new_bundle = personalize_bundle(real_inner_bundle_bytes,
                                    PersonalizationSpec(new_iflow_name="Z_CustomerSync_v1"))
    # then wrap with fetcher.cpi_package_export.build_export_zip / deploy via API.
"""

from __future__ import annotations

import io
import hashlib
import re
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

DEFAULT_GROOVY = (
    "import com.sap.gateway.ip.core.customdev.util.Message\n"
    "import java.util.HashMap\n\n"
    "// === Personalized by CPI Migrator :: {flow} :: {script} ===\n"
    "def Message processData(Message message) {{\n"
    "    def body = message.getBody(java.lang.String) ?: \"\"\n"
    "    message.setHeader(\"X-Migrator-Personalized\", \"{flow}\")\n"
    "    message.setProperty(\"personalizedScript\", \"{script}\")\n"
    "    return message\n"
    "}}\n"
)


def _alnum(text: str, prefix: str = "Z") -> str:
    s = re.sub(r"[^A-Za-z0-9_]", "", (text or "").replace(" ", "_"))
    if not s or not s[0].isalpha():
        s = prefix + s
    return s


def _dos_zip(members: Dict[str, bytes]) -> bytes:
    """DEFLATED + MS-DOS stamped (create_system=0), like real CPI exports."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for arcname, data in members.items():
            if isinstance(data, str):
                data = data.encode("utf-8")
            zi = zipfile.ZipInfo(arcname)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 0
            z.writestr(zi, data)
    return buf.getvalue()


@dataclass
class PersonalizationSpec:
    """How to personalize a real iFlow bundle. Only new_iflow_name is required."""
    new_iflow_name: str
    new_symbolic: Optional[str] = None          # default: alnum(new_iflow_name)
    new_project_name: Optional[str] = None       # default: new_iflow_name
    # filename (basename, e.g. "script1.groovy") -> new Groovy content
    script_overrides: Dict[str, str] = field(default_factory=dict)
    # filename (basename, e.g. "msg_mapping.mmap"/"transform.xsl") -> new content.
    # Keeps the template's filename so the iFlow's Mapping step still resolves.
    mapping_overrides: Dict[str, str] = field(default_factory=dict)
    # replace EVERY .groovy with DEFAULT_GROOVY (overrides still take precedence)
    replace_all_scripts_with_default: bool = False
    xslt_marker: Optional[str] = None            # comment injected into each XSLT
    param_appends: Optional[str] = None          # appended to parameters.prop
    mmap_description: Optional[str] = None        # set into .mmap <description>
    # Rewrite the cloned .iflw's SENDER endpoint address(es) to a path unique to
    # this iFlow, so multiple clones of the same template don't all claim the
    # same runtime address (which collides — only one can own a given path, so
    # the rest fail to start). Default ON. Base path defaults to the symbolic
    # name; receiver addresses (full URLs) are left untouched.
    # Fill empty mandatory receiver-channel attributes (e.g. an empty
    # credentialName on a basic-auth receiver) with a clearly-named placeholder,
    # so generation/build validation passes and the clone deploys instead of
    # failing with "Credential name must be specified …". The consultant then
    # sets the real alias in the editor. Default ON.
    # Complete externalized parameters the template left blank: give each clone a
    # unique value for params used in a ProcessDirect *sender* address (e.g.
    # ENDPOINT_ID in GP_{{ENDPOINT_ID}}) so deployed clones don't collide on the
    # same consumer, and fill empty credential params referenced by Basic-auth
    # receivers so the build doesn't fail "Credential name must be specified".
    # Default ON.
    complete_externalized_params: bool = True
    fill_required_receiver_attrs: bool = True
    rewrite_sender_endpoints: bool = True


def _params_to_complete(iflw_text: str):
    """Scan the .iflw and return (unique_params, credential_params):

      unique_params     — parameters used in a ProcessDirect *Sender* (consumer)
                          address, e.g. ``GP_{{ENDPOINT_ID}}``. Two deployed
                          iFlows can't both register a consumer on the same
                          ProcessDirect address, so these MUST resolve to a value
                          unique per iFlow (else the runtime rejects the second
                          with "consumer … already exists").
      credential_params — parameters used as ``credentialName`` on a Basic-auth
                          *Receiver* channel. These must be non-empty or the
                          build fails ("Credential name must be specified for
                          basic authentication in receiver channel").
    """
    uniq, cred = set(), set()
    for m in re.finditer(r"<bpmn2:messageFlow\b[^>]*>(.*?)</bpmn2:messageFlow>",
                         iflw_text, re.S):
        blk = m.group(1)

        def _p(key, _blk=blk):
            mm = re.search(r"<key>" + key + r"</key>\s*<value>(.*?)</value>",
                           _blk, re.S)
            return mm.group(1) if mm else ""

        direction = _p("direction")
        ctype = _p("ComponentType")
        addr = _p("address")
        if direction == "Sender" and ctype == "ProcessDirect":
            uniq.update(p.strip() for p in re.findall(r"\{\{([^}]+)\}\}", addr))
        if direction == "Receiver":
            basic = _p("enableBasicAuthentication").lower() == "true"
            if basic or _p("authenticationMethod") == "Basic":
                cn = _p("credentialName")
                cred.update(p.strip() for p in re.findall(r"\{\{([^}]+)\}\}", cn))
    return uniq, cred


def _complete_params(params_text: str, unique_params, cred_params,
                     uniq_token: str, cred_placeholder: str) -> str:
    """Set ``unique_params`` to a per-iFlow token and fill EMPTY ``cred_params``
    with a placeholder, inside a parameters.prop. Property keys escape spaces as
    ``\\ ``; we match on the unescaped logical name (so ``CC\\ Credential\\ Name``
    matches a ``{{CC Credential Name}}`` reference). Non-empty credential values
    are left untouched; unique params are always (re)set. Params referenced by the
    iFlow but missing from the file are appended."""
    def _logical(key: str) -> str:
        return key.replace("\\", "").strip()

    def _esc(name: str) -> str:
        return name.replace(" ", "\\ ")

    lines = params_text.splitlines()
    seen = set()
    for i, ln in enumerate(lines):
        s = ln.lstrip()
        if not s or s.startswith("#") or "=" not in ln:
            continue
        key, _, val = ln.partition("=")
        name = _logical(key)
        seen.add(name)
        if name in unique_params:
            lines[i] = f"{key}={uniq_token}"
        elif name in cred_params and not val.strip():
            lines[i] = f"{key}={cred_placeholder}"
    for n in sorted(unique_params - seen):
        lines.append(f"{_esc(n)}={uniq_token}")
    for n in sorted(cred_params - seen):
        lines.append(f"{_esc(n)}={cred_placeholder}")
    out = "\n".join(lines)
    return out + ("\n" if not out.endswith("\n") else "")


_RECEIVER_CRED_PLACEHOLDER = "CHANGE_ME_RECEIVER_CRED"


def _fill_required_receiver_attrs(iflw_text: str) -> Tuple[str, int]:
    """Fill empty mandatory receiver-channel attributes so the clone passes
    build validation. Currently targets the common, unambiguous case: an empty
    credentialName on a basic-auth receiver (the tenant rejects deploy with
    "Credential name must be specified for basic authentication in receiver
    channel"). Only EMPTY values are filled; populated ones are left untouched.
    Handles both <value></value> and <value/> representations. Returns
    (new_text, n_filled)."""
    ph = _RECEIVER_CRED_PLACEHOLDER
    n = 0

    def _fill_pair(m):
        nonlocal n
        if (m.group(2) or "").strip():          # already populated → leave
            return m.group(0)
        n += 1
        return f"{m.group(1)}{ph}{m.group(3)}"

    text, _ = re.subn(
        r"(<key>credentialName</key>\s*<value>)([^<]*)(</value>)", _fill_pair, iflw_text)
    # self-closing empty value form
    text, k = re.subn(
        r"(<key>credentialName</key>\s*)<value\s*/>", r"\g<1><value>" + ph + "</value>", text)
    n += k
    return text, n


def _unique_sender_endpoints(iflw_text: str, base: str) -> Tuple[str, int]:
    """Rewrite SENDER endpoint address paths in a cloned .iflw so they're unique
    to this iFlow. Targets two property forms:
      * <key>urlPath</key><value>/…</value>   (HTTP/HTTPS sender inbound path)
      * <key>address</key><value>/…</value>   (path-style sender address, e.g. SOAP)
    Receiver HTTP addresses are full URLs (https://…), so the leading-'/' filter
    on `address` skips them; urlPath only exists on senders. Each rewritten path
    becomes /<base> (first) then /<base>_2, /<base>_3, … so multiple sender
    channels in one iFlow stay distinct too. Returns (new_text, n_rewritten)."""
    base = re.sub(r"[^A-Za-z0-9_]", "", (base or "iflow")).lower() or "iflow"
    counter = {"n": 0}

    def _next_path() -> str:
        counter["n"] += 1
        return f"/{base}" if counter["n"] == 1 else f"/{base}_{counter['n']}"

    def _sub_urlpath(m):
        return f"{m.group(1)}{_next_path()}{m.group(3)}"

    def _sub_address(m):
        return f"{m.group(1)}{_next_path()}{m.group(3)}"

    # urlPath: always a sender inbound path
    text, n1 = re.subn(
        r"(<key>urlPath</key>\s*<value>)(/[^<]*)(</value>)", _sub_urlpath, iflw_text)
    # address: only path-style values (start with '/') — sender; skip full URLs
    text, n2 = re.subn(
        r"(<key>address</key>\s*<value>)(/[^<]*)(</value>)", _sub_address, text)
    return text, n1 + n2


def personalize_bundle(bundle_bytes: bytes, spec: PersonalizationSpec) -> bytes:
    """Return a new inner-bundle (bytes) personalized per `spec`.

    Invariant: the ONLY file renamed is the .iflw; all script/mapping/resource
    filenames are preserved so the iFlow's internal references stay valid.
    """
    sym = spec.new_symbolic or _alnum(spec.new_iflow_name)
    proj = spec.new_project_name or spec.new_iflow_name
    zin = zipfile.ZipFile(io.BytesIO(bundle_bytes))
    out: Dict[str, bytes] = {}

    # Pre-scan the .iflw for externalized params that need completing, and derive
    # a per-iFlow unique token (stable hash of the symbolic name) for the ones
    # that must be unique across deployed clones (ProcessDirect consumer ids).
    uniq_params, cred_params = set(), set()
    if spec.complete_externalized_params:
        for _n in zin.namelist():
            if _n.endswith(".iflw"):
                uniq_params, cred_params = _params_to_complete(
                    zin.read(_n).decode("utf-8", "replace"))
                break
    uniq_token = hashlib.md5(sym.encode("utf-8")).hexdigest()[:8]
    params_seen = False

    for name in zin.namelist():
        data = zin.read(name)
        base = name.rsplit("/", 1)[-1]

        if name.endswith(".iflw"):
            newpath = name.rsplit("/", 1)[0] + "/" + spec.new_iflow_name + ".iflw"
            t = data.decode("utf-8", "replace")
            if spec.rewrite_sender_endpoints:
                t, _n = _unique_sender_endpoints(t, sym)
            if spec.fill_required_receiver_attrs:
                t, _f = _fill_required_receiver_attrs(t)
            out[newpath] = t.encode("utf-8")
            continue

        if name == "META-INF/MANIFEST.MF":
            t = data.decode("utf-8", "replace")
            t = re.sub(r"(Bundle-SymbolicName:\s*)([^;\r\n]+)", r"\g<1>" + sym, t)
            t = re.sub(r"(Origin-Bundle-SymbolicName:\s*)([^;\r\n]+)", r"\g<1>" + sym, t)
            t = re.sub(r"(Bundle-Name:\s*)([^\r\n]+)", r"\g<1>" + spec.new_iflow_name, t)
            t = re.sub(r"(Origin-Bundle-Name:\s*)([^\r\n]+)", r"\g<1>" + spec.new_iflow_name, t)
            out[name] = t.encode("utf-8")
            continue

        if name == ".project":
            t = data.decode("utf-8", "replace")
            t = re.sub(r"<name>[^<]*</name>", f"<name>{proj}</name>", t, count=1)
            out[name] = t.encode("utf-8")
            continue

        if name.endswith(".groovy"):
            if base in spec.script_overrides:
                out[name] = spec.script_overrides[base].encode("utf-8")
            elif spec.replace_all_scripts_with_default:
                out[name] = DEFAULT_GROOVY.format(
                    flow=spec.new_iflow_name, script=base).encode("utf-8")
            else:
                out[name] = data                                  # keep real script
            continue

        if name.endswith((".mmap", ".xsl", ".xslt")) and base in spec.mapping_overrides:
            out[name] = spec.mapping_overrides[base].encode("utf-8")  # keep filename
            continue

        if name.endswith((".xslt", ".xsl")) and spec.xslt_marker:
            t = data.decode("utf-8", "replace")
            t = re.sub(r"(<xsl:stylesheet[^>]*>)",
                       r"\g<1>\n  <!-- " + spec.xslt_marker + " -->\n", t, count=1)
            out[name] = t.encode("utf-8")
            continue

        if name.endswith(".mmap") and spec.mmap_description:
            t = data.decode("utf-8", "replace")
            t = t.replace("<description/>",
                          f"<description>{spec.mmap_description}</description>", 1)
            out[name] = t.encode("utf-8")
            continue

        if name.endswith("parameters.prop"):
            params_seen = True
            t = data.decode("utf-8", "replace")
            if spec.complete_externalized_params and (uniq_params or cred_params):
                t = _complete_params(t, uniq_params, cred_params,
                                     uniq_token, _RECEIVER_CRED_PLACEHOLDER)
            if spec.param_appends:
                t = t + ("" if t.endswith("\n") else "\n") + spec.param_appends + "\n"
            out[name] = t.encode("utf-8")
            continue

        out[name] = data                                          # keep as-is

    # If the iFlow referenced params to complete but the bundle had no
    # parameters.prop, create one at the standard path so the values resolve.
    if (spec.complete_externalized_params and not params_seen
            and (uniq_params or cred_params)):
        created = _complete_params("", uniq_params, cred_params,
                                   uniq_token, _RECEIVER_CRED_PLACEHOLDER)
        out["src/main/resources/parameters.prop"] = created.encode("utf-8")

    return _dos_zip(out)


def references_intact(bundle_bytes: bytes) -> Tuple[bool, List[str]]:
    """Verify every src/main/resources/... path the .iflw references exists in the
    bundle. Returns (ok, missing_paths)."""
    z = zipfile.ZipFile(io.BytesIO(bundle_bytes))
    names = set(z.namelist())
    iflw_name = next((n for n in names if n.endswith(".iflw")), None)
    if not iflw_name:
        return False, ["<no .iflw in bundle>"]
    iflw = z.read(iflw_name).decode("utf-8", "replace").replace("&amp;", "&")
    refs = set(re.findall(
        r"src/main/resources/[^\"<>]+?\.(?:groovy|mmap|xslt|xsl|edmx|xsd)", iflw))
    missing = sorted(r for r in refs if r not in names)
    return (not missing), missing


def template_script_files(bundle_bytes: bytes) -> List[str]:
    """Return the .groovy basenames in a template bundle, in archive order."""
    z = zipfile.ZipFile(io.BytesIO(bundle_bytes))
    return [n.rsplit("/", 1)[-1] for n in z.namelist() if n.endswith(".groovy")]


def template_mapping_files(bundle_bytes: bytes) -> List[str]:
    """Return the mapping basenames (.mmap/.xsl/.xslt) in archive order."""
    z = zipfile.ZipFile(io.BytesIO(bundle_bytes))
    return [n.rsplit("/", 1)[-1] for n in z.namelist()
            if n.endswith((".mmap", ".xsl", ".xslt"))]


_AUX_HINTS = ("error", "exception", "fault", "init", "util", "helper",
              "common", "log", "trace", "audit")
_MAIN_HINTS = ("process", "main", "map", "transform", "enrich", "convert",
               "script", "handler", "body")


def _slot_priority(fname: str) -> int:
    """Rank a template script slot for receiving the MAIN generated script:
    2 = looks primary, 1 = neutral, 0 = looks auxiliary (error/init/util)."""
    low = fname.lower()
    if any(k in low for k in _AUX_HINTS):
        return 0
    if any(k in low for k in _MAIN_HINTS):
        return 2
    return 1


def _assign_scripts(template_scripts: List[str],
                    generated_scripts: List[str]) -> Dict[str, str]:
    """Map each generated script body to the best template slot.

    Smarter than positional: prefer 'primary'-looking slots (process/map/…)
    over 'auxiliary' ones (error/init/util), stable within equal priority so a
    plain template (no naming hints) still fills in archive order. One body per
    slot; extras (more generated than slots) are dropped (reported by caller)."""
    if not template_scripts or not generated_scripts:
        return {}
    ranked = sorted(range(len(template_scripts)),
                    key=lambda i: (-_slot_priority(template_scripts[i]), i))
    assignments: Dict[str, str] = {}
    for body, slot_i in zip(generated_scripts, ranked):
        assignments[template_scripts[slot_i]] = body
    return assignments


def _mapping_kind(body: str) -> Optional[str]:
    """Classify a generated mapping body so we only inject it into a matching
    slot type (never an .mmap body into an .xsl-named file)."""
    head = (body or "")[:400].lower()
    if "<xsl:stylesheet" in head or "<xsl:transform" in head:
        return "xslt"
    if "messagemapping" in head or "<mmap" in head or ".mmap" in head:
        return "mmap"
    if head.lstrip().startswith("<?xml"):
        return "mmap"   # default xml mapping descriptor
    return None


def _reskin_mmap_name(body: str, new_name: str) -> str:
    """Best-effort: point a message mapping's name at the new interface."""
    return re.sub(r'(<messageMapping\b[^>]*\bname=")[^"]*(")',
                  r"\g<1>" + new_name + r"\g<2>", body, count=1)


def clone_and_adapt(template_bundle: bytes, new_iflow_name: str, *,
                    new_symbolic: Optional[str] = None,
                    generated_scripts: Optional[List[str]] = None,
                    generated_mapping: Optional[str] = None,
                    param_appends: Optional[str] = None
                    ) -> Tuple[bytes, bool, Dict]:
    """Clone a real, importable template iFlow and adapt it for one interface.

    Scripts: each body in ``generated_scripts`` is injected into the best-matching
    template Script slot (primary slots preferred over error/util ones), keeping
    the template's filenames so the Script steps still resolve.

    Mapping: if ``generated_mapping`` is given, it's injected into the template's
    primary mapping slot of the SAME kind (.mmap body -> .mmap slot, XSLT -> .xsl),
    filename preserved so the Mapping step resolves; its name is re-skinned to the
    interface. If no same-kind slot exists, the mapping is skipped (reported), so
    we never create a type mismatch that breaks OPEN.

    Identity is re-skinned (name / symbolic / .iflw / manifest / .project); the
    data contract (XSDs, edmx, BPMN model) is left intact — the proven 201 path.

    Returns (bundle_bytes, references_ok, report).
    """
    tpl_scripts = template_script_files(template_bundle)
    tpl_maps = template_mapping_files(template_bundle)

    script_overrides = _assign_scripts(tpl_scripts, generated_scripts or [])

    mapping_overrides: Dict[str, str] = {}
    mapping_slot = None
    if generated_mapping:
        kind = _mapping_kind(generated_mapping)
        want_ext = {"mmap": ".mmap", "xslt": ".xsl"}.get(kind)
        # pick the first template mapping slot whose extension matches the kind
        for m in tpl_maps:
            if want_ext and (m.endswith(".mmap") if kind == "mmap"
                             else m.endswith((".xsl", ".xslt"))):
                mapping_slot = m
                break
        if mapping_slot:
            body = (_reskin_mmap_name(generated_mapping, new_iflow_name)
                    if kind == "mmap" else generated_mapping)
            mapping_overrides[mapping_slot] = body

    spec = PersonalizationSpec(
        new_iflow_name=new_iflow_name, new_symbolic=new_symbolic,
        script_overrides=script_overrides, mapping_overrides=mapping_overrides,
        param_appends=param_appends)
    bundle = personalize_bundle(template_bundle, spec)
    ok, missing = references_intact(bundle)

    n_gen = len(generated_scripts or [])
    report = {
        "template_scripts": tpl_scripts,
        "template_mappings": tpl_maps,
        "generated_count": n_gen,
        "scripts_injected": list(script_overrides.keys()),
        "scripts_untouched": [s for s in tpl_scripts
                              if s not in script_overrides],
        "scripts_dropped": max(0, n_gen - len(script_overrides)),
        "mapping_injected": mapping_slot,
        "mapping_skipped": bool(generated_mapping) and mapping_slot is None,
        "refs_ok": ok,
        "missing": missing,
    }
    return bundle, ok, report


if __name__ == "__main__":
    print("Library. Import PersonalizationSpec / personalize_bundle / "
          "references_intact / clone_and_adapt / template_script_files.")
