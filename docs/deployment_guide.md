# Nasazení `LocalRoborock` do Home Assistantu (Docker na `Tulen-server`)

Tento návod popisuje kompletní postup nasazení nativní integrace `custom_components/local_roborock` včetně předpřipravených českých názvů místností (`rooms.json`), kešovaných map obou pater (`map_slot_0` a `map_slot_1`) a interaktivní webové mapy přímo do vašeho Docker kontejneru `homeassistant`.

---

## 1. Co obsahuje připravený instalační balíček

V adresáři `deploy/local_roborock_ha_bundle.tar.gz` (velikost ~129 KB) je zabalena celá struktura připravená k rozbalení přímo do `/config` uvnitř kontejneru `homeassistant`:
- `custom_components/local_roborock/*` – kompletní nativní integrace pro Home Assistant
- `.storage/local_roborock/rooms.json` – vaše pojmenované místnosti na obou patrech (*Obývák, Chodba, Kuchyně* na spodním patře; *Pracovna, Pracovna - gauč, Ložnice, Chodba (horní), Dětský pokoj* na horním patře)
- `.storage/local_roborock/map_cache/*` – stažené a sloučené binární mapy `.rrmap` + vykreslené `.png` a `.svg` mapy obou pater (takže mapa v Home Assistantu funguje okamžitě offline bez nutnosti stahovat ji z cloudu)
- `www/local_roborock_map.html` – interaktivní HTML5/Canvas mapa dostupná přímo v Home Assistantu na adrese `http://<IP_HOME_ASSISTANTA>:8123/local/local_roborock_map.html` (nebo jako Webová karta / Panel v dashboardu)

---

## 2. Postup instalace přes SCP + SSH (3 příkazy)

### Krok 1: Nahrání balíčku z vašeho Windows PC na server
V terminálu (PowerShell) ve složce projektu spusťte (nahraďte `<IP_SERVERU>` IP adresou nebo hostname vašeho serveru):

```powershell
scp deploy\local_roborock_ha_bundle.tar.gz user@<IP_SERVERU>:/tmp/
```

### Krok 2: Rozbalení přímo do Docker kontejneru `homeassistant` a restart HA
V SSH relaci na `Tulen-server` (jako `root` nebo s `sudo`) spusťte:

```bash
docker cp /tmp/local_roborock_ha_bundle.tar.gz homeassistant:/config/ && \
docker exec homeassistant tar -xzvf /config/local_roborock_ha_bundle.tar.gz -C /config/ && \
docker exec homeassistant rm /config/local_roborock_ha_bundle.tar.gz && \
rm /tmp/local_roborock_ha_bundle.tar.gz && \
docker restart homeassistant
```

### Krok 3: Přidání integrace v UI Home Assistantu a vypnutí původního Xiaomi Miot
1. Aby nedocházelo ke kolizím UDP paketů na portu `54321` mezi dvěma integracemi, v Home Assistantu přejděte do **Nastavení → Zařízení a služby → Xiaomi Miot Auto** a záznam vysavače **Luxík** buď **Zakažte (Disable)**, nebo odstraňte.
2. Klikněte na **Přidat integraci (Add Integration)** vpravo dole a vyhledejte **Local Roborock S6**.
3. Vyplňte:
   - **IP adresa vysavače:** `192.168.1.110`
   - **32znakový hexadecimální miIO token:** *(váš token ze souboru `.env`)*
   - **Interval dotazování:** `15`
   - *(Volitelné)* Pokud budete chtít z Home Assistantu spouštět i tlačítko `Refresh Floor Map` pro stažení nově naskenované mapy z FDS, můžete vyplnit i volitelná pole `Xiaomi User ID`, `Xiaomi Service Token` a `Xiaomi ssecurity` ze souboru `.env`. Jinak je můžete nechat prázdná – obě mapy už jsou v lokální cache!

---

## 3. Jak přidat Interaktivní Mapu do Dashboardu Home Assistantu

1. **Standardní karta s obrázkem mapy a tlačítky místností:**
   - Použijte entitu `image.roborock_s6_floor_map` spolu s přepínačem patra `select.roborock_s6_active_floor_map` a tlačítky `button.roborock_s6_clean_obyvak_lower_floor`, `button.roborock_s6_clean_detsky_pokoj_upper_floor` atd.
2. **Plně interaktivní HTML5/Canvas mapa a editor přímo v Lovelace Dashboardu:**
   - Přidejte na dashboard kartu typu **Webová stránka (Webpage / iframe)** s URL:
     ```text
     /local/local_roborock_map.html
     ```
     a poměrem stran např. `16:10`.
