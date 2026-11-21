# Empirický katalog lokálních schopností Roborock S6 (`roborock.vacuum.s6`)

Tento dokument shrnuje empiricky ověřené schopnosti, telemetrii, datové formáty a limity vysavače **Roborock S6** (firmware `3.5.8_2700`) na základě živého read-only skenu (`capabilities`).

---

## 1. Identifikace cílové platformy

| Parametr | Hodnota vyčtená ze zařízení | Zdrojová metoda |
| :--- | :--- | :--- |
| **Model** | `roborock.vacuum.s6` | `miIO.info` |
| **Formát sériového čísla** | `R0306SXXXXXXXX` (14 znaků) | `get_serial_number` |
| **Device ID (`did`)** | 32bitové celé číslo | `Hello Handshake` |
| **Verze firmwaru** | `3.5.8_2700` | `miIO.info` |
| **Hardwarová platforma** | `Linux` | `miIO.info` |
| **Časové pásmo** | `Europe/Prague` | `get_timezone` |
| **Hlasový balíček** | `en` (lokace `de`, BOM `A.03.0040`, `sid_in_use: 3`, `sid_version: 2`) | `get_current_sound` |
| **Aktivní příznaky FW (`get_fw_features`)** | `[111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 122, 123, 124, 125]` | `get_fw_features` |

---

## 2. Význam potvrzených příznaků firmwaru (`get_fw_features`)

Firmware `3.5.8_2700` na vašem S6 podporuje kompletní sadu pokročilých funkcí generace S6:
- **`111`**: Nový formát záznamů historie úklidu (`get_clean_record` s 9prvkovým polem včetně důvodu ukončení `finish_reason`).
- **`112`**: Podpora úklidu místností (`app_segment_clean`) a mapování místností (`get_room_mapping`).
- **`113`**: Podpora opakovaného úklidu (`repeat: 1..3`) pro místnosti i zóny.
- **`114` & `120`**: Podpora vícepatrových map (`get_multi_maps_list`, `load_multi_map`, max. `4` patra).
- **`115` & `117`**: Podpora automatického dělení mapy a úprav místností (`split_segment`, `merge_segment`).
- **`116`**: Podpora **No-Mop zakázaných zón** (potvrzeno i polem `"mop_forbidden_enable": 0` v `get_status`).
- **`118`**: Ovládání stavové LED kontrolky na horním panelu (`get_led_status` -> `[1]`, `set_led_status`).
- **`119` & `122`**: Přizpůsobení sacího výkonu a pořadí úklidu jednotlivých místností.
- **`123`**: Plánované úklidy konkrétních místností v časovačích (`get_timer` / `set_timer`).
- **`124`**: Chytré dobíjení (Smart Recharge – dobije pouze tolik energie, kolik chybí k dokončení rozdělaného patra).

---

## 3. Matice lokálních příkazů: Potvrzeno vs. Nepodporováno

### A. Potvrzené čtecí a diagnostické příkazy (Ověřeno živě na `3.5.8_2700`)

| Příkaz (`method`) | Skutečná návratová struktura na `3.5.8_2700` | Stav |
| :--- | :--- | :---: |
| `miIO.info` | Slovník: `model`, `fw_ver`, `hw_ver`, `mac`, `life`, `ap`, `netif` | **Potvrzeno** |
| `get_status` | Jednoprvkové pole se slovníkem (19 polí včetně `map_status`, `water_box_status`, `mop_forbidden_enable`, `lock_status`, `is_locating`) | **Potvrzeno** |
| `get_consumable` | Jednoprvkové pole se slovníkem: `main_brush_work_time`, `side_brush_work_time`, `filter_work_time`, `filter_element_work_time`, `sensor_dirty_time` | **Potvrzeno** |
| `get_clean_summary` | 4prvkové pole: `[clean_time_sec, clean_area_mm2, clean_count, [record_ids...]]` | **Potvrzeno** |
| `get_clean_record` | Parametr `[record_id]`. Vrací `[[start_ts, end_ts, duration_sec, area_mm2, error_code, completed, start_type, clean_type, finish_reason]]` | **Potvrzeno** |
| `get_multi_maps_list` | Jednoprvkové pole s objektem: `max_multi_map: 4`, `multi_map_count: 2`, `map_info: [{"mapFlag": 0, "name": "Lower floor"}, {"mapFlag": 1, "name": "Upper floor "}]` | **Potvrzeno** |
| `get_room_mapping` | Pole dvojic `[segment_id, "iot_id"]` pro aktuálně načtené patro (na slotu 1 `"Upper floor "` vrací `[]`) | **Potvrzeno** |
| `get_custom_mode` | `[102]` (aktuální sací výkon: `101`=Quiet, `102`=Balanced, `103`=Turbo, `104`=Max, `105`=Gentle) | **Potvrzeno** |
| `get_carpet_mode` | `[{"enable": 1, "current_integral": 450, "current_high": 500, "current_low": 400, "stall_time": 10}]` | **Potvrzeno** |
| `get_dnd_timer` | `[{"start_hour": 22, "start_minute": 0, "end_hour": 8, "end_minute": 0, "enabled": 1}]` | **Potvrzeno** |
| `get_sound_volume` | `[90]` (0–100 %) | **Potvrzeno** |
| `get_current_sound` | `[{"sid_in_use": 3, "sid_version": 2, "location": "de", "bom": "A.03.0040", "language": "en", "msg_ver": 2}]` | **Potvrzeno** |
| `get_led_status` | `[1]` (`1` = LED zapnutá, `0` = LED vypnutá) | **Potvrzeno** |
| `get_network_info` | Slovník: `{"ssid": "...", "ip": "192.168.1.xxx", "mac": "64:90:c1:xx:xx:xx", "bssid": "**REDACTED**", "rssi": -41}` | **Potvrzeno** |
| `get_serial_number` | `[{"serial_number": "R0306SXXXXXXXX"}]` | **Potvrzeno** |
| `get_timezone` | `["Europe/Prague"]` | **Potvrzeno** |
| `get_timer` / `get_server_timer` | `[]` (seznam lokálních / serverových plánovačů) | **Potvrzeno** |
| `get_fw_features` | `[111, 112, ..., 125]` | **Potvrzeno** |

