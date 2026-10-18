#include <Arduino_BuiltIn.h>

// ColdTrust sensor node: combined firmware (NodeMCU 1.0 / ESP-12E)
//
// What it does
//   - Self-test at power-on: checks every part and prints which wire to look at if one fails.
//     Type  t  + Enter in the Serial Monitor to run it again.
//   - Reads the DHT11 every 5 s and watches the light sensor every 0.1 s.
//   - Logs a sample every 30 s into a 1-hour memory buffer (kept while Wi-Fi is down).
//   - Routine ping: sends all unsent samples every 15 min (60 s in DEMO_MODE).
//   - Event ping: sends immediately when the hold is lit (door open), the temperature leaves 2-8 C,
//     or it jumps 5 C within a minute.
//   - Every payload is signed (HMAC-SHA256) so the backend can reject fakes and replays.
//   - Green LED: short blink = reading OK, double blink = data sent.
//     Red LED: solid = alarm, fast blink = Wi-Fi down.
//   - No buzzer: the dashboard sounds the alarm and Telegram messages the driver and admin.
//
// Wiring: see wiring.svg in this folder.  DHT11 DATA -> D2 | light AO -> A0, DO -> D5 |
//         green LED -> D7 | red LED -> D1 | every VCC -> 3V3 | every GND -> GND
// Libraries: "DHT sensor library" (Adafruit), "ArduinoJson" 7.x (Benoit Blanchon)
// Settings: secrets.h in this folder (Wi-Fi, SERVER_URL, DEVICE_ID, DEVICE_SECRET)
// Backend:  .venv\Scripts\python -m backend --host 0.0.0.0 --db data/hardware.db --reset   (in cold-chain/)

#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <bearssl/bearssl.h>
#include <time.h>
#include <math.h>
#include "secrets.h"

// ------------------------------------------------------------------ settings you may change
const bool DEMO_MODE = true;            // true: routine ping every 60 s (for the demo); false: every 15 min
const bool ALLOW_MOCK = true;           // follow the dashboard's "Mock temperature" / "Mock light" checkboxes (backend needs --demo).
                                        // Unticked = the real sensor; ticked = the slider value, labelled as mock.
const bool LIGHT_INVERT = false;        // set as found in test 2 (true if the raw value DROPS with more light)
const bool DO_BRIGHT_IS_LOW = true;     // most LM393 light modules pull DO LOW when it is bright
const float HOLD_MIN_C = 2.0;           // tightest product limits in the hold
const float HOLD_MAX_C = 8.0;
const float LIGHT_ALARM_PCT = 30.0;     // same as the backend's COLDTRUST_LIGHT_PCT
const float STEP_ALARM_C = 5.0;         // change within 60 s that counts as a step

// ------------------------------------------------------------------ pins
const int PIN_DHT = D2;
const int PIN_LIGHT_AO = A0;
const int PIN_LIGHT_DO = D5;
const int PIN_LED_OK = D7;      // green
const int PIN_LED_ALERT = D1;   // red

// ------------------------------------------------------------------ timing
const unsigned long SAMPLE_MS = 5000;
const unsigned long LOG_MS = 30000;
const unsigned long ROUTINE_MS = DEMO_MODE ? 60000UL : 900000UL;
const unsigned long LIGHT_POLL_MS = 100;
const unsigned long LIGHT_RESEND_MS = 60000;   // while the door stays open
const unsigned long TEMP_RESEND_MS = 120000;   // while the temperature stays out of range
const unsigned long WIFI_RETRY_MS = 10000;
const int MAX_PER_PAYLOAD = 40;

// ------------------------------------------------------------------ state
struct LogEntry { uint32_t t; float temp; uint8_t hum; uint16_t light; bool mockTemp; bool mockLight; };
const int BUF_SIZE = 120;                      // 120 x 30 s = 1 hour without Wi-Fi
LogEntry buf[BUF_SIZE];
int bufHead = 0, bufCount = 0;
uint32_t lastLogT = 0, lastSentT = 0, seq = 0;

DHT dht(PIN_DHT, DHT11);
float tempNow = NAN, humNow = NAN;
float tempHistory[13];                          // last 60 s of 5-s readings, for the step check
int tempHistCount = 0;
int outOfRangeReads = 0;
int brightPolls = 0;
uint16_t lightNow = 0;
volatile bool doChanged = false;

