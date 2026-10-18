// Test 1 of 6: DHT11 temperature + humidity sensor
//
// Wiring (DHT11 module):  VCC -> 3V3   GND -> GND   DATA -> D2
// Library: "DHT sensor library" by Adafruit (also installs "Adafruit Unified Sensor")
// Board:   NodeMCU 1.0 (ESP-12E Module)      Serial Monitor: 115200 baud
//
// Pass: a reading every 5 s. Hold the sensor in your hand: temperature rises 1-3 C within a minute.
// Fail: "no data" -> check the DATA wire is on D2 and the module has power.

#include <DHT.h>

const int DHT_PIN = D2;
const unsigned long READ_EVERY_MS = 5000;   // the DHT11 needs at least 1 s between reads

DHT dht(DHT_PIN, DHT11);
unsigned long lastRead = 0;
unsigned long readings = 0;
unsigned long failures = 0;

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println();
  Serial.println("Test 1: DHT11 on D2");
  Serial.println("==========");
  dht.begin();
}

void loop() {
  if (millis() - lastRead < READ_EVERY_MS) return;
  lastRead = millis();

  float tempC = dht.readTemperature();
  float hum = dht.readHumidity();
  readings++;

  Serial.print("Reading #");
  Serial.println(readings);
  if (isnan(tempC) || isnan(hum)) {
    failures++;
    Serial.println("ERROR: no data from DHT11");
    Serial.println("Check: DATA on D2, VCC on 3V3, GND on GND");
  } else {
    Serial.print("Temperature: ");
    Serial.print(tempC, 1);
    Serial.println(" C");
    Serial.print("Humidity: ");
    Serial.print(hum, 0);
    Serial.println(" %");
  }
  Serial.print("Failed reads so far: ");
  Serial.println(failures);
  Serial.println("----------");
}
