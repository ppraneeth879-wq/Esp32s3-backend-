#pragma once
#include <Arduino.h>

enum AudioState { ST_IDLE, ST_CONNECTING, ST_CONNECTED, ST_RECORDING, ST_TRANSCRIBING, ST_FINISHING, ST_CLOSED };

class StateMachine {
public:
    AudioState state = ST_IDLE;
    bool transition(AudioState next);
    const char* name();
};