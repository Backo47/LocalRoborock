"""Unit tests for Home Assistant Custom Component runtime, entities, and actions."""

import asyncio
from pathlib import Path
from typing import Any

from custom_components.local_roborock.binary_sensor import (
    RoborockCleaningActiveBinarySensor,
    RoborockMopAttachedBinarySensor,
)
from custom_components.local_roborock.button import (
    RoborockCleanRoomButton,
    RoborockResetConsumableButton,
)
from custom_components.local_roborock.client import RoborockLocalClient
from custom_components.local_roborock.coordinator import (
    LocalRoborockRuntime,
    RoborockCoordinatorData,
)
from custom_components.local_roborock.image import RoborockFloorMapImageEntity
from custom_components.local_roborock.number import RoborockSoundVolumeNumber
from custom_components.local_roborock.select import RoborockFloorMapSelectEntity
from custom_components.local_roborock.sensor import SENSOR_SPECS, LocalRoborockSensorEntity
from custom_components.local_roborock.switch import (
    RoborockCarpetBoostSwitch,
    RoborockDndSwitch,
    RoborockStatusLedSwitch,
)
from custom_components.local_roborock.vacuum import (
    LocalRoborockVacuumEntity,
    VacuumActivity,
)
from tests.test_client import TEST_TOKEN_HEX, _MockVacuumServer
from tests.test_rrmap import _build_synthetic_rrmap


class _FakeCoordinator:
    """Lightweight coordinator wrapper around LocalRoborockRuntime for entity unit tests."""

    def __init__(self, runtime: LocalRoborockRuntime, data: RoborockCoordinatorData) -> None:
        self.runtime = runtime
        self.client = runtime.client
        self.data = data
        self.refresh_count = 0

    async def async_request_refresh(self) -> None:
        self.refresh_count += 1
        self.data = await self.runtime.async_fetch_state()


def test_ha_runtime_and_entities(tmp_path: Path) -> None:
    async def _run() -> None:
        # Seed a cached map for slot 1 (since mock server returns map_status=7 -> slot 1)
        map_cache_dir = tmp_path / "map_cache"
        map_cache_dir.mkdir(parents=True)
        (map_cache_dir / "map_slot_1.rrmap").write_bytes(_build_synthetic_rrmap())

        loop = asyncio.get_running_loop()
        server = _MockVacuumServer(TEST_TOKEN_HEX)
        transport, _ = await loop.create_datagram_endpoint(
            lambda: server,
            local_addr=("127.0.0.1", 0),
        )
        try:
            port = int(transport.get_extra_info("sockname")[1])
            client = RoborockLocalClient(ip="127.0.0.1", token=TEST_TOKEN_HEX, port=port)
            runtime = LocalRoborockRuntime(client=client, storage_dir=tmp_path)

            data = await runtime.async_fetch_state()
            coord = _FakeCoordinator(runtime, data)

            # 1. Vacuum Entity
            vac = LocalRoborockVacuumEntity(coord, "entry_s6")
            assert vac.activity == VacuumActivity.DOCKED
            assert vac.battery_level == 95
            assert vac.fan_speed == "Turbo"
            assert vac.extra_state_attributes["active_map_slot"] == 1
            assert 16 in vac.extra_state_attributes["floor_rooms"]

            await vac.async_start()
            await vac.async_set_fan_speed("Max")
            await vac.async_return_to_base()

            # 2. Sensor Entities
            sensors = [LocalRoborockSensorEntity(coord, "entry_s6", spec) for spec in SENSOR_SPECS]
            sensor_by_key = {s.spec.key: s for s in sensors}
            assert sensor_by_key["battery"].native_value == 95
            assert sensor_by_key["status"].native_value == "Charging"
            assert sensor_by_key["clean_area"].native_value == 20.0

            # 3. Binary Sensors
            mop_bs = RoborockMopAttachedBinarySensor(coord, "entry_s6")
            clean_bs = RoborockCleaningActiveBinarySensor(coord, "entry_s6")
            assert mop_bs.is_on is True
            assert clean_bs.is_on is False

            # 4. Floor Select Entity
            floor_sel = RoborockFloorMapSelectEntity(coord, "entry_s6")
            assert "0: Lower floor" in floor_sel.options
            assert "1: Upper floor" in floor_sel.options
            assert floor_sel.current_option == "1: Upper floor"
            await floor_sel.async_select_option("0: Lower floor")

            # 5. Switches & Volume Number
            carpet_sw = RoborockCarpetBoostSwitch(coord, "entry_s6")
            led_sw = RoborockStatusLedSwitch(coord, "entry_s6")
            dnd_sw = RoborockDndSwitch(coord, "entry_s6")
            vol_num = RoborockSoundVolumeNumber(coord, "entry_s6")

            await carpet_sw.async_turn_on()
            await led_sw.async_turn_off()
            await dnd_sw.async_turn_off()
            await vol_num.async_set_native_value(75.0)

            # 6. Room Cleaning Button & Consumable Reset Button
            room_btn = RoborockCleanRoomButton(
                coordinator=coord,
                entry_id="entry_s6",
                floor_slot=0,
                floor_name="Lower floor",
                segment_id=16,
                room_name="Obývák",
            )
            await room_btn.async_press()

            reset_btn = RoborockResetConsumableButton(
                coord, "entry_s6", "filter_work_time", "Reset Filter", "mdi:air-filter"
            )
            await reset_btn.async_press()

            # 7. Map Image Entity
            img_ent = RoborockFloorMapImageEntity(coord, "entry_s6")
            png_bytes = await img_ent.async_image()
            assert png_bytes is not None and png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
            attrs: dict[str, Any] = img_ent.extra_state_attributes
            assert attrs["active_map_slot"] == 1
            assert len(attrs["rooms"]) == 3

            # Verify commands received by mock server
            called_methods = [m for m, _ in server.received_commands]
            assert "app_start" in called_methods
            assert "set_custom_mode" in called_methods
            assert "load_multi_map" in called_methods
            assert "app_segment_clean" in called_methods
            assert "reset_consumable" in called_methods
        finally:
            transport.close()

    asyncio.run(_run())