bool mockOn = false, tempMocked = false;        // dashboard checkbox, and whether the latest reading was mocked
float mockTemp = 4.0;                           // the dashboard slider
bool mockLightOn = false, lightMocked = false;  // same for the light sensor
float mockLightPct = 0.0;
unsigned long nextMockPoll = 0, lastMockPoll = 0;
const unsigned long MOCK_POLL_MS = 2000;       // how often the dashboard's checkbox / slider is checked
bool lightAlarm = false, tempAlarm = false;
unsigned long lightAlarmSent = 0, tempAlarmSent = 0, stepSent = 0;
unsigned long lastSample = 0, lastLog = 0, lastRoutine = 0, lastLightPoll = 0, lastWifiTry = 0;
unsigned long okLedOffAt = 0;
bool timeSynced = false;
String lastSendResult = "nothing sent yet";

// ------------------------------------------------------------------ small helpers
void IRAM_ATTR onLightDo() { doChanged = true; }

uint16_t readLight() {                          // 0..1023, higher = brighter
  int raw = analogRead(PIN_LIGHT_AO);
  return LIGHT_INVERT ? 1023 - raw : raw;
}

float lightPct(uint16_t v) { return v * 100.0 / 1023.0; }

bool doSaysBright() { return digitalRead(PIN_LIGHT_DO) == (DO_BRIGHT_IS_LOW ? LOW : HIGH); }

uint32_t nowEpoch() { return (uint32_t)time(nullptr); }

void flashOk(unsigned int ms) {                 // non-blocking green blink
  digitalWrite(PIN_LED_OK, HIGH);
  okLedOffAt = millis() + ms;
}

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

// ------------------------------------------------------------------ buffer
void logSample() {
  if (!timeSynced || isnan(tempNow)) return;   // the backend needs real timestamps
  uint32_t t = nowEpoch();
  if (t <= lastLogT) t = lastLogT + 1;          // strictly increasing, or the backend calls it a replay
  lastLogT = t;
  int idx = (bufHead + bufCount) % BUF_SIZE;
  if (bufCount == BUF_SIZE) {                    // full: drop the oldest
    bufHead = (bufHead + 1) % BUF_SIZE;
    bufCount--;
    Serial.println("Buffer full: oldest reading dropped");
  }
  buf[idx] = LogEntry{ t, tempNow, (uint8_t)lround(humNow), lightNow, tempMocked, lightMocked };
  bufCount++;
}

int unsentCount() {
  int n = 0;
  for (int i = 0; i < bufCount; i++) if (buf[(bufHead + i) % BUF_SIZE].t > lastSentT) n++;
  return n;
}

// ------------------------------------------------------------------ sending
// Posts up to MAX_PER_PAYLOAD unsent samples. Returns true if the backend answered (accepted or not).
bool postChunk(const char *pingType, const char *trigger) {
  JsonDocument doc;
  uint32_t ts = nowEpoch();
  seq++;
  String text = String(DEVICE_ID) + "|" + String(seq) + "|" + String(ts) + "|" + pingType + "|"
                + (trigger ? trigger : "-") + "|";
  doc["device_id"] = DEVICE_ID;
  doc["seq"] = seq;
  doc["ts"] = ts;
  doc["ping_type"] = pingType;
  if (trigger) doc["trigger"] = trigger; else doc["trigger"] = nullptr;
  JsonArray arr = doc["samples"].to<JsonArray>();
  int n = 0;
  bool anyMockTemp = false, anyMockLight = false;
  uint32_t newestT = lastSentT;
  for (int i = 0; i < bufCount && n < MAX_PER_PAYLOAD; i++) {
    const LogEntry &e = buf[(bufHead + i) % BUF_SIZE];
    if (e.t <= lastSentT) continue;
    String tempTxt = String(e.temp, 1);
    if (n) text += ";";
    text += String(e.t) + ":" + tempTxt + ":" + String(e.hum) + ":" + String(e.light);
    JsonObject s = arr.add<JsonObject>();
    s["t"] = e.t;
    s["temp_c"] = serialized(tempTxt);         // exactly the text that was signed
    s["hum"] = e.hum;
    s["light"] = e.light;
    newestT = e.t;
    if (e.mockTemp) anyMockTemp = true;
    if (e.mockLight) anyMockLight = true;
    n++;
  }
  if (n == 0) return true;
  if (anyMockTemp || anyMockLight) {             // labels only (not signed): the dashboard shows which sensor was mocked
    doc["demo"] = true;
    JsonArray what = doc["demo_sensors"].to<JsonArray>();
    if (anyMockTemp) what.add("temp");
    if (anyMockLight) what.add("light");
  }
  doc["sig"] = hmacSha256Hex(DEVICE_SECRET, text);
  String body;
  serializeJson(doc, body);

  WiFiClient client;
  HTTPClient http;
  http.setTimeout(5000);
  http.begin(client, String(SERVER_URL) + "/api/telemetry");
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(body);
  String resp;
  if (code > 0) resp = http.getString();
  http.end();

  if (code != 200) {
    lastSendResult = String("HTTP ") + code + " (kept " + unsentCount() + " readings to retry)";
    Serial.print("Send failed: ");
    Serial.println(lastSendResult);
    return false;
  }
  lastSentT = newestT;                           // answered: never resend these, even if rejected
  bool accepted = resp.indexOf("\"accepted\":true") >= 0;
  lastSendResult = String(pingType) + " " + n + " readings, " + (accepted ? "accepted" : "REJECTED");
  if (!accepted) {
    int r = resp.indexOf("\"reason\":\"");
    if (r >= 0) lastSendResult += " (" + resp.substring(r + 10, resp.indexOf('"', r + 10)) + ")";
  }
  Serial.print("Sent: ");
  Serial.println(lastSendResult);
  if (accepted) {                                 // double green blink = delivered
    digitalWrite(PIN_LED_OK, HIGH); delay(80); digitalWrite(PIN_LED_OK, LOW); delay(120);
    flashOk(80);
  }
  return true;
}

