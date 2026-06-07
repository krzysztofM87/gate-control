#pragma once

#include <Arduino.h>

#define GATE_TARGET_NONE 0
#define GATE_TARGET_1 1
#define GATE_TARGET_2 2
#define GATE_TARGET_BOTH 3

void setupGateOutputs();

void triggerGate(uint8_t gateTarget);
void triggerGate(uint8_t gateTarget, uint32_t pulseMs);

void triggerGateBoth();
void triggerGateBoth(uint32_t pulseMs);
