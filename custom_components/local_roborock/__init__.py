"""LocalRoborock - Native local Home Assistant Custom Component for Roborock S6."""

import asyncio
from pathlib import Path
from typing import Any

from .client import RoborockLocalClient
from .const import (
    CONF_HOST,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_XIAOMI_COUNTRY,
    CONF_XIAOMI_SERVICE_TOKEN,
    CONF_XIAOMI_SSECURITY,
    CONF_XIAOMI_USER_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import LocalRoborockRuntime, create_ha_coordinator
from .map_editor import MapRestrictionEditor
from .map_fetcher import XiaomiCloudSession
from .models import ZoneCoordinates

__version__ = "0.1.0"

PLATFORMS: list[str] = [
    "vacuum",
    "sensor",
    "binary_sensor",
    "select",
    "switch",
    "number",
    "button",
    "image",
]


async def async_setup_entry(hass: Any, entry: Any) -> bool:
    """Set up Local Roborock S6 from a Home Assistant config entry."""
    host = str(entry.data[CONF_HOST]).strip()
    token = str(entry.data[CONF_TOKEN]).strip()
    scan_interval = int(entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))

    user_id = str(entry.data.get(CONF_XIAOMI_USER_ID, "")).strip()
    service_token = str(entry.data.get(CONF_XIAOMI_SERVICE_TOKEN, "")).strip()
    ssecurity = str(entry.data.get(CONF_XIAOMI_SSECURITY, "")).strip()
    country = str(entry.data.get(CONF_XIAOMI_COUNTRY, "de")).strip() or "de"

    cloud_session = (
        XiaomiCloudSession(
            user_id=user_id,
            service_token=service_token,
            ssecurity=ssecurity,
            country=country,
        )
        if (user_id and service_token and ssecurity)
        else None
    )

    client = RoborockLocalClient(ip=host, token=token)
    storage_dir = Path(hass.config.path(".storage")) / DOMAIN
    runtime = LocalRoborockRuntime(
        client=client,
        storage_dir=storage_dir,
        cloud_session=cloud_session,
    )
    await runtime.async_initialize()

    coordinator = create_ha_coordinator(hass, runtime, scan_interval_sec=scan_interval)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await _async_cleanup_legacy_room_button_entities(hass, entry.entry_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _register_domain_services(hass)
    return True


async def _async_cleanup_legacy_room_button_entities(hass: Any, entry_id: str) -> None:
    """Remove legacy per-room button entities from Home Assistant's entity registry."""
    try:
        from homeassistant.helpers import entity_registry as er

        ent_reg = er.async_get(hass)
        entries = er.async_entries_for_config_entry(ent_reg, entry_id)
        for reg_entry in list(entries):
            if "_button_clean_f" in (reg_entry.unique_id or ""):
                ent_reg.async_remove(reg_entry.entity_id)
    except Exception:
        pass


async def async_unload_entry(hass: Any, entry: Any) -> bool:
    """Unload a Local Roborock S6 config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return bool(unload_ok)


def _get_first_coordinator(hass: Any) -> Any:
    entries = hass.data.get(DOMAIN, {})
    if not entries:
        raise ValueError("No active local_roborock coordinator configured")
    return next(iter(entries.values()))


def _register_domain_services(hass: Any) -> None:
    """Register custom services for targeted room cleaning, zone cleaning, goto, and map refresh."""
    if hass.services.has_service(DOMAIN, "clean_segments"):
        return

    async def _handle_clean_segments(call: Any) -> None:
        coord = _get_first_coordinator(hass)
        raw_segments = call.data.get("segments", [])
        repeat = int(call.data.get("repeat", 1))
        if isinstance(raw_segments, (str, int)):
            raw_segments = [raw_segments]
        active_slot = coord.data.status.active_map_slot
        seg_ids = coord.runtime.room_store.resolve_segments(active_slot, list(raw_segments))
        await coord.client.segment_clean(seg_ids, repeat=repeat)
        await coord.async_request_refresh()

    async def _handle_clean_zone(call: Any) -> None:
        coord = _get_first_coordinator(hass)
        zone = ZoneCoordinates(
            x1=int(call.data["x1"]),
            y1=int(call.data["y1"]),
            x2=int(call.data["x2"]),
            y2=int(call.data["y2"]),
            iterations=int(call.data.get("repeat", 1)),
        )
        await coord.client.zoned_clean([zone])
        await coord.async_request_refresh()

    async def _handle_goto(call: Any) -> None:
        coord = _get_first_coordinator(hass)
        await coord.client.goto_target(int(call.data["x"]), int(call.data["y"]))
        await coord.async_request_refresh()

    async def _handle_refresh_map(call: Any) -> None:
        coord = _get_first_coordinator(hass)
        slot = call.data.get("floor")
        await coord.runtime.async_refresh_map(slot=int(slot) if slot is not None else None)
        await coord.async_request_refresh()

    async def _handle_merge_rooms(call: Any) -> None:
        coord = _get_first_coordinator(hass)
        seg_a = int(call.data["segment_a"])
        seg_b = int(call.data["segment_b"])
        await coord.client.merge_segments(seg_a, seg_b)
        if coord.runtime.cloud_session is not None:
            try:
                await asyncio.sleep(1.5)
                await coord.runtime.async_refresh_map()
            except Exception:
                pass
        await coord.async_request_refresh()

    async def _handle_split_room(call: Any) -> None:
        coord = _get_first_coordinator(hass)
        await coord.client.split_segment(
            segment_id=int(call.data["segment_id"]),
            x1=int(call.data["x1"]),
            y1=int(call.data["y1"]),
            x2=int(call.data["x2"]),
            y2=int(call.data["y2"]),
        )
        if coord.runtime.cloud_session is not None:
            try:
                await asyncio.sleep(1.5)
                await coord.runtime.async_refresh_map()
            except Exception:
                pass
        await coord.async_request_refresh()

    async def _handle_add_restriction(call: Any) -> None:
        coord = _get_first_coordinator(hass)
        pm = coord.data.parsed_map
        if pm is None:
            raise ValueError("Cannot edit map restrictions without a cached floor map")
        editor = MapRestrictionEditor.from_parsed_map(pm)
        editor.save_backup(coord.runtime.map_cache_dir, coord.data.status.active_map_slot)
        rtype = str(call.data["restriction_type"]).strip().lower()
        x1, y1 = int(call.data["x1"]), int(call.data["y1"])
        x2, y2 = int(call.data["x2"]), int(call.data["y2"])
        if rtype == "virtual_wall":
            editor.add_virtual_wall(x1, y1, x2, y2)
        elif rtype == "no_go_zone":
            editor.add_forbidden_zone_rect(x1, y1, x2, y2)
        elif rtype == "no_mop_zone":
            editor.add_no_mop_zone_rect(x1, y1, x2, y2)
        else:
            raise ValueError(f"Unsupported restriction_type: {rtype}")
        await coord.client.save_map_restrictions(
            virtual_walls=editor.virtual_walls,
            forbidden_zones=editor.forbidden_zones,
            no_mop_zones=editor.no_mop_zones,
        )
        await coord.runtime.async_apply_restrictions_to_active_map(
            virtual_walls=editor.virtual_walls,
            forbidden_zones=editor.forbidden_zones,
            no_mop_zones=editor.no_mop_zones,
        )
        await coord.async_request_refresh()

    async def _handle_set_map_restrictions(call: Any) -> None:
        """Save a complete updated set of virtual walls, No-Go zones, and No-Mop zones."""
        coord = _get_first_coordinator(hass)
        pm = coord.data.parsed_map
        if pm is not None:
            old_editor = MapRestrictionEditor.from_parsed_map(pm)
            old_editor.save_backup(coord.runtime.map_cache_dir, coord.data.status.active_map_slot)

        raw_walls = call.data.get("virtual_walls", [])
        raw_nogo = call.data.get("forbidden_zones", [])
        raw_nomop = call.data.get("no_mop_zones", [])

        walls: list[tuple[int, int, int, int]] = [
            (int(w[0]), int(w[1]), int(w[2]), int(w[3]))
            for w in raw_walls
            if isinstance(w, list) and len(w) == 4
        ]

        def _normalize_zone_list(
            items: Any,
        ) -> list[tuple[int, int, int, int, int, int, int, int]]:
            from .map_editor import rect_to_quad

            out: list[tuple[int, int, int, int, int, int, int, int]] = []
            for z in items:
                if isinstance(z, list) and len(z) == 8:
                    out.append(
                        (
                            int(z[0]),
                            int(z[1]),
                            int(z[2]),
                            int(z[3]),
                            int(z[4]),
                            int(z[5]),
                            int(z[6]),
                            int(z[7]),
                        )
                    )
                elif isinstance(z, list) and len(z) == 4:
                    out.append(rect_to_quad(int(z[0]), int(z[1]), int(z[2]), int(z[3])))
            return out

        nogo = _normalize_zone_list(raw_nogo)
        nomop = _normalize_zone_list(raw_nomop)

        await coord.client.save_map_restrictions(
            virtual_walls=walls,
            forbidden_zones=nogo,
            no_mop_zones=nomop,
        )
        await coord.runtime.async_apply_restrictions_to_active_map(
            virtual_walls=walls,
            forbidden_zones=nogo,
            no_mop_zones=nomop,
        )
        await coord.async_request_refresh()

    async def _handle_delete_floor_map(call: Any) -> None:
        """Delete a stored multi-floor map slot (0..3) and archive its local cache."""
        coord = _get_first_coordinator(hass)
        slot = int(call.data["floor"])
        await coord.client.delete_floor_map(slot)
        coord.runtime._map_by_slot.pop(slot, None)
        await coord.runtime.async_regenerate_interactive_html()
        await coord.async_request_refresh()

    async def _handle_reset_active_map(call: Any) -> None:
        """Reset the currently loaded map so the vacuum creates a fresh map on next clean."""
        coord = _get_first_coordinator(hass)
        await coord.client.reset_active_map()
        await coord.async_request_refresh()

    async def _handle_start_new_mapping(call: Any) -> None:
        """Ensure multi-map saving is enabled and start a full-floor mapping/cleaning run."""
        coord = _get_first_coordinator(hass)
        if coord.data.status.lab_status == 0:
            await coord.client.set_lab_status(True)
        await coord.client.start_cleaning()
        await coord.async_request_refresh()

    hass.services.async_register(DOMAIN, "clean_segments", _handle_clean_segments)
    hass.services.async_register(DOMAIN, "clean_zone", _handle_clean_zone)
    hass.services.async_register(DOMAIN, "goto", _handle_goto)
    hass.services.async_register(DOMAIN, "refresh_map", _handle_refresh_map)
    hass.services.async_register(DOMAIN, "merge_rooms", _handle_merge_rooms)
    hass.services.async_register(DOMAIN, "split_room", _handle_split_room)
    hass.services.async_register(DOMAIN, "add_map_restriction", _handle_add_restriction)
    hass.services.async_register(DOMAIN, "set_map_restrictions", _handle_set_map_restrictions)
    hass.services.async_register(DOMAIN, "delete_floor_map", _handle_delete_floor_map)
    hass.services.async_register(DOMAIN, "reset_active_map", _handle_reset_active_map)
    hass.services.async_register(DOMAIN, "start_new_floor_mapping", _handle_start_new_mapping)
