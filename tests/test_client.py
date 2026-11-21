"""Unit tests for the async RoborockLocalClient using a local mock UDP server."""

import asyncio
import struct
from pathlib import Path
from typing import Any

import pytest

from custom_components.local_roborock.cli import main as cli_main
from custom_components.local_roborock.client import (
    RoborockCommandError,
    RoborockCommunicationError,
    RoborockLocalClient,
    sanitize_payload,
)
from custom_components.local_roborock.const import HEADER_SIZE, MIIO_MAGIC, RoborockFanPower
from custom_components.local_roborock.models import RoomMapping, ZoneCoordinates
from custom_components.local_roborock.protocol import (
    build_packet,
    parse_packet,
    validate_token,
)
from custom_components.local_roborock.room_store import RoomStore

TEST_TOKEN_HEX = "4b657931323334353637383930616263"
TEST_DEVICE_ID = 0x0A0B0C0D


class _MockVacuumServer(asyncio.DatagramProtocol):
    """In-memory mock of Roborock S6 miIO UDP server on localhost."""

    def __init__(self, token_hex: str) -> None:
        self.token = validate_token(token_hex)
        self.transport: asyncio.DatagramTransport | None = None
        self.stamp = 5000
        self.received_commands: list[tuple[str, Any]] = []

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str | Any, int]) -> None:
        assert self.transport is not None
        pkt = parse_packet(data, token=None if len(data) == HEADER_SIZE else self.token)
        if pkt.is_hello:
            reply = struct.pack(
                ">HHIII16s",
                MIIO_MAGIC,
                HEADER_SIZE,
                0,
                TEST_DEVICE_ID,
                self.stamp,
                b"\x00" * 16,
            )
            self.transport.sendto(reply, addr)
            return

        assert pkt.payload is not None
        msg_id = pkt.payload["id"]
        method = pkt.payload["method"]
        params = pkt.payload.get("params")
        self.received_commands.append((method, params))

        if method == "get_status":
            result: Any = [
                {
                    "msg_ver": 2,
                    "msg_seq": 12,
                    "state": 8,
                    "battery": 95,
                    "clean_time": 1200,
                    "clean_area": 20000000,
                    "error_code": 0,
                    "map_present": 1,
                    "in_cleaning": 0,
                    "in_returning": 0,
                    "in_fresh_state": 1,
                    "lab_status": 1,
                    "water_box_status": 1,
                    "fan_power": 103,
                    "dnd_enabled": 0,
                    "map_status": 7,
                }
            ]
        elif method == "get_room_mapping":
            result = [[16, "236010101"], [17, "236010102"]]
        elif method == "get_consumable":
            result = [
                {
                    "main_brush_work_time": 423189,
                    "side_brush_work_time": 423189,
                    "filter_work_time": 423189,
                    "filter_element_work_time": 111337,
                    "sensor_dirty_time": 25982,
                }
            ]
        elif method == "get_clean_summary":
            result = [419802, 8561400000, 610, [1794135571]]
        elif method == "get_clean_record":
            result = [[1794135571, 1794138060, 2489, 48442500, 0, 1, 2, 1, 52]]
        elif method == "get_dnd_timer":
            result = [
                {
                    "start_hour": 22,
                    "start_minute": 0,
                    "end_hour": 8,
                    "end_minute": 0,
                    "enabled": 1,
                }
            ]
        elif method == "get_carpet_mode":
            result = [
                {
                    "enable": 1,
                    "current_integral": 450,
                    "current_high": 500,
                    "current_low": 400,
                    "stall_time": 10,
                }
            ]
        elif method == "get_sound_volume":
            result = [90]
        elif method == "get_led_status":
            result = [1]
        elif method == "get_serial_number":
            result = [{"serial_number": "R0306S00000000"}]
        elif method == "get_multi_maps_list":
            result = [
                {
                    "max_multi_map": 4,
                    "multi_map_count": 2,
                    "map_info": [
                        {"mapFlag": 0, "name": "Lower floor", "add_time": 1714492596},
                        {"mapFlag": 1, "name": "Upper floor ", "add_time": 1794138113},
                    ],
                }
            ]
        elif method == "miIO.info":
            result = {
                "model": "roborock.vacuum.s6",
                "fw_ver": "3.5.8_002008",
                "hw_ver": "Linux",
                "token": TEST_TOKEN_HEX,
                "ap": {"ssid": "HomeWiFi", "rssi": -48, "bssid": "AA:BB:CC:DD:EE:FF"},
                "netif": {"localIp": "192.168.1.110"},
            }
        elif method == "bad_msg_id":
            resp_pkt = build_packet(
                device_id=TEST_DEVICE_ID,
                stamp=self.stamp + 1,
                token=self.token,
                payload={"id": msg_id + 99, "result": ["wrong_id"]},
            )
            self.transport.sendto(resp_pkt, addr)
            return
        elif method == "get_water_box_custom_mode":
            result = "unknown_method"
        else:
            result = ["ok"]

        resp_pkt = build_packet(
            device_id=TEST_DEVICE_ID,
            stamp=self.stamp + 1,
            token=self.token,
            payload={"id": msg_id, "result": result},
        )
        self.transport.sendto(resp_pkt, addr)