// Sends every unsent reading, oldest first; the last chunk carries the ping type and trigger.
void sendPending(const char *pingType, const char *trigger) {
  if (WiFi.status() != WL_CONNECTED || !timeSynced) return;
  while (unsentCount() > 0) {
    bool last = unsentCount() <= MAX_PER_PAYLOAD;
    if (!postChunk(last ? pingType : "ROUTINE", last ? trigger : nullptr)) return;
  }
}

void raiseEvent(const char *trigger) {
  Serial.print("EVENT: ");
  Serial.println(trigger);
  logSample();                                   // include the reading at the moment it happened
  sendPending("EVENT", trigger);
  lastRoutine = millis();
}

// ------------------------------------------------------------------ status LEDs (the alarm sound is on the dashboard)
void updateAlarmOutputs() {
  bool alarm = lightAlarm || tempAlarm;
  if (alarm) {
    digitalWrite(PIN_LED_ALERT, HIGH);
  } else if (WiFi.status() != WL_CONNECTED) {
    digitalWrite(PIN_LED_ALERT, (millis() / 100) % 2);      // fast blink: Wi-Fi down
  } else {
    digitalWrite(PIN_LED_ALERT, LOW);
  }
  if (okLedOffAt && millis() >= okLedOffAt) {
    digitalWrite(PIN_LED_OK, LOW);
    okLedOffAt = 0;
  }
}

// ------------------------------------------------------------------ sensing
// Light value for this moment: the dashboard's slider (with the tiny jitter a real LDR has, so it never looks
// like a frozen sensor) when "Mock light" is ticked, otherwise the real sensor.
void refreshLightValue() {
  if (ALLOW_MOCK && mockLightOn) {
    lightMocked = true;
    lightNow = constrain((int)(mockLightPct * 10.23f) + (int)random(-3, 4), 0, 1023);
  } else {
    lightMocked = false;
    lightNow = readLight();
  }
}

void pollLight() {
  if (!(ALLOW_MOCK && mockLightOn) && okLedOffAt) return;   // never read the real light while our own LED is lit
  refreshLightValue();
  bool bright = lightPct(lightNow) >= LIGHT_ALARM_PCT || (!lightMocked && doChanged && doSaysBright());
  doChanged = false;
  brightPolls = bright ? brightPolls + 1 : 0;
  bool open = brightPolls >= 2;                  // 2 polls in a row = 0.2 s of light
  if (open && !lightAlarm) {
    lightAlarm = true;
    lightAlarmSent = millis();
    raiseEvent("LIGHT");
  } else if (open && millis() - lightAlarmSent >= LIGHT_RESEND_MS) {
    lightAlarmSent = millis();
    raiseEvent("LIGHT");
  } else if (!bright && lightAlarm) {
    lightAlarm = false;
    Serial.println("Hold dark again (door closed)");
  }
}

