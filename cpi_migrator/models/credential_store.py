"""
models/credential_store.py

Local AES-256 encrypted credential profiles.
Profiles stored at ~/.cpi_migrator/profiles/<name>.profile
Each file is encrypted with a master password using Fernet (AES-128-CBC + HMAC).

Never stores credentials in plain text on disk.
Nothing is sent to any external server.

Usage:
    store = CredentialStore()
    store.save_profile("ACME_Migration", profile_data, master_password="mypassword")
    profile = store.load_profile("ACME_Migration", master_password="mypassword")
    names = store.list_profiles()
"""
from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

import os
# Profiles live in the user's home by default (OUTSIDE the project folder, so
# deleting/replacing the project never touches them). Override with the
# CPI_MIGRATOR_HOME environment variable if you want them elsewhere (e.g. a
# synced/backed-up location).
_BASE_DIR = Path(os.environ.get("CPI_MIGRATOR_HOME",
                                str(Path.home() / ".cpi_migrator")))
PROFILES_DIR = _BASE_DIR / "profiles"


# ---------------------------------------------------------------------------
# Profile data model
# ---------------------------------------------------------------------------

@dataclass
class TargetCredential:
    target_id: str                   # s4hana_cloud, ariba, etc.
    label: str                       # human label
    auth_method: str = "OAuth2"      # OAuth2 / Basic / APIKey / Certificate
    base_url: str = ""
    token_url: str = ""
    client_id: str = ""
    client_secret: str = ""
    username: str = ""
    password: str = ""
    api_key: str = ""
    certificate_alias: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class CPIProfile:
    """Complete credential profile for one client / project."""

    # Identity
    name: str = ""
    company_code: str = ""
    description: str = ""

    # CPI Tenant
    cpi_environment: str = "cf"      # cf / neo
    cpi_base_url: str = ""
    cpi_token_url: str = ""
    cpi_client_id: str = ""
    cpi_client_secret: str = ""
    # Neo
    cpi_username: str = ""
    cpi_password: str = ""

    # PI/PO Source
    pi_base_url: str = ""
    pi_username: str = ""
    pi_password: str = ""
    pi_export_file: str = ""

    # Cloud Connector
    scc_location_id: str = ""
    scc_virtual_host: str = ""
    scc_virtual_port: int = 443

    # SAP Hub
    hub_api_key: str = ""

    # GitHub (optional — for rate limit)
    github_token: str = ""

    # Target systems (list of TargetCredential)
    targets: list[dict] = field(default_factory=list)

    # cTMS
    ctms_url: str = ""
    ctms_client_id: str = ""
    ctms_client_secret: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CPIProfile":
        targets = d.pop("targets", [])
        profile = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        profile.targets = targets
        return profile

    def get_target(self, target_id: str) -> Optional[TargetCredential]:
        for t in self.targets:
            if isinstance(t, dict) and t.get("target_id") == target_id:
                return TargetCredential(**t)
            if isinstance(t, TargetCredential) and t.target_id == target_id:
                return t
        return None


# ---------------------------------------------------------------------------
# Encryption helpers
# ---------------------------------------------------------------------------

def _derive_key(password: str, salt: bytes) -> bytes:
    """Derive AES key from password using PBKDF2."""
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def encrypt_profile(data: dict, password: str) -> bytes:
    """Encrypt profile dict to bytes using Fernet (AES-128-CBC + HMAC-SHA256)."""
    from cryptography.fernet import Fernet
    salt    = os.urandom(16)
    key     = _derive_key(password, salt)
    f       = Fernet(key)
    payload = json.dumps(data, indent=2).encode("utf-8")
    token   = f.encrypt(payload)
    # Prepend salt so we can derive the same key on load
    return salt + token


def decrypt_profile(encrypted: bytes, password: str) -> dict:
    """Decrypt profile bytes back to dict. Raises ValueError on wrong password."""
    from cryptography.fernet import Fernet, InvalidToken
    salt    = encrypted[:16]
    token   = encrypted[16:]
    key     = _derive_key(password, salt)
    f       = Fernet(key)
    try:
        payload = f.decrypt(token)
        return json.loads(payload.decode("utf-8"))
    except (InvalidToken, Exception) as exc:
        raise ValueError("Wrong master password or corrupted profile file.") from exc


# ---------------------------------------------------------------------------
# Credential store
# ---------------------------------------------------------------------------

