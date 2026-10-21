#include "WebSocketClient.h"
#include <WebSocketsClient.h>

static WebSocketsClient ws;

void WebSocketClient::begin(const char* host, int port, const char* path) {
    ws.begin(host, port, path);
    ws.setReconnectInterval(3000);
    ws.onEvent([](WStype_t type, uint8_t* payload, size_t length) {
        if (type == WStype_ERROR) {
            Serial.printf("WS error: %s\n", (char*)payload);
        }
        if (type == WStype_DISCONNECTED) {
            Serial.println("WS disconnected");
        }
    });
}

bool WebSocketClient::connect() { return ws.isConnected(); }
bool WebSocketClient::connected() { return ws.isConnected(); }
void WebSocketClient::loop() { ws.loop(); }
bool WebSocketClient::sendBinary(const uint8_t* data, size_t len) {
    if (!ws.isConnected()) return false;
    return ws.sendBIN(data, len);
}
void WebSocketClient::disconnect() { ws.disconnect(); }