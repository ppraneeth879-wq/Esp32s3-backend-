#include "WifiManager.h"
#include <WiFi.h>
#include "Logger.h"

void WifiManager::begin() {
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

bool WifiManager::isConnected() { return WiFi.status() == WL_CONNECTED; }

void WifiManager::reconnect() {
    wl_status_t st = WiFi.status();
    if (st == WL_CONNECTED) return;
    if (st == WL_DISCONNECTED || st == WL_CONNECT_FAILED || st == WL_NO_SSID_AVAIL) {
        WiFi.disconnect(true);
        delay(100);
        WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    }
    // else: still connecting, do nothing
}

int WifiManager::rssi() { return WiFi.RSSI(); }