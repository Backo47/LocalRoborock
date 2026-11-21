# LocalRoborock – Native Local Home Assistant Integration & Interactive Map Editor for Roborock S6

[![CI](https://github.com/backo47/LocalRoborock/actions/workflows/ci.yml/badge.svg)](https://github.com/backo47/LocalRoborock/actions/workflows/ci.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**LocalRoborock** (`local_roborock`) is a native **Home Assistant Custom Component**, async Python library, CLI diagnostic suite, and **Interactive HTML5/Canvas Map Editor** designed for the **Roborock S6** (`roborock.vacuum.s6`) and compatible `miIO`-generation Roborock vacuums paired via **Xiaomi Home**.

Unlike generic cloud-dependent integrations, **LocalRoborock** executes all device control, telemetry polling, room/zone cleaning, Pin & Go navigation, multi-floor switching, and map editing (`save_map`, `merge_segment`, `split_segment`, `del_map`, `reset_map`) **100% locally over UDP port 54321** without requiring device rooting or firmware modification.

---

## ✨ Key Features

- **100% Local Control & Telemetry (`miIO` UDP `0x2131`):**
  - Pure `asyncio` single-queue UDP transport using `cryptography` (AES-128-CBC + PKCS#7 + MD5 key derivation) with automatic stamp synchronization, packet deduplication resistance, and secret redaction.
  - Zero dependency on heavy external libraries (`python-miio` is not required).
- **Complete Home Assistant Integration (`custom_components/local_roborock`):**
  - **`vacuum`**: Full `StateVacuumEntity` (Start, Stop, Pause, Return to Dock, Spot Clean, Locate, Fan Speed: *Quiet, Balanced, Turbo, Max, Gentle*).
  - **`image`**: Live rendered floor map PNG (`image.roborock_s6_floor_map`) with room centroids, charger, robot position, and zone metadata.
  - **`select`**: Multi-floor map switcher (`0: Lower floor`, `1: Upper floor`, up to 4 floors).
  - **`button`**: Dedicated 1-click cleaning buttons for every room across all floors (automatically switches the active floor map if needed), consumable reset buttons, locate, and map refresh.
  - **`sensor` & `binary_sensor`**: 15 telemetry & diagnostic sensors (Battery, Status, Error descriptions, Current/Last/Total cleaned area in $\text{m}^2$, Cleaning duration, Main/Side brush, Filter & Sensor remaining hours, Wi-Fi RSSI) + *Mop Attached* (`water_box_status`) and *Cleaning Active* binary sensors.
  - **`switch` & `number`**: *Carpet Boost*, *Status Indicator LED*, *Do Not Disturb (DND)* switches, and *Voice Volume* (`0–100%`) slider.
- **Binary `.rrmap` Decoder & Hybrid Local Cache:**
  - Zero-dependency binary `RRMap` parser decoding all blocks (Charger, Raster Image + Room Segments `1..31`, Trajectory Path, Robot Pose, Virtual Walls, No-Go Zones, and No-Mop Zones).
  - Automatically discovers room segments directly from the map raster—even on floors where rooms were never named in the Xiaomi Cloud app!
- **Interactive HTML5/Canvas Map UI & Non-Destructive Map Editor:**
  - Hosted directly inside Home Assistant at `/local/local_roborock_map.html` with **1-click execution** via Home Assistant's REST API.
  - Click rooms to clean or **merge adjacent rooms** (`merge_segment`).
  - Draw lines to **split rooms** (`split_segment`) or add **Virtual Walls**.
  - Draw rectangles for **Zoned Cleaning**, **No-Go Zones**, and **No-Mop Zones**.
  - Click existing No-Go / No-Mop zones to **highlight them in yellow**, replace them, or delete them with **`🗑️ Delete`**, backed by automatic timestamped JSON backups before every `save_map` commit.

---

## 📦 Installation

### Option 1: Install via HACS (Recommended)

1. Open **HACS** in Home Assistant → **Integrations** → click the top-right menu (`⋮`) → **Custom repositories**.
2. Enter the repository URL:
   ```text
   https://github.com/backo47/LocalRoborock
   ```
   and select category **Integration**.
3. Click **Download** on **Local Roborock S6** and restart Home Assistant.

### Option 2: Manual Installation

1. Copy the `custom_components/local_roborock` folder into your Home Assistant `/config/custom_components/` directory.
2. Restart Home Assistant.

---

## ⚙️ Configuration in Home Assistant

1. Before adding **Local Roborock S6**, disable any existing integration polling the same vacuum over UDP port `54321` (such as *Xiaomi Miot Auto*) to prevent UDP packet collisions.
2. Go to **Settings → Devices & Services → Add Integration** and search for **Local Roborock S6**.
3. Enter:
   - **IP Address:** Your vacuum's local IP (e.g. `192.168.1.110`)
   - **32-character hex miIO Token:** Your local device token (can be extracted from an existing Home Assistant setup using our CLI `extract-token` tool)
   - **Polling Interval:** `15` seconds (default)
   - *(Optional)* **Xiaomi Cloud session fields (`userId`, `serviceToken`, `ssecurity`, `country`)**: Only used when you press *Refresh Floor Map* to download a newly generated `.rrmap` file from Xiaomi FDS into the local disk cache.

### Adding the Interactive Map & Editor to the Home Assistant Sidebar

1. Go to **Settings → Dashboards → Add Dashboard → Webpage**.
2. Set Title to **`Vacuum Map`**, Icon to `mdi:robot-vacuum`, and URL to:
   ```text
   /local/local_roborock_map.html
   ```
3. Enable **Show in sidebar** and click **Create**.

---

## 🖥️ Standalone CLI & Development

You can also use `LocalRoborock` as a standalone command-line tool independent of Home Assistant:

```bash
# Install in a Python 3.13+ virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .\.venv\Scripts\activate
pip install -e ".[dev]"

# Copy .env.example to .env and fill in ROBOROCK_IP and ROBOROCK_TOKEN
cp .env.example .env

# 1. Extract token from an existing Home Assistant core.config_entries or xiaomi_miot cache
local-roborock extract-token /path/to/.storage --save-env

# 2. Run read-only diagnostic probe or full firmware capability discovery
local-roborock probe
local-roborock capabilities

# 3. Fetch & decode floor maps into map_cache/ and generate interactive HTML UI
local-roborock map fetch --floor 0
local-roborock map fetch --floor 1
local-roborock map ui

# 4. Control or edit maps from CLI (supports --dry-run on all action commands)
local-roborock clean-rooms 16 18 --repeat 2 --dry-run
local-roborock map-edit --floor 1 show-restrictions
local-roborock map-edit --floor 1 merge-rooms 3 18 --dry-run
```

---

## 🧪 Running Tests & Linter

```bash
ruff check .
pytest
```

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
