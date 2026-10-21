#pragma once

// Board: Seeed Studio XIAO ESP32S3 Sense
// Change only this file to support another ESP32-S3 board.

// ---- Wi-Fi ----
#define WIFI_SSID "DESKTOP-7SC2HDQ 7223"
#define WIFI_PASSWORD "123456789"

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