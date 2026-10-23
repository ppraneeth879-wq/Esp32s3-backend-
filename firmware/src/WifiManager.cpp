#include "WifiManager.h"
#include <WiFi.h>
#include <string.h>
#include "Logger.h"

namespace {

// Indices into WIFI_NETWORKS, ordered by priority and then signal strength.
int g_candidates[WIFI_NETWORK_COUNT];
int g_candidateRssi[WIFI_NETWORK_COUNT];
int g_candidateCount = 0;
int g_candidatePos = 0;

uint32_t g_lastScan = 0;
uint32_t g_connectStart = 0;
bool g_connecting = false;
bool g_haveScanned = false;

// Insert a candidate, keeping the list ordered by priority (lowest first)
// and then by signal strength (strongest first).
void insertCandidate(int index, int rssi) {
    int priority = WIFI_NETWORKS[index].priority;
    for (int i = 0; i < g_candidateCount; i++) {
        int other = g_candidates[i];
        bool comesFirst = (priority < WIFI_NETWORKS[other].priority) ||
                          (priority == WIFI_NETWORKS[other].priority && rssi > g_candidateRssi[i]);
        if (comesFirst) {
            for (int j = g_candidateCount; j > i; j--) {
                g_candidates[j] = g_candidates[j - 1];
                g_candidateRssi[j] = g_candidateRssi[j - 1];
            }
            g_candidates[i] = index;
            g_candidateRssi[i] = rssi;
            g_candidateCount++;
            return;
        }
    }
    g_candidates[g_candidateCount] = index;
    g_candidateRssi[g_candidateCount] = rssi;
    g_candidateCount++;
}

}  // namespace

void WifiManager::begin() {
    WiFi.mode(WIFI_STA);
    // We drive reconnection ourselves so we can fail over to a different
    // network. The library's auto-reconnect would keep retrying the same,
    // now out-of-range, access point forever.
    WiFi.setAutoReconnect(false);
    WiFi.setSleep(false);
    Logger::info("wifi", "starting, looking for a known network");
    reconnect();
}

bool WifiManager::isConnected() {
    return WiFi.status() == WL_CONNECTED;
}

int WifiManager::rssi() {
    return WiFi.RSSI();
}

void WifiManager::scan() {
    g_lastScan = millis();
    g_candidateCount = 0;
    g_candidatePos = 0;
    g_haveScanned = true;
    int found = WiFi.scanNetworks(false, true);
    for (int i = 0; i < found; i++) {
        const char* ssid = WiFi.SSID(i).c_str();
        int rssi = WiFi.RSSI(i);
        for (size_t k = 0; k < WIFI_NETWORK_COUNT; k++) {
            if (strcmp(ssid, WIFI_NETWORKS[k].ssid) == 0) {
                insertCandidate((int)k, rssi);
                break;
            }
        }
    }
    WiFi.scanDelete();

    _visibleCount = g_candidateCount;
    if (g_candidateCount == 0) {
        Logger::warn("wifi", "no known network in range");
    } else {
        char msg[96];
        snprintf(msg, sizeof(msg), "%d known network(s) in range, best is %s",
                 g_candidateCount, WIFI_NETWORKS[g_candidates[0]].ssid);
        Logger::info("wifi", msg);
    }
}

void WifiManager::connectTo(int index) {
    if (index < 0 || index >= (int)WIFI_NETWORK_COUNT) return;
    strncpy(_tryingSsid, WIFI_NETWORKS[index].ssid, sizeof(_tryingSsid) - 1);
    _tryingSsid[sizeof(_tryingSsid) - 1] = 0;

    char msg[96];
    snprintf(msg, sizeof(msg), "connecting to %s", WIFI_NETWORKS[index].ssid);
    Logger::info("wifi", msg);

    WiFi.disconnect(false);
    delay(50);
    WiFi.begin(WIFI_NETWORKS[index].ssid, WIFI_NETWORKS[index].password);
    g_connectStart = millis();
    g_connecting = true;
}

void WifiManager::tryNext() {
    if (g_candidateCount == 0) {
        // Nothing known is visible. Scan again and start over.
        g_candidatePos = 0;
        return;
    }
    if (g_candidatePos >= g_candidateCount) {
        // We have been through every visible network. Go back to the best
        // one and try again - it may have come back.
        g_candidatePos = 0;
    }
    connectTo(g_candidates[g_candidatePos]);
    g_candidatePos++;
}

void WifiManager::reconnect() {
    if (isConnected()) {
        if (strcmp(_ssid, WiFi.SSID().c_str()) != 0) {
            strncpy(_ssid, WiFi.SSID().c_str(), sizeof(_ssid) - 1);
            _ssid[sizeof(_ssid) - 1] = 0;
            rememberServerFor(_ssid);
            char msg[96];
            snprintf(msg, sizeof(msg), "connected to %s, server %s", _ssid, currentServerIp());
            Logger::info("wifi", msg);
        }
        return;
    }

    uint32_t now = millis();

    if (g_connecting) {
        if (now - g_connectStart >= WIFI_CONNECT_TIMEOUT_MS) {
            // This attempt timed out. Move on to the next one.
            g_connecting = false;
            Logger::warn("wifi", "connection timed out, trying another network");
            WiFi.disconnect(false);
            tryNext();
        }
        return;
    }

    // Not connected and not attempting anything. Use the cached scan result
    // if it is fresh enough, otherwise look around again.
    bool scanIsStale = !g_haveScanned || (now - g_lastScan >= WIFI_SCAN_INTERVAL_MS);
    if (scanIsStale) {
        scan();
    }
    if (g_candidateCount > 0) {
        tryNext();
    }
}

void WifiManager::rememberServerFor(const char* ssid) {
    _serverIp[0] = 0;
    for (size_t k = 0; k < WIFI_NETWORK_COUNT; k++) {
        if (strcmp(ssid, WIFI_NETWORKS[k].ssid) == 0) {
            if (WIFI_NETWORKS[k].serverIp) {
                strncpy(_serverIp, WIFI_NETWORKS[k].serverIp, sizeof(_serverIp) - 1);
                _serverIp[sizeof(_serverIp) - 1] = 0;
            }
            return;
        }
    }
}
