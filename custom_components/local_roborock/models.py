"""Data models for Roborock S6 responses."""

from dataclasses import dataclass
from typing import Any

from .const import (
    CONSUMABLE_LIMITS_SECONDS,
    ERROR_DESCRIPTIONS,
    FAN_POWER_DESCRIPTIONS,
    STATE_DESCRIPTIONS,
)


@dataclass(frozen=True)
class DeviceInfo:
    """System information returned by miIO.info."""

    model: str
    fw_ver: str
    hw_ver: str
    mac: str
    life: int
    ssid: str | None
    rssi: int | None
    ip: str | None
    netmask: str | None
    gw: str | None
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeviceInfo":
        ap = data.get("ap") or {}
        netif = data.get("netif") or {}
        return cls(
            model=str(data.get("model", "unknown")),
            fw_ver=str(data.get("fw_ver", "unknown")),
            hw_ver=str(data.get("hw_ver", "unknown")),
            mac=str(data.get("mac", "unknown")),
            life=int(data.get("life", 0)),
            ssid=ap.get("ssid"),
            rssi=ap.get("rssi"),
            ip=netif.get("localIp"),
            netmask=netif.get("mask"),
            gw=netif.get("gw"),
            raw=data,
        )


@dataclass(frozen=True)
class VacuumStatus:
    """Telemetry status returned by get_status."""

    msg_ver: int
    msg_seq: int
    state: int
    battery: int
    clean_time_sec: int
    clean_area_raw: int
    error_code: int
    map_present: bool
    in_cleaning: int
    in_returning: int
    in_fresh_state: int
    lab_status: int
    water_box_status: bool | None
    fan_power: int
    dnd_enabled: bool
    map_status: int
    is_locating: bool
    lock_status: bool
    mop_forbidden_enable: bool
    debug_mode: int
    raw: dict[str, Any]

    @property
    def state_name(self) -> str:
        return STATE_DESCRIPTIONS.get(self.state, f"Unknown ({self.state})")

    @property
    def fan_power_name(self) -> str:
        return FAN_POWER_DESCRIPTIONS.get(self.fan_power, f"Custom ({self.fan_power})")

    @property
    def error_name(self) -> str:
        return ERROR_DESCRIPTIONS.get(self.error_code, f"Error {self.error_code}")

    @property
    def clean_area_m2(self) -> float:
        """Convert raw area (mm² on FW 3.5.8+) to square meters (m²)."""
        return round(self.clean_area_raw / 1_000_000.0, 2)

    @property
    def clean_time_min(self) -> float:
        return round(self.clean_time_sec / 60.0, 1)

    @property
    def active_map_slot(self) -> int:
        return self.map_status >> 2

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VacuumStatus":
        wb = data.get("water_box_status")
        return cls(
            msg_ver=int(data.get("msg_ver", 0)),
            msg_seq=int(data.get("msg_seq", 0)),
            state=int(data.get("state", 0)),
            battery=int(data.get("battery", 0)),
            clean_time_sec=int(data.get("clean_time", 0)),
            clean_area_raw=int(data.get("clean_area", 0)),
            error_code=int(data.get("error_code", 0)),
            map_present=bool(data.get("map_present", 0)),
            in_cleaning=int(data.get("in_cleaning", 0)),
            in_returning=int(data.get("in_returning", 0)),
            in_fresh_state=int(data.get("in_fresh_state", 0)),
            lab_status=int(data.get("lab_status", 0)),
            water_box_status=bool(wb) if wb is not None else None,
            fan_power=int(data.get("fan_power", 0)),
            dnd_enabled=bool(data.get("dnd_enabled", 0)),
            map_status=int(data.get("map_status", 0)),
            is_locating=bool(data.get("is_locating", 0)),
            lock_status=bool(data.get("lock_status", 0)),
            mop_forbidden_enable=bool(data.get("mop_forbidden_enable", 0)),
            debug_mode=int(data.get("debug_mode", 0)),
            raw=data,
        )


@dataclass(frozen=True)
class CleanRecord:
    """Detailed record of a single past cleaning run returned by get_clean_record."""

    start_timestamp: int
    end_timestamp: int
    duration_sec: int
    area_raw: int
    error_code: int
    completed: bool
    start_type: int
    clean_type: int
    finish_reason: int
    raw: list[Any] | dict[str, Any]

    @property
    def area_m2(self) -> float:
        return round(self.area_raw / 1_000_000.0, 2)

    @property
    def duration_min(self) -> float:
        return round(self.duration_sec / 60.0, 1)

    @classmethod
    def from_raw(cls, data: Any) -> "CleanRecord":
        if isinstance(data, list) and data and isinstance(data[0], list):
            row = data[0]
        elif isinstance(data, list):
            row = data
        elif isinstance(data, dict):
            return cls(
                start_timestamp=int(data.get("begin", 0)),
                end_timestamp=int(data.get("end", 0)),
                duration_sec=int(data.get("duration", 0)),
                area_raw=int(data.get("area", 0)),
                error_code=int(data.get("error", 0)),
                completed=bool(data.get("complete", 0)),
                start_type=int(data.get("start_type", 0)),
                clean_type=int(data.get("clean_type", 0)),
                finish_reason=int(data.get("finish_reason", 0)),
                raw=data,
            )
        else:
            row = []

        return cls(
            start_timestamp=int(row[0]) if len(row) > 0 else 0,
            end_timestamp=int(row[1]) if len(row) > 1 else 0,
            duration_sec=int(row[2]) if len(row) > 2 else 0,
            area_raw=int(row[3]) if len(row) > 3 else 0,
            error_code=int(row[4]) if len(row) > 4 else 0,
            completed=bool(row[5]) if len(row) > 5 else False,
            start_type=int(row[6]) if len(row) > 6 else 0,
            clean_type=int(row[7]) if len(row) > 7 else 0,
            finish_reason=int(row[8]) if len(row) > 8 else 0,
            raw=row,
        )


