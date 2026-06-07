#pragma once

#include <Arduino.h>

void setupDebugLed();
void debugLedOn();
void debugLedOff();
void blinkDebugLedOnce();
void blinkDebugLed(uint8_t count, uint16_t onMs, uint16_t offMs);