// Asks the backend whether the dashboard's "Mock temperature" box is ticked, and the slider value.
// Returns true when the setting CHANGED (box ticked / unticked, or the slider moved).
// After a failed request it backs off for 30 s so a missing server never slows the sensors.
bool pollMock() {
  if (!ALLOW_MOCK || WiFi.status() != WL_CONNECTED || millis() < nextMockPoll) return false;
  bool wasOn = mockOn, wasLightOn = mockLightOn;
  float wasTemp = roundf(mockTemp), wasLight = roundf(mockLightPct);
  WiFiClient client;
  HTTPClient http;
  http.setTimeout(1500);
  http.begin(client, String(SERVER_URL) + "/api/demo");
  int code = http.GET();
  if (code == 200) {
    JsonDocument d;
    if (!deserializeJson(d, http.getString())) {
      bool enabled = d["enabled"].as<bool>();
      mockOn = enabled && d["mock"].as<bool>();
      mockTemp = d["temp_c"].as<float>();
      mockLightOn = enabled && d["light_mock"].as<bool>();
      mockLightPct = d["light_pct"].as<float>();
    }
  } else {
    nextMockPoll = millis() + 30000;
  }
  http.end();
  return mockOn != wasOn || (mockOn && roundf(mockTemp) != wasTemp) ||
         mockLightOn != wasLightOn || (mockLightOn && roundf(mockLightPct) != wasLight);
}

void sampleTemperature() {
  float t, h;
  if (ALLOW_MOCK && mockOn) {                    // box ticked: report the slider value
    t = roundf(mockTemp);
    h = 55 + random(-2, 3);
    tempMocked = true;
  } else {                                       // box unticked: the real sensor
    t = dht.readTemperature();
    h = dht.readHumidity();
    tempMocked = false;
    if (isnan(t) || isnan(h)) {
      Serial.println("DHT11 read failed (check DATA wire on D2)");
      return;
    }
  }
  tempNow = t;
  humNow = h;
  flashOk(30);                                   // heartbeat (light is not read while this is on)

  // step: compare with the reading about 60 s ago
  if (tempHistCount == 13) {
    for (int i = 0; i < 12; i++) tempHistory[i] = tempHistory[i + 1];
    tempHistCount = 12;
  }
  tempHistory[tempHistCount++] = t;
  if (tempHistCount == 13 && fabs(t - tempHistory[0]) >= STEP_ALARM_C && millis() - stepSent > 60000) {
    stepSent = millis();
    raiseEvent("STEP");
  }

  // out of range for 2 readings in a row (the DHT11 is only accurate to +/-2 C)
  bool out = t < HOLD_MIN_C || t > HOLD_MAX_C;
  outOfRangeReads = out ? outOfRangeReads + 1 : 0;
  if (outOfRangeReads >= 2 && !tempAlarm) {
    tempAlarm = true;
    tempAlarmSent = millis();
    raiseEvent(t > HOLD_MAX_C ? "TEMP_HIGH" : "TEMP_LOW");
  } else if (tempAlarm && outOfRangeReads >= 2 && millis() - tempAlarmSent >= TEMP_RESEND_MS) {
    tempAlarmSent = millis();
    raiseEvent(t > HOLD_MAX_C ? "TEMP_HIGH" : "TEMP_LOW");
  } else if (tempAlarm && !out) {
    tempAlarm = false;
    Serial.println("Temperature back in range");
  }
}

void printStatus() {
  Serial.print("Temperature: ");
  if (isnan(tempNow)) Serial.println("no reading");
  else { Serial.print(tempNow, 1); Serial.println(tempMocked ? " C (MOCK from the dashboard)" : " C"); }
  Serial.print("Humidity: ");
  if (isnan(humNow)) Serial.println("no reading"); else { Serial.print(humNow, 0); Serial.println(" %"); }
  Serial.print("Light: ");
  Serial.print(lightPct(lightNow), 1);
  Serial.println(lightMocked ? " % (MOCK from the dashboard)" : " %");
  Serial.print("Door alarm: ");
  Serial.println(lightAlarm ? "YES" : "no");
  Serial.print("Temperature alarm: ");
  Serial.println(tempAlarm ? "YES" : "no");
  Serial.print("Wi-Fi: ");
  Serial.println(WiFi.status() == WL_CONNECTED ? "connected" : "down (buffering)");
  Serial.print("Unsent readings: ");
  Serial.println(unsentCount());
  Serial.print("Last send: ");
  Serial.println(lastSendResult);
  Serial.println("----------");
}

