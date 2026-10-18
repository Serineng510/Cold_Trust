// Test 2 of 6: light sensor module (SN-LIGHT-MOD) and calibration
//
// Wiring:  VCC -> 3V3 (not 5V: A0 on the NodeMCU must stay at or below 3.3 V)
//          GND -> GND    AO -> A0    DO -> D5
// Serial Monitor: 115200 baud
//
// What to do:
//   1. Cover the sensor with your hand or a box (a dark cargo hold)  -> note "Raw (A0)"
//   2. Shine a phone torch on it (door opened)                        -> note "Raw (A0)"
//   3. If BRIGHT gives a LOWER raw number than DARK, set LIGHT_INVERT to true and re-upload.
//      After that, "Brightness" must go UP when you add light.
//   4. Turn the module's blue potentiometer until its DO LED switches between your dark and torch levels.
//
// Pass: Brightness is low (under ~10%) covered and high (over ~50%) under the torch; DO changes state too.
// The backend's door-open alarm fires at 30% brightness (COLDTRUST_LIGHT_PCT).

const bool LIGHT_INVERT = false;   // set true if the raw value DROPS when light increases
const int LIGHT_AO = A0;
const int LIGHT_DO = D5;
const unsigned long PRINT_EVERY_MS = 1000;

unsigned long lastPrint = 0;
int minRaw = 1023;
int maxRaw = 0;

int brightnessRaw() {              // 0..1023, higher = brighter (what the backend expects)
  int raw = analogRead(LIGHT_AO);
  return LIGHT_INVERT ? 1023 - raw : raw;
}

void setup() {
  Serial.begin(115200);
  delay(200);
  pinMode(LIGHT_DO, INPUT);
  Serial.println();
  Serial.println("Test 2: light sensor, AO on A0, DO on D5");
  Serial.println("==========");
}

void loop() {
  if (millis() - lastPrint < PRINT_EVERY_MS) return;
  lastPrint = millis();

  int raw = analogRead(LIGHT_AO);
  int bright = brightnessRaw();
  minRaw = min(minRaw, raw);
  maxRaw = max(maxRaw, raw);

  Serial.print("Raw (A0): ");
  Serial.println(raw);
  Serial.print("Brightness: ");
  Serial.print(bright * 100.0 / 1023.0, 1);
  Serial.println(" %");
  Serial.print("Door alarm (>= 30%): ");
  Serial.println(bright * 100.0 / 1023.0 >= 30.0 ? "YES" : "no");
  Serial.print("DO pin (D5): ");
  Serial.println(digitalRead(LIGHT_DO) == LOW ? "LOW" : "HIGH");
  Serial.print("Raw range seen: ");
  Serial.print(minRaw);
  Serial.print(" to ");
  Serial.println(maxRaw);
  Serial.println("----------");
}