class CredentialStore:
    """
    Manages encrypted credential profiles on local disk.
    All files stored at ~/.cpi_migrator/profiles/<name>.profile
    """

    def __init__(self, profiles_dir: Optional[Path] = None):
        self.profiles_dir = Path(profiles_dir) if profiles_dir else PROFILES_DIR
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

    # ── CRUD ──────────────────────────────────────────────────────────

    def save_profile(
        self,
        profile: CPIProfile,
        master_password: str,
    ) -> Path:
        """Encrypt and save a profile. Returns the file path."""
        if not profile.name:
            raise ValueError("Profile name cannot be empty")
        if not master_password:
            raise ValueError("Master password cannot be empty")

        safe_name = _safe_filename(profile.name)
        path      = self.profiles_dir / f"{safe_name}.profile"

        encrypted = encrypt_profile(profile.to_dict(), master_password)
        path.write_bytes(encrypted)
        logger.info("Profile '%s' saved → %s", profile.name, path)
        return path

    def load_profile(
        self,
        profile_name: str,
        master_password: str,
    ) -> CPIProfile:
        """Load and decrypt a profile. Raises ValueError on wrong password."""
        path = self._profile_path(profile_name)
        if not path.exists():
            raise FileNotFoundError(f"Profile '{profile_name}' not found")

        encrypted = path.read_bytes()
        data      = decrypt_profile(encrypted, master_password)
        return CPIProfile.from_dict(data)

    def delete_profile(self, profile_name: str) -> bool:
        path = self._profile_path(profile_name)
        if path.exists():
            path.unlink()
            logger.info("Profile '%s' deleted", profile_name)
            return True
        return False

    def list_profiles(self) -> list[str]:
        """Return list of saved profile names (not decrypted)."""
        return [
            p.stem.replace("_", " ")
            for p in sorted(self.profiles_dir.glob("*.profile"))
        ]

    def profile_exists(self, profile_name: str) -> bool:
        return self._profile_path(profile_name).exists()

    def rename_profile(
        self,
        old_name: str,
        new_name: str,
        master_password: str,
    ) -> bool:
        """Rename by load → resave under new name → delete old."""
        profile      = self.load_profile(old_name, master_password)
        profile.name = new_name
        self.save_profile(profile, master_password)
        self.delete_profile(old_name)
        return True

    # ── Test connections ─────────────────────────────────────────────

    def test_connections(self, profile: CPIProfile) -> dict[str, str]:
        """
        Ping each configured system and return status dict.
        Returns {system_name: "✓ Connected" | "✗ <error>"}
        """
        import requests
        results = {}

        # Test CPI
        if profile.cpi_base_url:
            try:
                if profile.cpi_environment == "cf":
                    from auth.authenticator import CFAuthenticator
                    auth = CFAuthenticator(
                        profile.cpi_token_url,
                        profile.cpi_client_id,
                        profile.cpi_client_secret,
                    )
                else:
                    from auth.authenticator import NeoAuthenticator
                    auth = NeoAuthenticator(profile.cpi_username, profile.cpi_password)

                sess = auth.get_session()
                r    = sess.get(
                    f"{profile.cpi_base_url}/api/v1/IntegrationPackages",
                    params={"$top": 1, "$format": "json"},
                    timeout=10,
                )
                results["CPI Tenant"] = "✓ Connected" if r.status_code in (200, 401) \
                    else f"✗ HTTP {r.status_code}"
            except Exception as e:
                results["CPI Tenant"] = f"✗ {str(e)[:60]}"

        # Test PI/PO
        if profile.pi_base_url and not profile.pi_export_file:
            try:
                r = requests.get(
                    f"{profile.pi_base_url}/CommunicationChannel/CommunicationChannel",
                    auth=(profile.pi_username, profile.pi_password),
                    params={"$top": 1, "$format": "json"},
                    timeout=10,
                )
                results["PI/PO"] = "✓ Connected" if r.status_code in (200, 401) \
                    else f"✗ HTTP {r.status_code}"
            except Exception as e:
                results["PI/PO"] = f"✗ {str(e)[:60]}"
        elif profile.pi_export_file:
            from pathlib import Path as _Path
            results["PI/PO"] = "✓ Export file configured" \
                if _Path(profile.pi_export_file).exists() \
                else "✗ Export file not found"

        # Test Hub API key
        if profile.hub_api_key:
            try:
                r = requests.get(
                    "https://api.sap.com/odata/1.0/catalog.svc/Packages?$top=1&$format=json",
                    headers={"APIKey": profile.hub_api_key},
                    timeout=10,
                )
                results["SAP Hub"] = "✓ API key valid" if r.status_code == 200 \
                    else f"✗ HTTP {r.status_code}"
            except Exception as e:
                results["SAP Hub"] = f"✗ {str(e)[:60]}"

        return results

    # ── Helpers ───────────────────────────────────────────────────────

    def _profile_path(self, name: str) -> Path:
        return self.profiles_dir / f"{_safe_filename(name)}.profile"


def _safe_filename(name: str) -> str:
    import re
    return re.sub(r"[^\w\-]", "_", name.strip())[:60]
