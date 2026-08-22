#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <lwip/sockets.h>

extern "C" {
#include "globals.h"
}

WebServer controlPanel(80);
bool bareiron_enabled = true;
extern void bareiron_main(void *);

static const char *ADMIN_PASSWORD = "change-me";

static bool auth_ok() {
  return controlPanel.hasArg("pw") && controlPanel.arg("pw") == ADMIN_PASSWORD;
}

static String esc(const char *s) {
  String out;
  if (!s) return out;
  while (*s) {
    char c = *s++;
    if (c == '\\') out += "\\\\";
    else if (c == '"') out += "\\\"";
    else if (c == '\n') out += "\\n";
    else out += c;
  }
  return out;
}

static uint32_t cpu_load_percent() {
  return bareiron_enabled ? 100 : 0;
}

static void api_stats() {
  String j = "{";
  j += "\"cpu_mhz\":" + String(getCpuFrequencyMhz());
  j += ",\"cpu_load\":" + String(cpu_load_percent());
  j += ",\"heap_free\":" + String(ESP.getFreeHeap());
  j += ",\"heap_total\":" + String(ESP.getHeapSize());
  j += ",\"heap_min\":" + String(ESP.getMinFreeHeap());
  j += ",\"uptime_ms\":" + String((unsigned long)millis());
  j += ",\"players\":" + String((unsigned)client_count);
  j += ",\"running\":" + String(bareiron_enabled ? "true" : "false");
  j += "}";
  controlPanel.send(200, "application/json", j);
}

static void api_players() {
  String j = "[";
  bool first = true;
  for (int i = 0; i < MAX_PLAYERS; ++i) {
    if (player_data[i].client_fd < 0) continue;
    if (!first) j += ",";
    first = false;
    j += "{\"id\":" + String(i);
    j += ",\"name\":\"" + esc(player_data[i].name) + "\"";
    j += ",\"x\":" + String(player_data[i].x);
    j += ",\"y\":" + String(player_data[i].y);
    j += ",\"z\":" + String(player_data[i].z);
    j += "}";
  }
  j += "]";
  controlPanel.send(200, "application/json", j);
}

static void api_start() {
  if (!auth_ok()) return controlPanel.send(403, "application/json", "{\"error\":\"unauthorized\"}");
  if (!bareiron_enabled) {
    bareiron_enabled = true;
    xTaskCreate(bareiron_main, "bareiron", 8192, nullptr, 5, nullptr);
  }
  controlPanel.send(200, "application/json", "{\"ok\":true}");
}

static void api_stop() {
  if (!auth_ok()) return controlPanel.send(403, "application/json", "{\"error\":\"unauthorized\"}");
  bareiron_enabled = false;
  for (int i = 0; i < MAX_PLAYERS; ++i) {
    if (player_data[i].client_fd >= 0) shutdown(player_data[i].client_fd, SHUT_RDWR);
  }
  controlPanel.send(200, "application/json", "{\"ok\":true}");
}

static void api_restart() {
  if (!auth_ok()) return controlPanel.send(403, "application/json", "{\"error\":\"unauthorized\"}");
  bareiron_enabled = false;
  for (int i = 0; i < MAX_PLAYERS; ++i) {
    if (player_data[i].client_fd >= 0) shutdown(player_data[i].client_fd, SHUT_RDWR);
  }
  vTaskDelay(pdMS_TO_TICKS(300));
  bareiron_enabled = true;
  xTaskCreate(bareiron_main, "bareiron", 8192, nullptr, 5, nullptr);
  controlPanel.send(200, "application/json", "{\"ok\":true}");
}

static void api_kick() {
  if (!auth_ok()) return controlPanel.send(403, "application/json", "{\"error\":\"unauthorized\"}");
  if (!controlPanel.hasArg("id")) return controlPanel.send(400, "application/json", "{\"error\":\"missing id\"}");
  int id = controlPanel.arg("id").toInt();
  if (id < 0 || id >= MAX_PLAYERS || player_data[id].client_fd < 0) return controlPanel.send(404, "application/json", "{\"ok\":false}");
  shutdown(player_data[id].client_fd, SHUT_RDWR);
  controlPanel.send(200, "application/json", "{\"ok\":true}");
}

