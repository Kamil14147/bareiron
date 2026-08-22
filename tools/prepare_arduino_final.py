from pathlib import Path
import re
import shutil
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "arduino" / "BareIronRework"
OUT.mkdir(parents=True, exist_ok=True)

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    archive = ROOT / "bareiron.zip"
    with zipfile.ZipFile(archive, "r") as z:
        z.extractall(td)
    src = next((p for p in td.rglob("bareiron.ino") if any(p.parent.glob("*.c"))), None)
    if src is None:
        raise SystemExit("BareIron bundle not found")

    for p in OUT.iterdir():
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    for p in src.parent.glob("*.h"):
        shutil.copy2(p, OUT / p.name)
    for p in src.parent.glob("*.c"):
        if p.name != "main.c":
            shutil.copy2(p, OUT / p.name)
    text = src.read_text(encoding="utf-8")

# Keep C/C++ linkage correct when Arduino compiles the sketch as C++.
old = '''#include "globals.h"
#include "tools.h"
#include "varnum.h"
#include "packets.h"
#include "worldgen.h"
#include "registries.h"
#include "procedures.h"
#include "serialize.h"'''
new = '''extern "C" {
#include "globals.h"
#include "tools.h"
#include "varnum.h"
#include "packets.h"
#include "worldgen.h"
#include "registries.h"
#include "procedures.h"
#include "serialize.h"
}'''
if old not in text:
    raise SystemExit("C header block not found")
text = text.replace(old, new, 1)

# The Arduino sketch owns the application entry point; the upstream POSIX main.c is excluded.
text = text.replace('while (true) {', 'while (bareiron_enabled) {', 1)
text = text.replace('void setup () {\n  Serial.begin(115200);\n  delay(500);\n\n  wifi_init();\n}', 'void setup () {\n  Serial.begin(115200);\n  delay(500);\n  setCpuFrequencyMhz(240);\n  WiFi.setSleep(false);\n  control_panel_begin();\n  wifi_init();\n}', 1)
text = text.replace('void loop () {\n  delay(1000);\n}', 'void loop () {\n  control_panel_handle();\n  delay(1);\n}', 1)

# Add the panel interface and server lifecycle state before the existing ESP32 section.
marker = '#ifdef ESP_PLATFORM\n\nvoid bareiron_main'
insert = '''extern bool bareiron_enabled;\nextern "C" void control_panel_begin();\nextern "C" void control_panel_handle();\n\n#ifdef ESP_PLATFORM\n\nvoid bareiron_main'''
if marker not in text:
    raise SystemExit("ESP32 server task marker not found")
text = text.replace(marker, insert, 1)

(OUT / "BareIronRework.ino").write_text(text, encoding="utf-8")

# Tune memory for classic ESP32 without changing protocol/game logic.
g = OUT / "globals.h"
t = g.read_text(encoding="utf-8")
t = re.sub(r'#define MAX_BLOCK_CHANGES\s+\d+', '#define MAX_BLOCK_CHANGES 1500', t)
t = t.replace('#define DO_FLUID_FLOW', '// #define DO_FLUID_FLOW')
t = t.replace('#define ALLOW_CHESTS', '// #define ALLOW_CHESTS')
t = t.replace('#define ENABLE_PICKUP_ANIMATION', '// #define ENABLE_PICKUP_ANIMATION')
t = t.replace('#define DEV_LOG_LENGTH_DISCREPANCY', '// #define DEV_LOG_LENGTH_DISCREPANCY')
g.write_text(t, encoding="utf-8")

# Let the BareIron task yield about once per millisecond instead of burning a full core.
g = OUT / "globals.c"
t = g.read_text(encoding="utf-8").replace('#define TASK_YIELD_INTERVAL 1000 * 1000', '#define TASK_YIELD_INTERVAL 1000')
g.write_text(t, encoding="utf-8")

