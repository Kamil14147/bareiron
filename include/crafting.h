#ifndef CRAFTING_H
#define CRAFTING_H

#ifdef __cplusplus
extern "C" {
#endif


#include "globals.h"

void getCraftingOutput (PlayerData *player, uint8_t *count, uint16_t *item);
void getSmeltingOutput (PlayerData *player);


#ifdef __cplusplus
}
#endif

#endif
