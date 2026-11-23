"""Binary RRMap (Roborock Map) decoder, coordinate converter, and SVG/PNG renderer."""

import gzip
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class RRMapParseError(Exception):
    """Raised when a binary RRMap buffer cannot be decompressed or parsed."""


class RRMapBlockType:
    """Standard block type identifiers in the Roborock RRMap format."""

    CHARGER_LOCATION = 1
    IMAGE = 2
    PATH = 3
    GOTO_PATH = 4
    GOTO_PREDICTED_PATH = 5
    CURRENTLY_CLEANED_ZONES = 6
    GOTO_TARGET = 7
    ROBOT_POSITION = 8
    FORBIDDEN_ZONES = 9
    VIRTUAL_WALLS = 10
    CURRENTLY_CLEANED_BLOCKS = 11
    NO_MOP_ZONES = 12
    OBSTACLES = 13
    IGNORED_OBSTACLES = 14
    OBSTACLES_WITH_PHOTO = 15
    IGNORED_OBSTACLES_WITH_PHOTO = 16
    CARPET_MAP = 17
    MOP_PATH = 18
    CARPET_FORBIDDEN = 19
    SMART_ZONE_PATH_TYPE = 20
    SMART_ZONE = 21
    CUSTOM_CARPET = 22
    CL_FORBIDDEN_ZONES = 23
    FLOOR_MAP = 24
    FURNITURE = 25
    DOCK_TYPE = 26
    ENEMIES = 27
    DIGEST = 1024


@dataclass(frozen=True)
class PointMM:
    """2D coordinate in Roborock millimeter space ([25600, 25600] = charging dock)."""

    x: int
    y: int
    angle: int = 0


@dataclass(frozen=True)
class RoomSegmentStats:
    """Spatial summary of a discovered room segment inside the IMAGE block."""

    segment_id: int
    pixel_count: int
    min_px_x: int
    min_px_y: int
    max_px_x: int
    max_px_y: int
    centroid_px_x: float
    centroid_px_y: float
    centroid_mm_x: int
    centroid_mm_y: int

    @property
    def area_m2(self) -> float:
        """Each map pixel represents a 5 cm x 5 cm square (0.0025 m²)."""
        return round(self.pixel_count * 0.0025, 2)


