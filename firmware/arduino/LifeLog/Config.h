#pragma once

// Board: Seeed Studio XIAO ESP32S3 Sense
// Change only this file to support another ESP32-S3 board.

// ---- Wi-Fi ----
// A list of networks the device knows. On boot it scans, picks the strongest
// one that is visible, and fails over to the next when the current one drops
// (for example: home Wi-Fi, then a mobile hotspot when you step outside).
// Order in this list does not matter - signal strength decides.
struct WifiNetworkConfig {
    const char* ssid;
    const char* password;
    // The server to reach while on this network. Leave it empty to use
    // DEFAULT_SERVER_IP. Your laptop usually has a different IP on the
    // hotspot than on your home Wi-Fi, so this is per network.
    const char* serverIp;
    // Lower wins. Networks with the same priority are ordered by signal
    // strength. Use 0 for all of them and let the signal decide, or give
    // your favourite network a lower number to always prefer it.
    int priority;
};
static const WifiNetworkConfig WIFI_NETWORKS[] = {
    { "DESKTOP-7SC2HDQ 7223", "123456789", "", 0 },   // mobile hotspot
    { "Leo",       "aaaaaaaa", "", 0 },
    {"vivo T2 5G", "aaaaaaaa", "" , 0},// home network
};
#define WIFI_NETWORK_COUNT (sizeof(WIFI_NETWORKS) / sizeof(WIFI_NETWORKS[0]))

// Used when a network does not name its own server.
#define DEFAULT_SERVER_IP "192.168.137.1"

// How long to wait for one network before moving on to the next.
#define WIFI_CONNECT_TIMEOUT_MS 12000
// How often to look for networks that were not visible before.
#define WIFI_SCAN_INTERVAL_MS 15000

// ---- Server ----
#define SERVER_IP "192.168.137.1"
#define SERVER_PORT 8000
#define WS_ENDPOINT "/audio"

// ---- Audio ----
#define SAMPLE_RATE 16000
#define CHANNELS 1
#define BIT_DEPTH 16
#define CHUNK_SIZE 2048
#define BUFFER_SIZE (CHUNK_SIZE * 8)

// ---- Device ----
#define DEVICE_ID "00000000-0000-0000-0000-000000000001"
#define DEVICE_NAME "xiao-esp32s3-sense"
#define FIRMWARE_VERSION "1.0.0"
#define HARDWARE_REVISION "seeed-xiao-esp32s3-sense"
#define PROTOCOL_VERSION 1

// ---- Microphone (PDM) ----
#define MIC_CLK_PIN 42
#define MIC_DATA_PIN 41

// ---- Timing ----
#define HEARTBEAT_INTERVAL_MS 5000
#define WIFI_RECONNECT_DELAY_MS 3000