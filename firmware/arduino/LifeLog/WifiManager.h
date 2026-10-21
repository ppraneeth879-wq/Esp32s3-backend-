#pragma once
#include <Arduino.h>
#include "Config.h"

class WifiManager {
public:
    void begin();
    bool isConnected();
    void reconnect();
    int rssi();
};