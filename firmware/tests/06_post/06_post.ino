// Test 6 of 6: send real, signed readings to the ColdTrust backend
//
// Needs tests 1, 2, 4 and 5 passing first.
// Setup:
//   - secrets.h in this folder: Wi-Fi, SERVER_URL (your laptop's IP) and DEVICE_SECRET
//     (DEVICE_SECRET must equal COLDTRUST_SECRET in cold-chain/.env).
//   - Library: "ArduinoJson" by Benoit Blanchon, version 7.x.
//   - On the laptop, in the cold-chain folder, start the backend so the board can reach it:
//         .venv\Scripts\python -m backend --host 0.0.0.0 --no-telegram
//     Allow Python through the Windows firewall when asked. Find the laptop IP with `ipconfig`.
// Serial Monitor: 115200 baud
//
// Pass: every 20 s it prints "HTTP 200" and "accepted: yes", and the dashboard's temperature and light
//       gauges show your real readings.

#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <bearssl/bearssl.h>
#include <time.h>
#include "secrets.h"

const int DHT_PIN = D2;
const int LIGHT_AO = A0;
const bool LIGHT_INVERT = false;            // copy the value you chose in test 2
const unsigned long SEND_EVERY_MS = 20000;

DHT dht(DHT_PIN, DHT11);
unsigned long lastSend = 0;
uint32_t seq = 0;   // must always increase, even across reboots: starts at the boot time in epoch seconds

String hmacSha256Hex(const String &key, const String &msg) {
  br_hmac_key_context kc;
  br_hmac_key_init(&kc, &br_sha256_vtable, key.c_str(), key.length());
  br_hmac_context ctx;
  br_hmac_init(&ctx, &kc, 0);
  br_hmac_update(&ctx, msg.c_str(), msg.length());
  uint8_t out[32];
  br_hmac_out(&ctx, out);
  static const char HEX_DIGITS[] = "0123456789abcdef";
  String hex;
  hex.reserve(64);
  for (int i = 0; i < 32; i++) {
    hex += HEX_DIGITS[out[i] >> 4];
    hex += HEX_DIGITS[out[i] & 0x0F];
  }
  return hex;
}

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println();
  Serial.println("Test 6: signed POST to the backend");
  Serial.println("==========");
  dht.begin();
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) { delay(250); Serial.print("."); }
  Serial.println();
  Serial.print("Board IP: ");
  Serial.println(WiFi.localIP());
  configTime(0, 0, "pool.ntp.org", "time.google.com");
  Serial.println("Waiting for internet time...");
  while (time(nullptr) < 1700000000) delay(250);
  seq = (uint32_t)time(nullptr);
  Serial.print("Sending to: ");
  Serial.println(SERVER_URL);
  Serial.println("----------");
}

void loop() {
  if (millis() - lastSend < SEND_EVERY_MS && lastSend != 0) return;
  lastSend = millis();

  float tempC = dht.readTemperature();
  float humF = dht.readHumidity();
  if (isnan(tempC) || isnan(humF)) {
    Serial.println("DHT11 read failed, skipping this round");
    Serial.println("----------");
    return;
  }
  int hum = (int)lround(humF);                       // whole percent, so both sides format it the same way
  int raw = analogRead(LIGHT_AO);
  int light = LIGHT_INVERT ? 1023 - raw : raw;
  uint32_t t = (uint32_t)time(nullptr);
  seq++;

  String text = String(DEVICE_ID) + "|" + String(seq) + "|" + String(t) + "|ROUTINE|-|"
                + String(t) + ":" + String(tempC, 1) + ":" + String(hum) + ":" + String(light);
  String sig = hmacSha256Hex(DEVICE_SECRET, text);

  JsonDocument doc;
  doc["device_id"] = DEVICE_ID;
  doc["seq"] = seq;
  doc["ts"] = t;
  doc["ping_type"] = "ROUTINE";
  doc["trigger"] = nullptr;
  JsonObject s = doc["samples"].to<JsonArray>().add<JsonObject>();
  s["t"] = t;
  s["temp_c"] = serialized(String(tempC, 1));        // exact text that was signed
  s["hum"] = hum;
  s["light"] = light;
  doc["sig"] = sig;
  String body;
  serializeJson(doc, body);

  WiFiClient client;
  HTTPClient http;
  http.begin(client, String(SERVER_URL) + "/api/telemetry");
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(body);
  String resp = http.getString();
  http.end();

  Serial.print("Temperature: ");
  Serial.print(tempC, 1);
  Serial.println(" C");
  Serial.print("Humidity: ");
  Serial.print(hum);
  Serial.println(" %");
  Serial.print("Light raw: ");
  Serial.println(light);
  Serial.print("Seq: ");
  Serial.println(seq);
  Serial.print("HTTP ");
  Serial.println(code);
  if (code == 200) {
    Serial.print("accepted: ");
    Serial.println(resp.indexOf("\"accepted\":true") >= 0 ? "yes" : "NO");
    int r = resp.indexOf("\"reason\":\"");
    if (r >= 0) {
      Serial.print("reason: ");
      Serial.println(resp.substring(r + 10, resp.indexOf('"', r + 10)));
    }
  } else {
    Serial.println("No answer: is the backend running with --host 0.0.0.0, and is SERVER_URL the laptop's IP?");
  }
  Serial.println("----------");
}
