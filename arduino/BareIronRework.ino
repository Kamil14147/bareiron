#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
extern "C" {
#include "bareiron_arduino.h"
}

static const char *WIFI_SSID = "YOUR_WIFI";
static const char *WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
static const char *ADMIN_PASSWORD = "change-me";
WebServer web(80);

static bool auth() {
  return !ADMIN_PASSWORD[0] || (web.hasArg("pw") && web.arg("pw") == ADMIN_PASSWORD);
}

static void stats() {
  String j = "{";
  j += "\"cpu_mhz\":" + String(getCpuFrequencyMhz());
  j += ",\"cpu_load\":" + String(bareiron_cpu_load_percent());
  j += ",\"heap_free\":" + String(ESP.getFreeHeap());
  j += ",\"heap_total\":" + String(ESP.getHeapSize());
  j += ",\"heap_min\":" + String(ESP.getMinFreeHeap());
  j += ",\"uptime_ms\":" + String((unsigned long)millis());
  j += ",\"players\":" + String(bareiron_player_count());
  j += ",\"running\":" + String(bareiron_running() ? "true" : "false");
  j += "}";
  web.send(200, "application/json", j);
}

static void players() {
  String j = "[";
  for (int i = 0; i < bareiron_max_players(); ++i) {
    char name[32]; short x, z; uint8_t y;
    if (!bareiron_player_get(i, name, sizeof(name), &x, &y, &z)) continue;
    if (j.length() > 1) j += ',';
    j += "{\"id\":" + String(i) + ",\"name\":\"" + String(name) + "\",\"x\":" + String(x) + ",\"y\":" + String(y) + ",\"z\":" + String(z) + "}";
  }
  j += "]";
  web.send(200, "application/json", j);
}

static void control(void (*fn)()) {
  if (!auth()) { web.send(403, "application/json", "{\"error\":\"unauthorized\"}"); return; }
  fn();
  web.send(200, "application/json", "{\"ok\":true}");
}

static void kick() {
  if (!auth() || !web.hasArg("id")) { web.send(403, "application/json", "{\"error\":\"unauthorized\"}"); return; }
  web.send(200, "application/json", bareiron_kick(web.arg("id").toInt()) ? "{\"ok\":true}" : "{\"ok\":false}");
}

static const char PAGE[] PROGMEM = R"HTML(<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BareIron Rework</title><style>body{font-family:system-ui;background:#111;color:#eee;margin:0}main{max-width:1100px;margin:22px auto;padding:0 14px}.g{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}.c{background:#1b1f26;border:1px solid #2d333d;border-radius:14px;padding:14px}.k{color:#8d96a5;font-size:12px}.v{font-size:26px;font-weight:700;margin-top:5px}.bar{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}button,input{background:#13161b;color:#eee;border:1px solid #383f49;border-radius:9px;padding:10px}button{cursor:pointer}table{width:100%}td,th{text-align:left;padding:8px;border-bottom:1px solid #2a3038}</style></head><body><main><h1>BareIron Rework</h1><div class=g><div class=c><div class=k>CPU</div><div id=cpu class=v>-</div></div><div class=c><div class=k>CPU load</div><div id=load class=v>-</div></div><div class=c><div class=k>RAM free</div><div id=ram class=v>-</div></div><div class=c><div class=k>Players</div><div id=pc class=v>-</div></div><div class=c><div class=k>Server</div><div id=st class=v>-</div></div></div><div class=bar><input id=pw type=password placeholder="admin password"><button onclick="a('/api/start')">Start</button><button onclick="a('/api/stop')">Stop</button><button onclick="a('/api/restart')">Restart</button></div><div class=c><h3>Players</h3><table><thead><tr><th>ID</th><th>Name</th><th>X</th><th>Y</th><th>Z</th><th></th></tr></thead><tbody id=tb></tbody></table></div></main><script>const $=s=>document.querySelector(s),pw=()=>encodeURIComponent($('#pw').value);async function j(u){return fetch(u).then(r=>r.json())}async function a(u){await j(u+'?pw='+pw());load()}async function k(i){await j('/api/kick?pw='+pw()+'&id='+i);load()}async function load(){try{let s=await j('/api/stats');cpu.textContent=s.cpu_mhz+' MHz';load.textContent=s.cpu_load+'%';ram.textContent=Math.round(s.heap_free/1024)+' KiB';pc.textContent=s.players;st.textContent=s.running?'RUNNING':'STOPPED';let p=await j('/api/players');tb.innerHTML=p.map(x=>`<tr><td>${x.id}</td><td>${x.name}</td><td>${x.x}</td><td>${x.y}</td><td>${x.z}</td><td><button onclick="k(${x.id})">Kick</button></td></tr>`).join('')}catch(e){}}load();setInterval(load,1000)</script></body></html>)HTML";

static void start_server() { bareiron_start(); }
static void stop_server() { bareiron_stop(); }
static void restart_server() { bareiron_restart(); }

void setup() {
  Serial.begin(115200);
  delay(500);
  setCpuFrequencyMhz(240);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) { delay(250); Serial.print('.'); }
  Serial.println();
  Serial.print("Minecraft: "); Serial.println(WiFi.localIP());
  Serial.print("Panel: http://"); Serial.println(WiFi.localIP());
  web.on("/", HTTP_GET, [](){ web.send_P(200, "text/html", PAGE); });
  web.on("/api/stats", HTTP_GET, stats);
  web.on("/api/players", HTTP_GET, players);
  web.on("/api/start", HTTP_GET, [](){ control(start_server); });
  web.on("/api/stop", HTTP_GET, [](){ control(stop_server); });
  web.on("/api/restart", HTTP_GET, [](){ control(restart_server); });
  web.on("/api/kick", HTTP_GET, kick);
  web.begin();
  bareiron_start();
}

void loop() {
  web.handleClient();
  delay(2);
}
