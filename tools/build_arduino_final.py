from pathlib import Path
import re, shutil, tempfile, zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'arduino' / 'BareIronRework'
OUT.mkdir(parents=True, exist_ok=True)

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    with zipfile.ZipFile(ROOT / 'bareiron.zip') as z:
        z.extractall(td)
    src = next((p.parent for p in td.rglob('bareiron.ino') if any(p.parent.glob('*.c'))), None)
    if src is None:
        raise SystemExit('BareIron source bundle not found')
    for p in OUT.iterdir():
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    for p in src.glob('*.h'):
        shutil.copy2(p, OUT / p.name)
    for p in src.glob('*.c'):
        if p.name != 'main.c':
            shutil.copy2(p, OUT / p.name)
    text = (src / 'bareiron.ino').read_text(encoding='utf-8')

# BareIron's C headers must have C linkage when the Arduino sketch is C++.
block = '''#include "globals.h"\n#include "tools.h"\n#include "varnum.h"\n#include "packets.h"\n#include "worldgen.h"\n#include "registries.h"\n#include "procedures.h"\n#include "serialize.h"'''
wrapped = '''extern "C" {\n#include "globals.h"\n#include "tools.h"\n#include "varnum.h"\n#include "packets.h"\n#include "worldgen.h"\n#include "registries.h"\n#include "procedures.h"\n#include "serialize.h"\n}'''
if block not in text:
    raise SystemExit('BareIron include block changed unexpectedly')
text = text.replace(block, wrapped, 1)

# Add panel hooks and firmware-side CPU setting.
text = text.replace('#include <errno.h>\n', '#include <errno.h>\n#include <WiFi.h>\n#include <WebServer.h>\nextern "C" void control_panel_begin();\nextern "C" void control_panel_handle();\nextern volatile bool bareiron_enabled;\n', 1)
text = text.replace('while (true) {', 'while (bareiron_enabled) {', 1)
text = text.replace('void setup () {\n  Serial.begin(115200);\n  delay(500);\n\n  wifi_init();\n}', 'void setup () {\n  Serial.begin(115200);\n  delay(500);\n  setCpuFrequencyMhz(240);\n  WiFi.setSleep(false);\n  control_panel_begin();\n  wifi_init();\n}', 1)
text = text.replace('void loop () {\n  delay(1000);\n}', 'void loop () {\n  control_panel_handle();\n  delay(1);\n}', 1)

# Expose the task state and keep server task in Arduino-managed lifecycle.
text = text.replace('void bareiron_main (void *pvParameters) {', 'extern volatile bool bareiron_enabled;\nvoid bareiron_main (void *pvParameters) {', 1)

# Reduce fixed RAM use and disable expensive optional subsystems for the classic 520KB ESP32.
g = OUT / 'globals.h'
gt = g.read_text(encoding='utf-8')
gt = re.sub(r'#define MAX_BLOCK_CHANGES\s+\d+', '#define MAX_BLOCK_CHANGES 1200', gt)
for macro in ['DO_FLUID_FLOW', 'ALLOW_CHESTS', 'ENABLE_PICKUP_ANIMATION', 'DEV_LOG_LENGTH_DISCREPANCY']:
    gt = gt.replace('#define ' + macro, '// #define ' + macro)
g.write_text(gt, encoding='utf-8')

# Let BareIron yield frequently so Arduino Wi-Fi remains responsive.
g = OUT / 'globals.c'
gt = g.read_text(encoding='utf-8').replace('#define TASK_YIELD_INTERVAL 1000 * 1000', '#define TASK_YIELD_INTERVAL 1000')
g.write_text(gt, encoding='utf-8')

