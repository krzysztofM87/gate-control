#pragma once

#include <Arduino.h>
#include "config.h"
#include "wifi_manager.h"
#include "gate_control.h"

struct GateCommand {
  bool shouldOpen = false;
  uint8_t target = GATE_TARGET_NONE;
  String commandId = "";
  uint32_t relayTimeMs = DEFAULT_GATE_PULSE_MS;
  int httpCode = 0;
  String raw = "";
};

void setupApiClient(const DeviceConfig &config);
GateCommand pollGateCommand();
bool ackGateCommand(const String &commandId, const String &status);
