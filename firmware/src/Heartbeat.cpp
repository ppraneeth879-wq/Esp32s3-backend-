#include "Heartbeat.h"
#include "Config.h"
#include <ArduinoJson.h>

static uint32_t lastSend = 0;

void Heartbeat::begin() { lastSend = millis(); }

bool Heartbeat::shouldSend() {
    if (millis() - lastSend >= HEARTBEAT_INTERVAL_MS) {
        lastSend = millis();
        return true;
    }
    return false;
}

String Heartbeat::build(uint32_t packetsSent, size_t queueUsage) {
    JsonDocument doc;
    doc["type"] = "heartbeat";
    doc["device_id"] = DEVICE_ID;
    doc["uptime_ms"] = millis();
    doc["rssi"] = 0;
    doc["free_heap"] = ESP.getFreeHeap();
    doc["packets_sent"] = packetsSent;
    doc["queue_usage"] = (float)queueUsage / BUFFER_SIZE;
    doc["firmware_version"] = FIRMWARE_VERSION;
    String out;
    serializeJson(doc, out);
    return out;
}