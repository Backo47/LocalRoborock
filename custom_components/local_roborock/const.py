"""Constants and enumerations for Roborock S6 (roborock.vacuum.s6)."""

from enum import IntEnum

DOMAIN = "local_roborock"
DEFAULT_NAME = "Roborock S6"
DEFAULT_PORT = 54321
DEFAULT_TIMEOUT = 5.0
DEFAULT_SCAN_INTERVAL = 15
MIIO_MAGIC = 0x2131
HEADER_SIZE = 32

# Config entry keys
CONF_HOST = "host"
CONF_TOKEN = "token"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_XIAOMI_USER_ID = "xiaomi_user_id"
CONF_XIAOMI_SERVICE_TOKEN = "xiaomi_service_token"
CONF_XIAOMI_SSECURITY = "xiaomi_ssecurity"
CONF_XIAOMI_COUNTRY = "xiaomi_country"

# Generic default floor and room names used before first live sync from vacuum
DEFAULT_FLOORS_CONFIG: dict[str, list[dict[str, object]]] = {
    "floors": [
        {
            "map_flag": 0,
            "name": "Floor 0",
            "rooms": [
                {"segment_id": 16, "iot_id": "", "name": "Room 16"},
                {"segment_id": 17, "iot_id": "", "name": "Room 17"},
                {"segment_id": 18, "iot_id": "", "name": "Room 18"},
            ],
        },
        {
            "map_flag": 1,
            "name": "Floor 1",
            "rooms": [
                {"segment_id": 16, "iot_id": "", "name": "Room 16"},
                {"segment_id": 17, "iot_id": "", "name": "Room 17"},
                {"segment_id": 18, "iot_id": "", "name": "Room 18"},
            ],
        },
    ]
}


class RoborockState(IntEnum):
    """Operational state codes returned by get_status['state']."""

    UNKNOWN = 0
    STARTING = 1
    CHARGER_DISCONNECTED = 2
    IDLE = 3
    REMOTE_CONTROL_ACTIVE = 4
    CLEANING = 5
    RETURNING_HOME = 6
    MANUAL_MODE = 7
    CHARGING = 8
    CHARGING_PROBLEM = 9
    PAUSED = 10
    SPOT_CLEANING = 11
    ERROR = 12
    SHUTTING_DOWN = 13
    UPDATING = 14
    DOCKING = 15
    GOING_TO_TARGET = 16
    ZONED_CLEANING = 17
    SEGMENT_CLEANING = 18
    RETURNING_HOME_EMPTYING = 22
    EMPTYING_THE_BIN = 23
    CHARGING_COMPLETE = 100
    DEVICE_OFFLINE = 101


STATE_DESCRIPTIONS: dict[int, str] = {
    1: "Starting",
    2: "Sleeping / Charger disconnected",
    3: "Idle",
    4: "Remote control active",
    5: "Cleaning",
    6: "Returning to dock",
    7: "Manual mode",
    8: "Charging",
    9: "Charging problem",
    10: "Paused",
    11: "Spot cleaning",
    12: "Error",
    13: "Shutting down",
    14: "Updating firmware",
    15: "Docking",
    16: "Going to target",
    17: "Zoned cleaning",
    18: "Room (segment) cleaning",
    22: "Emptying dustbin",
    23: "Emptying dustbin",
    100: "Charging complete",
    101: "Device offline",
}


class RoborockFanPower(IntEnum):
    """Fan power settings for Roborock S6."""

    SILENT = 101
    BALANCED = 102
    TURBO = 103
    MAX = 104
    GENTLE = 105


FAN_POWER_DESCRIPTIONS: dict[int, str] = {
    101: "Quiet (101)",
    102: "Balanced (102)",
    103: "Turbo (103)",
    104: "Max (104)",
    105: "Gentle / Mop (105)",
}


ERROR_DESCRIPTIONS: dict[int, str] = {
    0: "No error",
    1: "Laser distance sensor (LDS) error",
    2: "Collision bumper stuck",
    3: "Wheels on top of void / suspended",
    4: "Cliff sensor dirty or error",
    5: "Main brush tangled",
    6: "Side brush tangled",
    7: "Main wheels jammed",
    8: "Device stuck / trapped",
    9: "Dustbin missing",
    10: "Filter dirty or wet",
    11: "Intense magnetic field detected",
    12: "Low battery",
    13: "Charging fault",
    14: "Battery fault",
    15: "Wall sensor dirty",
    16: "Tilted / uneven surface",
    17: "Side brush module error",
    18: "Suction fan error",
    19: "Unpowered charging station",
    21: "Laser sensor vertical bumper pressed",
    22: "Dock locator sensor dirty",
    23: "Charging dock beacon obscured",
    24: "No-go zone or virtual wall detected",
}

# Consumable life limits in seconds
CONSUMABLE_LIMITS_SECONDS: dict[str, int] = {
    "main_brush_work_time": 300 * 3600,     # 300 hours
    "side_brush_work_time": 200 * 3600,     # 200 hours
    "filter_work_time": 150 * 3600,         # 150 hours
    "filter_element_work_time": 100 * 3600, # 100 hours (water filter element)
    "sensor_dirty_time": 30 * 3600,         # 30 hours
}

# Known Roborock firmware feature flags returned by get_fw_features
FW_FEATURE_DESCRIPTIONS: dict[int, str] = {
    111: "Support new clean history record format (v2)",
    112: "Support segment (room) cleaning & room mapping",
    113: "Support clean repeat count per segment/zone",
    114: "Support multi-floor map backup & management",
    115: "Support room naming / segment editing",
    116: "Support No-Mop forbidden zones (mop_forbidden_enable)",
    117: "Support map auto-partitioning",
    118: "Support LED status indicator control (get_led_status / set_led_status)",
    119: "Support room-specific fan power customization",
    120: "Support multi-floor auto-recognition (max 4 maps)",
    122: "Support customized cleaning sequence",
    123: "Support timer segment cleaning",
    124: "Support smart recharge (top-up only required battery to finish clean)",
    125: "Support valley electricity charging / DND enhancements",
}

