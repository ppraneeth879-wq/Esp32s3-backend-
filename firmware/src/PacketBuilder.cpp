#include "PacketBuilder.h"
#include "Config.h"
#include <string.h>

static void writeU32LE(uint8_t* p, uint32_t v){ p[0]=v&0xff; p[1]=(v>>8)&0xff; p[2]=(v>>16)&0xff; p[3]=(v>>24)&0xff; }
static void writeU64LE(uint8_t* p, uint64_t v){ for(int i=0;i<8;i++){ p[i]=(v>>(i*8))&0xff; } }

std::vector<uint8_t> PacketBuilder::build(uint8_t packetType, const char* deviceId,
                                          uint32_t sequence, uint64_t timestamp,
                                          const uint8_t* payload, size_t payloadLen) {
    std::vector<uint8_t> out;
    out.resize(40 + payloadLen);
    // magic LLV1
    out[0]='L'; out[1]='L'; out[2]='V'; out[3]='1';
    out[4]=PROTOCOL_VERSION;
    out[5]=packetType;
    out[6]=0; out[7]=0;
    // device id: parse UUID string into 16 bytes (best-effort; store raw bytes of string hash fallback)
    // For simplicity, copy first 16 chars
    memset(&out[8], 0, 16);
    size_t dl = strlen(deviceId);
    for (size_t i=0;i<16 && i<dl;i++) out[8+i] = (uint8_t)deviceId[i];
    writeU32LE(&out[24], sequence);
    writeU64LE(&out[28], timestamp);
    writeU32LE(&out[36], (uint32_t)payloadLen);
    if (payload && payloadLen) memcpy(&out[40], payload, payloadLen);
    return out;
}
