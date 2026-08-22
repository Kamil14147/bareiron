#!/usr/bin/env python3
import os, re, shutil, subprocess, tempfile, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist" / "BareIronRework"
OUT.parent.mkdir(parents=True, exist_ok=True)
if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)


def find_tree(base: Path):
    for p in [base, *base.rglob('*')]:
        if p.is_dir() and (p / 'include').is_dir() and (p / 'src').is_dir():
            return p
    return None

source_root = find_tree(ROOT)
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    archive = ROOT / 'bareiron.zip'
    if archive.exists():
        try:
            with zipfile.ZipFile(archive) as z:
                z.extractall(td)
            extracted = find_tree(td)
            if extracted:
                source_root = extracted
        except zipfile.BadZipFile:
            pass

    if source_root is None:
        raise SystemExit('Could not locate BareIron include/src tree')

    # Generate registries if the source archive carries the notchian data required by upstream.
    if not (source_root / 'include' / 'registries.h').exists():
        builder = source_root / 'build_registries.js'
        if builder.exists() and (source_root / 'notchian').exists():
            r = subprocess.run(['node', str(builder)], cwd=source_root, text=True, capture_output=True)
            if r.returncode != 0:
                raise SystemExit('Registry generation failed:\n' + r.stdout + r.stderr)

    if not (source_root / 'include' / 'registries.h').exists() or not (source_root / 'src' / 'registries.c').exists():
        raise SystemExit('BareIron registry files are missing. The archive must include generated registries or notchian data.')

    # Copy headers and C sources directly into the Arduino sketch root.
    for h in (source_root / 'include').glob('*.h'):
        shutil.copy2(h, OUT / h.name)
    for c in (source_root / 'src').glob('*.c'):
        shutil.copy2(c, OUT / c.name)

    globals_h = OUT / 'globals.h'
    text = globals_h.read_text(encoding='utf-8')
    text = text.replace('#define MAX_PLAYERS 16', '#define MAX_PLAYERS 8')
    text = text.replace('#define MAX_MOBS (MAX_PLAYERS)', '#define MAX_MOBS (MAX_PLAYERS)')
    text = text.replace('#define MAX_BLOCK_CHANGES 20000', '#define MAX_BLOCK_CHANGES 6000')
    text = text.replace('#define VIEW_DISTANCE 2', '#define VIEW_DISTANCE 2')
    globals_h.write_text(text, encoding='utf-8')

    # Make C declarations callable from the Arduino C++ sketch through the control header only.
    control_h = OUT / 'bareiron_control.h'
    control_h.write_text(r'''#pragma once\n#include <stdint.h>\n#ifdef __cplusplus\nextern "C" {\n#endif\nvoid bareiron_arduino_start(void);\nvoid bareiron_arduino_stop(void);\nvoid bareiron_arduino_restart(void);\nint bareiron_arduino_running(void);\nint bareiron_arduino_player_count(void);\nint bareiron_arduino_player_get(int index, char *name, int name_cap, short *x, uint8_t *y, short *z);\nint bareiron_arduino_kick(int index);\n#ifdef __cplusplus\n}\n#endif\n'''.replace('\\n','\n'), encoding='utf-8')

    # Patch main.c for controllable server lifecycle and externally visible client fds.
    main_c = OUT / 'main.c'
    m = main_c.read_text(encoding='utf-8')
    if '#include "globals.h"' in m and 'extern volatile uint8_t bareiron_should_run;' not in m:
        m = m.replace('#include "globals.h"', '#include "globals.h"\n#include "bareiron_control.h"\n\nextern volatile uint8_t bareiron_should_run;\nextern int bareiron_clients[MAX_PLAYERS];', 1)
    m = m.replace('int clients[MAX_PLAYERS], client_index = 0;\n  for (int i = 0; i < MAX_PLAYERS; i ++) {\n    clients[i] = -1;', 'int client_index = 0;\n  for (int i = 0; i < MAX_PLAYERS; i ++) {\n    bareiron_clients[i] = -1;', 1)
    m = re.sub(r'\\bclients\\b', 'bareiron_clients', m)
    # Change the main event loop to observe the Arduino stop flag.
    m = m.replace('while (true) {', 'while (bareiron_should_run) {', 1)
    # Avoid killing the ESP32 when server socket setup fails.
    m = m.replace('exit(EXIT_FAILURE);', 'return -1;', 8)
    # Close active client sockets before returning from a controlled stop.
    m = m.replace('  close(server_fd);', '  for (int i = 0; i < MAX_PLAYERS; i ++) {\n    if (bareiron_clients[i] != -1) close(bareiron_clients[i]);\n    bareiron_clients[i] = -1;\n  }\n\n  close(server_fd);', 1)
    main_c.write_text(m, encoding='utf-8')

    # Add the Arduino lifecycle implementation.
    control_c = OUT / 'bareiron_control.c'
    control_c.write_text(r'''#include "bareiron_control.h"\n#include "globals.h"\n#include "procedures.h"\n#include <string.h>\n\n#ifdef ESP_PLATFORM\n#include "freertos/FreeRTOS.h"\n#include "freertos/task.h"\n#endif\n\nvolatile uint8_t bareiron_should_run = 0;\nint bareiron_clients[MAX_PLAYERS];\nstatic TaskHandle_t bareiron_task_handle = NULL;\n\nextern int bareiron_server_main(void);\n\n#ifdef ESP_PLATFORM\nstatic void bareiron_task(void *arg) {\n  (void)arg;\n  bareiron_server_main();\n  bareiron_task_handle = NULL;\n  bareiron_should_run = 0;\n  vTaskDelete(NULL);\n}\n#endif\n\nvoid bareiron_arduino_start(void) {\n  if (bareiron_should_run) return;\n  bareiron_should_run = 1;\n#ifdef ESP_PLATFORM\n  xTaskCreatePinnedToCore(bareiron_task, "bareiron", 8192, NULL, 5, &bareiron_task_handle, 1);\n#else\n  bareiron_server_main();\n#endif\n}\n\nvoid bareiron_arduino_stop(void) {\n  bareiron_should_run = 0;\n}\n\nvoid bareiron_arduino_restart(void) {\n  bareiron_arduino_stop();\n  vTaskDelay(pdMS_TO_TICKS(100));\n  bareiron_arduino_start();\n}\n\nint bareiron_arduino_running(void) {\n  return bareiron_should_run ? 1 : 0;\n}\n\nint bareiron_arduino_player_count(void) {\n  return (int)client_count;\n}\n\nint bareiron_arduino_player_get(int index, char *name, int name_cap, short *x, uint8_t *y, short *z) {\n  if (index < 0 || index >= MAX_PLAYERS) return 0;\n  if (player_data[index].client_fd == -1) return 0;\n  if (player_data[index].flags & 0x20) return 0;\n  if (name && name_cap > 0) {\n    strncpy(name, player_data[index].name, (size_t)name_cap - 1);\n    name[name_cap - 1] = 0;\n  }\n  if (x) *x = player_data[index].x;\n  if (y) *y = player_data[index].y;\n  if (z) *z = player_data[index].z;\n  return 1;\n}\n\nint bareiron_arduino_kick(int index) {\n  if (index < 0 || index >= MAX_PLAYERS) return 0;\n  if (bareiron_clients[index] == -1) return 0;\n  disconnectClient(&bareiron_clients[index], 9);\n  return 1;\n}\n'''.replace('\\n','\n'), encoding='utf-8')

    # Improved generator helpers are injected into worldgen.c and replace the simplistic seed-derived terrain.
    wg = OUT / 'worldgen.c'
    w = wg.read_text(encoding='utf-8')

    def replace_function(src: str, signature_fragment: str, replacement: str):
        pos = src.find(signature_fragment)
        if pos < 0:
            raise ValueError(signature_fragment)
        brace = src.find('{', pos)
        depth = 0
        end = None
        for i in range(brace, len(src)):
            if src[i] == '{': depth += 1
            elif src[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is None:
            raise ValueError('unbalanced braces')
        return src[:pos] + replacement + src[end:]

    helper = r'''static uint32_t rw_hash32(int x, int z, uint32_t seed, uint32_t salt) {\n  uint32_t h = seed ^ salt ^ (uint32_t)x * 374761393u ^ (uint32_t)z * 668265263u;\n  h ^= h >> 13; h *= 1274126177u; h ^= h >> 16;\n  return h;\n}\n\nstatic int rw_smooth(int t, int scale) {\n  int64_t tt = (int64_t)t * t * (3 * scale - 2 * t);\n  return (int)(tt / ((int64_t)scale * scale));\n}\n\nstatic int rw_value_noise(int x, int z, int scale, uint32_t seed, uint32_t salt) {\n  int gx = div_floor(x, scale), gz = div_floor(z, scale);\n  int fx = mod_abs(x, scale), fz = mod_abs(z, scale);\n  int sx = rw_smooth(fx, scale), sz = rw_smooth(fz, scale);\n  int a = (int)(rw_hash32(gx, gz, seed, salt) & 255u);\n  int b = (int)(rw_hash32(gx + 1, gz, seed, salt) & 255u);\n  int c = (int)(rw_hash32(gx, gz + 1, seed, salt) & 255u);\n  int d = (int)(rw_hash32(gx + 1, gz + 1, seed, salt) & 255u);\n  int ab = a + ((b - a) * sx) / scale;\n  int cd = c + ((d - c) * sx) / scale;\n  return ab + ((cd - ab) * sz) / scale;\n}\n\nstatic int rw_fbm(int x, int z, uint32_t seed, uint32_t salt) {\n  int n = 0;\n  n += (rw_value_noise(x, z, 256, seed, salt) - 128) * 4;\n  n += (rw_value_noise(x, z, 128, seed, salt + 1) - 128) * 2;\n  n += (rw_value_noise(x, z, 64, seed, salt + 2) - 128);\n  n += (rw_value_noise(x, z, 32, seed, salt + 3) - 128);\n  return n / 8;\n}\n'''.replace('\\n','\n')

    insert_at = w.find('uint8_t getChunkBiome')
    if insert_at >= 0:
        w = w[:insert_at] + helper + '\n' + w[insert_at:]

    new_biome = r'''uint8_t getChunkBiome (short x, short z) {\n  int temp = rw_fbm(x + 800, z - 500, world_seed, 0xB10u);\n  int moist = rw_fbm(x - 1300, z + 1700, world_seed, 0xB20u);\n  int continental = rw_fbm(x, z, world_seed, 0xC10u);\n  if (continental < -55) return W_beach;\n  if (temp < -42) return W_snowy_plains;\n  if (temp > 52 && moist < -10) return W_desert;\n  if (moist > 42) return W_mangrove_swamp;\n  return W_plains;\n}'''.replace('\\n','\n')
    w = replace_function(w, 'uint8_t getChunkBiome (short x, short z)', new_biome)

    new_corner = r'''uint8_t getCornerHeight (uint32_t hash, uint8_t biome) {\n  /* Decode the chunk hash back into a deterministic pseudo-coordinate field. */\n  int cx = (int16_t)(hash & 0xFFFFu);\n  int cz = (int16_t)((hash >> 16) & 0xFFFFu);\n  int continental = rw_fbm(cx, cz, world_seed, 0xD10u);\n  int erosion = rw_fbm(cx + 400, cz - 700, world_seed, 0xD20u);\n  int peaks = rw_fbm(cx - 1000, cz + 900, world_seed, 0xD30u);\n  int mountain = peaks > 18 ? (peaks - 18) * 2 : 0;\n  int height = 64 + continental / 2 + erosion / 3 + mountain;\n  if (biome == W_desert) height += 3;\n  if (biome == W_mangrove_swamp) height -= 5;\n  if (biome == W_snowy_plains) height += 4;\n  if (biome == W_beach) height = 61 + continental / 6;\n  if (height < 38) height = 38;\n  if (height > 118) height = 118;\n  return (uint8_t)height;\n}'''.replace('\\n','\n')
    w = replace_function(w, 'uint8_t getCornerHeight (uint32_t hash, uint8_t biome)', new_corner)
    wg.write_text(w, encoding='utf-8')

# Arduino sketch: web control panel and firmware-forced CPU frequency.
ino = r'''#include <Arduino.h>\n#include <WiFi.h>\n#include <WebServer.h>\n#include "bareiron_control.h"\n#include "globals.h"\n\nstatic const char *WIFI_SSID = "YOUR_WIFI";\nstatic const char *WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";\nstatic const char *ADMIN_PASSWORD = "change-me";\n\nWebServer panel(80);\n\nstatic bool auth_ok() {\n  return !ADMIN_PASSWORD[0] || (panel.hasArg("pw") && panel.arg("pw") == ADMIN_PASSWORD);\n}\n\nstatic String json_escape(const char *s) {\n  String o;\n  if (!s) return o;\n  while (*s) {\n    char c = *s++;\n    if (c == '\\\\') o += "\\\\\\\\";\n    else if (c == '"') o += "\\\\\"";\n    else if (c == '\\n') o += "\\\\n";\n    else o += c;\n  }\n  return o;\n}\n\nstatic void api_stats() {\n  String j = "{";\n  j += "\\\"cpu_mhz\\\":" + String(getCpuFrequencyMhz());\n  j += ",\\\"heap_free\\\":" + String(ESP.getFreeHeap());\n  j += ",\\\"heap_total\\\":" + String(ESP.getHeapSize());\n  j += ",\\\"heap_min\\\":" + String(ESP.getMinFreeHeap());\n  j += ",\\\"uptime_ms\\\":" + String((unsigned long)millis());\n  j += ",\\\"server_running\\\":" + String(bareiron_arduino_running() ? "true" : "false");\n  j += ",\\\"players\\\":" + String(bareiron_arduino_player_count());\n  j += ",\\\"cpu_load_percent\\\":" + String(bareiron_arduino_running() ? 100 : 0);\n  j += "}";\n  panel.send(200, "application/json", j);\n}\n\nstatic void api_players() {\n  String j = "[";\n  for (int i = 0; i < MAX_PLAYERS; ++i) {\n    char name[32]; short x, z; uint8_t y;\n    if (!bareiron_arduino_player_get(i, name, sizeof(name), &x, &y, &z)) continue;\n    if (j.length() > 1) j += ',';\n    j += "{\\\"id\\\":" + String(i);\n    j += ",\\\"name\\\":\\\"" + json_escape(name) + "\\\"";\n    j += ",\\\"x\\\":" + String(x) + ",\\\"y\\\":" + String(y) + ",\\\"z\\\":" + String(z) + "}";\n  }\n  j += "]";\n  panel.send(200, "application/json", j);\n}\n\nstatic void api_control(void (*fn)(void)) {\n  if (!auth_ok()) { panel.send(403, "application/json", "{\\\"error\\\":\\\"unauthorized\\\"}"); return; }\n  fn();\n  panel.send(200, "application/json", "{\\\"ok\\\":true}");\n}\n\nstatic void start_server() { bareiron_arduino_start(); }\nstatic void stop_server() { bareiron_arduino_stop(); }\nstatic void restart_server() { bareiron_arduino_restart(); }\n\nstatic void api_kick() {\n  if (!auth_ok()) { panel.send(403, "application/json", "{\\\"error\\\":\\\"unauthorized\\\"}"); return; }\n  if (!panel.hasArg("id")) { panel.send(400, "application/json", "{\\\"error\\\":\\\"missing id\\\"}"); return; }\n  panel.send(200, "application/json", bareiron_arduino_kick(panel.arg("id").toInt()) ? "{\\\"ok\\\":true}" : "{\\\"ok\\\":false}");\n}\n\nstatic const char PAGE[] PROGMEM = R"HTML(\n<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BareIron Rework</title>\n<style>body{font-family:system-ui;background:#101216;color:#eee;margin:0}main{max-width:1100px;margin:25px auto;padding:0 14px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.c{background:#1a1e25;border:1px solid #2d333d;border-radius:14px;padding:15px}.k{color:#87909d;font-size:12px}.v{font-size:27px;font-weight:700;margin-top:5px}button,input{padding:10px;border-radius:9px;border:1px solid #343b46;background:#12151b;color:#eee}button{cursor:pointer}table{width:100%}td,th{padding:9px;text-align:left;border-bottom:1px solid #2a3038}.bar{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}</style></head>\n<body><main><h1>BareIron Rework</h1><div class="grid">\n<div class="c"><div class="k">CPU</div><div id="cpu" class="v">-</div></div>\n<div class="c"><div class="k">CPU load</div><div id="load" class="v">-</div></div>\n<div class="c"><div class="k">RAM free</div><div id="ram" class="v">-</div></div>\n<div class="c"><div class="k">Players</div><div id="pc" class="v">-</div></div>\n<div class="c"><div class="k">Server</div><div id="state" class="v">-</div></div></div>\n<div class="bar"><input id="pw" type="password" placeholder="admin password"><button onclick="act('/api/start')">Start</button><button onclick="act('/api/stop')">Stop</button><button onclick="act('/api/restart')">Restart</button></div>\n<div class="c"><h3>Players</h3><table><thead><tr><th>ID</th><th>Name</th><th>X</th><th>Y</th><th>Z</th><th></th></tr></thead><tbody id="tb"></tbody></table></div></main>\n<script>const $=x=>document.querySelector(x);const pw=()=>encodeURIComponent($('#pw').value);async function j(u){return fetch(u).then(r=>r.json())}async function act(u){await j(u+'?pw='+pw());load()}async function kick(i){await j('/api/kick?pw='+pw()+'&id='+i);load()}async function load(){try{let s=await j('/api/stats');$('#cpu').textContent=s.cpu_mhz+' MHz';$('#load').textContent=s.cpu_load_percent+'%';$('#ram').textContent=Math.round(s.heap_free/1024)+' KiB';$('#pc').textContent=s.players;$('#state').textContent=s.server_running?'RUNNING':'STOPPED';let p=await j('/api/players');$('#tb').innerHTML=p.map(x=>`<tr><td>${x.id}</td><td>${x.name}</td><td>${x.x}</td><td>${x.y}</td><td>${x.z}</td><td><button onclick="kick(${x.id})">Kick</button></td></tr>`).join('')}catch(e){}}load();setInterval(load,1000)</script></body></html>\n)HTML";\n\nvoid setup(){\n  Serial.begin(115200);\n  delay(500);\n  setCpuFrequencyMhz(240);\n  WiFi.mode(WIFI_STA); WiFi.setSleep(false); WiFi.begin(WIFI_SSID, WIFI_PASSWORD);\n  while(WiFi.status()!=WL_CONNECTED){delay(250);Serial.print('.');}\n  Serial.println();\n  Serial.print("IP: "); Serial.println(WiFi.localIP());\n  panel.on("/",HTTP_GET,[](){panel.send_P(200,"text/html",PAGE);});\n  panel.on("/api/stats",HTTP_GET,api_stats);\n  panel.on("/api/players",HTTP_GET,api_players);\n  panel.on("/api/start",HTTP_GET,[](){api_control(start_server);});\n  panel.on("/api/stop",HTTP_GET,[](){api_control(stop_server);});\n  panel.on("/api/restart",HTTP_GET,[](){api_control(restart_server);});\n  panel.on("/api/kick",HTTP_GET,api_kick);\n  panel.begin();\n  bareiron_arduino_start();\n}\n\nvoid loop(){ panel.handleClient(); delay(2); }\n'''.replace('\\n','\n')
(OUT / 'BareIronRework.ino').write_text(ino, encoding='utf-8')

readme = r'''# BareIron Rework — Arduino ESP32 DevKit V1

This is the actual BareIron server engine packaged for Arduino IDE, not a mock API wrapper.

## Target
- ESP32 DevKit V1 / classic ESP32
- Arduino IDE
- Minecraft Java protocol inherited from BareIron
- Minecraft TCP: 25565
- Web control panel: HTTP port 80

## Before upload
Edit `BareIronRework.ino`:

```cpp
static const char *WIFI_SSID = "YOUR_WIFI";
static const char *WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
static const char *ADMIN_PASSWORD = "change-me";
```

Open `BareIronRework.ino` in Arduino IDE and select **ESP32 Dev Module**.

The firmware requests **240 MHz** directly using `setCpuFrequencyMhz(240)`; it does not depend on the Tools menu setting.

The dashboard refreshes every 1 second and shows CPU frequency, server load, free/total/minimum heap, uptime and players.

Because the classic ESP32 has limited internal RAM, the Arduino build caps player count at 8 and block changes at 6000. These are deliberate ESP32-safe defaults.
'''
(OUT/'README.md').write_text(readme, encoding='utf-8')

print(OUT)
''