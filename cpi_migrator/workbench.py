"""
workbench.py — SAP CPI Migration Workbench
Run: streamlit run workbench.py
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
import zipfile
from pathlib import Path
from datetime import datetime

import streamlit as st

# ── path setup ───────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# ── Pinned local resource folders (per-machine; edit HERE, not in the UI) ──────
# These were previously selectable in the UI. They are now fixed so the wiring
# is unambiguous and can't be mis-set. Each is consumed at one specific moment:
#   Packages         → deploy-time template ranking (rank_templates)
#   Corpus           → Generate's artifact learning (capability corpus)
#   canonical_library→ schema source for resource carry (used once wired)
_RESOURCES_ROOT = Path("/home/landry/PycharmProjects/Resources")
PINNED_LOCAL_DIRS = {
    "template_library_dir":  str(_RESOURCES_ROOT / "Packages"),
    "capability_corpus_dir": str(_RESOURCES_ROOT / "Corpus"),
    "schema_library_dir":    str(_RESOURCES_ROOT / "canonical_library"),
}


def _pin_local_dirs():
    """Force the local resource folders to their fixed paths (replacing the UI
    selectors). To relocate them, edit PINNED_LOCAL_DIRS above — nowhere else."""
    try:
        from fetcher.user_settings import set_setting, get_setting
        for _k, _v in PINNED_LOCAL_DIRS.items():
            if get_setting(_k, "") != _v:
                set_setting(_k, _v)
    except Exception:
        pass


_pin_local_dirs()

from auth.authenticator import CFAuthenticator, NeoAuthenticator, PIAuthenticator
from extractor.pi_extractor import PIRestExtractor, PIFileExtractor, InterfaceRecord
from analyzer.complexity_analyzer import ComplexityAnalyzer, MigrationAssessment
from scaffolder.iflow_scaffolder import IFlowScaffolder
from scaffolder.pipeline_scaffolder import (
    PipelineScaffolder, should_use_pipeline, STRATEGIES,
    generate_package_name, generate_iflow_name, detect_domain,
)
from reporter.report_generator import ReportGenerator
from destinations.registry import DESTINATION_REGISTRY, list_targets
from destinations.hub_fetcher import HubFetcher
from destinations.resolver import DestinationResolver
from fetcher.cpi_fetcher import CPIFetcher, CPIArtifact
from fetcher.scc_configurator import auto_configure, TARGET_TOPOLOGY
from fetcher.github_fetcher import GitHubFetcher
from fetcher.sap_samples_browser import SAPSamplesBrowser, SAPSamplePackage, INTEGRATION_REPOS
from analyzer.clean_core_analyzer import CleanCoreAnalyzer, clean_core_summary
from reporter.doc_generator import TDDGenerator
from reporter.preflight_generator import PreflightGenerator
from reporter.security_inventory import SecurityInventoryGenerator
from reporter.infrastructure_guide import InfrastructureGuideGenerator
from reporter.intervention_estimator import InterventionEstimator
from testing.harness_generator import HarnessGenerator
from intake.requirement_parser import parse_requirements, generate_excel_template
from intake.isam_questionnaire import get_questions, evaluate, ISAMAnswer
from extractor.esr_extractor import ESRExtractor, ESRFileParser
from fetcher.cpi_uploader import CPIUploader
from fetcher.hub_catalog import HubCatalogClient
from scaffolder.groovy_generator import GroovyGenerator
from scaffolder.pipeline_scaffolder import needs_eoio_pattern, generate_eoio_pattern
from testing.payload_replayer import PayloadReplayer
from reporter.security_inventory import detect_security_level
from reporter.migration_ceiling import (
    MigrationCeilingClassifier, ceiling_summary,
    TIER_AUTO, TIER_GUIDED, TIER_SPECIALIST, TIER_EMOJI,
)
from reporter.proposal_generator import ProposalGenerator, PricingConfig
from models.client_tracker import ClientProblemTracker, PROBLEM_TYPES
from analyzer.recommendation_engine import RecommendationEngine, TIER_ICONS, TIER_START, TIER_BLOCKED, TIER_PARK, TIER_SPECIALIST, TIER_DEFER
from models.credential_store import CredentialStore, CPIProfile, TargetCredential
from models.interface_config import (
    InterfaceConfig, AuthConfig, ConnectivityConfig,
    MessageConfig, ReliabilityConfig, RuntimeConfig,
    AUTH_METHODS, MESSAGE_FORMATS, LOG_LEVELS, ADAPTER_TYPES,
)
from engine.feedback_loop import SolverSession, FeedbackLoopManager, COMMON_ISSUES
from engine.claude_solver import ClaudeSolver

# Program 2 — API Management
from apim.model import (
    APIProxy, APIProduct, Application, APIMLandscape, ProxyAuthType, KeyState,
)
from apim.proxy_generator import generate_proxy, proxy_from_iflow
from apim import policy_library
# Held-off Program 1 generators
from scaffolder.content_modifier_generator import (
    build_from_channel as build_content_modifier, render_descriptor as cm_descriptor,
    render_bpmn_step as cm_bpmn,
)
from scaffolder.value_mapping_generator import (
    build_from_pairs as build_value_mapping, render_artifact as vm_artifact,
    render_descriptor as vm_descriptor,
)

# Logging: INFO level so diagnostics are visible, with a persistent log file
# at ~/.cpi_migrator/cpi_migrator.log so it can be found and shared easily
# (terminal scrollback is unreliable). Console still shows logs too.
_LOG_DIR = Path.home() / ".cpi_migrator"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOG_DIR / "cpi_migrator.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(_LOG_FILE, mode="a", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
# Quiet down noisy third-party loggers so the file stays readable
for _noisy in ("urllib3", "requests", "watchdog", "PIL", "matplotlib"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logging.getLogger("cpi.diagnostics").setLevel(logging.INFO)

# Filter out persistent non-actionable warnings (Streamlit use_container_width
# / width deprecation spam) and route ALL logging into one unified wire log
# (the single log the user copies — communication + diagnostics together).
try:
    from fetcher.wire_log import install_unified_logging
    install_unified_logging()
except Exception:
    pass

# ── page config ──────────────────────────────────────────────────────────────
def _render_tenant_pull(expanded: bool = False):
    """Pull-from-tenant UI — rendered from the dedicated source type
    AND inside the Upload branch (same single implementation)."""
    with st.expander("📡 Pull packages from a CPI tenant",
                     expanded=expanded):
        st.caption(
            "End-to-end showcase: pull packages from the SOURCE tenant "
            "(Profiles tab — falls back to the target connection), run "
            "them through the workbench, and push the regenerated "
            "packages to the target.")
        _src_sess = (st.session_state.get("cpi_source_session")
                     or st.session_state.get("cpi_session"))
        _src_url = (st.session_state.get("cpi_source_base_url")
                    or st.session_state.get("cpi_base_url"))
        if not _src_sess:
            st.info("Connect a tenant in the Profiles tab first.")
        else:
            if st.button("📋 List packages on source tenant",
                         key="pull_list"):
                try:
                    _f = CPIFetcher(base_url=_src_url,
                                    session=_src_sess)
                    st.session_state["pull_pkg_list"] = \
                        _f.list_packages()
                except Exception as e:
                    st.error(f"List failed: {e}")
            _plist = st.session_state.get("pull_pkg_list") or []
            if _plist:
                _opts = {f"{p.get('Name', p.get('Id'))} "
                         f"({p.get('Id')})": p.get("Id")
                         for p in _plist}
                _sel = st.multiselect(
                    f"Packages on {_src_url.split('://')[-1].split('/')[0]}",
                    list(_opts), key="pull_pkg_sel")
                if _sel and st.button(
                        "⬇ Download & ingest selected",
                        key="pull_ingest", type="primary"):
                    _f = CPIFetcher(base_url=_src_url,
                                    session=_src_sess)
                    _items, _errs = [], []
                    with st.spinner("Exporting packages from the "
                                    "tenant…"):
                        for lbl in _sel:
                            pid = _opts[lbl]
                            try:
                                _items.append(
                                    (f"{pid}.zip",
                                     _f.download_package_zip(pid)))
                            except Exception as e:
                                # some packages refuse whole-package export
                                # (drafts/odd artifacts → 500); fall back to
                                # artifact-by-artifact
                                _got = 0
                                try:
                                    for _art in (_f.list_artifacts(pid)
                                                 or []):
                                        _aid2 = (_art.get("Id")
                                                 if isinstance(_art, dict)
                                                 else getattr(_art, "id",
                                                              "")) or ""
                                        if not _aid2:
                                            continue
                                        try:
                                            _u2 = (f"{_src_url}/api/v1/"
                                                   "IntegrationDesigntime"
                                                   f"Artifacts(Id='{_aid2}',"
                                                   "Version='active')/$value")
                                            _r2 = _src_sess.get(_u2,
                                                                timeout=120)
                                            _r2.raise_for_status()
                                            _items.append(
                                                (f"{pid}__{_aid2}.zip",
                                                 _r2.content))
                                            _got += 1
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                                if _got:
                                    st.warning(
                                        f"{pid}: whole-package export "
                                        f"failed ({e}); recovered {_got} "
                                        "artifact(s) individually.")
                                else:
                                    _errs.append((pid, str(e)))
                    if _items:
                        total, npkg, perr = _ingest_archive_items(
                            _items)
                        st.success(
                            f"✅ Pulled {len(_items)} package(s) → "
                            f"{total} interface(s) across {npkg} "
                            "loaded package(s). Assess in Tab 2, "
                            "regenerate + upload in Tab 5.")
                        _errs.extend(perr)
                    for pid, err in _errs:
                        st.error(f"{pid}: {err}")


def _ingest_archive_items(items):
    """Shared intake for package archives — used by BOTH the file uploader
    and the pull-from-tenant path (multi-tenant showcase: source tenant →
    workbench → target tenant). items: list of (name, raw_bytes).
    Returns (total_interfaces, package_count, parse_errors)."""
    from extractor.iflow_parser import extract_endpoints
    from extractor.pi_extractor import InterfaceRecord
    from fetcher.artifact_router import extract_iflows_recursive
    fetcher = CPIFetcher(
        base_url=st.session_state.cpi_base_url or "http://localhost",
        session=st.session_state.cpi_session)
    pkgs, all_arts, parse_errors = [], [], []
    for name, raw in items:
        try:
            flows = extract_iflows_recursive(
                raw, container_name=name.rsplit(".", 1)[0])
            try:
                from library_builder.corpus_pipeline import (WIRING_EXTS,
                                                             walk_zip_bytes)
                _up = st.session_state.setdefault("uploaded_resources", {})
                _up.update(walk_zip_bytes(raw, name.rsplit(".", 1)[0],
                                          exts=WIRING_EXTS))
                from scaffolder.passthrough import (
                    collect_passthrough_from_zip as _cpz)
                _pt = st.session_state.setdefault("uploaded_passthrough", {})
                _pt.update(_cpz(raw))
            except Exception:
                pass
            try:
                arts = fetcher.download_from_upload(raw, name)
            except Exception:
                arts = []
            all_arts.extend(arts)
            pkgs.append({
                "filename": name, "bytes": raw, "iflow_count": len(flows),
                "iflows": [{"id": f["id"], "name": f["name"],
                            "iflw_xml": f.get("iflw_xml", ""),
                            "package": f.get("package", "")}
                           for f in flows]})
        except Exception as exc:
            parse_errors.append((name, str(exc)))
    existing = {p["filename"]: p
                for p in st.session_state.uploaded_packages}
    for p in pkgs:
        existing[p["filename"]] = p
    st.session_state.uploaded_packages = list(existing.values())
    merged_records, seen_ids = [], set()
    for p in st.session_state.uploaded_packages:
        for fl in p["iflows"]:
            if fl["id"] in seen_ids:
                continue
            seen_ids.add(fl["id"])
            _ep = extract_endpoints(fl.get("iflw_xml", ""))
            _snd = _ep.get("sender_system", "")
            _rcv = _ep.get("receiver_system", "")
            merged_records.append(InterfaceRecord(
                id=fl["id"], name=fl["name"], namespace="",
                software_component="", sender_system=_snd,
                receiver_system=_rcv,
                sender_adapter=(_ep.get("sender_adapter")
                                or ("HTTPS" if _snd else "")),
                receiver_adapter=(_ep.get("receiver_adapter")
                                  or ("HTTPS" if _rcv else "")),
                message_interface="", description="",
                source_iflow_xml=fl.get("iflw_xml", ""),
                package=fl.get("package", "")))
    from analyzer.ma_assessments import assess_records
    st.session_state.interfaces = merged_records
    st.session_state.assessments = assess_records(merged_records)
    st.session_state.all_artifacts = all_arts
    return (len(merged_records), len(st.session_state.uploaded_packages),
            parse_errors)


st.set_page_config(
    page_title="CPI Migration Workbench",
    page_icon="🔄",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── session state init ───────────────────────────────────────────────────────
DEFAULTS = {
    "cpi_session":      None,
    "cpi_base_url":     "",
    "cpi_connected":    False,
    "pi_session":       None,
    "pi_connected":     False,
    "interfaces":       [],       # list[InterfaceRecord]
    "assessments":      [],       # list[MigrationAssessment]
    "selected":         [],       # list[str] interface names
    "all_artifacts":    [],       # list[CPIArtifact]
    "local_template_choice": {},   # {iface_name: local library template name}
    "target_ids":       {},       # {iface_name: str}
    "configs":          {},       # {iface_name: InterfaceConfig}
    "resolutions":      {},
    "active_profile":   None,   # CPIProfile currently loaded
    "profile_unlocked": False,
    "migration_strategy": "bluefield",
    "pipeline_mode":      "auto",
    "company_code":       "COMP",
    "package_names":      {},   # {(sender,receiver): package_name}
    "iflow_names":        {},   # {iface_name: display_name}
    "clean_core":       {},   # {iface_name: CleanCoreReport}
    "verifications":    {},   # {iface_name: VerificationReport}
    "ceilings":         {},   # {iface_name: MigrationCeiling}
    "solver_results":    {},   # {iface_name: SolverResult}
    "recommendations":   {},   # {iface_name: InterfaceRecommendation}
    "interventions":    {},   # {iface_name: InterfaceIntervention}
    "cfg":              {},       # raw settings.yaml content
    "uploaded_packages": [],      # list[{"filename","bytes","iflow_count","iflows":[{id,name}]}]
                                  # Tab 1 accumulates uploaded zips here; Tab 5
                                  # selects among them to push to the tenant
                                  # (one upload serves both assessment & deploy).
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_settings() -> dict:
    try:
        import yaml
        p = ROOT / "config" / "settings.yaml"
        if p.exists():
            return yaml.safe_load(p.read_text()) or {}
    except Exception:
        pass
    return {}

# Keyword → destination-target key, longest/most-specific first. Used to default
# each interface's target from its own receiver instead of all-the-same.
_TARGET_HINTS = [
    ("successfactors", "successfactors"), ("succ", "successfactors"),
    ("employee central", "successfactors"), ("ariba", "ariba"),
    ("fieldglass", "fieldglass"), ("concur", "concur"),
    ("event mesh", "btp"), ("aem", "btp"),
    ("servicebus", "azure_servicebus"), ("service bus", "azure_servicebus"),
    ("azure blob", "azure_blob"), ("pubsub", "gcp_pubsub"),
    ("pub/sub", "gcp_pubsub"), ("gcs", "gcp_gcs"),
    ("sqs", "aws_sqs"), ("s3", "aws_s3"),
    ("on-prem", "s4hana_op"), ("on prem", "s4hana_op"), ("ecc", "s4hana_op"),
    ("r/3", "s4hana_op"), ("s/4hana on", "s4hana_op"),
    ("s/4hana", "s4hana_cloud"), ("s4hana", "s4hana_cloud"), ("s4", "s4hana_cloud"),
]


def _default_target_for(iface, valid_keys, fallback: str = "s4hana_cloud") -> str:
    """Pick a sensible destination target for one interface from its receiver
    system / adapter / name. Falls back to S/4HANA Cloud when nothing matches."""
    hay = " ".join(str(getattr(iface, f, "") or "") for f in
                   ("receiver_system", "receiver_adapter", "name",
                    "namespace")).lower()
    for kw, key in _TARGET_HINTS:
        if kw in hay and key in valid_keys:
            return key
    return fallback if fallback in valid_keys else (valid_keys[0] if valid_keys else fallback)


_MA_ENGINE = None


def _ma_assess(iface):
    """MA-faithful assessment for an interface: (size, weight, effort_days_avg,
    effort_lo_days, effort_hi_days). When the interface carries real SAP MA
    figures (ma_weight set, from an imported MA export), uses the engine's
    calibrated Mode 1 (assess_true_ma → SAP bands + SAP effort table). Otherwise
    falls back to the keyword approximation (assess_interface). S/M/L/XL, scaling
    weight/effort — no LOW/MED/HIGH, no cap, no pre-set bucket."""
    global _MA_ENGINE
    try:
        from analyzer.sap_complexity_engine import SAPComplexityEngine
        if _MA_ENGINE is None:
            _MA_ENGINE = SAPComplexityEngine()
        if getattr(iface, "ma_weight", None) is not None:
            r = _MA_ENGINE.assess_true_ma(
                getattr(iface, "name", ""), weight=int(iface.ma_weight),
                size=getattr(iface, "ma_size", "") or "",
                category=getattr(iface, "ma_status", "") or "")
        else:
            r = _MA_ENGINE.assess_interface(iface)
        return (r.size, r.total_weight, r.effort_days_avg,
                round(r.effort_hours_low / 8.0, 1),
                round(r.effort_hours_high / 8.0, 1))
    except Exception:                                  # noqa
        return "?", 0, 0.0, 0.0, 0.0


def _ma_size_weight(iface):
    """Back-compat shim: (size, weight) only."""
    sz, wt, *_ = _ma_assess(iface)
    return sz, wt


def _save_settings(cfg: dict):
    try:
        import yaml
        p = ROOT / "config" / "settings.yaml"
        p.write_text(yaml.dump(cfg, default_flow_style=False), "utf-8")
    except Exception as e:
        st.warning(f"Could not save settings: {e}")

def _build_zip() -> bytes:
    buf = io.BytesIO()
    out = ROOT / "output"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in out.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(out))
    buf.seek(0)
    return buf.read()


# ═══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.title("🔄 CPI Workbench")
    st.caption("SAP PI/PO → CPI Migration Tool")

    # ── Program mode switch ──────────────────────────────────────────
    # Two programs share one app, one infrastructure spine (auth, Hub,
    # credential store, deploy). The mode toggle swaps the tab set so
    # neither program's UI bloats the other.
    workbench_mode = st.radio(
        "Mode",
        ["🔄 Migration (PI/PO → CPI)", "🌐 API Management"],
        key="workbench_mode",
        help="Migration: convert PI/PO interfaces to CPI iFlows. "
             "API Management: build + manage API proxies, products, policies.",
    )
    st.session_state["active_program"] = (
        "apim" if workbench_mode.startswith("🌐") else "migration")

    st.divider()

    # ── Profile selector ─────────────────────────────────────────────
    st.subheader("🔑 Profiles")
    _store = CredentialStore()
    _profile_names = _store.list_profiles()

    if _profile_names:
        _sel_profile = st.selectbox("Active profile",
                                    ["(none)"] + _profile_names,
                                    key="sb_profile_select")
        _master_pw = st.text_input("Master password", type="password",
                                   key="sb_master_pw")
        if st.button("🔓 Load profile", use_container_width=True):
            if _sel_profile != "(none)" and _master_pw:
                try:
                    _p = _store.load_profile(_sel_profile, _master_pw)
                    st.session_state.active_profile   = _p
                    st.session_state.profile_unlocked = True
                    st.session_state["sb_service_keys"] = dict(
                        getattr(_p, "service_keys", {}) or {})

                    # Set the environment selector to match the profile, so the
                    # correct field set renders (CF vs Neo).
                    _env = (getattr(_p, "cpi_environment", "cf") or "cf").lower()
                    st.session_state["sb_cpi_env"] = (
                        "Cloud Foundry (BTP)" if _env == "cf" else "Neo")

                    # Fill ALL connection widgets from the profile (both CF and
                    # Neo sets — whichever renders will show the right values).
                    st.session_state.cpi_base_url    = _p.cpi_base_url or ""
                    st.session_state["sb_cpi_url"]   = _p.cpi_base_url or ""
                    st.session_state["sb_token_url"] = getattr(_p, "cpi_token_url", "") or ""
                    st.session_state["sb_client_id"] = _p.cpi_client_id or ""
                    st.session_state["sb_secret"]    = getattr(_p, "cpi_client_secret", "") or ""
                    st.session_state["sb_neo_user"]  = getattr(_p, "cpi_username", "") or ""
                    st.session_state["sb_neo_pass"]  = getattr(_p, "cpi_password", "") or ""

                    # Also seed cfg so the main-panel / Tab-0 editor and any
                    # other reader sees the same values for this environment.
                    _cfg = st.session_state.cfg or {}
                    _cfg["environment"] = _env
                    _cfg.setdefault("cf", {}).update({
                        "base_url":      _p.cpi_base_url or "",
                        "token_url":     getattr(_p, "cpi_token_url", "") or "",
                        "client_id":     _p.cpi_client_id or "",
                        "client_secret": getattr(_p, "cpi_client_secret", "") or "",
                    })
                    _cfg.setdefault("neo", {}).update({
                        "base_url": _p.cpi_base_url or "",
                        "username": getattr(_p, "cpi_username", "") or "",
                        "password": getattr(_p, "cpi_password", "") or "",
                    })
                    st.session_state.cfg = _cfg

                    # Seed PI/PO source (Tab 1 reads cfg["pi"]) so the main
                    # screen fills, not just the sidebar.
                    _cfg.setdefault("pi", {}).update({
                        "base_url": getattr(_p, "pi_base_url", "") or "",
                        "username": getattr(_p, "pi_username", "") or "",
                        "password": getattr(_p, "pi_password", "") or "",
                        "export_file": getattr(_p, "pi_export_file", "") or "",
                    })
                    # Seed Cloud Connector (SCC) fields
                    _cfg.setdefault("scc", {}).update({
                        "location_id":  getattr(_p, "scc_location_id", "") or "",
                        "virtual_host": getattr(_p, "scc_virtual_host", "") or "",
                        "virtual_port": getattr(_p, "scc_virtual_port", 443) or 443,
                    })
                    # Seed project/company + hub key
                    _cfg["company_code"] = getattr(_p, "company_code", "") or ""
                    _cfg["project_name"] = getattr(_p, "name", "") or ""
                    if getattr(_p, "hub_api_key", ""):
                        _cfg.setdefault("destinations", {})["hub_api_key"] = _p.hub_api_key
                    st.session_state.cfg = _cfg

                    # Push values into session_state for widgets that use keys
                    # (key-based widgets ignore value=, so set the key directly).
                    _company = getattr(_p, "company_code", "") or ""
                    _proj    = getattr(_p, "name", "") or ""
                    for _wk, _wv in [
                        ("company_code_input", _company),
                        ("prop_co", _company),
                        ("prop_name", _proj),
                        ("pm_scc_loc",  getattr(_p, "scc_location_id", "") or ""),
                        ("pm_scc_host", getattr(_p, "scc_virtual_host", "") or ""),
                    ]:
                        if _wv:
                            st.session_state[_wk] = _wv

                    st.success(f"✓ {_sel_profile} loaded ({_env.upper()}). "
                               f"Filled sidebar + project + PI/PO + Cloud "
                               f"Connector fields. If a field shows an old test "
                               f"value, update it in **Tab 0 · Profiles** and "
                               f"re-save.")
                    st.rerun()
                except ValueError:
                    st.error("Wrong master password (or corrupted profile)")
                except Exception as e:
                    st.error(str(e))
        if st.session_state.get("profile_unlocked") and st.session_state.get("active_profile"):
            _ap = st.session_state.active_profile
            st.caption(f"✅ Active: **{_ap.name}** | {_ap.company_code}")
    else:
        st.caption("No profiles yet — go to **Tab 0 · Profiles** to create one")

    # ── Quick save (mirrors the full editor in Tab 0) ────────────────
    # Saves the profile currently being edited in Tab 0 from here, so you
    # don't have to scroll to the bottom of the form. The full data-entry
    # surface stays in Tab 0; this is just a shortcut to persist it.
    with st.expander("💾 Save current profile"):
        # Full profile management lives here now (former Tab 0): profiles
        # are AES-256-encrypted at ~/.cpi_migrator/profiles/, local only.
        _qs_name = st.text_input(
            "Profile name",
            value=st.session_state.get("pm_name", ""),
            key="sb_quicksave_name",
            help="Fill the profile fields in Tab 0 · Profiles, then save here.")
        _qs_pw = st.text_input("Master password", type="password",
                               key="sb_quicksave_pw")
        if st.button("💾 Save", use_container_width=True, key="sb_quicksave_btn"):
            if not _qs_name:
                st.warning("Enter a profile name (and fill fields in Tab 0).")
            elif not _qs_pw:
                st.warning("Master password required.")
            else:
                try:
                    _ss = st.session_state
                    _env_cf = "CF" in _ss.get("pm_env", "Cloud Foundry (BTP)")
                    _prof = CPIProfile(
                        name=_qs_name,
                        company_code=_ss.get("pm_code", ""),
                        description=_ss.get("pm_desc", ""),
                        cpi_environment="cf" if _env_cf else "neo",
                        cpi_base_url=(_ss.get("pm_cpi_url", "") if _env_cf
                                      else _ss.get("pm_neo_url", "")),
                        cpi_token_url=_ss.get("pm_token_url", ""),
                        cpi_client_id=_ss.get("pm_client_id", ""),
                        cpi_client_secret=_ss.get("pm_client_sec", ""),
                        cpi_username=_ss.get("pm_neo_user", ""),
                        cpi_password=_ss.get("pm_neo_pass", ""),
                        pi_base_url=_ss.get("pm_pi_url", ""),
                        pi_username=_ss.get("pm_pi_user", ""),
                        pi_password=_ss.get("pm_pi_pass", ""),
                        pi_export_file=_ss.get("pm_pi_file", ""),
                        scc_location_id=_ss.get("pm_scc_loc", ""),
                        scc_virtual_host=_ss.get("pm_scc_host", ""),
                        scc_virtual_port=int(_ss.get("pm_scc_port", 443) or 443),
                        hub_api_key=_ss.get("pm_hub_key", ""),
                        github_token=_ss.get("pm_gh_token", ""),
                        targets=_ss.get("pm_targets", []),
                        service_keys=_ss.get("sb_service_keys", {}) or {},
                        ctms_url=_ss.get("pm_ctms_url", ""),
                        ctms_client_id=_ss.get("pm_ctms_id", ""),
                        ctms_client_secret=_ss.get("pm_ctms_sec", ""),
                    )
                    _store.save_profile(_prof, _qs_pw)
                    st.success(f"✅ Saved '{_qs_name}'. Reload from above.")
                except Exception as _e:
                    st.error(f"Save failed: {_e}")

        _del_names = _store.list_profiles() if '_store' in dir() else []
        if _del_names:
            _del_pick = st.selectbox("Delete profile", ["(none)"] + _del_names,
                                     key="sb_profile_del_pick")
            if _del_pick != "(none)" and st.button(
                    f"🗑 Delete '{_del_pick}'", key="sb_profile_del_btn"):
                _store.delete_profile(_del_pick)
                st.success(f"Deleted profile {_del_pick}")
                st.rerun()

    # ── Per-client service-key wallet ────────────────────────────────────
    # Any BTP key an engagement needs (ANS, cTMS, Content Agent, CI/CD…)
    # lives in the active profile under a named slot — same pattern as the
    # CPI key picker, same encryption. NEVER displayed unmasked.
    with st.expander("🔑 Service keys (per client)"):
        _sk = st.session_state.setdefault("sb_service_keys", {})
        if _sk:
            from models.credential_store import CPIProfile as _CPf
            for _slot in sorted(_sk):
                _kc1, _kc2 = st.columns([4, 1])
                _kc1.caption(f"**{_slot}** — "
                             f"{_CPf.mask_key(_sk[_slot])}")
                if _kc2.button("🗑", key=f"sb_sk_del_{_slot}"):
                    _sk.pop(_slot, None)
                    st.rerun()
        else:
            st.caption("No service keys in this profile yet.")
        _slot_pick = st.selectbox(
            "Slot", ["ans", "ctms", "content_agent", "cicd", "custom…"],
            key="sb_sk_slot")
        _slot_name = (st.text_input("Custom slot name", key="sb_sk_custom")
                      if _slot_pick == "custom…" else _slot_pick)
        _sk_file = st.file_uploader("Service key (.json)", type=["json"],
                                    key="sb_sk_file")
        if st.button("➕ Add to profile", key="sb_sk_add",
                     disabled=not (_sk_file and _slot_name)):
            import json as _json
            try:
                _sk[_slot_name] = _json.loads(_sk_file.read())
                st.success(f"Stored under '{_slot_name}' — quick-save the "
                           f"profile to persist (encrypted).")
            except Exception as _ske:
                st.error(f"Not valid JSON: {_ske}")
    # Show where profiles live so it's clear they persist across project
    # re-installs (they're in your home dir, NOT the project folder).
    st.caption(f"📁 Profiles stored at: `{_store.profiles_dir}` "
               f"({len(_profile_names)} found)")

    st.divider()

    # ── CPI Tenant connection ────────────────────────────────────────
    st.subheader("🔌 CPI Tenant")
    cfg = st.session_state.cfg or _load_settings()
    st.session_state.cfg = cfg

    cpi_env = st.selectbox("Environment", ["Cloud Foundry (BTP)", "Neo"],
                           index=0 if cfg.get("environment", "cf") == "cf" else 1,
                           key="sb_cpi_env")
    # Initialise widget keys from cfg once, so loaded-profile values aren't
    # overwritten on rerun. value= is omitted so the session_state key is the
    # single source of truth (avoids the value/key conflict warning).
    for _k, _v in [
        ("sb_cpi_url",   cfg.get("cf", {}).get("base_url", "") if "CF" in cpi_env
                         else cfg.get("neo", {}).get("base_url", "")),
        ("sb_token_url", cfg.get("cf", {}).get("token_url", "")),
        ("sb_client_id", cfg.get("cf", {}).get("client_id", "")),
        ("sb_secret",    cfg.get("cf", {}).get("client_secret", "")),
        ("sb_neo_user",  cfg.get("neo", {}).get("username", "")),
        ("sb_neo_pass",  cfg.get("neo", {}).get("password", "")),
    ]:
        if _k not in st.session_state:
            st.session_state[_k] = _v

    cpi_url = st.text_input("Tenant Base URL", key="sb_cpi_url")

    if "CF" in cpi_env:
        cpi_token_url  = st.text_input("Token URL", key="sb_token_url")
        cpi_client_id  = st.text_input("Client ID", key="sb_client_id")
        cpi_secret     = st.text_input("Client Secret", type="password",
                                       key="sb_secret")
    else:
        cpi_user       = st.text_input("Username", key="sb_neo_user")
        cpi_pass       = st.text_input("Password", type="password",
                                       key="sb_neo_pass")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Test CPI", use_container_width=True):
            with st.spinner("Connecting…"):
                try:
                    if "CF" in cpi_env:
                        auth = CFAuthenticator(cpi_token_url, cpi_client_id, cpi_secret)
                    else:
                        auth = NeoAuthenticator(cpi_user, cpi_pass)
                    sess = auth.get_session()
                    # Quick ping
                    r = sess.get(f"{cpi_url}/api/v1/IntegrationPackages?$top=1&$format=json",
                                 timeout=15)
                    r.raise_for_status()
                    st.session_state.cpi_session   = sess
                    st.session_state.cpi_base_url  = cpi_url
                    st.session_state.cpi_connected = True
                    st.success("✓ Connected")
                except Exception as e:
                    st.session_state.cpi_connected = False
                    st.error(f"✗ {e}")
    with col2:
        status = "✅ Online" if st.session_state.cpi_connected else "⚫ Offline"
        st.markdown(f"**{status}**")

    with st.expander("📡 Source tenant (pull packages from)",
                     expanded=False):
        st.caption(
            "Multi-tenant: connect a SOURCE tenant to pull packages from, "
            "while the connection above stays the TARGET everything "
            "deploys to. For a same-tenant showcase (pull your own "
            "packages, regenerate them as new ones, push them back), just "
            "reuse the target connection.")
        if st.button("Use target connection as source", key="src_use_tgt"):
            if st.session_state.get("cpi_session"):
                st.session_state["cpi_source_session"] = \
                    st.session_state.cpi_session
                st.session_state["cpi_source_base_url"] = \
                    st.session_state.cpi_base_url
                st.success("Source = target tenant")
            else:
                st.warning("Connect the target first.")
        src_url = st.text_input("Source Tenant Base URL", key="src_cpi_url")
        sc1, sc2 = st.columns(2)
        with sc1:
            src_token = st.text_input("Token URL", key="src_token_url")
            src_cid = st.text_input("Client ID", key="src_client_id")
        with sc2:
            src_sec = st.text_input("Client Secret", type="password",
                                    key="src_secret")
        if st.button("Test source tenant", key="src_test"):
            with st.spinner("Connecting…"):
                try:
                    _a = CFAuthenticator(src_token, src_cid, src_sec)
                    _s = _a.get_session()
                    r = _s.get(f"{src_url}/api/v1/IntegrationPackages"
                               f"?$top=1&$format=json", timeout=15)
                    r.raise_for_status()
                    st.session_state["cpi_source_session"] = _s
                    st.session_state["cpi_source_base_url"] = src_url
                    st.success("✓ Source connected")
                except Exception as e:
                    st.error(f"✗ {e}")
        if st.session_state.get("cpi_source_session"):
            st.markdown("**Source: ✅ "
                        + (st.session_state.get("cpi_source_base_url") or "")
                        .split("://")[-1].split("/")[0] + "**")

    # ── Security Material check (also a connection self-test) ──────────
    if st.session_state.cpi_connected:
        with st.expander("🔐 Security Material"):
            st.caption("Lists credential names + keystore aliases (never secret "
                       "values — CPI doesn't expose those). Also confirms the "
                       "connection works for security-material reads.")
            if st.button("Check credentials", key="secmat_check"):
                try:
                    from fetcher.security_material import SecurityMaterialClient
                    smc = SecurityMaterialClient(
                        st.session_state.cpi_base_url, st.session_state.cpi_session)
                    rep = smc.list_credentials()
                    if rep.reachable:
                        st.success(f"✓ {len(rep.credentials)} credential(s) found")
                        if rep.credentials:
                            st.dataframe(
                                [{"Name": c.name, "Kind": c.kind} for c in rep.credentials],
                                hide_index=True, use_container_width=True)
                        st.session_state["security_material"] = rep
                    else:
                        st.warning(rep.error or "Could not read security material")
                except Exception as e:
                    st.error(str(e))

    st.divider()

    # ── Settings ─────────────────────────────────────────────────────
    with st.expander("⚙ Settings"):
    # ── Local source folders (set once here, persisted; no CLI needed) ────
        # These feed the Generate step (capability corpus) and the deploy step
        # (clone-and-adapt template library). Empty paths are why those came up
        # blank before — set them here, once.
        from fetcher.user_settings import (get_setting as _gs_path,
                                            set_setting as _ss_path)
    
        def _pick_folder_dialog():
            """Native folder chooser on the user's desktop (this app runs locally).
            Returns a path, or '' if unavailable/cancelled."""
            try:
                import tkinter as _tk
                from tkinter import filedialog as _fd
                _root = _tk.Tk()
                _root.withdraw()
                _root.wm_attributes("-topmost", 1)
                picked = _fd.askdirectory(master=_root) or ""
                _root.destroy()
                return picked
            except Exception:
                return ""
    
        def _folder_input(label, setting_key, widget_key, help_text):
            # apply a pending Browse result BEFORE the widget is instantiated
            # (Streamlit forbids mutating a widget's state after creation)
            pend = widget_key + "_pending"
            if pend in st.session_state:
                st.session_state[widget_key] = st.session_state.pop(pend)
            if widget_key not in st.session_state:
                st.session_state[widget_key] = _gs_path(setting_key, "")
            col_in, col_btn = st.columns([5, 1])
            with col_in:
                val = st.text_input(label, key=widget_key, help=help_text)
            with col_btn:
                st.markdown("<div style='height:1.75em'></div>", unsafe_allow_html=True)
                if st.button("📁 Browse", key=widget_key + "_browse"):
                    picked = _pick_folder_dialog()
                    if picked:
                        st.session_state[pend] = picked
                        _ss_path(setting_key, picked)
                        st.rerun()
                    else:
                        st.caption("(no dialog — paste path)")
            if val and val != _gs_path(setting_key, ""):
                _ss_path(setting_key, val)
            if val and os.path.isdir(val):
                try:
                    n = sum(1 for _ in os.scandir(val))
                    st.caption(f"✓ found — {n} entr(y/ies)")
                except OSError:
                    st.caption("✓ set")
            elif val:
                st.caption("⚠ folder not found on disk")
    
        with st.expander("📁 Local libraries — fixed paths (edit in code, not here)",
                         expanded=True):
            def _folder_status(label, setting_key):
                p = PINNED_LOCAL_DIRS.get(setting_key, "")
                if p and os.path.isdir(p):
                    try:
                        n = sum(1 for _ in os.scandir(p))
                        st.caption(f"**{label}**: `{p}` — ✓ {n} entr(y/ies)")
                    except OSError:
                        st.caption(f"**{label}**: `{p}` — ✓ set")
                else:
                    st.caption(f"**{label}**: `{p}` — ⚠ not found on disk")
            _folder_status("Packages (deploy template ranking)", "template_library_dir")
            _folder_status("Corpus (Generate artifact learning)", "capability_corpus_dir")
            _folder_status("Schemas (canonical_library)", "schema_library_dir")

        out_dir = st.text_input("Output directory", value="./output")
        cache_ttl = st.number_input("Hub cache TTL (hours)", value=24, min_value=1)
        from fetcher.user_settings import get_setting as _get_setting, \
            set_setting as _set_setting
        hub_key = st.text_input("SAP Hub API key",
                                value=cfg.get("destinations", {}).get("hub_api_key", "")
                                      or _get_setting("hub_api_key", ""),
                                type="password",
                                help="Stored externally (~/.cpi_migrator) on Save, "
                                     "so it survives re-importing the project.")
        owner_email = st.text_input(
            "Package owner email (recorded in package description)",
            value=cfg.get("destinations", {}).get("owner_email", ""),
            placeholder="you@company.com",
            help="The system 'Created by' is always the OAuth service account "
                 "and can't be changed via API, but this email is written into "
                 "the package description so the human owner is recorded.")
        anthropic_key = st.text_input(
            "Anthropic API key (for AI Solver)",
            value=cfg.get("destinations", {}).get("anthropic_api_key", ""),
            type="password",
            help="Needed for the AI Solver tab. Without it, the solver returns "
                 "a 401. Get a key at console.anthropic.com.")

        st.markdown("**📧 Email notifications (for long runs)**")
        _dst = cfg.get("destinations", {})
        smtp_host = st.text_input("SMTP host", value=_dst.get("smtp_host", ""),
                                  placeholder="smtp.gmail.com", key="smtp_host")
        sc1, sc2 = st.columns(2)
        with sc1:
            smtp_port = st.text_input("SMTP port", value=_dst.get("smtp_port", "587"),
                                      key="smtp_port")
            smtp_from = st.text_input("From address", value=_dst.get("smtp_from", ""),
                                      key="smtp_from")
        with sc2:
            smtp_user = st.text_input("SMTP user", value=_dst.get("smtp_user", ""),
                                      key="smtp_user")
            smtp_to = st.text_input("Notify address", value=_dst.get("smtp_to", ""),
                                    placeholder="you@email.com", key="smtp_to")
        smtp_pass = st.text_input("SMTP password", type="password",
                                  value=_dst.get("smtp_pass", ""), key="smtp_pass")
        notify_enabled = st.checkbox("Notify on completion (email + banner/sound)",
                                     value=_dst.get("notify_enabled", True),
                                     key="notify_enabled")

        if st.button("Save settings"):
            cfg.setdefault("destinations", {})["hub_api_key"] = hub_key
            if hub_key:                    # persist externally (survives re-import)
                _set_setting("hub_api_key", hub_key)
            cfg.setdefault("destinations", {})["cache_ttl_hours"] = cache_ttl
            cfg.setdefault("destinations", {})["owner_email"] = owner_email
            cfg.setdefault("destinations", {})["anthropic_api_key"] = anthropic_key
            cfg["destinations"].update({
                "smtp_host": smtp_host, "smtp_port": smtp_port,
                "smtp_user": smtp_user, "smtp_pass": smtp_pass,
                "smtp_from": smtp_from, "smtp_to": smtp_to,
                "notify_enabled": notify_enabled,
            })
            st.session_state["owner_email"] = owner_email
            st.session_state["anthropic_api_key"] = anthropic_key
            st.session_state["smtp_cfg"] = {
                "host": smtp_host, "port": smtp_port, "user": smtp_user,
                "password": smtp_pass, "from_addr": smtp_from,
                "to_addr": smtp_to, "use_tls": True,
            }
            _save_settings(cfg)
            st.success("Saved")

    # ── Unified log (communication + diagnostics in one) ─────────────────
    with st.sidebar.expander("📋 Full log (copy & share)"):
        st.caption("One unified log: every CPI request/response AND all "
                   "diagnostic messages, newest at the bottom. Click the copy "
                   "icon on the code block (top-right on hover), or download. "
                   "Tokens are redacted.")
        from fetcher.wire_log import read_wire_log, WIRE_LOG_FILE
        _wire = read_wire_log()
        st.code(_wire, language="text")
        wc1, wc2 = st.columns(2)
        with wc1:
            st.download_button("⬇ Download full log",
                               data=_wire,
                               file_name="cpi_full_log.txt",
                               key="wire_dl")
        with wc2:
            if st.button("🗑 Clear log", key="wire_clear"):
                from fetcher.wire_log import reset_wire_log
                reset_wire_log()
                st.rerun()
        st.caption(f"Saved at: {WIRE_LOG_FILE}")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN TABS
# ═══════════════════════════════════════════════════════════════════════════════


# ── Render helper functions (defined before tabs) ────────────────────────────

def render_isam_questionnaire():
    """Render the ISA-M questionnaire and return recommendation."""
    st.subheader("🗺 ISA-M Tool Recommendation")
    st.caption("Answer 10 questions to get the recommended SAP BTP tool for this integration.")

    questions = get_questions()
    answers   = []
    complete  = True

    for q in questions:
        options     = [o[1] for o in q["options"]]
        option_keys = [o[0] for o in q["options"]]
        sel = st.radio(q["text"], options, key=f"isam_{q['id']}",
                       index=None)
        if sel is None:
            complete = False
        else:
            idx = options.index(sel)
            answers.append(ISAMAnswer(q["id"], option_keys[idx]))

    if complete and len(answers) == len(questions):
        if st.button("▶ Get Recommendation", type="primary",
                     key="isam_submit"):
            rec = evaluate(answers)
            st.divider()
            st.markdown(f"### 🎯 Primary tool: **{rec.primary_tool}**")
            if rec.secondary_tools:
                st.markdown(f"**Also consider:** {', '.join(rec.secondary_tools)}")
            st.markdown(f"**ISA-M Pattern:** {rec.isa_m_pattern}")
            st.markdown(f"**Integration Style:** {rec.integration_style}")
            st.markdown(f"**Confidence:** {rec.confidence:.0%}")

            st.subheader("Reasoning")
            for r in rec.reasoning:
                st.markdown(f"- {r}")

            # Score breakdown
            import pandas as pd
            score_df = pd.DataFrame([
                {"Tool": k, "Score": v}
                for k, v in rec.score_breakdown.items()
            ]).sort_values("Score", ascending=False)
            st.bar_chart(score_df.set_index("Tool"))
            return rec
    elif not complete:
        st.info("Answer all questions to get a recommendation.")
    return None

def render_esr_uploader():
    """ESR file upload panel — call from Tab 1."""
    st.subheader("📂 Upload ESR Exports")
    st.caption("Upload exported PI/PO ESR files (.xsd, .wsdl, .mmap, .xim) "
               "to extract design-time artifacts.")

    uploaded = st.file_uploader(
        "Choose ESR export files",
        type=["xsd", "wsdl", "xml", "mmap", "xim"],
        accept_multiple_files=True,
        key="esr_upload",
    )
    if uploaded and st.button("📥 Parse ESR files", key="esr_parse"):
        parser = ESRFileParser()
        files  = {f.name: f.read() for f in uploaded}
        objs   = parser.parse_uploaded_files(files)
        if objs:
            import pandas as pd
            df = pd.DataFrame([
                {"Name": o.name, "Type": o.obj_type,
                 "Namespace": o.namespace[:40],
                 "Mapping Type": o.mapping_type}
                for o in objs
            ])
            st.dataframe(df, hide_index=True, use_container_width=True)
            st.session_state["esr_objects"] = objs
            st.success(f"✅ Parsed {len(objs)} ESR objects")
        else:
            st.warning("No recognisable ESR objects found in uploaded files")

def render_additional_outputs_section(selected_assessments, configs, output_dir):
    """Auto-generate parameters.prop per interface (part of "Generate all").
    BSR (client-facing Word doc) remains an explicit on-demand action below.
    All offline, no tenant required, files go into output_dir.
    """
    from pathlib import Path as _P
    st.divider()
    st.subheader("📦 Additional Outputs")

    # ── parameters.prop (auto) ───────────────────────────────────────
    from scaffolder.parameter_injector import build_parameters_prop
    out_dir = _P(output_dir) / "parameters"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    unresolved_total = 0
    for a in selected_assessments:
        cfg = configs.get(a.interface.name)
        prop_body = build_parameters_prop(a.interface.name, cfg, None)
        import re as _re
        slug = _re.sub(r"[^\w]", "_", a.interface.name)[:80]
        (out_dir / f"{slug}.prop").write_text(prop_body, encoding="utf-8")
        unresolved_total += prop_body.count("<FILL_")
        written += 1
    if written:
        st.success(f"✅ Wrote {written} parameters.prop file(s) → {out_dir}")
        if unresolved_total > 0:
            st.caption(f"⚠ {unresolved_total} placeholder value(s) across all "
                       f"files need manual completion (grep for `<FILL_`).")

    # ── BSR (ISA-M Interface Request bundle) — on-demand ─────────────
    st.markdown("**Business Solution Request (ISA-M)** — client-facing deliverable")
    bsr_mode = st.radio("BSR render mode",
                        ["canonical", "extended"],
                        index=0, horizontal=True, key="ao_bsr_mode",
                        help="Canonical = only SAP-form fields. "
                             "Extended = includes the extensions appendix.")
    if st.button("📝 Generate Business Solution Request (ISA-M)",
                 key="ao_bsr_btn",
                 help="Builds one InterfaceRequest per interface and bundles "
                      "them into a Business Solution Request, rendered as "
                      "Word + JSON. Use 'canonical' for SAP tool import."):
        from reporter.interface_request import (
            build_business_solution_request, render_word, render_json
        )
        bsr_dir = _P(output_dir) / "bsr"
        bsr_dir.mkdir(parents=True, exist_ok=True)
        bsr = build_business_solution_request(
            selected_assessments,
            project_name=st.session_state.get("company_code", "CPI Migration"),
        )
        try:
            word_path = render_word(bsr, bsr_dir / "BusinessSolutionRequest.docx",
                                    mode=bsr_mode)
            json_path = render_json(bsr, bsr_dir / "BusinessSolutionRequest.json",
                                    mode=bsr_mode)
            st.success(f"✅ Wrote BSR → {bsr_dir} "
                       f"({len(bsr.interface_requests)} interface request(s))")
            st.caption(f"Files: `{word_path.name}`, `{json_path.name}` "
                       f"(mode: {bsr_mode})")
        except Exception as exc:
            st.error(f"BSR generation failed: {exc}")


def render_deploy_section(selected_assessments, configs, unique_targets, output_dir):
    """Deploy + Replay section for Tab 5. Tenant connection required."""
    st.divider()
    st.subheader("🚀 Deploy to CPI Tenant")

    # ── Service key (.json) picker — connect with a downloaded CF key ────────
    # Additive: an alternative to the sidebar's typed connection. Pick a key
    # from a remembered folder (or upload one) and connect; this sets the same
    # cpi_session the rest of the deploy code already uses. Nothing else changes.
    with st.expander("🔑 Connect with a service key (.json)", expanded=False):
        from fetcher.service_key import (
            list_service_keys, connect_with_service_key,
            remember_keys_dir, recall_keys_dir,
            remember_key_path, recall_key_path, keys_store_dir)
        _kd = st.text_input(
            "Folder containing your service key(s)",
            value=st.session_state.get("svc_key_dir") or recall_keys_dir()
            or keys_store_dir(),
            key="svc_key_dir",
            help="The .json keys you download from BTP. Saved externally "
                 "(~/.cpi_migrator) so it survives re-importing the project.")
        # Persist the folder as soon as it's entered (not only on connect).
        if _kd and _kd != recall_keys_dir():
            remember_keys_dir(_kd)
        _picked_path = None
        if _kd:
            _keys = list_service_keys(_kd)
            if _keys:
                # Pre-select the previously-used key on first render.
                _rk = recall_key_path()
                _rk_name = os.path.basename(_rk) if _rk else None
                if "svc_key_sel" not in st.session_state and _rk_name in _keys:
                    st.session_state["svc_key_sel"] = _rk_name
                _sel = st.selectbox("Available keys", _keys, key="svc_key_sel")
                _picked_path = str(Path(_kd) / _sel)
            else:
                st.caption("No .json files found in that folder.")
        _up = st.file_uploader("…or upload a key (.json)", type=["json"],
                               key="svc_key_upload")
        # A key saved in a previous session lives in the store and is what the
        # poller authenticates with. Surface it and allow one-click reconnect so
        # there's no need to re-upload every session.
        _saved = recall_key_path()
        if _saved and Path(_saved).exists():
            st.caption(f"💾 Saved key: `{_saved}` — the background poller uses "
                       "this exact path.")
            if not st.session_state.get("cpi_connected") and st.button(
                    "🔌 Reconnect with saved key", key="svc_key_reconnect"):
                try:
                    _sess, _base = connect_with_service_key(_saved)
                    st.session_state.cpi_session = _sess
                    st.session_state.cpi_base_url = _base
                    st.session_state.cpi_connected = True
                    remember_key_path(_saved)
                    st.success(f"✓ Reconnected via saved key → {_base}")
                    st.rerun()
                except Exception as _e:                 # noqa
                    st.error(f"✗ Could not reconnect with saved key: {_e}")
        if st.button("🔌 Connect with this key", key="svc_key_connect"):
            try:
                if _up is not None:
                    # Persist the uploaded key to a stable folder so it survives
                    # the session, shows up in the picker next time, and the
                    # background poller can authenticate with it too.
                    _kdir = keys_store_dir()
                    _path = str(Path(_kdir) / (_up.name or "service_key.json"))
                    Path(_path).write_bytes(_up.getvalue())
                    remember_keys_dir(_kdir)
                    remember_key_path(_path)
                elif _picked_path:
                    _path = _picked_path
                    remember_keys_dir(_kd)
                    remember_key_path(_path)   # pin the specific key externally
                else:
                    st.warning("Pick a key from the folder or upload one first.")
                    _path = None
                if _path:
                    _sess, _base = connect_with_service_key(_path)
                    st.session_state.cpi_session   = _sess
                    st.session_state.cpi_base_url  = _base
                    st.session_state.cpi_connected = True
                    st.success(f"✓ Connected via service key → {_base}")
                    st.rerun()
            except Exception as _e:
                st.session_state.cpi_connected = False
                st.error(f"✗ Could not connect with that key: {_e}")

    if not st.session_state.cpi_connected:
        st.warning("Connect to CPI tenant in the sidebar first.")
        return

    dc1, dc2 = st.columns(2)
    with dc1:
        auto_deploy = st.checkbox("Auto-deploy after upload", value=False,
                                   key="deploy_auto")
    with dc2:
        overwrite = st.checkbox("Overwrite existing artifacts", value=True,
                                key="deploy_overwrite")

    # ── Target package selection (per iFlow) ─────────────────────────────
    st.markdown("**📦 Target package per iFlow**")
    st.caption("Choose where each iFlow lands: an existing package pulled from "
               "your tenant, a new one (type a name), or auto (grouped by "
               "source→target).")

    if st.button("🔄 Fetch packages from tenant", key="fetch_pkgs"):
        try:
            _u = CPIUploader(st.session_state.cpi_base_url,
                             st.session_state.cpi_session)
            pkgs = _u.list_packages()
            # Keep id + display name
            st.session_state["tenant_packages"] = [
                {"id": p.get("Id", ""), "name": p.get("Name", p.get("Id", ""))}
                for p in pkgs if p.get("Id")
            ]
            st.success(f"✓ Found {len(st.session_state['tenant_packages'])} "
                       f"package(s) in the tenant")
        except Exception as e:
            st.error(f"Could not fetch packages: {e}")

    tenant_pkgs = st.session_state.get("tenant_packages", [])
    pkg_labels  = [f"{p['name']} ({p['id']})" for p in tenant_pkgs]
    # Per-iFlow target choices live in session_state["pkg_targets"][name]
    st.session_state.setdefault("pkg_targets", {})

    if selected_assessments:
        with st.expander("Set target package for each iFlow", expanded=not bool(tenant_pkgs)):
            for _row_i, a in enumerate(selected_assessments):
                nm = a.interface.name
                cols = st.columns([2, 2, 2])
                with cols[0]:
                    st.caption(nm)
                with cols[1]:
                    mode = st.selectbox(
                        "Mode", ["Auto", "Existing", "New"],
                        key=f"pkgmode_{_row_i}_{nm}",
                        label_visibility="collapsed")
                with cols[2]:
                    if mode == "Existing":
                        if pkg_labels:
                            sel = st.selectbox(
                                "Package", pkg_labels, key=f"pkgsel_{_row_i}_{nm}",
                                label_visibility="collapsed")
                            idx = pkg_labels.index(sel)
                            st.session_state["pkg_targets"][nm] = {
                                "mode": "existing",
                                "id": tenant_pkgs[idx]["id"],
                                "name": tenant_pkgs[idx]["name"]}
                        else:
                            st.caption("Fetch packages first")
                            st.session_state["pkg_targets"][nm] = {"mode": "auto"}
                    elif mode == "New":
                        newname = st.text_input(
                            "New package name", key=f"pkgnew_{_row_i}_{nm}",
                            label_visibility="collapsed",
                            placeholder="New package name")
                        st.session_state["pkg_targets"][nm] = {
                            "mode": "new", "name": newname}
                    else:
                        st.session_state["pkg_targets"][nm] = {"mode": "auto"}

    # ── Upload to Integration Suite (package zip → routed per artifact) ──
    with st.expander("📦 Upload to Integration Suite (package or bundle zip)",
                     expanded=True):
        st.caption("Upload a CPI package export (.zip with resources.cnt + "
                   "artifacts) or a single artifact bundle. Each artifact is "
                   "sent to its correct endpoint (iFlow, Message Mapping, "
                   "Value Mapping, Script Collection, etc.) with its real Id "
                   "read from the manifest. New artifacts are created; existing "
                   "ones are replaced.")

        # Source selector: reuse a package already uploaded in Tab 1, or upload
        # a new one here. This removes the double-upload (Tab 1 + Tab 5).
        _pkgs = st.session_state.get("uploaded_packages", [])
        pkg_bytes_to_upload = None
        if _pkgs:
            opts = ["(upload a new file below)"] + [
                f"{p['filename']}  ·  {p['iflow_count']} iFlow(s)" for p in _pkgs]
            choice = st.selectbox(
                "Use a package already uploaded in Tab 1, or upload a new one:",
                opts, key="suite_pkg_source",
                help="Packages you parsed in Tab 1 are listed here — no need to "
                     "upload them again.")
            if choice != opts[0]:
                idx = opts.index(choice) - 1
                pkg_bytes_to_upload = _pkgs[idx]["bytes"]
                if not st.session_state.get("suite_pkg_id_default"):
                    st.session_state["suite_pkg_id_default"] = \
                        _pkgs[idx]["filename"].rsplit(".", 1)[0]

        pkg_zip = st.file_uploader("Package or bundle (.zip)", type=["zip"],
                                   key="suite_pkg_zip")
        if pkg_zip is not None:
            pkg_bytes_to_upload = pkg_zip.getvalue()

        suite_pkg_id = st.text_input(
            "Target package Id",
            value=st.session_state.get("suite_pkg_id_default", "MigratedPackage"),
            key="suite_pkg_id",
            help="The package all artifacts go into. Created if it doesn't exist.")

        # ── Dry-run preview ──
        if pkg_bytes_to_upload and st.button("🔍 Preview upload plan", key="suite_preview_btn"):
            from fetcher.artifact_router import (ArtifactRouter,
                                                 extract_package_artifacts)
            up = CPIUploader(st.session_state.cpi_base_url,
                             st.session_state.cpi_session)
            arts = extract_package_artifacts(pkg_bytes_to_upload)
            router = ArtifactRouter(up)
            plan = router.plan(suite_pkg_id, suite_pkg_id, arts)
            st.session_state["suite_plan_artifacts"] = arts
            st.markdown(f"**Plan:** {plan.summary()}")
            if plan.artifacts:
                import pandas as pd
                st.dataframe(pd.DataFrame([{
                    "Artifact Id": a.artifact_id, "Name": a.artifact_name,
                    "Type": a.artifact_type, "Endpoint": a.endpoint,
                    "Size": f"{len(a.zip_bytes):,} B",
                } for a in plan.artifacts]), hide_index=True,
                    use_container_width=True)
            if plan.skipped:
                st.caption("Skipped: " + "; ".join(
                    f"{n} ({r})" for n, r in plan.skipped))
            st.info("Review the plan, then click **Upload** below to execute.")

        # ── Execute ──
        if pkg_bytes_to_upload and st.button("⬆ Upload to Integration Suite",
                                 type="primary", key="suite_upload_btn"):
            from fetcher import wire_log
            from fetcher.artifact_router import (ArtifactRouter,
                                                 extract_package_artifacts)
            wire_log.log_note(f"───── Upload to Integration Suite: "
                              f"{suite_pkg_id} ─────")
            up = CPIUploader(st.session_state.cpi_base_url,
                             st.session_state.cpi_session)
            arts = extract_package_artifacts(pkg_bytes_to_upload)
            if not arts:
                st.error("No artifacts found in the zip. Is it a valid CPI "
                         "package export or artifact bundle?")
            else:
                router = ArtifactRouter(up)
                plan = router.plan(suite_pkg_id, suite_pkg_id, arts)
                with st.spinner(f"Uploading {len(plan.artifacts)} artifact(s)…"):
                    results = router.execute(
                        plan, overwrite=True,
                        owner_email=st.session_state.get("owner_email", ""))
                import pandas as pd
                rows = []
                ok = 0
                for r in results:
                    icon = {"uploaded": "✅ created", "updated": "🔄 updated",
                            "skipped": "⏭ skipped"}.get(r.status, "❌ failed")
                    if r.status in ("uploaded", "updated"):
                        ok += 1
                    rows.append({"Artifact": r.artifact_id, "Result": icon,
                                 "Detail": r.message[:80]})
                st.dataframe(pd.DataFrame(rows), hide_index=True,
                             use_container_width=True)
                if ok == len(results):
                    st.success(f"✅ All {ok} artifact(s) uploaded to "
                               f"'{suite_pkg_id}'.")
                else:
                    st.warning(f"{ok}/{len(results)} succeeded. See the table "
                               f"and the full log for details.")

    # ── One-click: grab everything produced in this run as a single zip ───
    # ── Fetch real run info (MPL) from the tenant into ONE file ───────────
    # ── Background poller: collects runs into the same file, on its own ───
    with st.expander("🔁 Background run poller (runs even when you're away)",
                     expanded=False):
        import subprocess as _subprocess, sys as _sys, signal as _signal
        from fetcher.user_settings import get_setting as _gs
        pid_path = Path(output_dir) / "poller.pid"

        # ── 🔔 Alerting (ANS) — ops-retainer seed (#3) ────────────────────
        # Failed runs the poller sees become ANS events routed to email or
        # webhook. Provisioning (condition/action/subscription) is created
        # programmatically and idempotently from the wallet key; the only
        # manual step ever is clicking the email confirmation ANS sends.
        with st.container(border=True):
            st.markdown("**🔔 Alerting** — notify on failed message runs "
                        "(SAP Alert Notification)")
            _ans_en = st.checkbox(
                "Enable alerting while the poller runs",
                key="ans_alert_enabled")
            _ac1, _ac2 = st.columns([1, 2])
            _ans_tt = _ac1.selectbox("Target", ["email", "webhook"],
                                     key="ans_target_type")
            _ans_tg = _ac2.text_input(
                "Email address / webhook URL", key="ans_target",
                placeholder="ops@client.com or https://hooks…")
            st.session_state.setdefault(
                "ans_key_path", "~/.cpi_migrator/keys/ans_key.json")
            st.text_input("ANS key file (used by the background process)",
                          key="ans_key_path")

            def _ans_client_from_wallet():
                _k = (st.session_state.get("sb_service_keys") or {}).get(
                    "ans")
                if not _k:
                    import os as _aos, json as _ajs
                    _p = _aos.path.expanduser(
                        st.session_state.get("ans_key_path", ""))
                    if _p and _aos.path.exists(_p):
                        with open(_p) as _fh:
                            _k = _ajs.load(_fh)
                if not _k:
                    st.error("No ANS key: add one to the profile wallet "
                             "(slot 'ans') or set the key file path.")
                    return None
                from fetcher.ans_notifier import ANSClient
                return ANSClient(_k)

            _ab1, _ab2 = st.columns(2)
            if _ab1.button("🛠 Provision alerting (idempotent)",
                           key="ans_provision_btn",
                           disabled=not _ans_tg):
                _cl = _ans_client_from_wallet()
                if _cl:
                    try:
                        _res = _cl.ensure_provisioning(
                            _ans_tg, target_type=_ans_tt)
                        if all(_res.values()):
                            st.success(f"Provisioned: {_res}. Email "
                                       f"targets must confirm the "
                                       f"activation mail ANS just sent.")
                        else:
                            st.warning(f"Partial: {_res} — see poller/app "
                                       f"log for the failing entity.")
                    except Exception as _ae:
                        st.error(f"Provisioning failed: {_ae}")
            if _ab2.button("📨 Send test event", key="ans_test_btn"):
                _cl = _ans_client_from_wallet()
                if _cl:
                    try:
                        from fetcher.ans_notifier import build_failure_event
                        _ok = _cl.produce_event(build_failure_event(
                            {"IntegrationFlowName": "cpi_migrator_test",
                             "MessageGuid": "TEST-" + str(int(__import__(
                                 "time").time())),
                             "Status": "FAILED"},
                            tenant=st.session_state.get("sb_cpi_url", "")))
                        (st.success if _ok else st.error)(
                            "Test event accepted by ANS — check the "
                            "target." if _ok else
                            "ANS rejected the test event — check key/"
                            "provisioning.")
                    except Exception as _ae:
                        st.error(f"Test event failed: {_ae}")

        runs_path2 = Path(output_dir) / "cpi_runs.json"

        def _poller_pid():
            if not pid_path.exists():
                return None
            try:
                pid = int(pid_path.read_text().strip())
                os.kill(pid, 0)        # raises if not alive
                return pid
            except Exception:          # noqa — stale/dead pid file
                return None

        running_pid = _poller_pid()
        pc1, pc2, pc3 = st.columns([1, 1, 2])
        with pc1:
            poll_iflow = st.text_input("iFlow filter (optional)",
                                       key="poller_iflow")
        with pc2:
            poll_interval = st.number_input("Interval (s)", 15, 3600, 60,
                                            key="poller_interval")
        sk_path = _gs("service_key_path", "")
        stop_path = Path(output_dir) / "poller.stop"
        if running_pid:
            st.success(f"🟢 Running (pid {running_pid}) → {runs_path2.name}")
            if runs_path2.exists():
                import datetime as _dt
                st.caption("Last file update: " + _dt.datetime.fromtimestamp(
                    runs_path2.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"))
            if st.button("⏹ Stop poller", key="stop_poller_btn"):
                # Belt-and-suspenders: drop a stop sentinel the poller checks each
                # cycle (race-free for a detached process) AND send SIGTERM.
                try:
                    stop_path.write_text("stop", encoding="utf-8")
                except Exception:                       # noqa
                    pass
                try:
                    os.kill(running_pid, _signal.SIGTERM)
                except Exception:                       # noqa — already gone
                    pass
                try:
                    pid_path.unlink()
                except Exception:                       # noqa
                    pass
                st.success("Stop requested.")
                st.rerun()
        else:
            if not sk_path:
                st.caption("Set a **service-key path** in settings to enable the "
                           "poller (it authenticates on its own, separate from "
                           "the UI session).")
            elif st.button("▶ Start poller", key="start_poller_btn"):
                # clear any prior stop sentinel so the new poller isn't killed
                try:
                    stop_path.unlink()
                except Exception:                       # noqa
                    pass
                log_path = Path(output_dir) / "poller.log"
                cmd = [_sys.executable, "-m", "fetcher.run_poller",
                       "--service-key", sk_path, "--file", str(runs_path2),
                       "--interval", str(int(poll_interval)),
                       "--pid-file", str(pid_path),
                       "--stop-file", str(stop_path)]
                if poll_iflow:
                    cmd += ["--iflow", poll_iflow]
                # ANS alerting (no secrets in the config file: key as PATH)
                if st.session_state.get("ans_alert_enabled"):
                    import json as _aj
                    _acfg_path = Path(output_dir) / "ans_alert_config.json"
                    _acfg_path.write_text(_aj.dumps({
                        "enabled": True,
                        "key_path": st.session_state.get(
                            "ans_key_path",
                            "~/.cpi_migrator/keys/ans_key.json"),
                        "target": st.session_state.get("ans_target", ""),
                        "target_type": st.session_state.get(
                            "ans_target_type", "email"),
                    }), encoding="utf-8")
                    cmd += ["--alert-config", str(_acfg_path)]
                try:
                    _logf = open(log_path, "w", encoding="utf-8")
                    proc = _subprocess.Popen(
                        cmd, cwd=str(Path(__file__).resolve().parent),
                        start_new_session=True,
                        stdout=_logf, stderr=_subprocess.STDOUT)
                    # Write the pid from the LAUNCHER immediately so the UI has a
                    # reliable handle without waiting for the child to auth and
                    # write it (the prior race made the UI show "not running"
                    # right after start, inviting repeat clicks → orphans).
                    pid_path.write_text(str(proc.pid), encoding="utf-8")
                    # Detect an instant crash (bad service key, auth failure) so
                    # we surface the real error instead of a false "started".
                    import time as _t
                    _t.sleep(1.5)
                    if proc.poll() is not None:
                        try:
                            pid_path.unlink()
                        except Exception:               # noqa
                            pass
                        tail = ""
                        try:
                            tail = log_path.read_text("utf-8")[-600:]
                        except Exception:               # noqa
                            pass
                        st.error("Poller exited immediately — check the "
                                 "service-key path / auth. Log tail:\n\n"
                                 f"```\n{tail}\n```")
                    else:
                        st.success("Poller started — collecting into "
                                   f"{runs_path2.name}. Log: {log_path.name}")
                        st.rerun()
                except Exception as e:                  # noqa
                    st.error(f"Could not start poller: {e}")

    # Per-artifact create is the only deploy path: it creates if new and updates
    # if present (idempotent), which is also what whole-package mode fell back to
    # internally — so the old "whole package" / "delete first" toggles only added
    # noise and a failure mode (empty-shell 201) and have been removed.

    # ── Diagnostic: write the full package zip(s) to disk for manual CPI UI
    # import. Manual import gives a precise structural error vs the API's
    # generic 500, isolating bundle validity from the API call. ──
    if st.button("📄 Export artifact .zip(s) to disk (ready for manual iFlow import)",
                 key="export_artifact_btn"):
        from fetcher import wire_log
        exporter = CPIUploader(st.session_state.cpi_base_url,
                               st.session_state.cpi_session)
        iflow_dir = Path(output_dir) / "iflows"
        export_dir = Path(output_dir) / "artifact_exports"
        written = []
        for a in selected_assessments:
            name = a.interface.name
            cands = list(iflow_dir.glob(f"*{name[:20]}*.iflw")) if iflow_dir.exists() else []
            if not cands:
                try:
                    from scaffolder.iflow_scaffolder import IFlowScaffolder
                    _scaf = IFlowScaffolder(output_dir=output_dir, resources_dir=PINNED_LOCAL_DIRS["template_library_dir"], extra_resources=st.session_state.get("uploaded_resources"), passthrough=st.session_state.get("uploaded_passthrough"), gold_error_handling=st.session_state.get("gold_eh_variant"), gold_eh_replace=bool(st.session_state.get("gold_eh_replace")), gold_eh_notify=bool(st.session_state.get("gold_eh_notify")), gold_eh_sftp=bool(st.session_state.get("gold_eh_sftp")), gold_eh_company=st.session_state.get("company_code", ""))
                    cands = [Path(_scaf.scaffold(a))]
                except Exception as _e:
                    wire_log.log_note(f"artifact export: could not generate iFlow for {name}: {_e}")
                    continue
            aid = name.replace(" ", "_")[:60]
            zp = exporter.export_artifact_zip_to_disk(
                cands[0], aid, name, Path(export_dir),
                extra_artifacts=st.session_state.get("artifact_bundles", {}).get(name))
            if zp:
                written.append(str(zp))
        if written:
            st.success(f"Wrote {len(written)} artifact .zip(s) to {export_dir}")
            for w in written:
                st.code(w, language=None)
            st.caption("Import in CPI: Design → Integrations → (space) → Edit → "
                       "Add → Integration Flow → Upload — pick the *_content_FILES.zip. "
                       "This is already a valid root-level bundle; do NOT unzip/rezip it.")
        else:
            st.warning("No artifact .zip written — see the log.")

    if st.button("📦 Export full package zip(s) to disk (for manual CPI import)",
                 key="export_pkg_btn"):
        from fetcher import wire_log
        exporter = CPIUploader(st.session_state.cpi_base_url,
                               st.session_state.cpi_session)
        iflow_dir = Path(output_dir) / "iflows"
        export_dir = Path(output_dir) / "package_exports"
        written = []
        for a in selected_assessments:
            name = a.interface.name
            cands = list(iflow_dir.glob(f"*{name[:20]}*.iflw")) if iflow_dir.exists() else []
            if not cands:
                try:
                    from scaffolder.iflow_scaffolder import IFlowScaffolder
                    _scaf = IFlowScaffolder(output_dir=output_dir, resources_dir=PINNED_LOCAL_DIRS["template_library_dir"], extra_resources=st.session_state.get("uploaded_resources"), passthrough=st.session_state.get("uploaded_passthrough"), gold_error_handling=st.session_state.get("gold_eh_variant"), gold_eh_replace=bool(st.session_state.get("gold_eh_replace")), gold_eh_notify=bool(st.session_state.get("gold_eh_notify")), gold_eh_sftp=bool(st.session_state.get("gold_eh_sftp")), gold_eh_company=st.session_state.get("company_code", ""))
                    cands = [Path(_scaf.scaffold(a))]
                except Exception as _e:
                    wire_log.log_note(f"export: could not generate iFlow for {name}: {_e}")
                    continue
            pkg_id = generate_package_name(
                "", a.interface.sender_system or "SRC",
                a.interface.receiver_system or "TGT",
                a.interface.namespace or "").replace(" ", "_")[:50]                 if "generate_package_name" in dir() else name.replace(" ", "_")[:50]
            try:
                from scaffolder.pipeline_scaffolder import generate_package_name as _gpn
                pkg_id = _gpn("", a.interface.sender_system or "SRC",
                              a.interface.receiver_system or "TGT",
                              a.interface.namespace or "").replace(" ", "_")[:50]
            except Exception:
                pass
            zp = exporter.export_full_package_to_disk(
                cands[0], pkg_id, pkg_id, name.replace(" ", "_")[:60], name,
                Path(export_dir),
                extra_artifacts=st.session_state.get("artifact_bundles", {}).get(name))
            if zp:
                written.append(str(zp))
        if written:
            st.success(f"Wrote {len(written)} package zip(s) to {export_dir}")
            for w in written:
                st.code(w, language=None)
            st.caption("Import in CPI: Design → Integrations → (your space) → "
                       "Import — pick the package zip. The UI error (if any) is "
                       "precise, unlike the API 500.")
        else:
            st.warning("No package zips written — see the log.")

    _eh_choice = st.selectbox(
        "Gold-standard error handling (flows without an exception subprocess)",
        ["Off (pure fidelity)", "Error End (Guidelines baseline)",
         "Escalation End", "Message End (don't throw error)"],
        key="gold_eh_choice",
        help="Injects the SAP Design Guidelines 'Handle Errors Gracefully' "
             "pattern — Error Start → error-capture Groovy (MPL attachments, "
             "custom status) → chosen end — ONLY into flows that have no "
             "exception subprocess. Flows that already handle errors are "
             "never touched.")
    st.session_state["gold_eh_variant"] = {
        "Off (pure fidelity)": None,
        "Error End (Guidelines baseline)": "error_end",
        "Escalation End": "escalation_end",
        "Message End (don't throw error)": "message_end",
    }[_eh_choice]
    if st.session_state["gold_eh_variant"]:
        _eh_scope = st.radio(
            "Apply to",
            ["Only flows without error handling",
             "Replace existing error handling too"],
            key="gold_eh_scope", horizontal=True,
            help="'Replace' swaps each flow's current main-process exception "
                 "subprocess (including mail-alert wiring) for the chosen "
                 "gold variant — for clients who want one standardized "
                 "pattern. LIP-level exception subprocesses are always kept "
                 "(they scope to their own process).")
        st.session_state["gold_eh_replace"] = \
            _eh_scope.startswith("Replace")
        st.session_state["gold_eh_notify"] = st.checkbox(
            "Notify by mail (RCI093 alert pattern, externalized SMTP "
            "parameters)",
            key="gold_eh_notify_cb",
            help="Error-report CM → Send → Mail receiver, body and shape "
                 "verbatim from RCI093's production alert with {company} "
                 "filled from Tab 1 · Company code. Subject defaults to "
                 "'iFlow name: reason' (the capture script classifies the "
                 "error: Endpoint / Incoming message / Outgoing message / "
                 "Mapping / Execution). If the source flow already defines "
                 "mail parameters (ConnectionError_Mail* family or a mail "
                 "credential), those are REUSED so the client's configured "
                 "values flow straight in.")
        st.session_state["gold_eh_sftp"] = st.checkbox(
            "Also archive the error report to SFTP",
            key="gold_eh_sftp_cb",
            help="RCI093's own shape: the alert body fans out through a "
                 "parallel Multicast to the mail leg AND an SFTP receiver "
                 "(+ MPL attachment script). Connection externalized as "
                 "{{ALERT_SFTP_HOST}}, _DIRECTORY, _FILENAME, _CRED, _AUTH, "
                 "_TIMEOUT…")
    else:
        st.session_state["gold_eh_replace"] = False
        st.session_state["gold_eh_notify"] = False
        st.session_state["gold_eh_sftp"] = False
    if st.button("⬆ Upload all iFlows to CPI", type="primary",
                 key="deploy_btn"):
        from fetcher import wire_log
        wire_log.log_note(f"───── Upload run started — {len(selected_assessments)} iFlow(s) ─────")
        uploader = CPIUploader(
            st.session_state.cpi_base_url,
            st.session_state.cpi_session,
        )
        iflow_dir = Path(output_dir) / "iflows"
        progress  = st.progress(0)
        results   = []
        pending_deploys = {}   # artifact_id -> UploadResult, resolved after loop

        _uploaded_ids_this_run = set()
        for i, a in enumerate(selected_assessments):
            name   = a.interface.name
            cfg    = configs.get(name)
            status = st.empty()
            status.text(f"Uploading {name}…")

            # one artifact id per run: the same flow can arrive from two
            # pulled packages (e.g. the source package AND a previous
            # showcase package) — uploading both means the second silently
            # clobbers the first via delete+recreate (seen in a live run)
            _aid = CPIUploader.sanitize_package_id(name)
            if _aid in _uploaded_ids_this_run:
                wire_log.log_note(
                    f"SKIP duplicate: '{name}' resolves to artifact id "
                    f"'{_aid}' which was already uploaded in this run "
                    "(same flow present in multiple loaded packages)")
                st.warning(f"⏭ {name}: duplicate of an already-uploaded "
                           "flow in this run — skipped")
                continue
            _uploaded_ids_this_run.add(_aid)

            # EOIO check
            if needs_eoio_pattern(a.interface):
                eoio_paths = generate_eoio_pattern(a, output_dir)
                st.info(f"⚡ EOIO pattern generated for {name}: "
                        f"{len(eoio_paths)} iFlows")

            # Find iflw — generate on demand if it's not already on disk, so
            # the upload never silently skips just because Generate-all wasn't
            # run in this exact session / output dir.
            # Always (re)generate the iFlow fresh for upload. Globbing for a
            # pre-existing file risked matching a STALE/partial .iflw whose meta
            # dir no longer matched — on the tenant that produced a tiny ~1.7KB
            # bundle and "Error while loading the details of the integration
            # flow" (Settlement_Batch_EOIO). Regeneration is cheap and
            # guarantees a correct iflw + validated manifest + bundled resources
            # every time.
            candidates = []
            try:
                from scaffolder.iflow_scaffolder import IFlowScaffolder
                _scaf = IFlowScaffolder(output_dir=output_dir, resources_dir=PINNED_LOCAL_DIRS["template_library_dir"], extra_resources=st.session_state.get("uploaded_resources"), passthrough=st.session_state.get("uploaded_passthrough"), gold_error_handling=st.session_state.get("gold_eh_variant"), gold_eh_replace=bool(st.session_state.get("gold_eh_replace")), gold_eh_notify=bool(st.session_state.get("gold_eh_notify")), gold_eh_sftp=bool(st.session_state.get("gold_eh_sftp")), gold_eh_company=st.session_state.get("company_code", ""))
                _tid = target_ids.get(name, "s4hana_cloud") if "target_ids" in dir() else "s4hana_cloud"
                _resolved = st.session_state.get("resolutions", {}).get(name, {}).get(_tid)
                _gen = _scaf.scaffold(a, resolved=_resolved)
                candidates = [Path(_gen)]
                wire_log.log_note(f"Generated iFlow for '{name}': {Path(_gen).name}")
            except Exception as _ge:
                wire_log.log_note(f"Could NOT generate iFlow for '{name}': {_ge}")
                import logging as _lg
                _lg.getLogger("cpi.upload").error(
                    "Upload skipped for %s — generation failed: %s", name, _ge)
            if candidates:
                from scaffolder.pipeline_scaffolder import (
                    generate_package_name, generate_iflow_name,
                    generate_package_display_name
                )
                # Resolve the target package for THIS iFlow from the per-iFlow
                # selector. Modes: existing (use chosen id), new (create named),
                # auto (derive from source→target, the default).
                target = st.session_state.get("pkg_targets", {}).get(name, {"mode": "auto"})

                if target.get("mode") == "existing" and target.get("id"):
                    # Use the tenant's Id verbatim — it's already valid; don't
                    # re-sanitize (would risk altering a real id).
                    pkg_id      = target["id"]
                    pkg_display = target.get("name", pkg_id)
                    pkg_preexists = True
                elif target.get("mode") == "new" and target.get("name"):
                    pkg_id = CPIUploader.sanitize_package_id(target["name"])
                    pkg_display = target["name"]
                    pkg_preexists = False
                else:
                    # Auto: prefer the SOURCE package identity carried on the
                    # interface (records built from uploaded CPI packages) so
                    # the tenant package MIRRORS the original — same name, and
                    # every flow lands with its true siblings. The synthesized
                    # Sender/Receiver convention name is the fallback for
                    # PI/PO-export records, which have no source package.
                    _src_pkg = (getattr(a.interface, "package", "") or "").strip()
                    if _src_pkg:
                        import re as _re
                        # browser download counters ('Pkg (4)', 'Pkg__4_') are
                        # filename noise, not package identity — strip for the
                        # display/id; the RAW name (kept on the interface)
                        # still drives on-disk zip matching.
                        _stem = _re.sub(r"(?:\s*\(\d+\)|__\d+_?)\s*$", "",
                                        _src_pkg).strip(" _")
                        pkg_display = (_stem or _src_pkg).replace("_", " ").strip()
                        pkg_id = CPIUploader.sanitize_package_id(pkg_display)
                        pkg_preexists = False
                    else:
                        # Auto: Id derived from source/target; Name follows convention.
                        pkg_id = generate_package_name(
                            "",
                            a.interface.sender_system or "SRC",
                            a.interface.receiver_system or "TGT",
                            a.interface.namespace or "",
                        ).replace(" ", "_")[:50]
                        pkg_display = generate_package_display_name(
                            a.interface.sender_system or "Source",
                            a.interface.receiver_system or "Target",
                            a.interface.namespace or "",
                        )
                        pkg_preexists = False

                # Ensure the package exists. For an existing tenant package we
                # skip creation (it's already there); otherwise create the shell
                # so the per-artifact create has a package to land in.
                if pkg_preexists:
                    pkg_ok = True
                else:
                    pkg_ok = uploader.ensure_package(
                        pkg_id, pkg_display,
                        owner_email=st.session_state.get("owner_email", "")
                        or st.session_state.cfg.get("destinations", {}).get("owner_email", ""))
                if not pkg_ok:
                    from fetcher.cpi_uploader import UploadResult
                    results.append(UploadResult(
                        interface_name=name, package_id=pkg_id,
                        artifact_id=name.replace(" ", "_")[:60],
                        status="failed",
                        message="Package could not be created — upload skipped "
                                "(see log for the API error).",
                    ))
                    progress.progress((i + 1) / len(selected_assessments))
                    continue
                _art_id = name.replace(" ", "_")[:60]
                _extras = st.session_state.get("artifact_bundles", {}).get(name)
                # Regenerated flows (real source XML) ship their REAL resources
                # from __meta — the clone-era synthesized stubs (<name>_mapping
                # .mmap / <name>_process.groovy) must NOT ride along: the stub
                # mmap fails the editor's mapping migration ('Unused Resource:
                # ... Stream after migration is null', seen on RCI093 v1.0.0).
                if (getattr(a.interface, "source_iflow_xml", "") or "").strip():
                    _extras = None
                result = uploader.upload_iflow(
                    candidates[0], pkg_id, _art_id, name,
                    overwrite=overwrite, extra_artifacts=_extras,
                    sender_adapter=getattr(a.interface, "sender_adapter", ""),
                    receiver_adapter=getattr(a.interface, "receiver_adapter", ""),
                )
                if result.status in ("uploaded", "updated") and auto_deploy:
                    deploy_status = uploader.deploy_iflow(result.artifact_id)
                    if deploy_status == "started":
                        # Trigger only — don't block here. All triggered deploys
                        # are polled TOGETHER after the loop so their settle
                        # windows overlap (a per-iFlow blocking wait was the run's
                        # main time cost: ~16s × N).
                        result.status  = "deploying"
                        result.message = "Deployment triggered — awaiting runtime status"
                        pending_deploys[result.artifact_id] = result
                    else:
                        result.status = "deploy-failed"
                        # surface the tenant's real deploy reason if available
                        rec = getattr(uploader, "last_deploy_recommendation", None)
                        if rec is None:
                            rec = uploader.fetch_deploy_error_detail(
                                result.artifact_id)
                        if rec is not None:
                            result.recommendation = rec
                            result.message = (f"Deploy failed — {rec.cause} "
                                              f"FIX: {rec.recommendation}")
                results.append(result)

            progress.progress((i + 1) / len(selected_assessments))

        # ── Resolve all triggered deploys together (overlapping settle windows) ──
        if pending_deploys:
            st.caption(f"Waiting for {len(pending_deploys)} deployment(s) to "
                       f"settle (polled together)…")
            finals = uploader.wait_for_deploys(
                list(pending_deploys.keys()), timeout=120, interval=5)
            for _aid, _res in pending_deploys.items():
                _final = finals.get(_aid, "")
                if _final == "STARTED":
                    _res.status  = "deployed"
                    _res.message = "Deployed — runtime STARTED"
                elif _final == "ERROR":
                    _res.status = "deploy-failed"
                    _rec = uploader.fetch_deploy_error_detail(_aid)
                    if _rec is not None:
                        _res.recommendation = _rec
                        _res.message = (f"Runtime ERROR — {_rec.cause} "
                                        f"FIX: {_rec.recommendation}")
                    else:
                        _res.message = "Deploy accepted but runtime ERROR"
                else:
                    _res.status  = "deployed"
                    _res.message = (f"Deploy accepted — runtime status "
                                    f"'{_final or 'STARTING'}' (still coming up)")

        # Show results
        import pandas as pd
        if results:
            df = pd.DataFrame([
                {"Interface": r.interface_name,
                 "Package":   r.package_id,
                 "Status":    r.status,
                 "Message":   (r.message or "")[:80]}
                for r in results
            ])
            st.dataframe(df, hide_index=True, use_container_width=True)
            ok = sum(1 for r in results if r.status in ("uploaded", "updated", "deployed"))
            st.success(f"✅ {ok}/{len(results)} iFlows uploaded successfully")

            # Surface the tenant's REAL failure reason + recommended fix for any
            # interface that failed (upload or deploy). This is the diagnostic
            # layer: the untruncated tenant message + a concrete recommendation,
            # tagged with whether it's safe to auto-fix later.
            failed = [r for r in results
                      if getattr(r, "recommendation", None) is not None]
            if failed:
                st.markdown("#### 🔎 Failure diagnosis")
                _FIX_BADGE = {"structural": "🟢 structural (safe to auto-fix)",
                              "substitution": "🟡 substitution (bounded)",
                              "semantic": "🔴 semantic (review needed)",
                              "auth": "🔑 auth (your action)",
                              "unknown": "⚪ unrecognized"}
                for r in failed:
                    rec = r.recommendation
                    with st.expander(f"❌ {r.interface_name} — {r.status}"):
                        st.write(f"**Cause:** {rec.cause}")
                        st.write(f"**Recommended fix:** {rec.recommendation}")
                        st.caption(_FIX_BADGE.get(rec.fix_class, rec.fix_class)
                                   + (f"  ·  HTTP {rec.status_code}"
                                      if rec.status_code else ""))
                        if rec.raw_error:
                            st.code(rec.raw_error, language="text")

            # Any failure WITHOUT a structured recommendation — show the FULL
            # tenant message so the real reason is never hidden by truncation.
            other_failed = [r for r in results
                            if r.status not in ("uploaded", "updated", "deployed")
                            and getattr(r, "recommendation", None) is None]
            if other_failed:
                st.markdown("#### ⚠ Upload failures (full tenant message)")
                for r in other_failed:
                    with st.expander(f"❌ {r.interface_name} — {r.status}"):
                        st.code(r.message or "(no message returned)", language="text")

    st.divider()
    st.subheader("🔄 Historic Payload Replay")
    st.caption("Extract real PI/PO messages and replay through CPI iFlows "
               "for validation.")

    if not st.session_state.pi_connected:
        st.info("PI/PO connection required for live replay. "
                "Connect in Tab 1 first.")
    else:
        rp_iface = st.selectbox(
            "Interface to replay",
            [a.interface.name for a in selected_assessments],
            key="replay_iface",
        )
        rp_count = st.number_input("Max payloads to extract",
                                    value=5, min_value=1, max_value=50,
                                    key="replay_count")
        rp_path  = st.text_input("CPI iFlow endpoint path",
                                  value=f"/http/{rp_iface.replace(' ','_')[:30]}",
                                  key="replay_path")

        if st.button("▶ Extract & Replay", key="replay_btn"):
            with st.spinner("Extracting payloads from PI/PO…"):
                replayer = PayloadReplayer(
                    pi_base_url=st.session_state.get("pi_base_url",""),
                    pi_session=st.session_state.get("pi_session"),
                    cpi_base_url=st.session_state.cpi_base_url,
                    cpi_session=st.session_state.cpi_session,
                    output_dir=output_dir,
                )
                payloads = replayer.extract_payloads(rp_iface, int(rp_count))

            if payloads:
                st.info(f"Extracted {len(payloads)} payload(s) — replaying…")
                results = replayer.replay_all(payloads, rp_path)
                report  = replayer.generate_report(results)

                passed = sum(1 for r in results if r.match)
                if passed == len(results):
                    st.success(f"✅ All {passed}/{len(results)} payloads passed")
                else:
                    st.warning(f"⚠ {passed}/{len(results)} passed — "
                               f"review replay_report.xlsx")

                buf = io.BytesIO()
                with open(report, "rb") as f:
                    buf.write(f.read())
                buf.seek(0)
                st.download_button("⬇ Download replay report",
                    data=buf.getvalue(),
                    file_name="replay_report.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            else:
                st.warning("No payloads extracted. Check PI/PO connection "
                           "and interface name.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 ADDITION — Migration Ceiling panel (shown above interface table)
# ═══════════════════════════════════════════════════════════════════════════════

def render_migration_ceiling_summary(assessments: list):
    """Show ceiling summary + run classifier. Call at top of Tab 2."""
    if not assessments:
        return

    st.subheader("🔍 Migration Ceiling Analysis")
    st.caption(
        "Classifies each interface by what the tool can handle vs. "
        "what needs a specialist decision."
    )

    if st.button("▶ Classify all interfaces", key="ceiling_btn",
                 type="primary"):
        classifier = MigrationCeilingClassifier()
        ceilings   = classifier.classify_all(
            assessments,
            configs=st.session_state.configs,
            clean_core_reports=st.session_state.clean_core,
        )
        st.session_state.ceilings = {c.interface_name: c for c in ceilings}

    if st.session_state.ceilings:
        ceilings = list(st.session_state.ceilings.values())
        summary  = ceiling_summary(ceilings)

        # Summary metrics
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("🟢 Auto",       summary["auto"])
        c2.metric("🟡 Guided",     summary["guided"])
        c3.metric("🔴 Specialist", summary["specialist"])
        c4.metric("Avg automation", f"{summary['avg_automation']:.0f}%")
        c5.metric("Client decisions", summary["client_decisions"])

        # Specialist interfaces — expanded detail
        spec_ceilings = [c for c in ceilings if c.tier == TIER_SPECIALIST]
        if spec_ceilings:
            st.divider()
            st.markdown("**🔴 Specialist interfaces — client decision required:**")
            for c in spec_ceilings:
                with st.expander(
                    f"🔴 {c.interface_name} — score {c.score} | "
                    f"automation ~{c.automation_pct}%"
                ):
                    if c.triggered_by:
                        st.markdown("**Blockers:**")
                        for t in c.triggered_by:
                            st.markdown(f"- **{t.description}**: {t.reason}")
                    if c.options:
                        st.markdown("**Options:**")
                        for opt in c.options[:4]:
                            st.markdown(f"  {opt}")
                    if c.extra_cost_min_usd:
                        st.info(
                            f"💰 Additional cost if escalated: "
                            f"${c.extra_cost_min_usd:,}–${c.extra_cost_max_usd:,} USD"
                        )
                    if c.manual_tasks:
                        st.markdown("**Manual tasks required regardless:**")
                        for task in c.manual_tasks:
                            st.markdown(f"- {task}")

        st.divider()


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5 ADDITION — Commercial Proposal generator
# ═══════════════════════════════════════════════════════════════════════════════

def render_proposal_section(selected_assessments, configs, output_dir,
                             project_name="CPI Migration",
                             company_code="CLIENT"):
    """Render proposal generator panel. Call from Tab 5."""
    st.divider()
    st.subheader("💼 Commercial Proposal")
    st.caption(
        "Generates a client-facing quote AND a private internal cost sheet. "
        "Never share the internal sheet with the client."
    )

    # Pricing settings
    with st.expander("⚙ Pricing settings"):
        pc1, pc2, pc3 = st.columns(3)
        with pc1:
            price_auto_min   = st.number_input("🟢 Auto min ($)",
                value=1000, step=100, key="prop_auto_min")
            price_auto_max   = st.number_input("🟢 Auto max ($)",
                value=1500, step=100, key="prop_auto_max")
        with pc2:
            price_guided_min = st.number_input("🟡 Guided min ($)",
                value=2000, step=100, key="prop_guided_min")
            price_guided_max = st.number_input("🟡 Guided max ($)",
                value=3000, step=100, key="prop_guided_max")
        with pc3:
            price_spec_min   = st.number_input("🔴 Specialist min ($)",
                value=4000, step=500, key="prop_spec_min")
            price_spec_max   = st.number_input("🔴 Specialist max ($)",
                value=7000, step=500, key="prop_spec_max")

        ic1, ic2, ic3 = st.columns(3)
        with ic1:
            day_rate = st.number_input("Your day rate ($)",
                value=800, step=50, key="prop_day_rate",
                help="Your internal cost per day — stays in the private sheet")
        with ic2:
            target_margin = st.number_input("Target margin (%)",
                value=60, min_value=10, max_value=90,
                key="prop_margin")
        with ic3:
            risk_buffer = st.number_input("Risk buffer (%)",
                value=15, min_value=0, max_value=30,
                key="prop_risk")

        p_name    = st.text_input("Project name",
            value=project_name, key="prop_name")
        co_code   = st.text_input("Client/company code",
            value=company_code, key="prop_co")

    if st.button("📊 Generate Commercial Proposal", type="primary",
                 key="prop_btn"):
        # Ensure ceilings are classified
        if not st.session_state.ceilings:
            classifier = MigrationCeilingClassifier()
            ceilings_list = classifier.classify_all(
                selected_assessments,
                configs=configs,
                clean_core_reports=st.session_state.clean_core,
            )
            st.session_state.ceilings = {c.interface_name: c
                                          for c in ceilings_list}

        pricing = PricingConfig(
            price_auto_min=price_auto_min,
            price_auto_max=price_auto_max,
            price_guided_min=price_guided_min,
            price_guided_max=price_guided_max,
            price_specialist_min=price_spec_min,
            price_specialist_max=price_spec_max,
            your_day_rate_usd=day_rate,
            target_margin_pct=target_margin,
            risk_buffer_pct=risk_buffer,
        )

        gen = ProposalGenerator(output_dir=output_dir)
        with st.spinner("Generating proposals…"):
            client_xl, internal_xl, docx = gen.generate(
                assessments=selected_assessments,
                ceilings=list(st.session_state.ceilings.values()),
                configs=configs,
                pricing=pricing,
                project_name=p_name,
                company_code=co_code,
            )

        # Show summary
        if st.session_state.ceilings:
            summary = ceiling_summary(list(st.session_state.ceilings.values()))
            from reporter.proposal_generator import ProjectProposal
            # Quick estimate for display
            total = len(selected_assessments)
            auto  = summary["auto"]
            guid  = summary["guided"]
            spec  = summary["specialist"]

            st.success("✅ Proposal generated!")
            m1, m2, m3 = st.columns(3)
            m1.metric("🟢 Auto",       auto)
            m2.metric("🟡 Guided",     guid)
            m3.metric("🔴 Specialist", spec)

        # Download buttons
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            with open(client_xl, "rb") as f:
                st.download_button(
                    "⬇ Client Proposal (share)",
                    data=f.read(),
                    file_name=client_xl.name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    type="primary",
                )
        with col_b:
            with open(internal_xl, "rb") as f:
                st.download_button(
                    "🔒 Internal Cost Sheet",
                    data=f.read(),
                    file_name=internal_xl.name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
        with col_c:
            with open(docx, "rb") as f:
                st.download_button(
                    "📄 Proposal Word Doc",
                    data=f.read(),
                    file_name=docx.name,
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )

        st.warning(
            "🔒 **Never share the Internal Cost Sheet with the client.** "
            "It contains your day rate and margin breakdown."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# GROOVY LIBRARY WIDGET
# ═══════════════════════════════════════════════════════════════════════════════

def render_groovy_library(interface_name: str = "",
                           sender_adapter: str = "",
                           receiver_adapter: str = "",
                           has_mapping: bool = False):
    """Render searchable Groovy script library panel."""
    st.subheader("📚 Groovy Script Library")
    st.caption("Community-verified scripts. Click a script to copy it.")

    from fetcher.groovy_library import GroovyLibrary
    lib = GroovyLibrary()

    gc1, gc2 = st.columns([2, 1])
    with gc1:
        query = st.text_input("Search scripts",
                               placeholder="IDoc transform, JSON convert, error handler…",
                               key="groovy_lib_query")
    with gc2:
        category = st.selectbox("Category",
                                 ["(all)"] + lib.list_categories(),
                                 key="groovy_lib_cat")

    # Smart suggestions if interface context given
    if interface_name and not query:
        suggestions = lib.suggest_for_interface(
            interface_name, sender_adapter, receiver_adapter, has_mapping
        )
        st.caption(f"💡 Suggested for **{interface_name}**:")
        scripts = suggestions
    elif query:
        scripts = lib.search(query, top_n=6)
    elif category != "(all)":
        scripts = lib.get_by_category(category)
    else:
        scripts = lib._catalog[:6]

    if not scripts:
        st.info("No scripts found. Try a different search term.")
        return

    for script in scripts:
        with st.expander(f"**{script.title}** `[{script.category}]`"):
            st.caption(script.description)
            st.caption(f"Adapters: {', '.join(script.adapters)} | Source: {script.source}")
            st.code(script.code, language="groovy")


# ═══════════════════════════════════════════════════════════════════════════════
# CAPABILITY SOLVER  (corpus_pipeline + solver — the EVALUATE→FETCH→SELECT panel)
# ═══════════════════════════════════════════════════════════════════════════════

@st.cache_resource(show_spinner=False)
def _load_capability_corpus(_packages, sig: str):
    """Build the capability corpus once and cache it (heavy — 40k+ caps).
    Cached by `sig` (a stable signature of the uploaded packages) so it only
    rebuilds when the uploaded set changes. `_packages` is underscore-prefixed
    so Streamlit doesn't try to hash the raw bytes."""
    from library_builder.corpus_pipeline import build_corpus
    return build_corpus(packages=_packages)


