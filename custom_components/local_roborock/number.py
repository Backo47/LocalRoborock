"""Number entity for Local Roborock S6 voice prompt volume (0..100%)."""

from typing import Any

from .entity import LocalRoborockEntity

try:
    from homeassistant.components.number import NumberEntity
    from homeassistant.const import PERCENTAGE, EntityCategory
except ImportError:

    class NumberEntity:  # type: ignore[no-redef]
        pass

    class EntityCategory:  # type: ignore[no-redef]
        CONFIG = "config"

    PERCENTAGE = "%"


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up the voice volume number entity."""
    coordinator = hass.data["local_roborock"][entry.entry_id]
    async_add_entities([RoborockSoundVolumeNumber(coordinator, entry.entry_id)])


class RoborockSoundVolumeNumber(LocalRoborockEntity, NumberEntity):
    """Slider to control voice prompt sound volume (0..100%)."""

    _attr_name = "Voice Volume"
    _attr_icon = "mdi:volume-high"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 5.0
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: Any, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "number_sound_volume")

    @property
    def native_value(self) -> float:
        return float(self.data.sound_volume)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.client.set_sound_volume(int(round(value)))
        await self.coordinator.async_request_refresh()
