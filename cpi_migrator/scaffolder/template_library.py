#!/usr/bin/env python3
"""
template_library.py  --  index a folder of REAL, tested CPI packages and expose
cloneable iFlow templates for the clone-and-adapt generation route.

A "template" is one complete, importable iFlow inner-bundle (META-INF/MANIFEST.MF
+ .project + src/main/resources/scenarioflows/integrationflow/<x>.iflw + its
scripts/mappings). We harvest these from a folder that may contain, in any mix:

  * extracted iFlow project dirs   (…/<proj>/src/main/resources/.../x.iflw)
  * raw inner-bundle zips          (a zip whose root has META-INF + the .iflw)
  * package export zips            (resources.cnt + one or more <guid>_content)

For each interface we then pick the closest template (by sender/receiver adapter,
falling back to any template that has a Script step to carry injected logic),
and hand it to scaffolder.iflow_personalizer.clone_and_adapt.

Pure stdlib — unit-testable, no Streamlit, no network.
"""

from __future__ import annotations

import io
import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_IFLW_IN = "scenarioflows/integrationflow/"

# Skip templates whose inner-bundle exceeds this size. A clone base should be a
# focused flow; multi-MB bundles slow indexing AND produce bloated clones that
# inherit a lot of unrelated routing (e.g. a 2.6 MB package was being chosen for
# a simple interface). Tunable; set high to disable.
MAX_TEMPLATE_BYTES = 1_500_000


@dataclass
class Template:
    name: str                     # iFlow name (from the .iflw filename)
    source: str                   # where it came from (path[:member])
    bundle: bytes                 # the inner-bundle zip bytes (cloneable)
    has_script: bool = False
    script_count: int = 0
    sender: str = ""              # sender adapter ComponentType (e.g. HTTPS)
    receiver: str = ""            # receiver adapter ComponentType
    step_count: int = 0           # serviceTask + callActivity (processing steps)


def _adapters_and_steps(iflw_xml: str):
    comps = re.findall(r"<key>ComponentType</key>\s*<value>([^<]*)</value>", iflw_xml)
    dirs = re.findall(r"<key>direction</key>\s*<value>([^<]*)</value>", iflw_xml)
    sender = receiver = ""
    # pair ComponentType with the nearest direction is fragile; instead use the
    # participant adapter types: first Sender-ish, first Receiver-ish.
    for c, d in zip(comps, dirs):
        if d.lower().startswith("sender") and not sender:
            sender = c
        elif d.lower().startswith("receiv") and not receiver:
            receiver = c
    if comps and not sender:
        sender = comps[0]
    steps = iflw_xml.count("<bpmn2:serviceTask") + iflw_xml.count("<bpmn2:callActivity")
    return sender, receiver, steps


def _template_from_bundle(bundle: bytes, source: str) -> Optional[Template]:
    """Validate a candidate inner-bundle and build a Template, or None."""
    try:
        z = zipfile.ZipFile(io.BytesIO(bundle))
        names = z.namelist()
    except Exception:
        return None
    if not any(n.endswith("MANIFEST.MF") for n in names):
        return None
    iflw = next((n for n in names if n.endswith(".iflw") and _IFLW_IN in n), None)
    if not iflw:
        return None
    scripts = [n for n in names if n.endswith(".groovy")]
    xml = z.read(iflw).decode("utf-8", "replace")
    sender, receiver, steps = _adapters_and_steps(xml)
    return Template(
        name=iflw.rsplit("/", 1)[-1][:-5], source=source, bundle=bundle,
        has_script=bool(scripts), script_count=len(scripts),
        sender=sender, receiver=receiver, step_count=steps)


