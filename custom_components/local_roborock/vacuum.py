"""StateVacuumEntity implementation for Local Roborock S6."""

from typing import Any

from .const import RoborockFanPower, RoborockState
from .entity import LocalRoborockEntity
from .models import ZoneCoordinates

try:
    from homeassistant.components.vacuum import (
        StateVacuumEntity,
        VacuumActivity,
        VacuumEntityFeature,
    )
except ImportError:

    class VacuumActivity:  # type: ignore[no-redef]
        CLEANING = "cleaning"
        DOCKED = "docked"
        IDLE = "idle"
        PAUSED = "paused"
        RETURNING = "returning"
        ERROR = "error"

    class VacuumEntityFeature:  # type: ignore[no-redef]
        START = 1
        STOP = 2
        PAUSE = 4
        RETURN_HOME = 8
        FAN_SPEED = 16
        LOCATE = 32
        CLEAN_SPOT = 64
        SEND_COMMAND = 128
        STATE = 256
        BATTERY = 512

    class StateVacuumEntity:  # type: ignore[no-redef]
        pass


SUPPORTED_VACUUM_FEATURES = (
    VacuumEntityFeature.START
    | VacuumEntityFeature.STOP
    | VacuumEntityFeature.PAUSE
    | VacuumEntityFeature.RETURN_HOME
    | VacuumEntityFeature.FAN_SPEED
    | VacuumEntityFeature.LOCATE
    | VacuumEntityFeature.CLEAN_SPOT
    | VacuumEntityFeature.SEND_COMMAND
    | getattr(VacuumEntityFeature, "STATE", 0)
    | getattr(VacuumEntityFeature, "BATTERY", 0)
)


FAN_SPEED_TO_CODE: dict[str, int] = {
    "Quiet": int(RoborockFanPower.SILENT),
    "Balanced": int(RoborockFanPower.BALANCED),
    "Turbo": int(RoborockFanPower.TURBO),
    "Max": int(RoborockFanPower.MAX),
    "Gentle": int(RoborockFanPower.GENTLE),
}

CODE_TO_FAN_SPEED: dict[int, str] = {v: k for k, v in FAN_SPEED_TO_CODE.items()}


def map_roborock_state_to_activity(state_code: int) -> Any:
    """Map Roborock S6 integer state code to Home Assistant VacuumActivity."""
    if state_code in (
        RoborockState.CLEANING,
        RoborockState.SPOT_CLEANING,
        RoborockState.ZONED_CLEANING,
        RoborockState.SEGMENT_CLEANING,
        RoborockState.GOING_TO_TARGET,
        RoborockState.REMOTE_CONTROL_ACTIVE,
        RoborockState.MANUAL_MODE,
    ):
        return VacuumActivity.CLEANING
    if state_code in (
        RoborockState.CHARGING,
        RoborockState.CHARGING_COMPLETE,
        RoborockState.EMPTYING_THE_BIN,
    ):
        return VacuumActivity.DOCKED
    if state_code in (
        RoborockState.RETURNING_HOME,
        RoborockState.DOCKING,
        RoborockState.RETURNING_HOME_EMPTYING,
    ):
        return VacuumActivity.RETURNING
    if state_code == RoborockState.PAUSED:
        return VacuumActivity.PAUSED
    if state_code in (RoborockState.ERROR, RoborockState.CHARGING_PROBLEM):
        return VacuumActivity.ERROR
    return VacuumActivity.IDLE


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up the Roborock S6 vacuum entity."""
    coordinator = hass.data["local_roborock"][entry.entry_id]
    async_add_entities([LocalRoborockVacuumEntity(coordinator, entry.entry_id)])


class LocalRoborockVacuumEntity(LocalRoborockEntity, StateVacuumEntity):
    """Representation of the Roborock S6 vacuum cleaner."""

    _attr_name = None
    _attr_fan_speed_list = list(FAN_SPEED_TO_CODE.keys())
    _attr_supported_features = SUPPORTED_VACUUM_FEATURES

    def __init__(self, coordinator: Any, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "vacuum")

    @property
    def activity(self) -> Any:
        return map_roborock_state_to_activity(self.data.status.state)

    @property
    def battery_level(self) -> int:
        return self.data.status.battery

    @property
    def fan_speed(self) -> str | None:
        return CODE_TO_FAN_SPEED.get(self.data.status.fan_power, "Balanced")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        st = self.data.status
        active_slot = st.active_map_slot
        floor_cfg = self.coordinator.runtime.room_store.floors.get(active_slot)
        rooms_attr = (
            {r.segment_id: r.name for r in floor_cfg.rooms.values()} if floor_cfg else {}
        )
        return {
            "roborock_state": st.state_name,
            "error_code": st.error_code,
            "error_description": st.error_name,
            "clean_area_m2": st.clean_area_m2,
            "clean_time_min": st.clean_time_min,
            "active_map_slot": active_slot,
            "active_floor_name": floor_cfg.name if floor_cfg else f"Map {active_slot}",
            "floor_rooms": rooms_attr,
            "water_box_attached": st.water_box_status,
            "dnd_enabled": st.dnd_enabled,
        }

    async def async_start(self) -> None:
        await self.coordinator.client.start_cleaning()
        await self.coordinator.async_request_refresh()

    async def async_stop(self, **kwargs: Any) -> None:
        await self.coordinator.client.stop_cleaning()
        await self.coordinator.async_request_refresh()

    async def async_pause(self) -> None:
        await self.coordinator.client.pause_cleaning()
        await self.coordinator.async_request_refresh()

    async def async_return_to_base(self, **kwargs: Any) -> None:
        await self.coordinator.client.return_to_dock()
        await self.coordinator.async_request_refresh()

    async def async_clean_spot(self, **kwargs: Any) -> None:
        await self.coordinator.client.spot_clean()
        await self.coordinator.async_request_refresh()

    async def async_locate(self, **kwargs: Any) -> None:
        await self.coordinator.client.find_me()

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        code = FAN_SPEED_TO_CODE.get(fan_speed)
        if code is None:
            # Also accept case-insensitive match or numeric string
            for k, v in FAN_SPEED_TO_CODE.items():
                if k.lower() == fan_speed.strip().lower() or str(v) == fan_speed.strip():
                    code = v
                    break
        if code is None:
            raise ValueError(f"Unsupported fan speed: {fan_speed}")
        await self.coordinator.client.set_fan_power(code)
        await self.coordinator.async_request_refresh()

    async def async_send_command(
        self,
        command: str,
        params: dict[str, Any] | list[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Execute raw miIO command or convenient high-level aliases from map cards."""
        if command == "app_segment_clean" and isinstance(params, list) and params:
            # Compatible with both [16, 17] and [{"segments": [16, 17], "repeat": 2}]
            if isinstance(params[0], int):
                await self.coordinator.client.segment_clean([int(x) for x in params], repeat=1)
            else:
                await self.coordinator.client.send_command(command, params)
        elif command == "app_zoned_clean" and isinstance(params, list) and params:
            zones = [
                ZoneCoordinates(
                    x1=int(z[0]),
                    y1=int(z[1]),
                    x2=int(z[2]),
                    y2=int(z[3]),
                    iterations=int(z[4]) if len(z) > 4 else 1,
                )
                for z in params
                if isinstance(z, list) and len(z) >= 4
            ]
            await self.coordinator.client.zoned_clean(zones)
        else:
            await self.coordinator.client.send_command(command, params)
        await self.coordinator.async_request_refresh()