@dataclass
class ParsedRRMap:
    """Structured representation of a decoded Roborock RRMap file."""

    major_version: int
    minor_version: int
    map_index: int
    map_sequence: int
    image_top: int = 0
    image_left: int = 0
    image_height: int = 0
    image_width: int = 0
    image_pixels: bytes = b""
    charger: PointMM | None = None
    robot: PointMM | None = None
    goto_target: PointMM | None = None
    path: list[tuple[int, int]] = field(default_factory=list)
    goto_path: list[tuple[int, int]] = field(default_factory=list)
    predicted_path: list[tuple[int, int]] = field(default_factory=list)
    cleaned_zones: list[tuple[int, int, int, int]] = field(default_factory=list)
    forbidden_zones: list[tuple[int, int, int, int, int, int, int, int]] = field(
        default_factory=list
    )
    no_mop_zones: list[tuple[int, int, int, int, int, int, int, int]] = field(
        default_factory=list
    )
    virtual_walls: list[tuple[int, int, int, int]] = field(default_factory=list)
    cleaned_blocks: list[int] = field(default_factory=list)
    rooms: dict[int, RoomSegmentStats] = field(default_factory=dict)
    raw_block_types: list[int] = field(default_factory=list)

    def mm_to_pixel(self, x_mm: int, y_mm: int) -> tuple[float, float]:
        """Convert Roborock millimeter coordinates to image pixel coordinates (px_x, px_y).

        Note: In RRMap image space, Y=0 is at the bottom (`image_top`), while in screen/SVG
        rendering Y=0 is at the top (`image_height - 1 - rel_y`).
        """
        rel_x = (x_mm / 50.0) - self.image_left
        rel_y = (y_mm / 50.0) - self.image_top
        screen_y = (self.image_height - 1) - rel_y
        return round(rel_x, 2), round(screen_y, 2)

    def pixel_to_mm(self, px_x: float, screen_y: float) -> tuple[int, int]:
        """Convert screen/image pixel coordinates back to Roborock millimeter coordinates."""
        rel_y = (self.image_height - 1) - screen_y
        x_mm = round((px_x + self.image_left) * 50.0)
        y_mm = round((rel_y + self.image_top) * 50.0)
        return int(x_mm), int(y_mm)

    def to_summary_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable summary of the parsed map metadata and rooms."""
        return {
            "version": f"{self.major_version}.{self.minor_version}",
            "map_index": self.map_index,
            "map_sequence": self.map_sequence,
            "dimensions": {
                "width": self.image_width,
                "height": self.image_height,
                "left": self.image_left,
                "top": self.image_top,
            },
            "charger": (
                {"x": self.charger.x, "y": self.charger.y} if self.charger else None
            ),
            "robot": (
                {"x": self.robot.x, "y": self.robot.y, "angle": self.robot.angle}
                if self.robot
                else None
            ),
            "goto_target": (
                {"x": self.goto_target.x, "y": self.goto_target.y}
                if self.goto_target
                else None
            ),
            "path_point_count": len(self.path),
            "virtual_walls": [list(w) for w in self.virtual_walls],
            "forbidden_zones": [list(z) for z in self.forbidden_zones],
            "no_mop_zones": [list(z) for z in self.no_mop_zones],
            "cleaned_zones": [list(z) for z in self.cleaned_zones],
            "cleaned_blocks": self.cleaned_blocks,
            "raw_block_types": self.raw_block_types,
            "rooms": [
                {
                    "segment_id": r.segment_id,
                    "area_m2": r.area_m2,
                    "pixel_count": r.pixel_count,
                    "centroid_mm": [r.centroid_mm_x, r.centroid_mm_y],
                    "centroid_px": [round(r.centroid_px_x, 1), round(r.centroid_px_y, 1)],
                }
                for _, r in sorted(self.rooms.items())
            ],
        }


def decompress_rrmap(data: bytes) -> bytes:
    """Decompress a GZIP/zlib-compressed RRMap buffer or strip optional wrapper header."""
    if not data:
        raise RRMapParseError("Empty map buffer")

    # Already uncompressed 'rr' magic
    if data[:2] == b"rr":
        return data

    # GZIP magic (0x1f 0x8b)
    if data[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(data)
        except OSError as err:
            raise RRMapParseError(f"GZIP decompression failed: {err}") from err
        return decompress_rrmap(raw)

    # Check if there is a 28-byte wrapper header before 'rr' or before GZIP magic
    rr_idx = data.find(b"rr", 0, 64)
    if rr_idx > 0:
        return data[rr_idx:]

    gz_idx = data.find(b"\x1f\x8b", 0, 64)
    if gz_idx > 0:
        try:
            return decompress_rrmap(gzip.decompress(data[gz_idx:]))
        except OSError as err:
            raise RRMapParseError(f"Offset GZIP decompression failed: {err}") from err

    # Fallback: raw zlib stream
    try:
        raw = zlib.decompress(data)
        return decompress_rrmap(raw)
    except zlib.error as err:
        raise RRMapParseError("Data is neither 'rr' magic nor GZIP/zlib compressed") from err


def _extract_room_stats(
    pixels: bytes,
    width: int,
    height: int,
    left: int,
    top: int,
) -> dict[int, RoomSegmentStats]:
    """Scan the IMAGE pixel array to compute bounding boxes, areas, and centroids per room."""
    if width <= 0 or height <= 0 or len(pixels) < width * height:
        return {}

    # Accumulate [count, sum_x, sum_y, min_x, min_y, max_x, max_y] per segment_id
    acc: dict[int, list[int]] = {}
    for idx in range(width * height):
        b = pixels[idx]
        low = b & 0x07
        if low == 0 or low == 1:
            # 0 = background/unknown, 1 = wall
            continue
        seg_id = b >> 3
        if seg_id <= 0:
            continue

        px_x = idx % width
        raw_y = idx // width
        screen_y = (height - 1) - raw_y

        entry = acc.get(seg_id)
        if entry is None:
            acc[seg_id] = [1, px_x, screen_y, px_x, screen_y, px_x, screen_y]
        else:
            entry[0] += 1
            entry[1] += px_x
            entry[2] += screen_y
            if px_x < entry[3]:
                entry[3] = px_x
            if screen_y < entry[4]:
                entry[4] = screen_y
            if px_x > entry[5]:
                entry[5] = px_x
            if screen_y > entry[6]:
                entry[6] = screen_y

    stats: dict[int, RoomSegmentStats] = {}
    for seg_id, vals in acc.items():
        count, sum_x, sum_y, min_x, min_y, max_x, max_y = vals
        cx = sum_x / count
        cy = sum_y / count
        rel_y = (height - 1) - cy
        mm_x = round((cx + left) * 50.0)
        mm_y = round((rel_y + top) * 50.0)
        stats[seg_id] = RoomSegmentStats(
            segment_id=seg_id,
            pixel_count=count,
            min_px_x=min_x,
            min_px_y=min_y,
            max_px_x=max_x,
            max_px_y=max_y,
            centroid_px_x=cx,
            centroid_px_y=cy,
            centroid_mm_x=int(mm_x),
            centroid_mm_y=int(mm_y),
        )
    return stats


def _parse_path_points(payload: bytes) -> list[tuple[int, int]]:
    points: list[tuple[int, int]] = []
    for i in range(0, len(payload) - 3, 4):
        x, y = struct.unpack_from("<HH", payload, i)
        points.append((x, y))
    return points


def _extract_room_polygons(
    pixels: bytes,
    width: int,
    height: int,
    step: int = 2,
) -> dict[int, list[tuple[int, int, int, int]]]:
    """Extract horizontal run-length rectangles (x, screen_y, w, h) per room segment."""
    if width <= 0 or height <= 0 or len(pixels) < width * height:
        return {}
    runs: dict[int, list[tuple[int, int, int, int]]] = {}
    for raw_y in range(0, height, step):
        screen_y = (height - 1) - raw_y
        row_off = raw_y * width
        cur_seg = 0
        start_x = 0
        for px_x in range(width):
            b = pixels[row_off + px_x]
            low = b & 0x07
            seg = (b >> 3) if low not in (0, 1) else 0
            if seg != cur_seg:
                if cur_seg > 0:
                    runs.setdefault(cur_seg, []).append(
                        (start_x, max(0, screen_y - step + 1), px_x - start_x, step)
                    )
                cur_seg = seg
                start_x = px_x
        if cur_seg > 0:
            runs.setdefault(cur_seg, []).append(
                (start_x, max(0, screen_y - step + 1), width - start_x, step)
            )
    return runs


def extract_room_run_lengths(
    parsed: ParsedRRMap, step: int = 2
) -> dict[int, list[tuple[int, int, int, int]]]:
    """Return compact horizontal rectangles per room segment for SVG/Canvas hit-testing."""
    return _extract_room_polygons(
        parsed.image_pixels,
        parsed.image_width,
        parsed.image_height,
        step=step,
    )


def compute_content_bbox(
    parsed: ParsedRRMap, padding_px: int = 8
) -> tuple[int, int, int, int]:
    """Compute tight bounding box (min_x, min_screen_y, crop_w, crop_h) around non-empty pixels."""
    w = parsed.image_width
    h = parsed.image_height
    pixels = parsed.image_pixels
    if w <= 0 or h <= 0 or len(pixels) < w * h:
        return (0, 0, max(1, w), max(1, h))

    min_x, max_x = w, 0
    min_sy, max_sy = h, 0
    found = False

    for raw_y in range(h):
        screen_y = (h - 1) - raw_y
        row_off = raw_y * w
        for px_x in range(w):
            if (pixels[row_off + px_x] & 0x07) != 0:
                found = True
                if px_x < min_x:
                    min_x = px_x
                if px_x > max_x:
                    max_x = px_x
                if screen_y < min_sy:
                    min_sy = screen_y
                if screen_y > max_sy:
                    max_sy = screen_y

    if not found:
        return (0, 0, w, h)

    x0 = max(0, min_x - padding_px)
    y0 = max(0, min_sy - padding_px)
    x1 = min(w - 1, max_x + padding_px)
    y1 = min(h - 1, max_sy + padding_px)
    return (x0, y0, max(1, x1 - x0 + 1), max(1, y1 - y0 + 1))


def _parse_quad_zones(
    payload: bytes,
) -> list[tuple[int, int, int, int, int, int, int, int]]:
    zones: list[tuple[int, int, int, int, int, int, int, int]] = []
    for i in range(0, len(payload) - 15, 16):
        coords = struct.unpack_from("<HHHHHHHH", payload, i)
        zones.append(coords)
    return zones


def _parse_rect_or_wall_lines(payload: bytes) -> list[tuple[int, int, int, int]]:
    items: list[tuple[int, int, int, int]] = []
    for i in range(0, len(payload) - 7, 8):
        coords = struct.unpack_from("<HHHH", payload, i)
        items.append(coords)
    return items


def parse_rrmap(raw_input: bytes) -> ParsedRRMap:
    """Decode a raw or GZIP-compressed Roborock `.rrmap` binary buffer."""
    buf = decompress_rrmap(raw_input)
    if len(buf) < 20 or buf[:2] != b"rr":
        raise RRMapParseError("Invalid RRMap header: missing 'rr' magic bytes")

    header_len, _data_len, major, minor, map_idx, map_seq = struct.unpack_from(
        "<HIHHII", buf, 2
    )
    parsed = ParsedRRMap(
        major_version=major,
        minor_version=minor,
        map_index=map_idx,
        map_sequence=map_seq,
    )

    offset = header_len
    total_len = len(buf)

    while offset + 8 <= total_len:
        btype, hlen, dlen = struct.unpack_from("<HHI", buf, offset)
        if hlen < 8 or offset + hlen + dlen > total_len:
            break

        parsed.raw_block_types.append(btype)
        block_header = buf[offset : offset + hlen]
        payload = buf[offset + hlen : offset + hlen + dlen]

        if btype == RRMapBlockType.CHARGER_LOCATION and dlen >= 8:
            cx, cy = struct.unpack_from("<II", payload, 0)
            parsed.charger = PointMM(x=cx, y=cy)

        elif btype == RRMapBlockType.IMAGE:
            g3_offset = 4 if hlen > 24 else 0
            if len(block_header) >= 24 + g3_offset:
                top, left, height, width = struct.unpack_from(
                    "<iiii", block_header, 8 + g3_offset
                )
                parsed.image_top = top
                parsed.image_left = left
                parsed.image_height = height
                parsed.image_width = width
                parsed.image_pixels = payload
                parsed.rooms = _extract_room_stats(payload, width, height, left, top)

        elif btype == RRMapBlockType.PATH:
            parsed.path = _parse_path_points(payload)

        elif btype == RRMapBlockType.GOTO_PATH:
            parsed.goto_path = _parse_path_points(payload)

        elif btype == RRMapBlockType.GOTO_PREDICTED_PATH:
            parsed.predicted_path = _parse_path_points(payload)

        elif btype == RRMapBlockType.CURRENTLY_CLEANED_ZONES:
            parsed.cleaned_zones = _parse_rect_or_wall_lines(payload)

        elif btype == RRMapBlockType.GOTO_TARGET and dlen >= 4:
            if dlen >= 8:
                gx, gy = struct.unpack_from("<II", payload, 0)
            else:
                gx, gy = struct.unpack_from("<HH", payload, 0)
            parsed.goto_target = PointMM(x=gx, y=gy)

        elif btype == RRMapBlockType.ROBOT_POSITION and dlen >= 8:
            rx, ry = struct.unpack_from("<II", payload, 0)
            rangle = struct.unpack_from("<i", payload, 8)[0] if dlen >= 12 else 0
            parsed.robot = PointMM(x=rx, y=ry, angle=rangle)

        elif btype == RRMapBlockType.FORBIDDEN_ZONES:
            parsed.forbidden_zones = _parse_quad_zones(payload)

        elif btype == RRMapBlockType.VIRTUAL_WALLS:
            parsed.virtual_walls = _parse_rect_or_wall_lines(payload)

        elif btype == RRMapBlockType.CURRENTLY_CLEANED_BLOCKS:
            parsed.cleaned_blocks = [int(b) for b in payload]

        elif btype == RRMapBlockType.NO_MOP_ZONES:
            parsed.no_mop_zones = _parse_quad_zones(payload)

        offset += hlen + dlen

    return parsed


# Distinct pastel room palette for clear room segment identification
ROOM_PALETTE: tuple[tuple[int, int, int], ...] = (
    (115, 178, 255),  # Blue
    (255, 166, 105),  # Orange
    (130, 217, 147),  # Green
    (242, 145, 195),  # Pink
    (186, 152, 255),  # Purple
    (255, 217, 112),  # Yellow
    (108, 219, 214),  # Teal
    (235, 143, 143),  # Coral
)


def _room_color(segment_id: int) -> tuple[int, int, int]:
    return ROOM_PALETTE[(segment_id - 1) % len(ROOM_PALETTE)]


def _encode_png_rgba(width: int, height: int, rgba_bytes: bytes) -> bytes:
    """Minimal zero-dependency PNG encoder using Python standard library zlib."""

    def _chunk(tag: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(tag + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    stride = width * 4
    raw_rows = bytearray()
    for y in range(height):
        raw_rows.append(0)  # Filter type 0 (None)
        row_start = y * stride
        raw_rows.extend(rgba_bytes[row_start : row_start + stride])

    compressed = zlib.compress(bytes(raw_rows), level=6)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", compressed)
        + _chunk(b"IEND", b"")
    )


# 3x5 bitmap font for rendering room ID digits directly into the PNG image
_DIGIT_FONT_3X5: dict[str, tuple[int, int, int, int, int]] = {
    "0": (0b111, 0b101, 0b101, 0b101, 0b111),
    "1": (0b010, 0b110, 0b010, 0b010, 0b111),
    "2": (0b111, 0b001, 0b111, 0b100, 0b111),
    "3": (0b111, 0b001, 0b111, 0b001, 0b111),
    "4": (0b101, 0b101, 0b111, 0b001, 0b001),
    "5": (0b111, 0b100, 0b111, 0b001, 0b111),
    "6": (0b111, 0b100, 0b111, 0b101, 0b111),
    "7": (0b111, 0b001, 0b001, 0b001, 0b001),
    "8": (0b111, 0b101, 0b111, 0b101, 0b111),
    "9": (0b111, 0b101, 0b111, 0b001, 0b111),
}


def _draw_scaled_pixel(
    buf: bytearray,
    img_w: int,
    img_h: int,
    x: int,
    y: int,
    color: tuple[int, int, int, int],
) -> None:
    if 0 <= x < img_w and 0 <= y < img_h:
        off = (y * img_w + x) * 4
        buf[off : off + 4] = bytes(color)


def _draw_text_digits(
    buf: bytearray,
    img_w: int,
    img_h: int,
    center_x: int,
    center_y: int,
    text: str,
    font_scale: int = 3,
) -> None:
    """Draw a high-contrast badge with numeric room ID centered at (center_x, center_y)."""
    digits = [c for c in text if c in _DIGIT_FONT_3X5]
    if not digits:
        return
    char_w = 3 * font_scale
    char_h = 5 * font_scale
    gap = font_scale
    total_w = len(digits) * char_w + (len(digits) - 1) * gap
    start_x = center_x - (total_w // 2)
    start_y = center_y - (char_h // 2)

    # White background pill with dark border
    pad = font_scale + 1
    for by in range(start_y - pad, start_y + char_h + pad):
        for bx in range(start_x - pad, start_x + total_w + pad):
            is_border = (
                bx == start_x - pad
                or bx == start_x + total_w + pad - 1
                or by == start_y - pad
                or by == start_y + char_h + pad - 1
            )
            col = (30, 30, 30, 255) if is_border else (255, 255, 255, 235)
            _draw_scaled_pixel(buf, img_w, img_h, bx, by, col)

    # Foreground digits
    cur_x = start_x
    for ch in digits:
        rows = _DIGIT_FONT_3X5[ch]
        for r_idx, row_bits in enumerate(rows):
            for c_idx in range(3):
                if (row_bits >> (2 - c_idx)) & 1:
                    for sy in range(font_scale):
                        for sx in range(font_scale):
                            _draw_scaled_pixel(
                                buf,
                                img_w,
                                img_h,
                                cur_x + c_idx * font_scale + sx,
                                start_y + r_idx * font_scale + sy,
                                (15, 23, 42, 255),
                            )
        cur_x += char_w + gap


def render_rrmap_png(parsed: ParsedRRMap, scale: int = 4) -> bytes:
    """Render a ParsedRRMap into a crisp RGBA PNG image with room ID badges, dock, and robot."""
    w = parsed.image_width
    h = parsed.image_height
    if w <= 0 or h <= 0 or len(parsed.image_pixels) < w * h:
        raise RRMapParseError("Map has no valid IMAGE raster to render")

    out_w = w * scale
    out_h = h * scale
    rgba = bytearray(out_w * out_h * 4)

    # 1. Fill base occupancy and room grid (flipped vertically so Y=0 is top)
    pixels = parsed.image_pixels
    for raw_y in range(h):
        screen_y = (h - 1) - raw_y
        row_offset = raw_y * w
        for px_x in range(w):
            b = pixels[row_offset + px_x]
            low = b & 0x07
            if low == 0:
                color = (24, 28, 36, 255)  # Dark background
            elif low == 1:
                color = (75, 85, 99, 255)  # Wall
            else:
                seg_id = b >> 3
                if seg_id > 0:
                    r, g, bl = _room_color(seg_id)
                    color = (r, g, bl, 255)
                else:
                    color = (209, 213, 219, 255)  # Unsegmented free floor

            col_bytes = bytes(color)
            base_sy = screen_y * scale
            base_sx = px_x * scale
            for sy in range(scale):
                dest_row = ((base_sy + sy) * out_w + base_sx) * 4
                for sx in range(scale):
                    off = dest_row + sx * 4
                    rgba[off : off + 4] = col_bytes

    # 2. Draw cleaning trajectory path points
    for pt_x, pt_y in parsed.path:
        px_x, px_y = parsed.mm_to_pixel(pt_x, pt_y)
        cx, cy = int(round(px_x * scale)), int(round(px_y * scale))
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                _draw_scaled_pixel(rgba, out_w, out_h, cx + dx, cy + dy, (255, 255, 255, 220))

    # 3. Draw Charger Dock marker (Green square)
    if parsed.charger:
        cpx, cpy = parsed.mm_to_pixel(parsed.charger.x, parsed.charger.y)
        cx, cy = int(round(cpx * scale)), int(round(cpy * scale))
        radius = max(3, scale + 2)
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                _draw_scaled_pixel(rgba, out_w, out_h, cx + dx, cy + dy, (16, 185, 129, 255))

    # 4. Draw Robot Position marker (Red/White circle)
    if parsed.robot:
        rpx, rpy = parsed.mm_to_pixel(parsed.robot.x, parsed.robot.y)
        rx, ry = int(round(rpx * scale)), int(round(rpy * scale))
        radius = max(4, scale + 3)
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                dist2 = dx * dx + dy * dy
                if dist2 <= radius * radius:
                    col = (
                        (239, 68, 68, 255)
                        if dist2 <= (radius - 2) ** 2
                        else (255, 255, 255, 255)
                    )
                    _draw_scaled_pixel(rgba, out_w, out_h, rx + dx, ry + dy, col)

    # 5. Draw Room Segment ID labels at each room centroid
    font_scale = max(2, scale - 1)
    for seg_id, stats in sorted(parsed.rooms.items()):
        cx = int(round(stats.centroid_px_x * scale))
        cy = int(round(stats.centroid_px_y * scale))
        _draw_text_digits(rgba, out_w, out_h, cx, cy, str(seg_id), font_scale=font_scale)

    return _encode_png_rgba(out_w, out_h, bytes(rgba))


def render_rrmap_svg(
    parsed: ParsedRRMap,
    room_names: dict[int, str] | None = None,
    scale: int = 4,
) -> str:
    """Render an interactive SVG vector overlay containing the raster PNG + room names & zones."""
    import base64

    png_bytes = render_rrmap_png(parsed, scale=scale)
    b64_png = base64.b64encode(png_bytes).decode("ascii")
    out_w = parsed.image_width * scale
    out_h = parsed.image_height * scale

    svg_lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {out_w} {out_h}" '
        f'width="{out_w}" height="{out_h}" style="background:#181c24;font-family:sans-serif">',
        f'  <image href="data:image/png;base64,{b64_png}" width="{out_w}" height="{out_h}"/>',
    ]

    # Virtual walls (Red lines)
    for x1, y1, x2, y2 in parsed.virtual_walls:
        px1, py1 = parsed.mm_to_pixel(x1, y1)
        px2, py2 = parsed.mm_to_pixel(x2, y2)
        svg_lines.append(
            f'  <line x1="{px1 * scale:.1f}" y1="{py1 * scale:.1f}" '
            f'x2="{px2 * scale:.1f}" y2="{py2 * scale:.1f}" '
            f'stroke="#ef4444" stroke-width="{max(2, scale)}" stroke-linecap="round"/>'
        )

    # Forbidden zones (Semi-transparent red polygons)
    for z in parsed.forbidden_zones:
        pts = []
        for i in range(0, 8, 2):
            px, py = parsed.mm_to_pixel(z[i], z[i + 1])
            pts.append(f"{px * scale:.1f},{py * scale:.1f}")
        svg_lines.append(
            f'  <polygon points="{" ".join(pts)}" fill="rgba(239,68,68,0.28)" '
            f'stroke="#ef4444" stroke-width="2"/>'
        )

    # No-mop zones (Semi-transparent blue polygons)
    for z in parsed.no_mop_zones:
        pts = []
        for i in range(0, 8, 2):
            px, py = parsed.mm_to_pixel(z[i], z[i + 1])
            pts.append(f"{px * scale:.1f},{py * scale:.1f}")
        svg_lines.append(
            f'  <polygon points="{" ".join(pts)}" fill="rgba(59,130,246,0.28)" '
            f'stroke="#3b82f6" stroke-width="2" stroke-dasharray="4,4"/>'
        )

    # Room name subtitles under the numeric badges
    names = room_names or {}
    for seg_id, stats in sorted(parsed.rooms.items()):
        cx = stats.centroid_px_x * scale
        cy = stats.centroid_px_y * scale + (scale * 5)
        custom = names.get(seg_id)
        label = f"ID {seg_id}: {custom} ({stats.area_m2} m²)" if custom else f"({stats.area_m2} m²)"
        svg_lines.append(
            f'  <text x="{cx:.1f}" y="{cy:.1f}" text-anchor="middle" '
            f'fill="#ffffff" stroke="#0f172a" stroke-width="3" paint-order="stroke" '
            f'font-size="{max(10, scale * 3)}px" font-weight="bold">{label}</text>'
        )

    svg_lines.append("</svg>\n")
    return "\n".join(svg_lines)


def save_map_artifacts(
    raw_map: bytes,
    output_dir: str | Path,
    base_name: str = "map_slot_0",
    room_names: dict[int, str] | None = None,
    scale: int = 4,
) -> tuple[ParsedRRMap, dict[str, Path]]:
    """Save raw .rrmap to cache and generate PNG, SVG, and JSON metadata artifacts."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    parsed = parse_rrmap(raw_map)
    rrmap_path = out_dir / f"{base_name}.rrmap"
    png_path = out_dir / f"{base_name}.png"
    svg_path = out_dir / f"{base_name}.svg"
    json_path = out_dir / f"{base_name}.json"

    import json

    if rrmap_path.resolve() != Path(output_dir).resolve():
        try:
            rrmap_path.write_bytes(raw_map)
        except OSError:
            pass

    png_bytes = render_rrmap_png(parsed, scale=scale)
    try:
        png_path.write_bytes(png_bytes)
    except OSError:
        if png_path.exists():
            png_path.unlink(missing_ok=True)
            png_path.write_bytes(png_bytes)

    svg_path.write_text(
        render_rrmap_svg(parsed, room_names=room_names, scale=scale),
        encoding="utf-8",
    )
    json_path.write_text(
        json.dumps(parsed.to_summary_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return parsed, {
        "rrmap": rrmap_path,
        "png": png_path,
        "svg": svg_path,
        "json": json_path,
    }
