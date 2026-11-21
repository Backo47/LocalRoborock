"""Switch entities for Local Roborock S6 (Carpet Boost, Status LED, Do Not Disturb)."""

from typing import Any

from .entity import LocalRoborockEntity

try:
    from homeassistant.components.switch import SwitchEntity
    from homeassistant.const import EntityCategory
except ImportError:

    class SwitchEntity:  # type: ignore[no-redef]
        pass

    class EntityCategory:  # type: ignore[no-redef]
        CONFIG = "config"


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up Local Roborock S6 configuration switches."""
    coordinator = hass.data["local_roborock"][entry.entry_id]
    async_add_entities(
        [
            RoborockCarpetBoostSwitch(coordinator, entry.entry_id),
            RoborockStatusLedSwitch(coordinator, entry.entry_id),
            RoborockDndSwitch(coordinator, entry.entry_id),
        ]
    )


class RoborockCarpetBoostSwitch(LocalRoborockEntity, SwitchEntity):
    """Switch to toggle automatic Carpet Boost suction mode."""

    _attr_name = "Carpet Boost"
    _attr_icon = "mdi:rug"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: Any, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "switch_carpet_boost")

    @property
    def is_on(self) -> bool:
        return self.data.carpet_mode.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.client.set_carpet_mode(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.client.set_carpet_mode(False)
        await self.coordinator.async_request_refresh()


class RoborockStatusLedSwitch(LocalRoborockEntity, SwitchEntity):
    """Switch to toggle the top button status indicator LED."""

    _attr_name = "Status Indicator LED"
    _attr_icon = "mdi:led-on"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: Any, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "switch_status_led")

    @property
    def is_on(self) -> bool:
        return self.data.led_status

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.client.set_led_status(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.client.set_led_status(False)
        await self.coordinator.async_request_refresh()


class RoborockDndSwitch(LocalRoborockEntity, SwitchEntity):
    """Switch to toggle Do Not Disturb mode (using configured schedule hours)."""

    _attr_name = "Do Not Disturb"
    _attr_icon = "mdi:bell-sleep"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: Any, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "switch_dnd")

    @property
    def is_on(self) -> bool:
        return self.data.status.dnd_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        dnd = self.data.dnd_timer
        await self.coordinator.client.set_dnd_timer(
            dnd.start_hour, dnd.start_minute, dnd.end_hour, dnd.end_minute
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.client.disable_dnd_timer()
        await self.coordinator.async_request_refresh()
