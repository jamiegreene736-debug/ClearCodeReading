"""Token storage. Uses the same Fernet keys as CRM email."""

from __future__ import annotations

import hashlib
import json

from apps.crm_email.security import EmailError, decrypt, encrypt
from apps.social.exceptions import SocialError


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def encrypt_text(value: str) -> str:
    try:
        return encrypt(value.encode()).decode()
    except EmailError as exc:
        raise SocialError("Social connection encryption is not configured.") from exc


def decrypt_text(value: str) -> str:
    try:
        return decrypt(value.encode()).decode()
    except EmailError as exc:
        raise SocialError("This social connection cannot be decrypted. Sign in again.") from exc


def encrypt_json(value: object) -> str:
    return encrypt_text(json.dumps(value))


def decrypt_json(value: str) -> object:
    return json.loads(decrypt_text(value))


def encryption_ready() -> bool:
    try:
        encrypt_text("ok")
    except SocialError:
        return False
    return True
