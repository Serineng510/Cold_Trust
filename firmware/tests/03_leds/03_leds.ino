// Test 3 of 6: status LEDs
//
// Wiring:  green LED: D7 -> 220-330 ohm resistor -> LED long leg (+); short leg (-) -> GND
//          red LED:   D1 -> 220-330 ohm resistor -> LED long leg (+); short leg (-) -> GND
// Serial Monitor: 115200 baud
//
// There is no buzzer: the dashboard sounds the alarm and Telegram messages the driver and admin.
// Keep the LEDs away from the light sensor, or on the outside of the box: the LDR will see them
// as "door open" otherwise. The combined firmware reads the LDR before it blinks for the same reason.
//
// Pass: each step below happens in order and the Serial Monitor names it.

const int LED_OK = D7;      // green
const int LED_ALERT = D1;   // red

void step(const char *name) {
  Serial.println(name);
  delay(400);
}

void setup() {
  Serial.begin(115200);
  delay(200);
  pinMode(LED_OK, OUTPUT);
  pinMode(LED_ALERT, OUTPUT);
  digitalWrite(LED_OK, LOW);
  digitalWrite(LED_ALERT, LOW);
  Serial.println();
  Serial.println("Test 3: green LED D7, red LED D1");
  Serial.println("==========");
}

void loop() {
  step("1. Green on for 1 s");
  digitalWrite(LED_OK, HIGH); delay(1000); digitalWrite(LED_OK, LOW);

  step("2. Red on for 1 s");
  digitalWrite(LED_ALERT, HIGH); delay(1000); digitalWrite(LED_ALERT, LOW);

  step("3. Heartbeat: short green blink (sensor read OK)");
  for (int i = 0; i < 3; i++) { digitalWrite(LED_OK, HIGH); delay(40); digitalWrite(LED_OK, LOW); delay(900); }

  step("4. Double green blink (data sent to the backend)");
  for (int i = 0; i < 2; i++) { digitalWrite(LED_OK, HIGH); delay(80); digitalWrite(LED_OK, LOW); delay(120); }
  delay(800);

  step("5. Fast red blink (Wi-Fi down, buffering)");
  for (int i = 0; i < 10; i++) { digitalWrite(LED_ALERT, HIGH); delay(100); digitalWrite(LED_ALERT, LOW); delay(100); }

  step("6. Solid red (alarm: door open or temperature out of range)");
  digitalWrite(LED_ALERT, HIGH); delay(2000); digitalWrite(LED_ALERT, LOW);

  Serial.println("----------");
  delay(1500);
}
