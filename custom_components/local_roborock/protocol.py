"""Low-level Xiaomi miIO binary protocol (0x2131) implementation."""

import hashlib
import json
import struct
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .const import HEADER_SIZE, MIIO_MAGIC


class MiioProtocolError(Exception):
    """Raised when a miIO packet cannot be parsed, verified, or decrypted."""


def validate_token(token_hex: str) -> bytes:
    """Validate a 32-character hex token and convert it to 16 raw bytes."""
    cleaned = token_hex.strip()
    if len(cleaned) != 32:
        raise ValueError(f"Expected 32-character hex token, got length {len(cleaned)}")
    try:
        raw = bytes.fromhex(cleaned)
    except ValueError as err:
        raise ValueError("Token must be a valid hexadecimal string") from err
    if raw in (b"\x00" * 16, b"\xff" * 16):
        raise ValueError("Token cannot be all zeros or all 0xFF")
    return raw


def derive_key_iv(token: bytes) -> tuple[bytes, bytes]:
    """Derive 16-byte AES-128 key and 16-byte IV from a 16-byte device token.

    Key = MD5(token)
    IV  = MD5(Key + token)
    """
    if len(token) != 16:
        raise ValueError("Raw token must be exactly 16 bytes")
    key = hashlib.md5(token).digest()
    iv = hashlib.md5(key + token).digest()
    return key, iv


def encrypt_payload(plaintext: bytes, token: bytes) -> bytes:
    """Encrypt payload using AES-128-CBC with PKCS#7 padding."""
    key, iv = derive_key_iv(token)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def decrypt_payload(ciphertext: bytes, token: bytes) -> bytes:
    """Decrypt payload using AES-128-CBC and strip PKCS#7 padding."""
    if not ciphertext or len(ciphertext) % 16 != 0:
        raise MiioProtocolError(
            f"Invalid ciphertext length {len(ciphertext)} (must be non-empty multiple of 16)"
        )
    key, iv = derive_key_iv(token)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    try:
        return unpadder.update(padded) + unpadder.finalize()
    except ValueError as err:
        raise MiioProtocolError(
            "PKCS#7 unpadding failed (likely incorrect miIO token)"
        ) from err


@dataclass(frozen=True)
class MiioPacket:
    """Represents a decoded miIO UDP packet."""

    device_id: int
    stamp: int
    checksum: bytes
    payload: dict[str, Any] | None

    @property
    def is_hello(self) -> bool:
        return self.payload is None


def build_hello_packet() -> bytes:
    """Build the 32-byte miIO Hello handshake packet."""
    return struct.pack(
        ">HHIII16s",
        MIIO_MAGIC,
        HEADER_SIZE,
        0xFFFFFFFF,
        0xFFFFFFFF,
        0xFFFFFFFF,
        b"\xff" * 16,
    )


def build_packet(
    device_id: int,
    stamp: int,
    token: bytes,
    payload: dict[str, Any],
) -> bytes:
    """Encode, encrypt, and sign a JSON-RPC request packet."""
    raw_json = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\x00"
    encrypted = encrypt_payload(raw_json, token)
    total_len = HEADER_SIZE + len(encrypted)
    if total_len > 0xFFFF:
        raise MiioProtocolError(f"Packet length {total_len} exceeds 65535-byte UDP limit")
    header_prefix = struct.pack(
        ">HHIII",
        MIIO_MAGIC,
        total_len,
        0,
        device_id & 0xFFFFFFFF,
        stamp & 0xFFFFFFFF,
    )
    checksum = hashlib.md5(header_prefix + token + encrypted).digest()
    return header_prefix + checksum + encrypted


def parse_packet(data: bytes, token: bytes | None = None) -> MiioPacket:
    """Parse a raw miIO UDP datagram and optionally verify/decrypt the payload."""
    if len(data) < HEADER_SIZE:
        raise MiioProtocolError(f"Packet too short: {len(data)} bytes (min {HEADER_SIZE})")

    magic, length, _unknown, device_id, stamp, checksum = struct.unpack(
        ">HHIII16s", data[:HEADER_SIZE]
    )
    if magic != MIIO_MAGIC:
        raise MiioProtocolError(f"Invalid miIO magic bytes: 0x{magic:04x}")
    if length != len(data):
        raise MiioProtocolError(f"Header length {length} does not match datagram size {len(data)}")

    if length == HEADER_SIZE:
        return MiioPacket(
            device_id=device_id,
            stamp=stamp,
            checksum=checksum,
            payload=None,
        )

    if token is None:
        raise MiioProtocolError("Token is required to decrypt a non-empty miIO packet")

    encrypted = data[HEADER_SIZE:]
    expected_md5 = hashlib.md5(data[:16] + token + encrypted).digest()
    if expected_md5 != checksum:
        raise MiioProtocolError(
            "MD5 checksum mismatch on incoming packet (verify that the miIO token is correct)"
        )

    decrypted = decrypt_payload(encrypted, token).rstrip(b"\x00")
    try:
        decoded_json = json.loads(decrypted.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise MiioProtocolError(f"Failed to decode JSON payload: {err}") from err

    return MiioPacket(
        device_id=device_id,
        stamp=stamp,
        checksum=checksum,
        payload=decoded_json,
    )