@st.cache_resource(show_spinner=False)
def _load_capability_corpus_dir(corpus_dir: str):
    """Build the capability corpus from a directory on disk (e.g. the full
    Final/ harvest). Cached by the path so it only rebuilds if the path changes.
    walk_corpus recurses the folder and keys by path-qualified name, so
    same-named files across packages don't collapse."""
    from library_builder.corpus_pipeline import build_corpus
    return build_corpus(path=corpus_dir)


def render_capability_solver():
    """Demonstrates the capability pipeline + reasoning layer: type a CPI
    requirement, see it decomposed and matched against the learned capability
    catalogs (all 8 types: mmap / groovy / schema / xslt / js / props / iflw /
    pi), built from the uploaded packages.
    Honest by design: every result is a *reasoned* suggestion the user
    validates — never an SAP-certain answer."""
    st.subheader("🧠 Capability Solver (experimental)")
    st.caption("Decomposes a requirement and matches it to learned capabilities "
               "across all artifact types. Suggestions are reasoned, not "
               "tenant-verified — confirm before use.")

    # Two sources for the capability catalog:
    #  (a) a local directory (e.g. the full Final/ harvest on disk) — best,
    #      since it can hold the entire 34k-file corpus, and
    #  (b) the packages uploaded in Tab 1.
    # The directory path wins when given (it's the richer source).
    # Pinned to Resources/Corpus (see PINNED_LOCAL_DIRS) — no longer selectable.
    corpus_dir = PINNED_LOCAL_DIRS["capability_corpus_dir"]
    if os.path.isdir(corpus_dir):
        try:
            _n = sum(1 for _ in os.scandir(corpus_dir))
            st.caption(f"📁 Capability corpus: `{corpus_dir}` — ✓ {_n} entr(y/ies)")
        except OSError:
            st.caption(f"📁 Capability corpus: `{corpus_dir}` — ✓ set")
    else:
        st.caption(f"📁 Capability corpus: `{corpus_dir}` — ⚠ not found on disk "
                   "(falls back to Tab-1 uploads)")

    packages = st.session_state.get("uploaded_packages") or []
    corpus = None

    if corpus_dir and os.path.isdir(corpus_dir):
        try:
            corpus = _load_capability_corpus_dir(corpus_dir)
        except Exception as e:  # noqa
            st.warning(f"Could not build catalog from folder: {e}")
    elif corpus_dir:
        st.warning(f"Folder not found: {corpus_dir}")

    if corpus is None:
        if not packages:
            st.info("Point the **source folder** above at your harvest (e.g. "
                    "Final/), or upload CPI package(s) in **Tab 1 · Source**, "
                    "to build the capability catalog.")
            return
        sig = "|".join(sorted(p.get("filename", "") for p in packages))
        try:
            corpus = _load_capability_corpus(packages, sig)
        except Exception as e:  # noqa
            st.warning(f"Could not build capability corpus: {e}")
            return

    rep = corpus.report()
    if rep["capabilities"] == 0:
        st.info("No recognized capability artifacts found yet "
                "(groovy / xslt / xsd / wsdl / edmx / mmap / "
                "js / prop / propdef / iflw).")
        return
    cols = st.columns(4)
    cols[0].metric("Capabilities", f"{rep['capabilities']:,}")
    cols[1].metric("Files", f"{rep['files']:,}")
    cols[2].metric("Types", len(rep["types"]),
                   help="Covered capability types: "
                        + ", ".join(rep["types"]))
    # Non-capability files in the packages (images, PDFs, JSON, jars, etc.) —
    # not artifact types we model. All capability engines are built.
    non_cap = sum(rep["classify"]["unknown"].values())
    cols[3].metric("Non-capability files", f"{non_cap:,}",
                   help="Files that aren't a modeled capability artifact "
                        "(images, PDFs, JSON, jars, docs). All 8 capability "
                        "engines (mmap/groovy/schema/xslt/js/props/iflw/pi) "
                        "are built.")

    # ── Part 1: solve directly from a parsed input (requirement / MA / PI) ──
    # If interfaces have been parsed (Tab 2/3), let the user pick one and run the
    # bridge — no manual typing. The bridge carries source/target slots so the
    # field-spec layer (part 2) pre-fills them.
    parsed = st.session_state.get("assessments") or []
    iface_objs = []
    for a in parsed:
        obj = getattr(a, "interface", a)
        if getattr(obj, "name", None):
            iface_objs.append(obj)
    chosen_summary = None
    if iface_objs:
        names = ["(type a requirement instead)"] + [o.name for o in iface_objs]
        pick = st.selectbox("Solve from a parsed interface", names,
                            key="cap_solver_pick")
        if pick != names[0]:
            obj = next(o for o in iface_objs if o.name == pick)
            try:
                from library_builder.requirement_bridge import solve_for
                chosen_summary = solve_for(obj, corpus)
                st.caption(f"Derived requirement: _{chosen_summary['requirement']}_")
            except Exception as e:   # noqa
                st.warning(f"Could not bridge this interface: {e}")

    req = st.text_input("Describe what you need in CPI",
                        placeholder="Parse the JSON payload, look up the country "
                        "code, then log the result as an attachment",
                        key="cap_solver_req")
    if not req and chosen_summary is None:
        term = st.text_input("…or search capabilities directly",
                             placeholder="parse json, date format, value mapping",
                             key="cap_solver_search")
        if term:
            for cid, score in corpus.search(term, top_n=8):
                st.write(f"`{score:.2f}`  {cid}")
        return

    from library_builder.solver import solution_summary
    if chosen_summary is not None:
        summary = chosen_summary       # from the bridge (carries slots)
    else:
        sol = corpus.solve(req)
        summary = solution_summary(sol)

    st.markdown("**Proposed solution** "
                f"(confidence: _{summary['confidence']}_)")
    if summary["needs_tenant_test"]:
        st.warning("⚠️ Uses SAP runtime bindings — must be tested on your tenant.")
    for i, step in enumerate(summary["steps"], 1):
        with st.expander(f"Step {i} · `{step['ctype']}` · score {step['score']}"):
            st.caption(f"For need: _{step['need']}_")
            st.write(f"Use capability: **{step['use']}**")
    if summary["unmet"]:
        st.info("No confident match for: " + "; ".join(summary["unmet"]))

    # ── Part 2: editable setup fields derived from the solution ───────────
    # Externalized params + source/target, pre-filled, editable; user edits
    # persist across re-runs (the 'requirements change mid-call' safety).
    _render_solution_fields(summary, corpus, key_prefix="capsolve")