@dataclass(frozen=True)
class ConsumableStatus:
    """Consumable wear timers returned by get_consumable."""

    main_brush_work_time: int
    side_brush_work_time: int
    filter_work_time: int
    filter_element_work_time: int
    sensor_dirty_time: int
    raw: dict[str, Any]

    def remaining_seconds(self, key: str) -> int:
        limit = CONSUMABLE_LIMITS_SECONDS.get(key, 0)
        used = int(getattr(self, key, 0))
        return max(0, limit - used)

    def remaining_hours(self, key: str) -> float:
        return round(self.remaining_seconds(key) / 3600.0, 1)

    def remaining_percent(self, key: str) -> int:
        limit = CONSUMABLE_LIMITS_SECONDS.get(key, 1)
        return max(0, min(100, round((self.remaining_seconds(key) / limit) * 100)))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConsumableStatus":
        return cls(
            main_brush_work_time=int(data.get("main_brush_work_time", 0)),
            side_brush_work_time=int(data.get("side_brush_work_time", 0)),
            filter_work_time=int(data.get("filter_work_time", 0)),
            filter_element_work_time=int(data.get("filter_element_work_time", 0)),
            sensor_dirty_time=int(data.get("sensor_dirty_time", 0)),
            raw=data,
        )


@dataclass(frozen=True)
class RoomMapping:
    """Pair of local segment ID, Xiaomi IoT room ID string, and optional local name."""

    segment_id: int
    iot_id: str
    name: str | None = None


@dataclass(frozen=True)
class ZoneCoordinates:
    """Cleaning zone rectangle in Roborock millimeter coordinates ([25600, 25600] = dock)."""

    x1: int
    y1: int
    x2: int
    y2: int
    iterations: int = 1

    def validate(self) -> None:
        if not (1 <= self.iterations <= 3):
            raise ValueError(f"Zone iterations must be between 1 and 3, got {self.iterations}")
        if self.x1 == self.x2 or self.y1 == self.y2:
            raise ValueError("Zone width and height must be non-zero")

    def to_miio_list(self) -> list[int]:
        self.validate()
        return [
            min(self.x1, self.x2),
            min(self.y1, self.y2),
            max(self.x1, self.x2),
            max(self.y1, self.y2),
            self.iterations,
        ]


@dataclass(frozen=True)
class DndTimer:
    """Do Not Disturb schedule returned by get_dnd_timer."""

    start_hour: int
    start_minute: int
    end_hour: int
    end_minute: int
    enabled: bool

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DndTimer":
        return cls(
            start_hour=int(data.get("start_hour", 22)),
            start_minute=int(data.get("start_minute", 0)),
            end_hour=int(data.get("end_hour", 8)),
            end_minute=int(data.get("end_minute", 0)),
            enabled=bool(data.get("enabled", 0)),
        )


@dataclass(frozen=True)
class CarpetMode:
    """Carpet boost configuration returned by get_carpet_mode."""

    enabled: bool
    current_integral: int = 450
    current_high: int = 500
    current_low: int = 400
    stall_time: int = 10

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CarpetMode":
        return cls(
            enabled=bool(data.get("enable", 0)),
            current_integral=int(data.get("current_integral", 450)),
            current_high=int(data.get("current_high", 500)),
            current_low=int(data.get("current_low", 400)),
            stall_time=int(data.get("stall_time", 10)),
        )

    def to_miio_dict(self) -> dict[str, int]:
        return {
            "enable": 1 if self.enabled else 0,
            "current_integral": self.current_integral,
            "current_high": self.current_high,
            "current_low": self.current_low,
            "stall_time": self.stall_time,
        }


@dataclass(frozen=True)
class MapFloorInfo:
    """Saved multi-floor map entry returned by get_multi_maps_list."""

    map_flag: int
    name: str
    add_time: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MapFloorInfo":
        return cls(
            map_flag=int(data.get("mapFlag", 0)),
            name=str(data.get("name", "")).strip() or f"Map {data.get('mapFlag', 0)}",
            add_time=int(data.get("add_time", 0)),
        )

