from __future__ import annotations

import base64
import hashlib
import hmac
import io
import os
import struct
import time
from dataclasses import dataclass
from urllib.parse import quote

import qrcode
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import Settings, get_settings


_password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def validate_password_strength(password: str) -> None:
    if len(password) < 12:
        raise ValueError("รหัสผ่านต้องมีอย่างน้อย 12 ตัวอักษร")
    checks = (
        any(c.islower() for c in password),
        any(c.isupper() for c in password),
        any(c.isdigit() for c in password),
        any(not c.isalnum() for c in password),
    )
    if not all(checks):
        raise ValueError("รหัสผ่านต้องมีตัวพิมพ์เล็ก ตัวพิมพ์ใหญ่ ตัวเลข และอักขระพิเศษ")


def _fernet(settings: Settings | None = None) -> Fernet:
    settings = settings or get_settings()
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str, settings: Settings | None = None) -> str:
    return _fernet(settings).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str, settings: Settings | None = None) -> str:
    try:
        return _fernet(settings).decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("ไม่สามารถถอดรหัสข้อมูลลับได้ ตรวจสอบ SECRET_KEY") from exc


def generate_totp_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode("ascii").rstrip("=")


def _decode_base32(secret: str) -> bytes:
    padded = secret.upper() + "=" * ((8 - len(secret) % 8) % 8)
    return base64.b32decode(padded, casefold=True)


def totp_code(secret: str, timestamp: int | None = None, step_seconds: int = 30, digits: int = 6) -> str:
    timestamp = timestamp if timestamp is not None else int(time.time())
    counter = timestamp // step_seconds
    digest = hmac.new(_decode_base32(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**digits)).zfill(digits)


def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    normalized = "".join(ch for ch in code if ch.isdigit())
    if len(normalized) != 6:
        return False
    now = int(time.time())
    for drift in range(-window, window + 1):
        expected = totp_code(secret, now + drift * 30)
        if hmac.compare_digest(expected, normalized):
            return True
    return False


def provisioning_uri(secret: str, account_name: str, issuer: str = "NT Drone") -> str:
    label = quote(f"{issuer}:{account_name}")
    return f"otpauth://totp/{label}?secret={quote(secret)}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"


def qr_data_uri(content: str) -> str:
    image = qrcode.make(content)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


@dataclass(slots=True)
class SignedTokenService:
    settings: Settings

    def _serializer(self, salt: str) -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(self.settings.secret_key, salt=salt)

    def create_session(self, user_id: str, role: str) -> str:
        return self._serializer("session").dumps({"sub": user_id, "role": role, "purpose": "session"})

    def read_session(self, token: str) -> dict[str, str] | None:
        try:
            data = self._serializer("session").loads(token, max_age=self.settings.session_ttl_seconds)
        except (BadSignature, SignatureExpired):
            return None
        if data.get("purpose") != "session":
            return None
        return data

    def create_challenge(self, user_id: str) -> str:
        return self._serializer("challenge").dumps({"sub": user_id, "purpose": "2fa"})

    def read_challenge(self, token: str) -> dict[str, str] | None:
        try:
            data = self._serializer("challenge").loads(token, max_age=self.settings.challenge_ttl_seconds)
        except (BadSignature, SignatureExpired):
            return None
        if data.get("purpose") != "2fa":
            return None
        return data


def get_token_service(settings: Settings | None = None) -> SignedTokenService:
    return SignedTokenService(settings or get_settings())