def _bundle_from_dir(project_root: str) -> bytes:
    """Zip an extracted iFlow project tree into an inner bundle (MS-DOS stamped)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _d, files in os.walk(project_root):
            for fn in files:
                full = os.path.join(root, fn)
                arc = os.path.relpath(full, project_root).replace(os.sep, "/")
                zi = zipfile.ZipInfo(arc)
                zi.create_system = 0
                zi.compress_type = zipfile.ZIP_DEFLATED
                with open(full, "rb") as fh:
                    z.writestr(zi, fh.read())
    return buf.getvalue()


def _candidates_from_zip(path: str) -> List[bytes]:
    """Return inner-bundle byte-blobs from a zip: itself if it IS a bundle, or
    each <guid>_content if it is a package export."""
    out: List[bytes] = []
    try:
        z = zipfile.ZipFile(path)
        names = z.namelist()
    except Exception:
        return out
    if any(n.endswith("MANIFEST.MF") for n in names) and \
       any(n.endswith(".iflw") for n in names):
        out.append(open(path, "rb").read())            # raw inner bundle
    if any(n == "resources.cnt" or n.endswith("/resources.cnt") for n in names):
        for n in names:                                  # package export blobs
            if n.endswith("_content"):
                out.append(z.read(n))
    return out


def index_templates(folder: str, limit: Optional[int] = None) -> List[Template]:
    """Walk `folder` and return de-duplicated cloneable iFlow templates."""
    templates: List[Template] = []
    seen_roots = set()

    for root, _dirs, files in os.walk(folder):
        # (a) extracted project: an .iflw under scenarioflows/integrationflow/
        for fn in files:
            if fn.endswith(".iflw") and _IFLW_IN.rstrip("/") in root.replace(os.sep, "/"):
                proj = root
                up = root
                for _ in range(6):                       # walk up to project root
                    up = os.path.dirname(up)
                    if os.path.exists(os.path.join(up, "META-INF", "MANIFEST.MF")):
                        proj = up
                        break
                if proj in seen_roots:
                    continue
                seen_roots.add(proj)
                t = _template_from_bundle(_bundle_from_dir(proj), proj)
                if t:
                    templates.append(t)
            # (b) zips (inner bundle or package export)
            elif fn.lower().endswith(".zip"):
                p = os.path.join(root, fn)
                for blob in _candidates_from_zip(p):
                    t = _template_from_bundle(blob, p)
                    if t:
                        templates.append(t)
        if limit and len(templates) >= limit:
            break
    # de-dup by (name, script_count, step_count); drop oversized bundles
    uniq, key = [], set()
    for t in templates:
        if len(t.bundle) > MAX_TEMPLATE_BYTES:
            continue
        k = (t.name, t.script_count, t.step_count, len(t.bundle))
        if k not in key:
            key.add(k); uniq.append(t)
    return uniq


def pick_template(templates: List[Template], *, sender: str = "", receiver: str = "",
                  require_script: bool = True, need_scripts: int = 0) -> Optional[Template]:
    """Best template for an interface: prefer adapter match, then a Script step,
    then most processing steps. When `need_scripts` is given, prefer a template
    with enough script slots to host the injected scripts and whose slot count is
    closest to the need — this differentiates interfaces that share adapters but
    carry different logic (so they stop cloning the identical template). Returns
    None if the library is empty."""
    if not templates:
        return None
    if require_script:
        pool = [t for t in templates if t.has_script] or templates
    else:
        pool = templates

    def score(t: Template) -> tuple:
        s = 0
        if t.step_count > 0:                 # a real, complete (importable) flow
            s += 10
        if sender and t.sender and sender.lower() in t.sender.lower():
            s += 2
        if receiver and t.receiver and receiver.lower() in t.receiver.lower():
            s += 2
        if t.has_script:
            s += 1
        # capability-fit: enough script slots, and the closer the better
        fit = 0
        if need_scripts > 0:
            if t.script_count >= need_scripts:
                fit += 3
            fit -= abs(t.script_count - need_scripts)   # closeness (can go neg)
        return (s + fit, s, t.step_count, t.script_count)

    return max(pool, key=score)


# ── cached, bounded single-template lookup (for the live upload path) ──────────
_BEST_CACHE: Dict[tuple, Optional[Template]] = {}


def _folder_signature(folder: str):
    """Cheap signature over the folder's immediate entries (name+mtime+size), so
    the cache invalidates when packages are added/changed but we don't restat
    thousands of nested files on every call."""
    try:
        entries = []
        for name in sorted(os.listdir(folder)):
            try:
                st = os.stat(os.path.join(folder, name))
                entries.append((name, int(st.st_mtime), st.st_size))
            except OSError:
                pass
        return hash(tuple(entries))
    except OSError:
        return None


def find_best_template(folder: str, *, sender: str = "", receiver: str = "",
                       limit: int = 200, need_scripts: int = 0) -> Optional[Template]:
    """Return ONE best template from a (possibly huge) library, cached per
    folder-signature and bounded by `limit` so the live upload path never
    re-walks thousands of packages. Prefers a real, complete flow with a Script
    step; adapter hints + `need_scripts` (slots to host injected scripts) refine
    the choice so same-adapter interfaces don't all clone the same template."""
    sig = _folder_signature(folder)
    key = (folder, sender, receiver, limit, need_scripts, sig)
    if key in _BEST_CACHE:
        return _BEST_CACHE[key]
    pick = pick_template(index_templates(folder, limit=limit),
                         sender=sender, receiver=receiver, require_script=True,
                         need_scripts=need_scripts)
    _BEST_CACHE[key] = pick
    return pick


