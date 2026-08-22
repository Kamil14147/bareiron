from pathlib import Path
import shutil, tempfile, zipfile, re
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'arduino'/'BareIronRework'; OUT.mkdir(parents=True,exist_ok=True)
def find(p):
    if (p/'include').is_dir() and (p/'src').is_dir(): return p
    for q in p.rglob('*'):
        if q.is_dir() and (q/'include').is_dir() and (q/'src').is_dir(): return q
src=find(ROOT)
with tempfile.TemporaryDirectory() as td:
    td=Path(td); z=ROOT/'bareiron.zip'
    if z.exists():
        try:
            with zipfile.ZipFile(z) as f: f.extractall(td)
            src=find(td) or src
        except zipfile.BadZipFile: pass
if not src: raise SystemExit('BareIron sources not found')
for p in (src/'include').glob('*.h'): shutil.copy2(p,OUT/p.name)
for p in (src/'src').glob('*.c'): shutil.copy2(p,OUT/p.name)
# Memory limits and Arduino-friendly timing.
g=OUT/'globals.h'; t=g.read_text(); t=re.sub(r'(#define MAX_PLAYERS )\\d+',r'\\g<1>4',t); t=re.sub(r'(#define MAX_BLOCK_CHANGES )\\d+',r'\\g<1>4000',t); g.write_text(t)
g=OUT/'globals.c'; t=g.read_text().replace('#define TASK_YIELD_INTERVAL 1000 * 1000','#define TASK_YIELD_INTERVAL 1000'); g.write_text(t)
# Rename the POSIX main so it cannot collide with Arduino core main().
m=OUT/'main.c'; t=m.read_text(); t=t.replace('int main () {','int bareiron_server_main () {',1); t=t.replace('main();','bareiron_server_main();',1); t=t.replace('int clients[MAX_PLAYERS], client_index = 0;','int client_index = 0;',1); t=t.replace('clients[i] = -1;','bareiron_clients[i] = -1;',1); start=t.find('int client_index = 0;'); a=t[:start]; b=t[start:]; b=b.replace('clients[','bareiron_clients['); t=a+b; t=t.replace('while (true) {','while (bareiron_should_run) {',1); t=t.replace('    task_yield();\n','    task_yield();\n    int64_t bareiron_loop_start = get_program_time();\n',1); t=t.replace('\n  close(server_fd);','\n  for (int i=0;i<MAX_PLAYERS;i++){if(bareiron_clients[i]!=-1)close(bareiron_clients[i]);bareiron_clients[i]=-1;}\n  close(server_fd);',1); t=t.replace('#include "globals.h"','#include "globals.h"\n#include "bareiron_arduino.h"\nextern volatile uint8_t bareiron_should_run;\nextern volatile uint32_t bareiron_active_us;\nextern int bareiron_clients[MAX_PLAYERS;',1); t=t.replace('extern int bareiron_clients[MAX_PLAYERS;','extern int bareiron_clients[MAX_PLAYERS];',1); m.write_text(t)
# Spatial multi-scale height fields replacing the old simple corner-height lookup.
w=OUT/'worldgen.c'; t=w.read_text(); helper='''static uint32_t rw_hash(int x,int z,uint32_t s,uint32_t k){uint32_t h=s^k^(uint32_t)x*374761393u^(uint32_t)z*668265263u;h^=h>>13;h*=1274126177u;h^=h>>16;return h;}\nstatic int rw_s(int t,int n){return (int)(((int64_t)t*t*(3*n-2*t))/((int64_t)n*n));}\nstatic int rw_n(int x,int z,int n,uint32_t s,uint32_t k){int gx=div_floor(x,n),gz=div_floor(z,n),fx=mod_abs(x,n),fz=mod_abs(z,n),sx=rw_s(fx,n),sz=rw_s(fz,n);int a=rw_hash(gx,gz,s,k)&255,b=rw_hash(gx+1,gz,s,k)&255,c=rw_hash(gx,gz+1,s,k)&255,d=rw_hash(gx+1,gz+1,s,k)&255;int ab=a+((b-a)*sx)/n,cd=c+((d-c)*sx)/n;return ab+((cd-ab)*sz)/n;}\nstatic int rw_f(int x,int z,uint32_t s,uint32_t k){return ((rw_n(x,z,256,s,k)-128)*4+(rw_n(x,z,128,s,k+1)-128)*2+(rw_n(x,z,64,s,k+2)-128)+(rw_n(x,z,32,s,k+3)-128))/8;}\nstatic int rw_h(int x,int z,uint8_t b){int c=rw_f(x,z,world_seed,0xC1),e=rw_f(x+400,z-700,world_seed,0xC2),p=rw_f(x-1000,z+900,world_seed,0xC3);int h=64+c/2+e/3+(p>18?(p-18)*2:0);if(b==W_desert)h+=3;if(b==W_mangrove_swamp)h-=5;if(b==W_snowy_plains)h+=4;if(b==W_beach)h=61+c/6;if(h<38)h=38;if(h>118)h=118;return h;}\n'''
if 'static uint32_t rw_hash(' not in t:t=t.replace('uint8_t getChunkBiome',helper+'\nuint8_t getChunkBiome',1)
def rep(s,sig,b):
 p=s.find(sig); q=s.find('{',p); d=0
 for i in range(q,len(s)):
  if s[i]=='{':d+=1
  elif s[i]=='}':
   d-=1
   if d==0:return s[:p]+b+s[i+1:]
 raise SystemExit(sig)
t=rep(t,'uint8_t getChunkBiome (short x, short z)','''uint8_t getChunkBiome (short x, short z){int t=rw_f(x+800,z-500,world_seed,0xB1),m=rw_f(x-1300,z+1700,world_seed,0xB2),c=rw_f(x,z,world_seed,0xB3);if(c<-55)return W_beach;if(t<-42)return W_snowy_plains;if(t>52&&m<-10)return W_desert;if(m>42)return W_mangrove_swamp;return W_plains;}''')
t=rep(t,'uint8_t getHeightAtFromHash (int rx, int rz, int _x, int _z, uint32_t chunk_hash, uint8_t biome)','''uint8_t getHeightAtFromHash (int rx,int rz,int _x,int _z,uint32_t chunk_hash,uint8_t biome){if(rx==0&&rz==0){int h=rw_h(_x,_z,biome);if(h>67)return h-1;}return interpolate((uint8_t)rw_h(_x,_z,biome),(uint8_t)rw_h(_x+1,_z,getChunkBiome(_x+1,_z)),(uint8_t)rw_h(_x,_z+1,getChunkBiome(_x,_z+1)),(uint8_t)rw_h(_x+1,_z+1,getChunkBiome(_x+1,_z+1)),rx,rz);}''')
w.write_text(t)
# Copy Arduino-specific files back last so they are never overwritten.
for name in ['BareIronRework.ino','bareiron_arduino.h','bareiron_arduino.c']: shutil.copy2(ROOT/'arduino'/name,OUT/name)
print('ready')
