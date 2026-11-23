"""Unit tests for binary RRMap parsing, coordinate transforms, PNG/SVG rendering, and RC4."""

import gzip
import struct
from pathlib import Path

from custom_components.local_roborock.cli import main as cli_main
from custom_components.local_roborock.map_editor import (
    MapRestrictionEditor,
    generate_interactive_map_html,
)
from custom_components.local_roborock.map_fetcher import (
    compute_signed_nonce,
    decrypt_rc4_bytes,
    encrypt_rc4_str,
    generate_nonce,
)
from custom_components.local_roborock.rrmap import (
    RRMapBlockType,
    parse_rrmap,
    render_rrmap_png,
    render_rrmap_svg,
    save_map_artifacts,
)
from tests.test_client import TEST_TOKEN_HEX


def _build_synthetic_rrmap() -> bytes:
    """Build a synthetic GZIP-compressed RRMap containing rooms 16, 17, 18, dock, robot, walls."""
    width = 20
    height = 20
    left = 500
    top = 500

    # Create a 20x20 grid with walls on borders and rooms 16, 17, 18 inside
    pixels = bytearray(width * height)
    for y in range(height):
        for x in range(width):
            idx = y * width + x
            if x == 0 or x == width - 1 or y == 0 or y == height - 1:
                pixels[idx] = 1  # Wall
            elif x < 7:
                pixels[idx] = (16 << 3) | 3  # Room 16
            elif x < 14:
                pixels[idx] = (17 << 3) | 3  # Room 17
            else:
                pixels[idx] = (18 << 3) | 3  # Room 18

    # Block 1: Charger Location (25600, 25600)
    b1_payload = struct.pack("<II", 25600, 25600)
    b1 = struct.pack("<HHI", RRMapBlockType.CHARGER_LOCATION, 8, len(b1_payload)) + b1_payload

    # Block 2: Image (24-byte header: type=2, hlen=24, dlen=400, top, left, height, width)
    b2_hdr = struct.pack(
        "<HHIiiii",
        RRMapBlockType.IMAGE,
        24,
        len(pixels),
        top,
        left,
        height,
        width,
    )
    b2 = b2_hdr + bytes(pixels)

    # Block 3: Path (20-byte header + 2 points)
    b3_payload = struct.pack("<HHHH", 25500, 25500, 25600, 25600)
    b3_hdr = struct.pack("<HHIIII", RRMapBlockType.PATH, 20, len(b3_payload), 2, 4, 0)
    b3 = b3_hdr + b3_payload

    # Block 8: Robot Position (25550, 25550, angle=90)
    b8_payload = struct.pack("<IIi", 25550, 25550, 90)
    b8 = struct.pack("<HHI", RRMapBlockType.ROBOT_POSITION, 8, len(b8_payload)) + b8_payload

    # Block 9: Forbidden Zones (1 zone)
    b9_payload = struct.pack("<HHHHHHHH", 25100, 25100, 25300, 25100, 25300, 25300, 25100, 25300)
    b9_hdr = struct.pack("<HHII", RRMapBlockType.FORBIDDEN_ZONES, 12, len(b9_payload), 1)
    b9 = b9_hdr + b9_payload

    # Block 10: Virtual Walls (1 wall)
    b10_payload = struct.pack("<HHHH", 25200, 25200, 25400, 25200)
    b10_hdr = struct.pack("<HHII", RRMapBlockType.VIRTUAL_WALLS, 12, len(b10_payload), 1)
    b10 = b10_hdr + b10_payload

    body = b1 + b2 + b3 + b8 + b9 + b10
    file_hdr = b"rr" + struct.pack("<HIHHII", 20, len(body), 1, 0, 7, 42)
    return gzip.compress(file_hdr + body)


