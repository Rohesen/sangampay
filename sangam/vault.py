"""Envelope encryption for secrets (bank details, gateway credentials, webhook secrets).

Each record gets its own random data key (AES-256-GCM). That data key is wrapped with the
master key, also AES-256-GCM. The record type + owner is bound in as associated data, so a
ciphertext copied into another merchant's row will not decrypt.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import settings
from .util import APIError


class VaultError(Exception):
    pass


def _kid(key: bytes) -> str:
    return hashlib.sha256(b"sangam-kid" + key).hexdigest()[:12]


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _unb64(s: str) -> bytes:
    return base64.b64decode(s)


def encrypt(data: dict, context: str) -> str:
    kek = settings.master_keys[0]
    dek = AESGCM.generate_key(bit_length=256)
    aad = context.encode()
    nonce, wrap_nonce = os.urandom(12), os.urandom(12)
    ciphertext = AESGCM(dek).encrypt(nonce, json.dumps(data, separators=(",", ":")).encode(), aad)
    wrapped = AESGCM(kek).encrypt(wrap_nonce, dek, aad)
    return json.dumps({"v": 1, "kid": _kid(kek), "wn": _b64(wrap_nonce), "wk": _b64(wrapped),
                       "n": _b64(nonce), "ct": _b64(ciphertext)})


def decrypt(blob: str, context: str) -> dict:
    env = json.loads(blob)
    kek = next((k for k in settings.master_keys if _kid(k) == env["kid"]), None)
    if kek is None:
        raise VaultError("Record was encrypted with a master key that is not configured")
    aad = context.encode()
    try:
        dek = AESGCM(kek).decrypt(_unb64(env["wn"]), _unb64(env["wk"]), aad)
        return json.loads(AESGCM(dek).decrypt(_unb64(env["n"]), _unb64(env["ct"]), aad))
    except InvalidTag as exc:
        raise VaultError("Ciphertext failed authentication") from exc


def needs_rewrap(blob: str) -> bool:
    return json.loads(blob)["kid"] != _kid(settings.master_keys[0])


def fingerprint(value: str, context: str) -> str:
    """Keyed hash used to de-duplicate without storing the plaintext."""
    key = hmac.new(settings.master_keys[0], b"sangam-fingerprint", hashlib.sha256).digest()
    return hmac.new(key, f"{context}:{value}".encode(), hashlib.sha256).hexdigest()


def mask(value: str, visible: int = 4) -> str:
    return "X" * max(len(value) - visible, 0) + value[-visible:]


# ---- Bank detail validation -------------------------------------------------------------

_IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_VPA = re.compile(r"^[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z][a-zA-Z0-9]{1,63}$")


def _iban_ok(iban: str) -> bool:
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", iban):
        return False
    rearranged = iban[4:] + iban[:4]
    return int("".join(str(int(c, 36)) for c in rearranged)) % 97 == 1


def _aba_ok(routing: str) -> bool:
    if not re.fullmatch(r"\d{9}", routing):
        return False
    d = [int(c) for c in routing]
    return (3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])) % 10 == 0


def normalize_bank_details(kind: str, details: dict) -> tuple[dict, dict, str]:
    """Validate and return (secret_payload, masked_view, fingerprint_source)."""
    def field(name: str) -> str:
        value = str(details.get(name) or "").strip()
        if not value:
            raise APIError(400, "missing_field", f"'{name}' is required for {kind}", name)
        return value

    holder = field("account_holder_name")
    if kind == "in_bank":
        number = re.sub(r"\s", "", field("account_number"))
        ifsc = field("ifsc").upper()
        if not re.fullmatch(r"\d{9,18}", number):
            raise APIError(400, "invalid_account_number", "Indian account numbers are 9-18 digits", "account_number")
        if not _IFSC.match(ifsc):
            raise APIError(400, "invalid_ifsc", "IFSC must look like HDFC0001234", "ifsc")
        return ({"account_holder_name": holder, "account_number": number, "ifsc": ifsc},
                {"account_holder_name": holder, "account_number": mask(number), "ifsc": ifsc, "bank_code": ifsc[:4]},
                f"{ifsc}:{number}")
    if kind == "iban":
        iban = re.sub(r"\s", "", field("iban")).upper()
        if not _iban_ok(iban):
            raise APIError(400, "invalid_iban", "IBAN checksum failed", "iban")
        bic = str(details.get("bic") or "").strip().upper() or None
        return ({"account_holder_name": holder, "iban": iban, "bic": bic},
                {"account_holder_name": holder, "iban": iban[:4] + mask(iban[4:]), "bic": bic, "country": iban[:2]},
                iban)
    if kind == "us_ach":
        number = re.sub(r"\s", "", field("account_number"))
        routing = field("routing_number")
        if not re.fullmatch(r"\d{4,17}", number):
            raise APIError(400, "invalid_account_number", "US account numbers are 4-17 digits", "account_number")
        if not _aba_ok(routing):
            raise APIError(400, "invalid_routing_number", "ABA routing checksum failed", "routing_number")
        return ({"account_holder_name": holder, "account_number": number, "routing_number": routing},
                {"account_holder_name": holder, "account_number": mask(number), "routing_number": routing},
                f"{routing}:{number}")
    if kind == "upi_vpa":
        vpa = field("vpa").lower()
        if not _VPA.match(vpa):
            raise APIError(400, "invalid_vpa", "UPI ID must look like name@bank", "vpa")
        user, handle = vpa.split("@", 1)
        return ({"account_holder_name": holder, "vpa": vpa},
                {"account_holder_name": holder, "vpa": f"{user[:2]}{'X' * max(len(user) - 2, 0)}@{handle}"},
                vpa)
    raise APIError(400, "invalid_kind", "kind must be one of in_bank, iban, us_ach, upi_vpa", "kind")


def is_valid_vpa(vpa: str) -> bool:
    return bool(_VPA.match(vpa))
