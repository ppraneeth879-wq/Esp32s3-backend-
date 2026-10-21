#pragma once
#include <Arduino.h>
#include "Config.h"

class MicManager {
public:
    bool begin();
    size_t read(uint8_t* out, size_t len);
};