// Test 4 of 6: Wi-Fi + internet time (NTP)
//
// Setup: fill WIFI_SSID and WIFI_PASS in secrets.h (this folder). The ESP8266 only joins 2.4 GHz Wi-Fi;
//        a phone hotspot works if it is set to 2.4 GHz / "maximise compatibility".
// Serial Monitor: 115200 baud
//
// Pass: shows the board's IP address and signal strength, then the current UTC time every 5 s.
//       Signed telemetry is rejected by the backend if the clock is off by more than 5 minutes,
//       so a correct time here matters.

#include <ESP8266WiFi.h>
#include <time.h>
#include "secrets.h"

const unsigned long PRINT_EVERY_MS = 5000;
unsigned long lastPrint = 0;

void connectWifi() {
  Serial.print("Connecting to ");
  Serial.println(WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("Connected. IP: ");
    Serial.println(WiFi.localIP());
    Serial.print("Signal (RSSI): ");
    Serial.print(WiFi.RSSI());
    Serial.println(" dBm (above -70 is fine)");
  } else {
    Serial.println("ERROR: could not join. Check the name/password and that the network is 2.4 GHz.");
  }
}

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println();
  Serial.println("Test 4: Wi-Fi + NTP time");
  Serial.println("==========");
  connectWifi();
  configTime(0, 0, "pool.ntp.org", "time.google.com");   // UTC; the backend works in UTC epoch seconds
  Serial.println("Waiting for internet time...");
  unsigned long start = millis();
  while (time(nullptr) < 1700000000 && millis() - start < 20000) delay(250);
  Serial.println(time(nullptr) >= 1700000000 ? "Time synchronised." : "ERROR: no time yet (internet blocked?)");
  Serial.println("----------");
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Wi-Fi lost, reconnecting...");
    connectWifi();
  }
  if (millis() - lastPrint < PRINT_EVERY_MS) return;
  lastPrint = millis();

  time_t now = time(nullptr);
  struct tm *utc = gmtime(&now);
  char buf[32];
  strftime(buf, sizeof buf, "%Y-%m-%d %H:%M:%S UTC", utc);
  Serial.print("Epoch: ");
  Serial.println((unsigned long)now);
  Serial.print("Time: ");
  Serial.println(buf);
  Serial.print("Wi-Fi: ");
  Serial.println(WiFi.status() == WL_CONNECTED ? "connected" : "down");
  Serial.println("----------");
}
