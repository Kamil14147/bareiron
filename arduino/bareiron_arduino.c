#include "bareiron_arduino.h"
#include "../include/globals.h"
#include "../include/procedures.h"
#include <string.h>
#ifdef ESP_PLATFORM
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#endif

volatile uint8_t bareiron_should_run = 0;
volatile uint32_t bareiron_active_us = 0;
int bareiron_clients[MAX_PLAYERS];

extern int bareiron_server_main(void);

#ifdef ESP_PLATFORM
static TaskHandle_t bareiron_task_handle = NULL;
static void bareiron_task(void *arg) {
  (void)arg;
  bareiron_server_main();
  bareiron_task_handle = NULL;
  bareiron_should_run = 0;
  vTaskDelete(NULL);
}
#endif

void bareiron_start(void) {
  if (bareiron_should_run) return;
  bareiron_should_run = 1;
#ifdef ESP_PLATFORM
  xTaskCreatePinnedToCore(bareiron_task, "bareiron", 8192, NULL, 5, &bareiron_task_handle, 1);
#else
  bareiron_server_main();
#endif
}

void bareiron_stop(void) {
  bareiron_should_run = 0;
}

void bareiron_restart(void) {
  bareiron_stop();
#ifdef ESP_PLATFORM
  vTaskDelay(pdMS_TO_TICKS(150));
#endif
  bareiron_start();
}

int bareiron_running(void) { return bareiron_should_run ? 1 : 0; }
int bareiron_player_count(void) { return (int)client_count; }
int bareiron_max_players(void) { return MAX_PLAYERS; }

int bareiron_player_get(int index, char *name, int cap, short *x, uint8_t *y, short *z) {
  if (index < 0 || index >= MAX_PLAYERS) return 0;
  if (player_data[index].client_fd == -1) return 0;
  if (player_data[index].flags & 0x20) return 0;
  if (name && cap > 0) {
    strncpy(name, player_data[index].name, (size_t)cap - 1);
    name[cap - 1] = 0;
  }
  if (x) *x = player_data[index].x;
  if (y) *y = player_data[index].y;
  if (z) *z = player_data[index].z;
  return 1;
}

int bareiron_kick(int index) {
  if (index < 0 || index >= MAX_PLAYERS || bareiron_clients[index] == -1) return 0;
  disconnectClient(&bareiron_clients[index], 9);
  return 1;
}

uint32_t bareiron_cpu_load_percent(void) {
  uint32_t us = bareiron_active_us;
  bareiron_active_us = 0;
  if (us > 1000000UL) us = 1000000UL;
  return us / 10000UL;
}