// ------------------------------------------------------------------ Wi-Fi + time
void wifiBegin() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  lastWifiTry = millis();
}

void keepWifi() {
  if (WiFi.status() == WL_CONNECTED) {
    if (!timeSynced && nowEpoch() > 1700000000) {
      timeSynced = true;
      seq = nowEpoch();                          // always larger than any sequence used before a reboot
      Serial.println("Internet time synchronised; logging starts");
    }
    return;
  }
  if (millis() - lastWifiTry >= WIFI_RETRY_MS) {
    Serial.println("Wi-Fi down, retrying...");
    WiFi.disconnect();
    wifiBegin();
  }
}

// ------------------------------------------------------------------ self-test
int passCount = 0, checkCount = 0;

void result(bool ok, const char *part, const String &detail, const char *wires) {
  Serial.print(ok ? "[PASS]  " : "[CHECK] ");
  Serial.print(part);
  Serial.print(": ");
  Serial.println(detail);
  if (!ok) {
    Serial.print("        Check: ");
    Serial.println(wires);
  }
  if (ok) passCount++; else checkCount++;
}

void selfTest() {
  passCount = checkCount = 0;
  Serial.println();
  Serial.println("========== ColdTrust self-test ==========");
  Serial.println("(run it in normal room light; type t + Enter to repeat)");

  // 1. DHT11
  float t = NAN, h = NAN;
  for (int i = 0; i < 3 && (isnan(t) || isnan(h)); i++) {
    delay(i ? 1200 : 0);
    t = dht.readTemperature();
    h = dht.readHumidity();
  }
  String dhtDetail = "no data";
  if (!isnan(t) && !isnan(h)) dhtDetail = String(t, 1) + " C, " + String(h, 0) + " %";
  result(!isnan(t) && !isnan(h), "DHT11", dhtDetail,
         "DATA -> D2, VCC -> 3V3, GND -> GND (3-pin module; a bare sensor also needs 10k DATA-3V3)");
  if (ALLOW_MOCK) {
    Serial.println("[INFO]  The dashboard can mock the temperature and the light (Hold / Light chips). Mock readings are labelled.");
  }

  // 2. light sensor, analog
  int lo = 1023, hi = 0;
  for (int i = 0; i < 20; i++) { int v = analogRead(PIN_LIGHT_AO); lo = min(lo, v); hi = max(hi, v); delay(50); }
  bool stuckLow = hi <= 2, stuckHigh = lo >= 1021;
  String lightDetail = String("raw ") + lo + "-" + hi + " (" + String(lightPct(LIGHT_INVERT ? 1023 - hi : hi), 1) + " % bright)";
  result(!stuckLow && !stuckHigh, "Light sensor AO", lightDetail,
         stuckLow ? "reads 0: AO -> A0, VCC -> 3V3, GND -> GND (loose wire or no power)"
                  : "reads maximum: AO -> A0; module VCC must be 3V3, not 5V");

  // 3. light sensor, digital
  String doDetail = String("D5 is ") + (digitalRead(PIN_LIGHT_DO) ? "HIGH" : "LOW")
                    + ": cover / torch it and watch the module's DO LED; the blue pot sets the switch point";
  result(true, "Light sensor DO", doDetail, "");

  // 4-5. LEDs: watch
  Serial.println("[WATCH] Green LED (D7) blinks 3 times now...");
  for (int i = 0; i < 3; i++) { digitalWrite(PIN_LED_OK, HIGH); delay(200); digitalWrite(PIN_LED_OK, LOW); delay(200); }
  Serial.println("        Nothing? D7 -> resistor -> LED long leg; short leg -> GND (flip the LED if reversed)");
  Serial.println("[WATCH] Red LED (D1) blinks 3 times now...");
  for (int i = 0; i < 3; i++) { digitalWrite(PIN_LED_ALERT, HIGH); delay(200); digitalWrite(PIN_LED_ALERT, LOW); delay(200); }
  Serial.println("        Nothing? D1 -> resistor -> LED long leg; short leg -> GND (flip the LED if reversed)");

  // 6. Wi-Fi
  if (WiFi.status() != WL_CONNECTED) {
    wifiBegin();
    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) delay(250);
  }
  bool wifi = WiFi.status() == WL_CONNECTED;
  String wifiDetail = "not connected";
  if (wifi) wifiDetail = WiFi.localIP().toString() + ", signal " + WiFi.RSSI() + " dBm";
  result(wifi, "Wi-Fi", wifiDetail, "WIFI_SSID / WIFI_PASS in secrets.h; the network must be 2.4 GHz");

  // 7. time
  if (wifi) {
    unsigned long start = millis();
    while (nowEpoch() < 1700000000 && millis() - start < 15000) delay(250);
  }
  bool tok = nowEpoch() > 1700000000;
  String timeDetail = "not synchronised";
  if (tok) timeDetail = String(nowEpoch()) + " (UTC epoch)";
  result(tok, "Internet time", timeDetail, "the network must reach the internet (pool.ntp.org)");
  keepWifi();

  // 8. backend
  bool backend = false;
  String detail = "skipped (no Wi-Fi)";
  if (wifi) {
    WiFiClient client;
    HTTPClient http;
    http.setTimeout(4000);
    http.begin(client, String(SERVER_URL) + "/api/health");
    int code = http.GET();
    backend = code == 200;
    if (backend) detail = String(SERVER_URL) + " answered";
    else detail = String("HTTP ") + code + " from " + SERVER_URL;
    http.end();
  }
  result(backend, "Backend", detail,
         "SERVER_URL = laptop IP from ipconfig; start the backend with --host 0.0.0.0; allow Python in the firewall");

  Serial.print("Result: ");
  Serial.print(passCount);
  Serial.print(" PASS, ");
  Serial.print(checkCount);
  Serial.println(" CHECK (plus the two LED steps you watched)");
  Serial.println("=========================================");
  Serial.println();
}