# Replace the terrain height/biome functions with a deterministic multi-scale integer terrain field.
w = OUT / "worldgen.c"
t = w.read_text(encoding="utf-8")
helper = r'''
static int rw_floor_div(int x, int d) {
  if (x >= 0) return x / d;
  return -(((-x) + d - 1) / d);
}
static int rw_mod(int x, int d) {
  int r = x % d;
  return r < 0 ? r + d : r;
}
static uint32_t rw_hash(int x, int z, uint32_t seed, uint32_t salt) {
  uint32_t h = seed ^ salt ^ (uint32_t)x * 374761393u ^ (uint32_t)z * 668265263u;
  h ^= h >> 13; h *= 1274126177u; h ^= h >> 16;
  return h;
}
static int rw_smooth(int t, int n) {
  return (int)(((int64_t)t * t * (3 * n - 2 * t)) / ((int64_t)n * n));
}
static int rw_noise(int x, int z, int n, uint32_t seed, uint32_t salt) {
  int gx = rw_floor_div(x, n), gz = rw_floor_div(z, n);
  int fx = rw_smooth(rw_mod(x, n), n), fz = rw_smooth(rw_mod(z, n), n);
  int a = (int)(rw_hash(gx, gz, seed, salt) & 255u);
  int b = (int)(rw_hash(gx + 1, gz, seed, salt) & 255u);
  int c = (int)(rw_hash(gx, gz + 1, seed, salt) & 255u);
  int d = (int)(rw_hash(gx + 1, gz + 1, seed, salt) & 255u);
  int ab = a + ((b - a) * fx) / n;
  int cd = c + ((d - c) * fx) / n;
  return ab + ((cd - ab) * fz) / n;
}
static int rw_fbm(int x, int z, uint32_t seed, uint32_t salt) {
  return ((rw_noise(x,z,256,seed,salt)-128)*4 +
          (rw_noise(x,z,128,seed,salt+1)-128)*2 +
          (rw_noise(x,z,64,seed,salt+2)-128) +
          (rw_noise(x,z,32,seed,salt+3)-128)) / 8;
}
static int rw_height(int x, int z, uint8_t biome) {
  int continental = rw_fbm(x, z, world_seed, 0xC100u);
  int erosion = rw_fbm(x + 400, z - 700, world_seed, 0xC200u);
  int peaks = rw_fbm(x - 1000, z + 900, world_seed, 0xC300u);
  int h = 64 + continental / 2 + erosion / 3;
  if (peaks > 18) h += (peaks - 18) * 2;
  if (biome == W_desert) h += 3;
  if (biome == W_mangrove_swamp) h -= 5;
  if (biome == W_snowy_plains) h += 4;
  if (biome == W_beach) h = 61 + continental / 6;
  if (h < 38) h = 38;
  if (h > 118) h = 118;
  return h;
}
'''
if 'static int rw_floor_div' not in t:
    t = t.replace('uint8_t getChunkBiome', helper + '\nuint8_t getChunkBiome', 1)

def replace_fn(src, signature, replacement):
    start = src.find(signature)
    if start < 0:
        raise SystemExit(f'function not found: {signature}')
    brace = src.find('{', start)
    depth = 0
    for i in range(brace, len(src)):
        if src[i] == '{': depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                return src[:start] + replacement + src[i+1:]
    raise SystemExit(f'unbalanced function: {signature}')

t = replace_fn(t, 'uint8_t getChunkBiome (short x, short z)', '''uint8_t getChunkBiome (short x, short z) {
  int temp = rw_fbm(x + 800, z - 500, world_seed, 0xB100u);
  int moist = rw_fbm(x - 1300, z + 1700, world_seed, 0xB200u);
  int continental = rw_fbm(x, z, world_seed, 0xB300u);
  if (continental < -55) return W_beach;
  if (temp < -42) return W_snowy_plains;
  if (temp > 52 && moist < -10) return W_desert;
  if (moist > 42) return W_mangrove_swamp;
  return W_plains;
}''')
t = replace_fn(t, 'uint8_t getHeightAtFromHash (int rx, int rz, int _x, int _z, uint32_t chunk_hash, uint8_t biome)', '''uint8_t getHeightAtFromHash (int rx, int rz, int _x, int _z, uint32_t chunk_hash, uint8_t biome) {
  (void)chunk_hash;
  uint8_t h00 = (uint8_t)rw_height(_x, _z, biome);
  uint8_t h10 = (uint8_t)rw_height(_x + 1, _z, getChunkBiome(_x + 1, _z));
  uint8_t h01 = (uint8_t)rw_height(_x, _z + 1, getChunkBiome(_x, _z + 1));
  uint8_t h11 = (uint8_t)rw_height(_x + 1, _z + 1, getChunkBiome(_x + 1, _z + 1));
  return interpolate(h00, h10, h01, h11, rx, rz);
}''')
w.write_text(t, encoding="utf-8")
