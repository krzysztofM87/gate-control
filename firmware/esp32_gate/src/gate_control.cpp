#include <Arduino.h>
#include "../include/config.h"
#include "../include/gate_control.h"
#include "../include/debug_led.h"

static uint8_t pinForGate(uint8_t gateTarget) {
  if (gateTarget == GATE_TARGET_1) {
    return GATE1_OUTPUT_PIN;
  }

  if (gateTarget == GATE_TARGET_2) {
    return GATE2_OUTPUT_PIN;
  }

  return 255;
}

static void setGatePin(uint8_t pin, bool active) {
  if (pin == 255) {
    return;
  }

#if GATE_ACTIVE_HIGH
  digitalWrite(pin, active ? HIGH : LOW);
#else
  digitalWrite(pin, active ? LOW : HIGH);
#endif
}

static uint32_t normalizePulseMs(uint32_t pulseMs) {
  if (pulseMs < MIN_GATE_PULSE_MS) {
    pulseMs = MIN_GATE_PULSE_MS;
  }

  if (pulseMs > MAX_GATE_PULSE_MS) {
    pulseMs = MAX_GATE_PULSE_MS;
  }

  return pulseMs;
}

void setupGateOutputs() {
  pinMode(GATE1_OUTPUT_PIN, OUTPUT);
  pinMode(GATE2_OUTPUT_PIN, OUTPUT);

  setGatePin(GATE1_OUTPUT_PIN, false);
  setGatePin(GATE2_OUTPUT_PIN, false);

  Serial.print("[GATE] Gate 1 output GPIO: ");
  Serial.println(GATE1_OUTPUT_PIN);

  Serial.print("[GATE] Gate 2 output GPIO: ");
  Serial.println(GATE2_OUTPUT_PIN);
}

void triggerGate(uint8_t gateTarget) {
  triggerGate(gateTarget, DEFAULT_GATE_PULSE_MS);
}

void triggerGate(uint8_t gateTarget, uint32_t pulseMs) {
  pulseMs = normalizePulseMs(pulseMs);

  if (gateTarget == GATE_TARGET_BOTH) {
    triggerGateBoth(pulseMs);
    return;
  }

  uint8_t pin = pinForGate(gateTarget);

  if (pin == 255) {
    Serial.print("[GATE] Invalid gate target: ");
    Serial.println(gateTarget);
    return;
  }

  Serial.print("[GATE] Pulse start, target=");
  Serial.print(gateTarget);
  Serial.print(", pin=");
  Serial.print(pin);
  Serial.print(", ms=");
  Serial.println(pulseMs);

  blinkDebugLed(2, 80, 80);

  setGatePin(pin, true);
  delay(pulseMs);
  setGatePin(pin, false);

  Serial.println("[GATE] Pulse end");
}

void triggerGateBoth() {
  triggerGateBoth(DEFAULT_GATE_PULSE_MS);
}

void triggerGateBoth(uint32_t pulseMs) {
  pulseMs = normalizePulseMs(pulseMs);

  Serial.print("[GATE] Pulse BOTH start, pins=");
  Serial.print(GATE1_OUTPUT_PIN);
  Serial.print(",");
  Serial.print(GATE2_OUTPUT_PIN);
  Serial.print(", ms=");
  Serial.println(pulseMs);

  blinkDebugLed(4, 60, 60);

  setGatePin(GATE1_OUTPUT_PIN, true);
  setGatePin(GATE2_OUTPUT_PIN, true);

  delay(pulseMs);

  setGatePin(GATE1_OUTPUT_PIN, false);
  setGatePin(GATE2_OUTPUT_PIN, false);

  Serial.println("[GATE] Pulse BOTH end");
}
