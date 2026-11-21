"""Unit tests for Home Assistant token extraction and data models."""

import json
from pathlib import Path
from typing import Any

from custom_components.local_roborock.cli import main as cli_main
from custom_components.local_roborock.models import ConsumableStatus, VacuumStatus
from custom_components.local_roborock.token_extractor import (
    extract_tokens_from_json_data,
    extract_tokens_from_path,
)

FAKE_S6_TOKEN = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
FAKE_OTHER_TOKEN = "11112222333344445555666677778888"


def test_extract_token_from_core_config_entries(tmp_path: Path) -> None:
    sample_config_entries = {
        "version": 1,
        "minor_version": 1,
        "key": "core.config_entries",
        "data": {
            "entries": [
                {
                    "entry_id": "other_device",
                    "domain": "xiaomi_miot",
                    "title": "Xiaomi Air Purifier",
                    "data": {
                        "host": "192.168.1.150",
                        "token": FAKE_OTHER_TOKEN,
                        "model": "zhimi.airpurifier.mb3",
                    },
                },
                {
                    "entry_id": "roborock_s6_entry",
                    "domain": "xiaomi_miot",
                    "title": "Roborock S6",
                    "data": {
                        "host": "192.168.1.110",
                        "token": FAKE_S6_TOKEN,
                        "model": "roborock.vacuum.s6",
                    },
                    "options": {},
                },
            ]
        },
    }

    storage_dir = tmp_path / ".storage"
    storage_dir.mkdir()
    entries_file = storage_dir / "core.config_entries"
    entries_file.write_text(json.dumps(sample_config_entries), encoding="utf-8")

    results = extract_tokens_from_path(tmp_path, target_ip="192.168.1.110")
    assert len(results) == 2
    best = results[0]
    assert best.token == FAKE_S6_TOKEN
    assert best.ip == "192.168.1.110"
    assert best.model == "roborock.vacuum.s6"
    assert best.domain == "xiaomi_miot"
    assert best.masked_token() == "a1b2...8f90"


def test_ignore_redacted_diagnostics() -> None:
    redacted_diag = {
        "config_entry": {
            "domain": "xiaomi_miot",
            "title": "Roborock S6",
            "data": {
                "host": "192.168.1.110",
                "token": "**REDACTED**",
                "model": "roborock.vacuum.s6",
            },
        }
    }
    results = extract_tokens_from_json_data(redacted_diag)
    assert results == []


def test_vacuum_status_and_consumables_models() -> None:
    raw_status = {
        "msg_ver": 2,
        "msg_seq": 100,
        "state": 8,
        "battery": 100,
        "clean_time": 1800,
        "clean_area": 32500000,
        "error_code": 0,
        "map_present": 1,
        "in_cleaning": 0,
        "in_returning": 0,
        "in_fresh_state": 1,
        "lab_status": 1,
        "water_box_status": 0,
        "fan_power": 102,
        "dnd_enabled": 0,
        "map_status": 3,
    }
    st = VacuumStatus.from_dict(raw_status)
    assert st.state_name == "Charging"
    assert st.fan_power_name == "Balanced (102)"
    assert st.clean_area_m2 == 32.5
    assert st.clean_time_min == 30.0
    assert st.active_map_slot == 0
    assert st.water_box_status is False

    cons = ConsumableStatus.from_dict(
        {
            "main_brush_work_time": 100 * 3600,
            "side_brush_work_time": 50 * 3600,
            "filter_work_time": 75 * 3600,
            "sensor_dirty_time": 15 * 3600,
        }
    )
    assert cons.remaining_hours("main_brush_work_time") == 200.0
    assert cons.remaining_percent("filter_work_time") == 50
    assert cons.remaining_percent("sensor_dirty_time") == 50


def test_cli_save_env_refuses_ip_mismatch(tmp_path: Path) -> None:
    entries_data = {
        "data": {
            "entries": [
                {
                    "domain": "xiaomi_miot",
                    "title": "Other Device",
                    "data": {
                        "host": "192.168.1.150",
                        "token": FAKE_OTHER_TOKEN,
                        "model": "zhimi.airpurifier.mb3",
                    },
                }
            ]
        }
    }
    cfg_file = tmp_path / "core.config_entries"
    cfg_file.write_text(json.dumps(entries_data), encoding="utf-8")
    env_file = tmp_path / ".env"

    # Should refuse to overwrite .env because 192.168.1.150 != 192.168.1.110
    rc = cli_main(
        [
            "--env-file",
            str(env_file),
            "extract-token",
            str(cfg_file),
            "--ip",
            "192.168.1.110",
            "--save-env",
        ]
    )
    assert rc == 1
    assert not env_file.exists()


def test_cli_probe_dry_run_sends_no_packets(tmp_path: Path, capsys: Any) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"ROBOROCK_IP=192.168.1.110\nROBOROCK_TOKEN={FAKE_S6_TOKEN}\n",
        encoding="utf-8",
    )
    rc = cli_main(["--env-file", str(env_file), "probe", "--dry-run"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "192.168.1.110:54321" in captured
    assert "a1b2...8f90" in captured
    assert FAKE_S6_TOKEN not in captured
    assert "Safety Check     : PASS" in captured