// ------------------------------------------------------------------ setup / loop
void setup() {
  Serial.begin(115200);
  delay(300);
  pinMode(PIN_LED_OK, OUTPUT);
  pinMode(PIN_LED_ALERT, OUTPUT);
  pinMode(PIN_LIGHT_DO, INPUT);
  digitalWrite(PIN_LED_OK, LOW);
  digitalWrite(PIN_LED_ALERT, LOW);
  attachInterrupt(digitalPinToInterrupt(PIN_LIGHT_DO), onLightDo, CHANGE);
  dht.begin();
  configTime(0, 0, "pool.ntp.org", "time.google.com");   // UTC, as the backend expects
  wifiBegin();
  Serial.println();
  Serial.print("ColdTrust node ");
  Serial.println(DEVICE_ID);
  Serial.println(DEMO_MODE ? "Demo mode: routine ping every 60 s" : "Routine ping every 15 min");
  randomSeed(micros());
  selfTest();
  lastRoutine = lastLog = lastSample = millis();
}

void loop() {
  unsigned long now = millis();
  if (Serial.available()) {
    char c = Serial.read();
    if (c == 't' || c == 'T') selfTest();
  }
  keepWifi();
  if (ALLOW_MOCK && now - lastMockPoll >= MOCK_POLL_MS) {
    lastMockPoll = now;
    if (pollMock()) {                            // setting changed: show it on the dashboard right away
      Serial.print("Mock setting changed: temperature ");
      Serial.print(mockOn ? "MOCK" : "real");
      Serial.print(", light ");
      Serial.println(mockLightOn ? "MOCK" : "real");
      sendPending("ROUTINE", nullptr);           // first deliver readings taken under the old setting
      refreshLightValue();                       // and take new readings under the new setting
      sampleTemperature();
      logSample();
      sendPending("ROUTINE", nullptr);
      lastSample = lastRoutine = millis();
    }
  }
  if (now - lastLightPoll >= LIGHT_POLL_MS) { lastLightPoll = now; pollLight(); }
  if (now - lastSample >= SAMPLE_MS) { lastSample = now; sampleTemperature(); }
  if (now - lastLog >= LOG_MS) { lastLog = now; logSample(); printStatus(); }
  if (now - lastRoutine >= ROUTINE_MS) { lastRoutine = now; sendPending("ROUTINE", nullptr); }
  updateAlarmOutputs();
}
