"""Safe extraction of Roborock local miIO token from Home Assistant configuration or diagnostics."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HEX32_RE = re.compile(r"^[0-9a-fA-F]{32}$")

TOKEN_KEYS = ("token", "miio_token", "device_token", "local_token")
IP_KEYS = ("host", "ip", "ip_address", "local_ip", "localIp")
MODEL_KEYS = ("model", "device_model", "miband_model")


@dataclass(frozen=True)
class ExtractedCredential:
    """Represents a discovered miIO device credential from a Home Assistant file."""

    source: str
    domain: str
    title: str
    model: str | None
    ip: str | None
    token: str

    def masked_token(self) -> str:
        """Return a masked representation of the token for safe logging."""
        if len(self.token) == 32:
            return f"{self.token[:4]}...{self.token[-4:]}"
        return "***"


def _find_in_mapping(mapping: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        val = mapping.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _scan_object(
    obj: Any,
    context_domain: str = "unknown",
    context_title: str = "unknown",
    inherited_ip: str | None = None,
    inherited_model: str | None = None,
    source_path: str = "",
) -> list[ExtractedCredential]:
    """Recursively scan a JSON structure for 32-char hex tokens alongside model/IP fields."""
    found: list[ExtractedCredential] = []

    if isinstance(obj, dict):
        domain = str(obj.get("domain") or context_domain)
        title = str(obj.get("title") or obj.get("name") or context_title)
        ip = _find_in_mapping(obj, IP_KEYS) or inherited_ip
        model = _find_in_mapping(obj, MODEL_KEYS) or inherited_model

        # Check direct token keys in this dict
        for t_key in TOKEN_KEYS:
            raw_val = obj.get(t_key)
            if isinstance(raw_val, str) and HEX32_RE.match(raw_val.strip()):
                found.append(
                    ExtractedCredential(
                        source=source_path,
                        domain=domain,
                        title=title,
                        model=model,
                        ip=ip,
                        token=raw_val.strip(),
                    )
                )

        # Recurse into nested dicts/lists
        for key, value in obj.items():
            if isinstance(value, (dict, list)):
                found.extend(
                    _scan_object(
                        value,
                        context_domain=domain,
                        context_title=title if key not in ("data", "options", "config") else title,
                        inherited_ip=ip,
                        inherited_model=model,
                        source_path=source_path,
                    )
                )

    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                found.extend(
                    _scan_object(
                        item,
                        context_domain=context_domain,
                        context_title=context_title,
                        inherited_ip=inherited_ip,
                        inherited_model=inherited_model,
                        source_path=source_path,
                    )
                )

    return found


def extract_tokens_from_json_data(
    data: Any,
    source_name: str = "memory",
    target_ip: str | None = "192.168.1.110",
    target_model: str = "roborock.vacuum.s6",
) -> list[ExtractedCredential]:
    """Extract and rank miIO credentials from parsed Home Assistant JSON data.

    Supports:
    - Home Assistant `.storage/core.config_entries`
    - Home Assistant downloaded device/config_entry diagnostics JSON (`config_entry-...json`)
    """
    raw_matches = _scan_object(data, source_path=source_name)

    # Deduplicate by (token, ip, model) while preserving the richest record
    unique: dict[str, ExtractedCredential] = {}
    for item in raw_matches:
        existing = unique.get(item.token)
        if existing is None:
            unique[item.token] = item
        else:
            unique[item.token] = ExtractedCredential(
                source=existing.source,
                domain=item.domain if existing.domain == "unknown" else existing.domain,
                title=item.title if existing.title == "unknown" else existing.title,
                model=existing.model or item.model,
                ip=existing.ip or item.ip,
                token=item.token,
            )

    def _score(cred: ExtractedCredential) -> tuple[int, int, int]:
        ip_match = int(bool(target_ip and cred.ip == target_ip))
        model_match = int(
            bool(
                cred.model
                and (
                    target_model.lower() in cred.model.lower()
                    or "roborock.vacuum" in cred.model.lower()
                )
            )
        )
        domain_match = int(cred.domain in ("xiaomi_miot", "xiaomi_miio"))
        return (ip_match, model_match, domain_match)

    return sorted(unique.values(), key=_score, reverse=True)


def extract_tokens_from_path(
    path: str | Path,
    target_ip: str | None = "192.168.1.110",
    target_model: str = "roborock.vacuum.s6",
) -> list[ExtractedCredential]:
    """Load a Home Assistant config/diagnostics file or directory and extract tokens."""
    target = Path(path)
    candidates: list[Path] = []

    if target.is_dir():
        for rel in (
            Path(".storage") / "core.config_entries",
            Path("core.config_entries"),
        ):
            candidate = target / rel
            if candidate.is_file():
                candidates.append(candidate)
        candidates.extend(sorted(target.glob("*xiaomi_miot*.json")))
        candidates.extend(sorted(target.glob("*config_entry*.json")))
    elif target.is_file():
        candidates.append(target)
    else:
        raise FileNotFoundError(f"Path not found: {target}")

    results: list[ExtractedCredential] = []
    seen_tokens: set[str] = set()
    for file_path in candidates:
        try:
            content = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for cred in extract_tokens_from_json_data(
            content,
            source_name=str(file_path),
            target_ip=target_ip,
            target_model=target_model,
        ):
            if cred.token not in seen_tokens:
                seen_tokens.add(cred.token)
                results.append(cred)

    return results
