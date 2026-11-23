"""Button entities for targeted room cleaning, consumable resets, locate, and map refresh."""

from typing import Any

from .entity import LocalRoborockEntity

try:
    from homeassistant.components.button import ButtonEntity
    from homeassistant.const import EntityCategory
except ImportError:

    class ButtonEntity:  # type: ignore[no-redef]
        pass

    class EntityCategory:  # type: ignore[no-redef]
        CONFIG = "config"
        DIAGNOSTIC = "diagnostic"


CONSUMABLE_RESET_BUTTONS: tuple[tuple[str, str, str], ...] = (
    ("main_brush_work_time", "Reset Main Brush", "mdi:brush"),
    ("side_brush_work_time", "Reset Side Brush", "mdi:autorenew"),
    ("filter_work_time", "Reset Filter", "mdi:air-filter"),
    ("sensor_dirty_time", "Reset Sensors Timer", "mdi:eye-check-outline"),
)


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up Local Roborock S6 diagnostic/config buttons (no per-room tile clutter)."""
    coordinator = hass.data["local_roborock"][entry.entry_id]
    entities: list[Any] = [
        RoborockLocateButton(coordinator, entry.entry_id),
        RoborockRefreshMapButton(coordinator, entry.entry_id),
    ]

    for part_key, title, icon in CONSUMABLE_RESET_BUTTONS:
        entities.append(
            RoborockResetConsumableButton(coordinator, entry.entry_id, part_key, title, icon)
        )

    async_add_entities(entities)


class RoborockLocateButton(LocalRoborockEntity, ButtonEntity):
    """Button to trigger voice locator prompt ('find_me')."""

    _attr_name = "Locate Vacuum"
    _attr_icon = "mdi:map-marker-radius"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: Any, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "button_locate")

    async def async_press(self) -> None:
        await self.coordinator.client.find_me()


class RoborockRefreshMapButton(LocalRoborockEntity, ButtonEntity):
    """Button to trigger an on-demand map refresh and re-render for the active floor."""

    _attr_name = "Refresh Floor Map"
    _attr_icon = "mdi:map-search"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: Any, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "button_refresh_map")

    async def async_press(self) -> None:
        await self.coordinator.runtime.async_refresh_map()
        await self.coordinator.async_request_refresh()


class RoborockResetConsumableButton(LocalRoborockEntity, ButtonEntity):
    """Button to reset a specific consumable wear timer."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: Any,
        entry_id: str,
        consumable_key: str,
        title: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator, entry_id, f"button_reset_{consumable_key}")
        self._consumable_key = consumable_key
        self._attr_name = title
        self._attr_icon = icon

    async def async_press(self) -> None:
        await self.coordinator.client.reset_consumable(self._consumable_key)
        await self.coordinator.async_request_refresh()


class RoborockCleanRoomButton(LocalRoborockEntity, ButtonEntity):
    """Button to clean a specific named room (automatically loads the target floor if needed)."""

    _attr_icon = "mdi:broom"

    def __init__(
        self,
        coordinator: Any,
        entry_id: str,
        floor_slot: int,
        floor_name: str,
        segment_id: int,
        room_name: str,
    ) -> None:
        super().__init__(coordinator, entry_id, f"button_clean_f{floor_slot}_s{segment_id}")
        self._floor_slot = floor_slot
        self._floor_name = floor_name
        self._segment_id = segment_id
        self._room_name = room_name
        self._attr_name = f"Clean {room_name} ({floor_name})"

    async def async_press(self) -> None:
        active_slot = self.data.status.active_map_slot
        if active_slot != self._floor_slot:
            await self.coordinator.client.load_multi_map(self._floor_slot)
        await self.coordinator.client.segment_clean([self._segment_id], repeat=1)
        await self.coordinator.async_request_refresh()
