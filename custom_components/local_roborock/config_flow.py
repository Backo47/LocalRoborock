"""Config flow for Local Roborock S6 integration."""

from typing import Any

import voluptuous as vol

from .client import RoborockCommandError, RoborockCommunicationError, RoborockLocalClient
from .const import (
    CONF_HOST,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_XIAOMI_COUNTRY,
    CONF_XIAOMI_SERVICE_TOKEN,
    CONF_XIAOMI_SSECURITY,
    CONF_XIAOMI_USER_ID,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .protocol import MiioProtocolError, validate_token

try:
    from homeassistant import config_entries
    from homeassistant.data_entry_flow import FlowResult
except ImportError:

    class _StubConfigEntries:
        class ConfigFlow:
            def __init_subclass__(cls, **kwargs: Any) -> None:
                super().__init_subclass__()

    config_entries = _StubConfigEntries()  # type: ignore[assignment]
    FlowResult = dict[str, Any]  # type: ignore[misc]


async def async_validate_connection(host: str, token: str) -> dict[str, str]:
    """Validate local UDP connection to Roborock S6 and return metadata."""
    validate_token(token)
    client = RoborockLocalClient(ip=host.strip(), token=token.strip(), timeout=5.0, retries=2)
    hello = await client.handshake()
    info = await client.get_device_info()
    return {
        "title": f"{DEFAULT_NAME} ({host.strip()})",
        "unique_id": f"{hello.device_id}",
        "model": info.model,
        "fw_ver": info.fw_ver,
    }


class LocalRoborockConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Local Roborock S6."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial setup step in Home Assistant UI."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = str(user_input.get(CONF_HOST, "")).strip()
            token = str(user_input.get(CONF_TOKEN, "")).strip()
            try:
                meta = await async_validate_connection(host, token)
            except ValueError:
                errors["base"] = "invalid_token"
            except (RoborockCommunicationError, MiioProtocolError, RoborockCommandError):
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"
            else:
                if hasattr(self, "async_set_unique_id"):
                    await self.async_set_unique_id(meta["unique_id"])
                    self._abort_if_unique_id_configured()
                return self.async_create_entry(title=meta["title"], data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default="192.168.1.110"): str,
                vol.Required(CONF_TOKEN): str,
                vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): int,
                vol.Optional(CONF_XIAOMI_COUNTRY, default="de"): str,
                vol.Optional(CONF_XIAOMI_USER_ID, default=""): str,
                vol.Optional(CONF_XIAOMI_SERVICE_TOKEN, default=""): str,
                vol.Optional(CONF_XIAOMI_SSECURITY, default=""): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
