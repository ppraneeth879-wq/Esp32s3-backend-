#include "StateMachine.h"

bool StateMachine::transition(AudioState next) {
    // Basic allowed transitions
    if (state == ST_IDLE && next == ST_CONNECTING) { state = next; return true; }
    if (state == ST_CONNECTING && (next == ST_CONNECTED || next == ST_CLOSED)) { state = next; return true; }
    if (state == ST_CONNECTED && (next == ST_RECORDING || next == ST_CLOSED)) { state = next; return true; }
    if (state == ST_RECORDING && (next == ST_TRANSCRIBING || next == ST_FINISHING || next == ST_CLOSED)) { state = next; return true; }
    if (state == ST_TRANSCRIBING && (next == ST_RECORDING || next == ST_FINISHING || next == ST_CLOSED)) { state = next; return true; }
    if (state == ST_FINISHING && next == ST_CLOSED) { state = next; return true; }
    if (state == ST_CLOSED && next == ST_IDLE) { state = next; return true; }
    return false;
}

const char* StateMachine::name() {
    switch (state) {
        case ST_IDLE: return "IDLE";
        case ST_CONNECTING: return "CONNECTING";
        case ST_CONNECTED: return "CONNECTED";
        case ST_RECORDING: return "RECORDING";
        case ST_TRANSCRIBING: return "TRANSCRIBING";
        case ST_FINISHING: return "FINISHING";
        case ST_CLOSED: return "CLOSED";
    }
    return "UNKNOWN";
}