static const char INDEX_HTML[] PROGMEM = R"HTML(
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BareIron Rework</title>
<style>
body{font-family:system-ui;margin:0;background:#0f1115;color:#f2f4f7}main{max-width:1050px;margin:24px auto;padding:0 15px}.g{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.c{background:#181c23;border:1px solid #2b313b;border-radius:15px;padding:16px}.l{font-size:12px;color:#919aa8}.v{font-size:27px;font-weight:700;margin-top:5px}button,input{background:#101319;color:#fff;border:1px solid #3b4350;border-radius:9px;padding:9px 12px;margin:4px}table{width:100%;border-collapse:collapse}td,th{text-align:left;border-bottom:1px solid #2a3039;padding:9px}
</style></head><body><main><h1>BareIron Rework</h1><p>ESP32 DevKit V1 • Minecraft server</p>
<section class="g"><div class="c"><div class="l">CPU</div><div class="v" id="cpu">-</div></div><div class="c"><div class="l">Server CPU load</div><div class="v" id="load">-</div></div><div class="c"><div class="l">Free RAM</div><div class="v" id="ram">-</div><div class="l" id="ram2"></div></div><div class="c"><div class="l">Players</div><div class="v" id="players">-</div></div></section>
<section class="c" style="margin-top:12px"><input id="pw" type="password" placeholder="admin password"><button onclick="act('/api/start')">START</button><button onclick="act('/api/restart')">RESTART</button><button onclick="act('/api/stop')">STOP</button><b id="state"></b></section>
<section class="c" style="margin-top:12px"><h3>Players</h3><table><thead><tr><th>ID</th><th>Name</th><th>Position</th><th></th></tr></thead><tbody id="plist"></tbody></table></section>
</main><script>
const $=x=>document.querySelector(x);const pw=()=>encodeURIComponent($('#pw').value);
async function act(u){await fetch(u+'?pw='+pw());refresh()}async function kick(i){await fetch('/api/kick?pw='+pw()+'&id='+i);refresh()}
async function refresh(){try{let s=await fetch('/api/stats').then(r=>r.json());$('#cpu').textContent=s.cpu_mhz+' MHz';$('#load').textContent=s.cpu_load+'%';$('#ram').textContent=Math.round(s.heap_free/1024)+' KiB';$('#ram2').textContent='min '+Math.round(s.heap_min/1024)+' / total '+Math.round(s.heap_total/1024)+' KiB';$('#players').textContent=s.players;$('#state').textContent=s.running?' RUNNING':' STOPPED';let p=await fetch('/api/players').then(r=>r.json());$('#plist').innerHTML=p.map(x=>'<tr><td>'+x.id+'</td><td>'+x.name+'</td><td>'+x.x+', '+x.y+', '+x.z+'</td><td><button onclick="kick('+x.id+')">KICK</button></td></tr>').join('')}catch(e){}}
refresh();setInterval(refresh,1000);
</script></body></html>
)HTML";

extern "C" void control_panel_begin() {
  controlPanel.on("/", HTTP_GET, [](){ controlPanel.send_P(200, "text/html", INDEX_HTML); });
  controlPanel.on("/api/stats", HTTP_GET, api_stats);
  controlPanel.on("/api/players", HTTP_GET, api_players);
  controlPanel.on("/api/start", HTTP_GET, api_start);
  controlPanel.on("/api/stop", HTTP_GET, api_stop);
  controlPanel.on("/api/restart", HTTP_GET, api_restart);
  controlPanel.on("/api/kick", HTTP_GET, api_kick);
  controlPanel.begin();
}

extern "C" void control_panel_handle() {
  controlPanel.handleClient();
}