def _render_solution_fields(summary: dict, corpus, key_prefix: str):
    """Render the field spec (part 2) as editable Streamlit inputs in session
    state, preserving user edits across re-proposals. `summary` should carry
    `source_target_slots` (from requirement_bridge.solve_for); if absent, only
    externalized params from matched capabilities are shown."""
    from library_builder.field_spec import build_field_spec, merge_edits

    spec = build_field_spec(summary, corpus)
    if not spec.fields:
        return

    store_key = f"{key_prefix}_field_values"
    prior = st.session_state.get(store_key, {})
    spec = merge_edits(spec, prior)

    st.divider()
    st.markdown("**Setup fields** — pre-filled where known, editable. "
                "Externalized parameters are set here, not hardcoded.")

    groups = [("source", "📥 Sources"), ("target", "📤 Targets"),
              ("parameter", "🔧 Externalized parameters"),
              ("config", "⚙️ Configuration")]
    new_values = {}
    for gkey, gtitle in groups:
        gfields = spec.by_group(gkey)
        if not gfields:
            continue
        st.caption(gtitle)
        for f in gfields:
            val = st.text_input(
                f.label, value=f.value, key=f"{key_prefix}_{f.key}",
                help=f.hint or None,
                placeholder="(set via parameter — not hardcoded)"
                if gkey == "parameter" else "")
            edited = (val != f.suggested)
            new_values[f.key] = {"value": val, "user_edited": edited}
    st.session_state[store_key] = new_values

    filled = sum(1 for v in new_values.values() if v["value"])
    st.caption(f"{filled}/{len(new_values)} fields set. "
               "Edits persist if you re-run the solver with changed requirements.")


