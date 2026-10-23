# ESP32 Firmware — Networking

## Multiple known networks

The device keeps a list of networks it knows about. On boot it scans, picks
the best one that is visible, and fails over to the next when the current one
drops. You never touch the device when you leave the house.

Edit **only** `firmware/include/Config.h` (and the copy in
`firmware/arduino/LifeLog/Config.h` if you build from the Arduino IDE):

```c
struct WifiNetworkConfig {
    const char* ssid;
    const char* password;
    const char* serverIp;   // "" = use DEFAULT_SERVER_IP
    int priority;           // lower wins
};

static const WifiNetworkConfig WIFI_NETWORKS[] = {
    { "DESKTOP-7SC2HDQ 7223", "123456789", "", 0 },   // mobile hotspot
    { "MyHomeWiFi",           "homepass",   "", 0 },   // home network
};
```

### How the choice is made

1. The ESP32 scans for networks.
2. Every SSID that matches one in the list becomes a **candidate**.
3. Candidates are ordered by `priority` (lowest first), then by **signal
   strength** (strongest first).
4. It tries them in that order, 12 s each. If one times out, it moves to the
   next.
5. Every 15 s it re-scans, so a network that *appears* — your hotspot when
   you walk out of the house — is picked up without a reboot.
6. When the current network disappears, the device drops and works through the
   list again.

`WiFi.setAutoReconnect(false)` is deliberate: the library's own auto-reconnect
would keep hammering the access point that just went out of range instead of
looking for a better one.

### Priority

Leave `priority` at `0` for every network and **signal strength decides** —
which is usually what you want:

- At home, the home router is closer than the hotspot in your pocket.
- Outside, only the hotspot is in range.

Set a lower number on your favourite network if you want it to win even when
another one has a marginally stronger signal:

```c
{ "MyHomeWiFi", "homepass", "", 0 },   // always preferred
{ "Hotspot",   "hotpass", "", 5 },     // fallback
```

### The server address is per network

Your laptop normally has a **different IP** on the hotspot than on your home
Wi-Fi. That is why each network carries its own `serverIp`:

```c
{ "Hotspot",   "hotpass", "192.168.137.1", 0 }, // Windows ICS / mobile hotspot
{ "MyHomeWiFi","homepass","192.168.1.42",  0 }, // your laptop on the home router
```

Leave it empty to use `DEFAULT_SERVER_IP`. The firmware connects the
WebSocket to `wifi.currentServerIp()`, so it automatically follows the network
it is on.

**The server must be listening on `0.0.0.0`, not `127.0.0.1`.** `scripts\run_local.bat`
already does this. A server bound to localhost is unreachable from the device
even though it works on your own machine.

### Finding your server's IP

```powershell
ipconfig
```
Look for the address on the adapter the device is on:
- Windows mobile hotspot / ICS → `192.168.137.1`
- Home router → usually `192.168.1.x` or `192.168.0.x`

## What the serial log shows

```
[INFO] wifi: starting, looking for a known network
[INFO] wifi: 2 known network(s) in range, best is DESKTOP-7SC2HDQ 7223
[INFO] wifi: connecting to DESKTOP-7SC2HDQ 7223
[INFO] wifi: connected to DESKTOP-7SC2HDQ 7223, server 192.168.137.1
```

When the network drops:

```
[WARN] wifi: connection timed out, trying another network
[INFO] wifi: connecting to MyHomeWiFi
[INFO] wifi: connected to MyHomeWiFi, server 192.168.1.42
```

## Build and flash

```powershell
cd D:\ESP Backend\firmware
platformio run            # build
platformio run -t upload  # build and flash
```

`platformio run -t monitor` shows the serial log (115200 baud).

## Tuning

| Setting | Default | What it does |
|---|---|---|
| `WIFI_CONNECT_TIMEOUT_MS` | 12000 | How long to wait for one network before trying the next |
| `WIFI_SCAN_INTERVAL_MS` | 15000 | How often to look for networks that were not visible before |
| `WIFI_RECONNECT_DELAY_MS` | 3000 | How often the net task checks the Wi-Fi state |

Lower `WIFI_SCAN_INTERVAL_MS` to notice a new network faster (it costs a scan
every interval, which briefly blocks the network task).
