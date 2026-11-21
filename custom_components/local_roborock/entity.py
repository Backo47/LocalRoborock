"""Base entity and device info helper for Local Roborock S6."""

from typing import Any

from .const import DEFAULT_NAME, DOMAIN
from .coordinator import RoborockCoordinatorData

try:
    from homeassistant.helpers.device_registry import DeviceInfo as HADeviceInfo
    from homeassistant.helpers.update_coordinator import CoordinatorEntity
except ImportError:

    class CoordinatorEntity:  # type: ignore[no-redef]
        """Fallback CoordinatorEntity stub when running unit tests outside Home Assistant."""

        def __init__(self, coordinator: Any) -> None:
            self.coordinator = coordinator

        async def async_added_to_hass(self) -> None:
            return None

    HADeviceInfo = dict  # type: ignore[misc,assignment]


class LocalRoborockEntity(CoordinatorEntity):
    """Common base class for all Local Roborock S6 Home Assistant entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: Any, entry_id: str, key: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._entity_key = key
        self._attr_unique_id = f"{entry_id}_{key}"

    @property
    def data(self) -> RoborockCoordinatorData:
        return self.coordinator.data

    @property
    def device_info(self) -> Any:
        info = self.data.device_info
        serial = self.data.serial_number or self._entry_id
        return HADeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=DEFAULT_NAME,
            manufacturer="Roborock",
            model=info.model,
            sw_version=info.fw_ver,
            hw_version=info.hw_ver,
        )
