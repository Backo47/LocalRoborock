"""DataUpdateCoordinator and state container for the Local Roborock S6 integration."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from .client import RoborockCommandError, RoborockCommunicationError, RoborockLocalClient
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .map_fetcher import XiaomiCloudSession, XiaomiMapFetchError
from .models import (
    CarpetMode,
    CleanRecord,
    ConsumableStatus,
    DeviceInfo,
    DndTimer,
    MapFloorInfo,
    VacuumStatus,
)
from .protocol import MiioProtocolError
from .room_store import RoomStore
from .rrmap import ParsedRRMap, RRMapParseError, parse_rrmap, render_rrmap_png

_LOGGER = logging.getLogger(__name__)


@dataclass
class RoborockCoordinatorData:
    """Aggregated telemetry, configuration, and map state for Home Assistant entities."""

    device_info: DeviceInfo
    serial_number: str | None
    status: VacuumStatus
    consumables: ConsumableStatus
    clean_summary: dict[str, Any]
    last_clean_record: CleanRecord | None
    dnd_timer: DndTimer
    carpet_mode: CarpetMode
    sound_volume: int
    led_status: bool
    floors: list[MapFloorInfo] = field(default_factory=list)
    parsed_map: ParsedRRMap | None = None
    map_image_png: bytes | None = None


class LocalRoborockRuntime:
    """Core async runtime managing periodic telemetry, multi-floor rooms, and map cache.

    Can be used directly or wrapped inside Home Assistant's DataUpdateCoordinator.
    """

    def __init__(
        self,
        client: RoborockLocalClient,
        storage_dir: Path,
        cloud_session: XiaomiCloudSession | None = None,
    ) -> None:
        self.client = client
        self.storage_dir = storage_dir
        self.map_cache_dir = storage_dir / "map_cache"
        self.room_store = RoomStore(storage_dir / "rooms.json")
        self.cloud_session = cloud_session
        self.www_map_html_path = storage_dir.parent / "www" / "local_roborock_map.html"

        self._device_info: DeviceInfo | None = None
        self._serial_number: str | None = None
        self._floors: list[MapFloorInfo] = []
        self._slow_tick: int = 0
        self._cached_consumables: ConsumableStatus | None = None
        self._cached_summary: dict[str, Any] = {}
        self._cached_last_record: CleanRecord | None = None
        self._cached_dnd: DndTimer = DndTimer(22, 0, 8, 0, True)
        self._cached_carpet: CarpetMode = CarpetMode(enabled=True)
        self._cached_volume: int = 90
        self._cached_led: bool = True
        self._map_by_slot: dict[int, tuple[ParsedRRMap, bytes]] = {}
        self._initialized = False

    async def async_initialize(self) -> None:
        """Load local room store, cached `.rrmap` files from disk, and static device info."""
        if self._initialized:
            return
        await asyncio.to_thread(self._init_disk_state)
        self._device_info = await self.client.get_device_info()
        try:
            self._serial_number = await self.client.get_serial_number()
        except (RoborockCommunicationError, RoborockCommandError, MiioProtocolError):
            self._serial_number = None
        try:
            self._floors = await self.client.get_floors()
        except (RoborockCommunicationError, RoborockCommandError, MiioProtocolError):
            self._floors = []

        # Auto-discover room segments on the active floor if not yet customized
        try:
            st = await self.client.get_status()
            active_slot = st.active_map_slot
            mappings = await self.client.get_room_mapping()
            floor_name = next(
                (f.name for f in self._floors if f.map_flag == active_slot),
                f"Floor {active_slot}",
            )
            if mappings:
                self.room_store.update_floor_mappings(active_slot, floor_name, mappings)
                await asyncio.to_thread(self.room_store.save)
        except Exception:
            pass

        self._initialized = True

    def _init_disk_state(self) -> None:
        self.map_cache_dir.mkdir(parents=True, exist_ok=True)
        self.room_store.load(seed_defaults=True)
        for slot in range(4):
            rrmap_file = self.map_cache_dir / f"map_slot_{slot}.rrmap"
            if rrmap_file.is_file():
                try:
                    raw = rrmap_file.read_bytes()
                    parsed = parse_rrmap(raw)
                    png = render_rrmap_png(parsed, scale=4)
                    self._map_by_slot[slot] = (parsed, png)
                    # Also ensure any room segments discovered in the map raster exist in room_store
                    if parsed.rooms:
                        floor_cfg = self.room_store.floors.get(slot)
                        fname = floor_cfg.name if floor_cfg else f"Floor {slot}"
                        from .models import RoomMapping

                        raster_mappings = [
                            RoomMapping(segment_id=seg_id, iot_id="")
                            for seg_id in sorted(parsed.rooms)
                        ]
                        self.room_store.update_floor_mappings(slot, fname, raster_mappings)
                except (OSError, RRMapParseError) as err:
                    _LOGGER.warning("Failed to load cached map for slot %d: %s", slot, err)

    async def async_fetch_state(self) -> RoborockCoordinatorData:
        """Poll fast telemetry every cycle and slower settings every 4th cycle."""
        if not self._initialized:
            await self.async_initialize()

        status = await self.client.get_status()
        refresh_slow = (self._slow_tick % 4 == 0) or (self._cached_consumables is None)
        self._slow_tick += 1

        if refresh_slow:
            self._cached_consumables = await self.client.get_consumables()
            self._cached_summary = await self.client.get_clean_summary()
            records = self._cached_summary.get("records", [])
            if isinstance(records, list) and records:
                try:
                    self._cached_last_record = await self.client.get_clean_record(int(records[0]))
                except (RoborockCommunicationError, RoborockCommandError, MiioProtocolError):
                    pass

            dnd_raw = await self.client.get_dnd_timer()
            self._cached_dnd = DndTimer.from_dict(dnd_raw)

            carpet_raw = await self.client.get_carpet_mode()
            self._cached_carpet = CarpetMode.from_dict(carpet_raw)

            vol = await self.client.get_sound_volume()
            if vol is not None:
                self._cached_volume = vol

            led = await self.client.get_led_status()
            if led is not None:
                self._cached_led = bool(led)

            try:
                floors = await self.client.get_floors()
                if floors:
                    self._floors = floors
            except (RoborockCommunicationError, RoborockCommandError, MiioProtocolError):
                pass

        active_slot = status.active_map_slot
        # If no cached map exists for active slot and cloud session is configured, try fetching once
        if active_slot not in self._map_by_slot and self.cloud_session is not None:
            try:
                await self.async_refresh_map(slot=active_slot)
            except Exception as err:
                _LOGGER.debug(
                    "Automatic initial map fetch for slot %d skipped: %s", active_slot, err
                )

        parsed_map, map_png = self._map_by_slot.get(active_slot, (None, None))
        assert self._device_info is not None
        assert self._cached_consumables is not None

        return RoborockCoordinatorData(
            device_info=self._device_info,
            serial_number=self._serial_number,
            status=status,
            consumables=self._cached_consumables,
            clean_summary=self._cached_summary,
            last_clean_record=self._cached_last_record,
            dnd_timer=self._cached_dnd,
            carpet_mode=self._cached_carpet,
            sound_volume=self._cached_volume,
            led_status=self._cached_led,
            floors=self._floors,
            parsed_map=parsed_map,
            map_image_png=map_png,
        )

    async def async_refresh_map(self, slot: int | None = None) -> tuple[ParsedRRMap, bytes]:
        """Fetch fresh `.rrmap` via `get_map_v1` + Xiaomi FDS (or re-read disk cache if offline)."""
        status = await self.client.get_status()
        initial_slot = status.active_map_slot
        target_slot = slot if slot is not None else initial_slot

        if self.cloud_session is None:
            if target_slot in self._map_by_slot:
                return self._map_by_slot[target_slot]
            raise XiaomiMapFetchError(
                "Xiaomi FDS session is not configured and no cached map exists for this floor"
            )

        switched = False
        try:
            if target_slot != initial_slot:
                await self.client.load_multi_map(target_slot)
                switched = True
                await asyncio.sleep(1.5)

            pointer = await self.client.get_map_v1()
            await asyncio.sleep(1.0)
            raw_blob = await asyncio.to_thread(self.cloud_session.download_raw_map, pointer)
        finally:
            if switched:
                await self.client.load_multi_map(initial_slot)
                await asyncio.sleep(1.0)

        def _persist_and_render() -> tuple[ParsedRRMap, bytes]:
            self.map_cache_dir.mkdir(parents=True, exist_ok=True)
            rrmap_path = self.map_cache_dir / f"map_slot_{target_slot}.rrmap"
            rrmap_path.write_bytes(raw_blob)
            parsed = parse_rrmap(raw_blob)
            png = render_rrmap_png(parsed, scale=4)
            png_path = self.map_cache_dir / f"map_slot_{target_slot}.png"
            try:
                png_path.write_bytes(png)
            except OSError:
                pass
            return parsed, png

        parsed, png = await asyncio.to_thread(_persist_and_render)
        self._map_by_slot[target_slot] = (parsed, png)
        await self.async_regenerate_interactive_html(active_slot=initial_slot)
        return parsed, png

    async def async_regenerate_interactive_html(self, active_slot: int = 1) -> None:
        """Regenerate `/config/www/local_roborock_map.html` from current cached maps and rooms."""
        from .map_editor import generate_interactive_map_html

        floors_data: dict[int, tuple[str, ParsedRRMap, dict[int, str]]] = {}
        for slot, (parsed, _png) in sorted(self._map_by_slot.items()):
            floor_cfg = self.room_store.floors.get(slot)
            fname = floor_cfg.name if floor_cfg else f"Map {slot}"
            rnames = (
                {seg: rm.name for seg, rm in floor_cfg.rooms.items() if rm.name}
                if floor_cfg
                else {}
            )
            floors_data[slot] = (fname, parsed, rnames)

        if not floors_data:
            return

        def _write_html() -> None:
            html_str = generate_interactive_map_html(floors_data, active_slot=active_slot)
            self.www_map_html_path.parent.mkdir(parents=True, exist_ok=True)
            self.www_map_html_path.write_text(html_str, encoding="utf-8")

        try:
            await asyncio.to_thread(_write_html)
        except OSError as err:
            _LOGGER.warning("Could not update %s: %s", self.www_map_html_path, err)

    async def async_apply_restrictions_to_active_map(
        self,
        virtual_walls: list[tuple[int, int, int, int]],
        forbidden_zones: list[tuple[int, int, int, int, int, int, int, int]],
        no_mop_zones: list[tuple[int, int, int, int, int, int, int, int]],
    ) -> None:
        """Update in-memory cached map restrictions immediately and regenerate interactive HTML."""
        status = await self.client.get_status()
        active_slot = status.active_map_slot
        if active_slot in self._map_by_slot:
            parsed, _old_png = self._map_by_slot[active_slot]
            parsed.virtual_walls = list(virtual_walls)
            parsed.forbidden_zones = list(forbidden_zones)
            parsed.no_mop_zones = list(no_mop_zones)
            new_png = await asyncio.to_thread(render_rrmap_png, parsed, 4)
            self._map_by_slot[active_slot] = (parsed, new_png)
        if self.cloud_session is not None:
            try:
                await self.async_refresh_map(slot=active_slot)
                return
            except Exception as err:
                _LOGGER.debug("Cloud FDS refresh after save_map skipped: %s", err)
        await self.async_regenerate_interactive_html(active_slot=active_slot)


def create_ha_coordinator(
    hass: Any,
    runtime: LocalRoborockRuntime,
    scan_interval_sec: int = DEFAULT_SCAN_INTERVAL,
) -> Any:
    """Create a Home Assistant DataUpdateCoordinator wrapping LocalRoborockRuntime."""
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

    class LocalRoborockDataUpdateCoordinator(DataUpdateCoordinator[RoborockCoordinatorData]):
        def __init__(self) -> None:
            super().__init__(
                hass,
                _LOGGER,
                name=DOMAIN,
                update_interval=timedelta(seconds=scan_interval_sec),
            )
            self.runtime = runtime
            self.client = runtime.client

        async def _async_update_data(self) -> RoborockCoordinatorData:
            try:
                return await self.runtime.async_fetch_state()
            except (
                RoborockCommunicationError,
                RoborockCommandError,
                MiioProtocolError,
            ) as err:
                raise UpdateFailed(f"Error communicating with Roborock S6: {err}") from err

    return LocalRoborockDataUpdateCoordinator()
