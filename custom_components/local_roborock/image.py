"""Image entity displaying the rendered floor map for Local Roborock S6."""

from datetime import UTC, datetime
from typing import Any

from .entity import LocalRoborockEntity

try:
    from homeassistant.components.image import ImageEntity
    from homeassistant.const import EntityCategory
except ImportError:

    class ImageEntity:  # type: ignore[no-redef]
        def __init__(self, hass: Any = None) -> None:
            pass

    class EntityCategory:  # type: ignore[no-redef]
        DIAGNOSTIC = "diagnostic"


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up the floor map image entity."""
    coordinator = hass.data["local_roborock"][entry.entry_id]
    async_add_entities([RoborockFloorMapImageEntity(coordinator, entry.entry_id, hass)])


class RoborockFloorMapImageEntity(LocalRoborockEntity, ImageEntity):
    """Image entity rendering the active floor's RRMap PNG and exposing calibration data."""

    _attr_name = "Floor Map"
    _attr_icon = "mdi:map"
    _attr_content_type = "image/png"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: Any, entry_id: str, hass: Any = None) -> None:
        LocalRoborockEntity.__init__(self, coordinator, entry_id, "image_floor_map")
        try:
            ImageEntity.__init__(self, hass)
        except Exception:
            try:
                ImageEntity.__init__(self)  # type: ignore[call-arg]
            except Exception:
                pass
        self._attr_image_last_updated = datetime.now(UTC)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pm = self.data.parsed_map
        active_slot = self.data.status.active_map_slot
        floor_cfg = self.coordinator.runtime.room_store.floors.get(active_slot)
        if pm is None:
            return {"active_map_slot": active_slot}

        room_attrs = []
        for seg_id, stats in sorted(pm.rooms.items()):
            rm = floor_cfg.rooms.get(seg_id) if floor_cfg else None
            room_attrs.append(
                {
                    "segment_id": seg_id,
                    "name": rm.name if rm and rm.name else f"Room {seg_id}",
                    "area_m2": stats.area_m2,
                    "center_mm": [stats.centroid_mm_x, stats.centroid_mm_y],
                }
            )

        return {
            "active_map_slot": active_slot,
            "floor_name": floor_cfg.name if floor_cfg else f"Map {active_slot}",
            "map_index": pm.map_index,
            "dimensions": {
                "width": pm.image_width,
                "height": pm.image_height,
                "left": pm.image_left,
                "top": pm.image_top,
            },
            "charger_mm": [pm.charger.x, pm.charger.y] if pm.charger else None,
            "robot_mm": [pm.robot.x, pm.robot.y] if pm.robot else None,
            "rooms": room_attrs,
            "forbidden_zones_count": len(pm.forbidden_zones),
            "no_mop_zones_count": len(pm.no_mop_zones),
            "virtual_walls_count": len(pm.virtual_walls),
        }

    async def async_image(self) -> bytes | None:
        return self.data.map_image_png
