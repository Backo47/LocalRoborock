"""Phase 1 CLI prototype for token extraction and read-only Roborock S6 verification."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from .client import RoborockCommandError, RoborockCommunicationError, RoborockLocalClient
from .const import CONSUMABLE_LIMITS_SECONDS, FAN_POWER_DESCRIPTIONS, RoborockFanPower
from .map_editor import MapRestrictionEditor, generate_interactive_map_html
from .map_fetcher import XiaomiCloudSession, XiaomiMapFetchError
from .models import ZoneCoordinates
from .protocol import MiioProtocolError
from .room_store import RoomStore
from .rrmap import ParsedRRMap, RRMapParseError, parse_rrmap, save_map_artifacts
from .token_extractor import extract_tokens_from_path


def _load_dotenv(dotenv_path: Path) -> dict[str, str]:
    """Lightweight .env file parser without external dependencies."""
    env_vars: dict[str, str] = {}
    if not dotenv_path.is_file():
        return env_vars
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env_vars[key.strip()] = val.strip().strip("'\"")
    return env_vars


def _save_dotenv(dotenv_path: Path, ip: str, token: str) -> None:
    """Write or update ROBOROCK_IP and ROBOROCK_TOKEN in local .env file."""
    content = (
        "# LocalRoborock local credentials (ignored by Git)\n"
        f"ROBOROCK_IP={ip}\n"
        f"ROBOROCK_TOKEN={token}\n"
    )
    dotenv_path.write_text(content, encoding="utf-8")


def _cmd_extract_token(args: argparse.Namespace) -> int:
    try:
        creds = extract_tokens_from_path(
            args.path,
            target_ip=args.ip,
            target_model="roborock.vacuum.s6",
        )
    except FileNotFoundError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    if not creds:
        print(
            "No valid 32-character hexadecimal miIO tokens found in the specified path.\n"
            " Note: If using a downloaded Home Assistant diagnostics JSON file, check whether\n"
            " the token field was replaced with '**REDACTED**'. If redacted, inspect\n"
            " '/config/.storage/core.config_entries' on the Home Assistant host or view\n"
            " the `miio_token` attribute in Home Assistant Developer Tools -> States.",
            file=sys.stderr,
        )
        return 1

    print(f"Found {len(creds)} candidate miIO device credential(s):\n")
    for idx, cred in enumerate(creds, start=1):
        token_display = cred.token if args.show_token else cred.masked_token()
        print(
            f"  [{idx}] Title : {cred.title}\n"
            f"      Domain: {cred.domain}\n"
            f"      Model : {cred.model or 'unknown'}\n"
            f"      IP    : {cred.ip or 'unknown'}\n"
            f"      Token : {token_display}\n"
            f"      Source: {cred.source}\n"
        )

    best = creds[0]
    if args.save_env:
        if args.ip and best.ip and best.ip != args.ip and not args.allow_ip_mismatch:
            print(
                f"Error: Top discovered credential has IP '{best.ip}', which does not match "
                f"expected target IP '{args.ip}' (model={best.model}).\n"
                "Refusing to write mismatched device IP to .env. Pass --ip <target_ip> "
                "or --allow-ip-mismatch if intentional.",
                file=sys.stderr,
            )
            return 1
        env_path = Path(args.env_file)
        resolved_ip = args.ip or best.ip or "192.168.1.110"
        _save_dotenv(env_path, ip=resolved_ip, token=best.token)
        print(
            f"Saved top candidate (IP={resolved_ip}, Token={best.masked_token()}) "
            f"to '{env_path}'."
        )
    return 0


async def _run_read_only_probe(client: RoborockLocalClient) -> dict[str, Any]:
    """Execute read-only diagnostic queries against the Roborock S6."""
    hello = await client.handshake()
    info = await client.get_device_info()
    status = await client.get_status()
    consumables = await client.get_consumables()
    rooms = await client.get_room_mapping()
    multi_maps = await client.get_multi_maps_list()
    clean_summary = await client.get_clean_summary()
    dnd = await client.get_dnd_timer()
    carpet = await client.get_carpet_mode()
    volume = await client.get_sound_volume()

    return {
        "handshake": {
            "device_id_hex": f"0x{hello.device_id:08x}",
            "device_id_int": hello.device_id,
            "stamp": hello.stamp,
        },
        "device_info": {
            "model": info.model,
            "fw_ver": info.fw_ver,
            "hw_ver": info.hw_ver,
            "life_sec": info.life,
            "rssi": info.rssi,
            "ip": info.ip,
        },
        "status": {
            "state": status.state,
            "state_name": status.state_name,
            "battery_pct": status.battery,
            "fan_power": status.fan_power,
            "fan_power_name": status.fan_power_name,
            "clean_time_sec": status.clean_time_sec,
            "clean_time_min": status.clean_time_min,
            "clean_area_raw": status.clean_area_raw,
            "clean_area_m2": status.clean_area_m2,
            "error_code": status.error_code,
            "error_name": status.error_name,
            "map_present": status.map_present,
            "active_map_slot": status.active_map_slot,
            "water_box_status": status.water_box_status,
            "dnd_enabled": status.dnd_enabled,
            "lab_status": status.lab_status,
            "in_cleaning": status.in_cleaning,
            "lock_status": status.lock_status,
            "mop_forbidden_enable": status.mop_forbidden_enable,
            "is_locating": status.is_locating,
        },
        "consumables": {
            "main_brush_remaining_h": consumables.remaining_hours("main_brush_work_time"),
            "main_brush_remaining_pct": consumables.remaining_percent("main_brush_work_time"),
            "side_brush_remaining_h": consumables.remaining_hours("side_brush_work_time"),
            "side_brush_remaining_pct": consumables.remaining_percent("side_brush_work_time"),
            "filter_remaining_h": consumables.remaining_hours("filter_work_time"),
            "filter_remaining_pct": consumables.remaining_percent("filter_work_time"),
            "sensor_remaining_h": consumables.remaining_hours("sensor_dirty_time"),
            "sensor_remaining_pct": consumables.remaining_percent("sensor_dirty_time"),
            "raw_seconds": consumables.raw,
        },
        "room_mapping": [{"segment_id": r.segment_id, "iot_id": r.iot_id} for r in rooms],
        "multi_maps": multi_maps,
        "clean_summary": clean_summary,
        "dnd_timer": dnd,
        "carpet_mode": carpet,
        "sound_volume": volume,
    }


READ_ONLY_PROBE_METHODS: tuple[str, ...] = (
    "<Hello Handshake 32B (0x2131)>",
    "miIO.info",
    "get_status",
    "get_consumable",
    "get_room_mapping",
    "get_multi_maps_list",
    "get_clean_summary",
    "get_dnd_timer",
    "get_carpet_mode",
    "get_sound_volume",
)


def _mask_token(token: str) -> str:
    if len(token) == 32:
        return f"{token[:4]}...{token[-4:]}"
    return "***"


async def _run_capabilities_scan(client: RoborockLocalClient) -> dict[str, Any]:
    """Phase 2 read-only capability and telemetry discovery scan."""
    base = await _run_read_only_probe(client)

    extra_queries: dict[str, tuple[str, list[Any]]] = {
        "get_fw_features": ("get_fw_features", []),
        "get_custom_mode": ("get_custom_mode", []),
        "get_water_box_custom_mode": ("get_water_box_custom_mode", []),
        "get_timer": ("get_timer", []),
        "get_timezone": ("get_timezone", []),
        "get_current_sound": ("get_current_sound", []),
        "get_child_lock_status": ("get_child_lock_status", []),
        "get_led_status": ("get_led_status", []),
        "get_Flow_led_status": ("get_Flow_led_status", []),
        "get_network_info": ("get_network_info", []),
        "get_serial_number": ("get_serial_number", []),
        "get_server_timer": ("get_server_timer", []),
    }

    # Also test get_clean_record on the latest 3 cleaning records if available
    records = base.get("clean_summary", {}).get("records", [])
    clean_records: list[dict[str, Any]] = []
    for rec_id in records[:3]:
        try:
            rec = await client.get_clean_record(int(rec_id))
            clean_records.append(
                {
                    "record_id": rec_id,
                    "start_timestamp": rec.start_timestamp,
                    "end_timestamp": rec.end_timestamp,
                    "duration_sec": rec.duration_sec,
                    "duration_min": rec.duration_min,
                    "area_raw": rec.area_raw,
                    "area_m2": rec.area_m2,
                    "error_code": rec.error_code,
                    "completed": rec.completed,
                    "start_type": rec.start_type,
                    "clean_type": rec.clean_type,
                    "finish_reason": rec.finish_reason,
                    "raw": rec.raw,
                }
            )
        except (MiioProtocolError, RoborockCommunicationError, RoborockCommandError) as err:
            clean_records.append({"record_id": rec_id, "error": str(err)})

    discovered_methods: dict[str, Any] = {}
    for label, (method, params) in extra_queries.items():
        try:
            res = await client.send_command(method, params, retries=1)
            if method == "get_network_info" and isinstance(res, dict):
                from .client import sanitize_payload

                res = sanitize_payload(res)
            discovered_methods[label] = {"supported": True, "result": res}
        except RoborockCommandError as err:
            discovered_methods[label] = {"supported": False, "reason": str(err)}
        except (RoborockCommunicationError, MiioProtocolError) as err:
            discovered_methods[label] = {"supported": "unknown_timeout", "reason": str(err)}

    base["recent_clean_records"] = clean_records
    base["extended_capabilities"] = discovered_methods
    return base


def _cmd_capabilities(args: argparse.Namespace) -> int:
    env_vars = _load_dotenv(Path(args.env_file))
    ip = args.ip or os.environ.get("ROBOROCK_IP") or env_vars.get("ROBOROCK_IP") or "192.168.1.110"
    token = args.token or os.environ.get("ROBOROCK_TOKEN") or env_vars.get("ROBOROCK_TOKEN")

    if not token or token == "0" * 32:
        print(
            "Error: No valid ROBOROCK_TOKEN provided. Set ROBOROCK_TOKEN in .env or pass --token.",
            file=sys.stderr,
        )
        return 1

    try:
        client = RoborockLocalClient(ip=ip, token=token, timeout=args.timeout, retries=3)
        report = asyncio.run(_run_capabilities_scan(client))
    except (ValueError, MiioProtocolError, RoborockCommunicationError, RoborockCommandError) as err:
        print(f"Capabilities scan failed: {err}", file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def _cmd_probe(args: argparse.Namespace) -> int:
    env_vars = _load_dotenv(Path(args.env_file))
    ip = args.ip or os.environ.get("ROBOROCK_IP") or env_vars.get("ROBOROCK_IP") or "192.168.1.110"
    token = args.token or os.environ.get("ROBOROCK_TOKEN") or env_vars.get("ROBOROCK_TOKEN")

    if not token or token == "0" * 32:
        print(
            "Error: No valid ROBOROCK_TOKEN provided. Set ROBOROCK_TOKEN in .env or pass --token.",
            file=sys.stderr,
        )
        return 1

    try:
        client = RoborockLocalClient(ip=ip, token=token, timeout=args.timeout)
    except ValueError as err:
        print(f"Configuration validation failed: {err}", file=sys.stderr)
        return 1

    if args.dry_run:
        print("=== LocalRoborock Probe Dry-Run Verification (NO PACKETS SENT) ===")
        print(f"Target Endpoint  : {client.ip}:{client.port} (UDP)")
        print(f"Token (Masked)   : {_mask_token(token)} (32-char hex validated)")
        print(f"Timeout / Retries: {client.timeout}s / {client.retries}")
        print(f"Commands ({len(READ_ONLY_PROBE_METHODS)} read-only queries, params=[]):")
        for idx, method_name in enumerate(READ_ONLY_PROBE_METHODS, start=1):
            print(f"  {idx:2d}. {method_name}")
        print("Safety Check     : PASS (100% read-only, zero state/motion/config mutations)")
        return 0

    try:
        report = asyncio.run(_run_read_only_probe(client))
    except (ValueError, MiioProtocolError, RoborockCommunicationError, RoborockCommandError) as err:
        print(f"Probe failed: {err}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    info = report["device_info"]
    st = report["status"]
    cons = report["consumables"]
    rooms = report["room_mapping"]

    print(f"=== Roborock S6 Local Probe ({ip}) ===")
    print(f"Model / Firmware : {info['model']} / FW {info['fw_ver']} (HW {info['hw_ver']})")
    print(f"Device ID / Wi-Fi: {report['handshake']['device_id_hex']} / RSSI {info['rssi']} dBm")
    print(f"State / Battery  : {st['state_name']} / {st['battery_pct']}%")
    print(f"Fan Power        : {st['fan_power_name']}")
    print(f"Last/Cur Clean   : {st['clean_area_m2']} m² in {st['clean_time_min']} min")
    print(f"Error Status     : {st['error_name']} (code {st['error_code']})")
    print(
        f"Map / Mop Status : Map present={st['map_present']} (slot {st['active_map_slot']}, "
        f"lab_status={st['lab_status']}), Water box={st['water_box_status']}"
    )
    print(
        f"Sound / DND      : Volume={report['sound_volume']}%, "
        f"DND={st['dnd_enabled']} ({report['dnd_timer']})"
    )
    print("\n--- Consumables ---")
    print(
        f"  Main Brush : {cons['main_brush_remaining_h']} h ({cons['main_brush_remaining_pct']}%)"
    )
    print(
        f"  Side Brush : {cons['side_brush_remaining_h']} h ({cons['side_brush_remaining_pct']}%)"
    )
    print(f"  Filter     : {cons['filter_remaining_h']} h ({cons['filter_remaining_pct']}%)")
    print(f"  Sensors    : {cons['sensor_remaining_h']} h ({cons['sensor_remaining_pct']}%)")
    print(f"\n--- Room Segments ({len(rooms)}) ---")
    for r in rooms:
        print(f"  Segment {r['segment_id']} -> IoT ID: {r['iot_id']}")
    return 0


def _build_client_from_args(args: argparse.Namespace) -> RoborockLocalClient:
    env_vars = _load_dotenv(Path(args.env_file))
    ip = (
        getattr(args, "ip", None)
        or os.environ.get("ROBOROCK_IP")
        or env_vars.get("ROBOROCK_IP")
        or "192.168.1.110"
    )
    token = (
        getattr(args, "token", None)
        or os.environ.get("ROBOROCK_TOKEN")
        or env_vars.get("ROBOROCK_TOKEN")
    )
    if not token or token == "0" * 32:
        raise ValueError(
            "No valid ROBOROCK_TOKEN provided. Set ROBOROCK_TOKEN in .env or pass --token."
        )
    timeout = float(getattr(args, "timeout", 5.0))
    return RoborockLocalClient(ip=ip, token=token, timeout=timeout)


def _cmd_rooms(args: argparse.Namespace) -> int:
    store = RoomStore(args.rooms_file)
    store.load()

    if args.rooms_action == "list":
        if not store.floors:
            print(f"No floors stored in '{store.path}'.")
            return 0
        for map_flag, floor in sorted(store.floors.items()):
            print(f"Floor {map_flag}: {floor.name} ({len(floor.rooms)} rooms)")
            for seg_id, rm in sorted(floor.rooms.items()):
                print(f"  - Segment {seg_id:2d}: {rm.name} (IoT ID: {rm.iot_id})")
        return 0

    if args.rooms_action == "rename":
        rm = store.rename_room(args.floor, args.segment, args.name)
        store.save()
        print(
            f"Renamed segment {rm.segment_id} on floor {args.floor} to '{rm.name}' "
            f"(saved to '{store.path}')."
        )
        return 0

    if args.rooms_action == "sync":
        try:
            client = _build_client_from_args(args)

            async def _sync() -> tuple[int, str, int]:
                st = await client.get_status()
                floors = await client.get_floors()
                mappings = await client.get_room_mapping()
                slot = st.active_map_slot
                floor_name = next(
                    (f.name for f in floors if f.map_flag == slot), f"Map {slot}"
                )
                cfg = store.update_floor_mappings(slot, floor_name, mappings)
                store.save()
                return slot, cfg.name, len(cfg.rooms)

            slot, fname, count = asyncio.run(_sync())
            print(
                f"Synced active floor {slot} ('{fname}'): {count} room segments "
                f"saved to '{store.path}'."
            )
            return 0
        except (
            ValueError,
            MiioProtocolError,
            RoborockCommunicationError,
            RoborockCommandError,
        ) as err:
            print(f"Room sync failed: {err}", file=sys.stderr)
            return 1

    return 1


def _cmd_map(args: argparse.Namespace) -> int:
    store = RoomStore(args.rooms_file)
    store.load()

    if args.map_action == "decode":
        in_path = Path(args.file)
        if not in_path.is_file():
            print(f"Error: Map file not found: {in_path}", file=sys.stderr)
            return 1
        floor_cfg = store.floors.get(args.floor)
        room_names = (
            {seg: rm.name for seg, rm in floor_cfg.rooms.items() if rm.name}
            if floor_cfg
            else {}
        )
        try:
            raw_bytes = in_path.read_bytes()
            parsed, paths = save_map_artifacts(
                raw_bytes,
                output_dir=args.output_dir,
                base_name=f"map_slot_{args.floor}",
                room_names=room_names,
                scale=args.scale,
            )
        except RRMapParseError as err:
            print(f"Map decode failed: {err}", file=sys.stderr)
            return 1

        print(
            f"Decoded RRMap v{parsed.major_version}.{parsed.minor_version} "
            f"({parsed.image_width}x{parsed.image_height} px, {len(parsed.rooms)} rooms):"
        )
        for seg_id, r in sorted(parsed.rooms.items()):
            rname = room_names.get(seg_id, f"Room {seg_id}")
            print(
                f"  - Segment {seg_id:2d} ({rname}): {r.area_m2} m² "
                f"(center: [{r.centroid_mm_x}, {r.centroid_mm_y}] mm)"
            )
        print(f"Saved PNG : {paths['png']}")
        print(f"Saved SVG : {paths['svg']}")
        print(f"Saved JSON: {paths['json']}")
        return 0

    if args.map_action == "fetch":
        env_vars = _load_dotenv(Path(args.env_file))
        user_id = os.environ.get("XIAOMI_USER_ID") or env_vars.get("XIAOMI_USER_ID")
        service_token = os.environ.get("XIAOMI_SERVICE_TOKEN") or env_vars.get(
            "XIAOMI_SERVICE_TOKEN"
        )
        ssecurity = os.environ.get("XIAOMI_SSECURITY") or env_vars.get("XIAOMI_SSECURITY")
        country = os.environ.get("XIAOMI_COUNTRY") or env_vars.get("XIAOMI_COUNTRY") or "de"

        if not user_id or not service_token or not ssecurity:
            print(
                "Error: Missing XIAOMI_USER_ID, XIAOMI_SERVICE_TOKEN, or XIAOMI_SSECURITY in .env "
                "for hybrid Xiaomi FDS map download.",
                file=sys.stderr,
            )
            return 1

        try:
            client = _build_client_from_args(args)
            session = XiaomiCloudSession(
                user_id=user_id,
                service_token=service_token,
                ssecurity=ssecurity,
                country=country,
            )

            async def _fetch_flow() -> tuple[int, str, bytes]:
                st_init = await client.get_status()
                initial_slot = st_init.active_map_slot
                target_slot = args.floor if args.floor is not None else initial_slot
                switched = False
                try:
                    if target_slot != initial_slot:
                        await client.load_multi_map(target_slot)
                        switched = True
                        await asyncio.sleep(1.5)
                    pointer = await client.get_map_v1()
                    # Wait briefly for the vacuum to finish uploading the blob to FDS
                    await asyncio.sleep(1.0)
                    raw_blob = session.download_raw_map(pointer)
                    return target_slot, pointer, raw_blob
                finally:
                    if switched:
                        await client.load_multi_map(initial_slot)
                        await asyncio.sleep(1.0)

            slot, pointer, raw_blob = asyncio.run(_fetch_flow())
            floor_cfg = store.floors.get(slot)
            room_names = (
                {seg: rm.name for seg, rm in floor_cfg.rooms.items() if rm.name}
                if floor_cfg
                else {}
            )
            parsed, paths = save_map_artifacts(
                raw_blob,
                output_dir=args.output_dir,
                base_name=f"map_slot_{slot}",
                room_names=room_names,
                scale=args.scale,
            )
            print(
                f"Fetched map for floor slot {slot} (pointer='{pointer}', "
                f"{parsed.image_width}x{parsed.image_height} px, {len(parsed.rooms)} rooms):"
            )
            for seg_id, r in sorted(parsed.rooms.items()):
                rname = room_names.get(seg_id, f"Room {seg_id}")
                print(
                    f"  - Segment {seg_id:2d} ({rname}): {r.area_m2} m² "
                    f"(center: [{r.centroid_mm_x}, {r.centroid_mm_y}] mm)"
                )
            print(f"Cached RRMap: {paths['rrmap']}")
            print(f"Rendered PNG: {paths['png']}")
            print(f"Rendered SVG: {paths['svg']}")
            print(f"Summary JSON: {paths['json']}")
            return 0
        except (
            ValueError,
            MiioProtocolError,
            RoborockCommunicationError,
            RoborockCommandError,
            XiaomiMapFetchError,
            RRMapParseError,
        ) as err:
            print(f"Map fetch failed: {err}", file=sys.stderr)
            return 1

    if args.map_action == "ui":
        out_dir = Path(args.output_dir)
        floors_data: dict[int, tuple[str, ParsedRRMap, dict[int, str]]] = {}
        for slot in range(4):
            rrmap_file = out_dir / f"map_slot_{slot}.rrmap"
            if not rrmap_file.is_file():
                continue
            try:
                parsed = parse_rrmap(rrmap_file.read_bytes())
            except (OSError, RRMapParseError) as err:
                print(f"Warning: could not parse {rrmap_file}: {err}", file=sys.stderr)
                continue
            floor_cfg = store.floors.get(slot)
            fname = floor_cfg.name if floor_cfg else f"Map {slot}"
            rnames = (
                {seg: rm.name for seg, rm in floor_cfg.rooms.items() if rm.name}
                if floor_cfg
                else {}
            )
            floors_data[slot] = (fname, parsed, rnames)

        if not floors_data:
            print(
                f"Error: No cached map_slot_*.rrmap files found in '{out_dir}'. "
                "Run 'map fetch' first.",
                file=sys.stderr,
            )
            return 1

        html_content = generate_interactive_map_html(floors_data, active_slot=args.floor)
        html_path = out_dir / "interactive_map.html"
        html_path.write_text(html_content, encoding="utf-8")
        print(
            f"Generated interactive HTML5 Map UI ({len(floors_data)} floor(s)): {html_path}"
        )
        return 0

    return 1


def _cmd_map_edit(args: argparse.Namespace) -> int:
    map_dir = Path(args.map_dir)
    slot = int(args.floor)
    rrmap_path = map_dir / f"map_slot_{slot}.rrmap"

    action = args.edit_action
    if action == "show-restrictions":
        if not rrmap_path.is_file():
            print(f"Error: Cached map not found: {rrmap_path}", file=sys.stderr)
            return 1
        parsed = parse_rrmap(rrmap_path.read_bytes())
        editor = MapRestrictionEditor.from_parsed_map(parsed)
        print(f"=== Current Map Restrictions on Floor Slot {slot} ===")
        print(f"Virtual Walls ({len(editor.virtual_walls)})  : {editor.virtual_walls}")
        print(f"No-Go Zones   ({len(editor.forbidden_zones)}): {editor.forbidden_zones}")
        print(f"No-Mop Zones  ({len(editor.no_mop_zones)})   : {editor.no_mop_zones}")
        print(f"save_map payload: {json.dumps(editor.to_save_map_params())}")
        return 0

    try:
        client = _build_client_from_args(args)
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    if action == "merge-rooms":
        a, b = int(args.segment_a), int(args.segment_b)
        if a <= 0 or b <= 0 or a == b:
            print("Error: merge-rooms requires two distinct positive segment IDs.", file=sys.stderr)
            return 1
        method = "merge_segment"
        params: list[Any] = [a, b]
        if args.dry_run:
            print("=== LocalRoborock Map Editor Dry-Run (NO PACKETS SENT) ===")
            print(f"Target Endpoint : {client.ip}:{client.port} (UDP)")
            print(f"Target Floor    : Slot {slot}")
            print(f"Operation       : Merge room segments {a} and {b}")
            print(f"miIO Method     : {method}")
            print(f"miIO Params     : {json.dumps(params)}")
            return 0
        try:
            ok = asyncio.run(client.merge_segments(a, b))
        except (MiioProtocolError, RoborockCommunicationError, RoborockCommandError) as err:
            print(f"merge-rooms failed: {err}", file=sys.stderr)
            return 1
        print(f"Merged segments {a} and {b}: {'OK' if ok else 'FAILED'}")
        return 0 if ok else 1

    if action == "split-room":
        seg = int(args.segment)
        x1, y1, x2, y2 = int(args.x1), int(args.y1), int(args.x2), int(args.y2)
        method = "split_segment"
        params = [seg, x1, y1, x2, y2]
        if args.dry_run:
            print("=== LocalRoborock Map Editor Dry-Run (NO PACKETS SENT) ===")
            print(f"Target Endpoint : {client.ip}:{client.port} (UDP)")
            print(f"Target Floor    : Slot {slot}")
            print(f"Operation       : Split room segment {seg} along ({x1},{y1}) -> ({x2},{y2})")
            print(f"miIO Method     : {method}")
            print(f"miIO Params     : {json.dumps(params)}")
            return 0
        try:
            ok = asyncio.run(client.split_segment(seg, x1, y1, x2, y2))
        except (MiioProtocolError, RoborockCommunicationError, RoborockCommandError) as err:
            print(f"split-room failed: {err}", file=sys.stderr)
            return 1
        print(f"Split segment {seg}: {'OK' if ok else 'FAILED'}")
        return 0 if ok else 1

    if action == "delete-floor":
        if not (0 <= slot <= 3):
            print("Error: Floor slot must be between 0 and 3.", file=sys.stderr)
            return 1
        if args.dry_run:
            print("=== LocalRoborock Map Editor Dry-Run (NO PACKETS SENT) ===")
            print(f"Target Endpoint : {client.ip}:{client.port} (UDP)")
            print(f"Operation       : Delete stored floor map slot {slot}")
            print("miIO Method     : del_map")
            print(f"miIO Params     : [{slot}]")
            return 0
        try:
            ok = asyncio.run(client.delete_floor_map(slot))
        except (MiioProtocolError, RoborockCommunicationError, RoborockCommandError) as err:
            print(f"delete-floor failed: {err}", file=sys.stderr)
            return 1
        print(f"Deleted floor map slot {slot} (del_map): {'OK' if ok else 'FAILED'}")
        return 0 if ok else 1

    if action == "reset-map":
        if args.dry_run:
            print("=== LocalRoborock Map Editor Dry-Run (NO PACKETS SENT) ===")
            print(f"Target Endpoint : {client.ip}:{client.port} (UDP)")
            print("Operation       : Reset currently active floor map")
            print("miIO Method     : reset_map")
            print("miIO Params     : []")
            return 0
        try:
            ok = asyncio.run(client.reset_active_map())
        except (MiioProtocolError, RoborockCommunicationError, RoborockCommandError) as err:
            print(f"reset-map failed: {err}", file=sys.stderr)
            return 1
        print(f"Reset active floor map (reset_map): {'OK' if ok else 'FAILED'}")
        return 0 if ok else 1

    # Restriction editing commands (add-wall, add-nogo, add-nomop, clear-restrictions)
    if not rrmap_path.is_file():
        print(
            f"Error: Cached map '{rrmap_path}' is required to read and preserve existing "
            "walls/zones before calling save_map.",
            file=sys.stderr,
        )
        return 1

    parsed = parse_rrmap(rrmap_path.read_bytes())
    editor = MapRestrictionEditor.from_parsed_map(parsed)
    backup_path = editor.save_backup(map_dir, floor_slot=slot)

    try:
        if action == "add-wall":
            editor.add_virtual_wall(args.x1, args.y1, args.x2, args.y2)
        elif action == "add-nogo":
            editor.add_forbidden_zone_rect(args.x1, args.y1, args.x2, args.y2)
        elif action == "add-nomop":
            editor.add_no_mop_zone_rect(args.x1, args.y1, args.x2, args.y2)
        elif action == "clear-restrictions":
            editor.clear_all()
        else:
            raise ValueError(f"Unknown map-edit action: {action}")
    except ValueError as err:
        print(f"Validation error: {err}", file=sys.stderr)
        return 1

    save_params = editor.to_save_map_params()
    if args.dry_run:
        print("=== LocalRoborock Map Editor Dry-Run (NO PACKETS SENT) ===")
        print(f"Target Endpoint : {client.ip}:{client.port} (UDP)")
        print(f"Target Floor    : Slot {slot}")
        print(f"Backup Saved To : {backup_path}")
        print("miIO Method     : save_map")
        print(f"miIO Params     : {json.dumps(save_params)}")
        return 0

    try:
        ok = asyncio.run(
            client.save_map_restrictions(
                virtual_walls=editor.virtual_walls,
                forbidden_zones=editor.forbidden_zones,
                no_mop_zones=editor.no_mop_zones,
            )
        )
    except (MiioProtocolError, RoborockCommunicationError, RoborockCommandError) as err:
        print(f"save_map failed: {err} (Backup preserved at {backup_path})", file=sys.stderr)
        return 1

    print(f"Committed updated map restrictions via save_map (backup at {backup_path}): OK")
    return 0 if ok else 1


FAN_MODE_ALIASES: dict[str, int] = {
    "quiet": int(RoborockFanPower.SILENT),
    "silent": int(RoborockFanPower.SILENT),
    "101": int(RoborockFanPower.SILENT),
    "balanced": int(RoborockFanPower.BALANCED),
    "standard": int(RoborockFanPower.BALANCED),
    "102": int(RoborockFanPower.BALANCED),
    "turbo": int(RoborockFanPower.TURBO),
    "103": int(RoborockFanPower.TURBO),
    "max": int(RoborockFanPower.MAX),
    "104": int(RoborockFanPower.MAX),
    "gentle": int(RoborockFanPower.GENTLE),
    "mop": int(RoborockFanPower.GENTLE),
    "105": int(RoborockFanPower.GENTLE),
}


def _cmd_action(args: argparse.Namespace) -> int:
    try:
        client = _build_client_from_args(args)
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    cmd = args.command
    store = RoomStore(getattr(args, "rooms_file", "rooms.json"))
    store.load()

    try:
        if cmd == "start":
            method, params = "app_start", []
            coro = client.start_cleaning()
        elif cmd == "stop":
            method, params = "app_stop", []
            coro = client.stop_cleaning()
        elif cmd == "pause":
            method, params = "app_pause", []
            coro = client.pause_cleaning()
        elif cmd == "home":
            method, params = "app_charge", []
            coro = client.return_to_dock()
        elif cmd == "spot":
            method, params = "app_spot", []
            coro = client.spot_clean()
        elif cmd == "locate":
            method, params = "find_me", [""]
            coro = client.find_me()
        elif cmd == "fan-speed":
            mode_key = str(args.mode).strip().lower()
            if mode_key not in FAN_MODE_ALIASES:
                raise ValueError(
                    f"Unknown fan mode '{args.mode}'. Allowed: {sorted(FAN_MODE_ALIASES)}"
                )
            val = FAN_MODE_ALIASES[mode_key]
            method, params = "set_custom_mode", [val]
            coro = client.set_fan_power(val)
        elif cmd == "clean-rooms":
            seg_ids = store.resolve_segments(args.floor, args.rooms)
            if not (1 <= args.repeat <= 3):
                raise ValueError("Repeat must be between 1 and 3")
            method, params = "app_segment_clean", [{"segments": seg_ids, "repeat": args.repeat}]
            coro = client.segment_clean(seg_ids, repeat=args.repeat)
        elif cmd == "clean-zone":
            zone = ZoneCoordinates(
                x1=args.x1,
                y1=args.y1,
                x2=args.x2,
                y2=args.y2,
                iterations=args.repeat,
            )
            method, params = "app_zoned_clean", [zone.to_miio_list()]
            coro = client.zoned_clean([zone])
        elif cmd == "goto":
            method, params = "app_goto_target", [args.x, args.y]
            coro = client.goto_target(args.x, args.y)
        elif cmd == "load-map":
            if not (0 <= args.slot <= 3):
                raise ValueError("Map slot must be between 0 and 3")
            method, params = "load_multi_map", [args.slot]
            coro = client.load_multi_map(args.slot)
        elif cmd == "volume":
            if not (0 <= args.level <= 100):
                raise ValueError("Volume level must be between 0 and 100")
            method, params = "change_sound_volume", [args.level]
            coro = client.set_sound_volume(args.level)
        elif cmd == "led":
            enabled = args.state.lower() in ("on", "1", "true")
            method, params = "set_led_status", [1 if enabled else 0]
            coro = client.set_led_status(enabled)
        elif cmd == "carpet-mode":
            enabled = args.state.lower() in ("on", "1", "true")
            method, params = "set_carpet_mode", [{"enable": 1 if enabled else 0}]
            coro = client.set_carpet_mode(enabled)
        elif cmd == "dnd":
            if args.state.lower() in ("off", "0", "false", "disable"):
                method, params = "close_dnd_timer", []
                coro = client.disable_dnd_timer()
            else:
                method = "set_dnd_timer"
                params = [args.start_hour, args.start_minute, args.end_hour, args.end_minute]
                coro = client.set_dnd_timer(
                    args.start_hour, args.start_minute, args.end_hour, args.end_minute
                )
        elif cmd == "reset-consumable":
            if args.part not in CONSUMABLE_LIMITS_SECONDS:
                raise ValueError(
                    f"Invalid consumable '{args.part}'. "
                    f"Valid: {sorted(CONSUMABLE_LIMITS_SECONDS)}"
                )
            method, params = "reset_consumable", [args.part]
            coro = client.reset_consumable(args.part)
        else:
            raise ValueError(f"Unsupported command: {cmd}")
    except ValueError as err:
        # Close unawaited coroutine if created before error
        print(f"Validation error: {err}", file=sys.stderr)
        return 1

    if getattr(args, "dry_run", False):
        coro.close()
        print("=== LocalRoborock Action Dry-Run (NO PACKETS SENT) ===")
        print(f"Target Endpoint : {client.ip}:{client.port} (UDP)")
        print(f"CLI Action      : {cmd}")
        print(f"miIO Method     : {method}")
        print(f"miIO Params     : {json.dumps(params)}")
        return 0

    try:
        ok = asyncio.run(coro)
    except (MiioProtocolError, RoborockCommunicationError, RoborockCommandError) as err:
        print(f"Command '{cmd}' failed: {err}", file=sys.stderr)
        return 1

    print(f"Command '{cmd}' ({method}) executed: {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="local-roborock",
        description="LocalRoborock Phase 1 CLI - Token extraction and read-only diagnostics",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to local .env file (default: .env)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_extract = sub.add_parser(
        "extract-token",
        help="Extract local miIO token from Home Assistant config_entries or diagnostics JSON",
    )
    p_extract.add_argument(
        "path",
        help="Path to core.config_entries, diagnostics JSON, or HA config folder",
    )
    p_extract.add_argument(
        "--ip",
        default="192.168.1.110",
        help="Target vacuum IP address to prioritize matching entries (default: 192.168.1.110)",
    )
    p_extract.add_argument(
        "--show-token",
        action="store_true",
        help="Print the full 32-character token in stdout (masked by default)",
    )
    p_extract.add_argument(
        "--save-env",
        action="store_true",
        help="Save the extracted IP and token directly to the local .env file",
    )
    p_extract.add_argument(
        "--allow-ip-mismatch",
        action="store_true",
        help="Allow saving a token whose config entry IP differs from --ip",
    )

    p_probe = sub.add_parser(
        "probe",
        help="Run a read-only local UDP diagnostic probe against the Roborock S6",
    )
    p_probe.add_argument("--ip", help="Override vacuum IP address (default from .env)")
    p_probe.add_argument("--token", help="Override 32-char hex token (default from .env)")
    p_probe.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="UDP socket timeout in seconds (default: 5.0)",
    )
    p_probe.add_argument(
        "--json",
        action="store_true",
        help="Output full diagnostic report as formatted JSON",
    )
    p_probe.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate IP, token, and list exact read-only commands without sending any packets",
    )

    p_cap = sub.add_parser(
        "capabilities",
        help="Run Phase 2 extended read-only capability discovery scan against Roborock S6",
    )
    p_cap.add_argument("--ip", help="Override vacuum IP address (default from .env)")
    p_cap.add_argument("--token", help="Override 32-char hex token (default from .env)")
    p_cap.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="UDP socket timeout in seconds per query (default: 3.0)",
    )

    # Room management commands
    p_rooms = sub.add_parser("rooms", help="Manage local multi-floor room mappings (rooms.json)")
    p_rooms.add_argument("--rooms-file", default="rooms.json", help="Path to rooms.json")
    rooms_sub = p_rooms.add_subparsers(dest="rooms_action", required=True)
    rooms_sub.add_parser("list", help="List saved floors and room names from rooms.json")

    p_r_rename = rooms_sub.add_parser("rename", help="Assign a human-readable name to a room ID")
    p_r_rename.add_argument("--floor", type=int, default=0, help="Floor mapFlag (default: 0)")
    p_r_rename.add_argument("segment", type=int, help="Numeric room segment ID (e.g. 16)")
    p_r_rename.add_argument("name", help="New room name (e.g. 'Kitchen')")

    p_r_sync = rooms_sub.add_parser(
        "sync", help="Read active floor room mappings from vacuum and update rooms.json"
    )
    p_r_sync.add_argument("--ip", help="Override vacuum IP address")
    p_r_sync.add_argument("--token", help="Override 32-char hex token")
    p_r_sync.add_argument("--timeout", type=float, default=5.0)

    # Map decoding & hybrid fetching commands (Phase 5)
    p_map = sub.add_parser("map", help="Fetch, cache, decode, and render Roborock RRMap files")
    p_map.add_argument("--rooms-file", default="rooms.json", help="Path to rooms.json")
    p_map.add_argument(
        "--output-dir",
        default="map_cache",
        help="Directory for cached .rrmap and rendered PNG/SVG/JSON files (default: map_cache)",
    )
    p_map.add_argument("--scale", type=int, default=4, help="Pixel scale factor (default: 4)")
    map_sub = p_map.add_subparsers(dest="map_action", required=True)

    p_m_decode = map_sub.add_parser(
        "decode", help="Decode an existing local .rrmap/.gz file and render PNG/SVG/JSON"
    )
    p_m_decode.add_argument("file", help="Path to local .rrmap file")
    p_m_decode.add_argument("--floor", type=int, default=0, help="Floor slot for room name lookup")

    p_m_fetch = map_sub.add_parser(
        "fetch",
        help="Trigger get_map_v1, download .rrmap to local cache, and render PNG/SVG/JSON",
    )
    p_m_fetch.add_argument(
        "--floor",
        type=int,
        help="Optional floor slot (0..3) to load temporarily before fetching map",
    )
    p_m_fetch.add_argument("--ip", help="Override vacuum IP address")
    p_m_fetch.add_argument("--token", help="Override 32-char hex token")
    p_m_fetch.add_argument("--timeout", type=float, default=5.0)

    p_m_ui = map_sub.add_parser(
        "ui",
        help="Generate standalone interactive HTML5/Canvas/SVG Map UI & Editor from cached maps",
    )
    p_m_ui.add_argument("--floor", type=int, default=1, help="Default active floor slot (0..3)")

    # Helper to attach common --ip, --token, --timeout, --dry-run flags to action subcommands
    def _add_action_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--ip", help="Override vacuum IP address")
        p.add_argument("--token", help="Override 32-char hex token")
        p.add_argument("--timeout", type=float, default=5.0)
        p.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate parameters and print exact miIO payload without sending packets",
        )

    # Phase 7: Map Editor commands (map-edit)
    p_medit = sub.add_parser(
        "map-edit",
        help="Local map editor for merging/splitting rooms and managing walls/no-go/no-mop zones",
    )
    p_medit.add_argument(
        "--map-dir",
        default="map_cache",
        help="Directory containing cached map_slot_*.rrmap files (default: map_cache)",
    )
    p_medit.add_argument("--floor", type=int, default=1, help="Target floor slot (default: 1)")
    medit_sub = p_medit.add_subparsers(dest="edit_action", required=True)

    medit_sub.add_parser(
        "show-restrictions",
        help="Inspect existing virtual walls, no-go zones, and no-mop zones in cached .rrmap",
    )

    p_me_merge = medit_sub.add_parser(
        "merge-rooms", help="Merge two adjacent room segments (merge_segment)"
    )
    p_me_merge.add_argument("segment_a", type=int)
    p_me_merge.add_argument("segment_b", type=int)
    _add_action_flags(p_me_merge)

    p_me_split = medit_sub.add_parser(
        "split-room",
        help="Split a room segment along line (x1, y1) -> (x2, y2) in mm (split_segment)",
    )
    p_me_split.add_argument("segment", type=int)
    p_me_split.add_argument("x1", type=int)
    p_me_split.add_argument("y1", type=int)
    p_me_split.add_argument("x2", type=int)
    p_me_split.add_argument("y2", type=int)
    _add_action_flags(p_me_split)

    for rect_cmd, rect_help in (
        ("add-wall", "Add a virtual wall line (x1, y1) -> (x2, y2) in mm while preserving zones"),
        ("add-nogo", "Add a rectangular No-Go zone (x1, y1, x2, y2) in mm while preserving zones"),
        (
            "add-nomop",
            "Add a rectangular No-Mop zone (x1, y1, x2, y2) in mm while preserving zones",
        ),
    ):
        p_me_rect = medit_sub.add_parser(rect_cmd, help=rect_help)
        p_me_rect.add_argument("x1", type=int)
        p_me_rect.add_argument("y1", type=int)
        p_me_rect.add_argument("x2", type=int)
        p_me_rect.add_argument("y2", type=int)
        _add_action_flags(p_me_rect)

    p_me_clear = medit_sub.add_parser(
        "clear-restrictions",
        help="Clear all virtual walls, No-Go zones, and No-Mop zones (creates backup first)",
    )
    _add_action_flags(p_me_clear)

    p_me_del = medit_sub.add_parser(
        "delete-floor",
        help="Delete a stored multi-floor map slot (0..3) from vacuum memory (del_map)",
    )
    _add_action_flags(p_me_del)

    p_me_reset = medit_sub.add_parser(
        "reset-map",
        help="Reset/clear the currently active floor map to force re-mapping (reset_map)",
    )
    _add_action_flags(p_me_reset)

    for simple_cmd, desc in (
        ("start", "Start full-floor cleaning (app_start)"),
        ("stop", "Stop active cleaning (app_stop)"),
        ("pause", "Pause active cleaning (app_pause)"),
        ("home", "Return vacuum to charging dock (app_charge)"),
        ("spot", "Start local spot cleaning (app_spot)"),
        ("locate", "Play voice locator prompt (find_me)"),
    ):
        p_act = sub.add_parser(simple_cmd, help=desc)
        _add_action_flags(p_act)

    p_fan = sub.add_parser(
        "fan-speed",
        help=f"Set suction power ({', '.join(FAN_POWER_DESCRIPTIONS.values())})",
    )
    p_fan.add_argument("mode", help="quiet/101, balanced/102, turbo/103, max/104, gentle/105")
    _add_action_flags(p_fan)

    p_crooms = sub.add_parser(
        "clean-rooms",
        help="Clean specific room segment IDs or room names from rooms.json (app_segment_clean)",
    )
    p_crooms.add_argument("rooms", nargs="+", help="Segment IDs (16 17) or room names")
    p_crooms.add_argument("--repeat", type=int, default=1, help="Clean passes 1..3 (default: 1)")
    p_crooms.add_argument("--floor", type=int, default=0, help="Floor mapFlag for name lookup")
    p_crooms.add_argument("--rooms-file", default="rooms.json", help="Path to rooms.json")
    _add_action_flags(p_crooms)

    p_czone = sub.add_parser(
        "clean-zone",
        help="Clean a rectangular zone in millimeter coordinates (app_zoned_clean)",
    )
    p_czone.add_argument("x1", type=int)
    p_czone.add_argument("y1", type=int)
    p_czone.add_argument("x2", type=int)
    p_czone.add_argument("y2", type=int)
    p_czone.add_argument("--repeat", type=int, default=1, help="Clean passes 1..3 (default: 1)")
    _add_action_flags(p_czone)

    p_goto = sub.add_parser(
        "goto",
        help="Send vacuum to (x, y) millimeter target coordinates (app_goto_target)",
    )
    p_goto.add_argument("x", type=int)
    p_goto.add_argument("y", type=int)
    _add_action_flags(p_goto)

    p_lmap = sub.add_parser(
        "load-map",
        help="Switch active multi-floor map slot 0..3 (load_multi_map)",
    )
    p_lmap.add_argument("slot", type=int, help="Map slot index (0='Lower floor', 1='Upper floor')")
    _add_action_flags(p_lmap)

    p_vol = sub.add_parser("volume", help="Set voice volume 0..100 (change_sound_volume)")
    p_vol.add_argument("level", type=int, help="Volume percentage 0..100")
    _add_action_flags(p_vol)

    p_led = sub.add_parser("led", help="Turn top status indicator LED on or off (set_led_status)")
    p_led.add_argument("state", choices=["on", "off"], help="LED state")
    _add_action_flags(p_led)

    p_carpet = sub.add_parser("carpet-mode", help="Turn Carpet Boost on or off (set_carpet_mode)")
    p_carpet.add_argument("state", choices=["on", "off"], help="Carpet Boost state")
    _add_action_flags(p_carpet)

    p_dnd = sub.add_parser("dnd", help="Configure or disable Do Not Disturb (set_dnd_timer)")
    p_dnd.add_argument("state", choices=["on", "off"], help="Enable or disable DND")
    p_dnd.add_argument("--start-hour", type=int, default=22)
    p_dnd.add_argument("--start-minute", type=int, default=0)
    p_dnd.add_argument("--end-hour", type=int, default=8)
    p_dnd.add_argument("--end-minute", type=int, default=0)
    _add_action_flags(p_dnd)

    p_reset = sub.add_parser(
        "reset-consumable",
        help="Reset wear timer for a consumable part (reset_consumable)",
    )
    p_reset.add_argument("part", choices=sorted(CONSUMABLE_LIMITS_SECONDS), help="Consumable key")
    _add_action_flags(p_reset)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "extract-token":
        return _cmd_extract_token(args)
    if args.command == "probe":
        return _cmd_probe(args)
    if args.command == "capabilities":
        return _cmd_capabilities(args)
    if args.command == "rooms":
        return _cmd_rooms(args)
    if args.command == "map":
        return _cmd_map(args)
    if args.command == "map-edit":
        return _cmd_map_edit(args)
    return _cmd_action(args)


if __name__ == "__main__":
    raise SystemExit(main())