# Replace terrain functions with deterministic multi-scale integer noise.
w = OUT / 'worldgen.c'
wt = w.read_text(encoding='utf-8')
helper = r'''
static int bi_floor_div(int x,int d){return x>=0?x/d:-(((-x)+d-1)/d);} static int bi_mod(int x,int d){int r=x%d;return r<0?r+d:r;}
static uint32_t bi_hash(int x,int z,uint32_t s,uint32_t k){uint32_t h=s^k^(uint32_t)x*374761393u^(uint32_t)z*668265263u;h^=h>>13;h*=1274126177u;h^=h>>16;return h;}
static int bi_smooth(int t,int n){return (int)(((int64_t)t*t*(3*n-2*t))/((int64_t)n*n));}
static int bi_noise(int x,int z,int n,uint32_t s,uint32_t k){int gx=bi_floor_div(x,n),gz=bi_floor_div(z,n),fx=bi_smooth(bi_mod(x,n),n),fz=bi_smooth(bi_mod(z,n),n);int a=bi_hash(gx,gz,s,k)&255,b=bi_hash(gx+1,gz,s,k)&255,c=bi_hash(gx,gz+1,s,k)&255,d=bi_hash(gx+1,gz+1,s,k)&255;int ab=a+((b-a)*fx)/n,cd=c+((d-c)*fx)/n;return ab+((cd-ab)*fz)/n;}
static int bi_fbm(int x,int z,uint32_t s,uint32_t k){return ((bi_noise(x,z,256,s,k)-128)*4+(bi_noise(x,z,128,s,k+1)-128)*2+(bi_noise(x,z,64,s,k+2)-128)+(bi_noise(x,z,32,s,k+3)-128))/8;}
static int bi_height(int x,int z,uint8_t b){int c=bi_fbm(x,z,world_seed,0xC1),e=bi_fbm(x+400,z-700,world_seed,0xC2),p=bi_fbm(x-1000,z+900,world_seed,0xC3);int h=64+c/2+e/3+(p>18?(p-18)*2:0);if(b==W_desert)h+=3;if(b==W_mangrove_swamp)h-=5;if(b==W_snowy_plains)h+=4;if(b==W_beach)h=61+c/6;if(h<38)h=38;if(h>118)h=118;return h;}
'''
if 'static int bi_floor_div' not in wt:
    wt = wt.replace('uint8_t getChunkBiome', helper + '\nuint8_t getChunkBiome', 1)

def repl(src, sig, body):
    p=src.find(sig)
    if p<0: raise SystemExit('missing '+sig)
    b=src.find('{',p); d=0
    for i in range(b,len(src)):
        if src[i]=='{': d+=1
        elif src[i]=='}':
            d-=1
            if d==0: return src[:p]+body+src[i+1:]
    raise SystemExit('bad braces '+sig)
wt = repl(wt,'uint8_t getChunkBiome (short x, short z)', '''uint8_t getChunkBiome (short x, short z){int t=bi_fbm(x+800,z-500,world_seed,0xB1),m=bi_fbm(x-1300,z+1700,world_seed,0xB2),c=bi_fbm(x,z,world_seed,0xB3);if(c<-55)return W_beach;if(t<-42)return W_snowy_plains;if(t>52&&m<-10)return W_desert;if(m>42)return W_mangrove_swamp;return W_plains;}''')
wt = repl(wt,'uint8_t getHeightAtFromHash (int rx, int rz, int _x, int _z, uint32_t chunk_hash, uint8_t biome)', '''uint8_t getHeightAtFromHash (int rx,int rz,int _x,int _z,uint32_t chunk_hash,uint8_t biome){(void)chunk_hash;uint8_t a=bi_height(_x,_z,biome),b=bi_height(_x+1,_z,getChunkBiome(_x+1,_z)),c=bi_height(_x,_z+1,getChunkBiome(_x,_z+1)),d=bi_height(_x+1,_z+1,getChunkBiome(_x+1,_z+1));return interpolate(a,b,c,d,rx,rz);}''')
w.write_text(wt, encoding='utf-8')

# Copy panel implementation created in repo.
shutil.copy2(ROOT / 'arduino' / 'control_panel.cpp', OUT / 'control_panel.cpp')
print('prepared', OUT)