# ═══════════════════════════════════════════════════════════════════════════════
# MIGRATION CEILING + INTERVENTION PANEL
# ═══════════════════════════════════════════════════════════════════════════════

def render_ceiling_and_intervention(
    selected_assessments, configs, clean_core_reports,
    verification_reports, output_dir
):
    """Migration ceiling classification + human intervention estimate."""

    st.divider()
    st.subheader("🚦 Migration Ceiling Classification")
    st.caption("Classifies each interface as AUTO / GUIDED / SPECIALIST.")

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("▶ Classify all interfaces", type="primary",
                     key="ceiling_run"):
            classifier = MigrationCeilingClassifier()
            with st.spinner("Classifying…"):
                for a in selected_assessments:
                    name = a.interface.name
                    ceiling = classifier.classify(
                        a,
                        cfg=configs.get(name),
                        clean_core_report=clean_core_reports.get(name),
                    )
                    st.session_state.ceilings[name] = ceiling
            st.success(f"Classified {len(selected_assessments)} interfaces")

    with col_b:
        if st.session_state.ceilings:
            summary = ceiling_summary(list(st.session_state.ceilings.values()))
            mc1, mc2, mc3 = st.columns(3)
            mc1.metric("🟢 AUTO",       summary.get("auto", 0))
            mc2.metric("🟡 GUIDED",     summary.get("guided", 0))
            mc3.metric("🔴 SPECIALIST", summary.get("specialist", 0))

    if st.session_state.ceilings:
        import pandas as pd
        ceiling_rows = []
        for name, c in st.session_state.ceilings.items():
            icon = {"AUTO": "🟢", "GUIDED": "🟡", "SPECIALIST": "🔴"}.get(c.tier, "")
            ceiling_rows.append({
                "Interface":  name,
                "Tier":       f"{icon} {c.tier}",
                "Can You?":   "✓ Yes" if c.tier != "SPECIALIST" else "⚠ Review",
                "Triggers":   "; ".join(t.reason for t in c.triggered_by[:2]),
            })
        st.dataframe(pd.DataFrame(ceiling_rows),
                     hide_index=True, use_container_width=True)

        # ── Green-path batch automation ──────────────────────────────────
        st.divider()
        st.markdown("**⚡ Batch-process the green path**")
        st.caption("Run AUTO interfaces end-to-end (scaffold → parameters → "
                   "optional upload) in one pass. GUIDED/SPECIALIST interfaces "
                   "are skipped and listed so you know exactly what's left to "
                   "do by hand.")

        # ── Per-iFlow decision: generate? which shape? ───────────────────
        # Timer is the default shape for every interface — it's the proven,
        # self-contained Timer→CM→CM→End flow with no sender/receiver and no
        # dependency on a standard package or endpoint. Uncheck an interface to
        # leave it out (e.g. a genuine push/inbound interface that needs the
        # sender path instead); those are reported under "Left for you".
        from scaffolder.iflow_scaffolder import IFlowScaffolder as _IFS
        _ceil = st.session_state.get("ceilings", {}) or {}
        _batch_rows = []
        for a in selected_assessments:
            _if = a.interface
            _c = _ceil.get(_if.name)
            _batch_rows.append({
                "Generate":  True,
                "Interface": _if.name,
                "Tier":      getattr(_c, "tier", "") if _c else getattr(a, "tier", ""),
                "Shape":     "timer",
                "Note":      ("⚠ push/sync — may need sender path"
                              if _IFS.likely_needs_sender(_if) else "outbound-safe"),
            })
        edited_batch = st.data_editor(
            pd.DataFrame(_batch_rows),
            hide_index=True, use_container_width=True, key="batch_shape_editor",
            column_config={
                "Generate":  st.column_config.CheckboxColumn("Generate", default=True),
                "Interface": st.column_config.TextColumn("Interface", disabled=True),
                "Tier":      st.column_config.TextColumn("Tier", disabled=True),
                "Shape":     st.column_config.SelectboxColumn(
                                 "Shape", options=["timer", "minimal"],
                                 default="timer",
                                 help="timer = self-contained scheduled flow "
                                      "(default); minimal = Start→End with a "
                                      "sender (legacy)"),
                "Note":      st.column_config.TextColumn("Note", disabled=True),
            },
        )

        bc1, bc2, bc3 = st.columns(3)
        with bc1:
            _b_include_guided = st.checkbox("Include GUIDED too", value=False,
                                            key="batch_inc_guided")
        with bc2:
            _b_upload = st.checkbox("Upload to tenant", value=False,
                                    key="batch_upload",
                                    disabled=not st.session_state.cpi_connected)
        with bc3:
            _b_go = st.button("⚡ Process green path", type="primary",
                              key="batch_go")

        if _b_upload and not st.session_state.cpi_connected:
            st.info("Connect to the tenant in the sidebar to enable upload.")

        if _b_go:
            from scaffolder.batch_orchestrator import BatchOrchestrator
            from scaffolder.iflow_scaffolder import IFlowScaffolder
            from scaffolder.parameter_injector import build_parameters_prop

            scaffolder = IFlowScaffolder(output_dir=str(Path(output_dir) / "iflows"), resources_dir=PINNED_LOCAL_DIRS["template_library_dir"], extra_resources=st.session_state.get("uploaded_resources"), passthrough=st.session_state.get("uploaded_passthrough"), gold_error_handling=st.session_state.get("gold_eh_variant"), gold_eh_replace=bool(st.session_state.get("gold_eh_replace")), gold_eh_notify=bool(st.session_state.get("gold_eh_notify")), gold_eh_sftp=bool(st.session_state.get("gold_eh_sftp")), gold_eh_company=st.session_state.get("company_code", ""))
            uploader = None
            if _b_upload and st.session_state.cpi_connected:
                uploader = CPIUploader(st.session_state.cpi_base_url,
                                       st.session_state.cpi_session)

            def _pb(done, total, nm):
                pass  # progress handled by spinner below

            orch = BatchOrchestrator(
                scaffolder=scaffolder,
                output_dir=str(output_dir),
                uploader=uploader,
                param_builder=lambda a, c: build_parameters_prop(
                    a.interface.name, config=c),
            )
            # Per-iFlow choices from the editor above
            _shapes = {r["Interface"]: r["Shape"] for _, r in edited_batch.iterrows()}
            _excluded = {r["Interface"] for _, r in edited_batch.iterrows()
                         if not r["Generate"]}

            with st.spinner("Processing green-path interfaces…"):
                run = orch.run(
                    selected_assessments,
                    st.session_state.ceilings,
                    configs=configs,
                    include_guided=_b_include_guided,
                    upload=_b_upload,
                    shapes=_shapes,
                    excluded_names=_excluded,
                )
            s = run.summary()
            st.success(
                f"✅ Processed {s['processed']} · "
                f"⚠ {s['needs_attention']} need attention · "
                f"❌ {s['failed']} failed"
                + (f" · ⬆ {s['uploaded']} uploaded" if _b_upload else ""))

            if run.processed:
                st.markdown("**Processed (green path):**")
                st.dataframe(pd.DataFrame([
                    {"Interface": r.interface_name, "Tier": r.tier,
                     "iFlow": Path(r.iflow_path).name if r.iflow_path else "—",
                     "Uploaded": "✓" if r.uploaded else ("—" if not _b_upload else "✗"),
                     "Note": r.upload_status[:50] if r.upload_status else ""}
                    for r in run.processed
                ]), hide_index=True, use_container_width=True)

            if run.needs_attention:
                st.markdown("**⚠ Left for you (GUIDED / SPECIALIST):**")
                st.dataframe(pd.DataFrame([
                    {"Interface": r.interface_name, "Tier": r.tier,
                     "Reason": r.reason}
                    for r in run.needs_attention
                ]), hide_index=True, use_container_width=True)

            if run.failed:
                st.markdown("**❌ Failed:**")
                st.dataframe(pd.DataFrame([
                    {"Interface": r.interface_name, "Reason": r.reason}
                    for r in run.failed
                ]), hide_index=True, use_container_width=True)

    # ── Effort model controls (multiplier + mode) — build 8 ─────────────
    # Exposes the Option-X effort model: mode seeds a default multiplier, the
    # slider lets the consultant tune it (1.0–3.0, 0.25 grid), and hypercare
    # is an optional add. Values are stored in session_state for the estimator
    # / proposal to consume.
    from reporter.effort_model import (
        default_multiplier_for_mode, snap_multiplier)
    st.markdown("**⚙ Effort model**")
    _modes = ["Migration", "Support", "Implementation"]
    em1, em2, em3 = st.columns([1.2, 1.4, 1])
    with em1:
        eff_mode = st.selectbox(
            "Mode", _modes,
            index=_modes.index(st.session_state.get("effort_mode", "Support")),
            key="effort_mode",
            help="Seeds the default multiplier; you can still tune it.")
    _seed = default_multiplier_for_mode(eff_mode)
    with em2:
        eff_mult = st.slider(
            "Effort multiplier", min_value=1.0, max_value=3.0,
            value=float(st.session_state.get("effort_multiplier", _seed)),
            step=0.25, key="effort_multiplier",
            help="Applied to itemized gap hours. 1.0 = no overhead.")
        st.caption(f"Snapped: ×{snap_multiplier(eff_mult)}  "
                   f"(mode default ×{_seed})")
    with em3:
        st.session_state["effort_hypercare"] = st.checkbox(
            "Hypercare", value=st.session_state.get("effort_hypercare", False),
            help="Add a post-go-live hypercare allowance.")

    if st.button("▶ Generate intervention estimate", key="iv_run"):
        estimator = InterventionEstimator(output_dir=output_dir)
        with st.spinner("Estimating…"):
            project = estimator.estimate_all(
                selected_assessments,
                configs=configs,
                verification_reports=verification_reports,
                clean_core_reports=clean_core_reports,
                ceilings=st.session_state.ceilings,
                multiplier=snap_multiplier(
                    st.session_state.get("effort_multiplier", _seed)),
                mode=st.session_state.get("effort_mode", ""),
                hypercare_enabled=st.session_state.get("effort_hypercare", False),
            )
            st.session_state.interventions = {
                iv.interface_name: iv for iv in project.interfaces
            }

            # Show project summary
            im1, im2, im3, im4 = st.columns(4)
            im1.metric("Avg automation",    f"{project.avg_automation_pct:.0f}%")
            im2.metric("Your hours",        f"{project.your_hours:.1f}h")
            im3.metric("Client hours",      f"{project.client_hours:.1f}h")
            im4.metric("Ready to start",    project.ready_to_start)

            # Generate Excel
            xl = estimator.generate_excel(project)
            buf = io.BytesIO()
            with open(xl, "rb") as f:
                buf.write(f.read())
            buf.seek(0)
            st.download_button(
                "⬇ Download Intervention Estimate Excel",
                data=buf.getvalue(),
                file_name="intervention_estimate.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
            )

            # Per-interface detail
            st.subheader("Per-interface breakdown")
            import pandas as pd
            iv_rows = []
            for iv in project.interfaces:
                iv_rows.append({
                    "Interface":   iv.interface_name,
                    "Automation":  f"{iv.automation_pct:.0f}%",
                    "Your hrs":    f"{iv.your_hours:.1f}",
                    "Client hrs":  f"{iv.client_hours:.1f}",
                    "Missing":     len(iv.missing_info),
                    "Blocking":    sum(1 for m in iv.missing_info if m.blocking),
                    "Ready":       "✓" if iv.ready_to_start else "⚠",
                    "Tier":        iv.tier,
                })
            st.dataframe(pd.DataFrame(iv_rows),
                         hide_index=True, use_container_width=True)

    # ── Migration Insights (effort reconciliation, mapping inventory, adapters) ──
    st.divider()
    st.subheader("📊 Migration Insights")
    ins_tabs = st.tabs(["Adapter advisories", "Mapping inventory", "Effort reconciliation"])

    ifaces = [a.interface for a in selected_assessments]

    with ins_tabs[0]:
        st.caption("Per-adapter migration guidance (PI/PO → CPI).")
        try:
            import pandas as pd
            from analyzer.adapter_advisor import advise_all
            adv = advise_all(ifaces)
            rows = [{"Adapter": a.pi_adapter, "Direction": a.direction,
                     "CPI equivalent": a.cpi_adapter, "Severity": a.severity,
                     "Notes": " | ".join(a.notes[:2])} for a in adv["advisories"]]
            if rows:
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
                if adv["warnings"]:
                    st.warning(f"{len(adv['warnings'])} adapter(s) need client setup "
                               f"(e.g. Cloud Connector, TPM).")
            else:
                st.info("No adapters detected.")
        except Exception as e:
            st.caption(f"(advisories unavailable: {e})")

    with ins_tabs[1]:
        st.caption("Mappings catalogued by type + reuse.")
        try:
            from analyzer.mapping_inventory import build_inventory
            inv = build_inventory(ifaces)
            s = inv.summary()
            mc1, mc2, mc3 = st.columns(3)
            mc1.metric("Total mappings", s["total_mappings"])
            mc2.metric("Reused", s["reused_mappings"])
            mc3.metric("Reuse savings", s["reuse_opportunity"])
            if inv.mappings:
                st.dataframe(
                    pd.DataFrame([{"Mapping": m.name, "Type": m.mapping_type,
                                   "Used by": m.reuse_count} for m in inv.mappings]),
                    hide_index=True, use_container_width=True)
        except Exception as e:
            st.caption(f"(inventory unavailable: {e})")

    with ins_tabs[2]:
        st.caption("Tool estimate vs SAP Migration Assessment effort.")
        try:
            from reporter.effort_reconciliation import reconcile
            ma_report = st.session_state.get("sap_ma_report")
            rec = reconcile(selected_assessments, ma_report=ma_report)
            rs = rec.summary()
            ec1, ec2, ec3 = st.columns(3)
            ec1.metric("Tool total (days)", rs["tool_total_days"])
            ec2.metric("SAP total (days)", rs["sap_total_days"])
            ec3.metric("Divergent", rs["tool_higher"] + rs["sap_higher"])
            if rec.divergent:
                st.dataframe(
                    pd.DataFrame([{"Interface": c.interface_name,
                                   "Tool (d)": c.tool_days, "SAP (d)": c.sap_days,
                                   "Flag": c.flag, "Note": c.note}
                                  for c in rec.divergent]),
                    hide_index=True, use_container_width=True)
            else:
                st.info("No SAP MA effort data loaded, or all aligned.")
        except Exception as e:
            st.caption(f"(reconciliation unavailable: {e})")

def render_proposal_generator(selected_assessments, output_dir,
                               project_name="CPI Migration"):
    """Commercial proposal generator."""
    st.divider()
    st.subheader("💼 Commercial Proposal Generator")
    st.caption("Generates client quote + your internal margin sheet.")

    pc1, pc2, pc3 = st.columns(3)
    with pc1:
        day_rate   = st.number_input("Your day rate (USD)",
                                      value=850, min_value=100,
                                      key="prop_day_rate")
        margin_pct = st.number_input("Target margin %",
                                      value=40, min_value=0, max_value=90,
                                      key="prop_margin")
    with pc2:
        price_low    = st.number_input("LOW price/interface ($)",
                                        value=1250, key="prop_low")
        price_medium = st.number_input("MEDIUM price/interface ($)",
                                        value=2500, key="prop_medium")
    with pc3:
        price_high   = st.number_input("HIGH price/interface ($)",
                                        value=5500, key="prop_high")
        currency     = st.selectbox("Currency", ["USD","EUR","MXN","GBP"],
                                     key="prop_currency")

    client_name = st.text_input("Client name (for proposal header)",
                                 placeholder="Acme Corporation",
                                 key="prop_client")

    if st.button("📄 Generate Proposal", type="primary", key="prop_gen"):
        with st.spinner("Generating proposal…"):
            try:
                pricing = PricingConfig(
                    currency=currency,
                    your_day_rate_usd=day_rate,
                    target_margin_pct=int(margin_pct),
                    price_auto_min=price_low,
                    price_guided_min=price_medium,
                    price_specialist_min=price_high,
                )
                gen = ProposalGenerator(output_dir=output_dir)
                ceilings = list(st.session_state.get("ceilings", {}).values())
                # 3 standard docs (client xlsx, internal xlsx, proposal docx)
                client_xlsx, internal_xlsx, docx = gen.generate(
                    selected_assessments,
                    ceilings=ceilings,
                    configs=configs,
                    pricing=pricing,
                    project_name=project_name,
                    company_code=company_code or "CLIENT",
                )
                # + 2 task documents (client tasks / consultant tasks)
                client_tasks_doc, consultant_tasks_doc = gen.generate_task_documents(
                    selected_assessments,
                    ceilings=ceilings,
                    interventions=st.session_state.get("interventions"),
                    project_name=project_name,
                    company_code=company_code or "CLIENT",
                )

                st.success("✅ Proposal generated (5 documents)")
                # Offer each file for download
                import os as _os
                for label, path in [
                    ("Client proposal (Excel)", client_xlsx),
                    ("Internal cost (Excel)", internal_xlsx),
                    ("Proposal (Word)", docx),
                    ("What the CLIENT must do (Word)", client_tasks_doc),
                    ("What I will do (Word)", consultant_tasks_doc),
                ]:
                    try:
                        with open(path, "rb") as f:
                            st.download_button(
                                f"⬇ {label}", data=f.read(),
                                file_name=_os.path.basename(str(path)),
                                key=f"dl_{_os.path.basename(str(path))}")
                    except Exception as fe:
                        st.caption(f"({label} unavailable: {fe})")

            except Exception as e:
                st.error(f"Proposal generation failed: {e}")



# ═══════════════════════════════════════════════════════════════════════════════
# WIRING — Tab 1: ESR source option
# This block adds ESR file upload to the existing Tab 1 source options.
# Note: Displayed via a dedicated expander in Tab 1 after SAP GitHub section.
# The full ESR live connection requires PI/PO credentials from Tab 0 Profile.
# ═══════════════════════════════════════════════════════════════════════════════

# ── End render functions ─────────────────────────────────────────────────────

_active_program = st.session_state.get("active_program", "migration")

# Global default: most client interfaces DO have endpoints, so default ON.
# When the user turns this OFF in Tab 1, every iFlow is the self-contained
# timer scaffold and the endpoint-selection tabs (3 · Match, 4 · Configure)
# are not needed — so we drop them from the tab bar and route their bodies to
# a discarded sink (same proven mechanism APIM mode uses, cleared at the end).
st.session_state.setdefault("iflows_have_endpoints", True)
_ep_sink = None

if _active_program == "migration":
    # All tabs are ALWAYS shown. Conditionally hiding tabs 3/4 on the endpoint
    # toggle caused the "Match iFlow flickers / hides" bug AND hid the corpus
    # folder selector (needed for pattern extraction even in timer mode). The
    # endpoint toggle now only sets the default deploy shape, never the tab set.
    _labels = ["📥 1 · Source", "🔍 2 · Interfaces", "⚙ 3 · Configure",
               "🚀 4 · Generate & Deploy", "🤖 5 · AI Solver",
               "📋 6 · Client Tracker", "🧪 7 · Payload Lab"]
    _t = st.tabs(_labels)
    (tab1, tab2, tab4, tab5, tab8, tab9, tab10) = _t
    _apim_active = False
    _migration_sink = None
else:
    # API Management mode. The existing migration `with tabN:` blocks below
    # still execute (we don't re-indent 3000 lines), but we route their
    # output into a placeholder that we clear immediately, so nothing from
    # the migration UI is visible. The APIM tabs render normally above it.
    apim_tab_landscape, apim_tab_proxies, apim_tab_products, apim_tab_apps, apim_tab_policies, apim_tab_deploy = st.tabs([
        "🗺 Landscape",
        "🔌 API Proxies",
        "📦 Products",
        "👥 Applications",
        "🛡 Policies",
        "🚀 Deploy",
    ])
    _apim_active = True
    _migration_sink = st.empty()
    _sink_container = _migration_sink.container()
    tab1 = tab2 = tab4 = tab5 = tab8 = tab9 = tab10 = _sink_container


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — SOURCE
# ═══════════════════════════════════════════════════════════════════════════════

