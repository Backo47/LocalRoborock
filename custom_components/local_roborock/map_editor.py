"""Phase 6 & 7: Safe Map Editor & Interactive HTML5/Canvas Map UI with Direct HA Control."""

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
    """Safe stateful editor for Roborock `save_map` restrictions (walls, no-go, no-mop zones).

    Guarantees that existing zones parsed from `.rrmap` blocks 9, 10, and 12 are preserved
    and backed up before any modification is committed via `save_map`.
    """

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
    """Generate a standalone interactive HTML5/Canvas/SVG Map UI & Editor for Roborock S6.

    Features:
    - Direct 1-click execution against Home Assistant REST API (automatically authenticated
      when opened inside Home Assistant at `/local/local_roborock_map.html`).
    - Visual list of all saved Virtual Walls, No-Go Zones, and No-Mop Zones on the current floor,
      allowing the user to add new zones, delete individual existing zones, or edit them and
      commit the updated layout directly to the vacuum (`local_roborock.set_map_restrictions`).
    - 1-click room cleaning, zoned cleaning, Pin & Go navigation, room merging, and room splitting.
    """
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
  <title>LocalRoborock S6 – Interaktivní Mapa & Editor</title>
  <style>
    :root {{
      --bg: #0f172a;
      --panel: #1e293b;
      --border: #334155;
      --accent: #38bdf8;
      --text: #f8fafc;
      --muted: #94a3b8;
      --danger: #ef4444;
      --success: #10b981;
      --warning: #f59e0b;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, sans-serif;
      display: grid;
      grid-template-columns: 410px 1fr;
      height: 100vh;
      overflow: hidden;
    }}
    aside {{
      background: var(--panel);
      border-right: 1px solid var(--border);
      padding: 14px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }}
    h1 {{ font-size: 1.1rem; margin: 0; color: var(--accent); }}
    h2 {{ font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); margin: 0 0 6px 0; }}
    .card {{
      background: rgba(15, 23, 42, 0.55);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 11px;
    }}
    .btn-group {{ display: flex; flex-wrap: wrap; gap: 5px; }}
    button, select {{
      background: #0f172a;
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 6px 10px;
      font-size: 0.82rem;
      cursor: pointer;
      transition: all 0.15s;
    }}
    button:hover {{ border-color: var(--accent); }}
    button.active {{
      background: var(--accent);
      color: #0f172a;
      border-color: var(--accent);
      font-weight: 600;
    }}
    button.primary-action {{
      background: var(--success);
      color: #052e16;
      border-color: var(--success);
      font-weight: 700;
      width: 100%;
      padding: 9px 12px;
      font-size: 0.88rem;
      margin-top: 6px;
    }}
    button.warn-action {{
      background: var(--warning);
      color: #451a03;
      border-color: var(--warning);
      font-weight: 700;
      width: 100%;
      padding: 9px 12px;
      font-size: 0.88rem;
      margin-top: 6px;
    }}
    button.danger-sm {{
      background: rgba(239, 68, 68, 0.18);
      color: #fca5a5;
      border-color: rgba(239, 68, 68, 0.5);
      padding: 3px 8px;
      font-size: 0.75rem;
    }}
    button.danger-sm:hover {{ background: var(--danger); color: #fff; }}
    .room-chip, .zone-item {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 5px 9px;
      margin-bottom: 4px;
      border-radius: 6px;
      border: 1px solid var(--border);
      font-size: 0.82rem;
    }}
    .room-chip {{ cursor: pointer; }}
    .room-chip.selected {{
      border-color: var(--accent);
      background: rgba(56, 189, 248, 0.18);
    }}
    .zone-item.selected {{
      border-color: var(--warning);
      background: rgba(245, 158, 11, 0.18);
    }}
    .badge {{
      display: inline-block;
      padding: 1px 6px;
      border-radius: 4px;
      font-size: 0.72rem;
      font-weight: 600;
      margin-right: 6px;
    }}
    .badge-nogo {{ background: rgba(239, 68, 68, 0.25); color: #fca5a5; border: 1px solid #ef4444; }}
    .badge-nomop {{ background: rgba(59, 130, 246, 0.25); color: #93c5fd; border: 1px solid #3b82f6; }}
    .badge-wall {{ background: rgba(245, 158, 11, 0.25); color: #fcd34d; border: 1px solid #f59e0b; }}
    #status-toast {{
      display: none;
      padding: 8px 10px;
      border-radius: 6px;
      font-size: 0.82rem;
      font-weight: 600;
      margin-top: 6px;
    }}
    pre {{
      background: #090d16;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px;
      font-size: 0.74rem;
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-all;
      margin: 4px 0 0 0;
      color: #e2e8f0;
    }}
    main {{
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      position: relative;
      overflow: auto;
      padding: 16px;
    }}
    #canvas-wrap {{
      position: relative;
      border: 1px solid var(--border);
      border-radius: 8px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.5);
      line-height: 0;
    }}
    canvas {{
      cursor: crosshair;
      max-width: calc(100vw - 460px);
      max-height: calc(100vh - 70px);
      width: auto;
      height: auto;
    }}
    #hud {{
      position: absolute;
      top: 16px;
      right: 16px;
      background: rgba(15, 23, 42, 0.9);
      border: 1px solid var(--border);
      padding: 7px 12px;
      border-radius: 6px;
      font-size: 0.8rem;
      font-family: monospace;
    }}
  </style>
