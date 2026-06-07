#include <Arduino.h>
#include "../include/config.h"
#include "../include/debug_led.h"

void setupDebugLed() {
  pinMode(DEBUG_LED_PIN, OUTPUT);
  digitalWrite(DEBUG_LED_PIN, LOW);
}

void debugLedOn() {
  digitalWrite(DEBUG_LED_PIN, HIGH);
}

void debugLedOff() {
  digitalWrite(DEBUG_LED_PIN, LOW);
}

void blinkDebugLedOnce() {
  digitalWrite(DEBUG_LED_PIN, HIGH);
  delay(80);
  digitalWrite(DEBUG_LED_PIN, LOW);
}

void blinkDebugLed(uint8_t count, uint16_t onMs, uint16_t offMs) {
  for (uint8_t i = 0; i < count; i++) {
    digitalWrite(DEBUG_LED_PIN, HIGH);
    delay(onMs);
    digitalWrite(DEBUG_LED_PIN, LOW);
    delay(offMs);
  }
}
