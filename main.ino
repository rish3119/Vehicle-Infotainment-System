// LED Blink — 1 Hz (500ms on, 500ms off)
// LED on D13 via 220 ohm resistor

const uint8_t LED_PIN = 13;

void setup() {
  pinMode(LED_PIN, OUTPUT);
}

void loop() {
  digitalWrite(LED_PIN, HIGH);  // LED on
  delay(500);
  digitalWrite(LED_PIN, LOW);   // LED off
  delay(500);
}
