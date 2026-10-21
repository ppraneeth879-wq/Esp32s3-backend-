#pragma once
#include <Arduino.h>

class Heartbeat {
public:
    void begin();
    bool shouldSend();
    // returns JSON string
    String build(uint32_t packetsSent, size_t queueUsage);
};