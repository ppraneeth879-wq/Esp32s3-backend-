#include <Arduino.h>
#include <WiFi.h>
#include "Config.h"
#include "Logger.h"
#include "WifiManager.h"
#include "MicManager.h"
#include "RingBuffer.h"
#include "PacketBuilder.h"
#include "WebSocketClient.h"
#include "Heartbeat.h"
#include "StateMachine.h"

WifiManager wifi;
MicManager mic;
RingBuffer ring(BUFFER_SIZE);
WebSocketClient wsClient;
Heartbeat heartbeat;
StateMachine sm;

TaskHandle_t micTaskHandle = NULL;
TaskHandle_t netTaskHandle = NULL;

uint32_t seq = 0;

void micTask(void* param) {
    uint8_t chunk[CHUNK_SIZE];
    while (true) {
        size_t n = mic.read(chunk, CHUNK_SIZE);
        if (n > 0) {
            ring.write(chunk, n);
        }
    }
}

void netTask(void* param) {
    uint8_t chunk[CHUNK_SIZE];
    bool wasConnected = false;
    bool wsStarted = false;
    bool wsConnected = false;
    while (true) {
        if (!wifi.isConnected()) {
            wasConnected = false;
            wsStarted = false;
            wsConnected = false;
            wifi.reconnect();
            vTaskDelay(pdMS_TO_TICKS(WIFI_RECONNECT_DELAY_MS));
            continue;
        }
        if (!wasConnected) {
            wasConnected = true;
            Serial.printf("WiFi connected: %s (SSID %s)\n",
                          WiFi.localIP().toString().c_str(),
                          wifi.currentSsid());
        }
        if (!wsStarted) {
            // The server address depends on which network we are on.
            wsClient.begin(wifi.currentServerIp(), SERVER_PORT, WS_ENDPOINT);
            wsStarted = true;
        }
        wsClient.loop();
        if (!wsClient.connected()) {
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
        if (!wsConnected) {
            wsConnected = true;
            Serial.println("WebSocket connected");
        }
        if (ring.available() >= CHUNK_SIZE) {
            size_t r = ring.read(chunk, CHUNK_SIZE);
            if (r > 0) {
                auto pkt = PacketBuilder::build(0x01, DEVICE_ID, seq++, millis(), chunk, r);
                wsClient.sendBinary(pkt.data(), pkt.size());
            }
        }
        if (heartbeat.shouldSend()) {
            auto pkt = PacketBuilder::build(0x02, DEVICE_ID, seq++, millis(), nullptr, 0);
            wsClient.sendBinary(pkt.data(), pkt.size());
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

void setup() {
    Serial.begin(115200);
    wifi.begin();
    mic.begin();
    heartbeat.begin();
    sm.transition(ST_CONNECTING);
    xTaskCreatePinnedToCore(micTask, "mic", 8192, NULL, 2, &micTaskHandle, 1);
    xTaskCreatePinnedToCore(netTask, "net", 8192, NULL, 1, &netTaskHandle, 0);
}

void loop() {
    vTaskDelay(pdMS_TO_TICKS(1000));
}