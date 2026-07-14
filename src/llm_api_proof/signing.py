from __future__ import annotations

from base64 import urlsafe_b64encode, urlsafe_b64decode
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption

from .models import canonical_json


def _b64encode(raw: bytes) -> str:
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return urlsafe_b64decode(value + padding)


def public_key_fingerprint(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return sha256(raw).hexdigest()


@dataclass
class ReceiptSigner:
    private_key: Ed25519PrivateKey

    @classmethod
    def generate(cls) -> "ReceiptSigner":
        return cls(Ed25519PrivateKey.generate())

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self.private_key.public_key()

    @property
    def public_key_fingerprint(self) -> str:
        return public_key_fingerprint(self.public_key)

    def sign(self, payload: Any) -> str:
        message = canonical_json(payload).encode("utf-8")
        return _b64encode(self.private_key.sign(message))

    def verify(self, payload: Any, signature: str) -> bool:
        message = canonical_json(payload).encode("utf-8")
        try:
            self.public_key.verify(_b64decode(signature), message)
            return True
        except Exception:
            return False

    def export_private_key(self) -> str:
        raw = self.private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        return _b64encode(raw)

    def export_public_key(self) -> str:
        raw = self.public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
        return _b64encode(raw)

    @classmethod
    def import_private_key(cls, encoded: str) -> "ReceiptSigner":
        return cls(Ed25519PrivateKey.from_private_bytes(_b64decode(encoded)))

    @classmethod
    def import_public_key(cls, encoded: str) -> Ed25519PublicKey:
        return Ed25519PublicKey.from_public_bytes(_b64decode(encoded))
