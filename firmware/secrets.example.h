// ColdTrust device settings. Copy to secrets.h next to the sketch and fill in.
// secrets.h is git-ignored: it holds your Wi-Fi password and the device key.
#pragma once

#define WIFI_SSID      "your-2.4GHz-network"
#define WIFI_PASS      "your-wifi-password"

// Laptop running the backend (`ipconfig` -> IPv4 Address), started with --host 0.0.0.0
#define SERVER_URL     "http://192.168.1.50:8000"

#define DEVICE_ID      "CC-001"          // the dashboard follows this device
#define DEVICE_SECRET  "paste COLDTRUST_SECRET from cold-chain/.env here"   // must equal COLDTRUST_SECRET in cold-chain/.env
