// Test 5 of 6: payload signing (HMAC-SHA256), no hardware or network needed
//
// The backend rejects any payload whose signature does not match, so the board must build the
// signed text exactly like the backend does. This test signs a fixed example with the default
// development key and compares it with the value the backend's own code produced.
// Serial Monitor: 115200 baud
//
// Pass: both lines say PASS.

#include <bearssl/bearssl.h>   // ships with the ESP8266 Arduino core

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

// Same layout as backend/rules.py canonical():
//   device|seq|ts|ping_type|trigger or -|t:temp(1 decimal):hum(0 decimals):light;...
String sampleText(unsigned long t, float tempC, int hum, int light) {
  return String(t) + ":" + String(tempC, 1) + ":" + String(hum) + ":" + String(light);
}

void check(const char *label, const String &got, const char *expected) {
  Serial.print(label);
  Serial.println(got == expected ? "PASS" : "FAIL");
  Serial.print("  got:      ");
  Serial.println(got);
  Serial.print("  expected: ");
  Serial.println(expected);
}

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println();
  Serial.println("Test 5: payload signing");
  Serial.println("==========");

  const char *EXPECTED_TEXT = "CC-001|1|1800000060|ROUTINE|-|1800000055:3.0:55:20";
  const char *EXPECTED_SIG = "a3d9abac90955464592b58d6192a50a0bed65e1d42f4f2ed15bdfe863dfab10c";

  String text = String("CC-001") + "|" + String(1UL) + "|" + String(1800000060UL) + "|ROUTINE|-|"
                + sampleText(1800000055UL, 3.0, 55, 20);
  check("Signed text matches the backend format: ", text, EXPECTED_TEXT);
  check("Signature matches the backend:          ", hmacSha256Hex("dev-secret-change-me", text), EXPECTED_SIG);
  Serial.println("----------");
}

void loop() {}
