"""Sensor entities for Local Roborock S6 telemetry, consumables, and cleaning history."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .coordinator import RoborockCoordinatorData
from .entity import LocalRoborockEntity

try:
    from homeassistant.components.sensor import (
        SensorDeviceClass,
        SensorEntity,
        SensorStateClass,
    )
    from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfArea, UnitOfTime
except ImportError:

    class SensorEntity:  # type: ignore[no-redef]
        pass

    class SensorDeviceClass:  # type: ignore[no-redef]
        BATTERY = "battery"
        DURATION = "duration"
        SIGNAL_STRENGTH = "signal_strength"

    class SensorStateClass:  # type: ignore[no-redef]
        MEASUREMENT = "measurement"
        TOTAL_INCREASING = "total_increasing"

    class EntityCategory:  # type: ignore[no-redef]
        DIAGNOSTIC = "diagnostic"

    PERCENTAGE = "%"
    UnitOfArea = type("UnitOfArea", (), {"SQUARE_METERS": "m²"})
    UnitOfTime = type("UnitOfTime", (), {"MINUTES": "min", "HOURS": "h"})


@dataclass(frozen=True)
class RoborockSensorSpec:
    """Specification for a Local Roborock S6 sensor entity."""

    key: str
    name: str
    icon: str
    unit: str | None
    device_class: Any | None
    state_class: Any | None
    entity_category: Any | None
    value_fn: Callable[[RoborockCoordinatorData], Any]


SENSOR_SPECS: tuple[RoborockSensorSpec, ...] = (
    RoborockSensorSpec(
        key="battery",
        name="Battery",
        icon="mdi:battery",
        unit=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        value_fn=lambda d: d.status.battery,
    ),
    RoborockSensorSpec(
        key="status",
        name="Status",
        icon="mdi:robot-vacuum",
        unit=None,
        device_class=None,
        state_class=None,
        entity_category=None,
        value_fn=lambda d: d.status.state_name,
    ),
    RoborockSensorSpec(
        key="error",
        name="Error",
        icon="mdi:alert-circle-outline",
        unit=None,
        device_class=None,
        state_class=None,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.status.error_name,
    ),
    RoborockSensorSpec(
        key="clean_area",
        name="Current Clean Area",
        icon="mdi:texture-box",
        unit=UnitOfArea.SQUARE_METERS,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        value_fn=lambda d: d.status.clean_area_m2,
    ),
    RoborockSensorSpec(
        key="clean_time",
        name="Current Clean Duration",
        icon="mdi:timer-outline",
        unit=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        value_fn=lambda d: d.status.clean_time_min,
    ),
    RoborockSensorSpec(
        key="last_clean_area",
        name="Last Clean Area",
        icon="mdi:vector-square",
        unit=UnitOfArea.SQUARE_METERS,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        value_fn=lambda d: (
            d.last_clean_record.area_m2 if d.last_clean_record else d.status.clean_area_m2
        ),
    ),
    RoborockSensorSpec(
        key="last_clean_duration",
        name="Last Clean Duration",
        icon="mdi:clock-check-outline",
        unit=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        value_fn=lambda d: (
            d.last_clean_record.duration_min
            if d.last_clean_record
            else d.status.clean_time_min
        ),
    ),
    RoborockSensorSpec(
        key="total_clean_area",
        name="Total Cleaned Area",
        icon="mdi:floor-plan",
        unit=UnitOfArea.SQUARE_METERS,
        device_class=None,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.clean_summary.get("clean_area_m2", 0.0),
    ),
    RoborockSensorSpec(
        key="total_clean_time",
        name="Total Cleaning Time",
        icon="mdi:history",
        unit=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.clean_summary.get("clean_time_hours", 0.0),
    ),
    RoborockSensorSpec(
        key="total_clean_count",
        name="Total Clean Count",
        icon="mdi:counter",
        unit="cleans",
        device_class=None,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.clean_summary.get("clean_count", 0),
    ),
    RoborockSensorSpec(
        key="main_brush_left",
        name="Main Brush Remaining",
        icon="mdi:brush",
        unit=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.consumables.remaining_hours("main_brush_work_time"),
    ),
    RoborockSensorSpec(
        key="side_brush_left",
        name="Side Brush Remaining",
        icon="mdi:Loading",
        unit=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.consumables.remaining_hours("side_brush_work_time"),
    ),
    RoborockSensorSpec(
        key="filter_left",
        name="Filter Remaining",
        icon="mdi:air-filter",
        unit=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.consumables.remaining_hours("filter_work_time"),
    ),
    RoborockSensorSpec(
        key="sensor_left",
        name="Sensors Clean Remaining",
        icon="mdi:eye-outline",
        unit=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.consumables.remaining_hours("sensor_dirty_time"),
    ),
    RoborockSensorSpec(
        key="wifi_rssi",
        name="Wi-Fi Signal",
        icon="mdi:wifi",
        unit="dBm",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.device_info.rssi,
    ),
)


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up Local Roborock S6 sensor entities."""
    coordinator = hass.data["local_roborock"][entry.entry_id]
    async_add_entities(
        [LocalRoborockSensorEntity(coordinator, entry.entry_id, spec) for spec in SENSOR_SPECS]
    )


class LocalRoborockSensorEntity(LocalRoborockEntity, SensorEntity):
    """Representation of a Local Roborock S6 sensor."""

    def __init__(self, coordinator: Any, entry_id: str, spec: RoborockSensorSpec) -> None:
        super().__init__(coordinator, entry_id, f"sensor_{spec.key}")
        self.spec = spec
        self._attr_name = spec.name
        self._attr_icon = spec.icon
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_device_class = spec.device_class
        self._attr_state_class = spec.state_class
        self._attr_entity_category = spec.entity_category

    @property
    def native_value(self) -> Any:
        return self.spec.value_fn(self.data)
