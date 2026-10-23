#pragma once
#include <Arduino.h>
#include "Config.h"

class WifiManager {
public:
    void begin();
    bool isConnected();
    void reconnect();
    int rssi();

    // The SSID the device is currently on, or "" when it is not connected.
    const char* currentSsid() const { return _ssid; }
    // The server to talk to while on the current network. Falls back to
    // DEFAULT_SERVER_IP when the network does not name one.
    const char* currentServerIp() const { return _serverIp[0] ? _serverIp : DEFAULT_SERVER_IP; }
    // How many of the known networks were visible at the last scan.
    int visibleNetworks() const { return _visibleCount; }
    // The name of the network currently being tried, or "" when idle.
    const char* tryingSsid() const { return _tryingSsid; }

private:
    void scan();
    void tryNext();
    void connectTo(int index);
    void rememberServerFor(const char* ssid);

    char _ssid[33] = {0};
    char _tryingSsid[33] = {0};
    char _serverIp[16] = {0};
    int _visibleCount = 0;
};