with tab1:
    st.header("Source Configuration")
    st.caption("Choose how to load PI/PO interfaces. The form adapts to your selection.")

    # ── Endpoint mode (project-wide default) ─────────────────────────────
    # ON (default): interfaces have real sender/receiver endpoints — the
    # common case for client work; tabs 3 · Match and 4 · Configure are shown
    # and the per-interface default in Tab 2 is "endpoints". OFF: every iFlow
    # is the self-contained timer scaffold (no endpoints) and tabs 3 & 4 are
    # hidden. Toggling reruns the app, which rebuilds the tab bar.
    # NOTE: the checkbox must NOT share its key with the layout-controlling
    # state `iflows_have_endpoints`, which the tab bar reads at the top of the
    # run. Sharing the key (plus the setdefault above) creates a race where the
    # widget re-asserts its value after the tab bar was already built, so tab 3
    # mounts then vanishes on the next rerun ("Match iFlow flickers"). Instead
    # the widget owns its own key and an on_change callback copies the value
    # into the controlling key — callbacks run BEFORE the script reruns, so the
    # tab bar always sees a settled value.
    # Single build path: every iFlow goes through the same generator
    # (clean-room regeneration when a source iFlow is present, else the
    # self-contained timer scaffold). The old endpoints on/off toggle used to
    # route to a separate clone-and-adapt method; that path has been removed, so
    # there is no longer anything to choose — all tabs are always available.
    st.session_state["iflows_have_endpoints"] = True

    # Quick-access How To button (visible at top regardless of source choice).
    # Steps are kept in session state so they survive reruns; toggle on click.
    htb1, htb2, _ = st.columns([2, 2, 6])
    with htb1:
        if st.button("📖 How to run SAP Migration Assessment",
                     key="sap_ma_howto_btn",
                     help="Opens a step-by-step guide above the source form."):
            st.session_state["show_sap_ma_howto"] = not st.session_state.get(
                "show_sap_ma_howto", False)
    with htb2:
        if st.session_state.get("show_sap_ma_howto") and st.button(
                "✕ Close guide", key="sap_ma_howto_close_btn"):
            st.session_state["show_sap_ma_howto"] = False

    if st.session_state.get("show_sap_ma_howto"):
        with st.container(border=True):
            st.markdown("""
### 🛠 Running SAP's Migration Assessment Tool

SAP includes this tool free with any Integration Suite subscription (including the free tier).
It scans a PI/PO system and produces the Excel file the parser below consumes — no
custom credentials or extraction work on your side.

**Prerequisites**
1. **BTP subaccount with Integration Suite subscription** — free tier is fine. Activate via BTP Cockpit → Subscriptions → Integration Suite.
2. **Cloud Connector pointing at the client's PI/PO system** — installed on a host that can reach both BTP and PI/PO. Configure a virtual mapping for the PI/PO host. Note the Location ID.
3. **PI/PO credentials with read access** — service user with ESR + Integration Directory + SLD read roles (typically `SAP_XI_DISPLAY_USER_J2EE` is sufficient).

**Run the assessment**
1. Open Integration Suite → **Migration Assessment** capability.
2. **Data Extraction** → New Request. Enter PI/PO hostname, port, credentials, Cloud Connector Location ID.
3. Click **Test Connection** — wait for "Successfully connected" before proceeding.
4. **Submit**. Wait for "completed" status in the extraction log (10-60 minutes depending on landscape size).
5. **Scenario Evaluation** → New Evaluation. The Data Extraction Request name auto-fills.
6. Click **Create**. Wait for "Evaluation Completed".
7. The Excel report downloads automatically — two-sheet structure: *Executive Summary* (KPI dashboard) and *Scenario Evaluation* (per-ICO inventory). Plus a *Rules Log* sheet listing detected blockers and remediation strategies.

**Then in this workbench**
Select **"SAP Migration Assessment (Excel)"** as the source type below, upload the file, and every interface lands with full metadata (adapters, mapping types, migration status, effort estimates) populated automatically — no extra columns to fill in.
""")
        st.divider()

    source_type = st.radio(
        "Source type",
        ["Live PI/PO REST API", "Upload package / ZIP",
         "📡 Pull from CPI tenant", "Upload Excel inventory",
         "🛠 SAP Migration Assessment (Excel)"],
        horizontal=True,
        key="source_type",
    )

    st.divider()

    # ── Migration Strategy ────────────────────────────────────────
    with st.expander("🎯 Migration Strategy & Pipeline Mode", expanded=True):
        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            strategy_choice = st.radio(
                "Migration approach",
                options=list(STRATEGIES.keys()),
                format_func=lambda x: STRATEGIES[x]["label"],
                index=list(STRATEGIES.keys()).index(
                    st.session_state.get("migration_strategy", "bluefield")),
                key="strat_radio",
            )
            st.session_state["migration_strategy"] = strategy_choice
            st.caption(STRATEGIES[strategy_choice]["description"])
            st.caption(f"**When:** {STRATEGIES[strategy_choice]['when']}")

        with sc2:
            pipeline_choice = st.radio(
                "iFlow generation mode",
                options=["auto", "pipeline", "simple"],
                format_func=lambda x: {
                    "auto":     "🤖 Auto (pipeline if 10+ interfaces)",
                    "pipeline": "🔗 Pipeline Concept (always)",
                    "simple":   "📄 Simple (one iFlow per interface)",
                }[x],
                index=["auto","pipeline","simple"].index(
                    st.session_state.get("pipeline_mode", "auto")),
                key="pipeline_radio",
            )
            st.session_state["pipeline_mode"] = pipeline_choice

        with sc3:
            company_code = st.text_input(
                "Company code (for package naming)",
                value=st.session_state.get("company_code", "COMP"),
                placeholder="ACME",
                max_chars=10,
                key="company_code_input",
            )
            st.session_state["company_code"] = company_code.upper()
            _sep_label = st.radio(
                "Word separator for generated names",
                ["Underscore (_)", "Hyphen (-)", "Space ( )"],
                key="word_sep_choice", horizontal=True,
                help="Some clients have a naming convention — applies to "
                     "generated iFlow/package names and injected error-"
                     "handling step names. CPI ids are sanitized "
                     "separately and never carry spaces.")
            _sep = {"Underscore (_)": "_", "Hyphen (-)": "-",
                    "Space ( )": " "}[_sep_label]
            st.session_state["word_separator"] = _sep
            from scaffolder.pipeline_scaffolder import set_word_separator \
                as _set_ws_pipe
            from scaffolder.error_handling import set_word_separator \
                as _set_ws_eh
            _set_ws_pipe(_sep)
            _set_ws_eh(_sep)
            st.caption("Used in package names: "
                       + _sep.join(["ACME", "ECC", "S4HANA", "Finance"]))

    if source_type == "Live PI/PO REST API":
        c1, c2 = st.columns(2)
        with c1:
            pi_url  = st.text_input("PI/PO Host URL",
                                    value=cfg.get("pi", {}).get("base_url", "http://pihost:50000"),
                                    placeholder="http://pihost:50000")
            pi_user = st.text_input("Username",
                                    value=cfg.get("pi", {}).get("username", ""))
        with c2:
            pi_pass = st.text_input("Password", type="password",
                                    value=cfg.get("pi", {}).get("password", ""))
            pi_ns   = st.text_input("Filter namespace (optional)",
                                    placeholder="http://company.com/")

        if st.button("🔗 Load from PI/PO", type="primary"):
            with st.spinner("Connecting to PI/PO and fetching interfaces…"):
                try:
                    pi_auth = PIAuthenticator(pi_user, pi_pass)
                    pi_sess = pi_auth.get_session()
                    extractor = PIRestExtractor(pi_url, pi_sess)
                    records = extractor.extract_all()
                    if pi_ns:
                        records = [r for r in records if pi_ns in r.namespace]
                    from analyzer.ma_assessments import assess_records
                    st.session_state.interfaces  = records
                    st.session_state.assessments = assess_records(records)
                    st.session_state.pi_connected = True
                    st.success(f"✅ Loaded {len(records)} interfaces from PI/PO")
                except Exception as e:
                    st.error(f"Connection failed: {e}")

        if st.button("🔐 Download channels & credentials", key="pi_dl_ch",
                     help="Pulls every communication channel (adapter config "
                          "+ attributes) via the Directory API, harvests the "
                          "credential references, and lets you replicate the "
                          "missing ones into CPI Security Material. PI never "
                          "exposes passwords or private keys — created "
                          "credentials carry a placeholder to re-key, and "
                          "certificates are listed for manual transport."):
            with st.spinner("Fetching communication channels…"):
                try:
                    from fetcher.pipo_directory import (PIChannelExtractor,
                                                        harvest_credentials)
                    pi_sess = PIAuthenticator(pi_user, pi_pass).get_session()
                    chans = PIChannelExtractor(pi_url,
                                               pi_sess).extract_all()
                    creds, certs = harvest_credentials(chans)
                    from fetcher.pipo_directory import \
                        harvest_security_needs
                    st.session_state["pi_sec_needs"] = \
                        harvest_security_needs(chans)
                    st.session_state["pi_channels"] = chans
                    st.session_state["pi_credentials"] = creds
                    st.session_state["pi_cert_refs"] = certs
                    st.success(f"✅ {len(chans)} channels · "
                               f"{len(creds)} credential reference(s) · "
                               f"{len(certs)} certificate reference(s)")
                except Exception as e:
                    st.error(f"Channel download failed: {e}")

        if st.button("🎭 Load sample channels (demo)", key="pi_dl_demo",
                     help="No PI/PO system at hand? Loads realistic demo "
                          "channels so the whole pipeline — harvest, tenant "
                          "check, replication, worksheets — is testable. "
                          "Replication runs against your REAL CPI tenant "
                          "(named placeholder credentials, easy to delete "
                          "in Security Material afterwards)."):
            from fetcher.pipo_directory import (harvest_credentials,
                                                sample_channels)
            chans = sample_channels()
            creds, certs = harvest_credentials(chans)
            from fetcher.pipo_directory import harvest_security_needs
            st.session_state["pi_sec_needs"] = harvest_security_needs(chans)
            st.session_state["pi_channels"] = chans
            st.session_state["pi_credentials"] = creds
            st.session_state["pi_cert_refs"] = certs
            st.info(f"🎭 Demo data: {len(chans)} channels · {len(creds)} "
                    f"credential reference(s) · {len(certs)} certificate "
                    "reference(s)")

        if st.session_state.get("pi_credentials") is not None:
            from fetcher.pipo_directory import (channels_to_csv,
                                                credentials_to_csv,
                                                replicate_credentials)
            _creds = st.session_state["pi_credentials"]
            _certs = st.session_state.get("pi_cert_refs") or []
            st.markdown(f"**Credential references found: {len(_creds)}**")
            if _creds:
                st.dataframe(
                    [{"CPI alias": c.alias, "User": c.user, "Auth": c.auth,
                      "Adapter": c.adapter, "Channels": len(c.channels),
                      "On tenant": {True: "✅ exists", False: "❌ missing"}
                      .get(c.exists_in_cpi, "—")} for c in _creds],
                    use_container_width=True)
            if _certs:
                st.caption(f"🔏 {len(_certs)} certificate/keystore "
                           "reference(s) — private keys can NOT be exported "
                           "from PI; transport them into the CPI keystore "
                           "manually (listed in the worksheet).")
            _needs = st.session_state.get("pi_sec_needs") or []
            if _needs:
                st.markdown(f"**Other security material: {len(_needs)} "
                            "item(s)**")
                st.dataframe(
                    [{"Kind": n.kind,
                      "Automated": "✅ API" if n.automated else "✋ manual",
                      "Adapter": n.adapter, "Detail": n.detail,
                      "Channel": n.channel} for n in _needs],
                    use_container_width=True)
                _oauth = [n for n in _needs if n.kind == "OAUTH2_CLIENT"]
                if _oauth and st.button(
                        f"🚀 Replicate {len(_oauth)} OAuth2 client(s) "
                        "to CPI", key="pi_oauth_rep"):
                    if not st.session_state.get("cpi_session"):
                        st.warning("Connect to the tenant first "
                                   "(Profiles tab).")
                    else:
                        from fetcher.pipo_directory import replicate_oauth2
                        from fetcher.security_material import \
                            SecurityMaterialClient
                        _sm = SecurityMaterialClient(
                            st.session_state.cpi_base_url,
                            st.session_state.cpi_session)
                        _os = replicate_oauth2(
                            _oauth, _sm,
                            st.session_state.get("pi_cred_ph")
                            or "ChangeMe-2026!",
                            sep=st.session_state.get("word_separator", "_"))
                        if _os["created"]:
                            st.success("Created: "
                                       + ", ".join(_os["created"])
                                       + " — ⚠ placeholder secrets, re-key")
                        if _os["existing"]:
                            st.info("Already exist: "
                                    + ", ".join(_os["existing"]))
                        if _os["failed"]:
                            st.error("Failed: " + ", ".join(_os["failed"]))
                            for _al, _msg in (_os.get("errors")
                                              or {}).items():
                                st.code(f"{_al}: {_msg[:300]}")
                if any(n.kind.startswith("PGP") for n in _needs):
                    st.caption(
                        "🔐 PGP keyrings can NOT be replicated by API: CPI "
                        "only accepts keyring upload through the UI, and "
                        "PI never exposes the secret keys. Each PGP row in "
                        "the worksheet names the key files involved and "
                        "the exact manual step.")
            _ph = st.text_input(
                "Placeholder password for created credentials",
                value="ChangeMe-2026!", type="password", key="pi_cred_ph",
                help="PI never returns passwords — every created credential "
                     "uses this placeholder and is flagged for re-keying.")
            cba, cbb, cbc = st.columns(3)
            with cba:
                if st.button("🔬 Test write access", key="pi_cred_probe",
                             help="Creates + deletes a probe credential "
                                  "and shows the exact HTTP outcome — "
                                  "diagnoses 403 (missing write role), "
                                  "CSRF, or payload issues in one click."):
                    if not st.session_state.get("cpi_session"):
                        st.warning("Connect to the tenant first "
                                   "(Profiles tab).")
                    else:
                        from fetcher.security_material import \
                            SecurityMaterialClient
                        _smp = SecurityMaterialClient(
                            st.session_state.cpi_base_url,
                            st.session_state.cpi_session)
                        _pr = _smp.probe_write_access()
                        (st.success if _pr.startswith("WRITE OK")
                         else st.error)(_pr)
                if st.button("🔎 Check against tenant", key="pi_cred_chk"):
                    if not st.session_state.get("cpi_session"):
                        st.warning("Connect to the tenant first "
                                   "(Profiles tab).")
                    else:
                        from fetcher.security_material import \
                            SecurityMaterialClient
                        _sm = SecurityMaterialClient(
                            st.session_state.cpi_base_url,
                            st.session_state.cpi_session)
                        _sum = replicate_credentials(_creds, _sm, _ph,
                                                     dry_run=True)
                        st.info(f"{len(_sum['existing'])} exist · "
                                f"{len(_sum['missing'])} missing")
            with cbb:
                if st.button("🚀 Replicate missing to CPI",
                             key="pi_cred_rep", type="primary"):
                    if not st.session_state.get("cpi_session"):
                        st.warning("Connect to the tenant first "
                                   "(Profiles tab).")
                    else:
                        from fetcher.security_material import \
                            SecurityMaterialClient
                        _sm = SecurityMaterialClient(
                            st.session_state.cpi_base_url,
                            st.session_state.cpi_session)
                        _sum = replicate_credentials(_creds, _sm, _ph)
                        if _sum["created"]:
                            st.success(f"Created: "
                                       f"{', '.join(_sum['created'])} — "
                                       "⚠ placeholder passwords, re-key "
                                       "before go-live")
                        if _sum["failed"]:
                            st.error(f"Failed: {', '.join(_sum['failed'])}")
                            for _al, _msg in (_sum.get("errors")
                                              or {}).items():
                                st.code(f"{_al}: {_msg[:300]}")
                        if not _sum["created"] and not _sum["failed"]:
                            st.info("Nothing to create — all credentials "
                                    "already exist.")
            with cbc:
                st.download_button(
                    "⬇ Credentials worksheet (CSV)",
                    credentials_to_csv(
                        _creds, _certs,
                        st.session_state.get("pi_sec_needs")),
                    file_name="pi_credentials_worksheet.csv",
                    key="pi_cred_csv")
                st.download_button(
                    "⬇ Channels inventory (CSV)",
                    channels_to_csv(st.session_state.get("pi_channels")
                                    or []),
                    file_name="pi_channels.csv", key="pi_ch_csv")

    elif source_type == "📡 Pull from CPI tenant":
        st.info("Multi-tenant: pulls packages straight from a CPI tenant "
                "and runs them through the same pipeline as uploaded "
                "exports. The SOURCE connection comes from the sidebar "
                "('📡 Source tenant' expander) and falls back to the "
                "target connection — for a same-tenant showcase just "
                "connect normally and click 'Use target connection as "
                "source'.")
        _render_tenant_pull(expanded=True)

    elif source_type == "Upload package / ZIP":
        st.info("Upload one or more exported PI/PO integration packages, CPI "
                "packages, or a full GitHub repo dump. Supported formats: .zip, "
                ".tar, .tar.gz, .tgz, .tar.bz2. Max size 10 GB each. "
                "Interfaces are counted as iFlows (ICOs) inside the archive — "
                "including nested package zips — not as packages.")
        _render_tenant_pull(expanded=False)

        uploaded_files = st.file_uploader(
            "Choose archive file(s) (.zip / .tar / .tar.gz / .tgz / .tar.bz2)",
            type=["zip", "tar", "gz", "tgz", "bz2"],
            accept_multiple_files=True,
            key="pkg_upload",
            help="Upload several package zips at once — Tab 1 counts the total "
                 "interfaces across all of them, and Tab 5 lets you pick which "
                 "to push to the tenant (no re-upload needed).")
        cpa, cpb = st.columns([3, 2])
        with cpa:
            parse_clicked = st.button("📦 Parse package(s)", type="primary")
        with cpb:
            if st.session_state.uploaded_packages and st.button(
                    "🗑 Clear uploaded packages"):
                st.session_state.uploaded_packages = []
                st.session_state.interfaces = []
                st.session_state.assessments = []
                st.session_state.all_artifacts = []
                st.rerun()

        if uploaded_files and parse_clicked:
            with st.spinner(f"Parsing {len(uploaded_files)} archive(s)…"):
                total, npkg, parse_errors = _ingest_archive_items(
                    [(uf.name, uf.read()) for uf in uploaded_files])
                st.success(f"✅ {total} interface(s) (iFlows) across {npkg} "
                           f"uploaded package(s) — go to Tab 2 to assess, or "
                           f"Tab 5 to upload to the tenant.")
                if parse_errors:
                    with st.expander(f"⚠ {len(parse_errors)} file(s) "
                                     "failed to parse"):
                        for nm, err in parse_errors:
                            st.markdown(f"- **{nm}**: {err}")

        # Show what's currently loaded (per-package iFlow breakdown).
        if st.session_state.uploaded_packages:
            with st.expander(
                    f"📋 {len(st.session_state.uploaded_packages)} package(s) "
                    f"loaded · "
                    f"{sum(p['iflow_count'] for p in st.session_state.uploaded_packages)} "
                    f"total interface(s)", expanded=False):
                import pandas as _pd
                rows = [{"Package": p["filename"], "Interfaces (iFlows)": p["iflow_count"]}
                        for p in st.session_state.uploaded_packages]
                st.dataframe(_pd.DataFrame(rows), use_container_width=True,
                             hide_index=True)

    elif source_type == "🌐 SAP GitHub samples":
        st.info("Browse and download official SAP integration samples from github.com/SAP-samples — no authentication required.")

        github_token = st.text_input("GitHub token (optional — raises rate limit from 60 to 5000 req/hr)",
                                      type="password", key="gh_token",
                                      help="Create at github.com/settings/tokens — no scopes needed for public repos")

        browser = SAPSamplesBrowser(
            github_token=github_token,
            cache_ttl_hours=24,
        )

        # Filters
        fc1, fc2, fc3, fc4 = st.columns(4)
        with fc1:
            gh_query = st.text_input("Search", placeholder="purchase order, IDoc, RFC…", key="gh_query")
        with fc2:
            gh_adapter = st.selectbox("Adapter filter", ["(any)"] + ADAPTER_TYPES[:10], key="gh_adapter")
        with fc3:
            gh_target = st.selectbox("Target filter",
                                     ["(any)"] + list(DESTINATION_REGISTRY.keys()),
                                     format_func=lambda x: x if x == "(any)" else DESTINATION_REGISTRY.get(x, type("",(),{"label":x})()).label,
                                     key="gh_target")
        with fc4:
            gh_complexity = st.selectbox("Size hint", ["(any)", "S", "M", "L", "XL"], key="gh_complexity")

        col_a, col_b, col_c = st.columns([2, 2, 3])
        with col_a:
            scan_btn = st.button("🔍 Scan SAP GitHub", type="primary")
        with col_b:
            refresh_btn = st.button("🔄 Force refresh index",
                                    help="Ignore cached index and re-scan GitHub. "
                                         "Use after adding a token, or to pick up new packages.")
        with col_c:
            gh_max_results = st.slider("Max results",
                                       min_value=10, max_value=300, value=100, step=10,
                                       key="gh_max_results",
                                       help="Repo contains ~170 recipe folders. Higher values "
                                            "show more results but require more API calls.")

        if scan_btn or refresh_btn:
            with st.spinner("Scanning SAP-samples repos… (first run takes ~30s)"):
                try:
                    if refresh_btn:
                        # Bust cache so token / new code is actually used
                        browser.get_package_index(force_refresh=True)
                    packages = browser.search(
                        query=gh_query,
                        adapter=gh_adapter if gh_adapter != "(any)" else "",
                        target_id=gh_target if gh_target != "(any)" else "",
                        complexity={"S": "LOW", "M": "MEDIUM", "L": "HIGH",
                                    "XL": "HIGH"}.get(gh_complexity, ""),
                        top_n=gh_max_results,
                    )
                    st.session_state["gh_packages"] = packages
                    useful = sum(1 for p in packages if p.has_zip or p.has_iflow or p.has_mapping)
                    if not github_token and len(packages) <= 60:
                        st.warning(f"Found {len(packages)} packages. Add a GitHub token "
                                   f"above to lift the 60-req/hr limit and scan more. "
                                   f"{useful} of these contain downloadable artifacts.")
                    else:
                        st.success(f"Found {len(packages)} packages "
                                   f"({useful} with downloadable artifacts)")
                except Exception as e:
                    st.error(f"Scan failed: {e}")

        # Display results
        packages = st.session_state.get("gh_packages", [])
        if packages:
            import pandas as pd
            # Toggle that drives the default "Select" value on every row.
            # Flipping it triggers a Streamlit rerun, so the table re-renders
            # with all checkboxes set/cleared. The data_editor key changes
            # alongside it so Streamlit treats this as a fresh table.
            select_all_state = st.session_state.get("gh_select_all", False)
            sa1, sa2, sa3 = st.columns([1, 1, 6])
            with sa1:
                if st.button(f"✅ Select all ({len(packages)})", key="gh_select_all_btn"):
                    st.session_state["gh_select_all"] = True
                    st.session_state["gh_table_nonce"] = st.session_state.get("gh_table_nonce", 0) + 1
                    st.rerun()
            with sa2:
                if st.button("☐ Clear", key="gh_clear_btn"):
                    st.session_state["gh_select_all"] = False
                    st.session_state["gh_table_nonce"] = st.session_state.get("gh_table_nonce", 0) + 1
                    st.rerun()

            rows = []
            for pkg in packages:
                rows.append({
                    "Select":       select_all_state,
                    "Name":         pkg.name,
                    "Repo":         pkg.repo,
                    "Adapters":     ", ".join(pkg.detected_adapters) or "—",
                    "Targets":      ", ".join(pkg.detected_targets[:2]) or "—",
                    "Complexity":   pkg.complexity_hint,
                    "Has iFlow":    "✓" if pkg.has_iflow else "",
                    "Has Mapping":  "✓" if pkg.has_mapping else "",
                    "Has WSDL":     "✓" if pkg.has_wsdl else "",
                })
            edited_gh = st.data_editor(
                pd.DataFrame(rows),
                column_config={"Select": st.column_config.CheckboxColumn("Select", default=False)},
                hide_index=True,
                use_container_width=True,
                key=f"gh_table_{st.session_state.get('gh_table_nonce', 0)}",
            )
            selected_pkgs = [
                packages[i] for i, row in edited_gh.iterrows()
                if row["Select"]
            ]

            # ── Bulk artifact corpus builder ────────────────────────────
            # Downloads every package currently in the result list and
            # extracts .mmap / .xsl / .iflw / .groovy / .wsdl / .xsd files
            # for offline use (shadow testing, mapping fidelity work).
            # Different from "Download & import": that operation converts
            # packages to InterfaceRecords for the migration pipeline; this
            # one is a pure corpus build.
            bd1, bd2 = st.columns([3, 4])
            with bd1:
                bulk_btn = st.button(
                    f"🗂 Bulk-extract artifacts from all {len(packages)} package(s)",
                    key="gh_bulk_extract_btn",
                    help="Downloads every package's zip and extracts .mmap, .xsl, "
                         ".iflw, .groovy, .wsdl, .xsd files into your local cache. "
                         "Slow operation (~30s per package) — use for offline corpus "
                         "building, not for normal browsing.")
            with bd2:
                extract_only_with_artifacts = st.checkbox(
                    "Skip packages without downloadable artifacts",
                    value=True, key="gh_bulk_skip_empty",
                    help="When checked, packages flagged as README-only "
                         "(no .zip / .iflw / .mmap) are skipped to save time.")

            if bulk_btn:
                targets = [p for p in packages
                           if (not extract_only_with_artifacts) or
                              p.has_zip or p.has_iflow or p.has_mapping]
                if not targets:
                    st.warning("No packages with downloadable artifacts in the current results.")
                else:
                    st.info(f"Extracting from {len(targets)} package(s). "
                            f"This may take {len(targets) * 30 // 60} - "
                            f"{len(targets) * 60 // 60} minute(s).")
                    progress = st.progress(0.0)
                    status_line = st.empty()
                    totals = {"mappings": 0, "xslt": 0, "iflows": 0,
                              "groovy": 0, "wsdl": 0, "xsd": 0}
                    failed = []
                    for idx, pkg in enumerate(targets):
                        status_line.text(f"[{idx+1}/{len(targets)}] {pkg.name[:60]}")
                        try:
                            arts = browser.extract_artifacts(pkg)
                            for k in totals:
                                totals[k] += len(arts.get(k, []))
                        except Exception as exc:
                            failed.append((pkg.name, str(exc)))
                        progress.progress((idx + 1) / len(targets))
                    status_line.empty()
                    cache_root = browser.cache_dir / "downloads"
                    st.success(
                        f"✅ Bulk extraction complete. Found "
                        f"{totals['mappings']} mappings · "
                        f"{totals['xslt']} XSLT · "
                        f"{totals['iflows']} iFlows · "
                        f"{totals['groovy']} Groovy · "
                        f"{totals['wsdl']} WSDL · "
                        f"{totals['xsd']} XSD."
                    )
                    st.caption(f"Files cached at: `{cache_root}`")
                    if failed:
                        with st.expander(f"⚠ {len(failed)} package(s) failed"):
                            for name, err in failed[:20]:
                                st.markdown(f"- **{name}**: {err}")

            if selected_pkgs and st.button(f"⬇ Download & import {len(selected_pkgs)} package(s)", type="primary"):
                all_records = []
                with st.spinner("Downloading packages…"):
                    for pkg in selected_pkgs:
                        try:
                            local_path = browser.download_package(pkg)
                            if local_path:
                                # Try to load as CPI artifacts
                                from fetcher.cpi_fetcher import CPIFetcher
                                fetcher_local = CPIFetcher("http://localhost", None,
                                                           cache_dir=local_path.parent)
                                arts = fetcher_local._load_local_artifacts()
                                if arts:
                                    st.session_state.all_artifacts.extend(arts)

                                # Convert to InterfaceRecords for pipeline.
                                # Derive sender/receiver from the package
                                # rather than hardcoding "SAP_Source/SAP_Target",
                                # otherwise every imported record looks identical
                                # in Tab 2's flow table.
                                from extractor.pi_extractor import InterfaceRecord
                                import re as _re

                                # Use detected_targets as the most reliable hint,
                                # fall back to splitting the package name on
                                # "to"/"-"/"_" for patterns like "S4_to_Ariba"
                                targets = list(pkg.detected_targets)
                                tokens = [t for t in _re.split(r"[_\-\s]+|to|To|TO",
                                                                pkg.name) if len(t) > 2]
                                sender_sys = (targets[0] if targets
                                              else (tokens[0] if tokens else pkg.repo[:20]))
                                receiver_sys = (targets[1] if len(targets) > 1
                                                else (tokens[-1] if len(tokens) > 1
                                                      else "SAP_Target"))
                                # Avoid trivial duplicate sender==receiver
                                if sender_sys == receiver_sys and len(tokens) > 1:
                                    receiver_sys = tokens[-1]

                                record = InterfaceRecord(
                                    id=_re.sub(r"[^\w]", "_", pkg.id),
                                    name=pkg.name[:60],
                                    namespace="http://sap-samples.github.com",
                                    software_component=pkg.repo,
                                    sender_system=sender_sys[:30],
                                    receiver_system=receiver_sys[:30],
                                    sender_adapter=pkg.detected_adapters[0] if pkg.detected_adapters else "HTTPS",
                                    receiver_adapter=pkg.detected_adapters[1] if len(pkg.detected_adapters) > 1 else "HTTPS",
                                    message_interface=pkg.name,
                                    description=pkg.description,
                                )
                                all_records.append(record)
                                st.write(f"✓ {pkg.name}")
                        except Exception as e:
                            st.warning(f"⚠ {pkg.name}: {e}")

                if all_records:
                    from analyzer.ma_assessments import assess_records
                    existing = st.session_state.interfaces or []
                    all_ifaces = existing + all_records
                    st.session_state.interfaces = all_ifaces
                    st.session_state.assessments = assess_records(all_ifaces)
                    st.success(f"✅ Imported {len(all_records)} interfaces from SAP GitHub — go to Tab 2")

        # Cache status
        with st.expander("📦 Cache status"):
            try:
                status = browser.cache_status()
                st.json(status)
            except Exception:
                st.info("No cache yet — run a scan first")

    elif source_type == "Upload Excel inventory":
        st.info("Upload an Excel (.xlsx) file with columns: Name, SenderSystem, SenderAdapter, ReceiverSystem, ReceiverAdapter, Namespace, MappingProgram, Description")
        uploaded_xl = st.file_uploader("Choose .xlsx file", type=["xlsx"], key="xl_upload")

        with st.expander("📋 Download sample Excel template"):
            if st.button("Generate sample_interfaces.xlsx"):
                import openpyxl
                wb = openpyxl.Workbook()
                ws = wb.active
                ws.append(["Name","Namespace","SoftwareComponent","SenderSystem",
                           "SenderAdapter","ReceiverSystem","ReceiverAdapter",
                           "MessageInterface","MappingProgram","Description"])
                ws.append(["PO_Create","http://co.com/po","SC_MM","ECC","IDoc",
                           "S4HANA","SOAP","MI_PO","MM_PO","Purchase Order"])
                ws.append(["Emp_Sync","http://co.com/hr","SC_HR","S4HANA","RFC",
                           "SuccessFactors","HTTPS","MI_Emp","MM_Emp","Employee sync"])
                buf = io.BytesIO()
                wb.save(buf)
                buf.seek(0)
                st.download_button("⬇ Download template",
                                   data=buf.getvalue(),
                                   file_name="sample_interfaces.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        if uploaded_xl and st.button("📊 Load from Excel", type="primary"):
            with st.spinner("Reading Excel…"):
                try:
                    tmp = Path("/tmp/uploaded_interfaces.xlsx")
                    tmp.write_bytes(uploaded_xl.read())
                    extractor = PIFileExtractor(str(tmp))
                    records   = extractor.extract_all()
                    from analyzer.ma_assessments import assess_records
                    st.session_state.interfaces  = records
                    st.session_state.assessments = assess_records(records)
                    st.success(f"✅ Loaded {len(records)} interfaces from Excel")
                except Exception as e:
                    st.error(f"Read failed: {e}")

    elif source_type == "🛠 SAP Migration Assessment (Excel)":
        st.info("Upload the Excel produced by SAP's Migration Assessment tool. "
                "All adapter, mapping, and complexity data is populated automatically "
                "— click the 📖 How To button at the top of the tab for the run guide.")

        uploaded_ma = st.file_uploader("Choose Migration Assessment .xlsx",
                                        type=["xlsx"], key="sap_ma_upload")

        if uploaded_ma and st.button("📊 Parse Migration Assessment",
                                      type="primary", key="sap_ma_parse_btn"):
            with st.spinner("Parsing SAP Migration Assessment export…"):
                try:
                    from intake.sap_ma_parser import parse_sap_ma_excel
                    tmp = Path("/tmp/uploaded_sap_ma.xlsx")
                    tmp.write_bytes(uploaded_ma.read())
                    report = parse_sap_ma_excel(str(tmp))

                    from analyzer.ma_assessments import assess_records
                    st.session_state.interfaces  = report.interfaces
                    # MODE 1: completely loyal to the MA file's weights,
                    # bands and Est. Effort — no re-scoring
                    st.session_state.assessments = assess_records(report.interfaces)
                    st.session_state["sap_ma_summary"] = report.summary
                    st.session_state["sap_ma_rules"]   = report.rules
                    st.session_state["sap_ma_report"]  = report

                    s = report.summary
                    st.success(f"✅ Loaded {len(report.interfaces)} interfaces from SAP MA export")

                    # Show SAP's own assessment alongside ours
                    sa, sb, sc, sd, se = st.columns(5)
                    sa.metric("SAP: Total ICOs",      s.total_icos)
                    sb.metric("SAP: Ready",           s.ready_to_migrate)
                    sc.metric("SAP: Adjustment",      s.adjustment_required)
                    sd.metric("SAP: Evaluation",      s.evaluation_required)
                    se.metric("SAP: Effort (hrs)",    f"{s.total_effort_hours:.0f}")

                    if report.rules:
                        with st.expander(f"📋 {len(report.rules)} rule(s) triggered "
                                          f"by SAP MA (blockers & remediation)"):
                            import pandas as pd
                            st.dataframe(pd.DataFrame([
                                {"Rule": r.rule_id,
                                 "Affected ICO": r.affected_ico,
                                 "Asset": r.asset_string[:60],
                                 "Strategy": r.technical_note[:120]}
                                for r in report.rules
                            ]), hide_index=True, use_container_width=True)
                except Exception as e:
                    st.error(f"Parse failed: {e}")
                    st.caption("If the file is from a recent SAP MA release, the "
                               "sheet layout may have changed slightly — please "
                               "share the column headers via thumbs-down feedback.")

    # ESR file upload
    with st.expander("📂 Upload ESR files from PI/PO export (.xsd .wsdl .mmap)"):
        render_esr_uploader()

    # Summary after load
    if st.session_state.assessments:
        st.divider()
        assessments = st.session_state.assessments
        # MA-faithful S/M/L/XL counts + total scaling effort (engine), not the
        # legacy 3-band complexity.
        _sizes = {"S": 0, "M": 0, "L": 0, "XL": 0}
        _eff_lo = _eff_hi = 0.0
        for a in assessments:
            _sz, _wt, _d, _lo, _hi = _ma_assess(a.interface)
            if _sz in _sizes:
                _sizes[_sz] += 1
            _eff_lo += _lo
            _eff_hi += _hi
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Interfaces", len(assessments))
        c2.metric("S", _sizes["S"])
        c3.metric("M", _sizes["M"])
        c4.metric("L", _sizes["L"])
        c5.metric("XL", _sizes["XL"])
        st.caption(f"Estimated effort: **{_eff_lo:g}–{_eff_hi:g} days** "
                   "(MA-style, scales with weight). Import a real Migration "
                   "Assessment export for SAP-calibrated figures. → **Tab 2** to "
                   "browse and select interfaces.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — INTERFACE BROWSER
# ═══════════════════════════════════════════════════════════════════════════════

    # ═══ 📚 LIBRARY — the distilled, growing, deletable-source asset ═══
    # Additive content-hash store (library_builder/library_store.py):
    # re-extraction adds only unseen content; client-tenant harvests stay
    # in scoped workspaces (client IP) unless explicitly promoted.
    st.divider()
    with st.expander("📚 Library — extract, grow, compare", expanded=False):
        from library_builder.library_store import LibraryStore
        from fetcher.user_settings import (get_setting as _lb_gs,
                                            set_setting as _lb_ss)
        _lb_default = str(_RESOURCES_ROOT / "Library")
        _lb_dir = st.text_input("Library folder", value=_lb_gs(
            "library_dir", _lb_default), key="lib_dir")
        if _lb_dir != _lb_gs("library_dir", _lb_default):
            _lb_ss("library_dir", _lb_dir)
        _lib = LibraryStore(_lb_dir)
        _stat = _lib.stats()
        sc1, sc2, sc3 = st.columns(3)
        sc1.metric("Unique files", _stat["unique_files"])
        sc2.metric("Size", f"{_stat['bytes'] // 1024 // 1024} MB")
        sc3.metric("Client workspaces", len(_lib.scopes()))
        if _stat["by_ext"]:
            st.caption(" · ".join(f"{k} {v}" for k, v in
                                  list(_stat["by_ext"].items())[:8]))

        lc1, lc2 = st.columns(2)
        with lc1:
            if st.button("📥 Extract uploaded packages → library",
                         key="lib_from_uploads",
                         disabled=not st.session_state.get(
                             "uploaded_packages")):
                _tot = [0, 0]
                for _p in st.session_state.uploaded_packages:
                    _r = _lib.add_from_zip(_p["bytes"],
                                           source=_p["filename"])
                    _tot[0] += _r.added; _tot[1] += _r.duplicates
                st.success(f"+{_tot[0]} new, {_tot[1]} already known")
                st.rerun()
        with lc2:
            _lb_src = st.text_input(
                "…or local packages folder", key="lib_src_dir",
                placeholder=str(_RESOURCES_ROOT / "Packages"))
            if st.button("📥 Extract folder → library",
                         key="lib_from_dir", disabled=not _lb_src):
                import os as _os
                if _os.path.isdir(_lb_src):
                    _r = _lib.add_from_dir(_lb_src)
                    st.success(f"+{_r.added} new, {_r.duplicates} known, "
                               f"{_r.skipped} skipped")
                    st.rerun()
                else:
                    st.error("folder not found")

        # ── safe-to-delete check ─────────────────────────────────────
        _cv_src = st.text_input("Coverage check — folder of package zips",
                                key="lib_cov_dir",
                                placeholder="(does the library fully "
                                            "cover this folder?)")
        if st.button("🔍 Check coverage", key="lib_cov_btn",
                     disabled=not _cv_src):
            import os as _os
            if _os.path.isdir(_cv_src):
                _cv = _lib.coverage(_cv_src)
                if _cv["safe_to_delete"]:
                    st.success(f"✅ {_cv['covered']}/{_cv['total']} "
                               f"(100%) — the library fully covers this "
                               f"folder; deleting the raw zips loses "
                               f"nothing the resolver needs.")
                else:
                    st.warning(f"⚠ {_cv['covered']}/{_cv['total']} "
                               f"({_cv['pct']}%) covered — extract this "
                               f"folder first; do NOT delete yet.")
            else:
                st.error("folder not found")

        # ── tenant harvest → CLIENT WORKSPACE (never the main library) ──
        st.markdown("**🛰 Harvest tenant content** — lands in a scoped "
                    "client workspace; promote individual files to the "
                    "main library explicitly.")
        _scope = st.text_input("Client workspace label", key="lib_scope",
                               placeholder="acme-prod")
        if st.button("⬇ Pull all tenant packages → workspace",
                     key="lib_pull_btn", disabled=not _scope):
            _src_sess = (st.session_state.get("cpi_source_session")
                         or st.session_state.get("cpi_session"))
            _src_url = (st.session_state.get("cpi_source_base_url")
                        or st.session_state.get("cpi_base_url"))
            if not _src_sess:
                st.error("Connect a tenant first (sidebar).")
            else:
                _f = CPIFetcher(base_url=_src_url, session=_src_sess)
                try:
                    _pids = [p.get("Id") for p in
                             (_f.list_packages() or [])]
                except Exception as _e:
                    _pids = []
                    st.error(f"package list failed: {_e}")
                _new = _known = _failed = 0
                _prog = st.progress(0)
                for _i, _pid in enumerate(_pids):
                    try:
                        _zb = _f.download_package_zip(_pid)
                        _r = _lib.add_from_zip(
                            _zb, source=f"tenant:{_pid}", scope=_scope)
                        _new += _r.added; _known += _r.duplicates
                    except Exception:
                        _failed += 1
                    _prog.progress((_i + 1) / max(1, len(_pids)))
                st.success(f"Workspace '{_scope}': +{_new} files the "
                           f"library didn't have, {_known} already "
                           f"known, {_failed} package(s) failed")

        # ── promote from a workspace ─────────────────────────────────
        if _lib.scopes():
            _pr_scope = st.selectbox("Promote from workspace",
                                     _lib.scopes(), key="lib_pr_scope")
            _pr_idx = _lib.load_index(_pr_scope)
            _pr_opts = {f"{e['names'][0].rsplit('/', 1)[-1]}  "
                        f"({e['ext']}, {e['size']}B)": _sha
                        for _sha, e in _pr_idx.items()}
            if _pr_opts:
                _pr_pick = st.selectbox("File", list(_pr_opts),
                                        key="lib_pr_pick")
                if st.button("⬆ Promote to main library",
                             key="lib_pr_btn"):
                    if _lib.promote(_pr_opts[_pr_pick], _pr_scope):
                        st.success("Promoted.")
                        st.rerun()

        # ── conversion linter: 3-layer reference (SAP API + GDK 2.4/4.0
        #    extracted from Apache source + stdlib heuristics) ──────────
        if st.button("🔬 Lint library scripts (Groovy 2.4 vs 4.0.29 "
                     "runtime)", key="lib_lint_btn"):
            from tools.script_lint import lint_corpus
            _sm = lint_corpus(_lib.as_corpus())
            v = _sm["verdicts"]
            lm1, lm2, lm3, lm4 = st.columns(4)
            lm1.metric("Runs on both", v.get("both", 0))
            lm2.metric("4.x-only", v.get("needs_4_runtime", 0))
            lm3.metric("Breaks on 4.0.29", v.get("breaks_on_4", 0))
            lm4.metric("Review", v.get("review", 0))
            if _sm["breaks_on_4"]:
                st.error("These import groovy.util XML classes that were "
                         "REMOVED in Groovy 4 — they fail on the 4.0.29 "
                         "runtime:")
                for _k in _sm["breaks_on_4"]:
                    st.markdown(f"- `{_k.rsplit('/', 1)[-1]}`")
            if _sm["unknown_calls"]:
                st.caption("Unresolved calls (custom helpers or typos): "
                           + ", ".join(f"{k}×{c}" for k, c in sorted(
                               _sm["unknown_calls"].items(),
                               key=lambda kv: -kv[1])[:8]))

        # ── persisted, additive capability catalog ───────────────────
        if st.button("🧠 Rebuild capability catalog (additive merge)",
                     key="lib_cat_btn"):
            _corpus = _lib.as_corpus()
            _by_type = {"groovy": {}, "xslt": {}, "mmap": {}, "schema": {},
                        "js": {}, "props": {}, "iflw": {}}
            for _k, _t in _corpus.items():
                _e = "." + _k.rsplit(".", 1)[-1].lower()
                _kind = {".groovy": "groovy", ".gsh": "groovy",
                         ".xsl": "xslt", ".xslt": "xslt", ".mmap": "mmap",
                         ".xsd": "schema", ".wsdl": "schema",
                         ".edmx": "schema", ".js": "js", ".prop": "props",
                         ".propdef": "props", ".iflw": "iflw"}.get(_e)
                if _kind:
                    _by_type[_kind][_k] = _t
            _cat = _lib.merged_catalog(_by_type)
            st.success("Catalog merged: " + " · ".join(
                f"{_k}: {(_v.get('count') if isinstance(_v, dict) else None) or len(_v.get('capabilities', _v.get('identities', []))) if isinstance(_v, dict) else '?'}"
                for _k, _v in _cat.items()))


with tab2:
    st.header("Interface Browser")

    if not st.session_state.assessments:
        st.info("Load interfaces in **Tab 1** first.")
    else:
        assessments = st.session_state.assessments

        # Filters
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            f_size = st.multiselect("Size", ["S", "M", "L", "XL"],
                                    default=["S", "M", "L", "XL"])
        with fc2:
            all_sender_adapters = sorted({a.interface.sender_adapter for a in assessments})
            f_sender = st.multiselect("Sender adapter", all_sender_adapters,
                                      default=all_sender_adapters)
        with fc3:
            all_receiver_adapters = sorted({a.interface.receiver_adapter for a in assessments})
            f_receiver = st.multiselect("Receiver adapter", all_receiver_adapters,
                                        default=all_receiver_adapters)

        filtered = [
            a for a in assessments
            if _ma_size_weight(a.interface)[0] in f_size
            and a.interface.sender_adapter in f_sender
            and a.interface.receiver_adapter in f_receiver
        ]

        st.caption(f"Showing {len(filtered)} of {len(assessments)} interfaces")

        # Run recommendations
        if st.button("🔍 Analyse — what to start, park, or defer", key="run_recs"):
            render_recommendations_panel(
                filtered,
                configs=st.session_state.configs,
                verifications=st.session_state.verifications,
                clean_core_reports=st.session_state.clean_core,
                target_ids=st.session_state.target_ids,
            )
        elif st.session_state.recommendations:
            # Show tier badge per row already in table
            pass

        # Build display table
        # Sender/Receiver are now user-selectable adapter dropdowns (incl.
        # "None"). The list is curated to the common CPI adapter types; "None"
        # means no endpoint on that side. Both sides None → self-contained timer
        # scaffold; otherwise endpoint-bearing (clone-and-adapt today).
        _ADAPTER_OPTS = ["None", "HTTPS", "HTTP", "SOAP", "IDoc", "REST",
                         "OData", "SFTP", "FTP", "JDBC", "JMS", "AS2", "Mail",
                         "ProcessDirect", "SuccessFactors"]

        def _adapter_opt(raw):
            """Map a detected adapter string to one of _ADAPTER_OPTS."""
            if not raw:
                return "None"
            r = str(raw).strip().lower()
            return next((o for o in _ADAPTER_OPTS if o.lower() == r), "HTTPS")

        rows = []
        for a in filtered:
            iface = a.interface
            # Auto-generate package name
            domain   = detect_domain(iface.name, iface.description)
            auto_pkg = generate_package_name(
                st.session_state.get("company_code", "COMP"),
                iface.sender_system, iface.receiver_system, domain,
            )
            # Auto-generate iFlow display name
            obj_name  = iface.message_interface or iface.name
            auto_name = generate_iflow_name(
                "OUT", iface.sender_system, iface.receiver_system, obj_name
            )
            custom_pkg = st.session_state.get("package_names", {}).get(
                (iface.sender_system, iface.receiver_system), auto_pkg)
            custom_name = st.session_state.get("iflow_names", {}).get(iface.name, auto_name)

            _ma_sz, _ma_wt, _ma_days, _ma_lo, _ma_hi = _ma_assess(iface)
            _topo = st.session_state.get("endpoint_topology", {}).get(iface.name, {})
            # Default to the detected adapter only when a real system exists on
            # that side; otherwise None (→ timer scaffold). This keeps endpoint-
            # less interfaces timer-first while pre-filling real ones.
            _snd_def = _adapter_opt(iface.sender_adapter) if iface.sender_system else "None"
            _rcv_def = _adapter_opt(iface.receiver_adapter) if iface.receiver_system else "None"
            _snd_sel = _topo.get("sender_adapter", _snd_def)
            _rcv_sel = _topo.get("receiver_adapter", _rcv_def)
            rows.append({
                "Name":          iface.name,
                "iFlow Name":    custom_name,
                "Package":       custom_pkg,
                "Sender":        _snd_sel,
                "Receiver":      _rcv_sel,
                "Size":          _ma_sz,
                "Weight":        _ma_wt,
                "Effort (d)":    f"{_ma_lo:g}–{_ma_hi:g}",
                "Pattern":       a.recommended_pattern,
                "Extract order": _topo.get("order", ""),
                "BPM":           "⚠ Yes" if iface.has_bpm else "",
                "Multi-map":     "⚠ Yes" if iface.has_multi_mapping else "",
            })

        import pandas as pd
        df = pd.DataFrame(rows)

        st.caption("💡 Click any row to select/deselect it. Use the buttons "
                   "below for bulk selection. iFlow/Package names are editable "
                   "in the expander beneath.")

        # Native multi-row selection — click anywhere in a row to pick it.
        # Pre-select rows already in session_state.selected.
        _preselected = [i for i, r in enumerate(rows)
                        if r["Name"] in st.session_state.selected]
        event = st.dataframe(
            df,
            hide_index=True,
            use_container_width=True,
            on_select="rerun",
            selection_mode="multi-row",
            key="interface_table_select",
        )

        # Resolve selection from the click event (fall back to prior selection)
        sel_rows = event.selection.rows if event and event.selection else []
        if sel_rows:
            selected_names = [rows[i]["Name"] for i in sel_rows]
        else:
            selected_names = list(st.session_state.selected)

        # Editable iFlow/Package names + per-iFlow Endpoints (kept out of grid).
        # Endpoints = "no" (timer scaffold, self-contained, no matching) or
        # "yes" (clone-and-adapt against a matched template — the only current
        # endpoint-bearing path; not yet fully personalized). Default follows
        # the Tab-1 global toggle.
        # Per-interface endpoint topology: how many senders / receivers and the
        # extraction order. Default "none" (no endpoints → self-contained timer
        # scaffold). Choosing any count marks the interface endpoint-bearing.
        # NOTE: today only none-vs-some changes the deploy (none → timer, some →
        # clone-and-adapt). The exact counts + order are captured for the
        # from-scratch multi-sender generator (the endpoint-build milestone);
        # they don't yet change the generated iFlow.
        # Default OFF: every iFlow deploys as a self-contained, runnable timer
        # scaffold. Clone-and-adapt is opt-in only (enable this toggle) because
        # cloned templates start with real senders and can't run/verify on a
        # trial. This keeps adapter picks in the sheet (useful for effort
        # sizing) without routing to the unverifiable clone path.
        _ep_global = st.session_state.get("iflows_have_endpoints", False)
        _edit_cols = ["Name", "iFlow Name", "Package",
                      "Sender", "Receiver", "Extract order"]
        # Guard: empty filter result → empty df with no columns → KeyError on
        # slice. Use an empty frame WITH the right columns instead.
        _edit_df = df[_edit_cols] if not df.empty else pd.DataFrame(columns=_edit_cols)
        with st.expander("✏ Edit iFlow / Package names · Endpoints (sender / receiver adapters)"):
            if not _ep_global:
                st.caption("Endpoint-less mode (Tab 1) — sender/receiver picks "
                           "are ignored; every iFlow deploys as a self-contained "
                           "timer scaffold.")
            else:
                st.caption("Pick the sender and receiver adapter per interface. "
                           "**None** on both sides → self-contained timer "
                           "scaffold; any adapter → endpoint-bearing (clone-and-"
                           "adapt against a matched template).")
            edited = st.data_editor(
                _edit_df,
                column_config={
                    "Name":          st.column_config.TextColumn("Name", disabled=True),
                    "iFlow Name":    st.column_config.TextColumn("iFlow Name", width="large"),
                    "Package":       st.column_config.TextColumn("Package", width="large"),
                    "Sender":        st.column_config.SelectboxColumn(
                                         "Sender", options=_ADAPTER_OPTS,
                                         default="None", width="small",
                                         help="Sender (inbound) adapter. "
                                              "None = no inbound endpoint."),
                    "Receiver":      st.column_config.SelectboxColumn(
                                         "Receiver", options=_ADAPTER_OPTS,
                                         default="None", width="small",
                                         help="Receiver (outbound) adapter. "
                                              "None = no outbound endpoint."),
                    "Extract order": st.column_config.TextColumn(
                                         "Extract order", width="medium",
                                         help="Order to read senders when one "
                                              "depends on another, e.g. S1>S2 "
                                              "(captured for the multi-sender "
                                              "generator; not used yet)."),
                },
                hide_index=True, use_container_width=True,
                key="interface_names_editor",
            )
        # Save names + endpoint topology → deploy shape. none/none → timer
        # (self-contained); any count → endpoint-bearing (clone today). Global
        # OFF forces timer regardless.
        for _, row in edited.iterrows():
            iface_name = row["Name"]
            st.session_state.setdefault("iflow_names", {})[iface_name] = row["iFlow Name"]
            _snd = str(row.get("Sender", "None") or "None")
            _rcv = str(row.get("Receiver", "None") or "None")
            _ord = str(row.get("Extract order", "") or "")
            st.session_state.setdefault("endpoint_topology", {})[iface_name] = {
                "sender_adapter": _snd, "receiver_adapter": _rcv, "order": _ord}
            a = next((x for x in assessments if x.interface.name == iface_name), None)
            if a:
                key = (a.interface.sender_system, a.interface.receiver_system)
                st.session_state.setdefault("package_names", {})[key] = row["Package"]

        bc1, bc2, bc3 = st.columns(3)
        with bc1:
            if st.button("Select all filtered"):
                selected_names = [a.interface.name for a in filtered]
        with bc2:
            if st.button("Clear selection"):
                selected_names = []
        with bc3:
            if st.button("Select all L/XL"):
                selected_names = [a.interface.name for a in filtered
                                  if _ma_size_weight(a.interface)[0] in ("L", "XL")]

        st.session_state.selected = selected_names

        if selected_names:
            st.success(f"✅ {len(selected_names)} interface(s) selected. "
                       f"Refine in Tabs 3–4 (optional) or go straight to "
                       f"**Tab 5 · Generate** — default configs are applied "
                       f"automatically.")
            # Init configs for newly selected interfaces
            for name in selected_names:
                if name not in st.session_state.configs:
                    a = next((x for x in assessments if x.interface.name == name), None)
                    if a:
                        st.session_state.configs[name] = InterfaceConfig.from_interface_record(
                            a.interface
                        )
                if name not in st.session_state.target_ids:
                    st.session_state.target_ids[name] = "s4hana_cloud"


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — MATCH STANDARD IFLOW
# ═══════════════════════════════════════════════════════════════════════════════

with tab4:
    st.header("Configure Interfaces")

    if not st.session_state.selected:
        st.info("Select and match interfaces in Tabs 2–3 first.")
    else:
        selected = st.session_state.selected
        chosen_iface = st.selectbox(
            "Interface to configure",
            options=selected,
            key="cfg_iface_select",
        )

        cfg_obj: InterfaceConfig = st.session_state.configs.get(chosen_iface)
        if not cfg_obj:
            st.warning("No config found. Go back to Tab 2.")
        else:
            assessment = next(
                (a for a in st.session_state.assessments
                 if a.interface.name == chosen_iface), None
            )
            if assessment:
                _csz, _cwt, _cd, _clo, _chi = _ma_assess(assessment.interface)
                c1, c2, c3 = st.columns(3)
                c1.metric("Size", _csz)
                c2.metric("Weight", _cwt)
                c3.metric("Effort", f"{_clo:g}–{_chi:g}d")

            cfg_tabs = st.tabs([
                "🔌 Connectivity",
                "🔑 Authentication",
                "📨 Message",
                "🛡 Reliability",
                "⚡ Runtime",
            ])

            # ── Connectivity ─────────────────────────────────────────
            with cfg_tabs[0]:
                st.subheader("Sender (source system → CPI)")
                cc1, cc2 = st.columns(2)
                with cc1:
                    cfg_obj.sender_adapter = st.selectbox(
                        "Sender adapter", ADAPTER_TYPES,
                        index=ADAPTER_TYPES.index(cfg_obj.sender_adapter)
                        if cfg_obj.sender_adapter in ADAPTER_TYPES else 0,
                        key=f"s_adap_{chosen_iface}")
                    cfg_obj.sender_connectivity.address = st.text_input(
                        "Sender address / host",
                        value=cfg_obj.sender_connectivity.address,
                        key=f"s_addr_{chosen_iface}")
                with cc2:
                    cfg_obj.sender_connectivity.path = st.text_input(
                        "Sender path",
                        value=cfg_obj.sender_connectivity.path,
                        placeholder="/sap/xi/adapter_plain",
                        key=f"s_path_{chosen_iface}")
                    cfg_obj.sender_connectivity.port = st.number_input(
                        "Port (0 = default)", value=cfg_obj.sender_connectivity.port,
                        min_value=0, max_value=65535,
                        key=f"s_port_{chosen_iface}")

                # File/SFTP-specific sender fields
                if cfg_obj.sender_adapter in ("File", "FTP", "SFTP"):
                    st.divider()
                    st.caption("File/SFTP sender settings")
                    fc1, fc2, fc3 = st.columns(3)
                    with fc1:
                        cfg_obj.message.file_directory = st.text_input(
                            "Directory", value=cfg_obj.message.file_directory,
                            key=f"s_fdir_{chosen_iface}")
                    with fc2:
                        cfg_obj.message.file_pattern = st.text_input(
                            "File pattern", value=cfg_obj.message.file_pattern,
                            key=f"s_fpat_{chosen_iface}")
                    with fc3:
                        cfg_obj.message.file_poll_interval_sec = st.number_input(
                            "Poll interval (sec)", value=cfg_obj.message.file_poll_interval_sec,
                            min_value=10, key=f"s_fpoll_{chosen_iface}")
                    cfg_obj.message.file_post_processing = st.selectbox(
                        "Post-processing", ["Delete", "Move", "Archive"],
                        index=["Delete","Move","Archive"].index(
                            cfg_obj.message.file_post_processing),
                        key=f"s_fpost_{chosen_iface}")
                    if cfg_obj.message.file_post_processing in ("Move", "Archive"):
                        cfg_obj.message.file_archive_dir = st.text_input(
                            "Archive directory",
                            value=cfg_obj.message.file_archive_dir,
                            key=f"s_farch_{chosen_iface}")

                st.divider()
                st.subheader("Receiver (CPI → target system)")
                rc1, rc2 = st.columns(2)
                with rc1:
                    cfg_obj.receiver_adapter = st.selectbox(
                        "Receiver adapter", ADAPTER_TYPES,
                        index=ADAPTER_TYPES.index(cfg_obj.receiver_adapter)
                        if cfg_obj.receiver_adapter in ADAPTER_TYPES else 0,
                        key=f"r_adap_{chosen_iface}")
                    cfg_obj.receiver_connectivity.address = st.text_input(
                        "Receiver address / host",
                        value=cfg_obj.receiver_connectivity.address,
                        key=f"r_addr_{chosen_iface}")
                with rc2:
                    cfg_obj.receiver_connectivity.path = st.text_input(
                        "Receiver path",
                        value=cfg_obj.receiver_connectivity.path,
                        key=f"r_path_{chosen_iface}")
                    cfg_obj.receiver_connectivity.port = st.number_input(
                        "Port (0 = default)", value=cfg_obj.receiver_connectivity.port,
                        min_value=0, max_value=65535,
                        key=f"r_port_{chosen_iface}")

                # JDBC-specific
                if cfg_obj.receiver_adapter == "JDBC":
                    st.divider()
                    st.caption("JDBC settings")
                    cfg_obj.message.jdbc_driver = st.text_input(
                        "Driver class", value=cfg_obj.message.jdbc_driver,
                        key=f"jdbc_drv_{chosen_iface}")
                    cfg_obj.message.jdbc_jndi = st.text_input(
                        "JNDI name", value=cfg_obj.message.jdbc_jndi,
                        key=f"jdbc_jndi_{chosen_iface}")
                    cfg_obj.message.jdbc_query = st.text_area(
                        "SQL / Stored procedure", value=cfg_obj.message.jdbc_query,
                        key=f"jdbc_sql_{chosen_iface}")

                # IDoc-specific
                if cfg_obj.sender_adapter == "IDoc" or cfg_obj.receiver_adapter == "IDoc":
                    st.divider()
                    st.caption("IDoc settings")
                    ic1, ic2, ic3 = st.columns(3)
                    with ic1:
                        cfg_obj.message.idoc_type = st.text_input(
                            "IDoc type", value=cfg_obj.message.idoc_type,
                            key=f"idoc_t_{chosen_iface}")
                    with ic2:
                        cfg_obj.message.idoc_message_type = st.text_input(
                            "Message type", value=cfg_obj.message.idoc_message_type,
                            key=f"idoc_mt_{chosen_iface}")
                    with ic3:
                        cfg_obj.message.idoc_partner_profile = st.text_input(
                            "Partner profile", value=cfg_obj.message.idoc_partner_profile,
                            key=f"idoc_pp_{chosen_iface}")

                # AS2/AS4
                if cfg_obj.sender_adapter in ("AS2","AS4") or cfg_obj.receiver_adapter in ("AS2","AS4"):
                    st.divider()
                    st.caption("AS2/AS4 settings")
                    a1, a2, a3, a4 = st.columns(4)
                    with a1:
                        cfg_obj.message.as2_partner_id = st.text_input(
                            "Partner ID", value=cfg_obj.message.as2_partner_id,
                            key=f"as2_pid_{chosen_iface}")
                    with a2:
                        cfg_obj.message.as2_signing_alg = st.selectbox(
                            "Signing alg", ["SHA-256","SHA-512","RSA-SHA256"],
                            key=f"as2_sig_{chosen_iface}")
                    with a3:
                        cfg_obj.message.as2_encryption_alg = st.selectbox(
                            "Encryption alg", ["AES128","AES256","3DES"],
                            key=f"as2_enc_{chosen_iface}")
                    with a4:
                        cfg_obj.message.as2_mdn_required = st.checkbox(
                            "MDN required", value=cfg_obj.message.as2_mdn_required,
                            key=f"as2_mdn_{chosen_iface}")

            # ── Authentication ────────────────────────────────────────
            with cfg_tabs[1]:
                for side, auth_cfg, prefix in [
                    ("Sender authentication", cfg_obj.sender_auth, "sa"),
                    ("Receiver authentication", cfg_obj.receiver_auth, "ra"),
                ]:
                    st.subheader(side)
                    auth_cfg.method = st.selectbox(
                        "Method", AUTH_METHODS,
                        index=AUTH_METHODS.index(auth_cfg.method)
                        if auth_cfg.method in AUTH_METHODS else 0,
                        key=f"{prefix}_method_{chosen_iface}")

                    if auth_cfg.method == "Basic":
                        auth_cfg.credential_name = st.text_input(
                            "Credential store alias (CPI secure parameter name)",
                            value=auth_cfg.credential_name,
                            placeholder="MySystem_Credentials",
                            key=f"{prefix}_cred_{chosen_iface}")

                    elif auth_cfg.method == "OAuth2 Client Credentials":
                        oc1, oc2 = st.columns(2)
                        with oc1:
                            auth_cfg.token_url = st.text_input(
                                "Token URL", value=auth_cfg.token_url,
                                key=f"{prefix}_turl_{chosen_iface}")
                            auth_cfg.client_id = st.text_input(
                                "Client ID", value=auth_cfg.client_id,
                                key=f"{prefix}_cid_{chosen_iface}")
                        with oc2:
                            auth_cfg.client_secret = st.text_input(
                                "Client Secret", value=auth_cfg.client_secret,
                                type="password",
                                key=f"{prefix}_csec_{chosen_iface}")
                            auth_cfg.credential_name = st.text_input(
                                "Credential alias (store in CPI secure params)",
                                value=auth_cfg.credential_name,
                                key=f"{prefix}_oalias_{chosen_iface}")

                    elif auth_cfg.method == "API Key":
                        ak1, ak2 = st.columns(2)
                        with ak1:
                            auth_cfg.api_key_header = st.text_input(
                                "Header name", value=auth_cfg.api_key_header,
                                key=f"{prefix}_akh_{chosen_iface}")
                        with ak2:
                            auth_cfg.api_key_value = st.text_input(
                                "API Key value", value=auth_cfg.api_key_value,
                                type="password",
                                key=f"{prefix}_akv_{chosen_iface}")

                    elif auth_cfg.method == "Certificate":
                        auth_cfg.certificate_alias = st.text_input(
                            "Certificate alias (keystore)", value=auth_cfg.certificate_alias,
                            key=f"{prefix}_cert_{chosen_iface}")

                    st.divider()

            # ── Message ───────────────────────────────────────────────
            with cfg_tabs[2]:
                st.subheader("Message processing")
                m1, m2 = st.columns(2)
                with m1:
                    cfg_obj.message.is_async = st.toggle(
                        "Asynchronous processing",
                        value=cfg_obj.message.is_async,
                        key=f"async_{chosen_iface}")
                    cfg_obj.message.format = st.selectbox(
                        "Message format", MESSAGE_FORMATS,
                        index=MESSAGE_FORMATS.index(cfg_obj.message.format)
                        if cfg_obj.message.format in MESSAGE_FORMATS else 0,
                        key=f"fmt_{chosen_iface}")
                    cfg_obj.message.content_type = st.text_input(
                        "Content-Type header",
                        value=cfg_obj.message.content_type,
                        key=f"ct_{chosen_iface}")
                with m2:
                    cfg_obj.message.encoding = st.selectbox(
                        "Encoding", ["UTF-8", "UTF-16", "ISO-8859-1"],
                        index=["UTF-8","UTF-16","ISO-8859-1"].index(
                            cfg_obj.message.encoding)
                        if cfg_obj.message.encoding in ["UTF-8","UTF-16","ISO-8859-1"] else 0,
                        key=f"enc_{chosen_iface}")
                    cfg_obj.message.namespace = st.text_input(
                        "Namespace", value=cfg_obj.message.namespace,
                        key=f"ns_{chosen_iface}")

                st.divider()
                mp1, mp2 = st.columns(2)
                with mp1:
                    cfg_obj.message.mapping_program = st.text_input(
                        "Message mapping program",
                        value=cfg_obj.message.mapping_program,
                        placeholder="MM_PO_Create",
                        key=f"mp_{chosen_iface}")
                with mp2:
                    cfg_obj.message.xslt_program = st.text_input(
                        "XSLT program (if applicable)",
                        value=cfg_obj.message.xslt_program,
                        key=f"xslt_{chosen_iface}")

            # ── Reliability ───────────────────────────────────────────
            with cfg_tabs[3]:
                st.subheader("Error handling & reliability")
                rel = cfg_obj.reliability
                rc1, rc2 = st.columns(2)
                with rc1:
                    rel.retry_enabled = st.toggle(
                        "Enable automatic retry",
                        value=rel.retry_enabled,
                        key=f"retry_{chosen_iface}")
                    if rel.retry_enabled:
                        rel.retry_max_attempts = st.number_input(
                            "Max retry attempts", value=rel.retry_max_attempts,
                            min_value=1, max_value=10,
                            key=f"retry_max_{chosen_iface}")
                        rel.retry_delay_sec = st.number_input(
                            "Retry delay (seconds)", value=rel.retry_delay_sec,
                            min_value=5, max_value=3600,
                            key=f"retry_delay_{chosen_iface}")
                        rel.retry_exponential_backoff = st.checkbox(
                            "Exponential backoff",
                            value=rel.retry_exponential_backoff,
                            key=f"retry_exp_{chosen_iface}")

                    rel.dead_letter_enabled = st.toggle(
                        "Dead letter queue",
                        value=rel.dead_letter_enabled,
                        key=f"dlq_{chosen_iface}")
                    if rel.dead_letter_enabled:
                        rel.dead_letter_queue = st.text_input(
                            "DLQ name", value=rel.dead_letter_queue,
                            key=f"dlq_name_{chosen_iface}")

                with rc2:
                    rel.store_message_on_failure = st.toggle(
                        "Store message on failure",
                        value=rel.store_message_on_failure,
                        key=f"store_{chosen_iface}")
                    rel.idempotency_enabled = st.toggle(
                        "Idempotency check",
                        value=rel.idempotency_enabled,
                        key=f"idem_{chosen_iface}")
                    if rel.idempotency_enabled:
                        rel.idempotency_header = st.text_input(
                            "Deduplication header",
                            value=rel.idempotency_header,
                            key=f"idem_hdr_{chosen_iface}")
                    rel.alert_on_failure = st.toggle(
                        "Alert on failure",
                        value=rel.alert_on_failure,
                        key=f"alert_{chosen_iface}")
                    if rel.alert_on_failure:
                        rel.alert_address = st.text_input(
                            "Alert email / channel",
                            value=rel.alert_address,
                            key=f"alert_addr_{chosen_iface}")

                st.divider()
                rel.log_level = st.selectbox(
                    "Message log level", LOG_LEVELS,
                    index=LOG_LEVELS.index(rel.log_level)
                    if rel.log_level in LOG_LEVELS else 1,
                    key=f"log_lvl_{chosen_iface}")

                rel.quality_of_service = st.selectbox(
                    "Quality of service",
                    ["Exactly Once", "At Least Once", "Best Effort"],
                    index=["Exactly Once","At Least Once","Best Effort"].index(
                        cfg_obj.runtime.quality_of_service)
                    if cfg_obj.runtime.quality_of_service in
                       ["Exactly Once","At Least Once","Best Effort"] else 0,
                    key=f"qos_{chosen_iface}")
                cfg_obj.runtime.quality_of_service = rel.quality_of_service

            # ── Runtime ───────────────────────────────────────────────
            with cfg_tabs[4]:
                st.subheader("Runtime configuration")
                rt = cfg_obj.runtime
                rtc1, rtc2 = st.columns(2)
                with rtc1:
                    rt.timeout_sec = st.number_input(
                        "Timeout (seconds)", value=rt.timeout_sec,
                        min_value=10, max_value=3600,
                        key=f"timeout_{chosen_iface}")
                    rt.max_message_mb = st.number_input(
                        "Max message size (MB)", value=rt.max_message_mb,
                        min_value=1, max_value=500,
                        key=f"maxmsg_{chosen_iface}")
                    rt.scheduler_cron = st.text_input(
                        "Scheduler cron (leave empty if triggered)",
                        value=rt.scheduler_cron,
                        placeholder="0 */1 * * *  (every hour)",
                        key=f"cron_{chosen_iface}")

                with rtc2:
                    rt.parallel_enabled = st.toggle(
                        "Parallel processing",
                        value=rt.parallel_enabled,
                        key=f"par_{chosen_iface}")
                    if rt.parallel_enabled:
                        rt.parallel_max_threads = st.number_input(
                            "Max parallel threads", value=rt.parallel_max_threads,
                            min_value=2, max_value=20,
                            key=f"par_thr_{chosen_iface}")

            # Persist updated config back
            st.session_state.configs[chosen_iface] = cfg_obj

            # Copy to all
            if len(selected) > 1:
                st.divider()
                if st.button("📋 Copy reliability + runtime config to ALL selected interfaces"):
                    for other_name in selected:
                        if other_name != chosen_iface and other_name in st.session_state.configs:
                            other = st.session_state.configs[other_name]
                            other.reliability = cfg_obj.reliability
                            other.runtime     = cfg_obj.runtime
                    st.success("Copied to all selected interfaces")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5 — GENERATE
# ═══════════════════════════════════════════════════════════════════════════════

with tab5:
    st.header("Generate & Download")

    if not st.session_state.selected:
        st.info("Select interfaces in **Tab 2** first. Tabs 3–4 (match + "
                "configure) are optional refinement — default configs are "
                "applied automatically on selection, so you can come straight "
                "here to generate, or use the **⚡ green-path batch** on the "
                "Clean Core tab to process AUTO interfaces in one click.")
    else:
        selected     = st.session_state.selected
        assessments  = st.session_state.assessments
        configs      = st.session_state.configs
        target_ids   = st.session_state.target_ids
        resolutions  = st.session_state.resolutions

        # Summary table
        import pandas as pd
        summary_rows = []
        _tot_lo = _tot_hi = 0.0
        for name in selected:
            a   = next((x for x in assessments if x.interface.name == name), None)
            cfg_obj = configs.get(name)
            tid = target_ids.get(name, "?")
            target_label = DESTINATION_REGISTRY.get(tid, type("", (), {"label": tid})()).label
            if a:
                _gsz, _gwt, _gd, _glo, _ghi = _ma_assess(a.interface)
                _tot_lo += _glo
                _tot_hi += _ghi
            else:
                _gsz, _gwt, _glo, _ghi = "?", "?", 0, 0
            summary_rows.append({
                "Interface":    name,
                "Target":       target_label,
                "Size":         _gsz,
                "Weight":       _gwt,
                "Effort (d)":   f"{_glo:g}–{_ghi:g}" if a else "?",
                "Sender":       cfg_obj.sender_adapter if cfg_obj else "?",
                "Receiver":     cfg_obj.receiver_adapter if cfg_obj else "?",
                "Async":        "✓" if (cfg_obj and cfg_obj.message.is_async) else "",
                "Retry":        f"✓ ×{cfg_obj.reliability.retry_max_attempts}"
                                if (cfg_obj and cfg_obj.reliability.retry_enabled) else "",
            })

        st.dataframe(pd.DataFrame(summary_rows), hide_index=True, use_container_width=True)

        st.metric("Total estimated effort", f"{_tot_lo:g}–{_tot_hi:g} days")

        st.divider()

        output_dir = st.text_input("Output directory", value="./output", key="gen_out")

        if st.button("🚀 Generate all", type="primary"):
            out_path = Path(output_dir)
            out_path.mkdir(parents=True, exist_ok=True)

            selected_assessments = [a for a in assessments
                                    if a.interface.name in selected]
            unique_targets = list(set(target_ids.values()))

            # Clean-core analysis runs automatically (former Tab 6) so the
            # generated reports always carry RISE/clean-core verdicts —
            # generation itself already follows clean-core patterns.
            try:
                _cc = CleanCoreAnalyzer()
                for _a in selected_assessments:
                    _cfg = st.session_state.configs.get(_a.interface.name)
                    st.session_state.clean_core[_a.interface.name] = \
                        _cc.analyze_record(_a.interface, cfg=_cfg)
            except Exception as _ccerr:
                logger.warning("clean-core auto-run skipped: %s", _ccerr)

            # Build resolutions
            from fetcher.user_settings import get_setting as _get_setting
            fetcher_hub = HubFetcher(
                default_ttl=86400,
                hub_api_key=st.session_state.cfg.get("destinations", {}).get(
                    "hub_api_key", "") or _get_setting("hub_api_key", ""),
            )
            resolver = DestinationResolver(fetcher=fetcher_hub)
            try:
                st.session_state.resolutions = resolver.resolve_all(
                    selected_assessments, unique_targets)
            except Exception:
                st.session_state.resolutions = {}

            progress = st.progress(0)
            status   = st.empty()
            results  = []

            use_pipeline = should_use_pipeline(
                selected_assessments,
                st.session_state.get("pipeline_mode", "auto")
            )

            if use_pipeline:
                status.text("Generating Pipeline Concept iFlows…")
                pipeline_scaf = PipelineScaffolder(output_dir=output_dir)
                try:
                    packages = pipeline_scaf.scaffold_all(
                        selected_assessments,
                        configs,
                        company_code=st.session_state.get("company_code", "COMP"),
                        strategy=st.session_state.get("migration_strategy", "bluefield"),
                        custom_package_names=st.session_state.get("package_names", {}),
                    )
                    for pkg in packages:
                        for p in pkg.iflow_paths:
                            results.append({
                                "interface": pkg.package_name,
                                "status":    "✅ Done",
                                "file":      str(p),
                                "warnings":  [],
                            })
                    st.info(f"🔗 Pipeline mode: generated {len(packages)} package(s) "
                            f"with 4 generic iFlows + {len(selected_assessments)} scenario iFlows "
                            f"+ Partner Directory + JMS queue config per package.")
                except Exception as e:
                    st.error(f"Pipeline generation failed: {e}")
            else:
                templates_dir = str(ROOT / "templates")
                _eh_now = st.session_state.get("gold_eh_variant")
                st.caption("Gold-standard error handling: "
                           + (_eh_now or "off — pure fidelity")
                           + " (set via the selector in the upload section)")
                scaffolder    = IFlowScaffolder(templates_dir=templates_dir,
                                                output_dir=output_dir,
                                                resources_dir=PINNED_LOCAL_DIRS["template_library_dir"],
                                                extra_resources=st.session_state.get("uploaded_resources"), passthrough=st.session_state.get("uploaded_passthrough"),
                                                gold_error_handling=st.session_state.get("gold_eh_variant"), gold_eh_replace=bool(st.session_state.get("gold_eh_replace")), gold_eh_notify=bool(st.session_state.get("gold_eh_notify")), gold_eh_sftp=bool(st.session_state.get("gold_eh_sftp")), gold_eh_company=st.session_state.get("company_code", ""))
                # Load the learned capability corpus ONCE (heavy; cached). When
                # available, generate_bundle pulls REAL learned artifacts instead
                # of generic templates. Falls back to template-mode (corpus=None)
                # if no packages are uploaded or the corpus can't be built — so
                # generation never breaks, only upgrades.
                _gen_corpus = None
                try:
                    # FOLDER FIRST: the persisted by-type harvest (e.g. Final/),
                    # set externally so it survives project re-import. This is the
                    # richest source — the whole catalog, not just Tab-1 uploads.
                    from fetcher.user_settings import get_dir
                    _corpus_dir = get_dir("capability_corpus_dir")
                    if _corpus_dir:
                        status.text("Building capability corpus (one-time; cached "
                                    "to disk after)…")
                        _gen_corpus = _load_capability_corpus_dir(_corpus_dir)
                    else:
                        _pkgs = st.session_state.get("uploaded_packages") or []
                        if _pkgs:
                            _sig = "|".join(sorted(p.get("filename", "")
                                                   for p in _pkgs))
                            status.text("Building capability corpus (one-time; "
                                        "cached after)…")
                            _gen_corpus = _load_capability_corpus(_pkgs, _sig)
                except Exception:
                    _gen_corpus = None   # any failure → template fallback
                for i, a in enumerate(selected_assessments):
                    name     = a.interface.name
                    cfg_obj  = configs.get(name)
                    tid      = target_ids.get(name, "s4hana_cloud")
                    resolved = st.session_state.resolutions.get(name, {}).get(tid)
                    status.text(f"Generating {name} (iFlow + scripts + mapping)…")
                    try:
                        iflow_path = scaffolder.scaffold(a, resolved=resolved)
                        # Generate the referenced scripts + mapping so the
                        # package is self-contained (the "real win").
                        from scaffolder.artifact_bundle import generate_bundle
                        bundle = generate_bundle(a.interface, iflow_path,
                                                 corpus=_gen_corpus)
                        st.session_state.setdefault("artifact_bundles", {})[name] = [
                            (art.rel_path, art.content) for art in bundle.artifacts
                        ]
                        # show which artifacts came from REAL learned capabilities
                        # vs generic templates (so capability-mode is visible)
                        _real = [art.rel_path.split("/")[-1]
                                 for art in bundle.artifacts
                                 if "real learned" in (art.note or "").lower()]
                        results.append({
                            "interface": name, "status": "✅ Done",
                            "file": str(iflow_path),
                            "warnings": [],
                            "artifacts": [art.rel_path.split("/")[-1]
                                          for art in bundle.artifacts],
                            "from_capability": _real,
                        })
                    except Exception as e:
                        results.append({"interface": name, "status": f"❌ {e}",
                                        "file": "", "warnings": [str(e)]})
                    progress.progress((i + 1) / len(selected_assessments))

            # Reports
            status.text("Generating reports…")
            reporter = ReportGenerator(output_dir=output_dir)
            reporter.generate_excel(
                selected_assessments,
                resolutions=st.session_state.resolutions,
                target_ids=unique_targets,
            )
            reporter.generate_markdown(
                selected_assessments,
                resolutions=st.session_state.resolutions,
                target_ids=unique_targets,
            )
            progress.progress(1.0)
            status.text("Done!")

            # Compute the per-interface manual-steps list once, while we
            # have the configs in scope, then persist everything the
            # downstream sections need to session state. Without this,
            # ANY other button click reruns the script with the Generate-all
            # button == False, and the entire results+intervention+proposal
            # block disappears.
            all_manual = []
            for name in selected:
                cfg_obj = configs.get(name)
                if cfg_obj and cfg_obj.manual_steps:
                    for step in cfg_obj.manual_steps:
                        all_manual.append(f"**{name}**: {step}")

            st.session_state["gen_results"]      = results
            st.session_state["gen_selected"]     = selected_assessments
            st.session_state["gen_unique_tgts"]  = unique_targets
            st.session_state["gen_output_dir"]   = output_dir
            st.session_state["gen_all_manual"]   = all_manual
            st.session_state["gen_completed"]    = True

            # ── Completion notification (email + browser banner/sound) ──
            if st.session_state.get("notify_enabled", True):
                _done = sum(1 for r in results if "✅" in r.get("status", ""))
                _msg = (f"Generation complete: {_done}/{len(results)} interfaces "
                        f"processed.")
                # Browser banner + sound
                try:
                    import streamlit.components.v1 as _cmp
                    from reporter.run_notifier import browser_notify_html
                    _cmp.html(browser_notify_html(_msg, play_sound=True), height=70)
                except Exception:
                    pass
                # Email (if SMTP configured)
                _smtp = st.session_state.get("smtp_cfg") or {}
                if _smtp.get("host") and _smtp.get("to_addr"):
                    try:
                        from reporter.run_notifier import send_email_notification
                        ok, m = send_email_notification(
                            _smtp, "CPI Migration — generation complete", _msg)
                        if ok:
                            st.caption("📧 Notification email sent")
                    except Exception:
                        pass

        # ── Persistent post-generate sections ────────────────────────────
        # Render these whenever a previous Generate-all has completed in this
        # session, regardless of which button caused this rerun.
        if st.session_state.get("gen_completed"):
            results              = st.session_state["gen_results"]
            selected_assessments = st.session_state["gen_selected"]
            unique_targets       = st.session_state["gen_unique_tgts"]
            output_dir           = st.session_state["gen_output_dir"]
            all_manual           = st.session_state["gen_all_manual"]

            # Results table
            st.subheader("Results")
            res_df = pd.DataFrame(results)[["interface", "status", "file"]]
            st.dataframe(res_df, hide_index=True, use_container_width=True)

            # Deploy directly under Results — generate, review, push. The two
            # disk-export buttons inside the deploy section cover every
            # manual-import case; everything below is review material.
            render_deploy_section(
                selected_assessments, configs,
                unique_targets, output_dir
            )

            # Auto-generated "what's left" per iFlow — computed from the real
            # preflight tasks each interface triggers (WE20 for IDoc, comm
            # arrangements for inbound, security material for credentials, …)
            # plus any <FILL_> placeholders in its generated files. Replaces the
            # old manual tick-box checklist: it tells you what remains, per iFlow.
            st.subheader("🗒️ Remaining work per iFlow (auto-generated)")
            st.caption("What still needs doing before each iFlow is production-ready "
                       "— derived from its adapters and generated artifacts.")
            try:
                import os as _oswl, glob as _glwl

                _ON_PREM = {"IDOC", "RFC", "FILE", "JDBC"}
                _NEEDS_CRED = {"SOAP", "HTTPS", "HTTP", "ODATA", "REST",
                               "SFTP", "MAIL", "AS2", "JDBC"}
                _SAP_INBOUND = {"IDOC", "SOAP", "HTTPS", "ODATA", "REST", "RFC"}

                def _remaining_for(rec):
                    sa = (getattr(rec, "sender_adapter", "") or "").upper()
                    ra = (getattr(rec, "receiver_adapter", "") or "").upper()
                    ad = {sa, ra}
                    out = []
                    if "IDOC" in ad:
                        out.append(("Configure WE20 partner profile + WE21 port in the SAP system",
                                    "Client Basis"))
                    if ad & _ON_PREM:
                        out.append(("Map the on-premise system in Cloud Connector "
                                    "(host/port + access control)", "Client Basis"))
                    if ra in _SAP_INBOUND:
                        out.append(("Create the inbound Communication Arrangement in S/4HANA",
                                    "Client Basis"))
                    if ad & _NEEDS_CRED:
                        out.append(("Add credentials under Monitor → Manage Security Material",
                                    "Client Security"))
                    try:
                        from analyzer.orchestration_flag import (
                            assess_orchestration)
                        _kinds = {}
                        _src = getattr(rec, "source_iflow_xml", "") or ""
                        if _src:
                            from extractor.iflow_parser import parse_iflow
                            _m = parse_iflow(_src, "o")
                            for _s in _m.steps.values():
                                _kinds[_s.kind] = _kinds.get(_s.kind, 0) + 1
                            _kinds["__processes__"] = len(
                                getattr(_m, "processes", []) or [])
                        _of = assess_orchestration(
                            kinds=_kinds, name=getattr(rec, "name", ""))
                        if _of.flagged:
                            out.append((
                                "⚠ Orchestration-shaped (ccBPM profile) — "
                                "architecture decision needed: CPI + SBPA "
                                "split or stateless redesign. "
                                + "; ".join(_of.reasons[:3]), "Architect"))
                    except Exception:
                        pass
                    return out

                def _fill_count(nm):
                    n = 0
                    for pat in (f"parameters/*{nm}*", f"groovy/*{nm}*", f"artifacts/{nm}/**"):
                        for fp in _glwl.glob(_oswl.path.join(output_dir, pat), recursive=True):
                            if _oswl.path.isfile(fp):
                                try:
                                    n += open(fp, encoding="utf-8", errors="ignore").read().count("<FILL_")
                                except OSError:
                                    pass
                    return n

                for a in selected_assessments:
                    rec = a.interface
                    nm = getattr(rec, "name", "?")
                    items = _remaining_for(rec)
                    fills = _fill_count(nm)
                    left = len(items) + (1 if fills else 0)
                    label = (f"✅ {nm} — nothing outstanding" if left == 0
                             else f"{nm} — {left} item(s) left "
                                  f"({getattr(rec,'sender_adapter','?')}→"
                                  f"{getattr(rec,'receiver_adapter','?')})")
                    with st.expander(label, expanded=False):
                        if fills:
                            st.markdown(f"- **Fill {fills} placeholder value(s)** "
                                        "(`<FILL_…>`) in this iFlow's generated files")
                        for task, owner in items:
                            st.markdown(f"- **{task}**  \n  ↳ owner: _{owner}_")
                        if left == 0:
                            st.success("No interface-specific setup detected — review + activate.")

                # genuinely one-time, project-wide platform setup (not per iFlow)
                try:
                    from reporter.preflight_generator import PreflightGenerator
                    _pf = PreflightGenerator(output_dir)
                    base = _pf._btp_base_items([]) + _pf._cpi_tenant_items()
                    with st.expander(f"🌐 One-time platform setup — {len(base)} item(s)",
                                     expanded=False):
                        for it in base:
                            st.markdown(f"- **{it.task}** — {it.detail}")
                except Exception:
                    pass
            except Exception as _wlerr:
                st.caption(f"(remaining-work view unavailable: {_wlerr})")

            # Additional outputs: parameters.prop per interface, BSR bundle.
            # All offline, tenant-independent. Hidden behind buttons so they
            # don't auto-run on every page load.
            render_additional_outputs_section(
                selected_assessments, configs, output_dir
            )

            # Migration ceiling + intervention estimate + proposal
            render_ceiling_and_intervention(
                selected_assessments,
                configs=configs,
                clean_core_reports=st.session_state.clean_core,
                verification_reports=st.session_state.verifications,
                output_dir=output_dir,
            )
            render_proposal_generator(
                selected_assessments,
                output_dir=output_dir,
                project_name="CPI Migration",
            )

            if all_manual:
                st.subheader("⚠ Interface-specific manual steps")
                for step in all_manual:
                    st.markdown(f"- {step}")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 6 — CLEAN CORE
# ═══════════════════════════════════════════════════════════════════════════════

with tab8:
    st.header("🤖 AI Solver")
    st.caption(
        "Claude generates complete CPI solutions, deploys to your DEV tenant, "
        "you test and give feedback, Claude refines. Repeat until approved, then promote."
    )

    # ── Local capability solver (learned-catalog, no LLM) ─────────────────
    # Complements the Claude solver below: matches the requirement against the
    # locally-learned capability catalogs (groovy/xslt/schema/mmap) built from
    # the uploaded packages, via corpus_pipeline + the reasoning layer. Reasoned
    # suggestions, tenant-verified by you.
    with st.expander("🧠 Capability Solver (local, learned catalogs)",
                     expanded=False):
        render_capability_solver()

    if not st.session_state.selected:
        st.info("Select interfaces in **Tab 2** first.")
    else:
        assessments  = st.session_state.assessments
        configs      = st.session_state.configs
        selected     = st.session_state.selected

        # ── Connection check ──────────────────────────────────────────
        if not st.session_state.cpi_connected:
            st.warning("⚠ Connect to CPI tenant in the sidebar to enable auto-deploy. "
                       "You can still generate solutions without a connection.")

        # ── Project status overview ───────────────────────────────────
        solver_status = SolverSession
        status_rows = []
        for name in selected:
            session = SolverSession.load(name)
            a       = next((x for x in assessments if x.interface.name == name), None)
            if session:
                last_iter = session.iterations[-1] if session.iterations else {}
                status_rows.append({
                    "Interface":   name,
                    "Iterations":  session.current_iteration,
                    "Confidence":  f"{last_iter.get('confidence',0)*100:.0f}%",
                    "Deployed":    "✓" if last_iter.get("deployed") else "✗",
                    "Test":        "✓ Pass" if last_iter.get("test_passed") \
                                   else ("✗ Fail" if last_iter.get("test_passed") is False \
                                   else "—"),
                    "Status":      "✅ Approved" if session.approved \
                                   else ("⚠ Needs feedback" if session.needs_feedback \
                                   else "🔄 In progress"),
                })
            else:
                status_rows.append({
                    "Interface":   name, "Iterations": 0,
                    "Confidence":  "—", "Deployed": "—",
                    "Test": "—", "Status": "⬜ Not started",
                })

        if status_rows:
            import pandas as pd
            # data_editor with a Pick column drives the interface focus below.
            # Checkbox state is held by the editor itself across reruns.
            for row in status_rows:
                row["Select"] = False
            cols_order = ["Select"] + [c for c in status_rows[0] if c != "Select"]
            s_df = pd.DataFrame(status_rows)[cols_order]
            edited_s = st.data_editor(
                s_df,
                column_config={
                    "Select": st.column_config.CheckboxColumn(
                        "Pick", default=False,
                        help="Tick one row to load that interface below."),
                },
                disabled=[c for c in cols_order if c != "Select"],
                hide_index=True,
                use_container_width=True,
                key="solver_table_editor",
            )
            s_picked = [r["Interface"] for _, r in edited_s.iterrows() if r["Select"]]
            chosen = (s_picked[0] if s_picked
                      else (selected[0] if selected else None))
        else:
            chosen = selected[0] if selected else None

        st.divider()

        if chosen:
            st.caption(f"Working on: **{chosen}** "
                       f"(tick a different row above to switch)")

        a   = next((x for x in assessments if x.interface.name == chosen), None)
        cfg = configs.get(chosen)
        if not a:
            st.warning("Interface not found in assessments")
            st.stop()

        session = SolverSession.get_or_create(chosen)
        iface   = a.interface

        # Show interface summary
        sc1, sc2, sc3, sc4 = st.columns(4)
        sc1.metric("Size",          _ma_size_weight(iface)[0])
        sc2.metric("Sender",        iface.sender_adapter)
        sc3.metric("Receiver",      iface.receiver_adapter)
        sc4.metric("Iteration",     session.current_iteration)

        st.divider()

        # ── Iteration history ─────────────────────────────────────────
        if session.iterations:
            with st.expander(f"📋 History ({session.current_iteration} iteration(s))"):
                for it in session.iterations:
                    icon = "✅" if it.get("test_passed") \
                           else ("❌" if it.get("test_passed") is False else "⏳")
                    st.markdown(
                        f"**Iteration {it['iteration']}** {icon} — "
                        f"Confidence: {it.get('confidence',0)*100:.0f}% | "
                        f"Deployed: {'Yes' if it.get('deployed') else 'No'}"
                    )
                    if it.get("reasoning"):
                        st.caption(f"Reasoning: {it['reasoning']}")
                    if it.get("feedback") and it["feedback"].get("free_text"):
                        st.caption(f"Your feedback: {it['feedback']['free_text']}")

        # ── Solve panel ───────────────────────────────────────────────
        if not session.approved:
            is_first    = session.current_iteration == 0
            has_failed  = (session.iterations and
                           session.iterations[-1].get("test_passed") is False)

            if is_first:
                st.subheader("▶ Generate initial solution")
                btn_label = "🤖 Generate & Deploy to DEV"
            else:
                st.subheader(f"🔄 Iteration {session.current_iteration + 1} — Refine")
                btn_label = "🤖 Refine & Re-deploy to DEV"

            # Feedback panel (shown from iteration 2+)
            feedback_obj = None
            if not is_first:
                st.subheader("📝 Your feedback on the current version")

                # Common issues checkboxes
                st.caption("Check all that apply:")
                checked = []
                cols    = st.columns(2)
                for i, issue in enumerate(COMMON_ISSUES):
                    with cols[i % 2]:
                        if st.checkbox(issue, key=f"fb_chk_{chosen}_{i}"):
                            checked.append(issue)

                # Free text
                free_text = st.text_area(
                    "Additional notes / specific corrections",
                    placeholder="e.g. 'The LIFNR field should map to SupplierID, not VendorCode. "
                                "The namespace is wrong — should be http://company.com/po'",
                    height=120,
                    key=f"fb_text_{chosen}",
                )

                # Diff annotation
                with st.expander("📌 Annotate specific lines (optional)"):
                    st.caption("Paste specific lines from the generated script that need fixing:")
                    diff_line  = st.text_input("Line/code to fix", key=f"fb_diff_line_{chosen}")
                    diff_note  = st.text_input("What it should be", key=f"fb_diff_note_{chosen}")
                    diff_annots = []
                    if diff_line and diff_note:
                        diff_annots = [{"line": diff_line, "comment": diff_note}]

                if checked or free_text or diff_annots:
                    from engine.feedback_loop import FeedbackEntry
                    feedback_obj = FeedbackEntry(
                        iteration=session.current_iteration,
                        timestamp=__import__("datetime").datetime.now().isoformat(),
                        free_text=free_text,
                        checked_issues=checked,
                        diff_annotations=diff_annots,
                    )

            # Options
            opt1, opt2 = st.columns(2)
            with opt1:
                auto_deploy_solver = st.checkbox(
                    "Auto-deploy to DEV after generating",
                    value=st.session_state.cpi_connected,
                    key=f"solver_autodeploy_{chosen}",
                )
            with opt2:
                min_confidence = st.slider(
                    "Min confidence to auto-deploy",
                    min_value=0.5, max_value=1.0, value=0.7, step=0.05,
                    key=f"solver_conf_{chosen}",
                )

            if st.button(btn_label, type="primary", key=f"solver_run_{chosen}"):
                with st.spinner(f"Claude solving {chosen}… (iteration {session.current_iteration+1})"):
                    try:
                        solver = ClaudeSolver(
                            api_key=st.session_state.get("anthropic_api_key", "")
                            or st.session_state.cfg.get("destinations", {}).get(
                                "anthropic_api_key", ""))

                        # Get uploader if connected
                        uploader = None
                        if st.session_state.cpi_connected and auto_deploy_solver:
                            uploader = CPIUploader(
                                st.session_state.cpi_base_url,
                                st.session_state.cpi_session,
                            )

                        loop = FeedbackLoopManager(
                            solver=solver,
                            uploader=uploader,
                            output_dir="./output",
                        )

                        result, updated_session = loop.run_iteration(
                            assessment=a,
                            cfg=cfg,
                            feedback=feedback_obj,
                            auto_deploy=(auto_deploy_solver and
                                         st.session_state.cpi_connected),
                        )

                        st.session_state.solver_results[chosen] = result
                        session = updated_session

                        # Show results
                        conf_colour = "green" if result.confidence >= 0.8 \
                            else "orange" if result.confidence >= 0.6 else "red"
                        st.markdown(
                            f"**Confidence:** :{conf_colour}[{result.confidence*100:.0f}%]"
                        )

                        if result.reasoning and (session.current_iteration <= 2):
                            st.info(f"💬 **Claude:** {result.reasoning}")

                        # Show artifacts
                        if result.artifacts:
                            st.subheader("Generated artifacts")
                            for art in result.artifacts:
                                with st.expander(
                                    f"📄 {art.filename} "
                                    f"[{art.artifact_type}] "
                                    f"— confidence {art.confidence*100:.0f}%"
                                ):
                                    lang = "groovy" if art.artifact_type == "groovy" \
                                           else "xml" if art.filename.endswith(".xml") \
                                           else "properties" if art.filename.endswith(".prop") \
                                           else "text"
                                    st.code(art.content, language=lang)
                                    buf = art.content.encode("utf-8")
                                    st.download_button(
                                        f"⬇ Download {art.filename}",
                                        data=buf,
                                        file_name=art.filename,
                                        key=f"dl_{chosen}_{art.filename}",
                                    )

                        if result.iflow_modifications:
                            st.subheader("iFlow modifications to apply")
                            for mod in result.iflow_modifications:
                                st.markdown(f"- {mod}")

                        if result.remaining_manual:
                            st.subheader("⚠ Still needs manual work")
                            for task in result.remaining_manual:
                                st.markdown(f"- {task}")

                        deploy_status = "✅ Deployed to DEV" \
                            if (auto_deploy_solver and
                                st.session_state.cpi_connected and
                                result.confidence >= min_confidence) \
                            else "📁 Artifacts saved locally (not deployed)"
                        st.success(deploy_status)

                    except Exception as e:
                        st.error(f"Solver error: {e}")
                        import traceback
                        st.code(traceback.format_exc())

        # ── Test result recording ─────────────────────────────────────
        if session.iterations and session.iterations[-1].get("deployed"):
            st.divider()
            st.subheader("🧪 Record test result")
            st.caption("After running the iFlow in DEV with your test payload:")

            tr1, tr2, tr3 = st.columns(3)
            with tr1:
                if st.button("✅ Test passed", key=f"test_pass_{chosen}"):
                    session.mark_test_result(session.current_iteration, True)
                    session.save()
                    st.success("Marked as passed")
            with tr2:
                if st.button("❌ Test failed", key=f"test_fail_{chosen}"):
                    session.mark_test_result(session.current_iteration, False)
                    session.save()
                    st.warning("Marked as failed — provide feedback above and re-run")
            with tr3:
                if st.button("✅ Approve & promote", type="primary",
                             key=f"approve_{chosen}"):
                    session.approved = True
                    session.save()
                    st.success(f"✅ {chosen} approved!")
                    st.balloons()

        # ── Approved → promote ────────────────────────────────────────
        if session.approved:
            st.success(f"✅ **{chosen}** is approved")
            pr1, pr2 = st.columns(2)
            with pr1:
                if st.button("→ Promote to QA", key=f"promote_qa_{chosen}"):
                    session.promoted_to_qa = True
                    session.save()
                    st.success("Marked for QA promotion via cTMS")
            with pr2:
                if st.button("→ Promote to PROD", key=f"promote_prod_{chosen}",
                             disabled=not session.promoted_to_qa):
                    session.promoted_to_prod = True
                    session.save()
                    st.success("Marked for PROD promotion via cTMS")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 ENHANCEMENT — Advisory banners + recommendation engine
# Appended as render functions called from existing Tab 2
# ═══════════════════════════════════════════════════════════════════════════════

def render_advisory_banner(rec):
    """Show advisory flags for one interface."""
    if not rec.advisory_flags:
        return
    for flag in rec.advisory_flags:
        icon = "🔴" if flag.severity == "BLOCKER" else "🟡" if flag.severity == "WARNING" else "ℹ"
        with st.expander(f"{icon} {flag.title}", expanded=(flag.severity == "BLOCKER")):
            st.write(flag.detail)
            st.markdown(f"**Expertise needed:** {flag.expertise_needed}")
            st.markdown(f"**Action:** {flag.action}")
            st.markdown(f"**Quote type:** `{flag.quote_type}`")


def render_recommendations_panel(assessments, configs, verifications,
                                  clean_core_reports, target_ids):
    """Full recommendation panel for Tab 2."""
    engine  = RecommendationEngine()
    project = engine.analyze_all(
        assessments,
        configs=configs,
        verification_reports=verifications,
        clean_core_reports=clean_core_reports,
        target_ids=target_ids,
    )

    # Store in session
    for tier_list in [project.start_now, project.blocked_on_client,
                       project.park_research, project.specialist, project.defer]:
        for rec in tier_list:
            st.session_state.recommendations[rec.interface_name] = rec

    # Summary metrics
    st.info(project.summary_message)
    rm1, rm2, rm3, rm4, rm5 = st.columns(5)
    rm1.metric("🟢 Start now",   len(project.start_now))
    rm2.metric("🟡 Blocked",     len(project.blocked_on_client))
    rm3.metric("🟠 Park",        len(project.park_research))
    rm4.metric("🔴 Specialist",  len(project.specialist))
    rm5.metric("⚫ Defer",       len(project.defer))

    # Park + research section
    if project.park_research or project.specialist:
        st.divider()
        st.subheader("🟠 Interfaces to park — research required")
        tracker = ClientProblemTracker()

        park_all = project.park_research + project.specialist
        for rec in park_all:
            with st.expander(
                f"{rec.tier_icon} **{rec.interface_name}** — {rec.tier} | {rec.quote_type}"
            ):
                for flag in rec.blocking_flags:
                    st.error(f"**{flag.title}:** {flag.detail}")
                    st.markdown(f"**What you need:** {flag.expertise_needed}")
                    st.markdown(f"**Action:** {flag.action}")

                st.markdown("**Next steps:**")
                for step in rec.next_steps:
                    st.markdown(f"- {step}")

                # Park button
                park_col1, park_col2 = st.columns(2)
                with park_col1:
                    client_name = st.text_input(
                        "Client name (for tracker)",
                        key=f"park_client_{rec.interface_name}",
                        placeholder="CEMEX",
                    )
                with park_col2:
                    problem_sel = st.selectbox(
                        "Problem type",
                        options=list(PROBLEM_TYPES.keys()),
                        format_func=lambda x: PROBLEM_TYPES[x],
                        index=list(PROBLEM_TYPES.keys()).index(rec.problem_type)
                        if rec.problem_type in PROBLEM_TYPES else 0,
                        key=f"park_prob_{rec.interface_name}",
                    )

                if client_name and st.button(
                    "📌 Park this interface",
                    key=f"park_btn_{rec.interface_name}"
                ):
                    tracker.park_interface(
                        client_name=client_name,
                        interface_name=rec.interface_name,
                        problem_type=problem_sel,
                        complexity=next(
                            (a.complexity for a in assessments
                             if a.interface.name == rec.interface_name), "HIGH"
                        ),
                    )
                    st.success(f"Parked {rec.interface_name} for {client_name}")

                # Generate parking message
                contact = st.text_input(
                    "Contact name (for message)",
                    key=f"park_contact_{rec.interface_name}",
                    placeholder="Juan",
                )
                if st.button("✉ Generate parking message",
                             key=f"park_msg_{rec.interface_name}"):
                    msg = tracker.generate_parking_message(
                        rec.interface_name, contact
                    )
                    st.text_area("Message to client",
                                 value=msg, height=180,
                                 key=f"park_msg_text_{rec.interface_name}")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 9 — CLIENT TRACKER
# ═══════════════════════════════════════════════════════════════════════════════

# Add Tab 9 to tab list dynamically — appended content only
# Note: tab9 is rendered below as a separate with block

def render_client_tracker_tab():
    """Full client/problem tracker UI."""
    st.header("📋 Client Tracker")
    st.caption(
        "Track parked interfaces per client. When you solve a problem type, "
        "find all affected clients and generate follow-up messages."
    )

    tracker = ClientProblemTracker()

    ct_tab1, ct_tab2, ct_tab3, ct_tab4 = st.tabs([
        "🔍 Problem overview",
        "👥 Clients",
        "✉ Follow-ups ready",
        "📐 Effort model",
    ])

    # ── Two-axis effort model (build × delivery friction) ─────────────
    # Axis 1 (build days) comes from artifact weight × a coefficient that
    # CALIBRATES from logged actuals; Axis 2 is the 30-second consultant
    # enrichment per interface (the factors no scan can see). PIMAS/SAP
    # numbers stay in reports as the client-trusted reference — this is
    # the planning truth.
    with ct_tab4:
        st.subheader("📐 Two-axis effort estimate")
        from analyzer.delivery_friction import (
            FrictionProfile, FRICTION_FACTORS, CalibrationStore,
            ActualRecord, estimate_calibrated)
        _cal = CalibrationStore()
        c1, c2 = st.columns(2)
        c1.metric("Calibration records", _cal.n_records())
        c2.metric("Build coeff (days/weight pt)", f"{_cal.build_coeff():.3f}")
        if not st.session_state.assessments:
            st.info("Load interfaces in **Tab 1** first.")
        else:
            _names = [a.interface.name
                      for a in st.session_state.assessments]
            _pick = st.selectbox("Interface", _names, key="fr_iface")
            _a = next(a for a in st.session_state.assessments
                      if a.interface.name == _pick)
            _, _w, _, _, _ = _ma_assess(_a.interface)
            _answers = {}
            _cols = st.columns(len(FRICTION_FACTORS))
            for _col, (_factor, _table) in zip(_cols,
                                               FRICTION_FACTORS.items()):
                _answers[_factor] = _col.selectbox(
                    _factor.replace("_", " "), list(_table),
                    key=f"fr_{_factor}")
            _est = estimate_calibrated(_pick, int(_w),
                                       FrictionProfile(**_answers),
                                       store=_cal)
            m1, m2, m3 = st.columns(3)
            m1.metric("Build effort", f"{_est.build_days:g} days")
            m2.metric("Friction", f"×{_est.friction_multiplier:g}")
            m3.metric("Calendar", f"{_est.calendar_weeks:g} weeks")
            st.caption("Calendar = build × friction ÷ 5 + 1 wk wave "
                       "overhead. Waves parallelize across interfaces; "
                       "this is per-interface pressure, not a sum.")
            with st.expander("✍ Record actuals (calibrates every future "
                             "estimate)"):
                _ab = st.number_input("Actual build days", 0.0, 500.0,
                                      0.0, 0.5, key="fr_actual_build")
                _ac = st.number_input("Actual calendar weeks", 0.0, 200.0,
                                      0.0, 0.5, key="fr_actual_cal")
                _an = st.text_input("Note", key="fr_actual_note")
                if st.button("💾 Record actual", key="fr_record") \
                        and _ab > 0:
                    _cal.record_actual(ActualRecord(
                        interface=_pick, weight=int(_w),
                        actual_build_days=float(_ab),
                        actual_calendar_weeks=float(_ac),
                        profile=_answers, note=_an))
                    st.success(f"Recorded. New coeff: "
                               f"{_cal.build_coeff():.3f}")

    # ── Problem overview ──────────────────────────────────────────────
    with ct_tab1:
        parked_by_problem = tracker.get_parked_by_problem()

        if not parked_by_problem:
            st.info("No parked interfaces yet. Park interfaces from Tab 2 recommendations.")
        else:
            for prob_type, interfaces in parked_by_problem.items():
                prob_label = PROBLEM_TYPES.get(prob_type, prob_type)
                with st.expander(
                    f"**{prob_label}** — {len(interfaces)} interface(s) across "
                    f"{len(set(i['client_name'] for i in interfaces))} client(s)"
                ):
                    import pandas as pd
                    df = pd.DataFrame([{
                        "Interface":  i["interface_name"],
                        "Client":     i["client_name"],
                        "Company":    i["company"],
                        "Complexity": i["complexity"],
                        "Parked":     i["parked_at"][:10],
                    } for i in interfaces])
                    st.dataframe(df, hide_index=True, use_container_width=True)

                    if st.button(f"✅ I solved this — notify clients",
                                 key=f"solve_{prob_type}"):
                        affected = tracker.solve_problem_type(prob_type)
                        st.success(
                            f"Marked as solved. {len(affected)} client(s) ready for follow-up."
                        )
                        st.rerun()

    # ── Clients ───────────────────────────────────────────────────────
    with ct_tab2:
        # Add new client
        with st.expander("➕ Add new client"):
            nc1, nc2 = st.columns(2)
            with nc1:
                new_client_name = st.text_input("Client/project name",
                                                 key="new_client_name")
                new_company     = st.text_input("Company", key="new_company")
            with nc2:
                new_contact     = st.text_input("Contact name", key="new_contact")
                new_title       = st.text_input("Contact title", key="new_title")
            new_notes = st.text_input("Notes", key="new_notes")
            if st.button("Add client", key="add_client_btn"):
                if new_client_name and new_company:
                    tracker.add_client(
                        new_client_name, new_company,
                        new_contact, new_title, new_notes
                    )
                    st.success(f"Added client: {new_client_name}")
                    st.rerun()

        # Client list
        clients = tracker.get_all_clients()
        if not clients:
            st.info("No clients added yet.")
        else:
            for client in clients:
                summary = tracker.get_client_summary(client.client_name)
                with st.expander(
                    f"**{client.company}** ({client.client_name}) — "
                    f"✅ {summary['completed']} done | "
                    f"🟠 {summary['parked']} parked"
                ):
                    if summary["parked_list"]:
                        st.markdown("**Parked interfaces:**")
                        for p in summary["parked_list"]:
                            solved_tag = "✅ Solved" if p.get("solved") else "⏳ Researching"
                            st.markdown(
                                f"- `{p['interface_name']}` — "
                                f"{PROBLEM_TYPES.get(p['problem_type'], p['problem_type'])} "
                                f"| {solved_tag}"
                            )

    # ── Follow-ups ready ──────────────────────────────────────────────
    with ct_tab3:
        ready = tracker.get_clients_ready_for_followup()

        if not ready:
            st.info("No follow-ups ready yet. Solve a problem type in the Problem Overview tab.")
        else:
            st.success(f"{len(ready)} client(s) ready for follow-up!")
            for client_info in ready:
                with st.expander(
                    f"✉ {client_info['company']} — "
                    f"{len(client_info['interfaces'])} interface(s) ready"
                ):
                    for iface in client_info["interfaces"]:
                        st.markdown(f"- `{iface['interface_name']}`")

                    msg = tracker.generate_followup_message(
                        client_info["client_name"],
                        client_info["interfaces"],
                        client_info["contact_name"],
                    )
                    st.text_area(
                        "Follow-up message",
                        value=msg, height=200,
                        key=f"followup_{client_info['client_name']}",
                    )

                    if st.button(
                        "✅ Mark follow-up sent",
                        key=f"sent_{client_info['client_name']}"
                    ):
                        for iface in client_info["interfaces"]:
                            tracker.mark_followup_sent(
                                client_info["client_name"],
                                iface["interface_name"],
                            )
                        st.success("Marked as sent")
                        st.rerun()


with tab9:
    render_client_tracker_tab()


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 10 — PAYLOAD LAB (analyzer · redactor · flow tester · MPL check)
# ═══════════════════════════════════════════════════════════════════════════════

with tab10:
    st.header("🧪 Payload Lab")

    # ── 🔍 Trace analysis (#5): the troubleshooting view ─────────────────
    # Reads tools/dump_traces.py output: execution timeline, failure
    # pinpoint with the real Camel exception, per-step payload evolution.
    with st.expander("🔍 Trace analysis — step-by-step run forensics"):
        _tr_dir = st.text_input("Trace dump folder", key="trace_dir",
                                value="output/traces")
        import os as _tos
        if _tos.path.isdir(_tr_dir):
            from analyzer.trace_analysis import load_dump, analyze
            _msgs = load_dump(_tr_dir)
            if not _msgs:
                st.info("No dumped messages found here — run "
                        "`python3 -m tools.dump_traces …` first.")
            else:
                _opts = {f"{m.status:>9} · {m.iflow} · {m.guid[:14]}… "
                         f"({len(m.steps)} steps)": m for m in _msgs}
                _pick = st.selectbox("Message", list(_opts),
                                     key="trace_pick")
                _mt = _opts[_pick]
                _names = {}
                _src = st.session_state.get("trace_iflw_xml", "")
                _f = analyze(_mt, model_names=_names or None)
                if _f["failure"]:
                    _fl = _f["failure"]
                    st.error(f"💥 Failed at step #{_fl['order']} "
                             f"**{_fl['at']}** — `{_fl['exception']}`\n\n"
                             f"{_fl['message']}")
                    if _fl["last_good_payload"]:
                        st.caption("Last good payload entering the "
                                   "failing step:")
                        st.code(_fl["last_good_payload"][:400],
                                language="xml")
                elif _mt.steps:
                    st.success(f"✅ COMPLETED — {len(_mt.steps)} steps")
                else:
                    st.warning("No step data (trace probably wasn't "
                               "active when this message ran).")
                if _mt.steps:
                    st.dataframe([{
                        "#": t.order, "step": t.model_step_id or t.step_id,
                        "status": t.status or "·",
                        "ms": t.duration_ms or "",
                        "payload": (f"{t.payload_size} B"
                                    if t.payload_size >= 0 else "—"),
                        "kind": t.payload_kind,
                        "branch": t.branch}
                        for t in _mt.steps], use_container_width=True,
                        height=320)
                    if _f["payload_evolution"]:
                        _changes = [p for p in _f["payload_evolution"]
                                    if p["delta"] or p["kind_changed"]]
                        if _changes:
                            st.caption("Payload changed at: " + " · ".join(
                                f"#{p['order']} {p['step']} "
                                f"(Δ{p['delta']:+d}"
                                + (", kind→" + p["kind"]
                                   if p["kind_changed"] else "") + ")"
                                for p in _changes[:8]))
                    if _f["hotspots"]:
                        st.caption("Slowest steps: " + " · ".join(
                            f"{h['step']} {h['ms']}ms"
                            for h in _f["hotspots"]))
                    _ps = st.selectbox(
                        "Inspect step payload",
                        [t.model_step_id or t.step_id or str(t.order)
                         for t in _mt.steps if t.payload_size >= 0],
                        key="trace_payload_pick") if any(
                        t.payload_size >= 0 for t in _mt.steps) else None
                    if _ps:
                        _t = next(t for t in _mt.steps
                                  if (t.model_step_id or t.step_id or
                                      str(t.order)) == _ps)
                        st.code(_t.payload_head or "(empty)",
                                language="xml"
                                if _t.payload_kind == "xml" else None)
                        if _t.headers:
                            st.json({k: v for k, v in list(
                                _t.headers.items())[:12]})
        else:
            st.caption("Folder not found — dump traces first "
                       "(tools/dump_traces.py).")

    st.caption(
        "Troubleshoot client payloads safely: analyze the structure, produce "
        "a redacted copy (values masked, every name/format token kept), "
        "validate against requirements, and test the payload against an "
        "iFlow's actual step expectations to localize the failing step — "
        "all locally, nothing leaves this machine.")

    _pl_file = st.file_uploader(
        "Payload (XML · JSON · CSV · EDI · flat)", key="pl_payload",
        accept_multiple_files=False)
    _pl_keep = st.text_input(
        "Values to keep unredacted (comma-separated — e.g. routing codes)",
        key="pl_keep",
        help="Everything else is masked type-preservingly: digits→9, "
             "letters→X/x, separators/lengths/dates keep their shape.")
    c1, c2 = st.columns(2)
    with c1:
        _pl_schema = st.file_uploader("Optional schema (XSD / JSON Schema)",
                                      key="pl_schema")
    with c2:
        _pl_flow = st.file_uploader(
            "Optional iFlow bundle or package export (.zip)",
            key="pl_flow",
            help="Single bundle → that flow is tested. Package export → "
                 "every iFlow in it is tested (per-package troubleshooting).")
    _pl_xslt = st.checkbox("Also apply XSLT 1.0 mappings locally",
                           key="pl_xslt")

    if _pl_file and st.button("🔬 Inspect payload", key="pl_inspect",
                              type="primary"):
        from inspector.core import inspect_payload
        _keep = [k.strip() for k in (_pl_keep or "").split(",") if k.strip()]
        _schema_txt = _pl_schema.getvalue().decode("utf-8", "replace")             if _pl_schema else None
        _kind = None
        if _pl_schema:
            _kind = "xsd" if _pl_schema.name.lower().endswith(".xsd")                 else "json"
        _rep = inspect_payload(_pl_file.getvalue(), schema=_schema_txt,
                               schema_kind=_kind, keep_values=_keep)
        st.subheader(f"Format: `{_rep.fmt}` — "
                     + ("parsed ✓" if _rep.parse_ok
                        else f"PARSE FAILED: {_rep.parse_error}"))
        _ic1, _ic2 = st.columns(2)
        with _ic1:
            st.markdown("**Structure profile**")
            st.json(_rep.profile, expanded=False)
        with _ic2:
            st.markdown("**Findings**")
            for f in _rep.findings:
                _icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️",
                         "INFO": "ℹ️"}.get(f.level, "·")
                st.markdown(f"{_icon} **{f.check}** — {f.detail}"
                            + (f"  \n`{f.path}`" if f.path else ""))
        if _rep.redacted:
            st.markdown("**Redacted copy (safe to share)**")
            st.code(_rep.redacted[:4000] +
                    ("\n…(truncated preview)"
                     if len(_rep.redacted) > 4000 else ""),
                    language=_rep.fmt if _rep.fmt in ("xml", "json")
                    else None)
            st.download_button(
                "⬇ Download redacted payload", _rep.redacted,
                file_name=f"redacted_{_pl_file.name}", key="pl_dl")

    if _pl_file and _pl_flow and st.button(
            "🎯 Test payload against iFlow(s)", key="pl_flowtest"):
        import io as _io
        import zipfile as _zf
        from inspector.flow_test import test_payload_against_flow
        _payload = _pl_file.getvalue().decode("utf-8", "replace")
        _raw = _pl_flow.getvalue()
        _bundles = []
        try:
            _z = _zf.ZipFile(_io.BytesIO(_raw))
            _names = _z.namelist()
            if any(n.endswith(".iflw") for n in _names):
                _f = next(n for n in _names if n.endswith(".iflw"))
                _bundles.append((_f.rsplit("/", 1)[-1][:-5],
                                 _z.read(_f).decode("utf-8", "replace"),
                                 {m: _z.read(m) for m in _names if m != _f}))
            else:
                for _n in _names:
                    if not _n.endswith("_content"):
                        continue
                    _rawi = _z.read(_n)
                    if _rawi[:2] != b"PK":
                        continue
                    _zb = _zf.ZipFile(_io.BytesIO(_rawi))
                    _bn = _zb.namelist()
                    for _f in _bn:
                        if _f.endswith(".iflw"):
                            _bundles.append(
                                (_f.rsplit("/", 1)[-1][:-5],
                                 _zb.read(_f).decode("utf-8", "replace"),
                                 {m: _zb.read(m) for m in _bn if m != _f}))
        except _zf.BadZipFile:
            st.error("Not a readable zip.")
        if not _bundles:
            st.warning("No .iflw found in the upload.")
        for _name, _iflw, _res in _bundles:
            with st.expander(f"Flow: {_name}", expanded=len(_bundles) == 1):
                try:
                    _finds = test_payload_against_flow(
                        _iflw, _payload, _res, apply_xslt=_pl_xslt)
                except Exception as _exc:
                    st.error(f"Flow test failed: {_exc}")
                    continue
                _fails = sum(1 for f in _finds if f.level == "FAIL")
                st.markdown(f"**{len(_finds)} check(s), {_fails} FAIL**")
                for f in _finds:
                    _icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️",
                             "INFO": "ℹ️", "SKIP": "⏭"}.get(f.level, "·")
                    st.markdown(
                        f"{_icon} `{f.kind}` **{f.step}** — "
                        f"{f.check}: {f.detail}")
        st.caption(
            "Checks run against THIS payload as the flow's input. Steps "
            "after a format conversion (JSON→XML wraps in <root>, CSV→XML, "
            "EDI→XML) see the converted intermediate — grab it from the "
            "tenant trace and test it here as its own payload to check "
            "those steps.")

    st.divider()
    st.markdown("**Tenant check — last runs of a deployed artifact**")
    _mpl_id = st.text_input("Artifact Id (as deployed)", key="pl_mpl_id")
    if _mpl_id and st.button("📡 Fetch last MPLs", key="pl_mpl_btn"):
        if not st.session_state.get("cpi_session"):
            st.warning("Connect to the tenant first (Profiles tab).")
        else:
            from fetcher.cpi_uploader import CPIUploader
            _u = CPIUploader(st.session_state.cpi_base_url,
                             st.session_state.cpi_session)
            _rows = _u.fetch_mpls(_mpl_id.strip(), top=5)
            if not _rows:
                st.info("No MPLs returned (artifact never ran, id wrong, "
                        "or API blocked).")
            for _r in _rows:
                _sicon = {"COMPLETED": "✅", "FAILED": "❌",
                          "PROCESSING": "⏳", "ESCALATED": "🟠",
                          "RETRY": "🔁"}.get(_r.get("Status"), "·")
                st.markdown(
                    f"{_sicon} **{_r.get('Status')}** "
                    f"{(_r.get('CustomStatus') or '')} — "
                    f"{_r.get('LogStart')} → {_r.get('LogEnd')}  \n"
                    f"`{_r.get('MessageGuid')}`")
                if _r.get("Error"):
                    st.code(_r["Error"][:1200])


