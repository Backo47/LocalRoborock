"""Phase 6 & 7: Safe Map Editor & App-Style Fullscreen Interactive HTML5/Canvas Map UI."""

# ruff: noqa: E501

import base64
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .rrmap import ParsedRRMap, extract_room_run_lengths, render_rrmap_png


def rect_to_quad(x1: int, y1: int, x2: int, y2: int) -> tuple[int, int, int, int, int, int, int, int]:
    """Convert an axis-aligned rectangle (x1, y1, x2, y2) into an 8-coord Roborock polygon."""
    min_x, max_x = min(int(x1), int(x2)), max(int(x1), int(x2))
    min_y, max_y = min(int(y1), int(y2)), max(int(y1), int(y2))
    if min_x == max_x or min_y == max_y:
        raise ValueError("Zone rectangle must have non-zero width and height")
    return (min_x, max_y, max_x, max_y, max_x, min_y, min_x, min_y)


@dataclass
class MapRestrictionEditor:
    """Safe stateful editor for Roborock `save_map` restrictions (walls, no-go, no-mop zones)."""

    virtual_walls: list[tuple[int, int, int, int]] = field(default_factory=list)
    forbidden_zones: list[tuple[int, int, int, int, int, int, int, int]] = field(
        default_factory=list
    )
    no_mop_zones: list[tuple[int, int, int, int, int, int, int, int]] = field(
        default_factory=list
    )

    @classmethod
    def from_parsed_map(cls, parsed: ParsedRRMap) -> "MapRestrictionEditor":
        """Initialize the editor with all existing walls and zones from a decoded RRMap."""
        return cls(
            virtual_walls=list(parsed.virtual_walls),
            forbidden_zones=list(parsed.forbidden_zones),
            no_mop_zones=list(parsed.no_mop_zones),
        )

    def add_virtual_wall(self, x1: int, y1: int, x2: int, y2: int) -> None:
        if x1 == x2 and y1 == y2:
            raise ValueError("Virtual wall endpoints must be distinct")
        self.virtual_walls.append((int(x1), int(y1), int(x2), int(y2)))

    def add_forbidden_zone_rect(self, x1: int, y1: int, x2: int, y2: int) -> None:
        self.forbidden_zones.append(rect_to_quad(x1, y1, x2, y2))

    def add_no_mop_zone_rect(self, x1: int, y1: int, x2: int, y2: int) -> None:
        self.no_mop_zones.append(rect_to_quad(x1, y1, x2, y2))

    def clear_all(self) -> None:
        self.virtual_walls.clear()
        self.forbidden_zones.clear()
        self.no_mop_zones.clear()

    def to_save_map_params(self) -> list[list[int]]:
        """Serialize current walls and zones into the exact `save_map` miIO parameter array."""
        payload: list[list[int]] = []
        for x1, y1, x2, y2 in self.virtual_walls:
            payload.append([0, int(x1), int(y1), int(x2), int(y2)])
        for z in self.forbidden_zones:
            payload.append([1, *[int(c) for c in z]])
        for mz in self.no_mop_zones:
            payload.append([2, *[int(c) for c in mz]])
        return payload

    def save_backup(self, backup_dir: str | Path, floor_slot: int) -> Path:
        """Write a timestamped JSON backup of the current restrictions before committing."""
        out_dir = Path(backup_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        backup_path = out_dir / f"restrictions_backup_slot_{floor_slot}_{ts}.json"
        data = {
            "floor_slot": floor_slot,
            "timestamp": ts,
            "virtual_walls": [list(w) for w in self.virtual_walls],
            "forbidden_zones": [list(z) for z in self.forbidden_zones],
            "no_mop_zones": [list(mz) for mz in self.no_mop_zones],
            "save_map_params": self.to_save_map_params(),
        }
        backup_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return backup_path


def generate_interactive_map_html(
    floors_data: dict[int, tuple[str, ParsedRRMap, dict[int, str]]],
    active_slot: int = 1,
) -> str:
    """Generate a modern Xiaomi/Roborock App-inspired Fullscreen Interactive Map UI & Editor."""
    js_floors: dict[str, Any] = {}
    for slot, (floor_name, parsed, room_names) in sorted(floors_data.items()):
        png_bytes = render_rrmap_png(parsed, scale=4)
        b64_png = base64.b64encode(png_bytes).decode("ascii")
        runs = extract_room_run_lengths(parsed, step=2)

        rooms_meta: dict[str, Any] = {}
        for seg_id, stats in sorted(parsed.rooms.items()):
            rooms_meta[str(seg_id)] = {
                "id": seg_id,
                "name": room_names.get(seg_id, f"Room {seg_id}"),
                "area_m2": stats.area_m2,
                "cx_px": round(stats.centroid_px_x, 1),
                "cy_px": round(stats.centroid_px_y, 1),
                "cx_mm": stats.centroid_mm_x,
                "cy_mm": stats.centroid_mm_y,
                "runs": runs.get(seg_id, []),
            }

        raw_path = parsed.path or []
        step = max(1, len(raw_path) // 350)
        path_sampled = [[pt[0], pt[1]] for pt in raw_path[::step]]

        js_floors[str(slot)] = {
            "slot": slot,
            "name": floor_name,
            "width": parsed.image_width,
            "height": parsed.image_height,
            "left": parsed.image_left,
            "top": parsed.image_top,
            "scale": 4,
            "image_b64": f"data:image/png;base64,{b64_png}",
            "charger": (
                {"x": parsed.charger.x, "y": parsed.charger.y} if parsed.charger else None
            ),
            "robot": (
                {"x": parsed.robot.x, "y": parsed.robot.y, "angle": parsed.robot.angle}
                if parsed.robot
                else None
            ),
            "path": path_sampled,
            "predicted_path": [[pt[0], pt[1]] for pt in (parsed.predicted_path or [])],
            "virtual_walls": [list(w) for w in parsed.virtual_walls],
            "forbidden_zones": [list(z) for z in parsed.forbidden_zones],
            "no_mop_zones": [list(mz) for mz in parsed.no_mop_zones],
            "rooms": rooms_meta,
        }

    floors_json = json.dumps(js_floors, ensure_ascii=False)
    default_slot = str(active_slot if active_slot in floors_data else next(iter(floors_data)))

    return f"""<!DOCTYPE html>
<html lang="cs">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
  <title>LocalRoborock S6 – App UI & Map</title>
  <style>
    :root {{
      --bg: #0f172a;
      --glass: rgba(30, 41, 59, 0.86);
      --glass-border: rgba(255, 255, 255, 0.12);
      --accent: #38bdf8;
      --accent-dark: #0284c7;
      --text: #f8fafc;
      --muted: #94a3b8;
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
    }}
    * {{ box-sizing: border-box; user-select: none; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      height: 100vh;
      width: 100vw;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      position: relative;
    }}

    /* --- TOP HERO HEADER (Xiaomi Home App style) --- */
    header.hero {{
      position: absolute;
      top: 12px;
      left: 50%;
      transform: translateX(-50%);
      width: min(680px, calc(100vw - 24px));
      background: var(--glass);
      backdrop-filter: blur(14px);
      -webkit-backdrop-filter: blur(14px);
      border: 1px solid var(--glass-border);
      border-radius: 20px;
      padding: 12px 18px 14px;
      z-index: 20;
      box-shadow: 0 12px 32px rgba(0, 0, 0, 0.45);
    }}
    .hero-top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 10px;
    }}
    .floor-pill {{
      background: rgba(15, 23, 42, 0.75);
      color: var(--text);
      border: 1px solid var(--glass-border);
      border-radius: 12px;
      padding: 6px 10px;
      font-size: 0.8rem;
      font-weight: 600;
      cursor: pointer;
    }}
    .title-center {{
      text-align: center;
    }}
    .title-center h1 {{
      margin: 0;
      font-size: 1.15rem;
      font-weight: 700;
      letter-spacing: 0.01em;
    }}
    .status-subtitle {{
      font-size: 0.78rem;
      color: var(--success);
      font-weight: 600;
      margin-top: 2px;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 5px;
    }}
    .status-dot {{
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--success);
      display: inline-block;
    }}
    .icon-btn-top {{
      background: rgba(15, 23, 42, 0.75);
      color: var(--text);
      border: 1px solid var(--glass-border);
      border-radius: 12px;
      padding: 6px 11px;
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 5px;
      transition: all 0.15s;
    }}
    .icon-btn-top:hover, .icon-btn-top.active {{
      border-color: var(--accent);
      background: rgba(56, 189, 248, 0.2);
    }}
    .metrics-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      text-align: center;
      border-top: 1px solid rgba(255, 255, 255, 0.08);
      padding-top: 10px;
    }}
    .metric-box {{
      position: relative;
    }}
    .metric-box:not(:last-child)::after {{
      content: "";
      position: absolute;
      right: 0;
      top: 15%;
      height: 70%;
      width: 1px;
      background: rgba(255, 255, 255, 0.1);
    }}
    .metric-val {{
      font-size: 1.45rem;
      font-weight: 700;
      color: #ffffff;
      line-height: 1.1;
    }}
    .metric-unit {{
      font-size: 0.82rem;
      font-weight: 500;
      color: var(--muted);
      margin-left: 2px;
    }}
    .metric-label {{
      font-size: 0.74rem;
      color: var(--muted);
      margin-top: 3px;
    }}

    /* --- CENTER INTERACTIVE MAP CANVAS --- */
    main.map-viewport {{
      flex: 1;
      width: 100vw;
      height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 130px 16px 140px;
      overflow: hidden;
      position: relative;
    }}
    #canvas-wrap {{
      position: relative;
      display: flex;
      align-items: center;
      justify-content: center;
      max-width: 100%;
      max-height: 100%;
    }}
    canvas {{
      cursor: pointer;
      max-width: min(860px, 94vw);
      max-height: calc(100vh - 285px);
      width: auto;
      height: auto;
      border-radius: 16px;
      filter: drop-shadow(0 16px 40px rgba(0,0,0,0.55));
    }}

    /* --- FLOATING RIGHT ACTION BUTTONS (Pin & Go, Fan Power, Passes) --- */
    .fab-stack {{
      position: absolute;
      right: max(16px, calc(50vw - 340px));
      bottom: 148px;
      display: flex;
      flex-direction: column;
      gap: 10px;
      z-index: 20;
    }}
    .fab-btn {{
      width: 46px;
      height: 46px;
      border-radius: 14px;
      background: var(--glass);
      backdrop-filter: blur(12px);
      border: 1px solid var(--glass-border);
      color: var(--text);
      font-size: 1.15rem;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      box-shadow: 0 6px 20px rgba(0,0,0,0.35);
      transition: all 0.15s;
      position: relative;
    }}
    .fab-btn:hover, .fab-btn.active {{
      background: var(--accent);
      color: #0f172a;
      border-color: var(--accent);
    }}
    .fab-badge {{
      position: absolute;
      bottom: -4px;
      right: -4px;
      background: var(--accent);
      color: #0f172a;
      font-size: 0.65rem;
      font-weight: 800;
      padding: 1px 5px;
      border-radius: 8px;
    }}

    /* Fan Power Popover */
    .fan-popover {{
      position: absolute;
      right: 56px;
      bottom: 0;
      background: var(--glass);
      backdrop-filter: blur(16px);
      border: 1px solid var(--glass-border);
      border-radius: 14px;
      padding: 8px;
      display: none;
      flex-direction: column;
      gap: 4px;
      width: 165px;
      box-shadow: 0 10px 28px rgba(0,0,0,0.5);
    }}
    .fan-popover.open {{ display: flex; }}
    .fan-opt {{
      background: transparent;
      border: 1px solid transparent;
      color: var(--text);
      padding: 7px 10px;
      border-radius: 9px;
      text-align: left;
      font-size: 0.82rem;
      cursor: pointer;
      display: flex;
      justify-content: space-between;
    }}
    .fan-opt:hover {{ background: rgba(255,255,255,0.08); }}
    .fan-opt.active {{
      background: rgba(56, 189, 248, 0.22);
      border-color: var(--accent);
      color: var(--accent);
      font-weight: 700;
    }}

    /* --- BOTTOM FLOATING CONTROLS (Mode Tabs + Dock / Clean Bar) --- */
    footer.bottom-bar {{
      position: absolute;
      bottom: 14px;
      left: 50%;
      transform: translateX(-50%);
      width: min(680px, calc(100vw - 24px));
      z-index: 20;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}
    .mode-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }}
    .mode-pills {{
      display: inline-flex;
      background: var(--glass);
      backdrop-filter: blur(14px);
      border: 1px solid var(--glass-border);
      border-radius: 16px;
      padding: 4px;
      box-shadow: 0 8px 24px rgba(0,0,0,0.35);
    }}
    .mode-pill {{
      background: transparent;
      border: none;
      color: var(--muted);
      padding: 8px 18px;
      border-radius: 12px;
      font-size: 0.86rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s;
    }}
    .mode-pill.active {{
      background: var(--accent);
      color: #0f172a;
      font-weight: 700;
    }}
    .action-dock-clean {{
      display: grid;
      grid-template-columns: 1fr 1.35fr;
      gap: 10px;
      background: var(--glass);
      backdrop-filter: blur(14px);
      border: 1px solid var(--glass-border);
      border-radius: 20px;
      padding: 8px;
      box-shadow: 0 12px 32px rgba(0,0,0,0.45);
    }}
    .big-action-btn {{
      border: none;
      border-radius: 14px;
      padding: 13px 16px;
      font-size: 0.95rem;
      font-weight: 700;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      transition: all 0.15s;
    }}
    .btn-dock {{
      background: rgba(15, 23, 42, 0.75);
      color: var(--text);
      border: 1px solid var(--glass-border);
    }}
    .btn-dock:hover {{ border-color: var(--accent); }}
    .btn-clean {{
      background: linear-gradient(135deg, #38bdf8, #0284c7);
      color: #0f172a;
    }}
    .btn-clean.cleaning {{
      background: linear-gradient(135deg, #f59e0b, #d97706);
      color: #fff;
    }}

    /* --- SLIDE-OUT MAP EDITOR DRAWER --- */
    .drawer-backdrop {{
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,0.45);
      z-index: 30;
      display: none;
    }}
    .drawer-backdrop.open {{ display: block; }}
    aside.editor-drawer {{
      position: fixed;
      top: 0;
      right: 0;
      width: min(390px, 92vw);
      height: 100vh;
      background: #1e293b;
      border-left: 1px solid rgba(255,255,255,0.12);
      z-index: 35;
      transform: translateX(100%);
      transition: transform 0.25s cubic-bezier(0.16, 1, 0.3, 1);
      padding: 16px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 14px;
      box-shadow: -12px 0 40px rgba(0,0,0,0.6);
    }}
    aside.editor-drawer.open {{ transform: translateX(0); }}
    .drawer-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid rgba(255,255,255,0.1);
      padding-bottom: 10px;
    }}
    .drawer-card {{
      background: rgba(15, 23, 42, 0.55);
      border: 1px solid rgba(255,255,255,0.1);
      border-radius: 12px;
      padding: 12px;
    }}
    .drawer-card h3 {{
      margin: 0 0 8px 0;
      font-size: 0.78rem;
      text-transform: uppercase;
      color: var(--muted);
      letter-spacing: 0.05em;
    }}
    .tool-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 6px;
    }}
    .drawer-btn {{
      background: #0f172a;
      color: var(--text);
      border: 1px solid rgba(255,255,255,0.14);
      border-radius: 8px;
      padding: 8px 10px;
      font-size: 0.8rem;
      cursor: pointer;
      font-weight: 600;
    }}
    .drawer-btn.active {{
      background: var(--warning);
      color: #451a03;
      border-color: var(--warning);
    }}
    .zone-item {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 6px 9px;
      margin-bottom: 5px;
      border-radius: 8px;
      border: 1px solid rgba(255,255,255,0.1);
      font-size: 0.8rem;
    }}
    .zone-item.selected {{
      border-color: var(--warning);
      background: rgba(245, 158, 11, 0.18);
    }}
    .badge {{
      display: inline-block;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 0.7rem;
      font-weight: 700;
      margin-right: 6px;
    }}
    .badge-nogo {{ background: rgba(239, 68, 68, 0.25); color: #fca5a5; border: 1px solid #ef4444; }}
    .badge-nomop {{ background: rgba(59, 130, 246, 0.25); color: #93c5fd; border: 1px solid #3b82f6; }}
    .badge-wall {{ background: rgba(245, 158, 11, 0.25); color: #fcd34d; border: 1px solid #f59e0b; }}
    .danger-sm {{
      background: rgba(239, 68, 68, 0.18);
      color: #fca5a5;
      border: 1px solid rgba(239, 68, 68, 0.45);
      border-radius: 6px;
      padding: 4px 8px;
      font-size: 0.74rem;
      cursor: pointer;
    }}
    .danger-sm:hover {{ background: var(--danger); color: #fff; }}
    .warn-full-btn {{
      width: 100%;
      background: var(--warning);
      color: #451a03;
      border: none;
      border-radius: 9px;
      padding: 10px;
      font-weight: 700;
      font-size: 0.85rem;
      cursor: pointer;
      margin-top: 8px;
    }}

    /* Toast & Helper Banner */
    #toast {{
      position: fixed;
      top: 138px;
      left: 50%;
      transform: translateX(-50%);
      background: rgba(15, 23, 42, 0.92);
      border: 1px solid var(--accent);
      color: #fff;
      padding: 8px 16px;
      border-radius: 12px;
      font-size: 0.84rem;
      font-weight: 600;
      z-index: 40;
      display: none;
      box-shadow: 0 8px 24px rgba(0,0,0,0.45);
    }}
  </style>
</head>
<body>

  <!-- TOP HERO STATUS BAR -->
  <header class="hero">
    <div class="hero-top">
      <select id="floor-select" class="floor-pill" title="Přepnout patro"></select>
      <div class="title-center">
        <h1>Luxík (Roborock S6)</h1>
        <div class="status-subtitle">
          <span class="status-dot" id="status-dot"></span>
          <span id="status-text">Nabito (V doku)</span>
        </div>
      </div>
      <button id="btn-open-editor" class="icon-btn-top" title="Upravit zakázané zóny, místnosti a mapu">
        ⚙️ Editor
      </button>
    </div>

    <div class="metrics-grid">
      <div class="metric-box">
        <div><span class="metric-val" id="val-area">0.0</span><span class="metric-unit">m²</span></div>
        <div class="metric-label">Plocha úklidu</div>
      </div>
      <div class="metric-box">
        <div><span class="metric-val" id="val-battery">100</span><span class="metric-unit">%</span></div>
        <div class="metric-label">Baterie</div>
      </div>
      <div class="metric-box">
        <div><span class="metric-val" id="val-time">0</span><span class="metric-unit">min</span></div>
        <div class="metric-label">Doba úklidu</div>
      </div>
    </div>
  </header>

  <div id="toast"></div>

  <!-- FULLSCREEN INTERACTIVE MAP -->
  <main class="map-viewport">
    <div id="canvas-wrap">
      <canvas id="map-canvas"></canvas>
    </div>
  </main>

  <!-- RIGHT FLOATING QUICK BUTTONS (Pin & Go, Fan Speed, Repeat Passes) -->
  <div class="fab-stack">
    <button id="fab-goto" class="fab-btn" title="Pin & Go – Dojet na vybrané místo v mapě">
      📍
    </button>
    <button id="fab-repeat" class="fab-btn" title="Počet průjezdů (1× / 2× křížově / 3×)">
      🔁
      <span class="fab-badge" id="repeat-badge">1×</span>
    </button>
    <div style="position:relative;">
      <button id="fab-fan" class="fab-btn" title="Nastavit výkon sání">
        🌀
      </button>
      <div class="fan-popover" id="fan-popover">
        <button class="fan-opt" data-fan="Quiet"><span>🔉 Tichý</span><span>101</span></button>
        <button class="fan-opt active" data-fan="Balanced"><span>⚖️ Vyvážený</span><span>102</span></button>
        <button class="fan-opt" data-fan="Turbo"><span>🌪️ Turbo</span><span>103</span></button>
        <button class="fan-opt" data-fan="Max"><span>⚡ Max</span><span>104</span></button>
        <button class="fan-opt" data-fan="Gentle"><span>💧 Jemný / Mop</span><span>105</span></button>
      </div>
    </div>
  </div>

  <!-- BOTTOM BAR (Full / Rooms / Zones + Dock / Clean) -->
  <footer class="bottom-bar">
    <div class="mode-row">
      <div class="mode-pills" id="mode-pills">
        <button class="mode-pill active" data-mode="full">Celé patro</button>
        <button class="mode-pill" data-mode="rooms">Místnosti</button>
        <button class="mode-pill" data-mode="zone">Zóna</button>
      </div>
      <button id="btn-quick-refresh" class="icon-btn-top" title="Stáhnout čerstvý snímek mapy z vysavače">
        🔄 Mapa
      </button>
    </div>

    <div class="action-dock-clean">
      <button id="btn-dock" class="big-action-btn btn-dock">
        <span>🔌</span><span id="dock-label">Do doku</span>
      </button>
      <button id="btn-clean" class="big-action-btn btn-clean">
        <span id="clean-icon">🧹</span><span id="clean-label">Uklidit celé patro</span>
      </button>
    </div>
  </footer>

  <!-- SLIDE-OUT MAP EDITOR DRAWER -->
  <div class="drawer-backdrop" id="drawer-backdrop"></div>
  <aside class="editor-drawer" id="editor-drawer">
    <div class="drawer-header">
      <h2 style="margin:0;font-size:1rem;color:var(--accent);">⚙️ Editor mapy a zón</h2>
      <button id="btn-close-editor" class="drawer-btn">✕ Zavřít</button>
    </div>

    <div class="drawer-card">
      <h3>1. Kreslení do mapy</h3>
      <div style="font-size:0.75rem;color:var(--muted);margin-bottom:8px;">
        Zvolte nástroj a tažením myší v mapě nakreslete novou zónu, zeď nebo dělicí čáru místnosti:
      </div>
      <div class="tool-grid" id="editor-tools">
        <button class="drawer-btn" data-etool="nogo">🚫 + No-Go zóna</button>
        <button class="drawer-btn" data-etool="nomop">💧 + No-Mop zóna</button>
        <button class="drawer-btn" data-etool="wall">🧱 + Virtuální zeď</button>
        <button class="drawer-btn" data-etool="split">✂️ Rozdělit místnost</button>
      </div>
      <div id="editor-pending-box"></div>
    </div>

    <div class="drawer-card">
      <h3>2. Uložené zóny a zdi na tomto patře</h3>
      <div style="font-size:0.74rem;color:var(--muted);margin-bottom:8px;">
        Kliknutím na řádek zónu v mapě rozsvítíte žlutě, tlačítkem 🗑️ ji smažete:
      </div>
      <div id="restrictions-list"></div>
      <button id="btn-save-restrictions" class="warn-full-btn" style="display:none;">
        💾 Uložit změny zón do vysavače (save_map)
      </button>
    </div>

    <div class="drawer-card">
      <h3>3. Sloučení místností</h3>
      <div style="font-size:0.75rem;color:var(--muted);margin-bottom:6px;">
        V režimu <strong>Místnosti</strong> vyberte na mapě 2 sousední místnosti a klikněte zde:
      </div>
      <button id="btn-merge-rooms" class="drawer-btn" style="width:100%;">
        🔗 Sloučit 2 vybrané místnosti (merge_segment)
      </button>
    </div>

    <div class="drawer-card">
      <h3>4. Správa pater & Rychlé akce</h3>
      <div style="display:flex;flex-direction:column;gap:6px;">
        <button id="btn-locate-vac" class="drawer-btn">🔊 Kde jsi? (Zvuková lokalizace)</button>
        <button id="btn-new-mapping" class="drawer-btn">🗺️ Spustit mapování nového patra</button>
        <div style="display:flex;gap:6px;margin-top:4px;">
          <button id="btn-delete-floor" class="danger-sm" style="flex:1;padding:7px;">🗑️ Smazat patro</button>
          <button id="btn-reset-map" class="danger-sm" style="flex:1;padding:7px;">⚠️ Reset mapy</button>
        </div>
      </div>
    </div>
  </aside>

  <script>
    const FLOORS = {floors_json};
    let currentSlot = "{default_slot}";
    let currentMode = "full"; // 'full' | 'rooms' | 'zone' | 'goto' | 'edit_nogo' | 'edit_nomop' | 'edit_wall' | 'edit_split'
    let repeatCount = 1;
    let selectedRooms = []; // ordered array of segment IDs
    let gotoPoint = null;
    let dragStart = null;
    let dragCurrent = null;
    let selectedRestriction = null;
    let restrictionsDirty = false;
    let liveVacuumState = {{
      isCleaning: false,
      isPaused: false,
      isDocked: true,
      stateText: "Nabito (V doku)",
      fanSpeed: "Balanced",
      robotMm: null,
      chargerMm: null,
      pathMm: [],
      predictedPathMm: [],
    }};
    let baseImg = new Image();

    const canvas = document.getElementById("map-canvas");
    const ctx = canvas.getContext("2d");
    const floorSelect = document.getElementById("floor-select");
    const toastEl = document.getElementById("toast");
    const cleanBtn = document.getElementById("btn-clean");
    const cleanLabel = document.getElementById("clean-label");
    const cleanIcon = document.getElementById("clean-icon");
    const dockBtn = document.getElementById("btn-dock");
    const dockLabel = document.getElementById("dock-label");
    const restrictionsListEl = document.getElementById("restrictions-list");
    const btnSaveRestrictions = document.getElementById("btn-save-restrictions");
    const editorPendingBox = document.getElementById("editor-pending-box");

    // --- Home Assistant REST Token & Polling ---
    function getHaAccessToken() {{
      for (const win of [window, window.parent]) {{
        try {{
          if (!win) continue;
          const raw = win.localStorage.getItem("hassTokens");
          if (raw) {{
            const parsed = JSON.parse(raw);
            if (parsed && parsed.access_token) return parsed.access_token;
          }}
        }} catch (e) {{}}
      }}
      return null;
    }}

    function showToast(msg, isError = false) {{
      toastEl.style.display = "block";
      toastEl.style.borderColor = isError ? "#ef4444" : "#38bdf8";
      toastEl.style.color = isError ? "#fca5a5" : "#f8fafc";
      toastEl.textContent = msg;
      clearTimeout(window._toastTimer);
      window._toastTimer = setTimeout(() => {{ toastEl.style.display = "none"; }}, 4200);
    }}

    async function callHaService(domain, service, data) {{
      const token = getHaAccessToken();
      if (!token) {{
        showToast("Otevřete mapu uvnitř Home Assistantu pro přímé ovládání.", true);
        return false;
      }}
      try {{
        const resp = await fetch(`/api/services/${{domain}}/${{service}}`, {{
          method: "POST",
          headers: {{
            "Authorization": `Bearer ${{token}}`,
            "Content-Type": "application/json",
          }},
          body: JSON.stringify(data),
        }});
        if (!resp.ok) {{
          showToast(`Chyba příkazu (${{resp.status}})`, true);
          return false;
        }}
        setTimeout(pollLiveVacuumState, 800);
        return true;
      }} catch (err) {{
        showToast(`Chyba spojení: ${{err}}`, true);
        return false;
      }}
    }}

    async function pollLiveVacuumState() {{
      const token = getHaAccessToken();
      if (!token) return;
      try {{
        const resp = await fetch("/api/states", {{
          headers: {{ "Authorization": `Bearer ${{token}}` }},
        }});
        if (!resp.ok) return;
        const states = await resp.json();
        const vac = states.find(s => s.entity_id.startsWith("vacuum.") && s.attributes && s.attributes.roborock_state !== undefined);
        if (!vac) return;

        const attrs = vac.attributes;
        const bat = attrs.battery_level !== undefined ? attrs.battery_level : 100;
        const area = attrs.clean_area_m2 !== undefined ? attrs.clean_area_m2 : 0;
        const timeMin = attrs.clean_time_min !== undefined ? Math.round(attrs.clean_time_min) : 0;

        document.getElementById("val-area").textContent = area;
        document.getElementById("val-battery").textContent = bat;
        document.getElementById("val-time").textContent = timeMin;

        const rState = attrs.roborock_state || vac.state;
        const isCleaning = vac.state === "cleaning" || Boolean(attrs.in_cleaning);
        const isReturning = vac.state === "returning" || Boolean(attrs.in_returning);
        const isPaused = vac.state === "paused";
        const isDocked = vac.state === "docked";

        liveVacuumState.isCleaning = isCleaning;
        liveVacuumState.isPaused = isPaused;
        liveVacuumState.isDocked = isDocked;

        const dot = document.getElementById("status-dot");
        const stText = document.getElementById("status-text");
        if (isCleaning) {{
          stText.textContent = `Uklízí (${{rState}})`;
          stText.style.color = "#38bdf8";
          dot.style.background = "#38bdf8";
        }} else if (isReturning) {{
          stText.textContent = "Návrat do doku";
          stText.style.color = "#f59e0b";
          dot.style.background = "#f59e0b";
        }} else if (isPaused) {{
          stText.textContent = "Pozastaveno";
          stText.style.color = "#f59e0b";
          dot.style.background = "#f59e0b";
        }} else if (isDocked) {{
          stText.textContent = bat >= 100 ? "Nabito (V doku)" : `Nabíjení (${{bat}} %)`;
          stText.style.color = "#10b981";
          dot.style.background = "#10b981";
        }} else {{
          stText.textContent = rState;
          stText.style.color = "#94a3b8";
          dot.style.background = "#94a3b8";
        }}

        // Update fan popover active state
        if (attrs.fan_speed) {{
          liveVacuumState.fanSpeed = attrs.fan_speed;
          document.querySelectorAll(".fan-opt").forEach(btn => {{
            btn.classList.toggle("active", btn.dataset.fan.toLowerCase() === String(attrs.fan_speed).toLowerCase());
          }});
        }}

        // Live robot pose & cleaning trajectory on active floor
        const activeSlot = String(attrs.active_map_slot !== undefined ? attrs.active_map_slot : currentSlot);
        if (activeSlot === currentSlot) {{
          if (Array.isArray(attrs.robot_mm) && attrs.robot_mm.length >= 2) {{
            liveVacuumState.robotMm = {{
              x: attrs.robot_mm[0],
              y: attrs.robot_mm[1],
              angle: attrs.robot_mm[2] || 0,
            }};
          }}
          if (Array.isArray(attrs.charger_mm) && attrs.charger_mm.length >= 2) {{
            liveVacuumState.chargerMm = {{ x: attrs.charger_mm[0], y: attrs.charger_mm[1] }};
          }}
          if (Array.isArray(attrs.path_mm)) {{
            liveVacuumState.pathMm = attrs.path_mm;
          }}
          if (Array.isArray(attrs.predicted_path_mm)) {{
            liveVacuumState.predictedPathMm = attrs.predicted_path_mm;
          }}
          renderCanvas();
        }}

        updateBottomBarLabels();
      }} catch (e) {{}}
    }}

    function pxToMm(f, px, py) {{
      const relY = (f.height - 1) - py;
      const xMm = Math.round((px + f.left) * 50.0);
      const yMm = Math.round((relY + f.top) * 50.0);
      return [xMm, yMm];
    }}

    function mmToPx(f, xMm, yMm) {{
      const relX = (xMm / 50.0) - f.left;
      const relY = (yMm / 50.0) - f.top;
      const screenY = (f.height - 1) - relY;
      return [relX, screenY];
    }}

    function rectToQuad(x1, y1, x2, y2) {{
      const minX = Math.min(x1, x2), maxX = Math.max(x1, x2);
      const minY = Math.min(y1, y2), maxY = Math.max(y1, y2);
      return [minX, maxY, maxX, maxY, maxX, minY, minX, minY];
    }}

    function quadBounds(q) {{
      const xs = [q[0], q[2], q[4], q[6]];
      const ys = [q[1], q[3], q[5], q[7]];
      return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
    }}

    function hitTestRoom(f, px, py) {{
      const ix = Math.floor(px);
      const iy = Math.floor(py);
      for (const [segId, r] of Object.entries(f.rooms)) {{
        for (const [rx, ry, rw, rh] of r.runs) {{
          if (ix >= rx && ix <= rx + rw && iy >= ry && iy <= ry + rh) {{
            return parseInt(segId, 10);
          }}
        }}
      }}
      return null;
    }}

    function initApp() {{
      floorSelect.innerHTML = "";
      for (const [slot, f] of Object.entries(FLOORS)) {{
        const opt = document.createElement("option");
        opt.value = slot;
        opt.textContent = `${{f.name}}`;
        if (slot === currentSlot) opt.selected = true;
        floorSelect.appendChild(opt);
      }}
      loadFloor(currentSlot);
      pollLiveVacuumState();
      setInterval(pollLiveVacuumState, 3000);
    }}

    function loadFloor(slot) {{
      currentSlot = slot;
      selectedRooms = [];
      gotoPoint = null;
      dragStart = null;
      dragCurrent = null;
      selectedRestriction = null;
      restrictionsDirty = false;
      btnSaveRestrictions.style.display = "none";

      const f = FLOORS[slot];
      liveVacuumState.robotMm = f.robot;
      liveVacuumState.chargerMm = f.charger;
      liveVacuumState.pathMm = f.path || [];
      liveVacuumState.predictedPathMm = f.predicted_path || [];

      canvas.width = f.width * f.scale;
      canvas.height = f.height * f.scale;
      baseImg.onload = () => {{ renderCanvas(); }};
      baseImg.src = f.image_b64;
      renderRestrictionsList();
      updateBottomBarLabels();
    }}

    function updateBottomBarLabels() {{
      dockLabel.textContent = liveVacuumState.isDocked ? "Nabito" : "Do doku";
      if (liveVacuumState.isCleaning) {{
        cleanBtn.classList.add("cleaning");
        cleanIcon.textContent = "⏸️";
        cleanLabel.textContent = "Pozastavit úklid";
        return;
      }}
      cleanBtn.classList.remove("cleaning");
      cleanIcon.textContent = "🧹";

      if (currentMode === "goto" && gotoPoint) {{
        cleanIcon.textContent = "📍";
        cleanLabel.textContent = "Dojet na vybraný bod";
      }} else if (currentMode === "rooms") {{
        cleanLabel.textContent = selectedRooms.length > 0
          ? `Uklidit místnosti (${{selectedRooms.length}})`
          : "Vyberte místnosti v mapě";
      }} else if (currentMode === "zone") {{
        cleanLabel.textContent = (dragStart && dragCurrent)
          ? "Uklidit vybranou zónu"
          : "Nakreslete zónu v mapě";
      }} else {{
        cleanLabel.textContent = liveVacuumState.isPaused ? "Pokračovat v úklidu" : "Uklidit celé patro";
      }}
    }}

    function renderCanvas() {{
      const f = FLOORS[currentSlot];
      const s = f.scale;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(baseImg, 0, 0);

      // 1. Highlight selected rooms + order badge
      for (let i = 0; i < selectedRooms.length; i++) {{
        const segId = selectedRooms[i];
        const r = f.rooms[String(segId)];
        if (!r) continue;
        ctx.fillStyle = "rgba(56, 189, 248, 0.38)";
        for (const [rx, ry, rw, rh] of r.runs) {{
          ctx.fillRect(rx * s, ry * s, rw * s, rh * s);
        }}
      }}

      // 2. Draw saved Forbidden Zones (Red)
      f.forbidden_zones.forEach((z, idx) => {{
        const isSel = selectedRestriction && selectedRestriction.kind === "nogo" && selectedRestriction.index === idx;
        ctx.strokeStyle = isSel ? "#fde047" : "rgba(239, 68, 68, 0.85)";
        ctx.fillStyle = isSel ? "rgba(253, 224, 71, 0.35)" : "rgba(239, 68, 68, 0.22)";
        ctx.lineWidth = isSel ? 3 : 2;
        ctx.beginPath();
        for (let i = 0; i < 8; i += 2) {{
          const [px, py] = mmToPx(f, z[i], z[i+1]);
          if (i === 0) ctx.moveTo(px * s, py * s);
          else ctx.lineTo(px * s, py * s);
        }}
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
      }});

      // 3. Draw saved No-Mop Zones (Blue border)
      f.no_mop_zones.forEach((z, idx) => {{
        const isSel = selectedRestriction && selectedRestriction.kind === "nomop" && selectedRestriction.index === idx;
        ctx.strokeStyle = isSel ? "#fde047" : "rgba(59, 130, 246, 0.85)";
        ctx.fillStyle = isSel ? "rgba(253, 224, 71, 0.35)" : "rgba(59, 130, 246, 0.14)";
        ctx.lineWidth = isSel ? 3 : 2;
        ctx.beginPath();
        for (let i = 0; i < 8; i += 2) {{
          const [px, py] = mmToPx(f, z[i], z[i+1]);
          if (i === 0) ctx.moveTo(px * s, py * s);
          else ctx.lineTo(px * s, py * s);
        }}
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
      }});

      // 4. Draw saved Virtual Walls
      f.virtual_walls.forEach((w, idx) => {{
        const isSel = selectedRestriction && selectedRestriction.kind === "wall" && selectedRestriction.index === idx;
        ctx.strokeStyle = isSel ? "#fde047" : "#ef4444";
        ctx.lineWidth = isSel ? 5 : 3;
        const [x1, y1] = mmToPx(f, w[0], w[1]);
        const [x2, y2] = mmToPx(f, w[2], w[3]);
        ctx.beginPath();
        ctx.moveTo(x1 * s, y1 * s);
        ctx.lineTo(x2 * s, y2 * s);
        ctx.stroke();
      }});

      // 5. Draw Predicted Path (dashed white/blue line)
      const predPath = liveVacuumState.predictedPathMm || [];
      if (predPath.length > 1) {{
        ctx.save();
        ctx.strokeStyle = "rgba(56, 189, 248, 0.8)";
        ctx.lineWidth = 2.5;
        ctx.setLineDash([6, 4]);
        ctx.beginPath();
        predPath.forEach((pt, idx) => {{
          const [px, py] = mmToPx(f, pt[0], pt[1]);
          if (idx === 0) ctx.moveTo(px * s, py * s);
          else ctx.lineTo(px * s, py * s);
        }});
        ctx.stroke();
        ctx.restore();
      }}

      // 6. Draw Live Cleaning Path Trajectory (smooth white line as in Xiaomi Home)
      const pathPts = liveVacuumState.pathMm || [];
      if (pathPts.length > 1) {{
        ctx.save();
        ctx.strokeStyle = "rgba(255, 255, 255, 0.92)";
        ctx.lineWidth = 2.5;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.beginPath();
        pathPts.forEach((pt, idx) => {{
          const [px, py] = mmToPx(f, pt[0], pt[1]);
          if (idx === 0) ctx.moveTo(px * s, py * s);
          else ctx.lineTo(px * s, py * s);
        }});
        ctx.stroke();
        ctx.restore();
      }}

      // 7. Room labels & selection checkmarks
      ctx.font = "bold 13px system-ui, sans-serif";
      ctx.textAlign = "center";
      for (const r of Object.values(f.rooms)) {{
        const lx = r.cx_px * s;
        const ly = r.cy_px * s + 22;
        ctx.strokeStyle = "rgba(15, 23, 42, 0.9)";
        ctx.lineWidth = 3.5;
        ctx.strokeText(r.name, lx, ly);
        ctx.fillStyle = "#ffffff";
        ctx.fillText(r.name, lx, ly);

        const selIdx = selectedRooms.indexOf(r.id);
        if (selIdx !== -1) {{
          ctx.fillStyle = "#38bdf8";
          ctx.beginPath();
          ctx.arc(lx, r.cy_px * s - 16, 11, 0, Math.PI * 2);
          ctx.fill();
          ctx.fillStyle = "#0f172a";
          ctx.font = "bold 12px sans-serif";
          ctx.fillText(String(selIdx + 1), lx, r.cy_px * s - 12);
          ctx.font = "bold 13px system-ui, sans-serif";
        }}
      }}

      // 8. Draw Charging Dock Icon
      const dock = liveVacuumState.chargerMm || f.charger;
      if (dock) {{
        const [dx, dy] = mmToPx(f, dock.x, dock.y);
        ctx.save();
        ctx.fillStyle = "#10b981";
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(dx * s, dy * s, 7, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        ctx.restore();
      }}

      // 9. Draw Robot Vacuum Icon with Heading Orientation
      const rob = liveVacuumState.robotMm || f.robot;
      if (rob) {{
        const [rx, ry] = mmToPx(f, rob.x, rob.y);
        const cx = rx * s, cy = ry * s;
        ctx.save();
        if (liveVacuumState.isCleaning) {{
          ctx.strokeStyle = "rgba(56, 189, 248, 0.45)";
          ctx.lineWidth = 4;
          ctx.beginPath();
          ctx.arc(cx, cy, 15, 0, Math.PI * 2);
          ctx.stroke();
        }}
        // White vacuum disc with green/blue rim
        ctx.fillStyle = "#ffffff";
        ctx.strokeStyle = liveVacuumState.isCleaning ? "#38bdf8" : "#10b981";
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.arc(cx, cy, 10, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        // Lidar turret dot showing orientation
        const rad = ((rob.angle || 0) * Math.PI) / 180.0;
        const tx = cx + Math.cos(rad) * 4.5;
        const ty = cy - Math.sin(rad) * 4.5;
        ctx.fillStyle = "#f59e0b";
        ctx.beginPath();
        ctx.arc(tx, ty, 3, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }}

      // 10. Draw GoTo Pin & Go target marker
      if (gotoPoint) {{
        const [gx, gy] = mmToPx(f, gotoPoint[0], gotoPoint[1]);
        ctx.save();
        ctx.fillStyle = "#38bdf8";
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 2.5;
        ctx.beginPath();
        ctx.arc(gx * s, gy * s, 8, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        ctx.restore();
      }}

      // 11. Draw active drag Zone / Line
      if (dragStart && dragCurrent) {{
        const [sx, sy] = dragStart;
        const [ex, ey] = dragCurrent;
        if (currentMode === "edit_split" || currentMode === "edit_wall") {{
          ctx.strokeStyle = currentMode === "edit_split" ? "#f59e0b" : "#ef4444";
          ctx.lineWidth = 3.5;
          ctx.beginPath();
          ctx.moveTo(sx * s, sy * s);
          ctx.lineTo(ex * s, ey * s);
          ctx.stroke();
        }} else {{
          const isNogo = currentMode === "edit_nogo";
          const isNomop = currentMode === "edit_nomop";
          ctx.strokeStyle = isNogo ? "#ef4444" : (isNomop ? "#3b82f6" : "#10b981");
          ctx.fillStyle = isNogo ? "rgba(239,68,68,0.28)" : (isNomop ? "rgba(59,130,246,0.25)" : "rgba(16,185,129,0.25)");
          ctx.lineWidth = 2.5;
          const rx = Math.min(sx, ex) * s, ry = Math.min(sy, ey) * s;
          const rw = Math.abs(ex - sx) * s, rh = Math.abs(ey - sy) * s;
          ctx.fillRect(rx, ry, rw, rh);
          ctx.strokeRect(rx, ry, rw, rh);
        }}
      }}
    }}

    // --- Map Editor Drawer & Restrictions List ---
    function renderRestrictionsList() {{
      const f = FLOORS[currentSlot];
      restrictionsListEl.innerHTML = "";
      let total = 0;

      const addRow = (kind, badgeClass, badgeText, label, idx, onDelete) => {{
        total++;
        const isSel = selectedRestriction && selectedRestriction.kind === kind && selectedRestriction.index === idx;
        const div = document.createElement("div");
        div.className = "zone-item" + (isSel ? " selected" : "");
        div.innerHTML =
          `<div style="cursor:pointer;flex:1"><span class="badge ${{badgeClass}}">${{badgeText}}</span>${{label}}</div>` +
          `<button class="danger-sm">🗑️ Smazat</button>`;
        div.querySelector("div").onclick = () => {{
          selectedRestriction = (isSel ? null : {{ kind, index: idx }});
          renderRestrictionsList();
          renderCanvas();
        }};
        div.querySelector("button").onclick = (e) => {{
          e.stopPropagation();
          onDelete();
        }};
        restrictionsListEl.appendChild(div);
      }};

      f.forbidden_zones.forEach((z, idx) => {{
        const [x1, y1] = quadBounds(z);
        addRow("nogo", "badge-nogo", "No-Go", `Zóna #${{idx + 1}} [${{x1}}, ${{y1}}]`, idx, () => {{
          f.forbidden_zones.splice(idx, 1);
          selectedRestriction = null;
          markRestrictionsDirty();
        }});
      }});

      f.no_mop_zones.forEach((z, idx) => {{
        const [x1, y1] = quadBounds(z);
        addRow("nomop", "badge-nomop", "No-Mop", `Zóna #${{idx + 1}} [${{x1}}, ${{y1}}]`, idx, () => {{
          f.no_mop_zones.splice(idx, 1);
          selectedRestriction = null;
          markRestrictionsDirty();
        }});
      }});

      f.virtual_walls.forEach((w, idx) => {{
        addRow("wall", "badge-wall", "Zeď", `Zeď #${{idx + 1}} [${{w[0]}}, ${{w[1]}}]`, idx, () => {{
          f.virtual_walls.splice(idx, 1);
          selectedRestriction = null;
          markRestrictionsDirty();
        }});
      }});

      if (total === 0) {{
        restrictionsListEl.innerHTML = `<div style="font-size:0.78rem;color:var(--muted);">Žádné zakázané zóny ani zdi.</div>`;
      }}
    }}

    function markRestrictionsDirty() {{
      restrictionsDirty = true;
      btnSaveRestrictions.style.display = "block";
      renderRestrictionsList();
      renderCanvas();
    }}

    function updateEditorPendingAction() {{
      editorPendingBox.innerHTML = "";
      if (!dragStart || !dragCurrent) return;
      const f = FLOORS[currentSlot];
      const [x1, y1] = pxToMm(f, dragStart[0], dragStart[1]);
      const [x2, y2] = pxToMm(f, dragCurrent[0], dragCurrent[1]);
      const minX = Math.min(x1, x2), maxX = Math.max(x1, x2);
      const minY = Math.min(y1, y2), maxY = Math.max(y1, y2);

      if (currentMode === "edit_nogo" && minX !== maxX && minY !== maxY) {{
        const btn = document.createElement("button");
        btn.className = "warn-full-btn";
        btn.textContent = "➕ Přidat nakreslenou No-Go zónu";
        btn.onclick = () => {{
          f.forbidden_zones.push(rectToQuad(minX, minY, maxX, maxY));
          dragStart = null; dragCurrent = null;
          editorPendingBox.innerHTML = "";
          markRestrictionsDirty();
          document.getElementById("editor-drawer").classList.add("open");
        }};
        editorPendingBox.appendChild(btn);
      }} else if (currentMode === "edit_nomop" && minX !== maxX && minY !== maxY) {{
        const btn = document.createElement("button");
        btn.className = "warn-full-btn";
        btn.textContent = "➕ Přidat nakreslenou No-Mop zónu";
        btn.onclick = () => {{
          f.no_mop_zones.push(rectToQuad(minX, minY, maxX, maxY));
          dragStart = null; dragCurrent = null;
          editorPendingBox.innerHTML = "";
          markRestrictionsDirty();
          document.getElementById("editor-drawer").classList.add("open");
        }};
        editorPendingBox.appendChild(btn);
      }} else if (currentMode === "edit_wall" && (x1 !== x2 || y1 !== y2)) {{
        const btn = document.createElement("button");
        btn.className = "warn-full-btn";
        btn.textContent = "➕ Přidat nakreslenou virtuální zeď";
        btn.onclick = () => {{
          f.virtual_walls.push([x1, y1, x2, y2]);
          dragStart = null; dragCurrent = null;
          editorPendingBox.innerHTML = "";
          markRestrictionsDirty();
          document.getElementById("editor-drawer").classList.add("open");
        }};
        editorPendingBox.appendChild(btn);
      }} else if (currentMode === "edit_split" && (x1 !== x2 || y1 !== y2)) {{
        const hitSeg = hitTestRoom(f, (dragStart[0] + dragCurrent[0]) / 2, (dragStart[1] + dragCurrent[1]) / 2) || 16;
        const btn = document.createElement("button");
        btn.className = "warn-full-btn";
        btn.textContent = `✂️ Rozdělit místnost ID ${{hitSeg}} podle čáry`;
        btn.onclick = async () => {{
          if (confirm(`Rozdělit místnost ID ${{hitSeg}} podle nakreslené čáry?`)) {{
            await callHaService("local_roborock", "split_room", {{ segment_id: hitSeg, x1, y1, x2, y2 }});
            dragStart = null; dragCurrent = null;
            editorPendingBox.innerHTML = "";
          }}
        }};
        editorPendingBox.appendChild(btn);
      }}
    }}

    // --- Canvas Mouse & Touch Interaction ---
    function getCanvasPx(clientX, clientY) {{
      const f = FLOORS[currentSlot];
      const rect = canvas.getBoundingClientRect();
      const scaleX = canvas.width / rect.width;
      const scaleY = canvas.height / rect.height;
      const px = ((clientX - rect.left) * scaleX) / f.scale;
      const py = ((clientY - rect.top) * scaleY) / f.scale;
      return [px, py];
    }}

    function handlePointerDown(clientX, clientY) {{
      const f = FLOORS[currentSlot];
      const [px, py] = getCanvasPx(clientX, clientY);
      document.getElementById("fan-popover").classList.remove("open");

      if (currentMode === "full" || currentMode === "rooms") {{
        const segId = hitTestRoom(f, px, py);
        if (segId !== null) {{
          if (currentMode === "full") {{
            setMode("rooms");
          }}
          const idx = selectedRooms.indexOf(segId);
          if (idx !== -1) selectedRooms.splice(idx, 1);
          else selectedRooms.push(segId);
          renderCanvas();
          updateBottomBarLabels();
        }}
      }} else if (currentMode === "goto") {{
        gotoPoint = pxToMm(f, px, py);
        renderCanvas();
        updateBottomBarLabels();
      }} else {{
        dragStart = [px, py];
        dragCurrent = [px, py];
      }}
    }}

    function handlePointerMove(clientX, clientY, isPressed) {{
      if (!dragStart || !isPressed) return;
      const [px, py] = getCanvasPx(clientX, clientY);
      dragCurrent = [px, py];
      renderCanvas();
      updateBottomBarLabels();
      if (currentMode.startsWith("edit_")) {{
        updateEditorPendingAction();
      }}
    }}

    function handlePointerUp() {{
      if (currentMode.startsWith("edit_") && dragStart && dragCurrent) {{
        updateEditorPendingAction();
        document.getElementById("editor-drawer").classList.add("open");
      }}
    }}

    canvas.addEventListener("mousedown", (e) => handlePointerDown(e.clientX, e.clientY));
    canvas.addEventListener("mousemove", (e) => handlePointerMove(e.clientX, e.clientY, e.buttons === 1));
    window.addEventListener("mouseup", () => handlePointerUp());

    canvas.addEventListener("touchstart", (e) => {{
      if (e.touches.length === 1) {{
        handlePointerDown(e.touches[0].clientX, e.touches[0].clientY);
      }}
    }}, {{ passive: true }});
    canvas.addEventListener("touchmove", (e) => {{
      if (e.touches.length === 1 && dragStart) {{
        e.preventDefault();
        handlePointerMove(e.touches[0].clientX, e.touches[0].clientY, true);
      }}
    }}, {{ passive: false }});
    canvas.addEventListener("touchend", () => handlePointerUp());

    // --- UI Controls Event Listeners ---
    function setMode(mode) {{
      currentMode = mode;
      gotoPoint = null;
      dragStart = null;
      dragCurrent = null;
      document.querySelectorAll("#mode-pills .mode-pill").forEach(b => {{
        b.classList.toggle("active", b.dataset.mode === mode);
      }});
      document.getElementById("fab-goto").classList.toggle("active", mode === "goto");
      document.querySelectorAll("#editor-tools .drawer-btn").forEach(b => {{
        b.classList.toggle("active", `edit_${{b.dataset.etool}}` === mode);
      }});
      if (mode === "zone") {{
        // Place a default centered zone rectangle so user immediately sees it
        const f = FLOORS[currentSlot];
        dragStart = [f.width * 0.38, f.height * 0.38];
        dragCurrent = [f.width * 0.62, f.height * 0.62];
        showToast("Tažením v mapě upravte obdélník zóny");
      }}
      renderCanvas();
      updateBottomBarLabels();
    }}

    document.querySelectorAll("#mode-pills .mode-pill").forEach(btn => {{
      btn.addEventListener("click", () => setMode(btn.dataset.mode));
    }});

    document.getElementById("fab-goto").onclick = () => {{
      if (currentMode === "goto") setMode("full");
      else {{
        setMode("goto");
        showToast("Klikněte do mapy na místo, kam má vysavač dojet");
      }}
    }};

    document.getElementById("fab-repeat").onclick = () => {{
      repeatCount = (repeatCount % 3) + 1;
      document.getElementById("repeat-badge").textContent = `${{repeatCount}}×`;
      showToast(`Počet průjezdů úklidu: ${{repeatCount}}×`);
    }};

    document.getElementById("fab-fan").onclick = (e) => {{
      e.stopPropagation();
      document.getElementById("fan-popover").classList.toggle("open");
    }};

    document.querySelectorAll(".fan-opt").forEach(btn => {{
      btn.onclick = async () => {{
        const speed = btn.dataset.fan;
        document.querySelectorAll(".fan-opt").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        document.getElementById("fan-popover").classList.remove("open");
        showToast(`Nastavuji výkon sání: ${{speed}}`);
        await callHaService("vacuum", "set_fan_speed", {{
          entity_id: "vacuum.roborock_s6",
          fan_speed: speed,
        }});
      }};
    }});

    floorSelect.onchange = async (e) => {{
      const newSlot = e.target.value;
      loadFloor(newSlot);
      showToast(`Přepínám aktivní mapu ve vysavači na patro ${{newSlot}}...`);
      await callHaService("select", "select_option", {{
        entity_id: "select.roborock_s6_active_floor_map",
        option: `${{newSlot}}: ${{FLOORS[newSlot].name}}`,
      }});
    }};

    // Bottom Dock & Clean buttons
    dockBtn.onclick = async () => {{
      showToast("Odesílám vysavač do nabíjecí stanice...");
      await callHaService("vacuum", "return_to_base", {{ entity_id: "vacuum.roborock_s6" }});
    }};

    cleanBtn.onclick = async () => {{
      if (liveVacuumState.isCleaning) {{
        showToast("Pozastavuji úklid...");
        await callHaService("vacuum", "pause", {{ entity_id: "vacuum.roborock_s6" }});
        return;
      }}
      if (currentMode === "goto") {{
        if (!gotoPoint) {{
          showToast("Nejprve klikněte do mapy na cílový bod", true);
          return;
        }}
        showToast(`Vysavač jede na bod [${{gotoPoint[0]}}, ${{gotoPoint[1]}}]...`);
        await callHaService("local_roborock", "goto", {{ x: gotoPoint[0], y: gotoPoint[1] }});
        return;
      }}
      if (currentMode === "rooms") {{
        if (selectedRooms.length === 0) {{
          showToast("Nejprve kliknutím do mapy vyberte alespoň jednu místnost", true);
          return;
        }}
        showToast(`Spouštím úklid vybraných místností (${{repeatCount}}×)...`);
        await callHaService("local_roborock", "clean_segments", {{
          segments: selectedRooms,
          repeat: repeatCount,
        }});
        return;
      }}
      if (currentMode === "zone") {{
        if (!dragStart || !dragCurrent) {{
          showToast("Nejprve tažením v mapě označte zónu", true);
          return;
        }}
        const f = FLOORS[currentSlot];
        const [x1, y1] = pxToMm(f, dragStart[0], dragStart[1]);
        const [x2, y2] = pxToMm(f, dragCurrent[0], dragCurrent[1]);
        showToast(`Spouštím zónový úklid (${{repeatCount}}×)...`);
        await callHaService("local_roborock", "clean_zone", {{
          x1: Math.min(x1, x2),
          y1: Math.min(y1, y2),
          x2: Math.max(x1, x2),
          y2: Math.max(y1, y2),
          repeat: repeatCount,
        }});
        return;
      }}
      showToast("Spouštím celkový úklid patra...");
      await callHaService("vacuum", "start", {{ entity_id: "vacuum.roborock_s6" }});
    }};

    document.getElementById("btn-quick-refresh").onclick = async () => {{
      showToast("Stahuji čerstvou mapu a trasu z vysavače...");
      const ok = await callHaService("local_roborock", "refresh_map", {{ floor: parseInt(currentSlot, 10) }});
      if (ok) setTimeout(() => window.location.reload(), 2500);
    }};

    // Slide-out Editor Drawer
    const drawer = document.getElementById("editor-drawer");
    const backdrop = document.getElementById("drawer-backdrop");
    document.getElementById("btn-open-editor").onclick = () => {{
      drawer.classList.add("open");
      backdrop.classList.add("open");
    }};
    const closeDrawer = () => {{
      drawer.classList.remove("open");
      backdrop.classList.remove("open");
    }};
    document.getElementById("btn-close-editor").onclick = closeDrawer;
    backdrop.onclick = closeDrawer;

    document.querySelectorAll("#editor-tools .drawer-btn").forEach(btn => {{
      btn.onclick = () => {{
        const tool = `edit_${{btn.dataset.etool}}`;
        setMode(tool);
        closeDrawer();
        showToast("Nakreslete tažením myší v mapě požadovaný prvek");
      }};
    }});

    btnSaveRestrictions.onclick = async () => {{
      const f = FLOORS[currentSlot];
      if (!confirm(`Uložit změny zón (${{f.forbidden_zones.length}} No-Go, ${{f.no_mop_zones.length}} No-Mop, ${{f.virtual_walls.length}} zdí) do vysavače?`)) return;
      showToast("Ukládám zóny do vysavače (save_map)...");
      const ok = await callHaService("local_roborock", "set_map_restrictions", {{
        virtual_walls: f.virtual_walls,
        forbidden_zones: f.forbidden_zones,
        no_mop_zones: f.no_mop_zones,
      }});
      if (ok) {{
        restrictionsDirty = false;
        btnSaveRestrictions.style.display = "none";
        showToast("Zóny byly úspěšně uloženy do vysavače!");
      }}
    }};

    document.getElementById("btn-merge-rooms").onclick = async () => {{
      if (selectedRooms.length !== 2) {{
        showToast("Nejprve na mapě vyberte přesně 2 sousední místnosti.", true);
        return;
      }}
      if (!confirm(`Opravdu sloučit místnosti ID ${{selectedRooms[0]}} a ID ${{selectedRooms[1]}} do jedné?`)) return;
      await callHaService("local_roborock", "merge_rooms", {{
        segment_a: selectedRooms[0],
        segment_b: selectedRooms[1],
      }});
    }};

    document.getElementById("btn-locate-vac").onclick = () => {{
      callHaService("vacuum", "locate", {{ entity_id: "vacuum.roborock_s6" }});
    }};

    document.getElementById("btn-new-mapping").onclick = async () => {{
      if (!confirm("Spustit průzkum a mapování nového patra?")) return;
      await callHaService("local_roborock", "start_new_floor_mapping", {{}});
    }};

    document.getElementById("btn-delete-floor").onclick = async () => {{
      const f = FLOORS[currentSlot];
      if (!confirm(`POZOR: Trvale smazat mapu patra ${{f.name}} (Slot ${{currentSlot}})?`)) return;
      await callHaService("local_roborock", "delete_floor_map", {{ floor: parseInt(currentSlot, 10) }});
    }};

    document.getElementById("btn-reset-map").onclick = async () => {{
      if (!confirm("POZOR: Vyresetovat aktuální mapu ve vysavači?")) return;
      await callHaService("local_roborock", "reset_active_map", {{}});
    }};

    initApp();
  </script>
</body>
</html>
"""
