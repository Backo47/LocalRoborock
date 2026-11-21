"""Select entity for switching active multi-floor map on Local Roborock S6."""

from typing import Any

from .entity import LocalRoborockEntity

try:
    from homeassistant.components.select import SelectEntity
except ImportError:

    class SelectEntity:  # type: ignore[no-redef]
        pass


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up the floor map selector entity."""
    coordinator = hass.data["local_roborock"][entry.entry_id]
    async_add_entities([RoborockFloorMapSelectEntity(coordinator, entry.entry_id)])


class RoborockFloorMapSelectEntity(LocalRoborockEntity, SelectEntity):
    """Select entity to view and switch the active multi-floor map slot (load_multi_map)."""

    _attr_name = "Active Floor Map"
    _attr_icon = "mdi:layers-outline"

    def __init__(self, coordinator: Any, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "select_active_floor")

    def _slot_to_label(self) -> dict[int, str]:
        mapping: dict[int, str] = {}
        for f in self.data.floors:
            mapping[f.map_flag] = f"{f.map_flag}: {f.name}"
        for slot, cfg in sorted(self.coordinator.runtime.room_store.floors.items()):
            if slot not in mapping:
                mapping[slot] = f"{slot}: {cfg.name}"
        if not mapping:
            mapping = {0: "0: Lower floor", 1: "1: Upper floor"}
        return dict(sorted(mapping.items()))

    @property
    def options(self) -> list[str]:
        return list(self._slot_to_label().values())

    @property
    def current_option(self) -> str | None:
        slot = self.data.status.active_map_slot
        labels = self._slot_to_label()
        return labels.get(slot, next(iter(labels.values()), None))

    async def async_select_option(self, option: str) -> None:
        prefix, _, _ = option.partition(":")
        slot = int(prefix.strip())
        await self.coordinator.client.load_multi_map(slot)
        await self.coordinator.async_request_refresh()
