"""Local storage for multi-floor room mappings and user-friendly room names."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .const import DEFAULT_FLOORS_CONFIG
from .models import RoomMapping


@dataclass
class FloorRoomConfig:
    """Configuration for a single multi-map floor slot (e.g. mapFlag 0 or 1)."""

    map_flag: int
    name: str
    rooms: dict[int, RoomMapping] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "map_flag": self.map_flag,
            "name": self.name,
            "rooms": [
                {
                    "segment_id": r.segment_id,
                    "iot_id": r.iot_id,
                    "name": r.name or f"Room {r.segment_id}",
                }
                for r in sorted(self.rooms.values(), key=lambda x: x.segment_id)
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FloorRoomConfig":
        map_flag = int(data.get("map_flag", 0))
        name = str(data.get("name", f"Map {map_flag}"))
        rooms: dict[int, RoomMapping] = {}
        for item in data.get("rooms", []):
            if isinstance(item, dict) and "segment_id" in item:
                seg_id = int(item["segment_id"])
                rooms[seg_id] = RoomMapping(
                    segment_id=seg_id,
                    iot_id=str(item.get("iot_id", "")),
                    name=str(item.get("name", f"Room {seg_id}")),
                )
        return cls(map_flag=map_flag, name=name, rooms=rooms)


class RoomStore:
    """Manages local persistence of floor and room segment mappings (rooms.json)."""

    def __init__(self, path: str | Path = "rooms.json") -> None:
        self.path = Path(path)
        self.floors: dict[int, FloorRoomConfig] = {}

    def load(self, seed_defaults: bool = False) -> None:
        """Load room definitions from disk; optionally seed default S6 rooms if missing."""
        if not self.path.is_file():
            if seed_defaults:
                self._load_from_dict(DEFAULT_FLOORS_CONFIG)
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self.save()
                except OSError:
                    pass
            else:
                self.floors = {}
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self._load_from_dict(raw)

    def _load_from_dict(self, raw: dict[str, Any]) -> None:
        floors: dict[int, FloorRoomConfig] = {}
        for item in raw.get("floors", []):
            if isinstance(item, dict):
                cfg = FloorRoomConfig.from_dict(item)
                floors[cfg.map_flag] = cfg
        self.floors = floors

    def save(self) -> None:
        """Persist current floor and room definitions to disk."""
        payload = {
            "floors": [
                cfg.to_dict()
                for _, cfg in sorted(self.floors.items(), key=lambda pair: pair[0])
            ]
        }
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def update_floor_mappings(
        self,
        map_flag: int,
        floor_name: str,
        mappings: list[RoomMapping],
    ) -> FloorRoomConfig:
        """Merge fresh room mappings from the vacuum while preserving user-assigned room names."""
        existing_floor = self.floors.get(map_flag)
        merged_rooms: dict[int, RoomMapping] = {}
        for rm in mappings:
            existing_room = existing_floor.rooms.get(rm.segment_id) if existing_floor else None
            custom_name = (
                existing_room.name
                if existing_room and existing_room.name
                else (rm.name or f"Room {rm.segment_id}")
            )
            merged_rooms[rm.segment_id] = RoomMapping(
                segment_id=rm.segment_id,
                iot_id=rm.iot_id,
                name=custom_name,
            )
        resolved_name = floor_name.strip() or (
            existing_floor.name if existing_floor else f"Map {map_flag}"
        )
        floor_cfg = FloorRoomConfig(
            map_flag=map_flag,
            name=resolved_name,
            rooms=merged_rooms,
        )
        self.floors[map_flag] = floor_cfg
        return floor_cfg

    def rename_room(self, map_flag: int, segment_id: int, new_name: str) -> RoomMapping:
        """Assign a human-readable name to a room segment on a given floor."""
        if map_flag not in self.floors:
            self.floors[map_flag] = FloorRoomConfig(map_flag=map_flag, name=f"Map {map_flag}")
        floor = self.floors[map_flag]
        old = floor.rooms.get(segment_id)
        updated = RoomMapping(
            segment_id=segment_id,
            iot_id=old.iot_id if old else "",
            name=new_name.strip(),
        )
        floor.rooms[segment_id] = updated
        return updated

    def resolve_segments(self, map_flag: int, identifiers: list[str | int]) -> list[int]:
        """Resolve a mix of numeric segment IDs or room names into validated segment IDs."""
        floor = self.floors.get(map_flag)
        resolved: list[int] = []
        for ident in identifiers:
            if isinstance(ident, int) or (isinstance(ident, str) and ident.strip().isdigit()):
                seg_id = int(ident)
                resolved.append(seg_id)
                continue

            if floor is None:
                raise ValueError(
                    f"Cannot resolve room name '{ident}': floor {map_flag} has no stored room names"
                )
            needle = str(ident).strip().lower()
            matched_id: int | None = None
            for seg_id, rm in floor.rooms.items():
                if rm.name and rm.name.strip().lower() == needle:
                    matched_id = seg_id
                    break
            if matched_id is None:
                raise ValueError(f"Unknown room name '{ident}' on floor {map_flag} ({floor.name})")
            resolved.append(matched_id)
        return resolved
