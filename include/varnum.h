#ifndef H_VARNUM
#define H_VARNUM

#ifdef __cplusplus
extern "C" {
#endif


#include <stdint.h>

#define SEGMENT_BITS 0x7F
#define CONTINUE_BIT 0x80
#define VARNUM_ERROR 0xFFFFFFFF

int32_t readVarInt (int client_fd);
int sizeVarInt (uint32_t value);
void writeVarInt (int client_fd, uint32_t value);


#ifdef __cplusplus
}
#endif

#endif
