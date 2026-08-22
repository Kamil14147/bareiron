#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
void bareiron_start(void);
void bareiron_stop(void);
void bareiron_restart(void);
int bareiron_running(void);
int bareiron_player_count(void);
int bareiron_max_players(void);
int bareiron_player_get(int index, char *name, int cap, short *x, uint8_t *y, short *z);
int bareiron_kick(int index);
uint32_t bareiron_cpu_load_percent(void);
#ifdef __cplusplus
}
#endif