### B. Potvrzeně NEPODPOROVANÉ metody na `roborock.vacuum.s6` (`3.5.8_2700`)

Při volání nepodporované metody firmware S6 nevrací chybu v poli `"error"`, ale vrátí `"result": "unknown_method"` (v našem klientu `RoborockLocalClient` je to nyní automaticky detekováno a převáděno na výjimku `RoborockCommandError`):

| Příkaz (`method`) | Odpověď vysavače | Vysvětlení a alternativa na S6 |
| :--- | :--- | :--- |
| `get_water_box_custom_mode` / `set_water_box_custom_mode` | `"unknown_method"` | **Nepodporováno:** Roborock S6 nemá elektronické čerpadlo vody (pouze mechanický přepínač na nádržce a Hallův senzor `water_box_status` v `get_status`). |
| `get_child_lock_status` / `set_child_lock_status` | `"unknown_method"` | **Nepodporováno jako samostatný příkaz:** Stav dětského zámku se čte přímo z pole `"lock_status"` v `get_status`. |
| `get_Flow_led_status` | `"unknown_method"` | **Nepodporováno:** S6 používá standardní příkaz `get_led_status` (který vrátil `[1]`). |

---

## 4. Důležitá specifika datových formátů na FW `3.5.8_2700`

1. **Jednotka plochy je vždy $\text{mm}^2$ ($1\text{ m}^2 = 1\,000\,000\text{ jednotek}$):**
   - Ověřeno na reálném záznamu úklidu ID `1794135571`: doba úklidu `2489 s` (**41,5 min**), surová hodnota plochy `48 442 500` -> **$48{,}44\text{ m}^2$**.
   - Celková historie (`get_clean_summary`): `610` úklidů, `419 802 s` (**116,6 hodin**), surová plocha `8 561 400 000` -> **$8\,561{,}4\text{ m}^2$**.
2. **Pátý čítač spotřebního materiálu (`filter_element_work_time`):**
   - Kromě hlavního kartáče, bočního kartáče, HEPA filtru a senzorů vrací `get_consumable` i `filter_element_work_time` (`111 337 s` = 30,9 h odpracováno; doporučený interval výměny vodních filtrů nádržky je 100 h).
3. **Vícepatrové mapy (`get_multi_maps_list`) a segmenty místností:**
   - Vysavač má přímo v sobě uložené názvy pater:
     - `mapFlag: 0` -> **`"Lower floor"`**
     - `mapFlag: 1` -> **`"Upper floor "`** (aktuálně načtená mapa, `active_map_slot == 1`)
   - **Potvrzeno živým přepnutím mapy (`load_multi_map [0]`):**
     - Po přepnutí na slot `0` (`"Lower floor"`) vrátil příkaz `get_room_mapping` 3 segmenty místností (`16`, `17`, `18`) spárované s cloudovými IoT ID.
     - Pokud místnosti na některém patře nebyly v aplikaci pojmenovány do cloudu, `get_room_mapping` může vrátit `[]`, avšak čísla segmentů místností (`1..31`) lze vždy kompletně vyčíst přímo z rastrového bloku `IMAGE` (blok 2) v souboru `.rrmap`.

