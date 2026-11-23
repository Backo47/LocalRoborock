"""Binary sensor entities for Local Roborock S6 (Water box / Mop attachment, Cleaning state)."""

from typing import Any

from .entity import LocalRoborockEntity

try:
    from homeassistant.components.binary_sensor import BinarySensorEntity
    from homeassistant.const import EntityCategory
except ImportError:

    class BinarySensorEntity:  # type: ignore[no-redef]
        pass

    class EntityCategory:  # type: ignore[no-redef]
        DIAGNOSTIC = "diagnostic"


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up Local Roborock S6 binary sensors."""
    coordinator = hass.data["local_roborock"][entry.entry_id]
    async_add_entities(
        [
            RoborockMopAttachedBinarySensor(coordinator, entry.entry_id),
            RoborockCleaningActiveBinarySensor(coordinator, entry.entry_id),
        ]
    )


class RoborockMopAttachedBinarySensor(LocalRoborockEntity, BinarySensorEntity):
    """Binary sensor indicating whether the water tank / mop bracket is attached."""

    _attr_name = "Mop Attached"
    _attr_icon = "mdi:water-check"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: Any, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "binary_mop_attached")

    @property
    def is_on(self) -> bool | None:
        return self.data.status.water_box_status


class RoborockCleaningActiveBinarySensor(LocalRoborockEntity, BinarySensorEntity):
    """Binary sensor indicating whether a cleaning cycle is currently in progress."""

    _attr_name = "Cleaning Active"
    _attr_icon = "mdi:robot-vacuum-variant"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: Any, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "binary_cleaning_active")

    @property
    def is_on(self) -> bool:
        return bool(self.data.status.in_cleaning)
