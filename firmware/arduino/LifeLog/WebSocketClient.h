#pragma once
#include <Arduino.h>
#include "Config.h"

class WebSocketClient {
public:
    void begin(const char* host, int port, const char* path);
    bool connect();
    bool connected();
    void loop();
    bool sendBinary(const uint8_t* data, size_t len);
    void disconnect();
};