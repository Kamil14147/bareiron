#!/usr/bin/env python3
from pathlib import Path
import shutil, tempfile, zipfile

ROOT=Path(__file__).resolve().parents[1]
SKETCH=ROOT/'arduino'/'BareIronRework'
SKETCH.mkdir(parents=True,exist_ok=True)

def tree_with_sources(p):
    if (p/'include').is_dir() and (p/'src').is_dir(): return p
    for q in p.rglob('*'):
        if q.is_dir() and (q/'include').is_dir() and (q/'src').is_dir(): return q
    return None

src=tree_with_sources(ROOT)
with tempfile.TemporaryDirectory() as td:
    td=Path(td)
    z=ROOT/'bareiron.zip'
    if z.exists():
        try:
            with zipfile.ZipFile(z) as f: f.extractall(td)
            src=tree_with_sources(td) or src
        except zipfile.BadZipFile: pass
    if src is None: raise SystemExit('BareIron source tree not found')
    for p in (src/'include').glob('*.h'): shutil.copy2(p,SKETCH/p.name)
    for p in (src/'src').glob('*.c'): shutil.copy2(p,SKETCH/p.name)
    # Preserve our Arduino-specific wrapper files.

# Arduino C files must use local headers.
p=SKETCH/'bareiron_arduino.c'
t=p.read_text().replace('#include "../include/globals.h"','#include "globals.h"').replace('#include "../include/procedures.h"','#include "procedures.h"')
p.write_text(t)

# ESP32 memory budget. Keep the values already customized in the branch when they are lower.
g=SKETCH/'globals.h'; t=g.read_text()
def cap_define(t,name,maxv):
    import re
    m=re.search(r'#define\\s+'+name+r'\\s+(\\d+)',t)
    if not m: return t
    v=int(m.group(1))
    if v>maxv: t=t[:m.start(1)]+str(maxv)+t[m.end(1):]
    return t
t=cap_define(t,'MAX_PLAYERS',4)
t=cap_define(t,'MAX_BLOCK_CHANGES',4000)
g.write_text(t)

# Reduce the old 1-second yield interval to 1 ms.
gl=SKETCH/'globals.c'; t=gl.read_text().replace('#define TASK_YIELD_INTERVAL 1000 * 1000','#define TASK_YIELD_INTERVAL 1000'); gl.write_text(t)

m=SKETCH/'main.c'; t=m.read_text()
if 'bareiron_should_run' not in t:
    t=t.replace('#include "globals.h"','#include "globals.h"\n#include "bareiron_arduino.h"\nextern volatile uint8_t bareiron_should_run;\nextern volatile uint32_t bareiron_active_us;\nextern int bareiron_clients[MAX_PLAYERS];',1)
t=t.replace('int clients[MAX_PLAYERS], client_index = 0;\n  for (int i = 0; i < MAX_PLAYERS; i ++) {\n    clients[i] = -1;','int client_index = 0;\n  for (int i = 0; i < MAX_PLAYERS; i ++) {\n    bareiron_clients[i] = -1;',1)
# Only replace the client array tokens after the declaration.
pos=t.find('int client_index = 0;')
if pos>=0:
    a=t[:pos]; b=t[pos:]
    b=b.replace('clients[','bareiron_clients[')
    t=a+b
t=t.replace('while (true) {','while (bareiron_should_run) {',1)
# Add per-iteration CPU accounting after yield and before loop exits.
t=t.replace('    task_yield();\n','    task_yield();\n    int64_t bareiron_loop_start = get_program_time();\n',1)
t=t.replace('\n  }\n\n  close(server_fd);','\n    uint32_t loop_us = (uint32_t)(get_program_time() - bareiron_loop_start);\n    if (bareiron_active_us < 900000000UL) bareiron_active_us += loop_us;\n  }\n\n  for (int i = 0; i < MAX_PLAYERS; i ++) {\n    if (bareiron_clients[i] != -1) close(bareiron_clients[i]);\n    bareiron_clients[i] = -1;\n  }\n\n  close(server_fd);',1)
m.write_text(t)

