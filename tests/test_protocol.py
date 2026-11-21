"""Unit tests for the Xiaomi miIO binary protocol implementation."""

import struct

import pytest

from custom_components.local_roborock.const import HEADER_SIZE, MIIO_MAGIC
from custom_components.local_roborock.protocol import (
    MiioProtocolError,
    build_hello_packet,
    build_packet,
    decrypt_payload,
    derive_key_iv,
    encrypt_payload,
    parse_packet,
    validate_token,
)

SAMPLE_TOKEN_HEX = "4b657931323334353637383930616263"


def test_validate_token_valid() -> None:
    raw = validate_token(SAMPLE_TOKEN_HEX)
    assert len(raw) == 16
    assert raw == b"Key1234567890abc"


@pytest.mark.parametrize(
    "bad_token",
    [
        "1234",
        "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
        "0" * 32,
        "f" * 32,
    ],
)
def test_validate_token_invalid(bad_token: str) -> None:
    with pytest.raises(ValueError):
        validate_token(bad_token)


def test_encrypt_decrypt_roundtrip() -> None:
    token = validate_token(SAMPLE_TOKEN_HEX)
    key, iv = derive_key_iv(token)
    assert len(key) == 16
    assert len(iv) == 16

    plaintext = b'{"id":1,"method":"get_status","params":[]}\x00'
    encrypted = encrypt_payload(plaintext, token)
    assert len(encrypted) % 16 == 0
    decrypted = decrypt_payload(encrypted, token)
    assert decrypted == plaintext


def test_hello_packet_structure() -> None:
    hello = build_hello_packet()
    assert len(hello) == HEADER_SIZE
    magic, length, unk, did, stamp = struct.unpack(">HHIII", hello[:16])
    assert magic == MIIO_MAGIC
    assert length == HEADER_SIZE
    assert unk == 0xFFFFFFFF
    assert did == 0xFFFFFFFF
    assert stamp == 0xFFFFFFFF

    parsed = parse_packet(hello)
    assert parsed.is_hello
    assert parsed.payload is None


def test_build_and_parse_packet_roundtrip() -> None:
    token = validate_token(SAMPLE_TOKEN_HEX)
    payload = {"id": 42, "method": "get_status", "params": []}
    raw_pkt = build_packet(device_id=0x12345678, stamp=1500, token=token, payload=payload)

    parsed = parse_packet(raw_pkt, token=token)
    assert not parsed.is_hello
    assert parsed.device_id == 0x12345678
    assert parsed.stamp == 1500
    assert parsed.payload == payload


def test_parse_packet_wrong_token_fails() -> None:
    token_a = validate_token(SAMPLE_TOKEN_HEX)
    token_b = validate_token("11223344556677889900aabbccddeeff")
    raw_pkt = build_packet(
        device_id=0x12345678,
        stamp=100,
        token=token_a,
        payload={"id": 1, "method": "miIO.info", "params": []},
    )
    with pytest.raises(MiioProtocolError, match="MD5 checksum mismatch"):
        parse_packet(raw_pkt, token=token_b)
