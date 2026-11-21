"""Hybrid map fetcher using existing Xiaomi Cloud session tokens (serviceToken + ssecurity)."""

import base64
import gzip
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from typing import Any


class XiaomiMapFetchError(Exception):
    """Raised when fetching a map URL or binary blob from Xiaomi FDS fails."""


def _rc4_crypt(key: bytes, payload: bytes) -> bytes:
    """Pure-Python RC4 stream cipher with Xiaomi's 1024-byte initial keystream drop."""
    s = list(range(256))
    j = 0
    key_len = len(key)
    for i in range(256):
        j = (j + s[i] + key[i % key_len]) & 0xFF
        s[i], s[j] = s[j], s[i]

    i = 0
    j = 0
    # Drop the first 1024 bytes as required by Xiaomi MIOT-ENCRYPT-ALGORITHM: ENCRYPT-RC4
    for _ in range(1024):
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]

    out = bytearray(len(payload))
    for idx, b in enumerate(payload):
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        k = s[(s[i] + s[j]) & 0xFF]
        out[idx] = b ^ k
    return bytes(out)


def generate_nonce(millis: int | None = None) -> str:
    """Generate a 12-byte Xiaomi API nonce encoded in Base64."""
    now_ms = millis if millis is not None else int(time.time() * 1000)
    nonce_bytes = os.urandom(8) + int(now_ms // 60000).to_bytes(4, byteorder="big")
    return base64.b64encode(nonce_bytes).decode("ascii")


def compute_signed_nonce(ssecurity_b64: str, nonce_b64: str) -> str:
    """Compute SHA-256(base64_decode(ssecurity) + base64_decode(nonce)) in Base64."""
    raw_ssec = base64.b64decode(ssecurity_b64)
    raw_nonce = base64.b64decode(nonce_b64)
    digest = hashlib.sha256(raw_ssec + raw_nonce).digest()
    return base64.b64encode(digest).decode("ascii")


def generate_enc_signature(
    uri_path: str,
    http_method: str,
    signed_nonce_b64: str,
    params: dict[str, str],
) -> str:
    """Generate SHA-1 signature for RC4-encrypted Xiaomi API calls."""
    normalized_path = uri_path.replace("/app/", "/")
    parts = [http_method.upper(), normalized_path]
    for k, v in params.items():
        parts.append(f"{k}={v}")
    parts.append(signed_nonce_b64)
    sig_str = "&".join(parts)
    return base64.b64encode(hashlib.sha1(sig_str.encode("utf-8")).digest()).decode("ascii")


def encrypt_rc4_str(signed_nonce_b64: str, plaintext: str) -> str:
    key = base64.b64decode(signed_nonce_b64)
    encrypted = _rc4_crypt(key, plaintext.encode("utf-8"))
    return base64.b64encode(encrypted).decode("ascii")


def decrypt_rc4_bytes(signed_nonce_b64: str, ciphertext_b64: str | bytes) -> bytes:
    key = base64.b64decode(signed_nonce_b64)
    raw_cipher = base64.b64decode(ciphertext_b64)
    return _rc4_crypt(key, raw_cipher)


@dataclass(frozen=True)
class XiaomiCloudSession:
    """Pre-authenticated Xiaomi Cloud session credentials from Home Assistant config."""

    user_id: str
    service_token: str
    ssecurity: str
    country: str = "de"
    device_id: str = "LocalRoborockClient"

    @property
    def api_base_url(self) -> str:
        prefix = "" if self.country.lower() == "cn" else f"{self.country.lower()}."
        return f"https://{prefix}api.io.mi.com/app"

    def execute_encrypted_post(
        self, endpoint_path: str, data_obj: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute an RC4-encrypted POST request to Xiaomi Cloud API."""
        if not endpoint_path.startswith("/"):
            endpoint_path = "/" + endpoint_path
        url = f"{self.api_base_url}{endpoint_path}"
        uri_for_sig = f"/app{endpoint_path}"

        nonce = generate_nonce()
        s_nonce = compute_signed_nonce(self.ssecurity, nonce)

        params: dict[str, str] = {"data": json.dumps(data_obj, separators=(",", ":"))}
        params["rc4_hash__"] = generate_enc_signature(uri_for_sig, "POST", s_nonce, params)
        for k, v in list(params.items()):
            params[k] = encrypt_rc4_str(s_nonce, v)

        params.update(
            {
                "signature": generate_enc_signature(uri_for_sig, "POST", s_nonce, params),
                "ssecurity": self.ssecurity,
                "_nonce": nonce,
            }
        )

        body = urllib.parse.urlencode(params).encode("utf-8")
        cookie_str = (
            f"userId={self.user_id};"
            f"yetAnotherServiceToken={self.service_token};"
            f"serviceToken={self.service_token};"
            f"locale=en_GB;timezone=GMT+01:00;is_daylight=1;dst_offset=3600000;"
            f"channel=MI_APP_STORE"
        )
        user_agent = (
            "Android-7.1.1-1.0.0-ONEPLUS A3010-136-LocalRoborock "
            "APP/xiaomi.smarthome APPV/62830"
        )
        headers = {
            "Accept-Encoding": "identity",
            "User-Agent": user_agent,
            "Content-Type": "application/x-www-form-urlencoded",
            "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2",
            "MIOT-ENCRYPT-ALGORITHM": "ENCRYPT-RC4",
            "Cookie": cookie_str,
        }

        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                raw_resp = resp.read()
        except Exception as err:
            raise XiaomiMapFetchError(
                f"HTTP error calling Xiaomi API {endpoint_path}: {err}"
            ) from err

        try:
            decrypted = decrypt_rc4_bytes(s_nonce, raw_resp)
            # Some Xiaomi endpoints gzip-compress the inner decrypted JSON
            if decrypted[:2] == b"\x1f\x8b":
                decrypted = gzip.decompress(decrypted)
            elif decrypted[:1] != b"{":
                try:
                    decrypted = zlib.decompress(decrypted)
                except zlib.error:
                    pass
            return json.loads(decrypted.decode("utf-8"))
        except Exception as err:
            raise XiaomiMapFetchError(
                f"Failed to decrypt/parse Xiaomi API response from {endpoint_path}: {err}"
            ) from err

    def get_map_download_url(self, map_name: str) -> str:
        """Resolve a `get_map_v1` pointer into a temporary FDS download URL."""
        clean_name = urllib.parse.unquote(map_name)
        resp = self.execute_encrypted_post("/home/getmapfileurl", {"obj_name": clean_name})
        result = resp.get("result")
        if isinstance(result, dict) and result.get("url"):
            return str(result["url"])
        raise XiaomiMapFetchError(
            f"Xiaomi API did not return a valid map URL for '{clean_name}': "
            f"code={resp.get('code')}, message={resp.get('message')}"
        )

    def download_raw_map(self, map_name: str) -> bytes:
        """Resolve map pointer and download the binary `.rrmap` (GZIP) blob from FDS."""
        download_url = self.get_map_download_url(map_name)
        req = urllib.request.Request(download_url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15.0) as resp:
                return resp.read()
        except Exception as err:
            raise XiaomiMapFetchError(f"Failed to download map blob from FDS: {err}") from err