# Replace height interpolation with spatial, deterministic multi-octave value noise.
w=SKETCH/'worldgen.c'; t=w.read_text()
helper='''static uint32_t rw_hash(int x,int z,uint32_t seed,uint32_t salt){uint32_t h=seed^salt^(uint32_t)x*374761393u^(uint32_t)z*668265263u;h^=h>>13;h*=1274126177u;h^=h>>16;return h;}\nstatic int rw_smooth(int t,int s){return (int)(((int64_t)t*t*(3*s-2*t))/((int64_t)s*s));}\nstatic int rw_noise(int x,int z,int s,uint32_t seed,uint32_t salt){int gx=div_floor(x,s),gz=div_floor(z,s),fx=mod_abs(x,s),fz=mod_abs(z,s),sx=rw_smooth(fx,s),sz=rw_smooth(fz,s);int a=rw_hash(gx,gz,seed,salt)&255,b=rw_hash(gx+1,gz,seed,salt)&255,c=rw_hash(gx,gz+1,seed,salt)&255,d=rw_hash(gx+1,gz+1,seed,salt)&255;int ab=a+((b-a)*sx)/s,cd=c+((d-c)*sx)/s;return ab+((cd-ab)*sz)/s;}\nstatic int rw_fbm(int x,int z,uint32_t seed,uint32_t salt){return ((rw_noise(x,z,256,seed,salt)-128)*4+(rw_noise(x,z,128,seed,salt+1)-128)*2+(rw_noise(x,z,64,seed,salt+2)-128)+(rw_noise(x,z,32,seed,salt+3)-128))/8;}\nstatic int rw_height(int cx,int cz,uint8_t biome){int c=rw_fbm(cx,cz,world_seed,0xC1);int e=rw_fbm(cx+400,cz-700,world_seed,0xC2);int p=rw_fbm(cx-1000,cz+900,world_seed,0xC3);int h=64+c/2+e/3+(p>18?(p-18)*2:0);if(biome==W_desert)h+=3;if(biome==W_mangrove_swamp)h-=5;if(biome==W_snowy_plains)h+=4;if(biome==W_beach)h=61+c/6;if(h<38)h=38;if(h>118)h=118;return h;}\n'''
if 'static uint32_t rw_hash(' not in t: t=t.replace('uint8_t getChunkBiome',helper+'\nuint8_t getChunkBiome',1)

def repl(src,sig,body):
    p=src.find(sig); b=src.find('{',p); d=0
    for i in range(b,len(src)):
        if src[i]=='{': d+=1
        elif src[i]=='}':
            d-=1
            if d==0:return src[:p]+body+src[i+1:]
    raise SystemExit('function not found: '+sig)

t=repl(t,'uint8_t getChunkBiome (short x, short z)','''uint8_t getChunkBiome (short x, short z) {\n  int temp=rw_fbm(x+800,z-500,world_seed,0xB1), moist=rw_fbm(x-1300,z+1700,world_seed,0xB2), cont=rw_fbm(x,z,world_seed,0xB3);\n  if(cont<-55)return W_beach; if(temp<-42)return W_snowy_plains; if(temp>52&&moist<-10)return W_desert; if(moist>42)return W_mangrove_swamp; return W_plains;\n}''')
old='''uint8_t getHeightAtFromHash (int rx, int rz, int _x, int _z, uint32_t chunk_hash, uint8_t biome) {'''
new='''uint8_t getHeightAtFromHash (int rx, int rz, int _x, int _z, uint32_t chunk_hash, uint8_t biome) {\n  if (rx==0 && rz==0) { int h=rw_height(_x,_z,biome); if(h>67)return h-1; }\n  return interpolate((uint8_t)rw_height(_x,_z,biome),(uint8_t)rw_height(_x+1,_z,getChunkBiome(_x+1,_z)),(uint8_t)rw_height(_x,_z+1,getChunkBiome(_x,_z+1)),(uint8_t)rw_height(_x+1,_z+1,getChunkBiome(_x+1,_z+1)),rx,rz);\n}'''
t=repl(t,old,new)
wa=SKETCH/'worldgen.c'; wa.write_text(t)
print('prepared',SKETCH)