def test_parse_and_render_rrmap(tmp_path: Path) -> None:
    gz_data = _build_synthetic_rrmap()
    parsed = parse_rrmap(gz_data)

    assert parsed.major_version == 1
    assert parsed.map_index == 7
    assert parsed.image_width == 20
    assert parsed.image_height == 20
    assert parsed.charger is not None and parsed.charger.x == 25600
    assert parsed.robot is not None and parsed.robot.angle == 90
    assert len(parsed.path) == 2
    assert len(parsed.forbidden_zones) == 1
    assert len(parsed.virtual_walls) == 1
    assert set(parsed.rooms.keys()) == {16, 17, 18}

    # Verify coordinate round-trip (mm <-> pixel)
    px_x, px_y = parsed.mm_to_pixel(25500, 25500)
    mm_x, mm_y = parsed.pixel_to_mm(px_x, px_y)
    assert mm_x == 25500
    assert mm_y == 25500

    # Verify PNG and SVG generation
    png_bytes = render_rrmap_png(parsed, scale=4)
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"

    svg_text = render_rrmap_svg(parsed, room_names={16: "Kitchen"})
    assert "<svg" in svg_text
    assert "ID 16: Kitchen" in svg_text

    # Verify save_map_artifacts writes all 4 files
    _, paths = save_map_artifacts(
        gz_data,
        output_dir=tmp_path,
        base_name="test_slot_0",
        room_names={16: "Kitchen"},
    )
    for key in ("rrmap", "png", "svg", "json"):
        assert paths[key].is_file()


def test_xiaomi_rc4_encryption_roundtrip() -> None:
    ssecurity = "MTIzNDU2Nzg5MDEyMzQ1Ng=="
    nonce = generate_nonce(1700000000000)
    s_nonce = compute_signed_nonce(ssecurity, nonce)

    plaintext = '{"obj_name":"robomap/100000000/0"}'
    encrypted_b64 = encrypt_rc4_str(s_nonce, plaintext)
    decrypted = decrypt_rc4_bytes(s_nonce, encrypted_b64)
    assert decrypted.decode("utf-8") == plaintext


def test_phase6_and_7_map_editor_and_interactive_ui(tmp_path: Path, capsys: object) -> None:
    gz_data = _build_synthetic_rrmap()
    parsed = parse_rrmap(gz_data)

    # 1. Test MapRestrictionEditor preserves existing walls/zones and adds new ones
    editor = MapRestrictionEditor.from_parsed_map(parsed)
    assert len(editor.virtual_walls) == 1
    assert len(editor.forbidden_zones) == 1
    assert len(editor.no_mop_zones) == 0

    editor.add_virtual_wall(25000, 25000, 26000, 25000)
    editor.add_forbidden_zone_rect(24000, 24000, 24500, 24500)
    editor.add_no_mop_zone_rect(26000, 26000, 26500, 26500)

    params = editor.to_save_map_params()
    # 2 walls (type 0) + 2 no-go zones (type 1) + 1 no-mop zone (type 2) = 5 items
    assert len(params) == 5
    assert [p[0] for p in params] == [0, 0, 1, 1, 2]

    backup_file = editor.save_backup(tmp_path, floor_slot=1)
    assert backup_file.is_file()

    # 2. Test standalone Interactive Map HTML generation (Phase 6)
    html_str = generate_interactive_map_html(
        {1: ("Upper floor", parsed, {16: "Ložnice", 17: "Chodba", 18: "Dětský pokoj"})},
        active_slot=1,
    )
    assert "LocalRoborock S6" in html_str
    assert "Ložnice" in html_str
    assert "data:image/png;base64," in html_str

    # Also verify empty floors_data generates valid welcome UI for brand-new users
    empty_html = generate_interactive_map_html({}, active_slot=0)
    assert "empty-map-card" in empty_html

    # 3. Test CLI map ui and map-edit --dry-run
    (tmp_path / "map_slot_1.rrmap").write_bytes(gz_data)
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"ROBOROCK_IP=192.168.1.110\nROBOROCK_TOKEN={TEST_TOKEN_HEX}\n",
        encoding="utf-8",
    )

    rc_ui = cli_main(
        [
            "--env-file",
            str(env_file),
            "map",
            "--output-dir",
            str(tmp_path),
            "ui",
            "--floor",
            "1",
        ]
    )
    assert rc_ui == 0
    assert (tmp_path / "interactive_map.html").is_file()

    rc_merge = cli_main(
        [
            "--env-file",
            str(env_file),
            "map-edit",
            "--map-dir",
            str(tmp_path),
            "--floor",
            "1",
            "merge-rooms",
            "1",
            "2",
            "--dry-run",
        ]
    )
    assert rc_merge == 0

    rc_nogo = cli_main(
        [
            "--env-file",
            str(env_file),
            "map-edit",
            "--map-dir",
            str(tmp_path),
            "--floor",
            "1",
            "add-nogo",
            "24000",
            "24000",
            "25000",
            "25000",
            "--dry-run",
        ]
    )
    assert rc_nogo == 0