# ═══════════════════════════════════════════════════════════════════════════════
# PROGRAM 2 — API MANAGEMENT  (rendered only when mode == "apim")
# ═══════════════════════════════════════════════════════════════════════════════
# The migration `with tabN:` blocks above executed into a hidden sink when in
# APIM mode. Clear that sink now so none of the migration UI is visible, then
# render the six APIM tabs.

if _ep_sink is not None:
    _ep_sink.empty()   # discard hidden endpoint tabs (3 & 4) when endpoints OFF

if _apim_active:
    if _migration_sink is not None:
        _migration_sink.empty()   # discard everything the migration tabs rendered

    # APIM landscape persists across reruns
    if "apim_landscape" not in st.session_state:
        st.session_state["apim_landscape"] = APIMLandscape()
    _land: APIMLandscape = st.session_state["apim_landscape"]

    # ── Landscape overview ───────────────────────────────────────────────────
    with apim_tab_landscape:
        st.header("🗺 API Management Landscape")
        st.caption("Overview of all API proxies, products, and applications "
                   "in this project, with referential-integrity validation.")

        c1, c2, c3 = st.columns(3)
        c1.metric("API Proxies", len(_land.proxies))
        c2.metric("Products",    len(_land.products))
        c3.metric("Applications", len(_land.applications))

        issues = _land.validate()
        if issues:
            st.error(f"⚠ {len(issues)} referential-integrity issue(s):")
            for iss in issues:
                st.markdown(f"- {iss}")
        elif _land.proxies or _land.products or _land.applications:
            st.success("✅ Landscape is referentially consistent.")
        else:
            st.info("Empty landscape. Add a proxy in the **API Proxies** tab "
                    "to get started, or bridge a migrated iFlow into an API.")

        if _land.proxies:
            st.subheader("Proxies")
            st.dataframe([
                {"Name": p.name, "Base Path": p.base_path, "Target": p.target_url,
                 "Auth": p.auth_type.value, "From iFlow": p.source_iflow or "—"}
                for p in _land.proxies
            ], hide_index=True, use_container_width=True)
        if _land.products:
            st.subheader("Products")
            st.dataframe([
                {"Name": p.name, "Proxies": ", ".join(p.proxies),
                 "Quota": f"{p.quota_requests}/{p.quota_interval}",
                 "Environments": ", ".join(p.environments)}
                for p in _land.products
            ], hide_index=True, use_container_width=True)
        if _land.applications:
            st.subheader("Applications")
            st.dataframe([
                {"Name": a.name, "Products": ", ".join(a.subscribed_products),
                 "Active Keys": len(a.active_keys()), "Total Keys": len(a.keys),
                 "Developer": a.developer_email or "—"}
                for a in _land.applications
            ], hide_index=True, use_container_width=True)

    # ── API Proxies ──────────────────────────────────────────────────────────
    with apim_tab_proxies:
        st.header("🔌 API Proxies")
        st.caption("Create managed API proxies. A proxy fronts a backend "
                   "(an iFlow runtime URL, an OData service, any HTTP target) "
                   "and attaches policies for security and traffic control.")

        with st.expander("➕ New proxy", expanded=not _land.proxies):
            colp1, colp2 = st.columns(2)
            with colp1:
                np_name = st.text_input("Proxy name", key="apim_np_name",
                                        placeholder="OrderAPI")
                np_base = st.text_input("Base path", key="apim_np_base",
                                        placeholder="/v1/orders")
            with colp2:
                np_target = st.text_input("Target URL", key="apim_np_target",
                                          placeholder="https://rt.cpi.com/http/ordersync")
                np_auth = st.selectbox("Auth type", [a.value for a in ProxyAuthType],
                                       key="apim_np_auth")
            np_desc = st.text_input("Description", key="apim_np_desc")

            # Bridge: build a proxy from a migrated iFlow
            iflow_names = list(st.session_state.get("iflow_names", {}).values())
            bridge_iflow = ""
            if iflow_names:
                bridge_iflow = st.selectbox(
                    "…or front a migrated iFlow (optional)",
                    ["(none)"] + iflow_names, key="apim_bridge_iflow")

            if st.button("Create proxy", type="primary", key="apim_create_proxy"):
                if bridge_iflow and bridge_iflow != "(none)":
                    proxy = proxy_from_iflow(
                        bridge_iflow,
                        np_base or f"/v1/{bridge_iflow.lower()}",
                        np_target or "https://CHANGE_ME/http/endpoint",
                        ProxyAuthType(np_auth))
                    if np_name:
                        proxy.name = np_name
                    _land.proxies.append(proxy)
                    st.success(f"✓ Created proxy '{proxy.name}' fronting iFlow '{bridge_iflow}'")
                    st.rerun()
                elif np_name and np_base and np_target:
                    _land.proxies.append(APIProxy(
                        name=np_name, base_path=np_base, target_url=np_target,
                        auth_type=ProxyAuthType(np_auth), description=np_desc))
                    st.success(f"✓ Created proxy '{np_name}'")
                    st.rerun()
                else:
                    st.warning("Name, base path, and target URL are required.")

        # Per-proxy generation + preview
        for idx, proxy in enumerate(_land.proxies):
            with st.expander(f"🔌 {proxy.name} — {proxy.base_path}"):
                gen = generate_proxy(proxy)
                st.markdown(f"**Target:** `{proxy.target_url}`  \n"
                            f"**Auth:** {proxy.auth_type.value}  \n"
                            f"**Policies:** {', '.join(gen.manifest['policies'])}")
                if gen.manifest["unbuilt_policies"]:
                    st.warning(f"Referenced but not built: "
                               f"{', '.join(gen.manifest['unbuilt_policies'])}")
                files = gen.all_files()
                st.caption(f"Bundle: {len(files)} files")
                pick = st.selectbox("Preview file", list(files.keys()),
                                    key=f"apim_proxy_file_{idx}")
                st.code(files[pick], language="xml")
                # Download whole bundle as zip
                import io as _io, zipfile as _zip
                zbuf = _io.BytesIO()
                with _zip.ZipFile(zbuf, "w", _zip.ZIP_DEFLATED) as zf:
                    for fn, content in files.items():
                        zf.writestr(fn, content)
                zbuf.seek(0)
                st.download_button(
                    "⬇ Download proxy bundle (zip)", data=zbuf.getvalue(),
                    file_name=f"{proxy.name}_proxy.zip", mime="application/zip",
                    key=f"apim_proxy_dl_{idx}")
                if st.button("🗑 Delete proxy", key=f"apim_proxy_del_{idx}"):
                    _land.proxies.pop(idx)
                    st.rerun()

    # ── Products ─────────────────────────────────────────────────────────────
    with apim_tab_products:
        st.header("📦 API Products")
        st.caption("Bundle one or more proxies into a product with a rate-limit "
                   "quota. Applications subscribe to products, not proxies.")

        if not _land.proxies:
            st.info("Create at least one proxy first.")
        else:
            with st.expander("➕ New product", expanded=not _land.products):
                pr_name = st.text_input("Product name", key="apim_pr_name",
                                        placeholder="OrderProduct")
                pr_proxies = st.multiselect(
                    "Proxies in bundle", [p.name for p in _land.proxies],
                    key="apim_pr_proxies")
                colq1, colq2, colq3 = st.columns(3)
                with colq1:
                    pr_quota = st.number_input("Quota (requests)", min_value=1,
                                               value=1000, key="apim_pr_quota")
                with colq2:
                    pr_interval = st.selectbox(
                        "Per", ["second", "minute", "hour", "day", "month"],
                        index=2, key="apim_pr_interval")
                with colq3:
                    pr_envs = st.multiselect("Environments",
                                             ["dev", "test", "prod"],
                                             default=["dev"], key="apim_pr_envs")
                if st.button("Create product", type="primary", key="apim_create_product"):
                    if pr_name and pr_proxies:
                        _land.products.append(APIProduct(
                            name=pr_name, proxies=pr_proxies,
                            quota_requests=int(pr_quota), quota_interval=pr_interval,
                            environments=pr_envs or ["dev"]))
                        st.success(f"✓ Created product '{pr_name}'")
                        st.rerun()
                    else:
                        st.warning("Name and at least one proxy are required.")

            for idx, product in enumerate(_land.products):
                with st.expander(f"📦 {product.name} "
                                 f"({len(product.proxies)} prox, "
                                 f"{product.quota_requests}/{product.quota_interval})"):
                    st.markdown(f"**Proxies:** {', '.join(product.proxies)}  \n"
                                f"**Quota:** {product.quota_requests} per "
                                f"{product.quota_interval}  \n"
                                f"**Environments:** {', '.join(product.environments)}")
                    if st.button("🗑 Delete product", key=f"apim_prod_del_{idx}"):
                        _land.products.pop(idx)
                        st.rerun()

    # ── Applications + keys ──────────────────────────────────────────────────
    with apim_tab_apps:
        st.header("👥 Applications")
        st.caption("Consumer registrations. Each application subscribes to "
                   "products and holds API keys with a lifecycle "
                   "(issue, revoke, expire).")

        if not _land.products:
            st.info("Create at least one product first.")
        else:
            with st.expander("➕ New application", expanded=not _land.applications):
                app_name = st.text_input("Application name", key="apim_app_name",
                                         placeholder="MobileApp")
                app_email = st.text_input("Developer email", key="apim_app_email")
                app_products = st.multiselect(
                    "Subscribe to products", [p.name for p in _land.products],
                    key="apim_app_products")
                if st.button("Create application", type="primary", key="apim_create_app"):
                    if app_name:
                        _land.applications.append(Application(
                            name=app_name, developer_email=app_email,
                            subscribed_products=app_products))
                        st.success(f"✓ Created application '{app_name}'")
                        st.rerun()
                    else:
                        st.warning("Application name is required.")

            for idx, app in enumerate(_land.applications):
                with st.expander(f"👤 {app.name} "
                                 f"({len(app.active_keys())} active key(s))"):
                    st.markdown(f"**Products:** "
                                f"{', '.join(app.subscribed_products) or '—'}  \n"
                                f"**Developer:** {app.developer_email or '—'}")
                    colk1, colk2 = st.columns(2)
                    with colk1:
                        ttl = st.number_input(
                            "Key TTL (days, 0 = no expiry)", min_value=0,
                            value=90, key=f"apim_ttl_{idx}")
                    with colk2:
                        st.write("")
                        st.write("")
                        if st.button("🔑 Issue key", key=f"apim_issue_{idx}"):
                            k = app.issue_key(ttl_days=int(ttl) if ttl else None)
                            st.success(f"Issued: `{k.key_value}`")
                            st.rerun()
                    if app.keys:
                        for kidx, key in enumerate(app.keys):
                            state = key.state.value
                            valid = "✅" if key.is_valid() else "❌"
                            kcol1, kcol2 = st.columns([4, 1])
                            with kcol1:
                                exp = (key.expires_at.strftime("%Y-%m-%d")
                                       if key.expires_at else "never")
                                st.code(f"{valid} {key.key_value[:16]}… "
                                        f"[{state}, expires {exp}]")
                            with kcol2:
                                if key.is_valid() and st.button(
                                        "Revoke", key=f"apim_revoke_{idx}_{kidx}"):
                                    app.revoke_key(key.key_value)
                                    st.rerun()
                    if st.button("🗑 Delete application", key=f"apim_app_del_{idx}"):
                        _land.applications.pop(idx)
                        st.rerun()

    # ── Policy library browser ───────────────────────────────────────────────
    with apim_tab_policies:
        st.header("🛡 Policy Library")
        st.caption("Parameterised API Management policies. Preview the XML, "
                   "tune parameters, and copy into a proxy.")

        pol_name = st.selectbox("Policy type", policy_library.list_policies(),
                                key="apim_pol_select")
        st.markdown("**Parameters**")
        xml = None
        if pol_name == "Quota":
            q1, q2, q3 = st.columns(3)
            cnt = q1.number_input("Allow count", min_value=1, value=1000, key="apim_q_cnt")
            iv = q2.number_input("Interval", min_value=1, value=1, key="apim_q_iv")
            tu = q3.selectbox("Time unit",
                              ["second", "minute", "hour", "day", "month"],
                              index=2, key="apim_q_tu")
            xml = policy_library.quota(allow_count=int(cnt), interval=int(iv), time_unit=tu)
        elif pol_name == "SpikeArrest":
            rate = st.text_input("Rate (e.g. 100ps, 50pm)", value="100ps", key="apim_sa_rate")
            xml = policy_library.spike_arrest(rate=rate)
        elif pol_name == "CORS":
            origins = st.text_input("Allow origins", value="*", key="apim_cors_o")
            methods = st.text_input("Allow methods",
                                    value="GET, POST, PUT, DELETE, OPTIONS",
                                    key="apim_cors_m")
            xml = policy_library.cors(allow_origins=origins, allow_methods=methods)
        elif pol_name == "SetHeader":
            hn = st.text_input("Header name", value="X-Custom", key="apim_sh_n")
            hv = st.text_input("Header value", value="value", key="apim_sh_v")
            xml = policy_library.assign_message_set_header("Set-Header", hn, hv)
        elif pol_name == "JSONThreatProtection":
            d = st.number_input("Max depth", min_value=1, value=10, key="apim_jtp_d")
            sl = st.number_input("Max string length", min_value=1, value=5000, key="apim_jtp_s")
            xml = policy_library.json_threat_protection(max_depth=int(d),
                                                        max_string_length=int(sl))
        elif pol_name == "VerifyAPIKey":
            loc = st.text_input("Key location", value="request.header.apikey",
                                key="apim_vak_loc")
            xml = policy_library.verify_api_key(key_location=loc)
        elif pol_name == "OAuthVerify":
            xml = policy_library.oauth_verify()

        if xml:
            st.code(xml, language="xml")
            st.download_button("⬇ Download policy XML", data=xml,
                               file_name=f"{pol_name}.xml", mime="text/xml",
                               key="apim_pol_dl")

    # ── Deploy (stub) ────────────────────────────────────────────────────────
    with apim_tab_deploy:
        st.header("🚀 Deploy")
        st.caption("Deploy proxies to your API Management tenant.")
        st.info(
            "Deployment to a live API Management tenant is not yet wired in. "
            "For now, download each proxy bundle from the **API Proxies** tab "
            "and import it via the API Management UI or the apiportal API. "
            "This tab is the placeholder for one-click deploy once tenant "
            "credentials and the apiportal client are connected.")
        if _land.proxies:
            st.markdown("**Proxies ready to deploy:**")
            for p in _land.proxies:
                st.markdown(f"- `{p.name}` → {p.base_path}")
