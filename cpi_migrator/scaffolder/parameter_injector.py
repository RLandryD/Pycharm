"""
scaffolder/parameter_injector.py

Generates a populated CPI externalized-parameters file (parameters.prop) from
extracted PI/PO channel data and the refined InterfaceConfig — instead of the
empty stub that cpi_uploader.py writes by default.

CPI externalizes endpoints, credentials aliases, directories and timeouts so an
iFlow can be transported DEV→QA→PROD and re-pointed per landscape without
editing the flow. This module produces those key=value lines with REAL values
pulled from the source system wherever the channel parser captured them, and
sensible parameterized placeholders (e.g. {{Receiver_Host}}) otherwise.

Output is a `.prop` text body (str) plus the parsed dict, so callers can either
write it to src/main/resources/parameters.prop or display it in the UI.

No existing module is modified by importing this; cpi_uploader.py can OPT IN by
calling build_parameters_prop() and passing the result to its packager.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


_SAFE_KEY = re.compile(r"[^A-Za-z0-9_]")


def _key(name: str) -> str:
    """Normalise a label into a valid parameters.prop key."""
    k = _SAFE_KEY.sub("_", name.strip())
    k = re.sub(r"_+", "_", k).strip("_")
    return k or "Param"


@dataclass
class ParameterSet:
    """Result of building externalized parameters for one interface."""
    interface_name: str
    params: dict[str, str] = field(default_factory=dict)
    # Keys whose value is a placeholder the user must still fill in
    unresolved: list[str] = field(default_factory=list)

    def to_prop(self) -> str:
        """Render as a parameters.prop file body."""
        lines = [
            f"# Externalized parameters for {self.interface_name}",
            "# Auto-generated from PI/PO channel data. Review before import.",
            "# Lines with <FILL_*> values require manual completion.",
            "",
        ]
        for k in sorted(self.params):
            lines.append(f"{k}={self.params[k]}")
        return "\n".join(lines) + "\n"


def build_parameters(
    interface_name: str,
    config=None,        # InterfaceConfig (optional)
    channel=None,       # ChannelConfig (optional)
) -> ParameterSet:
    """Assemble externalized parameters from config + channel.

    Precedence for each value: channel (real, from source) > config (UI) >
    parameterized placeholder. Placeholders use the <FILL_x> convention so they
    are greppable and obviously incomplete.
    """
    ps = ParameterSet(interface_name=interface_name)

    def add(label: str, *candidates, placeholder: str = ""):
        key = _key(label)
        for c in candidates:
            if c:
                ps.params[key] = str(c)
                return
        if placeholder:
            ps.params[key] = placeholder
            ps.unresolved.append(key)

    # --- pull channel values (duck-typed) ---
    ch = channel
    ch_addr   = getattr(ch, "address", "") or getattr(ch, "endpoint_url", "") if ch else ""
    ch_path   = getattr(ch, "path", "") if ch else ""
    ch_port   = getattr(ch, "port", 0) if ch else 0
    ch_cred   = getattr(ch, "credential_name", "") if ch else ""
    ch_user   = getattr(ch, "username", "") if ch else ""
    ch_dir    = getattr(ch, "file_directory", "") if ch else ""
    ch_pat    = getattr(ch, "file_pattern", "") if ch else ""
    ch_queue  = getattr(ch, "queue_name", "") if ch else ""
    ch_jdbc   = getattr(ch, "jdbc_url", "") if ch else ""

    # --- config values ---
    cfg = config
    cfg_recv_addr = ""
    cfg_recv_path = ""
    cfg_cred = ""
    cfg_dir = ""
    cfg_pat = ""
    cfg_timeout = ""
    cfg_jdbc = ""
    if cfg is not None:
        rc = getattr(cfg, "receiver_connectivity", None)
        ra = getattr(cfg, "receiver_auth", None)
        msg = getattr(cfg, "message", None)
        rt = getattr(cfg, "runtime", None)
        if rc:
            cfg_recv_addr = getattr(rc, "address", "")
            cfg_recv_path = getattr(rc, "path", "")
        if ra:
            cfg_cred = getattr(ra, "credential_name", "")
        if msg:
            cfg_dir = getattr(msg, "file_directory", "")
            cfg_pat = getattr(msg, "file_pattern", "")
            cfg_jdbc = getattr(msg, "jdbc_jndi", "")
        if rt:
            cfg_timeout = getattr(rt, "timeout_sec", "")

    # --- emit parameters ---
    add("Receiver_Host", ch_addr, cfg_recv_addr, placeholder="<FILL_Receiver_Host>")
    add("Receiver_Path", ch_path, cfg_recv_path, placeholder="/")
    if ch_port:
        add("Receiver_Port", ch_port)
    add("Credential_Alias", ch_cred, cfg_cred, placeholder="<FILL_Credential_Alias>")
    if ch_user:
        add("Auth_User", ch_user)

    # adapter-specific
    if ch_dir or cfg_dir:
        add("File_Directory", ch_dir, cfg_dir, placeholder="<FILL_File_Directory>")
        add("File_Pattern", ch_pat, cfg_pat, placeholder="*.*")
    if ch_queue:
        add("Queue_Name", ch_queue)
    if ch_jdbc or cfg_jdbc:
        add("JDBC_URL", ch_jdbc, cfg_jdbc, placeholder="<FILL_JDBC_URL>")

    # runtime
    add("Timeout_ms", (int(cfg_timeout) * 1000) if str(cfg_timeout).isdigit() else "", placeholder="300000")

    # carry through any extra channel parameters not already mapped, prefixed
    raw = getattr(ch, "parameters", None) if ch else None
    if raw:
        known = {k.lower() for k in ps.params}
        for name, value in raw.items():
            k = _key(f"Src_{name}")
            if value and k.lower() not in known:
                ps.params[k] = str(value)

    return ps


def build_parameters_prop(interface_name: str, config=None, channel=None) -> str:
    """Convenience: return just the .prop file body."""
    return build_parameters(interface_name, config=config, channel=channel).to_prop()