def test_async_client_against_mock_server() -> None:
    async def _run() -> None:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _MockVacuumServer(TEST_TOKEN_HEX),
            local_addr=("127.0.0.1", 0),
        )
        try:
            sockname = transport.get_extra_info("sockname")
            port = int(sockname[1])

            client = RoborockLocalClient(
                ip="127.0.0.1",
                token=TEST_TOKEN_HEX,
                port=port,
                timeout=2.0,
            )
            status = await client.get_status()
            assert status.battery == 95
            assert status.state_name == "Charging"
            assert status.fan_power_name == "Turbo (103)"
            assert status.clean_area_m2 == 20.0
            assert status.active_map_slot == 1

            rooms = await client.get_room_mapping()
            assert len(rooms) == 2
            assert rooms[0].segment_id == 16
            assert rooms[0].iot_id == "236010101"

            info = await client.get_device_info()
            assert info.model == "roborock.vacuum.s6"
            assert info.raw["token"] == "**REDACTED**"
            assert info.raw["ap"]["bssid"] == "**REDACTED**"

            # Verify JSON-RPC response message ID mismatch is caught
            try:
                await client.send_command("bad_msg_id")
                raise AssertionError("Expected RoborockCommunicationError on mismatched msg_id")
            except RoborockCommunicationError as err:
                assert "Message ID mismatch" in str(err)

            # Verify 'unknown_method' result raises RoborockCommandError immediately
            try:
                await client.send_command("get_water_box_custom_mode")
                raise AssertionError("Expected RoborockCommandError on unknown_method")
            except RoborockCommandError as err:
                assert "unknown_method" in str(err)
        finally:
            transport.close()

    asyncio.run(_run())


def test_sanitize_payload_redacts_secrets() -> None:
    raw = {
        "model": "roborock.vacuum.s6",
        "token": "4b657931323334353637383930616263",
        "ap": {"ssid": "MyNet", "passwd": "secret_password", "bssid": "11:22:33:44:55:66"},
    }
    cleaned = sanitize_payload(raw)
    assert cleaned["token"] == "**REDACTED**"
    assert cleaned["ap"]["passwd"] == "**REDACTED**"
    assert cleaned["ap"]["bssid"] == "**REDACTED**"
    assert cleaned["ap"]["ssid"] == "MyNet"
    # Original dict must remain untouched
    assert raw["token"] == "4b657931323334353637383930616263"


def test_phase3_action_methods_against_mock_server() -> None:
    async def _run() -> None:
        loop = asyncio.get_running_loop()
        server = _MockVacuumServer(TEST_TOKEN_HEX)
        transport, _ = await loop.create_datagram_endpoint(
            lambda: server,
            local_addr=("127.0.0.1", 0),
        )
        try:
            port = int(transport.get_extra_info("sockname")[1])
            client = RoborockLocalClient(ip="127.0.0.1", token=TEST_TOKEN_HEX, port=port)

            assert await client.start_cleaning()
            assert await client.pause_cleaning()
            assert await client.stop_cleaning()
            assert await client.return_to_dock()
            assert await client.spot_clean()
            assert await client.find_me()
            assert await client.set_fan_power(RoborockFanPower.TURBO)
            assert await client.segment_clean([16, 17], repeat=2)
            assert await client.zoned_clean(
                [ZoneCoordinates(x1=26000, y1=26000, x2=24000, y2=24000, iterations=2)]
            )
            assert await client.goto_target(25500, 26500)
            assert await client.load_multi_map(0)
            assert await client.set_sound_volume(80)
            assert await client.set_led_status(True)
            assert await client.set_carpet_mode(True)
            assert await client.set_dnd_timer(22, 0, 8, 0)
            assert await client.disable_dnd_timer()
            assert await client.reset_consumable("filter_work_time")
            assert await client.remote_control_start()
            assert await client.remote_control_move(velocity=0.2, omega=-0.5, duration_ms=1000)
            assert await client.remote_control_end()
            assert await client.delete_floor_map(2)
            assert await client.reset_active_map()

            methods_called = [m for m, _ in server.received_commands]
            assert "app_start" in methods_called
            assert "app_segment_clean" in methods_called
            assert "app_zoned_clean" in methods_called
            assert "app_goto_target" in methods_called
            assert "load_multi_map" in methods_called
            assert "del_map" in methods_called
            assert "reset_map" in methods_called

            # Verify zone coordinates were auto-normalized to min/max
            zoned_call = next(p for m, p in server.received_commands if m == "app_zoned_clean")
            assert zoned_call == [[24000, 24000, 26000, 26000, 2]]

            # Verify validation errors
            with pytest.raises(ValueError):
                await client.set_fan_power(999)
            with pytest.raises(ValueError):
                await client.segment_clean([16], repeat=5)
            with pytest.raises(ValueError):
                await client.reset_consumable("invalid_part")
        finally:
            transport.close()

    asyncio.run(_run())


def test_room_store_and_cli_dry_run(tmp_path: Path, capsys: Any) -> None:
    rooms_file = tmp_path / "rooms.json"
    store = RoomStore(rooms_file)
    store.update_floor_mappings(
        map_flag=0,
        floor_name="Lower floor",
        mappings=[
            RoomMapping(segment_id=16, iot_id="100001"),
            RoomMapping(segment_id=17, iot_id="100002"),
        ],
    )
    store.rename_room(0, 16, "Kitchen")
    store.save()

    reloaded = RoomStore(rooms_file)
    reloaded.load()
    assert reloaded.resolve_segments(0, ["Kitchen", 17]) == [16, 17]

    env_file = tmp_path / ".env"
    env_file.write_text(
        f"ROBOROCK_IP=192.168.1.110\nROBOROCK_TOKEN={TEST_TOKEN_HEX}\n",
        encoding="utf-8",
    )
    rc = cli_main(
        [
            "--env-file",
            str(env_file),
            "clean-rooms",
            "Kitchen",
            "17",
            "--repeat",
            "2",
            "--rooms-file",
            str(rooms_file),
            "--dry-run",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "app_segment_clean" in out
    assert '"segments": [16, 17]' in out
    assert '"repeat": 2' in out


