"""inspector/pgp_inspect.py — read the MIGRATION-RELEVANT metadata out of an
OpenPGP keyring (public or secret): key IDs, v4 fingerprints, algorithms,
user IDs, creation dates, subkeys. Secret key material is never decoded —
for secret-key packets only the public fields (which precede the secret
fields in the packet) are read, enough for fingerprint + key ID.

This is a deliberate ~150-line OpenPGP *packet header* parser (RFC 4880),
not a crypto library: the workbench needs to DOCUMENT keyrings for the
re-key worksheet ('secret keyring contains key 0x28BE8244B6BD02E1,
CPI Test Own Key <own@...>, RSA-3072, expires …'), not use them.
"""
from __future__ import annotations

import base64
import hashlib
import re
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone

_ALGO = {1: "RSA", 2: "RSA-encrypt", 3: "RSA-sign", 16: "ElGamal",
         17: "DSA", 18: "ECDH", 19: "ECDSA", 22: "EdDSA"}

# packet tags we care about
_TAG_SECRET_KEY = 5
_TAG_PUBLIC_KEY = 6
_TAG_SECRET_SUBKEY = 7
_TAG_USER_ID = 13
_TAG_PUBLIC_SUBKEY = 14


@dataclass
class KeyInfo:
    kind: str                  # public | secret
    primary: bool
    algorithm: str
    bits: int
    key_id: str                # last 8 bytes of the fingerprint, hex upper
    fingerprint: str
    created: str
    user_ids: list = field(default_factory=list)


def _dearmor(data: bytes) -> bytes:
    m = re.search(rb"-----BEGIN PGP [^-]+-----(.*?)-----END PGP",
                  data, re.S)
    if not m:
        return data                        # already binary
    body = m.group(1)
    # drop armor headers (up to the first blank line) and the CRC line
    body = re.sub(rb"^.*?\r?\n\r?\n", b"", body, count=1, flags=re.S)
    lines = [ln for ln in body.splitlines()
             if ln.strip() and not ln.strip().startswith(b"=")]
    return base64.b64decode(b"".join(lines))


def _packets(raw: bytes):
    """Yield (tag, body) for each OpenPGP packet (old + new format
    headers, partial lengths unsupported — keyrings don't use them)."""
    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        if not c & 0x80:
            return
        if c & 0x40:                                   # new format
            tag = c & 0x3F
            i += 1
            l1 = raw[i]
            if l1 < 192:
                ln, i = l1, i + 1
            elif l1 < 224:
                ln = ((l1 - 192) << 8) + raw[i + 1] + 192
                i += 2
            elif l1 == 255:
                ln = struct.unpack(">I", raw[i + 1:i + 5])[0]
                i += 5
            else:
                return                                  # partial — bail
        else:                                          # old format
            tag = (c >> 2) & 0x0F
            lt = c & 0x03
            i += 1
            if lt == 0:
                ln, i = raw[i], i + 1
            elif lt == 1:
                ln = struct.unpack(">H", raw[i:i + 2])[0]
                i += 2
            elif lt == 2:
                ln = struct.unpack(">I", raw[i:i + 4])[0]
                i += 4
            else:
                return                                  # indeterminate
        yield tag, raw[i:i + ln]
        i += ln


def _mpi_bits(body: bytes, off: int) -> tuple:
    bits = struct.unpack(">H", body[off:off + 2])[0]
    return bits, off + 2 + (bits + 7) // 8


def _key_from_packet(tag: int, body: bytes) -> KeyInfo | None:
    if len(body) < 8 or body[0] != 4:                  # v4 keys only
        return None
    created = datetime.fromtimestamp(
        struct.unpack(">I", body[1:5])[0],
        tz=timezone.utc).strftime("%Y-%m-%d")
    algo = body[5]
    bits, _ = _mpi_bits(body, 6)
    # v4 fingerprint = SHA1 over 0x99 || 2-byte length || PUBLIC portion.
    # For SECRET keys the public portion is the prefix of the packet; we
    # hash only up to the end of the public MPIs (RSA: n + e) — secret
    # MPIs are never touched.
    pub_end = len(body)
    if tag in (_TAG_SECRET_KEY, _TAG_SECRET_SUBKEY):
        off = 6
        nmpi = 2 if algo in (1, 2, 3) else (3 if algo == 17 else 2)
        try:
            for _ in range(nmpi):
                _, off = _mpi_bits(body, off)
            pub_end = off
        except Exception:
            return None
    pub = body[:pub_end]
    fpr = hashlib.sha1(b"\x99" + struct.pack(">H", len(pub))
                       + pub).hexdigest().upper()
    return KeyInfo(
        kind="secret" if tag in (_TAG_SECRET_KEY, _TAG_SECRET_SUBKEY)
        else "public",
        primary=tag in (_TAG_SECRET_KEY, _TAG_PUBLIC_KEY),
        algorithm=_ALGO.get(algo, f"algo-{algo}"),
        bits=bits, key_id=fpr[-16:], fingerprint=fpr, created=created)


def inspect_keyring(data: "bytes | str") -> dict:
    """Returns {'kind': 'public'|'secret'|'mixed', 'keys': [KeyInfo …],
    'warnings': […]} — everything the re-key worksheet needs, nothing the
    security team would object to."""
    if isinstance(data, str):
        data = data.encode("utf-8", "replace")
    raw = _dearmor(data)
    keys, warnings = [], []
    current = None
    for tag, body in _packets(raw):
        if tag in (_TAG_PUBLIC_KEY, _TAG_SECRET_KEY,
                   _TAG_PUBLIC_SUBKEY, _TAG_SECRET_SUBKEY):
            ki = _key_from_packet(tag, body)
            if ki:
                keys.append(ki)
                if ki.primary:
                    current = ki
            elif body[:1] != b"\x04":
                warnings.append(
                    "non-v4 key packet found — CPI's IAIK library may "
                    "reject v3/v5 keys")
        elif tag == _TAG_USER_ID and current is not None:
            current.user_ids.append(body.decode("utf-8", "replace"))
    for k in keys:
        if k.algorithm in ("ECDH", "ECDSA", "EdDSA"):
            warnings.append(
                f"key {k.key_id} uses {k.algorithm} — CPI/IAIK has been "
                "seen rejecting modern ECC keys; RSA is the safe choice")
    kinds = {k.kind for k in keys}
    return {"kind": ("mixed" if len(kinds) > 1
                     else (kinds.pop() if kinds else "unknown")),
            "keys": keys, "warnings": warnings}