</head>
<body>
  <aside>
    <div>
      <h1>LocalRoborock S6 – Mapa & Editor</h1>
      <div id="ha-conn-status" style="font-size:0.76rem;color:var(--muted);margin-top:2px;">
        Kontrola spojení s Home Assistantem...
      </div>
    </div>

    <div class="card">
      <h2>1. Výběr patra (Multi-Map Slot)</h2>
      <div style="display:flex;gap:6px;">
        <select id="floor-select" style="flex:1;"></select>
        <button id="btn-refresh-map" title="Stáhnout čerstvou mapu z vysavače">🔄 Obnovit</button>
      </div>
    </div>

    <div class="card">
      <h2>2. Interaktivní nástroj v mapě</h2>
      <div class="btn-group" id="tool-buttons">
        <button data-tool="rooms" class="active">Výběr místností</button>
        <button data-tool="goto">Navigace (Pin & Go)</button>
        <button data-tool="zone">Zónový úklid</button>
        <button data-tool="split">Rozdělit místnost</button>
        <button data-tool="wall">+ Virtuální zeď</button>
        <button data-tool="nogo">+ No-Go zóna</button>
        <button data-tool="nomop">+ No-Mop zóna</button>
      </div>
      <div style="margin-top:8px;display:flex;justify-content:space-between;align-items:center;">
        <label style="font-size:0.8rem;color:var(--muted);">Počet průjezdů (repeat):</label>
        <select id="repeat-select">
          <option value="1">1× (Standard)</option>
          <option value="2">2× (Křížově)</option>
          <option value="3">3× (Intenzivně)</option>
        </select>
      </div>

      <!-- Direct 1-click execution buttons -->
      <div id="direct-action-box"></div>
      <div id="status-toast"></div>
    </div>

    <div class="card">
      <h2>3. Zakázané zóny a virtuální zdi na patře</h2>
      <div style="font-size:0.75rem;color:var(--muted);margin-bottom:6px;">
        Kliknutím zónu zvýrazníte/upravíte v mapě, tlačítkem 🗑️ ji smažete.
      </div>
      <div id="restrictions-list"></div>
      <button id="btn-save-restrictions" class="warn-action" style="display:none;">
        💾 Uložit změny zón do vysavače (save_map)
      </button>
    </div>

    <div class="card">
      <h2>4. Místnosti na aktivním patře</h2>
      <div id="room-list"></div>
    </div>

    <details class="card">
      <summary style="cursor:pointer;font-size:0.8rem;color:var(--muted);font-weight:600;">
        5. Správa pater: Nové mapování / Smazání mapy
      </summary>
      <div style="font-size:0.75rem;color:var(--muted);margin:6px 0;">
        Vysavač S6 podporuje až 4 patra. Pro zmapování nového patra přeneste vysavač na nové patro a spusťte mapovací úklid.
      </div>
      <button id="btn-new-mapping" class="primary-action" style="margin-top:4px;">
        🗺️ Spustit mapování nového patra (app_start)
      </button>
      <div style="display:flex;gap:6px;margin-top:6px;">
        <button id="btn-delete-floor" class="danger-sm" style="flex:1;padding:7px;">
          🗑️ Smazat mapu tohoto patra (del_map)
        </button>
        <button id="btn-reset-map" class="danger-sm" style="flex:1;padding:7px;">
          ⚠️ Reset aktivní mapy (reset_map)
        </button>
      </div>
    </details>

    <details class="card">
      <summary style="cursor:pointer;font-size:0.8rem;color:var(--muted);font-weight:600;">
        Pokročilé: CLI & Home Assistant YAML
      </summary>
      <pre id="cli-output">Klikněte do mapy...</pre>
      <pre id="ha-output" style="margin-top:6px;">Klikněte do mapy...</pre>
    </details>
  </aside>

  <main>
    <div id="hud">Souřadnice: [---, ---] mm | Pixel: [---, ---]</div>
    <div id="canvas-wrap">
      <canvas id="map-canvas"></canvas>
    </div>
  </main>

  <script>
    const FLOORS = {floors_json};
    let currentSlot = "{default_slot}";
    let currentTool = "rooms";
    let selectedRooms = new Set();
    let gotoPoint = null;
    let dragStart = null;
    let dragCurrent = null;
    let selectedRestriction = null; // {{ kind: 'nogo'|'nomop'|'wall', index: number }}
    let restrictionsDirty = false;
    let baseImg = new Image();

    const canvas = document.getElementById("map-canvas");
    const ctx = canvas.getContext("2d");
    const floorSelect = document.getElementById("floor-select");
    const repeatSelect = document.getElementById("repeat-select");
    const roomListEl = document.getElementById("room-list");
    const restrictionsListEl = document.getElementById("restrictions-list");
    const btnSaveRestrictions = document.getElementById("btn-save-restrictions");
    const directActionBox = document.getElementById("direct-action-box");
    const toastEl = document.getElementById("status-toast");
    const cliOut = document.getElementById("cli-output");
    const haOut = document.getElementById("ha-output");
    const hud = document.getElementById("hud");
    const haConnStatus = document.getElementById("ha-conn-status");

    // --- Home Assistant Authentication Helper ---
    function getHaAccessToken() {{
      try {{
        const raw = localStorage.getItem("hassTokens");
        if (raw) {{
          const parsed = JSON.parse(raw);
          if (parsed && parsed.access_token) return parsed.access_token;
        }}
      }} catch (e) {{}}
      try {{
        if (window.parent && window.parent !== window) {{
          const rawParent = window.parent.localStorage.getItem("hassTokens");
          if (rawParent) {{
            const parsedParent = JSON.parse(rawParent);
            if (parsedParent && parsedParent.access_token) return parsedParent.access_token;
          }}
        }}
      }} catch (e) {{}}
      return null;
    }}

    function showToast(msg, isError = false) {{
      toastEl.style.display = "block";
      toastEl.style.background = isError ? "rgba(239,68,68,0.22)" : "rgba(16,185,129,0.22)";
      toastEl.style.border = `1px solid ${{isError ? "#ef4444" : "#10b981"}}`;
      toastEl.style.color = isError ? "#fca5a5" : "#6ee7b7";
      toastEl.textContent = msg;
      setTimeout(() => {{ toastEl.style.display = "none"; }}, 5000);
    }}

    async function callHaService(service, data) {{
      const token = getHaAccessToken();
      if (!token) {{
        showToast("Chybí HA token (otevřete mapu uvnitř Home Assistantu).", true);
        return false;
      }}
      showToast(`Odesílám příkaz local_roborock.${{service}}...`);
      try {{
        const resp = await fetch(`/api/services/local_roborock/${{service}}`, {{
          method: "POST",
          headers: {{
            "Authorization": `Bearer ${{token}}`,
            "Content-Type": "application/json",
          }},
          body: JSON.stringify(data),
        }});
        if (!resp.ok) {{
          const txt = await resp.text();
          showToast(`Chyba (${{resp.status}}): ${{txt}}`, true);
          return false;
        }}
        showToast(`Hotovo: local_roborock.${{service}} provedeno!`);
        return true;
      }} catch (err) {{
        showToast(`Chyba spojení s HA: ${{err}}`, true);
        return false;
      }}
    }}

    function checkHaConnection() {{
      const token = getHaAccessToken();
      if (token) {{
        haConnStatus.innerHTML = `<span style="color:#10b981">● Připojeno k Home Assistantu (přímé ovládání aktivní)</span>`;
      }} else {{
        haConnStatus.innerHTML = `<span style="color:#f59e0b">● Offline režim (pro přímé ovládání otevřete v HA)</span>`;
      }}
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

    function initFloors() {{
      checkHaConnection();
      floorSelect.innerHTML = "";
      for (const [slot, f] of Object.entries(FLOORS)) {{
        const opt = document.createElement("option");
        opt.value = slot;
        opt.textContent = `Slot ${{slot}}: ${{f.name}} (${{Object.keys(f.rooms).length}} místností)`;
        if (slot === currentSlot) opt.selected = true;
        floorSelect.appendChild(opt);
      }}
      loadFloor(currentSlot);
    }}

    function loadFloor(slot) {{
      currentSlot = slot;
      selectedRooms.clear();
      gotoPoint = null;
      dragStart = null;
      dragCurrent = null;
      selectedRestriction = null;
      restrictionsDirty = false;
      btnSaveRestrictions.style.display = "none";
      const f = FLOORS[slot];
      canvas.width = f.width * f.scale;
      canvas.height = f.height * f.scale;
      baseImg.onload = () => {{ renderCanvas(); }};
      baseImg.src = f.image_b64;
      renderRoomList();
      renderRestrictionsList();
      updateCommands();
    }}

    function markRestrictionsDirty() {{
      restrictionsDirty = true;
      btnSaveRestrictions.style.display = "block";
      renderRestrictionsList();
      renderCanvas();
      updateCommands();
    }}

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
          `<button class="danger-sm" title="Smazat tuto zónu/zeď">🗑️ Smazat</button>`;
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
        const [x1, y1, x2, y2] = quadBounds(z);
        addRow("nogo", "badge-nogo", "No-Go", `#${{idx + 1}} [${{x1}}, ${{y1}}]`, idx, () => {{
          f.forbidden_zones.splice(idx, 1);
          selectedRestriction = null;
          markRestrictionsDirty();
        }});
      }});

      f.no_mop_zones.forEach((z, idx) => {{
        const [x1, y1, x2, y2] = quadBounds(z);
        addRow("nomop", "badge-nomop", "No-Mop", `#${{idx + 1}} [${{x1}}, ${{y1}}]`, idx, () => {{
          f.no_mop_zones.splice(idx, 1);
          selectedRestriction = null;
          markRestrictionsDirty();
        }});
      }});

      f.virtual_walls.forEach((w, idx) => {{
        addRow("wall", "badge-wall", "Zeď", `#${{idx + 1}} [${{w[0]}}, ${{w[1]}}]`, idx, () => {{
          f.virtual_walls.splice(idx, 1);
          selectedRestriction = null;
          markRestrictionsDirty();
        }});
      }});

      if (total === 0) {{
        restrictionsListEl.innerHTML = `<div style="font-size:0.8rem;color:var(--muted);">Žádné zakázané zóny ani zdi.</div>`;
      }}
    }}

    function renderRoomList() {{
      const f = FLOORS[currentSlot];
      roomListEl.innerHTML = "";
      for (const [segId, r] of Object.entries(f.rooms)) {{
        const idNum = parseInt(segId, 10);
        const div = document.createElement("div");
        div.className = "room-chip" + (selectedRooms.has(idNum) ? " selected" : "");
        div.innerHTML = `<span><strong>ID ${{idNum}}</strong> – ${{r.name}}</span><span style="color:var(--muted)">${{r.area_m2}} m²</span>`;
        div.onclick = () => {{
          if (selectedRooms.has(idNum)) selectedRooms.delete(idNum);
          else selectedRooms.add(idNum);
          renderRoomList();
          renderCanvas();
          updateCommands();
        }};
        roomListEl.appendChild(div);
      }}
    }}

    function renderCanvas() {{
      const f = FLOORS[currentSlot];
      const s = f.scale;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(baseImg, 0, 0);

      // Highlight selected rooms
      ctx.fillStyle = "rgba(56, 189, 248, 0.35)";
      for (const segId of selectedRooms) {{
        const r = f.rooms[String(segId)];
        if (!r) continue;
        for (const [rx, ry, rw, rh] of r.runs) {{
          ctx.fillRect(rx * s, ry * s, rw * s, rh * s);
        }}
      }}

      // Draw saved Forbidden Zones (Red)
      f.forbidden_zones.forEach((z, idx) => {{
        const isSel = selectedRestriction && selectedRestriction.kind === "nogo" && selectedRestriction.index === idx;
        ctx.strokeStyle = isSel ? "#fde047" : "#ef4444";
        ctx.fillStyle = isSel ? "rgba(253, 224, 71, 0.35)" : "rgba(239, 68, 68, 0.25)";
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

      // Draw saved No-Mop Zones (Blue dashed)
      f.no_mop_zones.forEach((z, idx) => {{
        const isSel = selectedRestriction && selectedRestriction.kind === "nomop" && selectedRestriction.index === idx;
        ctx.strokeStyle = isSel ? "#fde047" : "#3b82f6";
        ctx.fillStyle = isSel ? "rgba(253, 224, 71, 0.35)" : "rgba(59, 130, 246, 0.22)";
        ctx.lineWidth = isSel ? 3 : 2;
        ctx.setLineDash([6, 4]);
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
      ctx.setLineDash([]);

      // Draw saved Virtual Walls
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

      // Room labels
      ctx.font = "bold 13px sans-serif";
      ctx.textAlign = "center";
      for (const r of Object.values(f.rooms)) {{
        const lx = r.cx_px * s;
        const ly = r.cy_px * s + 22;
        ctx.strokeStyle = "#0f172a";
        ctx.lineWidth = 3;
        ctx.strokeText(`${{r.name}} (${{r.area_m2}} m²)`, lx, ly);
        ctx.fillStyle = "#ffffff";
        ctx.fillText(`${{r.name}} (${{r.area_m2}} m²)`, lx, ly);
      }}

      // Draw GoTo marker
      if (gotoPoint) {{
        const [gx, gy] = mmToPx(f, gotoPoint[0], gotoPoint[1]);
        ctx.fillStyle = "#38bdf8";
        ctx.beginPath();
        ctx.arc(gx * s, gy * s, 7, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 2;
        ctx.stroke();
      }}

      // Draw active drag rectangle or line
      if (dragStart && dragCurrent) {{
        const [sx, sy] = dragStart;
        const [ex, ey] = dragCurrent;
        if (currentTool === "split" || currentTool === "wall") {{
          ctx.strokeStyle = currentTool === "split" ? "#f59e0b" : "#ef4444";
          ctx.lineWidth = 3;
          ctx.beginPath();
          ctx.moveTo(sx * s, sy * s);
          ctx.lineTo(ex * s, ey * s);
          ctx.stroke();
        }} else {{
          ctx.strokeStyle = currentTool === "nogo" ? "#ef4444" : (currentTool === "nomop" ? "#3b82f6" : "#10b981");
          ctx.fillStyle = currentTool === "nogo" ? "rgba(239,68,68,0.28)" : (currentTool === "nomop" ? "rgba(59,130,246,0.28)" : "rgba(16,185,129,0.25)");
          ctx.lineWidth = 2;
          ctx.fillRect(Math.min(sx, ex) * s, Math.min(sy, ey) * s, Math.abs(ex - sx) * s, Math.abs(ey - sy) * s);
          ctx.strokeRect(Math.min(sx, ex) * s, Math.min(sy, ey) * s, Math.abs(ex - sx) * s, Math.abs(ey - sy) * s);
        }}
      }}
    }}

    function updateCommands() {{
      const f = FLOORS[currentSlot];
      const rep = parseInt(repeatSelect.value, 10);
      directActionBox.innerHTML = "";

      if (currentTool === "rooms") {{
        const ids = Array.from(selectedRooms).sort((a, b) => a - b);
        if (ids.length === 0) {{
          cliOut.textContent = "# Klikněte na jednu nebo více místností v mapě";
          haOut.textContent = "# Klikněte na místnost v mapě";
          return;
        }}
        const btnClean = document.createElement("button");
        btnClean.className = "primary-action";
        btnClean.textContent = `🧹 Uklidit vybrané místnosti (${{ids.join(", ")}}) – ${{rep}}×`;
        btnClean.onclick = () => callHaService("clean_segments", {{ segments: ids, repeat: rep }});
        directActionBox.appendChild(btnClean);

        if (ids.length === 2) {{
          const btnMerge = document.createElement("button");
          btnMerge.className = "warn-action";
          btnMerge.textContent = `🔗 Sloučit místnosti ID ${{ids[0]}} a ID ${{ids[1]}} do jedné`;
          btnMerge.onclick = () => {{
            if (confirm(`Opravdu chcete trvale sloučit místnosti ID ${{ids[0]}} a ID ${{ids[1]}} v mapě vysavače?`)) {{
              callHaService("merge_rooms", {{ segment_a: ids[0], segment_b: ids[1] }});
            }}
          }};
          directActionBox.appendChild(btnMerge);
        }}

        cliOut.textContent = `python -m custom_components.local_roborock.cli clean-rooms ${{ids.join(" ")}} --floor ${{currentSlot}} --repeat ${{rep}}`;
        haOut.textContent = `action: local_roborock.clean_segments\\ndata:\\n  segments: [${{ids.join(", ")}}]\\n  repeat: ${{rep}}`;
      }} else if (currentTool === "goto" && gotoPoint) {{
        const [x, y] = gotoPoint;
        const btnGo = document.createElement("button");
        btnGo.className = "primary-action";
        btnGo.textContent = `📍 Dojet vysavačem na bod [${{x}}, ${{y}}]`;
        btnGo.onclick = () => callHaService("goto", {{ x, y }});
        directActionBox.appendChild(btnGo);

        cliOut.textContent = `python -m custom_components.local_roborock.cli goto ${{x}} ${{y}}`;
        haOut.textContent = `action: local_roborock.goto\\ndata:\\n  x: ${{x}}\\n  y: ${{y}}`;
      }} else if (dragStart && dragCurrent) {{
        const [x1, y1] = pxToMm(f, dragStart[0], dragStart[1]);
        const [x2, y2] = pxToMm(f, dragCurrent[0], dragCurrent[1]);
        const minX = Math.min(x1, x2), maxX = Math.max(x1, x2);
        const minY = Math.min(y1, y2), maxY = Math.max(y1, y2);

        if (currentTool === "zone" && minX !== maxX && minY !== maxY) {{
          const btnZone = document.createElement("button");
          btnZone.className = "primary-action";
          btnZone.textContent = `🧹 Uklidit nakreslenou zónu (${{rep}}×)`;
          btnZone.onclick = () => callHaService("clean_zone", {{ x1: minX, y1: minY, x2: maxX, y2: maxY, repeat: rep }});
          directActionBox.appendChild(btnZone);

          cliOut.textContent = `python -m custom_components.local_roborock.cli clean-zone ${{minX}} ${{minY}} ${{maxX}} ${{maxY}} --repeat ${{rep}}`;
          haOut.textContent = `action: local_roborock.clean_zone\\ndata:\\n  x1: ${{minX}}\\n  y1: ${{minY}}\\n  x2: ${{maxX}}\\n  y2: ${{maxY}}\\n  repeat: ${{rep}}`;
        }} else if (currentTool === "split" && (x1 !== x2 || y1 !== y2)) {{
          const hitSeg = hitTestRoom(f, (dragStart[0] + dragCurrent[0]) / 2, (dragStart[1] + dragCurrent[1]) / 2) || 16;
          const btnSplit = document.createElement("button");
          btnSplit.className = "warn-action";
          btnSplit.textContent = `✂️ Rozdělit místnost ID ${{hitSeg}} podle čáry`;
          btnSplit.onclick = () => {{
            if (confirm(`Rozdělit místnost ID ${{hitSeg}} čárou [${{x1}}, ${{y1}}] -> [${{x2}}, ${{y2}}]?`)) {{
              callHaService("split_room", {{ segment_id: hitSeg, x1, y1, x2, y2 }});
            }}
          }};
          directActionBox.appendChild(btnSplit);

          cliOut.textContent = `python -m custom_components.local_roborock.cli map-edit split-room ${{hitSeg}} ${{x1}} ${{y1}} ${{x2}} ${{y2}} --floor ${{currentSlot}}`;
        }} else if (currentTool === "wall" && (x1 !== x2 || y1 !== y2)) {{
          const btnAddWall = document.createElement("button");
          btnAddWall.className = "warn-action";
          btnAddWall.textContent = `➕ Přidat tuto virtuální zeď do seznamu`;
          btnAddWall.onclick = () => {{
            f.virtual_walls.push([x1, y1, x2, y2]);
            dragStart = null; dragCurrent = null;
            markRestrictionsDirty();
          }};
          directActionBox.appendChild(btnAddWall);
        }} else if (currentTool === "nogo" && minX !== maxX && minY !== maxY) {{
          const btnAddNogo = document.createElement("button");
          btnAddNogo.className = "warn-action";
          if (selectedRestriction && selectedRestriction.kind === "nogo") {{
            btnAddNogo.textContent = `✏️ Nahradit vybranou No-Go zónu #${{selectedRestriction.index + 1}} tímto obdélníkem`;
            btnAddNogo.onclick = () => {{
              f.forbidden_zones[selectedRestriction.index] = rectToQuad(minX, minY, maxX, maxY);
              dragStart = null; dragCurrent = null;
              markRestrictionsDirty();
            }};
          }} else {{
            btnAddNogo.textContent = `➕ Přidat tuto No-Go zónu do seznamu`;
            btnAddNogo.onclick = () => {{
              f.forbidden_zones.push(rectToQuad(minX, minY, maxX, maxY));
              dragStart = null; dragCurrent = null;
              markRestrictionsDirty();
            }};
          }}
          directActionBox.appendChild(btnAddNogo);
        }} else if (currentTool === "nomop" && minX !== maxX && minY !== maxY) {{
          const btnAddNomop = document.createElement("button");
          btnAddNomop.className = "warn-action";
          if (selectedRestriction && selectedRestriction.kind === "nomop") {{
            btnAddNomop.textContent = `✏️ Nahradit vybranou No-Mop zónu #${{selectedRestriction.index + 1}} tímto obdélníkem`;
            btnAddNomop.onclick = () => {{
              f.no_mop_zones[selectedRestriction.index] = rectToQuad(minX, minY, maxX, maxY);
              dragStart = null; dragCurrent = null;
              markRestrictionsDirty();
            }};
          }} else {{
            btnAddNomop.textContent = `➕ Přidat tuto No-Mop zónu do seznamu`;
            btnAddNomop.onclick = () => {{
              f.no_mop_zones.push(rectToQuad(minX, minY, maxX, maxY));
              dragStart = null; dragCurrent = null;
              markRestrictionsDirty();
            }};
          }}
          directActionBox.appendChild(btnAddNomop);
        }}
      }}
    }}

    btnSaveRestrictions.onclick = async () => {{
      const f = FLOORS[currentSlot];
      if (!confirm(`Uložit upravený seznam zón (${{f.forbidden_zones.length}} No-Go, ${{f.no_mop_zones.length}} No-Mop, ${{f.virtual_walls.length}} zdí) přímo do vysavače? (Původní stav bude automaticky zazálohován).`)) {{
        return;
      }}
      const ok = await callHaService("set_map_restrictions", {{
        virtual_walls: f.virtual_walls,
        forbidden_zones: f.forbidden_zones,
        no_mop_zones: f.no_mop_zones,
      }});
      if (ok) {{
        restrictionsDirty = false;
        btnSaveRestrictions.style.display = "none";
      }}
    }};

    document.getElementById("btn-refresh-map").onclick = async () => {{
      const ok = await callHaService("refresh_map", {{ floor: parseInt(currentSlot, 10) }});
      if (ok) {{
        setTimeout(() => window.location.reload(), 2500);
      }}
    }};

    document.getElementById("btn-new-mapping").onclick = async () => {{
      if (!confirm("Spustit celkový úklid a mapování nového patra? (Ujistěte se, že jste vysavač přenesl na nové patro).")) {{
        return;
      }}
      await callHaService("start_new_floor_mapping", {{}});
    }};

    document.getElementById("btn-delete-floor").onclick = async () => {{
      const f = FLOORS[currentSlot];
      if (!confirm(`POZOR: Opravdu chcete TRVALE SMAZAT uloženou mapu patra Slot ${{currentSlot}} (${{f.name}}) z paměti vysavače (del_map [${{currentSlot}}])?`)) {{
        return;
      }}
      await callHaService("delete_floor_map", {{ floor: parseInt(currentSlot, 10) }});
    }};

    document.getElementById("btn-reset-map").onclick = async () => {{
      if (!confirm("POZOR: Opravdu chcete VYRESETOVAT aktuálně načtenou mapu ve vysavači (reset_map) a vynutit nové mapování?")) {{
        return;
      }}
      await callHaService("reset_active_map", {{}});
    }};

    function getCanvasPx(evt) {{
      const f = FLOORS[currentSlot];
      const rect = canvas.getBoundingClientRect();
      const scaleX = canvas.width / rect.width;
      const scaleY = canvas.height / rect.height;
      const px = ((evt.clientX - rect.left) * scaleX) / f.scale;
      const py = ((evt.clientY - rect.top) * scaleY) / f.scale;
      return [px, py];
    }}

    canvas.addEventListener("mousemove", (evt) => {{
      const f = FLOORS[currentSlot];
      const [px, py] = getCanvasPx(evt);
      const [mmX, mmY] = pxToMm(f, px, py);
      const hitSeg = hitTestRoom(f, px, py);
      const roomLabel = hitSeg && f.rooms[String(hitSeg)] ? ` | Místnost: ${{f.rooms[String(hitSeg)].name}} (ID ${{hitSeg}})` : "";
      hud.textContent = `Souřadnice: [${{mmX}}, ${{mmY}}] mm | Pixel: [${{Math.round(px)}}, ${{Math.round(py)}}]${{roomLabel}}`;

      if (dragStart && evt.buttons === 1) {{
        dragCurrent = [px, py];
        renderCanvas();
        updateCommands();
      }}
    }});

    canvas.addEventListener("mousedown", (evt) => {{
      const f = FLOORS[currentSlot];
      const [px, py] = getCanvasPx(evt);
      if (currentTool === "rooms") {{
        const segId = hitTestRoom(f, px, py);
        if (segId !== null) {{
          if (selectedRooms.has(segId)) selectedRooms.delete(segId);
          else selectedRooms.add(segId);
          renderRoomList();
          renderCanvas();
          updateCommands();
        }}
      }} else if (currentTool === "goto") {{
        gotoPoint = pxToMm(f, px, py);
        renderCanvas();
        updateCommands();
      }} else {{
        dragStart = [px, py];
        dragCurrent = [px, py];
      }}
    }});

    floorSelect.addEventListener("change", (e) => loadFloor(e.target.value));
    repeatSelect.addEventListener("change", () => updateCommands());

    document.querySelectorAll("#tool-buttons button").forEach((btn) => {{
      btn.addEventListener("click", () => {{
        document.querySelectorAll("#tool-buttons button").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        currentTool = btn.dataset.tool;
        dragStart = null;
        dragCurrent = null;
        renderCanvas();
        updateCommands();
      }});
    }});

    initFloors();
  </script>
</body>
</html>
"""
