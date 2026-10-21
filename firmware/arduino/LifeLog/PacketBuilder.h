#pragma once
#include <Arduino.h>
#include <vector>

class PacketBuilder {
public:
    static std::vector<uint8_t> build(uint8_t packetType, const char* deviceId,
                                      uint32_t sequence, uint64_t timestamp,
                                      const uint8_t* payload, size_t payloadLen);
};