_RANK_CACHE: dict = {}
_NAME_CACHE: dict = {}


def find_template_by_name(folder: str, name: str, *, limit: int = 400) -> Optional[Template]:
    """Return the library template whose name matches `name` (exact, then
    case-insensitive contains), or None. Cached per folder-signature so an
    explicit Tab-3 pick doesn't re-walk the library on every deploy."""
    if not folder or not name:
        return None
    sig = _folder_signature(folder)
    key = (folder, name, limit, sig)
    if key in _NAME_CACHE:
        return _NAME_CACHE[key]
    pool = index_templates(folder, limit=limit)
    hit = next((t for t in pool if t.name == name), None)
    if hit is None:
        nl = name.lower()
        hit = next((t for t in pool if nl in t.name.lower()), None)
    _NAME_CACHE[key] = hit
    return hit


def rank_templates(folder: str, *, sender: str = "", receiver: str = "",
                   limit: int = 200, top_n: int = 8) -> List[Template]:
    """Return the top-N templates from a local library, best-match first, using
    the same scoring as pick_template (complete flow + adapter match + script).
    Cached per folder-signature so the Tab-3 dropdown never re-walks the library.
    Unlike pick_template this does NOT require a Script step, so simple flows
    (e.g. a pure Content Modifier) still surface as candidates."""
    sig = _folder_signature(folder)
    key = (folder, sender, receiver, limit, top_n, sig)
    if key in _RANK_CACHE:
        return _RANK_CACHE[key]
    templates = index_templates(folder, limit=limit)

    def score(t: Template) -> tuple:
        s = 0
        if t.step_count > 0:
            s += 10
        if sender and t.sender and sender.lower() in t.sender.lower():
            s += 2
        if receiver and t.receiver and receiver.lower() in t.receiver.lower():
            s += 2
        if t.has_script:
            s += 1
        return (s, t.step_count, t.script_count)

    ranked = sorted(templates, key=score, reverse=True)[:max(1, top_n)]
    _RANK_CACHE[key] = ranked
    return ranked


if __name__ == "__main__":
    import sys
    folder = sys.argv[1] if len(sys.argv) > 1 else "."
    ts = index_templates(folder)
    print(f"Found {len(ts)} template(s) in {folder}")
    for t in ts[:20]:
        print(f"  {t.name:40} scripts={t.script_count} steps={t.step_count} "
              f"sender={t.sender} receiver={t.receiver}")