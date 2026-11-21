"""Asynchronous single-queue UDP client for Roborock S6 over Xiaomi miIO."""

import asyncio
import logging
import secrets
import time
from dataclasses import replace
from typing import Any

from .const import (
    CONSUMABLE_LIMITS_SECONDS,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    FAN_POWER_DESCRIPTIONS,
    RoborockFanPower,
)
from .models import (
    CarpetMode,
    CleanRecord,
    ConsumableStatus,
    DeviceInfo,
    DndTimer,
    MapFloorInfo,
    RoomMapping,
    VacuumStatus,
    ZoneCoordinates,
)
from .protocol import (
    MiioPacket,
    MiioProtocolError,
    build_hello_packet,
    build_packet,
    parse_packet,
    validate_token,
)

_LOGGER = logging.getLogger(__name__)

SENSITIVE_DICT_KEYS = frozenset({"token", "passwd", "password", "key", "bssid"})


def sanitize_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of a response dictionary with sensitive fields redacted."""
    clean: dict[str, Any] = {}
    for k, v in data.items():
        if k.lower() in SENSITIVE_DICT_KEYS and v:
            clean[k] = "**REDACTED**"
        elif isinstance(v, dict):
            clean[k] = sanitize_payload(v)
        else:
            clean[k] = v
    return clean


class RoborockCommunicationError(Exception):
    """Raised when UDP communication times out or fails."""


class RoborockCommandError(Exception):
    """Raised when the vacuum returns a JSON-RPC error object."""


class _UdpProtocol(asyncio.DatagramProtocol):
    """Internal asyncio DatagramProtocol handler for request/response pairing."""

    def __init__(self) -> None:
        self.transport: asyncio.DatagramTransport | None = None
        self._response_future: asyncio.Future[bytes] | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str | Any, int]) -> None:
        if self._response_future is not None and not self._response_future.done():
            self._response_future.set_result(data)

    def error_received(self, exc: Exception) -> None:
        if self._response_future is not None and not self._response_future.done():
            self._response_future.set_exception(exc)

    async def send_and_receive(self, payload: bytes, timeout: float) -> bytes:
        if self.transport is None:
            raise RoborockCommunicationError("UDP transport is not initialized")
        loop = asyncio.get_running_loop()
        self._response_future = loop.create_future()
        self.transport.sendto(payload)
        try:
            return await asyncio.wait_for(self._response_future, timeout=timeout)
        finally:
            self._response_future = None


class RoborockLocalClient:
    """Async local miIO UDP client for Roborock S6 (`roborock.vacuum.s6`)."""

    def __init__(
        self,
        ip: str,
        token: str,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = 3,
    ) -> None:
        self.ip = ip
        self.port = port
        self.timeout = timeout
        self.retries = retries
        self._token = validate_token(token)
        self._device_id: int | None = None
        self._server_stamp: int = 0
        self._stamp_monotonic: float = 0.0
        self._msg_id: int = secrets.randbelow(9000) + 100
        self._lock = asyncio.Lock()

    def _next_msg_id(self) -> int:
        self._msg_id = (self._msg_id % 9999) + 1
        return self._msg_id

    def _current_stamp(self) -> int:
        elapsed = int(time.monotonic() - self._stamp_monotonic)
        return (self._server_stamp + max(0, elapsed)) & 0xFFFFFFFF

    async def _exchange_datagram(self, packet_bytes: bytes) -> bytes:
        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            _UdpProtocol,
            remote_addr=(self.ip, self.port),
        )
        try:
            return await protocol.send_and_receive(packet_bytes, self.timeout)
        except TimeoutError as err:
            raise RoborockCommunicationError(
                f"UDP request to {self.ip}:{self.port} timed out after {self.timeout}s"
            ) from err
        except OSError as err:
            raise RoborockCommunicationError(
                f"Network error talking to {self.ip}:{self.port}: {err}"
            ) from err
        finally:
            transport.close()

    async def _handshake_unlocked(self) -> MiioPacket:
        """Internal handshake implementation (caller must hold self._lock)."""
        hello_bytes = build_hello_packet()
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                raw_reply = await self._exchange_datagram(hello_bytes)
                pkt = parse_packet(raw_reply)
                if not pkt.is_hello or pkt.device_id in (0, 0xFFFFFFFF):
                    raise MiioProtocolError(
                        f"Invalid Hello handshake response (device_id=0x{pkt.device_id:08x})"
                    )
                self._device_id = pkt.device_id
                self._server_stamp = pkt.stamp
                self._stamp_monotonic = time.monotonic()
                _LOGGER.debug(
                    "Handshake with %s succeeded: device_id=0x%08x, stamp=%d",
                    self.ip,
                    pkt.device_id,
                    pkt.stamp,
                )
                return pkt
            except (RoborockCommunicationError, MiioProtocolError) as err:
                last_error = err
                if attempt < self.retries:
                    await asyncio.sleep(0.3 * attempt)
        raise RoborockCommunicationError(
            f"Handshake with {self.ip}:{self.port} failed after {self.retries} attempts: "
            f"{last_error}"
        ) from last_error

    async def handshake(self) -> MiioPacket:
        """Perform a miIO Hello handshake serialized through the client lock."""
        async with self._lock:
            return await self._handshake_unlocked()

    async def send_command(
        self,
        method: str,
        params: list[Any] | dict[str, Any] | None = None,
        retries: int | None = None,
    ) -> Any:
        """Send an encrypted JSON-RPC command serialized through a single async lock."""
        max_attempts = retries if retries is not None else self.retries
        async with self._lock:
            last_error: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    # Re-handshake if not initialized or stamp is older than 120s
                    if (
                        self._device_id is None
                        or (time.monotonic() - self._stamp_monotonic) > 120.0
                    ):
                        await self._handshake_unlocked()

                    assert self._device_id is not None
                    msg_id = self._next_msg_id()
                    payload: dict[str, Any] = {
                        "id": msg_id,
                        "method": method,
                        "params": params if params is not None else [],
                    }
                    raw_req = build_packet(
                        device_id=self._device_id,
                        stamp=self._current_stamp(),
                        token=self._token,
                        payload=payload,
                    )
                    raw_resp = await self._exchange_datagram(raw_req)
                    resp_pkt = parse_packet(raw_resp, token=self._token)

                    if resp_pkt.payload is None:
                        raise MiioProtocolError("Received unexpected empty Hello packet")

                    if resp_pkt.device_id != self._device_id:
                        raise MiioProtocolError(
                            f"Device ID mismatch in response: expected 0x{self._device_id:08x}, "
                            f"got 0x{resp_pkt.device_id:08x}"
                        )

                    resp_id = resp_pkt.payload.get("id")
                    if resp_id != msg_id:
                        raise MiioProtocolError(
                            f"Message ID mismatch in response: expected id={msg_id}, "
                            f"got id={resp_id}"
                        )

                    # Keep stamp freshly synchronized with each valid response
                    self._server_stamp = resp_pkt.stamp
                    self._stamp_monotonic = time.monotonic()

                    if "error" in resp_pkt.payload and resp_pkt.payload["error"]:
                        err_obj = resp_pkt.payload["error"]
                        raise RoborockCommandError(
                            f"Command '{method}' failed with device error: {err_obj}"
                        )

                    result = resp_pkt.payload.get("result")
                    if result == "unknown_method" or result == ["unknown_method"]:
                        raise RoborockCommandError(
                            f"Command '{method}' is not supported by firmware ('unknown_method')"
                        )

                    return result

                except RoborockCommandError:
                    # Device explicitly rejected the command; do not retry
                    raise
                except (RoborockCommunicationError, MiioProtocolError) as err:
                    last_error = err
                    _LOGGER.debug(
                        "Attempt %d/%d for '%s' failed (%s); jumping msg_id and re-handshaking",
                        attempt,
                        max_attempts,
                        method,
                        err,
                    )
                    self._device_id = None
                    # Jump msg_id forward by a random offset in case the vacuum rejected a
                    # duplicate id from a previous CLI process or concurrent HA polling.
                    self._msg_id = ((self._msg_id + secrets.randbelow(1000) + 100) % 9999) + 1
                    if attempt < max_attempts:
                        await asyncio.sleep(0.3 * attempt)

            raise RoborockCommunicationError(
                f"Failed to execute '{method}' after {max_attempts} attempts: {last_error}"
            ) from last_error

    # --- High-level read-only diagnostic and status methods (Phase 1) ---

    async def get_device_info(self) -> DeviceInfo:
        """Fetch system & network metadata via miIO.info (with sensitive fields sanitized)."""
        res = await self.send_command("miIO.info")
        if not isinstance(res, dict):
            raise MiioProtocolError("Unexpected miIO.info response type")
        sanitized = sanitize_payload(res)
        info = DeviceInfo.from_dict(sanitized)
        return replace(info, raw=sanitized)

    async def get_status(self) -> VacuumStatus:
        """Fetch main telemetry via get_status."""
        res = await self.send_command("get_status")
        if not isinstance(res, list) or not res or not isinstance(res[0], dict):
            raise MiioProtocolError(f"Unexpected get_status response: {res}")
        return VacuumStatus.from_dict(res[0])

    async def get_consumables(self) -> ConsumableStatus:
        """Fetch consumable wear counters via get_consumable."""
        res = await self.send_command("get_consumable")
        if not isinstance(res, list) or not res or not isinstance(res[0], dict):
            raise MiioProtocolError(f"Unexpected get_consumable response: {res}")
        return ConsumableStatus.from_dict(res[0])

    async def get_room_mapping(self) -> list[RoomMapping]:
        """Fetch room segment mappings via get_room_mapping."""
        res = await self.send_command("get_room_mapping")
        if not isinstance(res, list):
            return []
        mappings: list[RoomMapping] = []
        for item in res:
            if isinstance(item, list) and len(item) >= 2:
                try:
                    mappings.append(RoomMapping(segment_id=int(item[0]), iot_id=str(item[1])))
                except (ValueError, TypeError):
                    continue
        return mappings

    async def get_multi_maps_list(self) -> dict[str, Any]:
        """Fetch saved multi-floor map metadata via get_multi_maps_list."""
        res = await self.send_command("get_multi_maps_list")
        if isinstance(res, list) and res and isinstance(res[0], dict):
            return res[0]
        if isinstance(res, dict):
            return res
        return {"raw": res}

    async def get_clean_summary(self) -> dict[str, Any]:
        """Fetch lifetime cleaning statistics via get_clean_summary."""
        res = await self.send_command("get_clean_summary")
        if isinstance(res, dict):
            return res
        if isinstance(res, list) and len(res) >= 4:
            area_raw = int(res[1])
            return {
                "clean_time_sec": int(res[0]),
                "clean_time_hours": round(int(res[0]) / 3600.0, 1),
                "clean_area_raw": area_raw,
                "clean_area_m2": round(area_raw / 1_000_000.0, 2),
                "clean_count": int(res[2]),
                "records": res[3],
            }
        return {"raw": res}

    async def get_clean_record(self, record_id: int) -> CleanRecord:
        """Fetch details of a specific cleaning run by its timestamp record_id."""
        res = await self.send_command("get_clean_record", [record_id])
        return CleanRecord.from_raw(res)

    async def get_timers(self) -> list[Any]:
        """Fetch scheduled cleaning timers via get_timer."""
        res = await self.send_command("get_timer")
        return res if isinstance(res, list) else []

    async def get_current_sound(self) -> Any:
        """Fetch active voice pack metadata via get_current_sound."""
        return await self.send_command("get_current_sound")

    async def get_child_lock_status(self) -> Any:
        """Fetch physical button child lock status via get_child_lock_status."""
        return await self.send_command("get_child_lock_status")

    async def get_timezone(self) -> Any:
        """Fetch configured device timezone via get_timezone."""
        return await self.send_command("get_timezone")

    async def get_fw_features(self) -> list[int]:
        """Fetch list of firmware feature capability IDs via get_fw_features."""
        res = await self.send_command("get_fw_features")
        if isinstance(res, list):
            return [int(x) for x in res if isinstance(x, (int, str)) and str(x).isdigit()]
        return []

    async def get_custom_mode(self) -> list[int]:
        """Fetch currently configured fan power mode via get_custom_mode."""
        res = await self.send_command("get_custom_mode")
        return res if isinstance(res, list) else []

    async def get_led_status(self) -> int | None:
        """Fetch top button status indicator LED state (1=on, 0=off) via get_led_status."""
        res = await self.send_command("get_led_status")
        if isinstance(res, list) and res:
            return int(res[0])
        return None

    async def get_serial_number(self) -> str | None:
        """Fetch device hardware serial number via get_serial_number."""
        res = await self.send_command("get_serial_number")
        if isinstance(res, list) and res and isinstance(res[0], dict):
            val = res[0].get("serial_number")
            return str(val) if val else None
        return None

    async def get_network_info(self) -> Any:
        """Fetch network diagnostics via get_network_info."""
        res = await self.send_command("get_network_info")
        if isinstance(res, dict):
            return sanitize_payload(res)
        return res

    async def get_floors(self) -> list[MapFloorInfo]:
        """Return typed list of saved multi-floor maps from get_multi_maps_list."""
        raw = await self.get_multi_maps_list()
        info_list = raw.get("map_info", [])
        if not isinstance(info_list, list):
            return []
        return [MapFloorInfo.from_dict(item) for item in info_list if isinstance(item, dict)]

    async def get_map_v1(self) -> str:
        """Query get_map_v1 to trigger map upload and retrieve the current map slot pointer."""
        for _ in range(5):
            res = await self.send_command("get_map_v1")
            if isinstance(res, list) and res:
                pointer = str(res[0])
                if pointer and pointer != "retry":
                    return pointer
            await asyncio.sleep(1.0)
        raise RoborockCommunicationError("Vacuum returned 'retry' or empty pointer for get_map_v1")

    # --- High-level action & configuration methods (Phase 3) ---

    @staticmethod
    def _is_ok(result: Any) -> bool:
        return result in ("ok", ["ok"], 0, [0])

    async def start_cleaning(self) -> bool:
        """Start a full-floor cleaning cycle (app_start)."""
        return self._is_ok(await self.send_command("app_start"))

    async def stop_cleaning(self) -> bool:
        """Stop the current cleaning cycle (app_stop)."""
        return self._is_ok(await self.send_command("app_stop"))

    async def pause_cleaning(self) -> bool:
        """Pause the current cleaning cycle (app_pause)."""
        return self._is_ok(await self.send_command("app_pause"))

    async def return_to_dock(self) -> bool:
        """Command the vacuum to return to its charging dock (app_charge)."""
        return self._is_ok(await self.send_command("app_charge"))

    async def spot_clean(self) -> bool:
        """Start spot cleaning around the current position (app_spot)."""
        return self._is_ok(await self.send_command("app_spot"))

    async def find_me(self) -> bool:
        """Play a voice locator prompt on the vacuum (find_me)."""
        return self._is_ok(await self.send_command("find_me", [""]))

    async def set_fan_power(self, power: int | RoborockFanPower) -> bool:
        """Set suction fan power (101=Quiet, 102=Balanced, 103=Turbo, 104=Max, 105=Gentle)."""
        val = int(power)
        if val not in FAN_POWER_DESCRIPTIONS:
            raise ValueError(
                f"Invalid fan power {val}. Allowed values: {sorted(FAN_POWER_DESCRIPTIONS)}"
            )
        return self._is_ok(await self.send_command("set_custom_mode", [val]))

    async def segment_clean(self, segments: list[int], repeat: int = 1) -> bool:
        """Clean specific room segment IDs (app_segment_clean) with optional repeat (1..3)."""
        if not segments:
            raise ValueError("At least one room segment ID is required")
        if not (1 <= repeat <= 3):
            raise ValueError(f"Repeat count must be between 1 and 3, got {repeat}")
        clean_ids = [int(s) for s in segments]
        if any(s <= 0 for s in clean_ids):
            raise ValueError(f"Segment IDs must be positive integers, got {clean_ids}")
        params: list[Any] = [{"segments": clean_ids, "repeat": repeat}]
        return self._is_ok(await self.send_command("app_segment_clean", params))

    async def resume_segment_clean(self) -> bool:
        """Resume a paused room (segment) cleaning job (resume_segment_clean)."""
        return self._is_ok(await self.send_command("resume_segment_clean"))

    async def zoned_clean(self, zones: list[ZoneCoordinates]) -> bool:
        """Clean one or more rectangular coordinate zones (app_zoned_clean)."""
        if not zones:
            raise ValueError("At least one ZoneCoordinates rectangle is required")
        payload = [z.to_miio_list() for z in zones]
        return self._is_ok(await self.send_command("app_zoned_clean", payload))

    async def resume_zoned_clean(self) -> bool:
        """Resume a paused zoned cleaning job (resume_zoned_clean)."""
        return self._is_ok(await self.send_command("resume_zoned_clean"))

    async def goto_target(self, x: int, y: int) -> bool:
        """Navigate the vacuum to a specific (x, y) millimeter coordinate (app_goto_target)."""
        return self._is_ok(await self.send_command("app_goto_target", [int(x), int(y)]))

    async def load_multi_map(self, map_flag: int) -> bool:
        """Switch the active multi-floor map slot (0..3) via load_multi_map."""
        if not (0 <= int(map_flag) <= 3):
            raise ValueError(f"map_flag must be between 0 and 3, got {map_flag}")
        return self._is_ok(await self.send_command("load_multi_map", [int(map_flag)]))

    async def set_sound_volume(self, volume: int) -> bool:
        """Set voice prompt volume percentage (0..100) via change_sound_volume."""
        if not (0 <= int(volume) <= 100):
            raise ValueError(f"Volume must be between 0 and 100, got {volume}")
        return self._is_ok(await self.send_command("change_sound_volume", [int(volume)]))

    async def test_sound_volume(self) -> bool:
        """Play a sound to test the currently configured voice volume (test_sound_volume)."""
        return self._is_ok(await self.send_command("test_sound_volume"))

    async def set_led_status(self, enabled: bool) -> bool:
        """Enable or disable the top status indicator LED (set_led_status)."""
        return self._is_ok(await self.send_command("set_led_status", [1 if enabled else 0]))

    async def set_dnd_timer(
        self,
        start_hour: int,
        start_minute: int,
        end_hour: int,
        end_minute: int,
    ) -> bool:
        """Enable and configure Do Not Disturb window (set_dnd_timer)."""
        for h in (start_hour, end_hour):
            if not (0 <= int(h) <= 23):
                raise ValueError(f"Hour must be between 0 and 23, got {h}")
        for m in (start_minute, end_minute):
            if not (0 <= int(m) <= 59):
                raise ValueError(f"Minute must be between 0 and 59, got {m}")
        params = [int(start_hour), int(start_minute), int(end_hour), int(end_minute)]
        return self._is_ok(await self.send_command("set_dnd_timer", params))

    async def disable_dnd_timer(self) -> bool:
        """Disable Do Not Disturb mode (close_dnd_timer)."""
        return self._is_ok(await self.send_command("close_dnd_timer"))

    async def set_carpet_mode(self, enabled: bool | CarpetMode) -> bool:
        """Enable or disable Carpet Boost mode (set_carpet_mode)."""
        cfg = enabled if isinstance(enabled, CarpetMode) else CarpetMode(enabled=bool(enabled))
        return self._is_ok(await self.send_command("set_carpet_mode", [cfg.to_miio_dict()]))

    async def reset_consumable(self, consumable_key: str) -> bool:
        """Reset wear counter for a specific consumable part (reset_consumable)."""
        if consumable_key not in CONSUMABLE_LIMITS_SECONDS:
            raise ValueError(
                f"Invalid consumable '{consumable_key}'. "
                f"Valid keys: {sorted(CONSUMABLE_LIMITS_SECONDS)}"
            )
        return self._is_ok(await self.send_command("reset_consumable", [consumable_key]))

    async def remote_control_start(self) -> bool:
        """Start manual remote control session (app_rc_start)."""
        return self._is_ok(await self.send_command("app_rc_start"))

    async def remote_control_move(
        self,
        velocity: float,
        omega: float,
        duration_ms: int = 1500,
        seqnum: int = 1,
    ) -> bool:
        """Send manual movement vector during active remote control session (app_rc_move)."""
        if not (-0.3 <= velocity <= 0.3):
            raise ValueError(f"velocity must be between -0.3 and 0.3 m/s, got {velocity}")
        if not (-3.15 <= omega <= 3.15):
            raise ValueError(f"omega must be between -3.15 and 3.15 rad/s, got {omega}")
        payload = [
            {
                "omega": round(float(omega), 3),
                "velocity": round(float(velocity), 3),
                "duration": int(duration_ms),
                "seqnum": int(seqnum),
            }
        ]
        return self._is_ok(await self.send_command("app_rc_move", payload))

    async def remote_control_end(self) -> bool:
        """End manual remote control session (app_rc_end)."""
        return self._is_ok(await self.send_command("app_rc_end"))

    # --- Phase 7: Local Map Editing Methods (Rooms, Virtual Walls, Forbidden Zones) ---

    async def set_lab_status(self, enabled: bool = True) -> bool:
        """Enable or disable map saving / Lab Mode (set_lab_status)."""
        return self._is_ok(await self.send_command("set_lab_status", [1 if enabled else 0]))

    async def merge_segments(self, segment_a: int, segment_b: int) -> bool:
        """Merge two adjacent room segment IDs on the active floor map (merge_segment)."""
        a, b = int(segment_a), int(segment_b)
        if a <= 0 or b <= 0 or a == b:
            raise ValueError(
                f"merge_segments requires two distinct positive segment IDs, got {a}, {b}"
            )
        res = await self.send_command("merge_segment", [a, b])
        return self._is_ok(res) or (isinstance(res, list) and len(res) > 0)

    async def split_segment(
        self,
        segment_id: int,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
    ) -> bool:
        """Split a room segment along a dividing line (x1, y1) -> (x2, y2) in mm (split_segment)."""
        seg = int(segment_id)
        if seg <= 0:
            raise ValueError(f"segment_id must be a positive integer, got {seg}")
        if x1 == x2 and y1 == y2:
            raise ValueError("Splitting line endpoints must be distinct")
        params = [seg, int(x1), int(y1), int(x2), int(y2)]
        res = await self.send_command("split_segment", params)
        return self._is_ok(res) or (isinstance(res, list) and len(res) > 0)

    async def save_map_restrictions(
        self,
        virtual_walls: list[tuple[int, int, int, int]] | None = None,
        forbidden_zones: list[tuple[int, int, int, int, int, int, int, int]] | None = None,
        no_mop_zones: list[tuple[int, int, int, int, int, int, int, int]] | None = None,
    ) -> bool:
        """Commit virtual walls (0), no-go zones (1), and no-mop zones (2) via save_map.

        IMPORTANT: Because `save_map` overwrites all walls/zones on the active floor, callers
        should use `MapRestrictionEditor.from_parsed_map()` to preserve existing boundaries.
        """
        payload: list[list[int]] = []
        for x1, y1, x2, y2 in virtual_walls or []:
            payload.append([0, int(x1), int(y1), int(x2), int(y2)])
        for z in forbidden_zones or []:
            if len(z) != 8:
                raise ValueError(f"Forbidden zone must have 8 coordinates, got {len(z)}")
            payload.append([1, *[int(c) for c in z]])
        for mz in no_mop_zones or []:
            if len(mz) != 8:
                raise ValueError(f"No-mop zone must have 8 coordinates, got {len(mz)}")
            payload.append([2, *[int(c) for c in mz]])
        res = await self.send_command("save_map", payload)
        return self._is_ok(res) or (isinstance(res, list) and len(res) > 0)

    async def delete_floor_map(self, map_flag: int) -> bool:
        """Delete a stored multi-floor map slot (0..3) from the vacuum's memory (del_map)."""
        slot = int(map_flag)
        if not (0 <= slot <= 3):
            raise ValueError(f"map_flag must be between 0 and 3, got {slot}")
        res = await self.send_command("del_map", [slot])
        return self._is_ok(res) or (isinstance(res, list) and len(res) > 0)

    async def reset_active_map(self) -> bool:
        """Clear/reset the currently active map to force full re-mapping (reset_map)."""
        res = await self.send_command("reset_map", [])
        return self._is_ok(res) or (isinstance(res, list) and len(res) > 0)

    # Keep DndTimer referenced for export hygiene
    _DND_MODEL = DndTimer


    async def get_dnd_timer(self) -> dict[str, Any]:
        """Fetch Do Not Disturb settings via get_dnd_timer."""
        res = await self.send_command("get_dnd_timer")
        if isinstance(res, list) and res and isinstance(res[0], dict):
            return res[0]
        return {"raw": res}

    async def get_carpet_mode(self) -> dict[str, Any]:
        """Fetch Carpet Boost configuration via get_carpet_mode."""
        res = await self.send_command("get_carpet_mode")
        if isinstance(res, list) and res and isinstance(res[0], dict):
            return res[0]
        return {"raw": res}

    async def get_sound_volume(self) -> int | None:
        """Fetch voice volume percentage (0-100) via get_sound_volume."""
        res = await self.send_command("get_sound_volume")
        if isinstance(res, list) and res:
            return int(res[0])
        return None
